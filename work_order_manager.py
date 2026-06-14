from datetime import datetime, timedelta
from typing import Dict, List, Optional
import config
from database import (
    SessionLocal, DrillIssue, ImprovementWorkOrder, WorkOrderLog,
    Drill, User
)
from logging_system import drill_logger, log_system_event, create_notification


SEVERITY_DUE_DAYS = {
    "critical": 3,
    "high": 7,
    "medium": 14,
    "low": 30
}

ESCALATION_DAYS = {
    "critical": [1, 2],
    "high": [2, 4],
    "medium": [5, 10],
    "low": [10, 20]
}


def create_work_orders_for_drill(drill_id: int) -> List[ImprovementWorkOrder]:
    db = SessionLocal()
    try:
        drill = db.query(Drill).get(drill_id)
        if not drill:
            drill_logger.error(f"Drill {drill_id} not found for work order creation")
            return []
        
        issues = db.query(DrillIssue).filter(
            DrillIssue.drill_id == drill_id,
            DrillIssue.resolved_at == None
        ).all()
        
        work_orders = []
        notification_tasks = []
        for issue in issues:
            existing_wo = db.query(ImprovementWorkOrder).filter(
                ImprovementWorkOrder.issue_id == issue.id
            ).first()
            if existing_wo:
                continue
            
            due_days = SEVERITY_DUE_DAYS.get(issue.severity, 14)
            due_date = datetime.utcnow() + timedelta(days=due_days)
            
            assignee, assignee_dept = _get_assignee_for_issue(issue, drill, db)
            
            work_order = ImprovementWorkOrder(
                issue_id=issue.id,
                title=f"[演练改进] {issue.description[:50]}",
                description=issue.description,
                severity=issue.severity,
                status="pending",
                assignee=assignee,
                assignee_department=assignee_dept,
                due_date=due_date
            )
            
            db.add(work_order)
            db.flush()
            
            log_entry = WorkOrderLog(
                work_order_id=work_order.id,
                action="CREATED",
                details=f"自动从演练问题生成工单，问题ID: {issue.id}，严重程度: {issue.severity}",
                performed_by="system"
            )
            db.add(log_entry)
            
            work_orders.append(work_order)
            
            if assignee:
                notification_tasks.append({
                    "recipient": assignee,
                    "title": work_order.title,
                    "severity": issue.severity,
                    "due_date": due_date
                })
        
        db.commit()
        
        for wo in work_orders:
            db.refresh(wo)
        
        db.close()
        db = None
        
        for nt in notification_tasks:
            try:
                create_notification(
                    recipient=nt["recipient"],
                    channel="email",
                    message=f"您被分配了新的改进工单：\n标题: {nt['title']}\n严重程度: {nt['severity']}\n截止日期: {nt['due_date'].strftime('%Y-%m-%d')}\n请及时处理。",
                    subject=f"新改进工单通知 - {nt['title']}"
                )
            except Exception:
                pass
        
        log_system_event(
            "INFO",
            "work_order",
            "work_orders_created",
            {
                "drill_id": drill_id,
                "count": len(work_orders),
                "order_ids": [wo.id for wo in work_orders]
            }
        )
        
        drill_logger.info(f"Created {len(work_orders)} work orders for drill {drill_id}")
        return work_orders
    except Exception as e:
        drill_logger.error(f"Failed to create work orders for drill {drill_id}: {str(e)}", exc_info=True)
        if db:
            db.rollback()
        return []
    finally:
        if db:
            db.close()


def _get_assignee_for_issue(issue: DrillIssue, drill: Drill, db) -> tuple:
    if drill.business_units and len(drill.business_units) > 0:
        primary_unit = drill.business_units[0]
    else:
        primary_unit = "IT"
    
    users = db.query(User).filter(
        User.department == primary_unit,
        User.is_active == True
    ).all()
    
    if users:
        import random
        selected = random.choice(users)
        return selected.username, primary_unit
    
    return f"{primary_unit}负责人", primary_unit


