from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="azure_notification",
    display_name="Azure Notification",
    historical_total=1340,
    historical_zero_comment_closes=1333,
    dominant_resolutions=("Done", "Dismissed", "Fixed / Completed"),
    reasoning="Azure alert with explicit Azure Notification topic or Azure sender.",
    output_topic="Azure Notification",
    rule=MatchRule(
        reporter_emails=("azure-noreply@microsoft.com",),
        summary_contains=("Azure:",),
        description_patterns=(
            "azure monitor alert rule",
            "alert rule description active service bus messages are not processing for batteries plus",
            "as a member of the rq support action group",
        ),
    ),
)