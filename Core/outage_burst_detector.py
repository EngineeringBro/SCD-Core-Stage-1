from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CORE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CORE_ROOT.parent
OUTAGE_BURST_STATE_PATH = Path(
    os.getenv("OUTAGE_BURST_STATE_PATH") or (REPO_ROOT / ".github" / "outage_burst_state.json")
)

OUTAGE_BURST_ENABLED = False
OUTAGE_BURST_ALERT_TITLE = "POTENTIAL OUTAGE DETECTED"
OUTAGE_BURST_THRESHOLD = 5
OUTAGE_BURST_WINDOW_MINUTES = 30
OUTAGE_BURST_TRACKED_SIGNALS = (
    "New Call",
    "Voice Message",
    "Similar Topic Burst",
)


def load_state() -> dict[str, Any]:
    if not OUTAGE_BURST_STATE_PATH.exists():
        return build_default_state()

    payload = json.loads(OUTAGE_BURST_STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return build_default_state()

    state = build_default_state()
    for key in state:
        if key in payload:
            state[key] = payload[key]
    return state


def save_state(state: dict[str, Any]) -> None:
    OUTAGE_BURST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTAGE_BURST_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def build_default_state() -> dict[str, Any]:
    return {
        "enabled": OUTAGE_BURST_ENABLED,
        "alert_title": OUTAGE_BURST_ALERT_TITLE,
        "threshold": OUTAGE_BURST_THRESHOLD,
        "window_minutes": OUTAGE_BURST_WINDOW_MINUTES,
        "tracked_signals": list(OUTAGE_BURST_TRACKED_SIGNALS),
        "active_alert_issue_number": "",
        "last_evaluated_at": "",
        "last_alerted_at": "",
        "status_message": "Outage burst detector placeholder is disabled.",
    }


def detect_outage_burst(_ticket_queue: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    state = load_state()
    state["status_message"] = (
        "Outage burst detector placeholder only. "
        "Future rule: create a GitHub issue titled 'POTENTIAL OUTAGE DETECTED' when 5 tickets "
        "matching New Call, Voice Message, or a similar-topic burst appear within 30 minutes."
    )
    return {
        "enabled": False,
        "checked": False,
        "should_alert": False,
        "alert_title": state["alert_title"],
        "threshold": state["threshold"],
        "window_minutes": state["window_minutes"],
        "tracked_signals": state["tracked_signals"],
        "status_message": state["status_message"],
    }


if __name__ == "__main__":
    print(json.dumps(detect_outage_burst(), indent=2))