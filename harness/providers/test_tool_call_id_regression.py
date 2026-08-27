"""
Regression test for the tool-call-id propagation bug caught in code
review on PR #51 (harness.loop dropping the provider's tool_call id
before appending the tool-result message, and the Anthropic adapter
emitting one user message PER tool result instead of merging all results
from one assistant turn into a single following user message, as
Anthropic's API requires).

Exercises the REAL translation functions in each adapter (not mocks)
against a message list shaped exactly like what harness.loop.run
produces for a multi-tool-call turn: one assistant message with two
tool_calls, followed by two normalized {"role": "tool", ...} messages
carrying the id loop.py now threads through.

Run directly (needs anthropic + openai installed, same as the harness's
own dependencies -- no network calls, this only exercises the pure
translation functions):

    .venv/bin/python -m harness.providers.test_tool_call_id_regression
"""

from harness.providers.anthropic_provider import _to_anthropic_messages
from harness.providers.openai_provider import _to_openai_messages


def _fake_multi_tool_call_turn(id_key: str, id_a: str, id_b: str) -> list[dict]:
    """The exact shape harness.loop.run produces for a turn where the
    model calls the same tool twice in one turn -- the case that most
    directly exposes the bug (falling back to the tool NAME as an id
    breaks immediately once the same name appears more than once)."""
    return [
        {"role": "user", "content": "read both files"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"name": "read_file", "args": {"path": "a.txt"}, id_key: id_a},
                {"name": "read_file", "args": {"path": "b.txt"}, id_key: id_b},
            ],
        },
        {"role": "tool", "name": "read_file", "content": "contents of a", "tool_call_id": id_a},
        {"role": "tool", "name": "read_file", "content": "contents of b", "tool_call_id": id_b},
    ]


def test_anthropic_merges_multiple_tool_results_into_one_user_message():
    messages = _fake_multi_tool_call_turn("id", "toolu_1", "toolu_2")
    out = _to_anthropic_messages(messages)

    assert len(out) == 3, (
        f"expected exactly 3 Anthropic messages (user, assistant, ONE "
        f"merged user with both tool_results), got {len(out)}: {out}"
    )
    assert out[2]["role"] == "user"
    assert len(out[2]["content"]) == 2, (
        "both tool_result blocks for one assistant turn must live in a "
        "SINGLE user message -- Anthropic's API rejects splitting them "
        "into separate user messages"
    )
    assert out[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert out[2]["content"][1]["tool_use_id"] == "toolu_2"
    assert out[1]["content"][0]["id"] == "toolu_1"
    assert out[1]["content"][1]["id"] == "toolu_2"


def test_anthropic_raises_on_missing_tool_call_id():
    """A tool result with no id must raise loudly, not silently fall
    back to the tool name (which is not a valid tool_use_id and would
    produce a confusing 400 from the real API instead)."""
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"name": "read_file", "args": {}, "id": "toolu_1"}]},
        {"role": "tool", "name": "read_file", "content": "x"},  # no tool_call_id
    ]
    try:
        _to_anthropic_messages(messages)
        raise AssertionError("expected ValueError for missing tool_call_id")
    except ValueError as exc:
        assert "tool_call_id" in str(exc)


def test_openai_round_trips_distinct_ids_for_multiple_calls():
    messages = _fake_multi_tool_call_turn("id", "call_1", "call_2")
    out = _to_openai_messages(messages)

    assert out[1]["tool_calls"][0]["id"] == "call_1"
    assert out[1]["tool_calls"][1]["id"] == "call_2"
    assert out[2]["tool_call_id"] == "call_1"
    assert out[3]["tool_call_id"] == "call_2"


def test_openai_raises_on_missing_tool_call_id():
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"name": "read_file", "args": {}, "id": "call_1"}]},
        {"role": "tool", "name": "read_file", "content": "x"},  # no tool_call_id
    ]
    try:
        _to_openai_messages(messages)
        raise AssertionError("expected ValueError for missing tool_call_id")
    except ValueError as exc:
        assert "tool_call_id" in str(exc)


def main():
    tests = [
        test_anthropic_merges_multiple_tool_results_into_one_user_message,
        test_anthropic_raises_on_missing_tool_call_id,
        test_openai_round_trips_distinct_ids_for_multiple_calls,
        test_openai_raises_on_missing_tool_call_id,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print("\nAll tool-call-id regression checks passed.")


if __name__ == "__main__":
    main()
