from gflow_worker.gflow import GflowResult, classify


def test_classify_usage_limit_production_indonesian():
    msg = (
        "transient: agent-only composer: the agent asked for a second credit confirmation in one run "
        "… Gagal Anda telah mencapai batas penggunaan. Coba lagi nanti. "
        "Apakah Anda ingin mulai membuat 1 video ini menggunakan 15 kredit?"
    )
    res = GflowResult(
        ok=False,
        payload={"error": {"detail": msg}},
        exit_code=25,
        error_class="FlowAgentUiError",
        message=msg,
    )
    assert classify(res) == "usage_limit"


def test_classify_usage_limit_english():
    msg = "You've reached your usage limit. Try again later."
    res = GflowResult(
        ok=False,
        payload={"error_message": msg},
        exit_code=25,
        message=msg,
    )
    assert classify(res) == "usage_limit"


def test_classify_agent_only():
    msg = "agent-only composer: no finished video within 600s of submit (approvals=0)"
    res = GflowResult(
        ok=False,
        payload={},
        exit_code=25,
        message=msg,
    )
    assert classify(res) == "agent_only"


def test_classify_unusual_activity_precedence():
    res = GflowResult(
        ok=False,
        payload={"error_message": "PUBLIC_ERROR_UNUSUAL_ACTIVITY"},
        exit_code=28,
        message="PUBLIC_ERROR_UNUSUAL_ACTIVITY",
    )
    assert classify(res) == "unusual_activity"


def test_classify_policy_unsafe_content():
    res = GflowResult(
        ok=False,
        payload={"error": {"class": "UnsafeContentError"}},
        exit_code=1,
        message="PUBLIC_ERROR_UNSAFE_CONTENT: generation blocked",
    )
    assert classify(res) == "policy"


def test_classify_transient_timeout():
    res = GflowResult(
        ok=False,
        payload={},
        exit_code=9,
        error_class="TimeoutExpired",
        message="gflow timed out",
    )
    assert classify(res) == "transient"
