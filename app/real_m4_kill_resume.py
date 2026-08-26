"""Real M4 kill/resume demonstration: run a real backfill against the real
authenticated GitHub account with a low kill_after_n_records budget to
force an interruption partway through, then resume from a completely
fresh BackfillEngine/CheckpointManager instance (simulating a fresh
process) and confirm it picks up where it left off without duplicating
or re-fetching already-covered repos -- at real scale, not the mocked
version in tests/test_backfill.py.

Run directly: .venv/bin/python real_m4_kill_resume.py
"""
import json
import os
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv()

from backfill import BackfillEngine, calculate_date_window
from fulcra_client import get_fulcra_client
from github_spike import GitHubAPISpike

github_identity = os.environ["GITHUB_USERNAME"]
github_token = os.environ["GITHUB_TOKEN"]

fulcra_client = get_fulcra_client()

# Real 1-year window, but restricted to a small, known-active repo set
# NOT already covered by the earlier real M4 measurement run (that run's
# checkpoints are already marked "completed" for those repos/window, so
# reusing the same repos here would correctly short-circuit as
# already-covered rather than demonstrate a genuine kill/resume). Uses a
# distinct, slightly-different window (bounded to the last 60 days
# instead of the full year) against the same known-active repos so this
# is a genuinely fresh, not-yet-covered range.
start_time, end_time = calculate_date_window(years=60 / 365.25)
known_active_repos = [
    "arc-claw-bot/fulcra-context",
    "fulcradynamics/agent-skills",
    "fulcradynamics/annotation-transform-task",
    "fulcradynamics/api-gateway-config",
]

print(f"Real kill/resume demo window: {start_time} .. {end_time}")
print(f"Real identity: {github_identity}")
print(f"Restricted to {len(known_active_repos)} known-active repos for a fast, cheap demo.\n")

print("--- Run 1: real backfill, killed after 2 records ---")
github_api_1 = GitHubAPISpike(token=github_token)
engine_1 = BackfillEngine(fulcra_client, github_api_1)
metrics_1 = engine_1.run_backfill(
    github_identity=github_identity,
    start_time=start_time,
    end_time=end_time,
    repos=known_active_repos,
    kill_after_n_records=2,
)
print(json.dumps(metrics_1, indent=2, default=str))
assert metrics_1["interrupted"] is True, "Run 1 should have been interrupted"

print("\n--- Run 2: completely fresh BackfillEngine/GitHubAPISpike instances (simulated fresh process), resuming ---")
github_api_2 = GitHubAPISpike(token=github_token)
engine_2 = BackfillEngine(fulcra_client, github_api_2)
metrics_2 = engine_2.run_backfill(
    github_identity=github_identity,
    start_time=start_time,
    end_time=end_time,
    repos=known_active_repos,
)
print(json.dumps(metrics_2, indent=2, default=str))

print("\n=== KILL/RESUME REAL-SCALE VERIFICATION ===")
print(f"Run 1 ingested: {metrics_1['records_ingested']} records (interrupted)")
print(f"Run 2 ingested: {metrics_2['records_ingested']} additional records (resumed)")
print(f"Run 2 repos_covered (already-completed, skipped): {metrics_2['repos_covered']}")
print(f"Run 2 repos_active (freshly processed): {metrics_2['repos_active']}")
