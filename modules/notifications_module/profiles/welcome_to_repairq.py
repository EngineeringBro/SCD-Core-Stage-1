from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="welcome_to_repairq",
    display_name="Welcome to RepairQ",
    historical_total=416,
    historical_zero_comment_closes=413,
    dominant_resolutions=("Done", "Fixed / Completed", "Dismissed"),
    reasoning="Blank-topic RepairQ welcome email with a stable summary.",
    output_topic="Sales",
    rule=MatchRule(
        reporter_emails=("mail@repairq.io",),
        summary_contains=("Welcome to RepairQ",),
        description_contains=("This is a RQ Admin notification email letting you know that the process for customer",),
        description_patterns=("rq admin notification email letting you know that the process for customer",),
    ),
)