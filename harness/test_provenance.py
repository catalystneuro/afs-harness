"""Tests for provenance.classify. Run with: python harness/test_provenance.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from provenance import classify

INIT = {"type": "system", "subtype": "init"}
ASSISTANT = {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}}
RETRY = {"type": "system", "subtype": "api_retry", "error_status": 429, "error": "rate_limit"}


def result(subtype, is_error, text):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": text, "num_turns": 5}


def check(events, expected):
    res = next((e for e in reversed(events) if e.get("type") == "result"), {})
    status, _ = classify(events, INIT, res, "")
    assert status == expected, f"expected {expected}, got {status} for {res}"


# A session cut off by a usage limit, with an error text that matches no marker.
check([INIT, ASSISTANT, RETRY, RETRY, result("error_during_execution", True, "Session stopped")], "infra_error")
# Known usage-limit wording.
check([INIT, ASSISTANT, result("success", True, "Claude AI usage limit reached")], "infra_error")
# A session that hit retries mid-run but then finished normally.
check([INIT, RETRY, ASSISTANT, result("success", False, "done")], "success")
# A session the agent ended, with no failed API call at the end.
check([INIT, ASSISTANT, result("error_max_budget_usd", True, "Budget exceeded")], "agent_ended")
print("ok")
