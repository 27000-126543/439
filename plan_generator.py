import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import config
from database import (
    SessionLocal, BusinessFunction, EmergencyPlanTemplate,
    BusinessContinuityPlan, User
)
from logging_system import plan_logger, log_system_event
from concurrency_manager import task_manager


RESPONSIBILITY_MATRIX = {
    "critical": {
        "approval_level": "CEO",
        "coordinator": "BCP Manager",
        "escalation_hours": 1
    },
    "high": {
        "approval_level": "Department Head",
        "coordinator": "Senior BCP Specialist",
        "escalation_hours": 4
    },
    "medium": {
        "approval_level": "Department Manager",
        "coordinator": "BCP Specialist",
        "escalation_hours": 8
    },
    "low": {
        "approval_level": "Team Lead",
        "coordinator": "BCP Analyst",
        "escalation_hours": 24
    }
}


DEFAULT_TEMPLATES = [
    {
        "name": "关键系统故障应急预案",
        "description": "针对关键业务系统完全中断的紧急响应流程",
        "risk_level": "critical",
        "template_content": """
# 关键系统故障应急预案

## 1. 应急响应目标
确保在关键系统发生重大故障时，能够在规定的RTO内恢复核心业务功能，将业务影响降至最低。

## 2. 适用范围
本预案适用于所有被标记为"关键"级别的业务系统。

## 3. 应急组织机构
### 3.1 指挥组
- 总指挥：CEO
- 副总指挥：CTO/COO
- 职责：决策重大事项，协调资源

### 3.2 技术恢复组
- 组长：IT部门经理
- 成员：系统架构师、DBA、网络工程师
- 职责：技术故障排查与系统恢复

### 3.3 业务协调组
- 组长：业务部门经理
- 成员：各业务线负责人
- 职责：业务影响评估，用户沟通

### 3.4 后勤保障组
- 组长：行政部门经理
- 成员：行政人员
- 职责：物资保障，场地协调

## 4. 应急响应流程
### 4.1 故障发现与报告（0-15分钟）
1. 监控系统自动告警
2. 值班人员确认故障
3. 立即报告BCP协调员

### 4.2 故障评估（15-30分钟）
1. 技术组初步排查故障原因
2. 评估影响范围和严重程度
3. 启动相应级别预案

### 4.3 应急处置（30分钟-RTO目标）
1. 执行系统恢复步骤
2. 启用备用系统/手工流程
3. 及时更新恢复进度

### 4.4 业务恢复验证
1. 技术组确认系统恢复
2. 业务组验证业务功能
3. 记录恢复时间和数据

## 5. 恢复步骤
[根据具体业务系统填写详细恢复步骤]

## 6.  escalation流程
- 1小时内未恢复：升级至CEO
- 4小时内未恢复：启动董事会应急会议

## 7. 事后复盘
- 24小时内提交初步报告
- 72小时内完成详细根因分析
- 1周内制定改进措施
        """,
        "recovery_steps": [
            {"step": 1, "description": "确认故障并启动应急预案", "duration": 15},
            {"step": 2, "description": "通知相关人员到位", "duration": 15},
            {"step": 3, "description": "评估故障影响范围", "duration": 30},
            {"step": 4, "description": "执行系统恢复操作", "duration": 120},
            {"step": 5, "description": "验证系统功能", "duration": 30},
            {"step": 6, "description": "业务部门验收", "duration": 15}
        ],
        "required_resources": [
            "备用服务器",
            "数据库备份",
            "网络设备",
            "应急通信设备",
            "恢复手册"
        ],
        "escalation_procedure": "15分钟未定位故障→升级技术经理；1小时未恢复→升级CTO；4小时未恢复→升级CEO"
    },
    {
        "name": "高风险业务中断预案",
        "description": "针对高风险级别的业务功能中断预案",
        "risk_level": "high",
        "template_content": """
# 高风险业务中断应急预案

## 1. 目标
在RTO规定时间内恢复业务功能，确保核心业务不受严重影响。

## 2. 应急流程
1. 故障确认与报告
2. 应急小组启动
3. 故障排查与定位
4. 执行恢复操作
5. 系统验证与业务确认
6. 恢复运行

## 3. 职责分工
[根据业务部门具体填写]

## 4. 技术恢复步骤
[根据具体系统填写]
        """,
        "recovery_steps": [
            {"step": 1, "description": "故障确认", "duration": 10},
            {"step": 2, "description": "人员召集", "duration": 20},
            {"step": 3, "description": "故障排查", "duration": 60},
            {"step": 4, "description": "系统恢复", "duration": 240},
            {"step": 5, "description": "验证测试", "duration": 30}
        ],
        "required_resources": [
            "技术支持团队",
            "备用系统",
            "数据备份"
        ],
        "escalation_procedure": "30分钟未定位→升级部门经理；2小时未恢复→升级分管副总；8小时未恢复→升级总经理"
    },
    {
        "name": "中等风险业务恢复预案",
        "description": "针对中等风险级别的业务功能中断",
        "risk_level": "medium",
        "template_content": """
# 中等风险业务恢复预案

## 1. 目标
在一个工作日内恢复业务功能。

## 2. 响应流程
1. 故障登记
2. 技术人员排查
3. 执行恢复步骤
4. 验证恢复结果
5. 通知用户

## 3. 恢复步骤
[根据具体业务填写]
        """,
        "recovery_steps": [
            {"step": 1, "description": "故障登记", "duration": 5},
            {"step": 2, "description": "技术人员处理", "duration": 120},
            {"step": 3, "description": "恢复验证", "duration": 15}
        ],
        "required_resources": ["技术支持人员"],
        "escalation_procedure": "2小时未解决→升级技术组长；8小时未解决→升级部门经理"
    },
    {
        "name": "低风险业务支持预案",
        "description": "针对低风险级别业务的支持预案",
        "risk_level": "low",
        "template_content": """
# 低风险业务支持预案

## 1. 目标
在三个工作日内恢复业务功能。

## 2. 处理流程
1. 问题提交
2. 技术人员处理
3. 结果反馈
4. 用户确认
        """,
        "recovery_steps": [
            {"step": 1, "description": "问题登记", "duration": 5},
            {"step": 2, "description": "问题处理", "duration": 480},
            {"step": 3, "description": "结果确认", "duration": 10}
        ],
        "required_resources": ["技术支持人员"],
        "escalation_procedure": "24小时未解决→升级技术组长"
    }
]


