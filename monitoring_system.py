import asyncio
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
import config
from database import (
    SessionLocal, Drill, DrillStep, ImprovementWorkOrder,
    DrillIssue, BusinessContinuityPlan
)
from logging_system import drill_logger, log_system_event, create_notification, notification_logger


class DrillMonitor:
    def __init__(self):
        self.running = False
        self.monitored_drills: Dict[int, Dict] = {}
        self.monitor_thread: Optional[threading.Thread] = None
        self.check_interval = 30
        self.alert_callbacks: List[Callable] = []
    
    def start(self):
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        drill_logger.info("Drill monitoring system started")
    
    def stop(self):
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        drill_logger.info("Drill monitoring system stopped")
    
    def _monitor_loop(self):
        while self.running:
            try:
                self.check_active_drills()
                self.check_overdue_work_orders()
            except Exception as e:
                drill_logger.error(f"Monitor loop error: {str(e)}", exc_info=True)
            
            time.sleep(self.check_interval)
    
    def check_active_drills(self) -> List[Dict]:
        db = SessionLocal()
        try:
            active_drills = db.query(Drill).filter(
                Drill.status == "in_progress"
            ).all()
            
            alerts = []
            for drill in active_drills:
                drill_alerts = self._check_drill_timings(drill, db)
                alerts.extend(drill_alerts)
            
            return alerts
        except Exception as e:
            drill_logger.error(f"Failed to check active drills: {str(e)}")
            return []
        finally:
            db.close()
    
    def _check_drill_timings(self, drill: Drill, db) -> List[Dict]:
        alerts = []
        now = datetime.utcnow()
        
        if drill.actual_start_time:
            elapsed_minutes = (now - drill.actual_start_time).total_seconds() / 60
            
            if drill.target_recovery_time and elapsed_minutes > drill.target_recovery_time:
                alert = {
                    "drill_id": drill.id,
                    "drill_name": drill.name,
                    "alert_type": "RTO_EXCEEDED",
                    "severity": "critical",
                    "message": f"演练「{drill.name}」已超过目标恢复时间！目标: {drill.target_recovery_time}分钟, 已用: {elapsed_minutes:.1f}分钟",
                    "elapsed_minutes": elapsed_minutes,
                    "target_minutes": drill.target_recovery_time,
                    "timestamp": now.isoformat()
                }
                alerts.append(alert)
                self._send_alert(alert, drill)
        
        active_steps = db.query(DrillStep).filter(
            DrillStep.drill_id == drill.id,
            DrillStep.status == "in_progress"
        ).all()
        
        for step in active_steps:
            if step.actual_start_time and step.target_duration_minutes:
                step_elapsed = (now - step.actual_start_time).total_seconds() / 60
                
                if step_elapsed > step.target_duration_minutes and not step.is_overdue:
                    step.is_overdue = True
                    db.commit()
                    
                    alert = {
                        "drill_id": drill.id,
                        "drill_name": drill.name,
                        "step_number": step.step_number,
                        "step_description": step.description,
                        "alert_type": "STEP_OVERDUE",
                        "severity": "warning",
                        "message": f"演练「{drill.name}」步骤{step.step_number}已超时！目标: {step.target_duration_minutes}分钟, 已用: {step_elapsed:.1f}分钟",
                        "elapsed_minutes": step_elapsed,
                        "target_minutes": step.target_duration_minutes,
                        "timestamp": now.isoformat()
                    }
                    alerts.append(alert)
                    self._send_alert(alert, drill)
        
        return alerts
    
    def _send_alert(self, alert: Dict, drill: Drill):
        drill_logger.warning(f"Alert: {alert['message']}")
        
        log_level = "WARNING" if alert["severity"] == "warning" else "ERROR"
        is_critical = alert["severity"] == "critical"
        
        log_system_event(
            log_level,
            "drill_monitoring",
            alert["alert_type"],
            alert,
            is_critical=is_critical
        )
        
        if drill.participants:
            for participant in drill.participants:
                create_notification(
                    recipient=participant.get("email", ""),
                    channel="email",
                    message=alert["message"],
                    subject=f"[{alert['alert_type']}] {drill.name}"
                )
        
        coordinator_message = f"""
【演练监控告警】
类型: {alert['alert_type']}
严重级别: {alert['severity']}
演练: {alert.get('drill_name', '')}
详情: {alert['message']}
时间: {alert.get('timestamp', datetime.utcnow().isoformat())}
请协调员立即关注并处理！
        """.strip()
        
        create_notification(
            recipient="bcp-coordinator@company.com",
            channel="wechat",
            message=coordinator_message,
            subject="演练监控告警"
        )
        
        for callback in self.alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                drill_logger.error(f"Alert callback failed: {str(e)}")
    
    def check_overdue_work_orders(self) -> List[Dict]:
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            overdue_orders = db.query(ImprovementWorkOrder).filter(
                ImprovementWorkOrder.status.in_(["pending", "in_progress"]),
                ImprovementWorkOrder.due_date < now
            ).all()
            
            escalated = []
            for order in overdue_orders:
                if order.status != "overdue":
                    order.status = "overdue"
                    db.commit()
                
                days_overdue = (now - order.due_date).days
                escalation_thresholds = [1, 3, 7]
                current_level = order.escalation_level or 0
                
                for i, threshold in enumerate(escalation_thresholds):
                    if days_overdue >= threshold and current_level <= i:
                        self._escalate_work_order(order, i + 1, days_overdue, db)
                        escalated.append({
                            "order_id": order.id,
                            "escalation_level": i + 1,
                            "days_overdue": days_overdue
                        })
                        break
            
            return escalated
        except Exception as e:
            drill_logger.error(f"Failed to check overdue work orders: {str(e)}")
            return []
        finally:
            db.close()
    
    def _escalate_work_order(self, order: ImprovementWorkOrder, new_level: int, days_overdue: int, db):
        order.escalation_level = new_level
        order.last_escalated_at = datetime.utcnow()
        
        if new_level >= 3:
            order.status = "escalated"
        
        db.commit()
        
        escalation_matrix = {
            1: {
                "notify": order.assignee or order.assignee_department,
                "message": f"改进工单「{order.title}」已超期{days_overdue}天，请尽快处理！"
            },
            2: {
                "notify": f"{order.assignee_department}部门经理",
                "message": f"改进工单「{order.title}」已超期{days_overdue}天，已升级至部门管理层，请督促处理！"
            },
            3: {
                "notify": "高级管理层",
                "message": f"【严重】改进工单「{order.title}」已超期{days_overdue}天，已升级至公司管理层，请关注！"
            }
        }
        
        escalation_info = escalation_matrix.get(new_level, escalation_matrix[1])
        
        log_system_event(
            "WARNING",
            "work_order_monitoring",
            "WORK_ORDER_ESCALATED",
            {
                "order_id": order.id,
                "title": order.title,
                "new_level": new_level,
                "days_overdue": days_overdue
            },
            is_critical=(new_level >= 3)
        )
        
        create_notification(
            recipient=escalation_info["notify"],
            channel="wechat" if new_level >= 2 else "email",
            message=escalation_info["message"],
            subject=f"工单超期升级通知 (Level {new_level})"
        )
        
        drill_logger.warning(f"Work order {order.id} escalated to level {new_level}")
    
    def get_drill_status(self, drill_id: int) -> Optional[Dict]:
        db = SessionLocal()
        try:
            drill = db.query(Drill).get(drill_id)
            if not drill:
                return None
            
            steps = db.query(DrillStep).filter(DrillStep.drill_id == drill_id).all()
            issues = db.query(DrillIssue).filter(DrillIssue.drill_id == drill_id).all()
            
            total_steps = len(steps)
            completed_steps = sum(1 for s in steps if s.status == "completed")
            overdue_steps = sum(1 for s in steps if s.is_overdue)
            
            now = datetime.utcnow()
            elapsed_minutes = 0
            if drill.actual_start_time:
                elapsed_minutes = (now - drill.actual_start_time).total_seconds() / 60
            
            return {
                "drill_id": drill.id,
                "name": drill.name,
                "status": drill.status,
                "drill_type": drill.drill_type,
                "elapsed_minutes": round(elapsed_minutes, 1),
                "target_recovery_time": drill.target_recovery_time,
                "total_steps": total_steps,
                "completed_steps": completed_steps,
                "overdue_steps": overdue_steps,
                "issues_found": len(issues),
                "critical_issues": sum(1 for i in issues if i.severity == "critical"),
                "progress_percentage": round(completed_steps / total_steps * 100, 1) if total_steps > 0 else 0,
                "steps": [
                    {
                        "step_number": s.step_number,
                        "description": s.description,
                        "status": s.status,
                        "is_overdue": s.is_overdue,
                        "target_duration": s.target_duration_minutes
                    }
                    for s in steps
                ]
            }
        except Exception as e:
            drill_logger.error(f"Failed to get drill status: {str(e)}")
            return None
        finally:
            db.close()
    
    def register_alert_callback(self, callback: Callable):
        self.alert_callbacks.append(callback)


drill_monitor = DrillMonitor()


def get_real_time_drills_status() -> List[Dict]:
    db = SessionLocal()
    try:
        active_drills = db.query(Drill).filter(
            Drill.status == "in_progress"
        ).all()
        
        status_list = []
        for drill in active_drills:
            status = drill_monitor.get_drill_status(drill.id)
            if status:
                status_list.append(status)
        
        return status_list
    except Exception as e:
        drill_logger.error(f"Failed to get real-time drill status: {str(e)}")
        return []
    finally:
        db.close()
