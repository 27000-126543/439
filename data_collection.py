import asyncio
import aiohttp
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import config
from database import SessionLocal, BusinessSystem, BusinessFunction, OperationalData
from logging_system import data_logger, log_system_event
from concurrency_manager import task_manager, run_concurrent_async


class DataCollector:
    def __init__(self):
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()
    
    async def collect_system_metrics(self, system: BusinessSystem) -> Optional[Dict]:
        try:
            metrics = {
                "system_id": system.id,
                "system_name": system.name,
                "uptime_percentage": round(random.uniform(95.0, 100.0), 2),
                "transaction_volume": random.randint(1000, 100000),
                "response_time_avg": round(random.uniform(0.1, 2.0), 3),
                "error_rate": round(random.uniform(0.0, 2.0), 2),
                "active_users": random.randint(10, 5000),
                "data_volume_gb": round(random.uniform(0.5, 100.0), 2),
                "additional_metrics": {
                    "cpu_usage": round(random.uniform(20.0, 90.0), 1),
                    "memory_usage": round(random.uniform(30.0, 85.0), 1),
                    "disk_usage": round(random.uniform(40.0, 95.0), 1),
                    "network_latency": round(random.uniform(1.0, 50.0), 1)
                }
            }
            
            data_logger.info(f"Collected metrics for {system.name}: uptime={metrics['uptime_percentage']}%")
            return metrics
            
        except Exception as e:
            data_logger.error(f"Failed to collect metrics for {system.name}: {str(e)}")
            return None
    
    def save_operational_data(self, metrics: Dict) -> Optional[OperationalData]:
        db = SessionLocal()
        try:
            data_record = OperationalData(
                system_id=metrics["system_id"],
                collection_date=datetime.utcnow(),
                uptime_percentage=metrics["uptime_percentage"],
                transaction_volume=metrics["transaction_volume"],
                response_time_avg=metrics["response_time_avg"],
                error_rate=metrics["error_rate"],
                active_users=metrics["active_users"],
                data_volume_gb=metrics["data_volume_gb"],
                additional_metrics=metrics["additional_metrics"]
            )
            db.add(data_record)
            db.commit()
            db.refresh(data_record)
            return data_record
        except Exception as e:
            data_logger.error(f"Failed to save operational data: {str(e)}")
            db.rollback()
            return None
        finally:
            db.close()


async def collect_all_systems_data() -> Dict:
    db = SessionLocal()
    try:
        systems = db.query(BusinessSystem).filter(BusinessSystem.is_active == True).all()
        data_logger.info(f"Starting data collection for {len(systems)} systems")
        
        collector = DataCollector()
        async with collector:
            tasks = [collector.collect_system_metrics(system) for system in systems]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        
        saved_count = 0
        failed_count = 0
        
        for result in results:
            if isinstance(result, dict):
                collector.save_operational_data(result)
                saved_count += 1
            else:
                failed_count += 1
        
        log_system_event(
            "INFO",
            "data_collection",
            "daily_data_collection",
            {
                "total_systems": len(systems),
                "saved": saved_count,
                "failed": failed_count
            }
        )
        
        return {
            "total_systems": len(systems),
            "saved": saved_count,
            "failed": failed_count
        }
        
    except Exception as e:
        data_logger.error(f"Data collection failed: {str(e)}", exc_info=True)
        log_system_event("ERROR", "data_collection", "collection_failed", {"error": str(e)}, is_critical=True)
        return {"error": str(e)}
    finally:
        db.close()


def identify_critical_functions() -> List[Dict]:
    db = SessionLocal()
    try:
        functions = db.query(BusinessFunction).all()
        critical_functions = []
        
        for func in functions:
            is_critical = False
            critical_reasons = []
            
            if func.financial_impact >= 80:
                is_critical = True
                critical_reasons.append(f"高财务影响: {func.financial_impact}")
            if func.operational_impact >= 80:
                is_critical = True
                critical_reasons.append(f"高运营影响: {func.operational_impact}")
            if func.regulatory_impact >= 70:
                is_critical = True
                critical_reasons.append(f"高合规影响: {func.regulatory_impact}")
            
            recent_data = db.query(OperationalData).filter(
                OperationalData.system_id == func.system_id,
                OperationalData.collection_date >= datetime.utcnow() - timedelta(days=7)
            ).order_by(OperationalData.collection_date.desc()).first()
            
            if recent_data:
                if recent_data.error_rate > 5.0:
                    is_critical = True
                    critical_reasons.append(f"高错误率: {recent_data.error_rate}%")
                if recent_data.uptime_percentage < 99.0:
                    is_critical = True
                    critical_reasons.append(f"低可用性: {recent_data.uptime_percentage}%")
            
            func.is_critical = is_critical
            critical_functions.append({
                "function_id": func.id,
                "function_name": func.name,
                "is_critical": is_critical,
                "reasons": critical_reasons
            })
        
        db.commit()
        
        data_logger.info(f"Identified {sum(1 for f in critical_functions if f['is_critical'])} critical functions")
        return critical_functions
        
    except Exception as e:
        data_logger.error(f"Failed to identify critical functions: {str(e)}")
        db.rollback()
        return []
    finally:
        db.close()


