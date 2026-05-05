from __future__ import annotations

from modules.notifications_module.profile_types import MatchRule, NotificationProfile

PROFILE = NotificationProfile(
    case_id="quickbooks_journal_entry_error",
    display_name="QuickBooks Journal Entry Error",
    historical_total=152,
    historical_zero_comment_closes=147,
    dominant_resolutions=("Fixed / Completed", "Dismissed", "Done"),
    reasoning="RepairQ journal entry alert, usually from noreply system sender.",
    output_topic="Quickbooks",
    rule=MatchRule(
        reporter_emails=("noreply@repairq.io",),
        summary_contains=("Error: RepairQ Journal Entries to QuickBooks Online",),
        description_contains=("RepairQ encountered a problem sending journal entries from RepairQ to your QuickBooks Online account.",),
        description_patterns=(
            "the date range attempted was",
            "you can resend journal entries to your quickbooks online account via the",
            "error messages fatal errors",
        ),
    ),
)