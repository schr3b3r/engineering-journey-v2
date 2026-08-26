"""Real M4 measurement: run a genuine 1-year backfill against the real
authenticated GitHub account (GITHUB_USERNAME/GITHUB_TOKEN from .env) and
the real authenticated Fulcra account, and print real volume/cost/wall-time
metrics -- not mocked, per M4's actual 'Done when' bar.

Run directly: .venv/bin/python real_m4_measurement.py
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
github_api = GitHubAPISpike(token=github_token)
engine = BackfillEngine(fulcra_client, github_api)

start_time, end_time = calculate_date_window(years=1.0)
print(f"Real 1-year window: {start_time} .. {end_time}")
print(f"Real identity: {github_identity}")
print("Starting real backfill run (repo discovery capped at 100 repos)...\n")

metrics = engine.run_backfill(
    github_identity=github_identity,
    start_time=start_time,
    end_time=end_time,
)

print("\n=== REAL M4 MEASUREMENT RESULTS ===")
print(json.dumps(metrics, indent=2, default=str))
