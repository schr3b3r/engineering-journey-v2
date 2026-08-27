"""
Provider auto-selection: picks which provider adapter to use so the
harness runs by default without asking the user for an API key.

Selection order (first match wins), each check being "is there already a
non-API-key, already-authenticated session available":

1. **Claude Code OAuth** (`CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_AUTH_TOKEN`
   env var set). This is the same credential a Claude Code session (like
   the one likely driving fulcra-rapid-prototype itself, per the skill's
   default flow) already has. If present, prefer it -- the person running
   this harness has almost certainly already authenticated to Claude in
   the surrounding environment, so reusing that avoids asking them to
   separately go get a Gemini/OpenAI API key for a harness whose whole
   point is to stay out of their way.
2. **Vertex AI / Gemini ADC** (`GOOGLE_CLOUD_PROJECT` env var set AND
   `google.auth.default()` actually resolves real credentials -- both
   conditions are checked; see `_has_gemini_adc` below. Just having the
   env var set is not enough: a stray `GOOGLE_CLOUD_PROJECT` with no real
   `gcloud auth application-default login` behind it would otherwise
   cause this to select Gemini and then fail outright, instead of
   correctly falling through to an available API-key provider).
3. **`GEMINI_API_KEY`** set (existing behavior, preserved for backward
   compatibility with projects scaffolded before this module existed).
4. **`ANTHROPIC_API_KEY`** set.
5. **`OPENAI_API_KEY`** set (the only provider with no OAuth path -- see
   openai_provider.py's module docstring).

If none of the above are set, raises with a clear, actionable message
covering every option rather than defaulting silently to one provider
and producing a confusing "credentials not found" error from deep inside
some SDK.

This module is intentionally the ONLY place that knows about all three
providers and how to choose between them -- loop.py just imports
`call_model` from here and stays provider-agnostic, exactly as it was
when only gemini.py existed.

Manual override: set `HARNESS_PROVIDER` to `anthropic`, `gemini`, or
`openai` to force a specific provider, bypassing the auto-detection
order above entirely (e.g. if you're authenticated to more than one and
want the OTHER one, or you're debugging a specific adapter). This is
read before any of the detection strategies below run, so setting it
guarantees that exact provider is used, or a clear error if that
provider's own credentials aren't actually configured -- it does not
silently fall through to a different provider on its own.
"""

import os


class NoProviderConfiguredError(RuntimeError):
    """Raised when no provider's credentials could be found by any of the
    detection strategies in this module."""


_VALID_PROVIDERS = ("anthropic", "gemini", "openai")


def _has_claude_oauth() -> bool:
    return bool(
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )


def _has_gemini_adc() -> bool:
    """True only if GOOGLE_CLOUD_PROJECT is set AND
    `google.auth.default()` actually resolves real, usable credentials --
    not just "the env var happens to be set". This does a real (local,
    no-network) credential resolution check via the same `google-auth`
    machinery `genai.Client(vertexai=True, ...)` uses internally, so a
    project var left over from an unrelated GCP setup with no completed
    `gcloud auth application-default login` correctly falls through to
    the next provider instead of being selected and then failing at
    call_model time.
    """
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return False
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError
    except ImportError:
        # google-auth not installed at all -- can't be a usable ADC path.
        return False
    try:
        google.auth.default()
        return True
    except DefaultCredentialsError:
        return False


def select_provider() -> str:
    """Return one of "anthropic", "gemini", "openai" -- whichever this
    environment is already authenticated for, preferring OAuth/ADC over
    a raw API key, and preferring "reuse the session already running
    this harness" (Claude Code OAuth) over anything else. Does not
    perform any network calls; only inspects environment variables that
    the corresponding CLI/`gcloud` login flow would have already set or
    that the user was asked to put in `.env`.

    `HARNESS_PROVIDER`, if set, short-circuits all of the above and is
    returned directly (after validating it names a real provider) -- see
    this module's docstring for the manual-override rationale.
    """
    override = os.environ.get("HARNESS_PROVIDER")
    if override:
        if override not in _VALID_PROVIDERS:
            raise ValueError(
                f"HARNESS_PROVIDER={override!r} is not a recognized "
                f"provider. Expected one of: {', '.join(_VALID_PROVIDERS)}."
            )
        return override

    if _has_claude_oauth():
        return "anthropic"
    if _has_gemini_adc():
        return "gemini"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"

    raise NoProviderConfiguredError(
        "No provider credentials found. Pick ONE of the following, in "
        "order of preference (no API key needed for the first two):\n"
        "  1. Anthropic/Claude OAuth: run `claude setup-token` once "
        "(requires a Claude subscription), then export "
        "CLAUDE_CODE_OAUTH_TOKEN with the token it prints.\n"
        "  2. Gemini/Vertex AI ADC: run "
        "`gcloud auth application-default login` once, then set "
        "GOOGLE_CLOUD_PROJECT in .env.\n"
        "  3. GEMINI_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY in "
        ".env, as a fallback if neither OAuth path is available.\n"
        "You can also force a specific provider by setting "
        "HARNESS_PROVIDER to 'anthropic', 'gemini', or 'openai' -- but "
        "that provider's own credentials still need to be configured. "
        "See harness/providers/<provider>.py module docstrings for "
        "details on each."
    )


def call_model(*args, **kwargs):
    """Dispatch to whichever provider `select_provider()` picks. Accepts
    the exact same arguments as gemini.call_model / anthropic_provider.
    call_model / openai_provider.call_model (they share an identical
    signature by design, `max_tokens` on the Anthropic adapter aside,
    which has a default and is rarely passed explicitly).

    An explicit `provider=` kwarg (not one of the underlying adapters'
    real parameters) can be passed to skip auto-selection programmatically
    -- e.g. for tests. This takes precedence even over HARNESS_PROVIDER;
    HARNESS_PROVIDER is the operator-facing override (set once in .env,
    applies to every call), `provider=` is the caller-facing one (this
    specific call only).
    """
    provider = kwargs.pop("provider", None) or select_provider()

    if provider == "anthropic":
        from harness.providers.anthropic_provider import call_model as _call
    elif provider == "gemini":
        from harness.providers.gemini import call_model as _call
    elif provider == "openai":
        from harness.providers.openai_provider import call_model as _call
    else:
        raise ValueError(
            f"Unknown provider {provider!r}. Expected one of: "
            f"{', '.join(_VALID_PROVIDERS)}."
        )

    return _call(*args, **kwargs)