def init_templates() -> int:
    db = SessionLocal()
    try:
        count = 0
        for template_data in DEFAULT_TEMPLATES:
            existing = db.query(EmergencyPlanTemplate).filter(
                EmergencyPlanTemplate.name == template_data["name"]
            ).first()
            if not existing:
                template = EmergencyPlanTemplate(
                    name=template_data["name"],
                    description=template_data["description"],
                    risk_level=template_data["risk_level"],
                    template_content=template_data["template_content"],
                    recovery_steps=template_data["recovery_steps"],
                    required_resources=template_data["required_resources"],
                    escalation_procedure=template_data["escalation_procedure"]
                )
                db.add(template)
                count += 1
        db.commit()
        plan_logger.info(f"Initialized {count} plan templates")
        return count
    except Exception as e:
        plan_logger.error(f"Failed to initialize templates: {str(e)}")
        db.rollback()
        return 0
    finally:
        db.close()


def match_template(risk_level: str) -> Optional[EmergencyPlanTemplate]:
    db = SessionLocal()
    try:
        template = db.query(EmergencyPlanTemplate).filter(
            EmergencyPlanTemplate.risk_level == risk_level
        ).order_by(EmergencyPlanTemplate.created_at.desc()).first()
        return template
    except Exception as e:
        plan_logger.error(f"Template matching failed: {str(e)}")
        return None
    finally:
        db.close()


