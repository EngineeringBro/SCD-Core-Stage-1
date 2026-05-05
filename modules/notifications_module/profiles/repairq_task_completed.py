from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="repairq_task_completed",
    display_name="RepairQ Task Completed",
    historical_total=106,
    historical_zero_comment_closes=106,
    dominant_resolutions=("Done", "Fixed / Completed", "Dismissed"),
    reasoning="Blank-topic RepairQ task completion alert with a stable summary.",
    output_topic="No change",
    rule=MatchRule(
        reporter_emails=("mail@repairq.io",),
        summary_contains=("RepairQ task completed",),
        description_contains=("Your task has finished processing",),
        description_patterns=(
            "the task created from is now complete",
            "please visit your queued tasks page in repairq to view more details",
        ),
    ),
)