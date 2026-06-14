import os
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session

import config
from database import (
    init_db, get_db, SessionLocal, BusinessSystem, BusinessFunction,
    OperationalData, BusinessContinuityPlan, Drill, DrillStep,
    DrillIssue, DrillReport, MonthlyReport, SystemLog, User, EmergencyPlanTemplate,
    ImprovementWorkOrder
)
from logging_system import system_logger, log_system_event
from concurrency_manager import task_manager, run_concurrent_sync
from data_collection import (
    run_daily_data_collection_and_assessment, identify_critical_functions,
    analyze_dependencies, assess_all_risks, collect_all_systems_data
)
from plan_generator import generate_bcp_for_function, generate_all_bcps, approve_plan, init_templates
from drill_manager import (
    create_drill, create_simulation_drill, start_drill, complete_drill_step,
    record_drill_issue, complete_drill, schedule_automatic_drills, check_due_drills,
    run_drill_concurrently
)
from monitoring_system import drill_monitor, get_real_time_drills_status
from report_generator import (
    generate_drill_reports, generate_monthly_report, export_lifecycle_data,
    analyze_drill_results
)
from work_order_manager import (
    create_work_orders_for_drill, update_work_order_status, reassign_work_order,
    query_work_orders, get_work_order_stats
)


scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    system_logger.info("Starting BCP Management System...")
    
    init_db()
    init_templates()
    _seed_initial_data()
    
    drill_monitor.start()
    
    scheduler.add_job(
        scheduled_daily_tasks,
        CronTrigger(hour=config.DATA_COLLECTION_TIME.hour, minute=config.DATA_COLLECTION_TIME.minute),
        id="daily_tasks",
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_drill_check,
        CronTrigger(hour=config.DAILY_DRILL_CHECK_TIME.hour, minute=config.DAILY_DRILL_CHECK_TIME.minute),
        id="drill_check",
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_monthly_report,
        CronTrigger(day=config.MONTHLY_REPORT_DAY, hour=6, minute=0),
        id="monthly_report",
        replace_existing=True
    )
    
    scheduler.start()
    system_logger.info("Schedulers started")
    
    try:
        yield
    finally:
        scheduler.shutdown()
        drill_monitor.stop()
        task_manager.shutdown()
        system_logger.info("BCP Management System shutdown complete")


