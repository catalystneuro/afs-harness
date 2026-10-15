"""Tests for provenance.classify. Run with: python harness/test_provenance.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from datetime import datetime, timezone

from provenance import classify, seconds_until_reset

INIT = {"type": "system", "subtype": "init"}
ASSISTANT = {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}}
RETRY = {"type": "system", "subtype": "api_retry", "error_status": 429, "error": "rate_limit"}


def result(subtype, is_error, text):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": text, "num_turns": 5}


def check(events, expected, exit_code=0):
    res = next((e for e in reversed(events) if e.get("type") == "result"), {})
    status, _ = classify(events, INIT, res, "", exit_code)
    assert status == expected, f"expected {expected}, got {status} for {res}"


# A session cut off by a usage limit, with an error text that matches no marker.
check([INIT, ASSISTANT, RETRY, RETRY, result("error_during_execution", True, "Session stopped")], "infra_error")
# Known usage-limit wording.
check([INIT, ASSISTANT, result("success", True, "Claude AI usage limit reached")], "infra_error")
# The wording seen on 2026-10-14, which ends with an assistant message and no API retry.
check([INIT, ASSISTANT, result("success", True, "You've hit your session limit · resets 4:40pm (UTC)")], "infra_error")
# A session that hit retries mid-run but then finished normally.
check([INIT, RETRY, ASSISTANT, result("success", False, "done")], "success")
# A session the agent ended, with no failed API call at the end.
check([INIT, ASSISTANT, result("error_max_budget_usd", True, "Budget exceeded")], "agent_ended")
# Stopped by the wall-clock limit (timeout exits 124, or 137 after --kill-after): no result event.
check([INIT, ASSISTANT], "agent_ended", exit_code=124)
check([INIT, ASSISTANT], "agent_ended", exit_code=137)
# No result event for any other reason is still an infrastructure error.
check([INIT, ASSISTANT], "infra_error", exit_code=1)
# Reset times: later today, and earlier than now (so tomorrow).
noon = datetime(2026, 10, 14, 15, 58, tzinfo=timezone.utc)
assert seconds_until_reset("resets 4:40pm (UTC)", noon) == 42 * 60
assert seconds_until_reset("resets 3pm (UTC)", noon) == (23 * 60 + 2) * 60
assert seconds_until_reset("no reset here", noon) is None
print("ok")
