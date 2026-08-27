#!/usr/bin/env python3
"""Real cross-repo period summarization + narrative generation driver.

This is harness-side tooling, deliberately NOT part of app/ -- it is the
"agent performing the summarization task" that
app/features/m7_rollup_summarization.md's design assumes exists, made
concrete and runnable end-to-end instead of a manual step nobody ever
completed (see GitHub issue #2 against schr3b3r/engineering-journey-v2,
and app/summarization.py's module docstring for the full diagnosis).

Why this lives here and not in app/cli.py:
- app/'s own requirements.txt and ENGINEERING_STANDARDS.md deliberately
  forbid LLM provider SDKs in application code (app/features/
  m7_rollup_summarization.md: "No Gemini/OpenAI provider API key or SDK
  dependency permitted in app code"). Importing anthropic/openai/
  google-genai from app/cli.py would violate that.
- harness/providers/ already has real, hardened multi-provider adapters
  (Anthropic OAuth-preferred, Gemini, OpenAI -- see
  harness/providers/__init__.py's module docstring) built for exactly
  this kind of "call a real model" need.
- This script bridges the two: it imports app/'s deterministic data
  layer (RollupEngine, RollupSummarizer, etc.) for the data, and
  harness/providers for the actual model call, keeping each side's
  dependency boundary intact. app/ itself never imports an LLM SDK.

Usage (from the repo root, with both app/ and this repo's own
pyproject.toml's deps installed -- see README.md):

    python scripts/summarize_periods.py --identity <username> \\
        [--years 1.0] [--since ISO] [--until ISO] [--provider anthropic|gemini|openai]

Then generate the narrative referencing the real summaries:

    cd app && python cli.py narrative --range full --identity <username>
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"

# app/'s modules use flat, sibling-style imports (from rollups import ...,
# not from app.rollups import ...) -- see SKILL.md's note on this. Put
# app/ on sys.path so this script can import them the same way app/cli.py
# does, without needing app/ to become a real installed package.
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from fulcra_client import FulcraAuthError, get_fulcra_client  # noqa: E402
from github_auth import get_github_auth_token, get_token_identity  # noqa: E402
from raw_ingestion import RawActivityIngestor  # noqa: E402
from rollups import RollupEngine  # noqa: E402
from summarization import RollupSummarizer  # noqa: E402

from harness.providers import call_model  # noqa: E402


def make_summary_provider_fn(provider: str | None):
    """Return a Callable[[str], str] that sends a period-summarization
    prompt to a real model via harness.providers.call_model and returns
    its text response -- the "real prose, not a template" callback
    summarize_periods_and_write_back requires.
    """

    def _provider_fn(prompt: str) -> str:
        response = call_model(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a technical writer producing a developer's "
                "engineering activity retrospective. Write real, "
                "connected prose -- never a template, never a bare "
                "list of numbers. Be concise: 1-3 sentences."
            ),
            provider=provider,
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError(
                "Model returned empty text for a period summarization "
                "prompt -- refusing to write back an empty summary_text "
                "(this would look identical to a bug, not a quiet "
                "period, in the generated narrative)."
            )
        return text

    return _provider_fn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--identity", type=str, help="GitHub username/identity.")
    parser.add_argument("--years", type=float, default=1.0, help="Years of rollups to summarize (default: 1.0).")
    parser.add_argument("--since", type=str, help="Start ISO timestamp.")
    parser.add_argument("--until", type=str, help="End ISO timestamp.")
    parser.add_argument(
        "--provider", type=str, choices=["anthropic", "gemini", "openai"],
        help="Force a specific provider instead of auto-detecting (see harness/providers/__init__.py).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print how many period groups/prompts would be generated without calling a model or writing back.",
    )
    args = parser.parse_args(argv)

    try:
        f_client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr)
        return 1

    token = get_github_auth_token(auto_accept_existing=True)
    identity = args.identity or get_token_identity(token)
    if not identity:
        print("Error: could not determine GitHub identity. Pass --identity explicitly.", file=sys.stderr)
        return 1

    end_dt = datetime.now(timezone.utc)
    start_dt = datetime.fromtimestamp(end_dt.timestamp() - (args.years * 365.25 * 86400), tz=timezone.utc)
    since_iso = args.since or start_dt.isoformat().replace("+00:00", "Z")
    until_iso = args.until or end_dt.isoformat().replace("+00:00", "Z")

    print(f"--- Fetching rollups for {identity} ({since_iso[:10]} to {until_iso[:10]}) ---")
    rollup_engine = RollupEngine(client=f_client)
    rollups = rollup_engine.get_rollups(
        github_identity=identity, start_time=since_iso, end_time=until_iso,
    )
    if not rollups:
        print(
            "No rollups found for this identity/range. Run "
            "`python cli.py rollup ...` first (see app/SKILL.md)."
        )
        return 1
    print(f"Found {len(rollups)} rollup records.")

    summarizer = RollupSummarizer(client=f_client)

    if args.dry_run:
        handoff = summarizer.prepare_period_handoff(rollups)
        print(f"\n[Dry Run] Would generate {len(handoff)} cross-repo period summaries:")
        for h in handoff:
            print(
                f"  - {h['period_type']} {h['start_time'][:10]} to {h['end_time'][:10]}: "
                f"{len(h['repos'])} repo(s), {h['total_activity_count']} activities"
            )
        return 0

    print(f"\n--- Generating real period summaries via provider={args.provider or 'auto'} ---")
    summary_provider_fn = make_summary_provider_fn(args.provider)
    updated = summarizer.summarize_periods_and_write_back(
        rollups, summary_provider_fn=summary_provider_fn, save_to_fulcra=True,
    )
    print(f"Wrote back real summaries for {len(updated)} rollup records.")
    print(
        "\nNext: cd app && python cli.py narrative --range full "
        f"--identity {identity}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
