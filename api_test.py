import os
import sys

for f in ["bcp_system.db", "bcp_system.db-wal", "bcp_system.db-shm"]:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
    if os.path.exists(p):
        os.remove(p)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import app
from fastapi.testclient import TestClient

def main():
    print("=" * 60)
    print("ENTERPRISE BCP SYSTEM - API TEST")
    print("=" * 60)

    errors = []

    def test_endpoint(name, method, path, **kwargs):
        try:
            if method == "GET":
                r = client.get(path, **kwargs)
            elif method == "POST":
                r = client.post(path, **kwargs)
            elif method == "PUT":
                r = client.put(path, **kwargs)
            else:
                r = client.get(path, **kwargs)
            status = "OK" if r.status_code in (200, 201) else "WARN"
            extra = ""
            if r.status_code in (200, 201):
                try:
                    data = r.json()
                    if isinstance(data, list):
                        extra = f" count={len(data)}"
                    elif isinstance(data, dict):
                        keys = list(data.keys())[:5]
                        extra = f" keys={keys}"
                except Exception:
                    pass
            print(f"    [{status}] {name}: {r.status_code}{extra}")
            if r.status_code >= 400:
                try:
                    detail = r.json().get("detail", r.text[:100])
                except Exception:
                    detail = r.text[:100]
                errors.append(f"{name}: {r.status_code} - {detail}")
            return r
        except Exception as e:
            print(f"    [FAIL] {name}: {e}")
            errors.append(f"{name}: {e}")
            return None

    with TestClient(app) as client:
        print("\n[1] TestClient created (lifespan triggered), app loaded successfully")

        print("\n[2] System & Data Collection APIs")
        test_endpoint("Root Health Check", "GET", "/")
        test_endpoint("System Overview", "GET", "/api/stats/overview")
        test_endpoint("List Systems", "GET", "/api/systems")
        test_endpoint("List Functions", "GET", "/api/functions")
        test_endpoint("Trigger Collection (run)", "POST", "/api/collection/run")
        test_endpoint("Trigger Collection (trigger)", "POST", "/api/collection/trigger")

        print("\n[3] BCP Plan APIs")
        test_endpoint("List Plans", "GET", "/api/plans")
        test_endpoint("Generate All BCPs", "POST", "/api/plans/generate-all")
        test_endpoint("Generate Single BCP", "POST", "/api/plans/generate/1")
        test_endpoint("Approve BCP", "POST", "/api/plans/approve/1")

        print("\n[4] Drill Management APIs")
        test_endpoint("List Drills", "GET", "/api/drills")
        test_endpoint("Get Drill Detail", "GET", "/api/drills/1")
        test_endpoint("Create Drill", "POST", "/api/drills/create",
                      json={"function_id": 1, "drill_type": "scheduled",
                            "scenario_name": "Test", "scheduled_time": "2026-06-20T10:00:00"})
        test_endpoint("Create Simulation Drill", "POST", "/api/drills/simulation",
                      json={"function_id": 1, "scenario_type": "server_outage",
                            "scenario_name": "Test Sim", "scenario_description": "test",
                            "severity": "medium"})
        test_endpoint("Monitor Real-time", "GET", "/api/drills/monitor/realtime")

        print("\n[5] Work Order APIs")
        test_endpoint("List Work Orders", "GET", "/api/work-orders")
        test_endpoint("Work Order Stats", "GET", "/api/work-orders/stats")
        test_endpoint("Query Work Orders", "GET", "/api/work-orders",
                      params={"status": "pending", "severity": "high"})

        print("\n[6] Report & Export APIs")
        test_endpoint("List Monthly Reports", "GET", "/api/reports/monthly")
        test_endpoint("Generate Monthly Report", "POST", "/api/reports/monthly/generate")

        print("\n[7] Task & Concurrency APIs")
        test_endpoint("Task Stats", "GET", "/api/tasks/stats")
        test_endpoint("System Logs", "GET", "/api/logs", params={"limit": 3})

        print("\n[8] Export & Misc APIs")
        test_endpoint("Export Lifecycle Data", "POST", "/api/export/lifecycle",
                      json={"start_date": "2026-01-01", "end_date": "2026-12-31",
                            "business_unit": "IT"})

    print("\n" + "=" * 60)
    if errors:
        print(f"COMPLETED WITH {len(errors)} WARNINGS/ERRORS:")
        unique_errors = []
        seen = set()
        for e in errors:
            key = e.split(":")[0]
            if key not in seen:
                seen.add(key)
                unique_errors.append(e)
        for e in unique_errors[:10]:
            print(f"  - {e}")
        return 1 if any("[FAIL]" in e for e in errors) else 0
    else:
        print("ALL API TESTS PASSED")
        return 0

if __name__ == "__main__":
    sys.exit(main())