def update_work_order_status(
    order_id: int,
    new_status: str,
    performed_by: str,
    details: str = None,
    resolution: str = None
) -> Optional[ImprovementWorkOrder]:
    db = SessionLocal()
    try:
        order = db.query(ImprovementWorkOrder).get(order_id)
        if not order:
            return None
        
        if new_status not in config.WORK_ORDER_STATUS:
            drill_logger.error(f"Invalid status: {new_status}")
            return None
        
        old_status = order.status
        order.status = new_status
        
        if new_status == "in_progress" and old_status == "pending":
            log_details = f"状态变更: pending -> in_progress"
            if details:
                log_details += f"\n{details}"
        elif new_status == "completed":
            order.completed_at = datetime.utcnow()
            order.resolution_details = resolution
            log_details = f"状态变更: {old_status} -> completed"
            if resolution:
                log_details += f"\n解决方案: {resolution}"
        elif new_status == "overdue":
            log_details = f"标记为超期，原状态: {old_status}"
        elif new_status == "escalated":
            order.escalation_level = max(order.escalation_level or 0, 3)
            order.last_escalated_at = datetime.utcnow()
            log_details = f"工单升级至管理层，原状态: {old_status}"
        else:
            log_details = f"状态变更: {old_status} -> {new_status}"
            if details:
                log_details += f"\n{details}"
        
        log_entry = WorkOrderLog(
            work_order_id=order.id,
            action=f"STATUS_{new_status.upper()}",
            details=log_details,
            performed_by=performed_by
        )
        db.add(log_entry)
        
        db.commit()
        db.refresh(order)
        
        log_system_event(
            "INFO",
            "work_order",
            "work_order_updated",
            {
                "order_id": order_id,
                "old_status": old_status,
                "new_status": new_status,
                "performed_by": performed_by
            }
        )
        
        drill_logger.info(f"Work order {order_id} status updated: {old_status} -> {new_status}")
        
        db.close()
        db2 = SessionLocal()
        try:
            return db2.query(ImprovementWorkOrder).get(order_id)
        finally:
            db2.close()
    except Exception as e:
        drill_logger.error(f"Failed to update work order {order_id}: {str(e)}", exc_info=True)
        if 'db' in locals() and db:
            db.rollback()
        return None
    finally:
        if 'db' in locals() and db:
            try:
                db.close()
            except Exception:
                pass


def reassign_work_order(
    order_id: int,
    new_assignee: str,
    new_department: str,
    performed_by: str,
    reason: str = None
) -> Optional[ImprovementWorkOrder]:
    db = SessionLocal()
    try:
        order = db.query(ImprovementWorkOrder).get(order_id)
        if not order:
            return None
        
        old_assignee = order.assignee
        old_department = order.assignee_department
        
        order.assignee = new_assignee
        order.assignee_department = new_department
        
        log_details = f"转派: {old_assignee}({old_department}) -> {new_assignee}({new_department})"
        if reason:
            log_details += f"\n原因: {reason}"
        
        log_entry = WorkOrderLog(
            work_order_id=order.id,
            action="REASSIGNED",
            details=log_details,
            performed_by=performed_by
        )
        db.add(log_entry)
        
        db.commit()
        db.refresh(order)
        order_title = order.title
        order_severity = order.severity
        order_due_date = order.due_date
        
        db.close()
        db = None
        
        try:
            create_notification(
                recipient=new_assignee,
                channel="email",
                message=f"您被转派了一个改进工单：\n标题: {order_title}\n严重程度: {order_severity}\n截止日期: {order_due_date.strftime('%Y-%m-%d') if order_due_date else 'N/A'}",
                subject=f"工单转派通知 - {order_title}"
            )
        except Exception:
            pass
        
        log_system_event(
            "INFO",
            "work_order",
            "work_order_reassigned",
            {
                "order_id": order_id,
                "old_assignee": old_assignee,
                "new_assignee": new_assignee,
                "performed_by": performed_by
            }
        )
        
        db2 = SessionLocal()
        try:
            return db2.query(ImprovementWorkOrder).get(order_id)
        finally:
            db2.close()
    except Exception as e:
        drill_logger.error(f"Failed to reassign work order {order_id}: {str(e)}", exc_info=True)
        if 'db' in locals() and db:
            db.rollback()
        return None
    finally:
        if 'db' in locals() and db:
            try:
                db.close()
            except Exception:
                pass


