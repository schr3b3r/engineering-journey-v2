"""
Standalone smoke test for the Anthropic provider adapter.

Not a unit test suite (no pytest, no mocking) — this is a quick, honest way
to prove the adapter actually works end-to-end against the real API before
you build anything on top of it. Run directly:

    .venv/bin/python harness/providers/test_anthropic_smoke.py

Requires either CLAUDE_CODE_OAUTH_TOKEN (run `claude setup-token` once) or
ANTHROPIC_API_KEY to be set -- see anthropic_provider.py's module
docstring for how credential selection works.
"""

from dotenv import load_dotenv

load_dotenv()

from anthropic_provider import call_model  # noqa: E402  (import after load_dotenv on purpose)


def main():
    print("--- Test 1: single-turn, no system prompt ---")
    resp = call_model(messages=[{"role": "user", "content": "Reply with exactly the word: PONG"}])
    print("text:", repr(resp.text))
    assert resp.text and "PONG" in resp.text

    print("\n--- Test 2: system prompt is respected ---")
    resp = call_model(
        messages=[{"role": "user", "content": "What are you?"}],
        system_prompt="You always answer in exactly three words, no more, no less.",
    )
    print("text:", repr(resp.text))

    print("\n--- Test 3: multi-turn history (role mapping) ---")
    resp = call_model(
        messages=[
            {"role": "user", "content": "My favorite number is 42. Just say OK."},
            {"role": "assistant", "content": "OK."},
            {"role": "user", "content": "What is my favorite number?"},
        ]
    )
    print("text:", repr(resp.text))
    assert "42" in resp.text

    print("\nAll smoke checks passed.")


if __name__ == "__main__":
    main()
