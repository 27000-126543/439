import random
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import config
from database import (
    SessionLocal, Drill, DrillStep, DrillIssue, BusinessContinuityPlan,
    BusinessFunction, User
)
from logging_system import drill_logger, log_system_event, create_notification
from concurrency_manager import task_manager


DRILL_SCHEDULE_INTERVAL = {
    "critical": 30,
    "high": 60,
    "medium": 90,
    "low": 180
}


SCENARIO_TEMPLATES = [
    {
        "name": "服务器宕机演练",
        "description": "模拟核心服务器突然宕机，验证系统恢复能力",
        "expected_impact": "核心业务中断，需要启动备用服务器",
        "severity": "high"
    },
    {
        "name": "网络故障演练",
        "description": "模拟办公区网络中断，验证应急通信流程",
        "expected_impact": "内部网络不可用，需启用4G热点和应急通信",
        "severity": "medium"
    },
    {
        "name": "数据损坏演练",
        "description": "模拟数据库损坏，验证数据备份恢复流程",
        "expected_impact": "业务数据不可读，需从备份恢复",
        "severity": "critical"
    },
    {
        "name": "电力中断演练",
        "description": "模拟数据中心电力中断，验证UPS和发电机切换",
        "expected_impact": "主电源中断，需验证备用电源启动",
        "severity": "high"
    },
    {
        "name": "人员不可用演练",
        "description": "模拟关键岗位人员突发不可用，验证人员备份机制",
        "expected_impact": "关键人员缺席，需验证替代人员能力",
        "severity": "medium"
    }
]


