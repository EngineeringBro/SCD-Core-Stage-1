from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="gsx_permission_violation",
    display_name="GSX Permission Violation",
    historical_total=117,
    historical_zero_comment_closes=117,
    dominant_resolutions=("Done", "Fixed / Completed", "Dismissed"),
    reasoning="Blank-topic GSX permission alert with a stable summary.",
    output_topic="No change",
    rule=MatchRule(
        reporter_emails=("mail@repairq.io",),
        summary_contains=("GSX Permission Violation Alert",),
        description_contains=("GSX cross-location permission violation detected:",),
        description_patterns=("gsx operations must be performed from the same location as the ticket",),
    ),
)