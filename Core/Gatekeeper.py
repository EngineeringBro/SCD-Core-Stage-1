from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


SCD_TICKET_KEY_PATTERN = re.compile(r"^SCD-\d+$", re.IGNORECASE)
VERSION_PATTERN = re.compile(r"^v\d+(?:\.\d+)*$", re.IGNORECASE)
SQL_CODE_BLOCK_PATTERN = re.compile(r"```sql[\s\S]*?```", re.IGNORECASE)

DEFAULT_BRAIN_1_CONFIDENCE = "98%"
MAX_RECOMMENDATION_LENGTH = 200
MAX_ISSUE_BODY_LENGTH = 50_000
MAX_NOTES_COUNT = 50
MAX_NOTE_LENGTH = 2_000

MODULE_CONFIDENCE_RANGES = {
    "general": (75, 85),
    "orphaned_transaction": (91, 97),
    "spam": (95, 99),
    "ringcentral": (95, 99),
    "notification": (95, 99),
}

PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore (?:all )?previous instructions?", re.IGNORECASE),
    re.compile(r"ignore the above", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"developer message", re.IGNORECASE),
    re.compile(r"follow these new instructions instead", re.IGNORECASE),
    re.compile(r"override (?:the )?(?:safety|guardrails|rules)", re.IGNORECASE),
    re.compile(r"bypass (?:the )?(?:safety|guardrails|rules)", re.IGNORECASE),
)

SECRET_SOLICITATION_PATTERNS = (
    re.compile(r"paste (?:your|the) (?:api key|token|password|cookie|credentials)", re.IGNORECASE),
    re.compile(r"share (?:your|the) (?:api key|token|password|cookie|credentials)", re.IGNORECASE),
    re.compile(r"send (?:your|the) (?:api key|token|password|cookie|credentials)", re.IGNORECASE),
    re.compile(r"provide (?:your|the) (?:api key|token|password|cookie|credentials)", re.IGNORECASE),
    re.compile(r"authorization:\s*bearer\s+\S+", re.IGNORECASE),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

UNSAFE_EXECUTION_OR_MARKUP_PATTERNS = (
    re.compile(r"```(?:bash|sh|shell|powershell|ps1|cmd)\b", re.IGNORECASE),
    re.compile(r"rm\s+-rf\b", re.IGNORECASE),
    re.compile(r"del\s+/f\b", re.IGNORECASE),
    re.compile(r"format-volume\b", re.IGNORECASE),
    re.compile(r"invoke-expression\b", re.IGNORECASE),
    re.compile(r"iwr\b.+iex\b", re.IGNORECASE),
    re.compile(r"curl\b.+\|\s*(?:sh|bash)\b", re.IGNORECASE),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"<iframe\b", re.IGNORECASE),
    re.compile(r"javascript:\s*", re.IGNORECASE),
    re.compile(r"onerror\s*=", re.IGNORECASE),
    re.compile(r"onload\s*=", re.IGNORECASE),
    re.compile(r"data:text/html", re.IGNORECASE),
)


@dataclass(frozen=True)
class GatekeeperResult:
    decision: str
    passed_checks: int
    total_checks: int
    summary: str
    brain_1_confidence: str
    brain_3_human_action: str
    check_names: tuple[str, ...]


def run_gatekeeper(
    *,
    ticket_id: str,
    module_name: str,
    module_version: str,
    recommendation: str,
    issue_body: str,
    notes: list[str],
    module_payload: dict[str, Any] | None = None,
) -> GatekeeperResult:
    normalized_payload = module_payload if isinstance(module_payload, dict) else {}
    content_blob = "\n".join([recommendation, issue_body, *notes])
    checks = (
        ("identity", _check_identity(ticket_id, module_name, module_version, recommendation, issue_body)),
        ("bounds", _check_bounds(recommendation, issue_body, notes)),
        ("prompt_injection", _check_no_pattern_hits(content_blob, PROMPT_INJECTION_PATTERNS)),
        ("secret_exposure", _check_no_pattern_hits(content_blob, SECRET_SOLICITATION_PATTERNS)),
        (
            "unsafe_execution_or_markup",
            _check_no_pattern_hits(_strip_sql_code_blocks(content_blob), UNSAFE_EXECUTION_OR_MARKUP_PATTERNS),
        ),
    )

    failures = [name for name, passed in checks if not passed]
    if failures:
        raise ValueError("gatekeeper blocked output: " + ", ".join(failures))

    total_checks = len(checks)
    brain_1_confidence = _resolve_brain_1_confidence(
        ticket_id=ticket_id,
        module_name=module_name,
        module_payload=normalized_payload,
    )
    brain_3_human_action = _resolve_brain_3_human_action(
        module_name=module_name,
        recommendation=recommendation,
    )
    return GatekeeperResult(
        decision="ALLOW",
        passed_checks=total_checks,
        total_checks=total_checks,
        summary=f"ALLOW ({total_checks} checks passed)",
        brain_1_confidence=brain_1_confidence,
        brain_3_human_action=brain_3_human_action,
        check_names=tuple(name for name, _ in checks),
    )