def get_responsible_person(business_unit: str, risk_level: str) -> Dict:
    db = SessionLocal()
    try:
        users = db.query(User).filter(
            User.department == business_unit,
            User.is_active == True
        ).all()
        
        role_hierarchy = {
            "critical": ["CEO", "CTO", "COO"],
            "high": ["Department Head", "Senior Manager"],
            "medium": ["Manager", "Team Lead"],
            "low": ["Team Lead", "Senior Engineer"]
        }
        
        preferred_roles = role_hierarchy.get(risk_level, ["Team Lead"])
        
        for role in preferred_roles:
            for user in users:
                if role.lower() in (user.role or "").lower():
                    return {
                        "name": user.username,
                        "email": user.email,
                        "department": user.department,
                        "role": user.role
                    }
        
        if users:
            return {
                "name": users[0].username,
                "email": users[0].email,
                "department": users[0].department,
                "role": users[0].role
            }
        
        return {
            "name": f"{business_unit}负责人",
            "email": f"{business_unit.lower()}@company.com",
            "department": business_unit,
            "role": "TBD"
        }
    except Exception as e:
        plan_logger.error(f"Failed to get responsible person: {str(e)}")
        return {
            "name": f"{business_unit}负责人",
            "email": f"{business_unit.lower()}@company.com",
            "department": business_unit,
            "role": "TBD"
        }
    finally:
        db.close()


def generate_plan_content(function: BusinessFunction, template: EmergencyPlanTemplate) -> Dict:
    resp = get_responsible_person(function.business_unit, function.risk_level)
    resp_info = RESPONSIBILITY_MATRIX.get(function.risk_level, RESPONSIBILITY_MATRIX["medium"])
    
    recovery_objectives = f"""
## 恢复目标
- **RTO（恢复时间目标）**: {function.rto_hours} 小时
- **RPO（恢复点目标）**: {function.rpo_hours} 小时
- **业务功能**: {function.name}
- **风险等级**: {function.risk_level.upper()}
- **优先级**: {function.priority}

## 影响评估
- 财务影响: {function.financial_impact}/100
- 运营影响: {function.operational_impact}/100
- 声誉影响: {function.reputational_impact}/100
- 合规影响: {function.regulatory_impact}/100
- 综合风险评分: {function.risk_score}/100

## 依赖关系
- 依赖系统: {json.dumps(function.dependencies, ensure_ascii=False) if function.dependencies else '无'}
    """.strip()
    
    contact_info = {
        "primary": resp,
        "escalation": {
            "level1": {
                "name": resp_info["coordinator"],
                "role": "BCP Coordinator",
                "escalation_hours": resp_info["escalation_hours"]
            },
            "level2": {
                "name": resp_info["approval_level"],
                "role": "Approval Authority"
            }
        },
        "technical_contacts": [],
        "business_contacts": []
    }
    
    recovery_procedures = template.recovery_steps if template.recovery_steps else []
    
    escalation_matrix = {
        "level1": {
            "trigger": f"故障发生后{resp_info['escalation_hours']}小时未恢复",
            "notify": resp_info["coordinator"],
            "action": "协调更多资源"
        },
        "level2": {
            "trigger": f"故障发生后{resp_info['escalation_hours'] * 2}小时未恢复",
            "notify": resp_info["approval_level"],
            "action": "重大事项决策"
        },
        "level3": {
            "trigger": f"故障发生后{resp_info['escalation_hours'] * 4}小时未恢复",
            "notify": "Executive Team",
            "action": "启动危机管理流程"
        }
    }
    
    return {
        "recovery_objectives": recovery_objectives,
        "responsible_person": resp["name"],
        "responsible_department": function.business_unit,
        "contact_info": contact_info,
        "recovery_procedures": recovery_procedures,
        "resource_requirements": template.required_resources if template.required_resources else [],
        "escalation_matrix": escalation_matrix,
        "rto_target": function.rto_hours,
        "rpo_target": function.rpo_hours
    }


