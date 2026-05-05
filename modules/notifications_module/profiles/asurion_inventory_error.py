from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="asurion_inventory_error",
    display_name="Asurion Inventory Error",
    historical_total=11459,
    historical_zero_comment_closes=11459,
    dominant_resolutions=("Done", "Fixed / Completed", "Dismissed"),
    reasoning="Blank-topic system ticket with a stable Asurion inventory error summary.",
    output_topic="Asurion",
    rule=MatchRule(
        reporter_emails=("noreply@repairq.io",),
        summary_contains=("Asurion: Error updating inventory quantities",),
        description_contains=("Error updating inventory quantities: Asurion API FAIL",),
        description_patterns=("authentication failed",),
    ),
)