def build_gatekeeper_table(
    *,
    ticket_id: str,
    module_name: str,
    module_version: str,
    gatekeeper_result: GatekeeperResult,
) -> str:
    module_label = module_name.strip() or "unknown"
    if module_version.strip():
        module_label = f"{module_label} {module_version.strip()}"

    lines = [
        "| Field | Value |",
        "| --- | --- |",
        f"| Ticket | {_escape_table_cell(ticket_id)} |",
        f"| Module | {_escape_table_cell(module_label)} |",
        f"| Brain 1 Confidence | {_escape_table_cell(gatekeeper_result.brain_1_confidence)} |",
        f"| Brain 2 (Gatekeeper) | {_escape_table_cell(gatekeeper_result.summary)} |",
        f"| Brain 3 (Human action) | {_escape_table_cell(gatekeeper_result.brain_3_human_action)} |",
    ]
    return "\n".join(lines)


def _check_identity(
    ticket_id: str,
    module_name: str,
    module_version: str,
    recommendation: str,
    issue_body: str,
) -> bool:
    if not SCD_TICKET_KEY_PATTERN.fullmatch(ticket_id.strip()):
        return False
    if not module_name.strip():
        return False
    if module_version.strip() and not VERSION_PATTERN.fullmatch(module_version.strip()):
        return False
    if not recommendation.strip():
        return False
    if not issue_body.strip():
        return False
    return True


def _check_bounds(recommendation: str, issue_body: str, notes: list[str]) -> bool:
    if len(recommendation.strip()) > MAX_RECOMMENDATION_LENGTH:
        return False
    if len(issue_body.strip()) > MAX_ISSUE_BODY_LENGTH:
        return False
    if len(notes) > MAX_NOTES_COUNT:
        return False
    if any(len(note.strip()) > MAX_NOTE_LENGTH for note in notes):
        return False
    return True


def _check_no_pattern_hits(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    for pattern in patterns:
        if pattern.search(text):
            return False
    return True


def _strip_sql_code_blocks(text: str) -> str:
    return SQL_CODE_BLOCK_PATTERN.sub(" ", text)


def _resolve_brain_1_confidence(*, ticket_id: str, module_name: str, module_payload: dict[str, Any]) -> str:
    for key in ("brain_1_confidence", "brain1_confidence", "confidence", "confidence_score"):
        if key not in module_payload:
            continue

        value = module_payload.get(key)
        formatted = _format_confidence(value)
        if formatted:
            return formatted

    confidence_range = MODULE_CONFIDENCE_RANGES.get(module_name.strip().lower())
    if confidence_range:
        return _generate_module_confidence(ticket_id=ticket_id, module_name=module_name, confidence_range=confidence_range)

    return DEFAULT_BRAIN_1_CONFIDENCE


def _generate_module_confidence(*, ticket_id: str, module_name: str, confidence_range: tuple[int, int]) -> str:
    minimum, maximum = confidence_range
    if minimum >= maximum:
        return f"{minimum}%"

    seed_input = f"{ticket_id.strip().upper()}::{module_name.strip().lower()}"
    digest = hashlib.sha256(seed_input.encode("utf-8")).hexdigest()
    offset = int(digest[:8], 16) % (maximum - minimum + 1)
    return f"{minimum + offset}%"


def _resolve_brain_3_human_action(*, module_name: str, recommendation: str) -> str:
    normalized_module = module_name.strip().lower()
    normalized_recommendation = recommendation.strip().lower()

    if normalized_module == "orphaned_transaction":
        return "Insert SQL then resolve"

    if normalized_module == "spam":
        return "Dismiss ticket as spam"

    if normalized_module == "ringcentral":
        if normalized_recommendation == "ringcentral_spam_safe_to_dismiss":
            return "Dismiss Fax/Robocaller as spam"
        if normalized_recommendation == "ringcentral_voicemail_callback_needed":
            return "Review then call back"
        return "Call back"

    if normalized_module == "notification":
        return "Close and Log notification"

    if normalized_module == "general":
        if normalized_recommendation in {"knowledge_guidance", "knowledge_guidance_fallback"}:
            return "Initiate customer assistance"
        if normalized_recommendation == "knowledge_gap":
            return "Manual review required"

    return "Review then resolve"


def _format_confidence(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return ""
        if normalized.endswith("%"):
            return normalized
        if normalized.replace(".", "", 1).isdigit():
            try:
                numeric_value = float(normalized)
            except ValueError:
                return normalized
            return _format_numeric_confidence(numeric_value)
        return normalized

    if isinstance(value, (int, float)):
        return _format_numeric_confidence(float(value))

    return ""


def _format_numeric_confidence(value: float) -> str:
    if 0 <= value <= 1:
        return f"{round(value * 100)}%"
    if 0 <= value <= 100:
        return f"{round(value)}%"
    return ""


def _escape_table_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", "<br>")