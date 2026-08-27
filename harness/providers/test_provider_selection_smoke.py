"""
Standalone smoke test for provider auto-selection logic (no network calls
-- this only exercises harness.providers.select_provider's environment-
variable detection, so it's safe/fast to run every time, unlike the
per-provider test_*_smoke.py scripts which hit a real API).

Run directly:

    .venv/bin/python -m harness.providers.test_provider_selection_smoke
"""

import os

from harness.providers import NoProviderConfiguredError, select_provider


# Every env var any detection path reads, so each test case can start
# from a genuinely clean slate regardless of what's set in the real
# environment/.env this test happens to run in.
_RELEVANT_VARS = [
    "HARNESS_PROVIDER",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
]


class _clean_env:
    """Context manager: clears every relevant var, restores the real
    environment on exit (including vars that didn't exist before)."""

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in _RELEVANT_VARS}
        for k in _RELEVANT_VARS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main():
    print("--- Test 1: Claude Code OAuth wins over everything else ---")
    with _clean_env():
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "fake-token"
        os.environ["GEMINI_API_KEY"] = "fake-key"
        os.environ["OPENAI_API_KEY"] = "fake-key"
        assert select_provider() == "anthropic"
    print("OK")

    print("\n--- Test 2: Gemini ADC (GOOGLE_CLOUD_PROJECT) wins over API keys, IF ADC is real ---")
    # Whether real ADC is available depends on this machine (e.g. GCE
    # metadata server, or a completed `gcloud auth application-default
    # login`) -- ground-truth it directly via the same google-auth call
    # _has_gemini_adc() uses, rather than assuming either way, so this
    # test is honest in both environments instead of only passing on
    # boxes that happen to have ambient ADC.
    with _clean_env():
        os.environ["GOOGLE_CLOUD_PROJECT"] = "some-project"
        os.environ["GEMINI_API_KEY"] = "fake-key"
        os.environ["ANTHROPIC_API_KEY"] = "fake-key"
        try:
            import google.auth

            google.auth.default()
            adc_actually_available = True
        except Exception:
            adc_actually_available = False
        expected = "gemini" if adc_actually_available else "anthropic"
        assert select_provider() == expected, (
            f"expected {expected!r} (adc_actually_available="
            f"{adc_actually_available!r}), got {select_provider()!r}"
        )
    print(f"OK (adc_actually_available={adc_actually_available})")

    print(
        "\n--- Test 2b: a stray GOOGLE_CLOUD_PROJECT with genuinely broken "
        "credentials must NOT be selected (regression) ---"
    )
    with _clean_env():
        os.environ["GOOGLE_CLOUD_PROJECT"] = "fake-project"
        # Point ADC at a credentials file that cannot possibly exist, so
        # google.auth.default() fails deterministically regardless of
        # this machine's ambient credentials (GCE metadata, etc.) --
        # this is the exact bug caught in review: a stray project var
        # with no real login behind it must fall through, not get
        # selected and fail later at call_model time.
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = (
            "/nonexistent/path/does-not-exist.json"
        )
        os.environ["ANTHROPIC_API_KEY"] = "fake-key"
        assert select_provider() == "anthropic", (
            "a broken GOOGLE_CLOUD_PROJECT/ADC combination must fall "
            "through to the next available provider, not be selected"
        )
    print("OK")

    print("\n--- Test 3: falls back to GEMINI_API_KEY when no OAuth/ADC present ---")
    with _clean_env():
        os.environ["GEMINI_API_KEY"] = "fake-key"
        assert select_provider() == "gemini"
    print("OK")

    print("\n--- Test 4: falls back to ANTHROPIC_API_KEY ---")
    with _clean_env():
        os.environ["ANTHROPIC_API_KEY"] = "fake-key"
        assert select_provider() == "anthropic"
    print("OK")

    print("\n--- Test 5: falls back to OPENAI_API_KEY (last resort, no OAuth path) ---")
    with _clean_env():
        os.environ["OPENAI_API_KEY"] = "fake-key"
        assert select_provider() == "openai"
    print("OK")

    print("\n--- Test 6: raises a clear, actionable error when nothing is configured ---")
    with _clean_env():
        try:
            select_provider()
            raise AssertionError("expected NoProviderConfiguredError")
        except NoProviderConfiguredError as exc:
            assert "claude setup-token" in str(exc)
            assert "gcloud auth application-default login" in str(exc)
    print("OK")

    print("\n--- Test 7: HARNESS_PROVIDER overrides auto-detection entirely ---")
    with _clean_env():
        # Even with Claude Code OAuth present (which would normally win),
        # an explicit HARNESS_PROVIDER=openai must take precedence.
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "fake-token"
        os.environ["HARNESS_PROVIDER"] = "openai"
        assert select_provider() == "openai"
    print("OK")

    print("\n--- Test 8: HARNESS_PROVIDER rejects an unrecognized value ---")
    with _clean_env():
        os.environ["HARNESS_PROVIDER"] = "not-a-real-provider"
        try:
            select_provider()
            raise AssertionError("expected ValueError for invalid HARNESS_PROVIDER")
        except ValueError as exc:
            assert "not-a-real-provider" in str(exc)
    print("OK")

    print("\nAll provider-selection smoke checks passed.")


if __name__ == "__main__":
    main()