def analyze_dependencies() -> Dict:
    db = SessionLocal()
    try:
        functions = db.query(BusinessFunction).all()
        dependency_graph = {}
        
        for func in functions:
            deps = func.dependencies or []
            dependency_graph[func.id] = {
                "name": func.name,
                "depends_on": deps,
                "dependents": []
            }
        
        for func_id, data in dependency_graph.items():
            for dep_id in data["depends_on"]:
                if dep_id in dependency_graph:
                    dependency_graph[dep_id]["dependents"].append(func_id)
        
        for func in functions:
            if func.id in dependency_graph:
                func.dependencies = dependency_graph[func.id]["depends_on"]
        
        db.commit()
        
        data_logger.info("Dependency analysis completed")
        return dependency_graph
        
    except Exception as e:
        data_logger.error(f"Dependency analysis failed: {str(e)}")
        db.rollback()
        return {}
    finally:
        db.close()


class RiskAssessor:
    @staticmethod
    def calculate_risk_score(function: BusinessFunction) -> float:
        weights = config.RISK_WEIGHTS
        
        score = (
            function.financial_impact * weights["financial_impact"] +
            function.operational_impact * weights["operational_impact"] +
            function.reputational_impact * weights["reputational_impact"] +
            function.regulatory_impact * weights["regulatory_impact"]
        )
        
        return round(score, 2)
    
    @staticmethod
    def determine_risk_level(risk_score: float) -> str:
        if risk_score >= 80:
            return "critical"
        elif risk_score >= 60:
            return "high"
        elif risk_score >= 40:
            return "medium"
        else:
            return "low"
    
    @staticmethod
    def calculate_rto_rpo(function: BusinessFunction, risk_level: str) -> Dict:
        return {
            "rto": config.RTO_THRESHOLDS.get(risk_level, 24),
            "rpo": config.RPO_THRESHOLDS.get(risk_level, 24)
        }


def assess_all_risks() -> Dict:
    db = SessionLocal()
    try:
        functions = db.query(BusinessFunction).all()
        results = []
        
        for func in functions:
            risk_score = RiskAssessor.calculate_risk_score(func)
            risk_level = RiskAssessor.determine_risk_level(risk_score)
            rto_rpo = RiskAssessor.calculate_rto_rpo(func, risk_level)
            
            func.risk_score = risk_score
            func.risk_level = risk_level
            func.rto_hours = rto_rpo["rto"]
            func.rpo_hours = rto_rpo["rpo"]
            
            results.append({
                "function_id": func.id,
                "function_name": func.name,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "rto": rto_rpo["rto"],
                "rpo": rto_rpo["rpo"]
            })
        
        sorted_functions = sorted(results, key=lambda x: x["risk_score"], reverse=True)
        for i, result in enumerate(sorted_functions):
            func = db.query(BusinessFunction).get(result["function_id"])
            if func:
                func.priority = i + 1
                result["priority"] = i + 1
        
        db.commit()
        
        log_system_event(
            "INFO",
            "risk_assessment",
            "risk_assessment_completed",
            {
                "total_functions": len(results),
                "critical": sum(1 for r in results if r["risk_level"] == "critical"),
                "high": sum(1 for r in results if r["risk_level"] == "high"),
                "medium": sum(1 for r in results if r["risk_level"] == "medium"),
                "low": sum(1 for r in results if r["risk_level"] == "low")
            }
        )
        
        return {
            "total": len(results),
            "by_level": {
                "critical": sum(1 for r in results if r["risk_level"] == "critical"),
                "high": sum(1 for r in results if r["risk_level"] == "high"),
                "medium": sum(1 for r in results if r["risk_level"] == "medium"),
                "low": sum(1 for r in results if r["risk_level"] == "low")
            },
            "priority_list": sorted_functions
        }
        
    except Exception as e:
        data_logger.error(f"Risk assessment failed: {str(e)}", exc_info=True)
        log_system_event("ERROR", "risk_assessment", "assessment_failed", {"error": str(e)}, is_critical=True)
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()


async def run_daily_data_collection_and_assessment():
    data_logger.info("Starting daily data collection and risk assessment...")
    
    collection_result = await collect_all_systems_data()
    critical_result = identify_critical_functions()
    dependency_result = analyze_dependencies()
    risk_result = assess_all_risks()
    
    return {
        "data_collection": collection_result,
        "critical_functions": critical_result,
        "dependency_analysis": dependency_result,
        "risk_assessment": risk_result
    }
