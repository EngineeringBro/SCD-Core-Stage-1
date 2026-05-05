from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="notify_tasks_completed",
    display_name="Notify Tasks Completed",
    historical_total=520,
    historical_zero_comment_closes=517,
    dominant_resolutions=("Done", "Fixed / Completed", "Dismissed"),
    reasoning="Blank-topic RepairQ task completion message with a stable summary.",
    output_topic="No change",
    rule=MatchRule(
        reporter_emails=("mail@repairq.io",),
        summary_contains=("Notify of the tasks completed",),
        description_contains=("Your task",),
        description_patterns=(
            "if you have any questions or need assistance contact our support team",
            "thanks again and we appreciate your interest the repairq team",
        ),
    ),
)