from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="assurant_inventory_error",
    display_name="Assurant Inventory Error",
    historical_total=8692,
    historical_zero_comment_closes=8473,
    dominant_resolutions=("Done", "Dismissed", "Fixed / Completed"),
    reasoning="Blank-topic system ticket with a stable Assurant inventory error summary.",
    output_topic="Assurant",
    rule=MatchRule(
        reporter_emails=("noreply@repairq.io",),
        summary_contains=("Assurant: Error updating inventory quantities",),
        description_contains=("Error updating inventory quantities:",),
        description_patterns=("error updating inventory quantities",),
    ),
)