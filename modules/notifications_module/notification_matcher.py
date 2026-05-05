from __future__ import annotations

import re

from modules.notifications_module.buckets import get_candidate_profiles
from modules.notifications_module.profile_types import ClassificationResult, NotificationProfile, ProfileMatch, TicketContext, build_ticket_context


NOTIFICATION_OUTPUT_RESOLUTION = "Done"
NOTIFICATION_OUTPUT_ROOT_CAUSE = "Unknown"


def classify_ticket(ticket_id: str, ticket_details: dict) -> ClassificationResult:
    context = build_ticket_context(ticket_id, ticket_details)
    matches = rank_profiles(context)
    best_match = matches[0] if matches else None
    if best_match is None:
        return ClassificationResult(
            ticket_id=ticket_id,
            recommendation="notification profile match: none",
            matched_case_id=None,
            matched_case_name=None,
            output_topic=None,
            evidence=("No notification profile matched all hard rules.",),
            notes=["Notification scope: notifications_module profiles only", "No hard-rule profile matched"],
            context=context,
        )

    output_topic = best_match.profile.output_topic
    return ClassificationResult(
        ticket_id=ticket_id,
        recommendation=f"notification profile match: {best_match.profile.case_id}",
        matched_case_id=best_match.profile.case_id,
        matched_case_name=best_match.profile.display_name,
        output_topic=output_topic,
        evidence=best_match.evidence,
        notes=[
            "Notification scope: notifications_module profiles only",
            f"Matched profile: {best_match.profile.case_id}",
            f"Output topic: {output_topic or 'None'}",
            f"Output resolution: {NOTIFICATION_OUTPUT_RESOLUTION}",
            f"Output root cause: {NOTIFICATION_OUTPUT_ROOT_CAUSE}",
            f"Historical closed without comment: {best_match.profile.historical_zero_comment_closes}/{best_match.profile.historical_total}",
        ],
        context=context,
    )


def rank_profiles(context: TicketContext) -> list[ProfileMatch]:
    matches: list[ProfileMatch] = []
    for profile in get_candidate_profiles(context.reporter_email):
        match = score_profile(context, profile)
        if match is not None:
            matches.append(match)
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches


def score_profile(context: TicketContext, profile: NotificationProfile) -> ProfileMatch | None:
    evidence: list[str] = []
    rule = profile.rule
    summary = context.summary
    summary_lower = summary.lower()
    summary_pattern_text = normalize_match_text(summary)
    description = context.description
    description_lower = description.lower()
    description_pattern_text = normalize_match_text(description)
    reporter = context.reporter_email.lower()

    if rule.reporter_emails:
        if not reporter or reporter not in lower_values(rule.reporter_emails):
            return None
        evidence.append(f"reporter match: {context.reporter_email}")

    if rule.summary_contains:
        contains_hit = find_contains_hit(summary, summary_pattern_text, rule.summary_contains)
        if contains_hit is None:
            return None
        evidence.append(f"summary contains match: {contains_hit}")

    if rule.summary_patterns:
        pattern_hit = find_pattern_hit(summary_lower, summary_pattern_text, rule.summary_patterns)
        if pattern_hit is None:
            return None
        evidence.append(f"summary pattern match: {pattern_hit}")

    if rule.description_contains:
        contains_hit = find_contains_hit(description, description_pattern_text, rule.description_contains)
        if contains_hit is None:
            return None
        evidence.append(f"description contains match: {contains_hit}")

    if rule.description_patterns:
        pattern_hit = find_pattern_hit(description_lower, description_pattern_text, rule.description_patterns)
        if pattern_hit is None:
            return None
        evidence.append(f"description pattern match: {pattern_hit}")

    if not evidence:
        return None

    return ProfileMatch(profile=profile, score=len(evidence), evidence=tuple(evidence))


def lower_values(values: tuple[str, ...]) -> set[str]:
    return {value.lower() for value in values}


def find_contains_hit(raw_text: str, normalized_text: str, fragments: tuple[str, ...]) -> str | None:
    raw_lower = raw_text.lower()
    for fragment in fragments:
        fragment_lower = fragment.lower()
        if fragment_lower in raw_lower:
            return fragment

        normalized_fragment = normalize_match_text(fragment)
        if normalized_fragment and normalized_fragment in normalized_text:
            return fragment
    return None


def normalize_match_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return " ".join(normalized.split()).strip()


def find_pattern_hit(raw_text_lower: str, normalized_text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        pattern_lower = pattern.lower()
        if re.search(pattern_lower, raw_text_lower):
            return pattern
        normalized_pattern = normalize_match_text(pattern)
        if normalized_pattern and normalized_pattern in normalized_text:
            return pattern
    return None