def query_work_orders(
    business_unit: str = None,
    severity: str = None,
    status: str = None,
    assignee: str = None
) -> List[Dict]:
    db = SessionLocal()
    try:
        query = db.query(ImprovementWorkOrder)
        
        if business_unit:
            query = query.filter(ImprovementWorkOrder.assignee_department == business_unit)
        if severity:
            query = query.filter(ImprovementWorkOrder.severity == severity)
        if status:
            query = query.filter(ImprovementWorkOrder.status == status)
        if assignee:
            query = query.filter(ImprovementWorkOrder.assignee == assignee)
        
        orders = query.order_by(ImprovementWorkOrder.created_at.desc()).all()
        
        result = []
        for order in orders:
            logs = db.query(WorkOrderLog).filter(
                WorkOrderLog.work_order_id == order.id
            ).order_by(WorkOrderLog.created_at).all()
            
            result.append({
                "id": order.id,
                "issue_id": order.issue_id,
                "title": order.title,
                "description": order.description,
                "severity": order.severity,
                "status": order.status,
                "assignee": order.assignee,
                "assignee_department": order.assignee_department,
                "due_date": order.due_date.isoformat() if order.due_date else None,
                "completed_at": order.completed_at.isoformat() if order.completed_at else None,
                "escalation_level": order.escalation_level,
                "resolution_details": order.resolution_details,
                "created_at": order.created_at.isoformat(),
                "updated_at": order.updated_at.isoformat(),
                "history": [
                    {
                        "action": log.action,
                        "details": log.details,
                        "performed_by": log.performed_by,
                        "created_at": log.created_at.isoformat()
                    }
                    for log in logs
                ]
            })
        
        return result
    except Exception as e:
        drill_logger.error(f"Failed to query work orders: {str(e)}", exc_info=True)
        return []
    finally:
        db.close()


def get_work_order_stats(business_unit: str = None) -> Dict:
    db = SessionLocal()
    try:
        query = db.query(ImprovementWorkOrder)
        if business_unit:
            query = query.filter(ImprovementWorkOrder.assignee_department == business_unit)
        
        all_orders = query.all()
        
        stats = {
            "total": len(all_orders),
            "by_status": {},
            "by_severity": {},
            "overdue_count": 0,
            "escalated_count": 0,
            "closure_rate": 0
        }
        
        for status in config.WORK_ORDER_STATUS:
            stats["by_status"][status] = 0
        
        for severity in config.SEVERITY_LEVELS:
            stats["by_severity"][severity] = 0
        
        completed = 0
        now = datetime.utcnow()
        for order in all_orders:
            stats["by_status"][order.status] = stats["by_status"].get(order.status, 0) + 1
            stats["by_severity"][order.severity] = stats["by_severity"].get(order.severity, 0) + 1
            
            if order.status == "completed":
                completed += 1
            if order.status == "overdue":
                stats["overdue_count"] += 1
            if order.status == "escalated" or (order.escalation_level or 0) >= 3:
                stats["escalated_count"] += 1
            if order.due_date and order.due_date < now and order.status not in ["completed"]:
                stats["overdue_count"] += 1
        
        if len(all_orders) > 0:
            stats["closure_rate"] = round(completed / len(all_orders) * 100, 1)
        
        return stats
    except Exception as e:
        drill_logger.error(f"Failed to get work order stats: {str(e)}", exc_info=True)
        return {}
    finally:
        db.close()
