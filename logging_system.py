import logging
import os
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler
import config
from database import SessionLocal, SystemLog, Notification

FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

def setup_logger(name, log_file, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not logger.handlers:
        formatter = logging.Formatter(FORMAT)
        
        file_handler = RotatingFileHandler(
            os.path.join(config.LOG_DIR, log_file),
            maxBytes=10*1024*1024,
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    return logger

data_logger = setup_logger("data_collection", "data_collection.log")
risk_logger = setup_logger("risk_assessment", "risk_assessment.log")
plan_logger = setup_logger("plan_generation", "plan_generation.log")
drill_logger = setup_logger("drill_management", "drill_management.log")
report_logger = setup_logger("report_generation", "report_generation.log")
notification_logger = setup_logger("notifications", "notifications.log")
system_logger = setup_logger("system", "system.log")
concurrency_logger = setup_logger("concurrency", "concurrency.log")


def log_system_event(log_level, module, action, details=None, user_id=None, ip_address=None, is_critical=False):
    db = SessionLocal()
    try:
        log_entry = SystemLog(
            log_level=log_level,
            module=module,
            action=action,
            details=json.dumps(details, ensure_ascii=False) if details else None,
            user_id=user_id,
            ip_address=ip_address,
            is_critical=is_critical
        )
        db.add(log_entry)
        db.commit()
        
        if is_critical and not log_entry.notified:
            send_critical_alert(log_entry)
            log_entry.notified = True
            db.commit()
        
        return log_entry.id
    except Exception as e:
        system_logger.error(f"Failed to log system event: {str(e)}")
        db.rollback()
        return None
    finally:
        db.close()


def send_critical_alert(log_entry):
    message = f"""
【BCP系统严重告警】
时间: {log_entry.timestamp}
模块: {log_entry.module}
操作: {log_entry.action}
详情: {log_entry.details}
请立即处理！
    """.strip()
    
    notification_logger.warning(f"Critical alert: {message}")
    
    if config.SECURITY_GROUP_WEBHOOK:
        try:
            import requests
            response = requests.post(
                config.SECURITY_GROUP_WEBHOOK,
                json={"msgtype": "text", "text": {"content": message}},
                timeout=10
            )
            notification_logger.info(f"Security group notification sent: {response.status_code}")
        except Exception as e:
            notification_logger.error(f"Failed to send security group notification: {str(e)}")


def create_notification(recipient, channel, message, subject=None):
    db = SessionLocal()
    try:
        notification = Notification(
            recipient=recipient,
            channel=channel,
            subject=subject,
            message=message
        )
        db.add(notification)
        db.commit()
        return notification
    except Exception as e:
        notification_logger.error(f"Failed to create notification: {str(e)}")
        db.rollback()
        return None
    finally:
        db.close()