app = FastAPI(
    title="企业级业务连续性计划与演练自动化管理系统",
    description="自动数据采集、风险评估、计划生成、演练管理、报告输出的完整BCP解决方案",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _seed_initial_data():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            default_users = [
                {"username": "admin", "email": "admin@company.com", "department": "IT", "role": "System Administrator"},
                {"username": "zhangsan", "email": "zhangsan@company.com", "department": "IT", "role": "CTO"},
                {"username": "lisi", "email": "lisi@company.com", "department": "Finance", "role": "Department Head"},
                {"username": "wangwu", "email": "wangwu@company.com", "department": "Operations", "role": "Manager"},
                {"username": "zhaoliu", "email": "zhaoliu@company.com", "department": "Sales", "role": "Team Lead"},
                {"username": "qianqi", "email": "qianqi@company.com", "department": "HR", "role": "Senior Engineer"},
                {"username": "sunba", "email": "sunba@company.com", "department": "Legal", "role": "Manager"},
                {"username": "zhoujiu", "email": "zhoujiu@company.com", "department": "CustomerService", "role": "Team Lead"},
            ]
            for u in default_users:
                db.add(User(**u))
            db.commit()
        
        if db.query(BusinessSystem).count() == 0:
            default_systems = [
                {"name": "核心交易系统", "description": "处理企业核心业务交易", "business_unit": "IT", "system_owner": "zhangsan", "contact_email": "zhangsan@company.com"},
                {"name": "财务管理系统", "description": "财务核算与报表系统", "business_unit": "Finance", "system_owner": "lisi", "contact_email": "lisi@company.com"},
                {"name": "运营管理平台", "description": "日常运营管理与监控", "business_unit": "Operations", "system_owner": "wangwu", "contact_email": "wangwu@company.com"},
                {"name": "客户关系管理系统", "description": "客户信息与销售管理", "business_unit": "Sales", "system_owner": "zhaoliu", "contact_email": "zhaoliu@company.com"},
                {"name": "人力资源系统", "description": "员工信息与考勤管理", "business_unit": "HR", "system_owner": "qianqi", "contact_email": "qianqi@company.com"},
            ]
            for s in default_systems:
                db.add(BusinessSystem(**s))
            db.commit()
            
            systems = db.query(BusinessSystem).all()
            risk_profiles = [
                (95, 90, 85, 88),
                (88, 85, 70, 80),
                (75, 80, 60, 50),
                (60, 70, 75, 40),
                (45, 50, 30, 35),
            ]
            function_names = [
                ["订单处理", "支付结算", "账户管理"],
                ["财务核算", "报表生成", "预算管理"],
                ["库存管理", "物流调度", "质量监控"],
                ["客户跟进", "销售报表", "合同管理"],
                ["薪资核算", "招聘管理", "培训管理"],
            ]
            
            for idx, system in enumerate(systems):
                profile = risk_profiles[idx]
                for j, fname in enumerate(function_names[idx]):
                    func = BusinessFunction(
                        system_id=system.id,
                        name=fname,
                        description=f"{system.name} - {fname}功能",
                        business_unit=system.business_unit,
                        function_owner=system.system_owner,
                        dependencies=[(systems[(idx + j) % len(systems)].id)] if j > 0 else [],
                        financial_impact=profile[0] - j * 5,
                        operational_impact=profile[1] - j * 5,
                        reputational_impact=profile[2] - j * 5,
                        regulatory_impact=profile[3] - j * 5,
                    )
                    db.add(func)
            db.commit()
        
        system_logger.info("Initial data seeding completed")
    except Exception as e:
        system_logger.error(f"Data seeding failed: {str(e)}")
        db.rollback()
    finally:
        db.close()


def scheduled_daily_tasks():
    system_logger.info("Executing scheduled daily tasks...")
    try:
        asyncio.run(run_daily_data_collection_and_assessment())
        schedule_automatic_drills()
        system_logger.info("Scheduled daily tasks completed")
    except Exception as e:
        system_logger.error(f"Scheduled daily tasks failed: {str(e)}", exc_info=True)
        log_system_event("ERROR", "scheduler", "daily_tasks_failed", {"error": str(e)}, is_critical=True)


def scheduled_drill_check():
    system_logger.info("Checking scheduled drills...")
    try:
        check_due_drills()
        drill_monitor.check_active_drills()
        drill_monitor.check_overdue_work_orders()
    except Exception as e:
        system_logger.error(f"Drill check failed: {str(e)}", exc_info=True)


def scheduled_monthly_report():
    system_logger.info("Generating monthly report...")
    try:
        now = datetime.now()
        if now.day == config.MONTHLY_REPORT_DAY:
            prev_month = now.replace(day=1) - timedelta(days=1)
            report_month = prev_month.strftime("%Y-%m")
            generate_monthly_report(report_month)
            system_logger.info(f"Monthly report for {report_month} generated")
    except Exception as e:
        system_logger.error(f"Monthly report generation failed: {str(e)}", exc_info=True)
        log_system_event("ERROR", "scheduler", "monthly_report_failed", {"error": str(e)}, is_critical=True)


class DrillCreateRequest(BaseModel):
    function_id: int
    drill_type: str = "tabletop"
    scheduled_time: Optional[datetime] = None
    business_units: Optional[List[str]] = None
    is_simulation: bool = False


class SimulationDrillRequest(BaseModel):
    function_id: int
    scenario_name: str
    scenario_description: str = "自定义模拟场景"
    scenario_severity: str = "medium"
    expected_impact: str = ""
    scheduled_time: Optional[datetime] = None
    business_units: Optional[List[str]] = None


class WorkOrderUpdateRequest(BaseModel):
    order_id: int
    status: str
    performed_by: str
    details: Optional[str] = None
    resolution: Optional[str] = None


class WorkOrderReassignRequest(BaseModel):
    order_id: int
    new_assignee: str
    new_department: str
    performed_by: str
    reason: Optional[str] = None


class StepCompleteRequest(BaseModel):
    drill_id: int
    step_number: int
    notes: Optional[str] = None


class IssueCreateRequest(BaseModel):
    drill_id: int
    description: str
    severity: str = "medium"
    identified_by: Optional[str] = None


@app.get("/")
def root():
    return {
        "name": "企业级业务连续性计划与演练自动化管理系统",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/api/collection/run")
async def run_data_collection(background_tasks: BackgroundTasks):
    background_tasks.add_task(scheduled_daily_tasks)
    result = await run_daily_data_collection_and_assessment()
    return {"status": "success", "data": result}


@app.post("/api/collection/trigger")
def trigger_collection():
    result = asyncio.run(collect_all_systems_data())
    critical = identify_critical_functions()
    deps = analyze_dependencies()
    risks = assess_all_risks()
    return {
        "status": "success",
        "data_collection": result,
        "critical_functions": len([c for c in critical if c["is_critical"]]),
        "risk_assessment": risks.get("by_level", {})
    }


@app.get("/api/systems")
def list_systems(db: Session = Depends(get_db)):
    systems = db.query(BusinessSystem).all()
    return {"count": len(systems), "data": [
        {"id": s.id, "name": s.name, "business_unit": s.business_unit, "is_active": s.is_active}
        for s in systems
    ]}


@app.get("/api/functions")
def list_functions(
    business_unit: Optional[str] = None,
    risk_level: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(BusinessFunction)
    if business_unit:
        query = query.filter(BusinessFunction.business_unit == business_unit)
    if risk_level:
        query = query.filter(BusinessFunction.risk_level == risk_level)
    
    functions = query.order_by(BusinessFunction.priority).all()
    return {"count": len(functions), "data": [
        {
            "id": f.id,
            "name": f.name,
            "business_unit": f.business_unit,
            "is_critical": f.is_critical,
            "risk_score": f.risk_score,
            "risk_level": f.risk_level,
            "priority": f.priority,
            "rto_hours": f.rto_hours,
            "rpo_hours": f.rpo_hours
        }
        for f in functions
    ]}


@app.get("/api/functions/{function_id}")
def get_function(function_id: int, db: Session = Depends(get_db)):
    func = db.query(BusinessFunction).get(function_id)
    if not func:
        raise HTTPException(status_code=404, detail="Function not found")
    return {
        "id": func.id,
        "name": func.name,
        "description": func.description,
        "business_unit": func.business_unit,
        "function_owner": func.function_owner,
        "is_critical": func.is_critical,
        "dependencies": func.dependencies,
        "financial_impact": func.financial_impact,
        "operational_impact": func.operational_impact,
        "reputational_impact": func.reputational_impact,
        "regulatory_impact": func.regulatory_impact,
        "risk_score": func.risk_score,
        "risk_level": func.risk_level,
        "priority": func.priority,
        "rto_hours": func.rto_hours,
        "rpo_hours": func.rpo_hours
    }


@app.post("/api/plans/generate/{function_id}")
def generate_plan(function_id: int):
    plan = generate_bcp_for_function(function_id)
    if not plan:
        raise HTTPException(status_code=500, detail="Failed to generate plan")
    return {"status": "success", "plan_id": plan.id, "name": plan.name}


@app.post("/api/plans/generate-all")
def generate_all_plans():
    result = generate_all_bcps()
    return {"status": "success", "data": result}


@app.post("/api/plans/approve/{plan_id}")
def approve_bcp(plan_id: int, approver: str = "admin"):
    plan = approve_plan(plan_id, approver)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"status": "success", "plan_id": plan.id, "plan_status": plan.status}


@app.get("/api/plans")
def list_plans(
    function_id: Optional[int] = None,
    status: Optional[str] = None,
    risk_level: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(BusinessContinuityPlan)
    if function_id:
        query = query.filter(BusinessContinuityPlan.function_id == function_id)
    if status:
        query = query.filter(BusinessContinuityPlan.status == status)
    if risk_level:
        query = query.filter(BusinessContinuityPlan.risk_level == risk_level)
    
    plans = query.order_by(BusinessContinuityPlan.generated_at.desc()).all()
    return {"count": len(plans), "data": [
        {
            "id": p.id,
            "name": p.name,
            "version": p.version,
            "status": p.status,
            "risk_level": p.risk_level,
            "rto_target": p.rto_target,
            "responsible_person": p.responsible_person,
            "generated_at": p.generated_at.isoformat()
        }
        for p in plans
    ]}


@app.post("/api/drills/create")
def create_new_drill(request: DrillCreateRequest):
    drill = create_drill(
        function_id=request.function_id,
        drill_type=request.drill_type,
        scheduled_time=request.scheduled_time,
        business_units=request.business_units,
        is_simulation=request.is_simulation
    )
    if not drill:
        raise HTTPException(status_code=500, detail="Failed to create drill")
    return {"status": "success", "drill_id": drill.id, "name": drill.name}


@app.post("/api/drills/simulation")
def create_sim_drill(request: SimulationDrillRequest):
    drill = create_simulation_drill(
        function_id=request.function_id,
        scenario_name=request.scenario_name,
        custom_scenario={
            "description": request.scenario_description,
            "severity": request.scenario_severity,
            "expected_impact": request.expected_impact
        },
        scheduled_time=request.scheduled_time,
        business_units=request.business_units
    )
    if not drill:
        raise HTTPException(status_code=500, detail="Failed to create simulation drill")
    return {"status": "success", "drill_id": drill.id, "name": drill.name}


@app.post("/api/drills/start/{drill_id}")
def start_drill_execution(drill_id: int):
    drill = start_drill(drill_id)
    if not drill:
        raise HTTPException(status_code=404, detail="Drill not found")
    return {"status": "success", "drill_id": drill.id, "actual_start_time": drill.actual_start_time.isoformat()}


@app.post("/api/drills/step/complete")
def complete_step(request: StepCompleteRequest):
    step = complete_drill_step(request.drill_id, request.step_number, request.notes)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    return {"status": "success", "is_overdue": step.is_overdue}


@app.post("/api/drills/issues")
def create_issue(request: IssueCreateRequest):
    issue = record_drill_issue(request.drill_id, request.description, request.severity, request.identified_by)
    if not issue:
        raise HTTPException(status_code=500, detail="Failed to record issue")
    return {"status": "success", "issue_id": issue.id}


@app.post("/api/drills/complete/{drill_id}")
def finish_drill(drill_id: int, background_tasks: BackgroundTasks):
    drill = complete_drill(drill_id)
    if not drill:
        raise HTTPException(status_code=404, detail="Drill not found")
    
    background_tasks.add_task(create_work_orders_for_drill, drill_id)
    background_tasks.add_task(generate_drill_reports, drill_id)
    
    return {"status": "success", "drill_id": drill.id}


@app.get("/api/drills")
def list_drills(
    function_id: Optional[int] = None,
    drill_type: Optional[str] = None,
    status: Optional[str] = None,
    business_unit: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Drill)
    if function_id:
        query = query.filter(Drill.function_id == function_id)
    if drill_type:
        query = query.filter(Drill.drill_type == drill_type)
    if status:
        query = query.filter(Drill.status == status)
    if start_date:
        query = query.filter(Drill.created_at >= start_date)
    if end_date:
        query = query.filter(Drill.created_at < end_date)
    
    drills = query.order_by(Drill.created_at.desc()).all()
    
    if business_unit:
        drills = [d for d in drills if d.business_units and business_unit in d.business_units]
    
    return {"count": len(drills), "data": [
        {
            "id": d.id,
            "name": d.name,
            "drill_type": d.drill_type,
            "status": d.status,
            "business_units": d.business_units,
            "is_simulation": d.is_simulation,
            "scheduled_start_time": d.scheduled_start_time.isoformat() if d.scheduled_start_time else None,
            "actual_start_time": d.actual_start_time.isoformat() if d.actual_start_time else None,
            "actual_end_time": d.actual_end_time.isoformat() if d.actual_end_time else None
        }
        for d in drills
    ]}


@app.get("/api/drills/{drill_id}")
def get_drill(drill_id: int, db: Session = Depends(get_db)):
    drill = db.query(Drill).get(drill_id)
    if not drill:
        raise HTTPException(status_code=404, detail="Drill not found")
    
    steps = db.query(DrillStep).filter(DrillStep.drill_id == drill_id).order_by(DrillStep.step_number).all()
    issues = db.query(DrillIssue).filter(DrillIssue.drill_id == drill_id).all()
    
    return {
        "id": drill.id,
        "name": drill.name,
        "description": drill.description,
        "drill_type": drill.drill_type,
        "status": drill.status,
        "business_units": drill.business_units,
        "participants": drill.participants,
        "is_simulation": drill.is_simulation,
        "scenario": drill.scenario,
        "target_recovery_time": drill.target_recovery_time,
        "scheduled_start_time": drill.scheduled_start_time.isoformat() if drill.scheduled_start_time else None,
        "actual_start_time": drill.actual_start_time.isoformat() if drill.actual_start_time else None,
        "actual_end_time": drill.actual_end_time.isoformat() if drill.actual_end_time else None,
        "steps": [
            {
                "step_number": s.step_number,
                "description": s.description,
                "responsible_party": s.responsible_party,
                "target_duration_minutes": s.target_duration_minutes,
                "status": s.status,
                "is_overdue": s.is_overdue,
                "notes": s.notes
            }
            for s in steps
        ],
        "issues": [
            {
                "id": i.id,
                "description": i.description,
                "severity": i.severity,
                "identified_by": i.identified_by,
                "identified_at": i.identified_at.isoformat(),
                "resolution": i.resolution
            }
            for i in issues
        ]
    }


@app.get("/api/drills/monitor/realtime")
def realtime_monitor():
    status = get_real_time_drills_status()
    return {"active_drills": len(status), "data": status}


@app.post("/api/drills/reports/generate/{drill_id}")
def generate_report(drill_id: int):
    report = generate_drill_reports(drill_id)
    if not report:
        raise HTTPException(status_code=500, detail="Failed to generate report")
    return {
        "status": "success",
        "report_id": report.id,
        "recovery_success_rate": report.recovery_success_rate,
        "pdf_path": report.pdf_path,
        "excel_path": report.excel_path
    }


@app.get("/api/drills/reports/{drill_id}")
def get_drill_report(drill_id: int, db: Session = Depends(get_db)):
    report = db.query(DrillReport).filter(DrillReport.drill_id == drill_id).order_by(DrillReport.generated_at.desc()).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        "id": report.id,
        "drill_id": report.drill_id,
        "recovery_success_rate": report.recovery_success_rate,
        "avg_recovery_time": report.avg_recovery_time,
        "time_deviation": report.time_deviation,
        "steps_completed": report.steps_completed,
        "total_steps": report.total_steps,
        "issues_found": report.issues_found,
        "critical_issues": report.critical_issues,
        "problem_list": report.problem_list,
        "recommendations": report.recommendations,
        "pdf_path": report.pdf_path,
        "excel_path": report.excel_path,
        "generated_at": report.generated_at.isoformat()
    }


@app.get("/api/reports/monthly")
def list_monthly_reports(db: Session = Depends(get_db)):
    reports = db.query(MonthlyReport).order_by(MonthlyReport.report_month.desc()).all()
    return {"count": len(reports), "data": [
        {
            "id": r.id,
            "report_month": r.report_month,
            "drill_completion_rate": r.drill_completion_rate,
            "avg_recovery_time": r.avg_recovery_time,
            "improvement_closure_rate": r.improvement_closure_rate,
            "total_drills": r.total_drills,
            "pdf_path": r.pdf_path,
            "excel_path": r.excel_path
        }
        for r in reports
    ]}


@app.post("/api/reports/monthly/generate")
def gen_monthly_report(report_month: Optional[str] = None):
    if not report_month:
        now = datetime.now()
        prev = now.replace(day=1) - timedelta(days=1)
        report_month = prev.strftime("%Y-%m")
    
    report = generate_monthly_report(report_month)
    if not report:
        raise HTTPException(status_code=500, detail="Failed to generate monthly report")
    return {"status": "success", "report_id": report.id, "report_month": report_month}


@app.post("/api/export/lifecycle")
def export_data(
    business_unit: Optional[str] = None,
    drill_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
):
    file_path = export_lifecycle_data(business_unit, drill_type, start_date, end_date)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=500, detail="Export failed")
    
    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(file_path)
    )


