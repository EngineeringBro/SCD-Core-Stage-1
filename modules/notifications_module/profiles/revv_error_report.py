from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="revv_error_report",
    display_name="Revv Error Report",
    historical_total=2468,
    historical_zero_comment_closes=2442,
    dominant_resolutions=("Done", "Fixed / Completed", "Dismissed"),
    reasoning="Explicit Revv Error Report topic or summary pattern.",
    output_topic="Revv Error Report",
    rule=MatchRule(
        reporter_emails=("mail@repairq.io",),
        summary_contains=("Revv Error Report",),
        description_contains=("Revv Error Report for instance:",),
        description_patterns=(
            "a closed repairq ticket should be sending information to revv",
            "a ticket should fail to send because of",
        ),
    ),
)