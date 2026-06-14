import os
import sys
import asyncio

if os.path.exists("bcp_system.db"):
    os.remove("bcp_system.db")

from database import (
    init_db, SessionLocal, BusinessFunction, DrillStep, Drill,
    BusinessContinuityPlan, EmergencyPlanTemplate
)
from data_collection import run_daily_data_collection_and_assessment
from plan_generator import generate_bcp_for_function
from drill_manager import (
    create_simulation_drill, create_drill, start_drill, complete_drill_step,
    complete_drill, generate_drill_script, estimate_impact_range, SCENARIO_TEMPLATES
)
from work_order_manager import create_work_orders_for_drill, get_work_order_stats
from report_generator import generate_drill_pdf_report, generate_drill_excel_report
from concurrency_manager import task_manager
from datetime import datetime, timedelta

def main():
    print("=" * 60)
    print("ENTERPRISE BCP SYSTEM - SMOKE TEST")
    print("=" * 60)

    try:
        # 1. Database initialization
        print("\n[1] Initializing database...")
        init_db()
        from plan_generator import init_templates
        init_templates()
        db = SessionLocal()
        print("    OK Database initialized")

        # 2. Seed initial data
        print("\n[2] Seeding initial data (users, systems, functions)...")
        from main import _seed_initial_data
        _seed_initial_data()
        functions = db.query(BusinessFunction).all()
        print(f"    OK Seeded {len(functions)} business functions")
        for f in functions[:3]:
            print(f"      - {f.name} [risk_level={f.risk_level}, score={f.risk_score:.2f}]")

        # 3. Daily data collection and risk assessment
        print("\n[3] Running daily data collection & risk assessment...")
        result = asyncio.run(run_daily_data_collection_and_assessment())
        dc = result.get("data_collection", {})
        risk = result.get("risk_assessment", {})
        crit = result.get("critical_functions", [])
        print(f"    OK Collected {dc.get('total_systems', 0)} systems, "
              f"{dc.get('saved', 0)} data points saved")
        print(f"    Risk assessment by level: {risk.get('by_level', {})}")
        print(f"    Critical functions identified: {len(crit) if isinstance(crit, list) else 0}")

        functions = db.query(BusinessFunction).all()
        for f in functions[:3]:
            print(f"      - {f.name} [risk={f.risk_level}, score={f.risk_score:.2f}, critical={f.is_critical}]")

        # 4. BCP generation
        print("\n[4] Generating BCP plan for critical function...")
        db.expire_all()
        critical_func = db.query(BusinessFunction).filter(BusinessFunction.risk_level == "critical").first()
        if not critical_func:
            critical_func = db.query(BusinessFunction).filter(BusinessFunction.risk_level == "high").first()
        if not critical_func:
            critical_func = functions[0]
        print(f"    Using function: {critical_func.name} [risk={critical_func.risk_level}, score={critical_func.risk_score:.2f}]")

        plan = generate_bcp_for_function(critical_func.id)
        if not plan:
            raise RuntimeError("generate_bcp_for_function returned None")
        print(f"    OK Plan generated: {plan.name}")
        print(f"      RTO={plan.rto_target}h, RPO={plan.rpo_target}h, Owner={plan.responsible_person}")
        content = plan.recovery_procedures or []
        print(f"      Recovery procedures: {len(content)} items")

        # 4b. Approve the plan (needed for drill creation)
        from plan_generator import approve_plan
        approve_plan(plan.id, "admin")
        db.expire_all()
        plan = db.get(BusinessContinuityPlan, plan.id)
        print(f"    OK Plan approved: status={plan.status}, by={plan.approved_by}")

        # 5. Drill script generation & impact estimation
        print("\n[5] Generating drill script & estimating impact...")
        scenario = SCENARIO_TEMPLATES[0]
        script = generate_drill_script(critical_func, plan, scenario)
        impact = estimate_impact_range(critical_func, "high")
        print(f"    OK Drill script: {len(script.get('steps', []))} steps")
        print(f"    OK Impact: financial_loss={impact.get('estimated_financial_loss', 0)}, "
              f"users_affected={impact.get('estimated_users_affected', 0)}")

        # 6. Create simulation drill
        print("\n[6] Creating simulation drill...")
        drill_obj = create_simulation_drill(
            function_id=critical_func.id,
            scenario_name="DB Server Outage Drill",
            custom_scenario={
                "description": "Simulate primary DB server hardware failure",
                "severity": "high"
            },
            scheduled_time=datetime.utcnow() + timedelta(minutes=5)
        )
        db.expire_all()
        drill = db.get(Drill, drill_obj.id)
        print(f"    OK Drill created: {drill.name} (id={drill.id}, type={drill.drill_type})")

        # 7. Start drill and complete steps
        print("\n[7] Executing drill flow...")
        start_drill(drill.id)
        db.expire_all()
        drill = db.get(Drill, drill.id)
        print(f"    OK Drill started: status={drill.status}")

        steps = db.query(DrillStep).filter(DrillStep.drill_id == drill.id).order_by(DrillStep.step_number).all()
        for i, step in enumerate(steps[:3]):
            complete_drill_step(drill.id, step.step_number, notes=f"Completed by tester_{i}")
            db.expire_all()
            step = db.get(DrillStep, step.id)
            print(f"    OK Step {step.step_number} done: {step.description[:40]}... [{step.status}]")

        # 8. Complete drill
        print("\n[8] Completing drill...")
        complete_drill(drill.id)
        db.expire_all()
        drill = db.get(Drill, drill.id)
        total_steps = db.query(DrillStep).filter(DrillStep.drill_id == drill.id).count()
        completed_steps = db.query(DrillStep).filter(
            DrillStep.drill_id == drill.id, DrillStep.status == "completed"
        ).count()
        print(f"    OK Drill completed: status={drill.status}, "
              f"steps={completed_steps}/{total_steps}")

        # 8b. Record some drill issues (to generate work orders)
        from drill_manager import record_drill_issue
        record_drill_issue(drill.id, "RTO目标未达成，恢复时间超预期", "high", "tester_0")
        record_drill_issue(drill.id, "部分人员联系方式过时，通知延迟", "medium", "tester_1")
        print(f"    OK Recorded 2 drill issues for work order generation")

        # 9. Generate improvement work orders
        print("\n[9] Generating improvement work orders...")
        work_orders = create_work_orders_for_drill(drill.id)
        print(f"    OK Created {len(work_orders)} work orders")
        for wo in work_orders[:2]:
            db.expire_all()
            wo_refresh = db.get(type(wo), wo.id) if hasattr(wo, 'id') else wo
            print(f"      - [{wo.severity.upper()}] {wo.title[:40]} -> {wo.assignee}")

        # 10. Work order stats
        stats = get_work_order_stats()
        print(f"    OK Stats: total={stats['total']}, "
              f"overdue={stats['overdue_count']}, closure_rate={stats['closure_rate']:.1f}%")
        print(f"       By status: {stats['by_status']}")

        # 11. Generate reports
        print("\n[10] Generating drill reports (PDF & Excel)...")
        from report_generator import save_drill_report
        import os
        os.makedirs("reports", exist_ok=True)
        report = save_drill_report(drill.id)
        if not report:
            raise RuntimeError("save_drill_report returned None")
        pdf_path = generate_drill_pdf_report(report, f"reports/drill_{drill.id}_report.pdf")
        excel_path = generate_drill_excel_report(report, f"reports/drill_{drill.id}_report.xlsx")
        print(f"    OK PDF report: {pdf_path} (exists={os.path.exists(pdf_path) if pdf_path else False})")
        print(f"    OK Excel report: {excel_path} (exists={os.path.exists(excel_path) if excel_path else False})")

        # 12. Concurrency manager test
        print("\n[11] Concurrency manager test...")
        def test_task(x):
            return x * 2
        results = []
        for i in range(5):
            r = task_manager.submit_sync(test_task, i)
            results.append(r)
        print(f"    OK Sync task results: {results}")
        task_ids = task_manager.submit_batch([
            {"func": test_task, "args": (i,), "kwargs": {}} for i in range(5)
        ])
        print(f"    OK Batch submitted: {len(task_ids)} tasks")
        import time
        time.sleep(1)
        qs = task_manager.get_queue_stats()
        print(f"    OK Queue: active={qs['active_tasks']}, "
              f"queued={qs['queue_size']}, completed={qs['completed_tasks']}, "
              f"limit={qs['queue_limit']}")

        db.close()

        print("\n" + "=" * 60)
        print("ALL 11 TESTS PASSED")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n!!! TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