@app.get("/api/work-orders")
def list_work_orders(
    business_unit: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    drill_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
):
    orders = query_work_orders(business_unit, severity, status, assignee, drill_type, start_date, end_date)
    return {"count": len(orders), "data": orders}


@app.get("/api/work-orders/stats")
def work_order_stats(business_unit: Optional[str] = None):
    return get_work_order_stats(business_unit)


@app.post("/api/work-orders/update-status")
def update_wo_status(request: WorkOrderUpdateRequest):
    order = update_work_order_status(
        request.order_id, request.status, request.performed_by,
        request.details, request.resolution
    )
    if not order:
        raise HTTPException(status_code=404, detail="Work order not found")
    return {"status": "success", "order_id": order.id, "new_status": order.status}


@app.post("/api/work-orders/reassign")
def reassign_wo(request: WorkOrderReassignRequest):
    order = reassign_work_order(
        request.order_id, request.new_assignee, request.new_department,
        request.performed_by, request.reason
    )
    if not order:
        raise HTTPException(status_code=404, detail="Work order not found")
    return {"status": "success", "order_id": order.id}


@app.get("/api/logs")
def list_logs(
    module: Optional[str] = None,
    log_level: Optional[str] = None,
    is_critical: Optional[bool] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(SystemLog)
    if module:
        query = query.filter(SystemLog.module == module)
    if log_level:
        query = query.filter(SystemLog.log_level == log_level)
    if is_critical is not None:
        query = query.filter(SystemLog.is_critical == is_critical)
    
    logs = query.order_by(SystemLog.timestamp.desc()).limit(limit).all()
    return {"count": len(logs), "data": [
        {
            "id": l.id,
            "timestamp": l.timestamp.isoformat(),
            "log_level": l.log_level,
            "module": l.module,
            "action": l.action,
            "details": l.details,
            "is_critical": l.is_critical
        }
        for l in logs
    ]}


@app.get("/api/stats/overview")
def system_overview(db: Session = Depends(get_db)):
    systems = db.query(BusinessSystem).count()
    functions = db.query(BusinessFunction).count()
    critical_functions = db.query(BusinessFunction).filter(BusinessFunction.is_critical == True).count()
    active_plans = db.query(BusinessContinuityPlan).filter(BusinessContinuityPlan.status == "active").count()
    scheduled_drills = db.query(Drill).filter(Drill.status == "scheduled").count()
    in_progress_drills = db.query(Drill).filter(Drill.status == "in_progress").count()
    completed_drills = db.query(Drill).filter(Drill.status == "completed").count()
    pending_work_orders = db.query(ImprovementWorkOrder).filter(
        ImprovementWorkOrder.status.in_(["pending", "in_progress"])
    ).count()
    
    risk_counts = {}
    for level in ["critical", "high", "medium", "low"]:
        risk_counts[level] = db.query(BusinessFunction).filter(
            BusinessFunction.risk_level == level
        ).count()
    
    return {
        "systems": systems,
        "functions": functions,
        "critical_functions": critical_functions,
        "active_plans": active_plans,
        "drills": {
            "scheduled": scheduled_drills,
            "in_progress": in_progress_drills,
            "completed": completed_drills
        },
        "pending_work_orders": pending_work_orders,
        "risk_distribution": risk_counts,
        "concurrency": task_manager.get_queue_stats()
    }


@app.get("/api/tasks/status/{task_id}")
def get_task_status(task_id: str):
    status = task_manager.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="Task not found")
    return status


@app.get("/api/tasks/stats")
def task_stats():
    return task_manager.get_queue_stats()


@app.post("/api/drills/batch-start")
def batch_start_drills(drill_ids: List[int]):
    result = run_drill_concurrently(drill_ids)
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
