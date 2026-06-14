import os
from datetime import time
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'bcp_system.db')}")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

SECRET_KEY = os.getenv("SECRET_KEY", "bcp-system-secret-key-2024")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440

DATA_COLLECTION_TIME = time(2, 0)
MONTHLY_REPORT_DAY = 1
DAILY_DRILL_CHECK_TIME = time(8, 0)

RISK_WEIGHTS = {
    "financial_impact": 0.35,
    "operational_impact": 0.30,
    "reputational_impact": 0.20,
    "regulatory_impact": 0.15
}

RTO_THRESHOLDS = {
    "critical": 4,
    "high": 8,
    "medium": 24,
    "low": 72
}

RPO_THRESHOLDS = {
    "critical": 1,
    "high": 4,
    "medium": 24,
    "low": 168
}

NOTIFICATION_CHANNELS = {
    "email": True,
    "sms": False,
    "wechat": True,
    "security_group": True
}

SECURITY_GROUP_WEBHOOK = os.getenv("SECURITY_GROUP_WEBHOOK", "")

CONCURRENCY_SETTINGS = {
    "max_workers": 50,
    "queue_limit": 5000,
    "task_timeout": 3600
}

LOG_DIR = os.path.join(BASE_DIR, "logs")
REPORT_DIR = os.path.join(BASE_DIR, "reports")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")

for directory in [LOG_DIR, REPORT_DIR, EXPORT_DIR]:
    os.makedirs(directory, exist_ok=True)

DRILL_TYPES = ["tabletop", "functional", "full_scale", "simulation"]
BUSINESS_UNITS = ["IT", "Finance", "Operations", "Sales", "HR", "Legal", "CustomerService"]
SEVERITY_LEVELS = ["low", "medium", "high", "critical"]
WORK_ORDER_STATUS = ["pending", "in_progress", "completed", "overdue", "escalated"]
