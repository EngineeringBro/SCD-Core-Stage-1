from modules.notifications_module.profiles.assurant_inventory_error import PROFILE as ASSURANT_INVENTORY_ERROR
from modules.notifications_module.profiles.asurion_inventory_error import PROFILE as ASURION_INVENTORY_ERROR
from modules.notifications_module.profiles.azure_notification import PROFILE as AZURE_NOTIFICATION
from modules.notifications_module.profiles.gsx_permission_violation import PROFILE as GSX_PERMISSION_VIOLATION
from modules.notifications_module.profiles.notify_tasks_completed import PROFILE as NOTIFY_TASKS_COMPLETED
from modules.notifications_module.profiles.quickbooks_journal_entry_error import PROFILE as QUICKBOOKS_JOURNAL_ENTRY_ERROR
from modules.notifications_module.profiles.repairq_task_completed import PROFILE as REPAIRQ_TASK_COMPLETED
from modules.notifications_module.profiles.revv_error_report import PROFILE as REVV_ERROR_REPORT
from modules.notifications_module.profiles.welcome_to_repairq import PROFILE as WELCOME_TO_REPAIRQ

PROFILES = (
    ASURION_INVENTORY_ERROR,
    ASSURANT_INVENTORY_ERROR,
    REVV_ERROR_REPORT,
    AZURE_NOTIFICATION,
    NOTIFY_TASKS_COMPLETED,
    WELCOME_TO_REPAIRQ,
    GSX_PERMISSION_VIOLATION,
    QUICKBOOKS_JOURNAL_ENTRY_ERROR,
    REPAIRQ_TASK_COMPLETED,
)

__all__ = ["PROFILES"]