def get_participants_for_drill(business_units: List[str], drill_type: str) -> List[Dict]:
    db = SessionLocal()
    try:
        participants = []
        for unit in business_units:
            users = db.query(User).filter(
                User.department == unit,
                User.is_active == True
            ).all()
            
            if users:
                if drill_type == "full_scale":
                    selected = users
                else:
                    count = min(len(users), max(2, len(users) // 2))
                    selected = random.sample(users, count)
                
                for user in selected:
                    participants.append({
                        "user_id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "department": user.department,
                        "role": user.role
                    })
        
        drill_logger.info(f"Selected {len(participants)} participants for drill")
        return participants
    except Exception as e:
        drill_logger.error(f"Failed to get participants: {str(e)}")
        return []
    finally:
        db.close()


def generate_drill_script(function: BusinessFunction, plan: BusinessContinuityPlan, scenario: Dict) -> Dict:
    steps = []
    
    intro_step = {
        "step_number": 0,
        "description": f"演练开始：{scenario.get('name', '应急演练')}\n场景描述：{scenario.get('description', '')}\n预期影响：{scenario.get('expected_impact', '')}",
        "responsible_party": "演练协调员",
        "target_duration_minutes": 5
    }
    steps.append(intro_step)
    
    if plan and plan.recovery_procedures:
        for i, proc in enumerate(plan.recovery_procedures):
            step = {
                "step_number": i + 1,
                "description": proc.get("description", f"执行恢复步骤 {i + 1}"),
                "responsible_party": plan.responsible_department,
                "target_duration_minutes": proc.get("duration", 30)
            }
            steps.append(step)
    else:
        default_steps = [
            "确认故障现象并报告",
            "评估影响范围",
            "执行应急操作",
            "验证恢复结果",
            "记录执行情况"
        ]
        for i, desc in enumerate(default_steps):
            steps.append({
                "step_number": i + 1,
                "description": desc,
                "responsible_party": function.business_unit,
                "target_duration_minutes": 15
            })
    
    final_step = {
        "step_number": len(steps),
        "description": "演练总结：汇报执行情况，讨论存在问题，记录改进建议",
        "responsible_party": "演练协调员",
        "target_duration_minutes": 15
    }
    steps.append(final_step)
    
    return {
        "scenario": scenario,
        "steps": steps,
        "estimated_duration": sum(s["target_duration_minutes"] for s in steps)
    }


def estimate_impact_range(function: BusinessFunction, scenario_severity: str) -> Dict:
    impact_multipliers = {
        "low": 0.3,
        "medium": 0.5,
        "high": 0.7,
        "critical": 0.9
    }
    
    multiplier = impact_multipliers.get(scenario_severity, 0.5)
    
    estimated_impact = {
        "affected_business_units": [function.business_unit],
        "estimated_financial_loss": function.financial_impact * multiplier * 10000,
        "estimated_operational_impact": function.operational_impact * multiplier,
        "estimated_duration_hours": function.rto_hours * multiplier,
        "affected_users": random.randint(50, 500) if scenario_severity in ["high", "critical"] else random.randint(10, 100),
        "dependent_systems_affected": function.dependencies or []
    }
    
    return estimated_impact


def create_drill(
    function_id: int,
    drill_type: str,
    scenario: Dict = None,
    scheduled_time: datetime = None,
    business_units: List[str] = None,
    is_simulation: bool = False
) -> Optional[Drill]:
    db = SessionLocal()
    try:
        function = db.query(BusinessFunction).get(function_id)
        if not function:
            drill_logger.error(f"Function {function_id} not found")
            return None
        
        plan = db.query(BusinessContinuityPlan).filter(
            BusinessContinuityPlan.function_id == function_id,
            BusinessContinuityPlan.status == "active"
        ).first()
        
        if not scenario:
            scenario = random.choice(SCENARIO_TEMPLATES)
        
        if not business_units:
            business_units = [function.business_unit]
        
        participants = get_participants_for_drill(business_units, drill_type)
        
        drill_script = generate_drill_script(function, plan, scenario)
        impact_estimate = estimate_impact_range(function, scenario.get("severity", "medium"))
        
        if not scheduled_time:
            scheduled_time = datetime.utcnow() + timedelta(days=7)
        
        drill = Drill(
            plan_id=plan.id if plan else None,
            function_id=function_id,
            name=f"{function.name} - {scenario.get('name', '演练')}",
            description=scenario.get("description", ""),
            drill_type=drill_type,
            status="scheduled",
            business_units=business_units,
            participants=participants,
            scheduled_start_time=scheduled_time,
            target_recovery_time=function.rto_hours * 60,
            scenario=scenario.get("description", ""),
            expected_impact=impact_estimate.get("estimated_operational_impact", 0),
            is_simulation=is_simulation
        )
        
        db.add(drill)
        db.flush()
        
        for step_data in drill_script["steps"]:
            step = DrillStep(
                drill_id=drill.id,
                step_number=step_data["step_number"],
                description=step_data["description"],
                responsible_party=step_data["responsible_party"],
                target_duration_minutes=step_data["target_duration_minutes"]
            )
            db.add(step)
        
        db.commit()
        db.refresh(drill)
        drill_id = drill.id
        drill_name = drill.name
        db.close()
        db = None
        
        for participant in participants:
            try:
                create_notification(
                    recipient=participant["email"],
                    channel="email",
                    message=f"您被分配参与演练：{drill_name}\n时间：{scheduled_time}\n请准时参加。",
                    subject=f"演练通知 - {drill_name}"
                )
            except Exception:
                pass
        
        log_system_event(
            "INFO",
            "drill_management",
            "drill_created",
            {
                "drill_id": drill_id,
                "drill_name": drill_name,
                "drill_type": drill_type,
                "participants": len(participants),
                "scheduled_time": scheduled_time.isoformat()
            }
        )
        
        drill_logger.info(f"Drill created: {drill_id} - {drill_name}")
        db2 = SessionLocal()
        try:
            result = db2.query(Drill).get(drill_id)
            return result
        finally:
            db2.close()
        
    except Exception as e:
        drill_logger.error(f"Failed to create drill: {str(e)}", exc_info=True)
        if db:
            db.rollback()
        return None
    finally:
        if db:
            db.close()


def create_simulation_drill(
    function_id: int,
    scenario_name: str,
    custom_scenario: Dict,
    **kwargs
) -> Optional[Drill]:
    scenario = {
        "name": scenario_name,
        "description": custom_scenario.get("description", "自定义模拟场景"),
        "expected_impact": custom_scenario.get("expected_impact", ""),
        "severity": custom_scenario.get("severity", "medium")
    }
    
    return create_drill(
        function_id=function_id,
        drill_type="simulation",
        scenario=scenario,
        is_simulation=True,
        **kwargs
    )


def start_drill(drill_id: int) -> Optional[Drill]:
    db = SessionLocal()
    try:
        drill = db.query(Drill).get(drill_id)
        if not drill:
            return None
        
        drill.status = "in_progress"
        drill.actual_start_time = datetime.utcnow()
        
        first_step = db.query(DrillStep).filter(
            DrillStep.drill_id == drill_id,
            DrillStep.step_number == 1
        ).first()
        if first_step:
            first_step.status = "in_progress"
            first_step.actual_start_time = datetime.utcnow()
        
        db.commit()
        
        log_system_event(
            "INFO",
            "drill_management",
            "drill_started",
            {"drill_id": drill_id, "drill_name": drill.name}
        )
        
        drill_logger.info(f"Drill started: {drill_id}")
        db.close()
        db2 = SessionLocal()
        try:
            return db2.query(Drill).get(drill_id)
        finally:
            db2.close()
    except Exception as e:
        drill_logger.error(f"Failed to start drill: {str(e)}")
        if 'db' in locals() and db:
            db.rollback()
        return None
    finally:
        if 'db' in locals() and db:
            try:
                db.close()
            except Exception:
                pass


def complete_drill_step(drill_id: int, step_number: int, notes: str = None) -> Optional[DrillStep]:
    db = SessionLocal()
    try:
        step = db.query(DrillStep).filter(
            DrillStep.drill_id == drill_id,
            DrillStep.step_number == step_number
        ).first()
        
        if not step:
            return None
        
        step.status = "completed"
        step.actual_end_time = datetime.utcnow()
        step.notes = notes
        
        if step.actual_start_time and step.actual_end_time:
            actual_duration = (step.actual_end_time - step.actual_start_time).total_seconds() / 60
            if actual_duration > step.target_duration_minutes:
                step.is_overdue = True
        
        next_step = db.query(DrillStep).filter(
            DrillStep.drill_id == drill_id,
            DrillStep.step_number == step_number + 1
        ).first()
        
        if next_step:
            next_step.status = "in_progress"
            next_step.actual_start_time = datetime.utcnow()
        
        db.commit()
        db.refresh(step)
        step_id = step.id
        
        log_system_event(
            "INFO",
            "drill_management",
            "step_completed",
            {"drill_id": drill_id, "step_number": step_number, "overdue": step.is_overdue}
        )
        
        db.close()
        db2 = SessionLocal()
        try:
            return db2.query(DrillStep).get(step_id)
        finally:
            db2.close()
    except Exception as e:
        drill_logger.error(f"Failed to complete drill step: {str(e)}")
        if 'db' in locals() and db:
            db.rollback()
        return None
    finally:
        if 'db' in locals() and db:
            try:
                db.close()
            except Exception:
                pass


def record_drill_issue(
    drill_id: int,
    description: str,
    severity: str,
    identified_by: str = None
) -> Optional[DrillIssue]:
    db = SessionLocal()
    try:
        issue = DrillIssue(
            drill_id=drill_id,
            description=description,
            severity=severity,
            identified_by=identified_by
        )
        db.add(issue)
        db.commit()
        db.refresh(issue)
        issue_id = issue.id
        
        log_system_event(
            "WARNING" if severity in ["high", "critical"] else "INFO",
            "drill_management",
            "issue_recorded",
            {"drill_id": drill_id, "severity": severity, "issue_id": issue.id},
            is_critical=(severity == "critical")
        )
        
        drill_logger.warning(f"Drill issue recorded: {issue.id} - {severity}")
        
        db.close()
        db2 = SessionLocal()
        try:
            return db2.query(DrillIssue).get(issue_id)
        finally:
            db2.close()
    except Exception as e:
        drill_logger.error(f"Failed to record drill issue: {str(e)}")
        if 'db' in locals() and db:
            db.rollback()
        return None
    finally:
        if 'db' in locals() and db:
            try:
                db.close()
            except Exception:
                pass


def complete_drill(drill_id: int) -> Optional[Drill]:
    db = SessionLocal()
    try:
        drill = db.query(Drill).get(drill_id)
        if not drill:
            return None
        
        drill.status = "completed"
        drill.actual_end_time = datetime.utcnow()
        
        pending_steps = db.query(DrillStep).filter(
            DrillStep.drill_id == drill_id,
            DrillStep.status != "completed"
        ).all()
        
        for step in pending_steps:
            if step.status == "in_progress":
                step.status = "completed"
                step.actual_end_time = datetime.utcnow()
        
        db.commit()
        db.refresh(drill)
        
        log_system_event(
            "INFO",
            "drill_management",
            "drill_completed",
            {"drill_id": drill_id, "drill_name": drill.name}
        )
        
        drill_logger.info(f"Drill completed: {drill_id}")
        
        db.close()
        db2 = SessionLocal()
        try:
            return db2.query(Drill).get(drill_id)
        finally:
            db2.close()
    except Exception as e:
        drill_logger.error(f"Failed to complete drill: {str(e)}")
        if 'db' in locals() and db:
            db.rollback()
        return None
    finally:
        if 'db' in locals() and db:
            try:
                db.close()
            except Exception:
                pass


def schedule_automatic_drills() -> Dict:
    db = SessionLocal()
    try:
        functions = db.query(BusinessFunction).filter(
            BusinessFunction.is_critical == True
        ).all()
        
        drill_logger.info(f"Scheduling automatic drills for {len(functions)} critical functions")
        
        created_drills = []
        for func in functions:
            last_drill = db.query(Drill).filter(
                Drill.function_id == func.id
            ).order_by(Drill.created_at.desc()).first()
            
            interval_days = DRILL_SCHEDULE_INTERVAL.get(func.risk_level, 90)
            should_schedule = True
            
            if last_drill:
                days_since_last = (datetime.utcnow() - last_drill.created_at).days
                should_schedule = days_since_last >= interval_days
            
            if should_schedule:
                drill_types = ["tabletop", "functional"]
                if func.risk_level == "critical":
                    drill_types.append("full_scale")
                
                drill_type = random.choice(drill_types)
                drill = create_drill(
                    function_id=func.id,
                    drill_type=drill_type,
                    scheduled_time=datetime.utcnow() + timedelta(days=random.randint(7, 14))
                )
                if drill:
                    created_drills.append({
                        "drill_id": drill.id,
                        "function_id": func.id,
                        "function_name": func.name,
                        "drill_type": drill_type
                    })
        
        log_system_event(
            "INFO",
            "drill_management",
            "auto_drills_scheduled",
            {"count": len(created_drills), "drills": created_drills}
        )
        
        return {
            "total_critical_functions": len(functions),
            "drills_created": len(created_drills),
            "drills": created_drills
        }
        
    except Exception as e:
        drill_logger.error(f"Failed to schedule automatic drills: {str(e)}", exc_info=True)
        log_system_event("ERROR", "drill_management", "auto_schedule_failed", {"error": str(e)}, is_critical=True)
        return {"error": str(e)}
    finally:
        db.close()


def check_due_drills() -> List[Drill]:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        due_soon = now + timedelta(hours=24)
        
        drills = db.query(Drill).filter(
            Drill.status == "scheduled",
            Drill.scheduled_start_time <= due_soon,
            Drill.scheduled_start_time > now
        ).all()
        
        for drill in drills:
            hours_until = (drill.scheduled_start_time - now).total_seconds() / 3600
            drill_logger.info(f"Drill {drill.id} starting in {hours_until:.1f} hours")
            
            if drill.participants:
                for participant in drill.participants:
                    create_notification(
                        recipient=participant.get("email", ""),
                        channel="email",
                        message=f"提醒：演练「{drill.name}」将在 {hours_until:.0f} 小时后开始。\n请做好准备。",
                        subject=f"演练提醒 - {drill.name}"
                    )
        
        return drills
    except Exception as e:
        drill_logger.error(f"Failed to check due drills: {str(e)}")
        return []
    finally:
        db.close()


async def run_drill_concurrently(drill_ids: List[int]) -> Dict:
    drill_logger.info(f"Running {len(drill_ids)} drills concurrently")
    
    tasks = []
    for drill_id in drill_ids:
        task_id = task_manager.submit_async(
            start_drill,
            drill_id,
            priority=1
        )
        if task_id:
            tasks.append(task_id)
    
    return {
        "drills_count": len(drill_ids),
        "tasks_submitted": len(tasks),
        "task_ids": tasks
    }
