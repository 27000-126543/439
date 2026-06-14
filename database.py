from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, JSON, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import config

_sqlite_connect_args = {
    "check_same_thread": False,
    "timeout": 30,
}

engine = create_engine(
    config.DATABASE_URL,
    connect_args=_sqlite_connect_args if "sqlite" in config.DATABASE_URL else {},
    pool_pre_ping=True,
    pool_recycle=3600,
)

if "sqlite" in config.DATABASE_URL:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class BusinessSystem(Base):
    __tablename__ = "business_systems"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    business_unit = Column(String(100), nullable=False)
    system_owner = Column(String(100))
    contact_email = Column(String(200))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    functions = relationship("BusinessFunction", back_populates="system")
    data_records = relationship("OperationalData", back_populates="system")


class BusinessFunction(Base):
    __tablename__ = "business_functions"

    id = Column(Integer, primary_key=True, index=True)
    system_id = Column(Integer, ForeignKey("business_systems.id"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    business_unit = Column(String(100), nullable=False)
    function_owner = Column(String(100))
    is_critical = Column(Boolean, default=False)
    dependencies = Column(JSON)
    financial_impact = Column(Float, default=0)
    operational_impact = Column(Float, default=0)
    reputational_impact = Column(Float, default=0)
    regulatory_impact = Column(Float, default=0)
    risk_score = Column(Float, default=0)
    risk_level = Column(String(20), default="low")
    priority = Column(Integer, default=999)
    rto_hours = Column(Float, default=24)
    rpo_hours = Column(Float, default=24)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    system = relationship("BusinessSystem", back_populates="functions")
    plans = relationship("BusinessContinuityPlan", back_populates="function")
    drills = relationship("Drill", back_populates="function")


class OperationalData(Base):
    __tablename__ = "operational_data"

    id = Column(Integer, primary_key=True, index=True)
    system_id = Column(Integer, ForeignKey("business_systems.id"), nullable=False)
    collection_date = Column(DateTime, default=datetime.utcnow)
    uptime_percentage = Column(Float, default=100)
    transaction_volume = Column(Float, default=0)
    response_time_avg = Column(Float, default=0)
    error_rate = Column(Float, default=0)
    active_users = Column(Integer, default=0)
    data_volume_gb = Column(Float, default=0)
    additional_metrics = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    system = relationship("BusinessSystem", back_populates="data_records")


class EmergencyPlanTemplate(Base):
    __tablename__ = "emergency_plan_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    risk_level = Column(String(20), nullable=False)
    template_content = Column(Text, nullable=False)
    recovery_steps = Column(JSON)
    required_resources = Column(JSON)
    escalation_procedure = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BusinessContinuityPlan(Base):
    __tablename__ = "business_continuity_plans"

    id = Column(Integer, primary_key=True, index=True)
    function_id = Column(Integer, ForeignKey("business_functions.id"), nullable=False)
    name = Column(String(200), nullable=False)
    version = Column(String(20), default="1.0")
    status = Column(String(20), default="draft")
    risk_level = Column(String(20))
    rto_target = Column(Float)
    rpo_target = Column(Float)
    recovery_objectives = Column(Text)
    responsible_person = Column(String(100))
    responsible_department = Column(String(100))
    contact_info = Column(JSON)
    recovery_procedures = Column(JSON)
    resource_requirements = Column(JSON)
    escalation_matrix = Column(JSON)
    generated_at = Column(DateTime, default=datetime.utcnow)
    last_reviewed_at = Column(DateTime)
    approved_by = Column(String(100))
    approved_at = Column(DateTime)

    function = relationship("BusinessFunction", back_populates="plans")
    drills = relationship("Drill", back_populates="plan")


class Drill(Base):
    __tablename__ = "drills"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("business_continuity_plans.id"))
    function_id = Column(Integer, ForeignKey("business_functions.id"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    drill_type = Column(String(50), nullable=False)
    status = Column(String(20), default="scheduled")
    business_units = Column(JSON)
    participants = Column(JSON)
    scheduled_start_time = Column(DateTime)
    actual_start_time = Column(DateTime)
    actual_end_time = Column(DateTime)
    target_recovery_time = Column(Float)
    scenario = Column(Text)
    expected_impact = Column(Text)
    is_simulation = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    plan = relationship("BusinessContinuityPlan", back_populates="drills")
    function = relationship("BusinessFunction", back_populates="drills")
    steps = relationship("DrillStep", back_populates="drill")
    issues = relationship("DrillIssue", back_populates="drill")


class DrillStep(Base):
    __tablename__ = "drill_steps"

    id = Column(Integer, primary_key=True, index=True)
    drill_id = Column(Integer, ForeignKey("drills.id"), nullable=False)
    step_number = Column(Integer, nullable=False)
    description = Column(Text, nullable=False)
    responsible_party = Column(String(100))
    target_duration_minutes = Column(Float)
    actual_start_time = Column(DateTime)
    actual_end_time = Column(DateTime)
    status = Column(String(20), default="pending")
    notes = Column(Text)
    is_overdue = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    drill = relationship("Drill", back_populates="steps")


class DrillIssue(Base):
    __tablename__ = "drill_issues"

    id = Column(Integer, primary_key=True, index=True)
    drill_id = Column(Integer, ForeignKey("drills.id"), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(20), nullable=False)
    identified_at = Column(DateTime, default=datetime.utcnow)
    identified_by = Column(String(100))
    resolution = Column(Text)
    resolved_at = Column(DateTime)

    drill = relationship("Drill", back_populates="issues")
    work_orders = relationship("ImprovementWorkOrder", back_populates="issue")


class ImprovementWorkOrder(Base):
    __tablename__ = "improvement_work_orders"

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("drill_issues.id"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    severity = Column(String(20), nullable=False)
    status = Column(String(20), default="pending")
    assignee = Column(String(100))
    assignee_department = Column(String(100))
    due_date = Column(DateTime)
    completed_at = Column(DateTime)
    escalation_level = Column(Integer, default=0)
    last_escalated_at = Column(DateTime)
    resolution_details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    issue = relationship("DrillIssue", back_populates="work_orders")
    logs = relationship("WorkOrderLog", back_populates="work_order")


class WorkOrderLog(Base):
    __tablename__ = "work_order_logs"

    id = Column(Integer, primary_key=True, index=True)
    work_order_id = Column(Integer, ForeignKey("improvement_work_orders.id"), nullable=False)
    action = Column(String(100), nullable=False)
    details = Column(Text)
    performed_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    work_order = relationship("ImprovementWorkOrder", back_populates="logs")


class DrillReport(Base):
    __tablename__ = "drill_reports"

    id = Column(Integer, primary_key=True, index=True)
    drill_id = Column(Integer, ForeignKey("drills.id"), nullable=False)
    recovery_success_rate = Column(Float, default=0)
    avg_recovery_time = Column(Float, default=0)
    time_deviation = Column(Float, default=0)
    steps_completed = Column(Integer, default=0)
    total_steps = Column(Integer, default=0)
    issues_found = Column(Integer, default=0)
    critical_issues = Column(Integer, default=0)
    problem_list = Column(JSON)
    recommendations = Column(JSON)
    generated_at = Column(DateTime, default=datetime.utcnow)
    pdf_path = Column(String(500))
    excel_path = Column(String(500))

    drill = relationship("Drill")


class MonthlyReport(Base):
    __tablename__ = "monthly_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_month = Column(String(7), nullable=False)
    business_unit = Column(String(100))
    drill_completion_rate = Column(Float, default=0)
    avg_recovery_time = Column(Float, default=0)
    improvement_closure_rate = Column(Float, default=0)
    total_drills = Column(Integer, default=0)
    completed_drills = Column(Integer, default=0)
    total_work_orders = Column(Integer, default=0)
    closed_work_orders = Column(Integer, default=0)
    trend_data = Column(JSON)
    comparison_matrix = Column(JSON)
    generated_at = Column(DateTime, default=datetime.utcnow)
    pdf_path = Column(String(500))
    excel_path = Column(String(500))


class SystemLog(Base):
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    log_level = Column(String(20), nullable=False)
    module = Column(String(100))
    action = Column(String(200), nullable=False)
    details = Column(Text)
    user_id = Column(String(100))
    ip_address = Column(String(50))
    is_critical = Column(Boolean, default=False)
    notified = Column(Boolean, default=False)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    recipient = Column(String(200), nullable=False)
    channel = Column(String(50), nullable=False)
    subject = Column(String(200))
    message = Column(Text, nullable=False)
    status = Column(String(20), default="pending")
    sent_at = Column(DateTime)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(200))
    department = Column(String(100))
    role = Column(String(50), default="user")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