def generate_bcp_for_function(function_id: int) -> Optional[BusinessContinuityPlan]:
    db = SessionLocal()
    try:
        function = db.query(BusinessFunction).get(function_id)
        if not function:
            plan_logger.error(f"Function {function_id} not found")
            return None
        
        template = match_template(function.risk_level)
        if not template:
            plan_logger.warning(f"No template found for risk level {function.risk_level}, using medium template")
            template = match_template("medium")
            if not template:
                plan_logger.error("No template available")
                return None
        
        plan_content = generate_plan_content(function, template)
        
        existing_plan = db.query(BusinessContinuityPlan).filter(
            BusinessContinuityPlan.function_id == function_id,
            BusinessContinuityPlan.status == "active"
        ).first()
        
        if existing_plan:
            version_parts = existing_plan.version.split(".")
            major = int(version_parts[0])
            minor = int(version_parts[1]) if len(version_parts) > 1 else 0
            new_version = f"{major}.{minor + 1}"
            existing_plan.status = "superseded"
        else:
            new_version = "1.0"
        
        plan = BusinessContinuityPlan(
            function_id=function_id,
            name=f"{function.name} - 业务连续性计划",
            version=new_version,
            status="draft",
            risk_level=function.risk_level,
            **plan_content
        )
        
        db.add(plan)
        db.commit()
        db.refresh(plan)
        plan_id = plan.id
        
        plan_logger.info(f"Generated BCP for {function.name}, plan ID: {plan_id}")
        
        db.close()
        db2 = SessionLocal()
        try:
            return db2.query(BusinessContinuityPlan).get(plan_id)
        finally:
            db2.close()
        
    except Exception as e:
        plan_logger.error(f"Failed to generate BCP for function {function_id}: {str(e)}", exc_info=True)
        if 'db' in locals() and db:
            db.rollback()
        return None
    finally:
        if 'db' in locals() and db:
            try:
                db.close()
            except Exception:
                pass


def generate_all_bcps() -> Dict:
    db = SessionLocal()
    try:
        init_templates()
        
        functions = db.query(BusinessFunction).all()
        plan_logger.info(f"Generating BCPs for {len(functions)} functions")
        
        tasks = []
        for func in functions:
            tasks.append({
                "func": generate_bcp_for_function,
                "args": (func.id,),
                "kwargs": {"priority": 1}
            })
        
        task_ids = task_manager.submit_batch(tasks)
        plan_logger.info(f"Submitted {len(task_ids)} BCP generation tasks")
        
        return {
            "total_functions": len(functions),
            "tasks_submitted": len(task_ids),
            "task_ids": task_ids
        }
        
    except Exception as e:
        plan_logger.error(f"BCP generation failed: {str(e)}", exc_info=True)
        log_system_event("ERROR", "plan_generation", "generation_failed", {"error": str(e)}, is_critical=True)
        return {"error": str(e)}
    finally:
        db.close()


def approve_plan(plan_id: int, approver: str) -> Optional[BusinessContinuityPlan]:
    db = SessionLocal()
    try:
        plan = db.query(BusinessContinuityPlan).get(plan_id)
        if not plan:
            return None
        
        plan.status = "active"
        plan.approved_by = approver
        plan.approved_at = datetime.utcnow()
        plan.last_reviewed_at = datetime.utcnow()
        
        db.commit()
        db.refresh(plan)
        plan_id = plan.id
        
        log_system_event(
            "INFO",
            "plan_generation",
            "plan_approved",
            {"plan_id": plan_id, "approver": approver}
        )
        
        db.close()
        db2 = SessionLocal()
        try:
            return db2.query(BusinessContinuityPlan).get(plan_id)
        finally:
            db2.close()
    except Exception as e:
        plan_logger.error(f"Failed to approve plan: {str(e)}")
        if db:
            db.rollback()
        return None
    finally:
        if db:
            db.close()
