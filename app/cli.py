"""Command-Line Interface (CLI) for Engineering Journey v2.

Directly runnable entry point supporting backfill, activity rollups, notability signals,
rollup summarization, and markdown narrative generation with no agent dependency required.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any, Dict, List, Optional

from agent_narration import (
    AgentNarrationValidationError,
    prepare_agent_handoff,
    publish_agent_narrative,
)
from backfill import BackfillEngine
from fulcra_client import FulcraAuthError, get_fulcra_client
from github_auth import (
    ExistingAuthConfirmationRequired,
    GitHubAuthenticationCancelled,
    get_github_auth_token,
    get_token_identity,
)
from github_spike import GitHubAPISpike
from history_coverage import HistoryCoverageManager
from narrative import NarrativeGenerator, NarrativeUploadError
from notability import NotabilityEngine
from raw_ingestion import RawActivityIngestor
from pipeline_run import PipelineRun, PipelineRunManager
from progress import ProgressReporter, format_progress_status, progress_snapshot
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
    auth_parser.add_argument("--yes", "-y", action="store_true", help="Confirm a previously reviewed GitHub account non-interactively.")

    # 2. BACKFILL command
    backfill_parser = subparsers.add_parser("backfill", help="Ingest raw GitHub activity into Fulcra.")
    backfill_parser.add_argument("--years", type=float, default=1.0, help="Years of history to backfill (default: 1.0).")
    backfill_parser.add_argument("--since", type=str, help="Start ISO timestamp (e.g. 2024-01-01T00:00:00Z).")
    backfill_parser.add_argument("--until", type=str, help="End ISO timestamp (e.g. 2025-01-01T00:00:00Z).")
    backfill_parser.add_argument("--identity", type=str, help="GitHub username/identity.")
    backfill_parser.add_argument("--repo", type=str, help="Optional specific repo to backfill (owner/repo).")
    backfill_parser.add_argument("--yes", "-y", action="store_true", help="Confirm the displayed account and run plan non-interactively.")
    backfill_parser.add_argument("--device-code", action="store_true", help="Force GitHub device-code auth flow.")
    backfill_parser.add_argument("--dry-run", action="store_true", help="Perform discovery/pre-checks without writing raw records.")
    backfill_parser.add_argument("--resume", action="store_true", help="Resume latest incomplete durable run for this identity/scope.")
    backfill_parser.add_argument("--progress-jsonl", help="Machine-readable progress JSONL path.")

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

    agent_handoff_parser = subparsers.add_parser(
        "agent-handoff",
        help="Prepare grounded narrative context for the LLM already running the skill.",
    )
    agent_handoff_parser.add_argument("--identity", required=True, help="GitHub identity.")
    agent_handoff_parser.add_argument("--range", default="full", help="Narrative range selection.")
    agent_handoff_parser.add_argument("--since", help="Exact immutable pipeline start timestamp.")
    agent_handoff_parser.add_argument("--until", help="Exact immutable pipeline end timestamp.")
    agent_handoff_parser.add_argument("--repo", help="Optional repository filter.")
    agent_handoff_parser.add_argument("--output", required=True, help="JSON handoff path.")
    agent_handoff_parser.add_argument("--progress-jsonl", help="Append structured progress to this JSONL path.")

    publish_parser = subparsers.add_parser(
        "publish-agent-narrative",
        help="Validate and publish prose written by the LLM running the skill.",
    )
    publish_parser.add_argument("--handoff", required=True, help="Grounded handoff JSON path.")
    publish_parser.add_argument("--response", required=True, help="Agent-authored response JSON path.")
    publish_parser.add_argument("--output", help="Optional local markdown output path.")
    publish_parser.add_argument("--progress-jsonl", help="Append structured progress to this JSONL path.")

    progress_parser = subparsers.add_parser(
        "progress-status",
        help="Summarize progress JSONL into one user-facing status line.",
    )
    progress_parser.add_argument("--file", required=True, help="Progress JSONL path.")
    progress_parser.add_argument(
        "--json", action="store_true", help="Print snapshot JSON instead of prose."
    )

    migration_parser = subparsers.add_parser(
        "coverage-migration",
        help="Plan, migrate, or explicitly remove legacy per-repo checkpoint types.",
    )
    migration_actions = migration_parser.add_mutually_exclusive_group(required=True)
    migration_actions.add_argument("--plan", action="store_true")
    migration_actions.add_argument("--migrate", action="store_true")
    migration_actions.add_argument("--delete-legacy-types", action="store_true")
    migration_parser.add_argument("--yes", action="store_true")
    migration_parser.add_argument(
        "--confirm-delete-legacy-checkpoints",
        action="store_true",
        help="Separate destructive confirmation required with --delete-legacy-types.",
    )

    # 6. PIPELINE / RUN-ALL command
    pipeline_parser = subparsers.add_parser("pipeline", aliases=["run-all"], help="Execute raw backfill -> agent handoff (canonical mode).")
    pipeline_parser.add_argument("--years", type=float, default=1.0, help="Years of history to backfill and report.")
    pipeline_parser.add_argument("--since", type=str, help="Start ISO timestamp (overrides --years for backfill/rollup/summarize).")
    pipeline_parser.add_argument("--until", type=str, help="End ISO timestamp (overrides --years for backfill/rollup/summarize).")
    pipeline_parser.add_argument("--range", type=str, default="full", help="Narrative range selection.")
    pipeline_parser.add_argument("--identity", type=str, help="GitHub username.")
    pipeline_parser.add_argument("--repo", type=str, help="Optional specific repo to backfill (owner/repo).")
    pipeline_parser.add_argument("--output", type=str, help="Path for narrative output file.")
    pipeline_parser.add_argument("--yes", "-y", action="store_true", help="Confirm a previously reviewed GitHub account and run plan.")
    pipeline_parser.add_argument("--device-code", action="store_true", help="Force GitHub device-code auth flow.")
    pipeline_parser.add_argument("--dry-run", action="store_true", help="Perform discovery/pre-checks without writing raw records (backfill step only; skips rollup/summarize/narrative since there is no real data to act on).")
    pipeline_parser.add_argument("--resume", action="store_true", help="Resume the latest incomplete durable run and reuse its exact window/repositories.")
    pipeline_parser.add_argument("--progress-jsonl", help="Machine-readable progress path (default: engineering_journey_progress.jsonl).")
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
        help="Provider for explicitly selected external narration mode only.",
    )
    pipeline_parser.add_argument(
        "--narration-mode",
        choices=["agent", "external", "limited"],
        default="agent",
        help="Narration source (default: the LLM already running this skill).",
    )
    pipeline_parser.add_argument(
        "--handoff-output",
        help="Agent-mode JSON handoff path (default: readable range-based filename).",
    )

    return parser


def _emit(message: str = "") -> None:
    """Print progress immediately, including through buffered agent shells."""
    print(message, flush=True)


def _progress_reporter(args: argparse.Namespace) -> ProgressReporter:
    existing = getattr(args, "_progress_reporter", None)
    if existing:
        return existing
    path = getattr(args, "progress_jsonl", None)
    if path is None and args.command in ("pipeline", "run-all", "backfill"):
        path = "engineering_journey_progress.jsonl"
        args.progress_jsonl = path
    reporter = ProgressReporter(
        path,
        human_callback=_emit,
        append=bool(getattr(args, "resume", False))
        or args.command == "publish-agent-narrative",
    )
    args._progress_reporter = reporter
    if reporter.path:
        _emit(f"[progress] Machine-readable events: {reporter.path}")
        reporter.emit(
            {
                "event": "progress_stream_ready",
                "stage": "pipeline",
                "path": str(reporter.path),
            }
        )
    return reporter


def _activity_window(args: argparse.Namespace) -> tuple[str, str]:
    end_dt = datetime.now(timezone.utc)
    start_dt = datetime.fromtimestamp(
        end_dt.timestamp() - (args.years * 365.25 * 86400), tz=timezone.utc
    )
    return (
        args.since or start_dt.isoformat().replace("+00:00", "Z"),
        args.until or end_dt.isoformat().replace("+00:00", "Z"),
    )


def _confirm_backfill_plan(
    args: argparse.Namespace,
    authenticated_identity: str,
    target_identity: str,
    since_iso: str,
    until_iso: str,
) -> bool:
    start_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
    if start_dt >= end_dt:
        _emit("Error: activity start time must be before end time.")
        return False
    duration_days = (end_dt - start_dt).total_seconds() / 86400
    pipeline = args.command in ("pipeline", "run-all")
    _emit("\n" + "=" * 60)
    _emit(" Review Engineering Journey Run Plan")
    _emit("=" * 60)
    _emit(f"Authenticated GitHub account: {authenticated_identity}")
    _emit(f"Activity identity:            {target_identity}")
    _emit(f"Activity range (UTC):         {since_iso}  →  {until_iso}")
    _emit(f"Range duration:               {duration_days:.1f} days")
    _emit(f"Repository scope:             {args.repo or 'all accessible repositories'}")
    _emit(f"Mode:                         {'discovery only; no writes' if args.dry_run else 'write durable records to Fulcra'}")
    if getattr(args, "resume", False):
        resume_run = getattr(args, "_resume_run", None)
        _emit(
            f"Resume:                       durable run {resume_run.run_id if resume_run else 'requested'}; reuse its exact window and repo list"
        )
    if pipeline and not args.dry_run:
        if getattr(args, "narration_mode", "agent") == "agent":
            _emit("Stages:                       backfill raw history → agent evidence handoff → this LLM writes → validated Fulcra file")
        else:
            _emit("Stages:                       backfill → legacy derived stages → selected narration mode")
    else:
        _emit("Stages:                       repository discovery → coverage checks → raw ingestion")
    _emit("=" * 60)

    if args.yes:
        _emit("Plan confirmed via --yes (use only after the user has reviewed it).")
        return True
    if not sys.stdin.isatty():
        _emit(
            "Plan not started: this is a non-interactive session. Show the plan "
            "above to the user, then rerun with --yes only if they approve."
        )
        return False
    try:
        answer = input("Proceed with this exact plan? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        _emit("\nCancelled; no backfill work was started.")
        return False
    if answer not in ("y", "yes"):
        _emit("Cancelled; no backfill work was started.")
        return False
    _emit("Plan confirmed. Starting now; progress will continue below.")
    return True


def handle_auth(args: argparse.Namespace) -> int:
    """Handle authentication check/flow."""
    _emit("=== Checking Fulcra Authentication ===")
    try:
        get_fulcra_client()
        _emit("Fulcra authentication: SUCCESS")
    except FulcraAuthError as err:
        _emit(f"Fulcra authentication: FAILED ({err})")
        return 1

    _emit("\n=== Checking GitHub Authentication ===")
    try:
        token = get_github_auth_token(
            confirm_existing=not args.yes,
            force_device_code=args.device_code,
            auto_accept_existing=args.yes,
        )
    except (ExistingAuthConfirmationRequired, GitHubAuthenticationCancelled) as err:
        print(f"GitHub authentication not used: {err}", file=sys.stderr, flush=True)
        return 2
    identity = get_token_identity(token) or "unknown"
    _emit(f"GitHub authentication: SUCCESS (User: {identity})")
    return 0


def handle_backfill(args: argparse.Namespace) -> int:
    """Review, run, or resume the canonical raw-history backfill."""
    try:
        client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr, flush=True)
        return 1
    reporter = _progress_reporter(args)
    run_manager = PipelineRunManager(client, event_callback=reporter.emit)

    try:
        token = get_github_auth_token(
            confirm_existing=not args.yes,
            force_device_code=args.device_code,
            auto_accept_existing=args.yes,
        )
    except ExistingAuthConfirmationRequired as err:
        target_identity = args.identity or err.identity
        resume_run = (
            run_manager.latest_incomplete(target_identity, args.repo)
            if getattr(args, "resume", False) else None
        )
        if resume_run:
            args._resume_run = resume_run
            since_iso, until_iso = resume_run.start_time, resume_run.end_time
        else:
            since_iso, until_iso = _activity_window(args)
        _confirm_backfill_plan(args, err.identity, target_identity, since_iso, until_iso)
        print(f"GitHub authentication not used: {err}", file=sys.stderr, flush=True)
        return 2
    except GitHubAuthenticationCancelled as err:
        print(f"GitHub authentication not used: {err}", file=sys.stderr, flush=True)
        return 2

    authenticated_identity = get_token_identity(token) or "unknown"
    identity = args.identity or (
        authenticated_identity if authenticated_identity != "unknown" else None
    )
    if not identity:
        print(
            "Error: Could not determine GitHub username identity. Pass --identity explicitly.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    run: Optional[PipelineRun] = None
    if getattr(args, "resume", False):
        run = run_manager.latest_incomplete(identity, args.repo)
        if run is None:
            print(
                "Error: no incomplete durable run exists for this identity/repository scope.",
                file=sys.stderr,
                flush=True,
            )
            return 1
        since_iso, until_iso = run.start_time, run.end_time
        args.repo = run.repo
        args._resume_run = run
        _emit(
            f"[resume] Found run {run.run_id}: stage={run.stage}, "
            f"repositories={run.next_repo_index}/{len(run.repositories)}, "
            f"window={since_iso} to {until_iso}."
        )
        reporter.emit(
            {
                "event": "resume_plan",
                "stage": run.stage,
                "run_id": run.run_id,
                "start_time": since_iso,
                "end_time": until_iso,
                "repos_completed": run.next_repo_index,
                "repos_total": len(run.repositories),
            }
        )
    else:
        since_iso, until_iso = _activity_window(args)

    if not _confirm_backfill_plan(
        args, authenticated_identity, identity, since_iso, until_iso
    ):
        return 2
    args.identity, args.since, args.until = identity, since_iso, until_iso

    spike = GitHubAPISpike(
        token=token,
        progress_callback=_emit,
        event_callback=reporter.emit,
    )
    if args.dry_run:
        _emit("[dry-run] Discovering repositories; no records will be written...")
        repos = spike.discover_user_repos(github_identity=identity)
        _emit(f"[dry-run] Discovery complete: {len(repos)} repositories accessible.")
        return 0

    if run is None:
        run = PipelineRun.create(identity, since_iso, until_iso, args.repo)
        run_manager.save(run)
    args._pipeline_run = run
    args._pipeline_run_manager = run_manager

    if run.stage in ("raw_complete", "handoff_complete"):
        _emit(
            f"[resume] Raw stage already complete for run {run.run_id}; "
            "skipping GitHub discovery and all repository processing."
        )
        reporter.emit(
            {
                "event": "stage_skipped",
                "stage": "backfill",
                "run_id": run.run_id,
                "reason": "durable raw_complete state",
            }
        )
        return 0


    if run.repositories:
        repositories = run.repositories
        _emit(
            f"[resume] Reusing {len(repositories)} durable discovered repositories; "
            "GitHub discovery skipped."
        )
    else:
        reporter.start_stage("discovery", "[discovery] Listing accessible repositories...")
        repositories = [args.repo] if args.repo else spike.discover_user_repos(identity)
        run.repositories = repositories
        run.next_repo_index = 0
        run.stage = "repos_discovered"
        run_manager.save(run)
        reporter.finish_stage(
            "discovery",
            f"[discovery] Saved {len(repositories)} repositories in durable run state.",
            repositories=len(repositories),
        )

    start_index = min(run.next_repo_index, len(repositories))
    remaining = repositories[start_index:]
    base_records = run.records_ingested
    def event_callback(event: Dict[str, Any]) -> None:
        reporter.emit({"run_id": run.run_id, **event})
        completed = int(event.get("repos_completed", run.next_repo_index))
        should_checkpoint = (
            completed > run.next_repo_index
            and (completed % 25 == 0 or event.get("event") == "stage_completed")
        )
        if should_checkpoint:
            run.next_repo_index = completed
            run.records_ingested = base_records + int(event.get("records_written", 0))
            run.stage = "repos_discovered"
            run_manager.save(run)
    engine = BackfillEngine(
        fulcra_client=client,
        github_api=spike,
        progress_callback=_emit,
        event_callback=event_callback,
    )
    reporter.start_stage(
        "backfill",
        f"[backfill] Processing repositories {start_index + 1}..{len(repositories)} "
        f"for immutable run {run.run_id}.",
    )
    summary = engine.run_backfill(
        github_identity=identity,
        start_time=since_iso,
        end_time=until_iso,
        repos=remaining,
        repo_offset=start_index,
        repos_total_override=len(repositories),
        kill_after_n_records=getattr(args, "kill_after_n_records", None),
        run_id=run.run_id,
        raw_record_count_base=base_records,
    )
    run.records_ingested = base_records + int(summary["records_ingested"])
    if summary["interrupted"]:
        run.stage = "repos_discovered"
        run_manager.save(run)
        reporter.emit(
            {
                "event": "pipeline_interrupted",
                "stage": "backfill",
                "run_id": run.run_id,
                "repos_completed": run.next_repo_index,
                "records_written": run.records_ingested,
            }
        )
        return 130

    run.next_repo_index = len(repositories)
    run.stage = "raw_complete"
    run_manager.save(run)
    reporter.finish_stage(
        "backfill",
        "[backfill] Raw history and coverage are complete.",
        repositories=len(repositories),
        records_written=run.records_ingested,
        github_api_calls=summary["api_calls_made"],
    )
    _emit("\n--- Backfill Execution Summary ---")
    _emit(f"Run ID: {run.run_id}")
    _emit(f"Immutable window: {since_iso} to {until_iso}")
    _emit(f"Repositories: {len(repositories)}")
    _emit(f"Total raw records written: {run.records_ingested}")
    _emit(f"This invocation: {summary['wall_time_seconds']}s, {summary['api_calls_made']} GitHub API calls")
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

    _emit(f"\n[rollup] Fetching durable raw activity ({since_iso[:10]} to {until_iso[:10]})...")
    raw_ingestor = RawActivityIngestor(client=f_client)
    raw_items = raw_ingestor.get_raw_activities(
        github_identity=identity,
        start_time=since_iso,
        end_time=until_iso,
    )
    _emit(f"[rollup] Found {len(raw_items)} raw activity records.")

    _emit("[rollup] Computing day, week, month, quarter, and year layers...")
    rollup_engine = RollupEngine(client=f_client)
    rollups_by_period = rollup_engine.generate_all_rollups(
        raw_items=raw_items,
        github_identity=identity,
        save_to_fulcra=True,
    )
    all_rollups = [r for period_rollups in rollups_by_period.values() for r in period_rollups]
    _emit(f"[rollup] Stored {len(all_rollups)} rollups across all period types.")

    _emit("[notability] Computing personal-baseline eventfulness signals...")
    notability_engine = NotabilityEngine(client=f_client)
    signals = notability_engine.compute_signals(all_rollups)
    notability_engine.save_signals(signals)
    _emit(f"[notability] Stored {len(signals)} signals. Derived-data stage complete.")

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

    _emit(f"\n[narrative] Loading summaries and signals for range: {args.range}...")
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
    _emit("[narrative] Fulcra upload complete; writing the local convenience copy...")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc_content)

    print(f"Successfully generated narrative document: {out_path}")
    if generator.last_fulcra_path:
        print(f"Saved automatically to Fulcra: {generator.last_fulcra_path}")
    print(f"Word Count: {len(doc_content.split())} words")
    print(f"Rollup Records Used: {len(rollups)}")
    print(f"Notability Signals Used: {len(signals)}")

    return 0


def handle_agent_handoff(args: argparse.Namespace) -> int:
    """Export durable grounded context for the surrounding running LLM."""
    try:
        client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr, flush=True)
        return 1
    reporter = _progress_reporter(args)
    reporter.start_stage(
        "handoff", "[agent narration] Loading durable raw evidence from Fulcra..."
    )
    handoff = prepare_agent_handoff(
        client=client,
        github_identity=args.identity,
        range_selection=getattr(args, "range", "full"),
        repo=getattr(args, "repo", None),
        exact_start_time=getattr(args, "since", None),
        exact_end_time=getattr(args, "until", None),
        event_callback=reporter.emit,
    )
    if not handoff["chunks"]:
        print(
            "Error: no durable raw GitHub records were found for this identity/range.",
            file=sys.stderr,
            flush=True,
        )
        return 1
    with open(args.output, "w", encoding="utf-8") as file_handle:
        json.dump(handoff, file_handle, indent=2, ensure_ascii=False)
    run = getattr(args, "_pipeline_run", None)
    run_manager = getattr(args, "_pipeline_run_manager", None)
    if run and run_manager:
        handoff["pipeline_run_id"] = run.run_id
        handoff["progress_jsonl"] = str(reporter.path) if reporter.path else None
        with open(args.output, "w", encoding="utf-8") as file_handle:
            json.dump(handoff, file_handle, indent=2, ensure_ascii=False)
        run.stage = "handoff_complete"
        run_manager.save(run)
    reporter.finish_stage(
        "handoff",
        "[agent narration] Raw evidence handoff complete.",
        raw_records=handoff["metadata"]["raw_record_count"],
        chunks=len(handoff["chunks"]),
    )
    _emit(
        f"[agent narration] Handoff ready: {args.output} "
        f"({handoff['metadata']['raw_record_count']} raw records in "
        f"{len(handoff['chunks'])} adaptive chunks)."
    )
    _emit(
        "[agent narration] The LLM already running this skill must now read "
        "that JSON, author the response_schema JSON itself, and call "
        "publish-agent-narrative. No external model credentials are needed."
    )
    return 0


def handle_publish_agent_narrative(args: argparse.Namespace) -> int:
    """Validate, render, and retry-safe publish prose authored by the running agent."""
    try:
        client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr, flush=True)
        return 1
    try:
        with open(args.handoff, "r", encoding="utf-8") as file_handle:
            handoff = json.load(file_handle)
        with open(args.response, "r", encoding="utf-8") as file_handle:
            response = json.load(file_handle)
        if not getattr(args, "progress_jsonl", None):
            args.progress_jsonl = handoff.get("progress_jsonl")
        reporter = _progress_reporter(args)
        reporter.start_stage(
            "publish",
            "[agent narration] Validating chronology and raw-source completeness...",
        )
        published = publish_agent_narrative(
            client,
            handoff,
            response,
            output_path=args.output,
            event_callback=reporter.emit,
        )
    except (
        OSError,
        json.JSONDecodeError,
        AgentNarrationValidationError,
        NarrativeUploadError,
    ) as err:
        print(f"Error: agent narrative was not published: {err}", file=sys.stderr, flush=True)
        return 1

    run_id = handoff.get("pipeline_run_id")
    if run_id:
        manager = PipelineRunManager(client, event_callback=reporter.emit)
        matching = [run for run in manager.get_runs() if run.run_id == run_id]
        if matching:
            run = matching[0]
            run.stage = "published"
            manager.save(run)
    reporter.finish_stage(
        "publish",
        "[agent narration] Grounded narrative validated and published.",
        markdown_path=published.markdown_path,
        fulcra_path=published.fulcra_path,
        sources_markdown_path=published.sources_markdown_path,
        sources_fulcra_path=published.sources_fulcra_path,
    )
    reporter.finish_pipeline()
    _emit(f"[agent narration] Local markdown: {published.markdown_path}")
    _emit(f"[agent narration] Fulcra file: {published.fulcra_path}")
    _emit(f"[agent narration] Local sources file: {published.sources_markdown_path}")
    _emit(f"[agent narration] Fulcra sources file: {published.sources_fulcra_path}")
    return 0


def handle_progress_status(args: argparse.Namespace) -> int:
    """Print one concise snapshot without touching GitHub or Fulcra."""
    snapshot = progress_snapshot(args.file)
    if args.json:
        print(json.dumps(snapshot, sort_keys=True), flush=True)
    else:
        print(format_progress_status(snapshot), flush=True)
    return 0


def handle_coverage_migration(args: argparse.Namespace) -> int:
    """Keep migration non-destructive unless two explicit confirmations are present."""
    try:
        client = get_fulcra_client()
    except FulcraAuthError as err:
        print(f"Error: Fulcra client failed: {err}", file=sys.stderr, flush=True)
        return 1
    manager = HistoryCoverageManager(client)
    plan = manager.migration_plan()
    if args.plan:
        print(json.dumps(plan, indent=2, sort_keys=True), flush=True)
        return 0
    if args.migrate:
        if not args.yes:
            print(
                "Migration not started: review --plan, then pass --migrate --yes.",
                file=sys.stderr,
                flush=True,
            )
            return 2
        result = manager.migrate_legacy()
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0

    if not args.yes or not args.confirm_delete_legacy_checkpoints:
        print(
            "Legacy deletion requires both --yes and "
            "--confirm-delete-legacy-checkpoints. Nothing was deleted.",
            file=sys.stderr,
            flush=True,
        )
        return 2
    existing_run_ids = {
        coverage.run_id for coverage in manager.get_coverages(refresh=True)
    }
    missing = [
        cohort["migration_run_id"]
        for cohort in plan["cohorts"]
        if cohort["migration_run_id"] not in existing_run_ids
    ]
    if missing:
        print(
            "Legacy deletion refused: migrate and verify all cohorts first. "
            f"Missing run-level coverage: {missing}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    deleted = manager.delete_legacy_types()
    print(f"Deleted legacy custom types: {', '.join(deleted) or 'none found'}", flush=True)
    return 0


def handle_pipeline(args: argparse.Namespace) -> int:
    """Run durable stages, then hand narration to the selected explicit mode."""
    reporter = _progress_reporter(args)
    _emit("=" * 60)
    _emit(" Engineering Journey v2 — Guided Pipeline")
    _emit("=" * 60)
    narration_mode = (
        "limited" if getattr(args, "skip_real_summarization", False)
        else getattr(args, "narration_mode", "agent")
    )
    _emit(f"[pipeline] Narration mode: {narration_mode}")
    _emit("[pipeline] No activity data has been touched yet.")
    reporter.emit(
        {"event": "pipeline_started", "stage": "pipeline", "narration_mode": narration_mode}
    )

    if narration_mode == "external" and not getattr(args, "dry_run", False):
        import subprocess

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(repo_root, "scripts", "summarize_periods.py")
        check_cmd = [sys.executable, script_path, "--check-provider"]
        if getattr(args, "provider", None):
            check_cmd += ["--provider", args.provider]
        _emit("[pipeline] External mode selected; checking its separately configured provider...")
        check = subprocess.run(check_cmd, cwd=repo_root)
        if check.returncode != 0:
            print(
                "External narration was explicitly selected, but its provider is unavailable. "
                "Use the default --narration-mode agent to use the LLM already running the skill.",
                file=sys.stderr,
                flush=True,
            )
            return check.returncode

    _emit("\n[pipeline 1/3] Confirming account/range and backfilling durable raw data.")
    result = handle_backfill(args)
    if result != 0:
        return result
    if getattr(args, "dry_run", False):
        _emit("[pipeline] Dry run complete; no derived or narrative stages were run.")
        return 0

    if narration_mode == "agent":
        _emit("\n[pipeline 2/2] Preparing adaptive raw evidence for this running agent.")
        handoff_output = args.handoff_output or (
            f"engineering_journey_handoff_{args.identity}_{args.since[:10]}_to_{args.until[:10]}.json"
        )
        handoff_args = argparse.Namespace(
            command="agent-handoff",
            identity=args.identity,
            range=args.range,
            since=args.since,
            until=args.until,
            repo=args.repo,
            output=handoff_output,
            progress_jsonl=getattr(args, "progress_jsonl", None),
            _progress_reporter=reporter,
            _pipeline_run=getattr(args, "_pipeline_run", None),
            _pipeline_run_manager=getattr(args, "_pipeline_run_manager", None),
        )
        return handle_agent_handoff(handoff_args)

    # Legacy derived layers are retained only for explicit standalone modes.
    _emit("\n[pipeline 2/3] Explicit standalone mode: building legacy rollups/notability.")
    result = handle_rollup(args)
    if result != 0:
        return result

    if narration_mode == "external":
        import subprocess

        _emit("\n[pipeline 3/3] Explicit external-provider synthesis.")
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(repo_root, "scripts", "summarize_periods.py")
        command = [
            sys.executable,
            script_path,
            "--identity",
            args.identity,
            "--since",
            args.since,
            "--until",
            args.until,
        ]
        if getattr(args, "provider", None):
            command += ["--provider", args.provider]
        external = subprocess.run(command, cwd=repo_root)
        if external.returncode != 0:
            print(
                "External synthesis failed; refusing to disguise fallback output as a quality narrative.",
                file=sys.stderr,
                flush=True,
            )
            return external.returncode
    else:
        _emit(
            "\n[pipeline 3/3] Explicit limited mode selected; output will be clearly labelled non-LLM fallback."
        )

    args.range = f"{args.since[:10]} to {args.until[:10]}"
    return handle_narrative(args)


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
    elif args.command == "agent-handoff":
        return handle_agent_handoff(args)
    elif args.command == "publish-agent-narrative":
        return handle_publish_agent_narrative(args)
    elif args.command == "progress-status":
        return handle_progress_status(args)
    elif args.command == "coverage-migration":
        return handle_coverage_migration(args)
    elif args.command in ("pipeline", "run-all"):
        return handle_pipeline(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
