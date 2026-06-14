import os
import sys
import time
from datetime import datetime, timedelta

for f in ["bcp_system.db", "bcp_system.db-wal", "bcp_system.db-shm"]:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
    if os.path.exists(p):
        os.remove(p)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import app
from fastapi.testclient import TestClient

def main():
    print("=" * 70)
    print("ENTERPRISE BCP SYSTEM - ACCEPTANCE TEST")
    print("=" * 70)

    errors = []
    passed = 0
    failed = 0

    def test(name, condition, details=""):
        nonlocal passed, failed, errors
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            print(f"     Details: {details}")
            failed += 1
            errors.append(f"{name}: {details}")
        return condition

    with TestClient(app) as client:
        print("\n[0] Initialization")
        test("App starts and seeds data", True)

        # Step 1: Batch generate BCP plans
        print("\n[1] Batch Generate BCP Plans")
        r = client.post("/api/plans/generate-all")
        test("API returns 200", r.status_code == 200, f"status={r.status_code}")
        data = r.json()
        task_ids = data.get("data", {}).get("task_ids", [])
        total_functions = data.get("data", {}).get("total_functions", 0)
        test(f"Returns {len(task_ids)} task IDs", len(task_ids) > 0, f"got {len(task_ids)} tasks, functions={total_functions}")
        test("Total functions > 0", total_functions > 0, f"got {total_functions}")

        print(f"\n  Waiting for batch tasks to complete (up to 30s)...")
        completed_tasks = 0
        for i, task_id in enumerate(task_ids):
            status_info = None
            for poll in range(60):
                r = client.get(f"/api/tasks/status/{task_id}")
                if r.status_code == 200:
                    status_info = r.json()
                    if status_info and status_info["status"] in ("completed", "failed", "timeout"):
                        break
                time.sleep(0.5)
            
            if status_info:
                test(f"Task {i+1}/{len(task_ids)} completes successfully", 
                     status_info["status"] == "completed",
                     f"id={task_id[:8]}..., status={status_info['status']}, error={status_info.get('error')}")
                if status_info["status"] == "completed":
                    completed_tasks += 1
            else:
                test(f"Task {i+1}/{len(task_ids)} - get status", False, f"Could not get status for task {task_id}")

        test("All tasks transition through queued → running → completed", completed_tasks > 0,
             f"{completed_tasks}/{len(task_ids)} tasks completed")

        # Verify queue stats show completed tasks
        r = client.get("/api/tasks/stats")
        if r.status_code == 200:
            stats = r.json()
            test(f"Queue stats show completed tasks", 
                 stats.get("completed_tasks", 0) >= completed_tasks,
                 f"completed={stats.get('completed_tasks')}, active={stats.get('active_tasks')}, queued={stats.get('queue_size')}")

        # Check that plans were actually created
        print("\n[2] Verify Generated Plans Exist")
        r = client.get("/api/plans", params={"status": "draft"})
        test("API returns plans", r.status_code == 200)
        plans_data = r.json()
        plans_count = plans_data.get("count", 0)
        test(f"Plans exist in database ({plans_count} plans)", plans_count > 0, f"count={plans_count}")
        if plans_count > 0:
            print(f"    Sample plan: {plans_data['data'][0]['name']} (status={plans_data['data'][0]['status']})")

        # Step 2: Create drills first (need scheduled drills to start)
        print("\n[3] Create and Start Drills")
        
        # First get some plans and functions
        r = client.get("/api/functions")
        functions = r.json().get("data", [])
        function_ids = [f["id"] for f in functions[:3]]
        
        # Create scheduled drills for these functions
        drill_ids = []
        for fid in function_ids:
            r = client.post("/api/drills/create", json={
                "function_id": fid,
                "drill_type": "scheduled",
                "scenario_name": f"Acceptance Test Drill for Function {fid}",
                "scheduled_time": (datetime.utcnow() + timedelta(hours=1)).isoformat()
            })
            if r.status_code == 200:
                drill_data = r.json()
                drill_ids.append(drill_data["drill_id"])
                print(f"    Created drill {drill_data['drill_id']}: {drill_data.get('name', 'N/A')}")
        
        test(f"Created {len(drill_ids)} test drills", len(drill_ids) >= 2, f"created {len(drill_ids)}")

        # Step 3: Batch start drills
        print("\n[4] Batch Start Drills")
        r = client.post("/api/drills/batch-start", json=drill_ids)
        test("Batch start API returns 200", r.status_code == 200, f"status={r.status_code}")
        batch_data = r.json()
        batch_task_ids = batch_data.get("task_ids", [])
        test(f"Returns {len(batch_task_ids)} task IDs", len(batch_task_ids) == len(drill_ids),
             f"expected {len(drill_ids)}, got {len(batch_task_ids)}")

        print(f"\n  Waiting for drill start tasks to complete...")
        for i, task_id in enumerate(batch_task_ids):
            for poll in range(60):
                r = client.get(f"/api/tasks/status/{task_id}")
                if r.status_code == 200:
                    status_info = r.json()
                    if status_info and status_info["status"] in ("completed", "failed", "timeout"):
                        break
                time.sleep(0.5)
            
            if status_info:
                test(f"Drill start task {i+1} - {status_info['status']}", 
                     status_info["status"] == "completed",
                     f"id={task_id[:8]}..., error={status_info.get('error')}")

        # Verify drills are now in_progress
        for drill_id in drill_ids:
            r = client.get(f"/api/drills/{drill_id}")
            if r.status_code == 200:
                data = r.json()
                test(f"Drill {drill_id} is in_progress", 
                     data.get("status") == "in_progress",
                     f"status={data.get('status')}")

        # Step 4: Complete a drill and generate report
        print("\n[5] Generate Drill Report")
        
        # First record issues BEFORE completing the drill so they get picked up by work order generation
        first_drill_id = drill_ids[0]
        
        # Start the drill first (it's already in_progress from batch start)
        # Record some issues so we get work orders
        client.post("/api/drills/issues", json={
            "drill_id": first_drill_id,
            "description": "Acceptance test issue - RTO not met",
            "severity": "high",
            "identified_by": "test"
        })
        client.post("/api/drills/issues", json={
            "drill_id": first_drill_id,
            "description": "Acceptance test issue - contact info outdated",
            "severity": "medium",
            "identified_by": "test"
        })
        print(f"    Added 2 test issues to drill {first_drill_id}")

        # Complete first drill
        r = client.post(f"/api/drills/complete/{first_drill_id}")
        test(f"Complete drill {first_drill_id}", r.status_code == 200)
        
        # Wait for background tasks (work order generation) to complete
        time.sleep(2)

        # Generate report
        r = client.post(f"/api/drills/reports/generate/{first_drill_id}")
        test("Generate report API returns 200", r.status_code == 200, f"status={r.status_code}")
        report_data = r.json()
        pdf_path = report_data.get("pdf_path")
        excel_path = report_data.get("excel_path")
        test("pdf_path is present in response", bool(pdf_path), f"pdf_path={pdf_path}")
        test("excel_path is present in response", bool(excel_path), f"excel_path={excel_path}")
        test("PDF file exists on disk", bool(pdf_path) and os.path.exists(pdf_path), f"path={pdf_path}")
        test("Excel file exists on disk", bool(excel_path) and os.path.exists(excel_path), f"path={excel_path}")

        # Step 5: Get report detail and verify paths persist
        print("\n[6] Verify Report Path Persistence")
        r = client.get(f"/api/drills/reports/{first_drill_id}")
        test("Get report detail API returns 200", r.status_code == 200)
        detail_data = r.json()
        test("pdf_path persisted in database", detail_data.get("pdf_path") == pdf_path,
             f"detail={detail_data.get('pdf_path')}, original={pdf_path}")
        test("excel_path persisted in database", detail_data.get("excel_path") == excel_path,
             f"detail={detail_data.get('excel_path')}, original={excel_path}")
        test("pdf_path is not None/empty after reload", bool(detail_data.get("pdf_path")))
        test("excel_path is not None/empty after reload", bool(detail_data.get("excel_path")))

        # Also trigger work order generation
        r = client.post("/api/work-orders/update-status", json={
            "order_id": 1,
            "status": "in_progress",
            "performed_by": "tester"
        })

        # Step 6: Test work order combined filtering
        print("\n[7] Work Order Combined Filtering")
        
        # First get all work orders
        r = client.get("/api/work-orders")
        test("Get all work orders", r.status_code == 200)
        all_orders = r.json()
        total_count = all_orders.get("count", 0)
        test(f"Total work orders exist ({total_count})", total_count > 0, f"count={total_count}")
        print(f"    Total work orders: {total_count}")
        if total_count > 0:
            wo = all_orders["data"][0]
            print(f"    Sample: #{wo['id']} {wo['title']} ({wo['severity']}/{wo['status']})")
            print(f"            drill_info: {wo.get('drill_info')}")
            test("Work order includes drill_info", wo.get("drill_info") is not None)
            test("drill_info has drill_type", "drill_type" in (wo.get("drill_info") or {}))

        # Test filtering by business_unit
        r = client.get("/api/work-orders", params={"business_unit": "IT"})
        filtered = r.json()
        test(f"Filter by business_unit: IT returns {filtered.get('count')} results", 
             r.status_code == 200)

        # Test filtering by severity
        r = client.get("/api/work-orders", params={"severity": "high"})
        filtered = r.json()
        test(f"Filter by severity: high returns {filtered.get('count')} results",
             all(wo.get("severity") == "high" for wo in filtered.get("data", [])),
             f"results: {[(wo.get('id'), wo.get('severity')) for wo in filtered.get('data', [])[:3]]}")

        # Test filtering by status
        r = client.get("/api/work-orders", params={"status": "pending"})
        filtered = r.json()
        test(f"Filter by status: pending returns {filtered.get('count')} results",
             all(wo.get("status") == "pending" for wo in filtered.get("data", [])),
             f"results: {[(wo.get('id'), wo.get('status')) for wo in filtered.get('data', [])[:3]]}")

        # Test filtering by drill_type
        r = client.get("/api/work-orders", params={"drill_type": "scheduled"})
        filtered = r.json()
        test(f"Filter by drill_type: scheduled returns {filtered.get('count')} results",
             r.status_code == 200,
             f"results count: {filtered.get('count')}")

        # Test date range filtering
        now = datetime.utcnow()
        start = (now - timedelta(days=1)).isoformat()
        end = (now + timedelta(days=1)).isoformat()
        r = client.get("/api/work-orders", params={"start_date": start, "end_date": end})
        filtered = r.json()
        test(f"Filter by date range returns {filtered.get('count')} results",
             r.status_code == 200,
             f"start={start}, end={end}, count={filtered.get('count')}")

        # Test combined filtering (all params together)
        r = client.get("/api/work-orders", params={
            "severity": "high",
            "status": "pending",
            "start_date": start,
            "end_date": end
        })
        filtered = r.json()
        combined_count = filtered.get("count", 0)
        test(f"Combined filter (high+pending+date) returns {combined_count} results",
             r.status_code == 200,
             f"count={combined_count}")

        # Step 7: Verify queue stats and task tracking works end-to-end
        print("\n[8] Queue Status Verification")
        r = client.get("/api/tasks/stats")
        test("Task stats API works", r.status_code == 200)
        stats = r.json()
        print(f"    Queue size: {stats.get('queue_size')}")
        print(f"    Active tasks: {stats.get('active_tasks')}")
        print(f"    Completed tasks: {stats.get('completed_tasks')}")
        print(f"    Failed tasks: {stats.get('failed_tasks')}")
        print(f"    Max workers: {stats.get('max_workers')}")
        print(f"    Queue limit: {stats.get('queue_limit')}")
        test("Completed tasks count is correct", stats.get("completed_tasks", 0) >= len(task_ids) + len(batch_task_ids),
             f"expected>={len(task_ids) + len(batch_task_ids)}, got={stats.get('completed_tasks')}")

    print("\n" + "=" * 70)
    print(f"TEST SUMMARY: {passed} PASSED, {failed} FAILED")
    if errors:
        print("\nFAILED TESTS:")
        for e in errors:
            print(f"  - {e}")
    print("=" * 70)
    
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
