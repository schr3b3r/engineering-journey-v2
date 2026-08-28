"""Command-Line Interface (CLI) for Engineering Journey v2.

Directly runnable entry point supporting backfill, activity rollups, notability signals,
rollup summarization, and markdown narrative generation with no agent dependency required.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from typing import List, Optional

from backfill import BackfillEngine
from fulcra_client import FulcraAuthError, get_fulcra_client
from github_auth import get_github_auth_token, get_token_identity
from github_spike import GitHubAPISpike
from narrative import NarrativeGenerator, NarrativeUploadError
from notability import NotabilityEngine
from raw_ingestion import RawActivityIngestor
from rollups import RollupEngine, attach_raw_evidence
from summarization import RollupSummarizer


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="engineering-journey",
        description="Engineering Journey v2 — GitHub activity ingestion and narrative generator for Fulcra.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 1. AUTH command
    auth_parser = subparsers.add_parser("auth", help="Check or perform GitHub and Fulcra authentication.")
    auth_parser.add_argument("--device-code", action="store_true", help="Force GitHub browser-based device code flow.")
    auth_parser.add_argument("--yes", "-y", action="store_true", help="Auto-accept detected existing GitHub session.")

    # 2. BACKFILL command
    backfill_parser = subparsers.add_parser("backfill", help="Ingest raw GitHub activity into Fulcra.")
    backfill_parser.add_argument("--years", type=float, default=1.0, help="Years of history to backfill (default: 1.0).")
    backfill_parser.add_argument("--since", type=str, help="Start ISO timestamp (e.g. 2024-01-01T00:00:00Z).")
    backfill_parser.add_argument("--until", type=str, help="End ISO timestamp (e.g. 2025-01-01T00:00:00Z).")
    backfill_parser.add_argument("--identity", type=str, help="GitHub username/identity.")
    backfill_parser.add_argument("--repo", type=str, help="Optional specific repo to backfill (owner/repo).")
    backfill_parser.add_argument("--yes", "-y", action="store_true", help="Auto-accept detected GitHub auth.")
    backfill_parser.add_argument("--device-code", action="store_true", help="Force GitHub device-code auth flow.")
    backfill_parser.add_argument("--dry-run", action="store_true", help="Perform discovery/pre-checks without writing raw records.")

    # 3. ROLLUP command
    rollup_parser = subparsers.add_parser("rollup", help="Precompute activity rollups and notability signals.")
    rollup_parser.add_argument("--years", type=float, default=1.0, help="Years of rollups to compute (default: 1.0).")
    rollup_parser.add_argument("--since", type=str, help="Start ISO timestamp.")
    rollup_parser.add_argument("--until", type=str, help="End ISO timestamp.")
    rollup_parser.add_argument("--identity", type=str, help="GitHub username.")

    # 4. SUMMARIZE command
    summarize_parser = subparsers.add_parser("summarize", help="Format or generate task prompts for rollup summarization.")
    summarize_parser.add_argument("--years", type=float, default=1.0, help="Years to cover.")
    summarize_parser.add_argument("--since", type=str, help="Start ISO timestamp.")
    summarize_parser.add_argument("--until", type=str, help="End ISO timestamp.")
    summarize_parser.add_argument("--identity", type=str, help="GitHub username.")
    summarize_parser.add_argument("--output", type=str, help="File path to write the full JSON prompt handoff (default: summarization_handoff.json).")

    # 5. NARRATIVE command
    narrative_parser = subparsers.add_parser("narrative", help="Generate paced markdown narrative document.")
    narrative_parser.add_argument("--range", type=str, default="full", help="Range selection: 'full', '1y', '2024', etc.")
    narrative_parser.add_argument("--since", type=str, help="Start ISO timestamp for custom date window.")
    narrative_parser.add_argument("--until", type=str, help="End ISO timestamp for custom date window.")
    narrative_parser.add_argument("--identity", type=str, help="GitHub username.")
    narrative_parser.add_argument("--output", type=str, help="File path to save output markdown narrative.")

    # 6. PIPELINE / RUN-ALL command
    pipeline_parser = subparsers.add_parser("pipeline", aliases=["run-all"], help="Execute complete pipeline (backfill -> rollups -> notability -> narrative).")
    pipeline_parser.add_argument("--years", type=float, default=1.0, help="Years of history to backfill and report.")
    pipeline_parser.add_argument("--since", type=str, help="Start ISO timestamp (overrides --years for backfill/rollup/summarize).")
    pipeline_parser.add_argument("--until", type=str, help="End ISO timestamp (overrides --years for backfill/rollup/summarize).")
    pipeline_parser.add_argument("--range", type=str, default="full", help="Narrative range selection.")
    pipeline_parser.add_argument("--identity", type=str, help="GitHub username.")
    pipeline_parser.add_argument("--repo", type=str, help="Optional specific repo to backfill (owner/repo).")
    pipeline_parser.add_argument("--output", type=str, help="Path for narrative output file.")
    pipeline_parser.add_argument("--yes", "-y", action="store_true", help="Auto-accept GitHub auth session.")
    pipeline_parser.add_argument("--device-code", action="store_true", help="Force GitHub device-code auth flow.")
    pipeline_parser.add_argument("--dry-run", action="store_true", help="Perform discovery/pre-checks without writing raw records (backfill step only; skips rollup/summarize/narrative since there is no real data to act on).")
    pipeline_parser.add_argument(
        "--skip-real-summarization", action="store_true",
        help=(
            "Skip invoking scripts/summarize_periods.py (the harness-side "
            "real model call) and go straight to narrative generation. "
            "The resulting narrative will fall back to templated, "
            "per-repo summaries instead of connected cross-repo prose "
            "(see app/summarization.py's module docstring) -- use this "
            "only if no provider credentials are configured."
        ),
    )
    pipeline_parser.add_argument(
        "--provider", type=str, choices=["anthropic", "gemini", "openai"],
        help="Force a specific provider for real summarization (passed through to scripts/summarize_periods.py).",
    )

    return parser


def handle_auth(args: argparse.Namespace) -> int:
    """Handle authentication check/flow."""
    print("=== Checking Fulcra Authentication ===")
    try:
        f_client = get_fulcra_client()
        print("Fulcra authentication: SUCCESS")
    except FulcraAuthError as err:
        print(f"Fulcra authentication: FAILED ({err})")
        return 1

    print("\n=== Checking GitHub Authentication ===")
    token = get_github_auth_token(
        confirm_existing=not args.yes,
        force_device_code=args.device_code,
        auto_accept_existing=args.yes,
    )
    identity = get_token_identity(token) or "unknown"
    print(f"GitHub authentication: SUCCESS (User: {identity})")
    return 0


def handle_backfill(args: argparse.Namespace) -> int:
    """Execute raw GitHub activity backfill."""
    try:
        f_client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr)
        return 1

    token = get_github_auth_token(
        confirm_existing=not args.yes,
        force_device_code=args.device_code,
        auto_accept_existing=args.yes,
    )
    identity = args.identity or get_token_identity(token)
    if not identity:
        print("Error: Could not determine GitHub username identity. Pass --identity explicitly.", file=sys.stderr)
        return 1

    print(f"\n--- Initiating Backfill for GitHub identity: {identity} ---")
    spike = GitHubAPISpike(token=token)
    engine = BackfillEngine(fulcra_client=f_client, github_api=spike)

    if args.dry_run:
        print("[Dry Run] Discovering repositories and pre-checking existence...")
        repos = spike.discover_user_repos(github_identity=identity)
        print(f"[Dry Run] Discovered {len(repos)} repositories.")
        return 0

    end_dt = datetime.now(timezone.utc)
    start_dt = datetime.fromtimestamp(end_dt.timestamp() - (args.years * 365.25 * 86400), tz=timezone.utc)
    since_iso = args.since or start_dt.isoformat().replace("+00:00", "Z")
    until_iso = args.until or end_dt.isoformat().replace("+00:00", "Z")

    summary = engine.run_backfill(
        github_identity=identity,
        start_time=since_iso,
        end_time=until_iso,
        repos=[args.repo] if args.repo else None,
    )

    print("\n--- Backfill Execution Summary ---")
    print(f"Identity: {summary.get('github_identity')}")
    print(f"Total Repos Processed: {summary.get('repos_total')}")
    print(f"Active Repos Ingested: {len(summary.get('repos_active', []))}")
    print(f"Total Raw Records Written: {summary.get('records_ingested')}")
    print(f"GitHub API Calls: {summary.get('api_calls_made')}")
    print(f"Wall-Clock Time: {summary.get('wall_time_seconds')}s")

    return 0


def handle_rollup(args: argparse.Namespace) -> int:
    """Execute activity rollups and notability signal computation."""
    try:
        f_client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr)
        return 1

    token = get_github_auth_token(auto_accept_existing=True)
    identity = args.identity or get_token_identity(token) or "gklei"

    end_dt = datetime.now(timezone.utc)
    start_dt = datetime.fromtimestamp(end_dt.timestamp() - (args.years * 365.25 * 86400), tz=timezone.utc)
    since_iso = args.since or start_dt.isoformat().replace("+00:00", "Z")
    until_iso = args.until or end_dt.isoformat().replace("+00:00", "Z")

    print(f"\n--- Fetching Raw Activity ({since_iso[:10]} to {until_iso[:10]}) ---")
    raw_ingestor = RawActivityIngestor(client=f_client)
    raw_items = raw_ingestor.get_raw_activities(
        github_identity=identity,
        start_time=since_iso,
        end_time=until_iso,
    )
    print(f"Found {len(raw_items)} raw activity records to roll up.")

    print(f"\n--- Computing Activity Rollups ({since_iso[:10]} to {until_iso[:10]}) ---")
    rollup_engine = RollupEngine(client=f_client)
    rollups_by_period = rollup_engine.generate_all_rollups(
        raw_items=raw_items,
        github_identity=identity,
        save_to_fulcra=True,
    )
    all_rollups = [r for period_rollups in rollups_by_period.values() for r in period_rollups]
    print(f"Successfully stored {len(all_rollups)} activity rollups across period types.")

    print(f"\n--- Computing Notability Signals ({since_iso[:10]} to {until_iso[:10]}) ---")
    notability_engine = NotabilityEngine(client=f_client)
    signals = notability_engine.compute_signals(all_rollups)
    notability_engine.save_signals(signals)
    print(f"Successfully stored {len(signals)} notability signal records.")

    return 0


def handle_summarize(args: argparse.Namespace) -> int:
    """Execute rollup summarization task prompt building.

    This command only PREVIEWS/exports the structured prompts -- it does
    not call a model or write anything back (per
    app/features/m7_rollup_summarization.md: app/ code must have zero
    LLM provider SDK dependencies). To actually generate and persist
    real cross-repo period summaries (what makes the eventual narrative
    read as connected prose instead of one templated line per rollup),
    run the harness-side driver script instead:

        python scripts/summarize_periods.py --identity <username>

    from the repo root (see README.md / SKILL.md).
    """
    try:
        f_client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr)
        return 1

    token = get_github_auth_token(auto_accept_existing=True)
    identity = args.identity or get_token_identity(token) or "gklei"

    end_dt = datetime.now(timezone.utc)
    start_dt = datetime.fromtimestamp(end_dt.timestamp() - (args.years * 365.25 * 86400), tz=timezone.utc)
    since_iso = args.since or start_dt.isoformat().replace("+00:00", "Z")
    until_iso = args.until or end_dt.isoformat().replace("+00:00", "Z")

    rollup_engine = RollupEngine(client=f_client)
    rollups = rollup_engine.get_rollups(
        github_identity=identity,
        start_time=since_iso,
        end_time=until_iso,
    )
    raw_items = RawActivityIngestor(f_client).get_raw_activities(
        github_identity=identity, start_time=since_iso, end_time=until_iso,
    )
    attach_raw_evidence(rollups, raw_items)
    print(f"\n--- Preparing Cross-Repo Period Summarization Handoff ({len(rollups)} rollups) ---")

    summarizer = RollupSummarizer(client=f_client)
    handoff_payloads = summarizer.prepare_period_handoff(rollups)

    out_path = args.output or "summarization_handoff.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(handoff_payloads, f, indent=2)

    print(f"\nWrote {len(handoff_payloads)} cross-repo period prompt(s) to: {out_path}")
    print(
        "\nThis command only previews prompts -- it does not call a model "
        "or write summaries back. To actually generate and persist real "
        "prose (recommended; produces a genuinely engaging narrative "
        "instead of a templated one), run:\n"
        "  python scripts/summarize_periods.py --identity "
        f"{identity}\n"
        "from the repo root. See that script's --help / SKILL.md for details."
    )

    return 0


def handle_narrative(args: argparse.Namespace) -> int:
    """Generate markdown narrative document."""
    try:
        f_client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr)
        return 1

    token = get_github_auth_token(auto_accept_existing=True)
    identity = args.identity or get_token_identity(token) or "gklei"

    print(f"\n--- Generating Narrative Document (Range: {args.range}) ---")
    generator = NarrativeGenerator(client=f_client)
    try:
        doc_content, filename, rollups, signals = generator.generate_narrative(
            github_identity=identity,
            range_selection=args.range,
        )
    except NarrativeUploadError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    out_path = args.output or filename
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc_content)

    print(f"Successfully generated narrative document: {out_path}")
    if generator.last_fulcra_path:
        print(f"Saved automatically to Fulcra: {generator.last_fulcra_path}")
    print(f"Word Count: {len(doc_content.split())} words")
    print(f"Rollup Records Used: {len(rollups)}")
    print(f"Notability Signals Used: {len(signals)}")

    return 0


def handle_pipeline(args: argparse.Namespace) -> int:
    """Run complete pipeline end-to-end."""
    print("============================================================")
    print(" Engineering Journey v2 — End-to-End Pipeline Execution")
    print("============================================================")

    # Fail before a potentially long GitHub/Fulcra run when the requested
    # quality narrative cannot be generated. Explicit skip is an honest opt-in.
    if not getattr(args, "skip_real_summarization", False) and not getattr(args, "dry_run", False):
        import subprocess

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(repo_root, "scripts", "summarize_periods.py")
        check_cmd = [sys.executable, script_path, "--check-provider"]
        if getattr(args, "provider", None):
            check_cmd += ["--provider", args.provider]
        check = subprocess.run(check_cmd, cwd=repo_root)
        if check.returncode != 0:
            print(
                "\nError: a quality Engineering Journey requires a configured "
                "model provider, and none is usable. No backfill was started. "
                "Configure harness provider credentials, or explicitly pass "
                "--skip-real-summarization to produce a clearly labelled limited fallback.",
                file=sys.stderr,
            )
            return check.returncode

    # Step 1: Auth check & Backfill
    ret = handle_backfill(args)
    if ret != 0:
        return ret

    if getattr(args, "dry_run", False):
        print(
            "\n[Dry Run] Backfill was a discovery-only dry run; skipping "
            "rollup/summarize/narrative steps since there is no real "
            "ingested data yet for them to act on."
        )
        return 0

    # Step 2: Rollups & Notability
    ret = handle_rollup(args)
    if ret != 0:
        return ret

    # Step 3: Real cross-repo period summarization. This is the step
    # that actually produces engaging, connected narrative prose instead
    # of a templated one-liner per rollup (see
    # app/summarization.py's module docstring / GitHub issue #2). It
    # requires calling a real model, which app/ code is not allowed to
    # do directly (app/features/m7_rollup_summarization.md: no LLM SDK
    # dependency in app code) -- so this shells out to the harness-side
    # driver script as a separate process, keeping that boundary intact.
    if not getattr(args, "skip_real_summarization", False):
        print("\n--- Generating real cross-repo period summaries (scripts/summarize_periods.py) ---")
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(repo_root, "scripts", "summarize_periods.py")
        cmd = [sys.executable, script_path, "--years", str(args.years)]
        if args.identity:
            cmd += ["--identity", args.identity]
        if getattr(args, "since", None):
            cmd += ["--since", args.since]
        if getattr(args, "until", None):
            cmd += ["--until", args.until]
        if getattr(args, "provider", None):
            cmd += ["--provider", args.provider]

        import subprocess

        result = subprocess.run(cmd, cwd=repo_root)
        if result.returncode != 0:
            print(
                "\nWarning: real summarization step failed or found no "
                "provider credentials configured (see output above). "
                "Continuing to narrative generation -- it will fall back "
                "to templated per-repo summaries for any period that "
                "didn't get a real one written back.",
                file=sys.stderr,
            )
    else:
        print(
            "\n--skip-real-summarization set: skipping real cross-repo "
            "summarization. The narrative will use templated per-repo "
            "summaries instead of connected prose."
        )

    # Step 4: Narrative Generation
    ret = handle_narrative(args)
    return ret


def main(argv: Optional[List[str]] = None) -> int:
    """CLI Entry Point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "auth":
        return handle_auth(args)
    elif args.command == "backfill":
        return handle_backfill(args)
    elif args.command == "rollup":
        return handle_rollup(args)
    elif args.command == "summarize":
        return handle_summarize(args)
    elif args.command == "narrative":
        return handle_narrative(args)
    elif args.command in ("pipeline", "run-all"):
        return handle_pipeline(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
