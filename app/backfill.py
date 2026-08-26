"""Multi-Repo Multi-Year GitHub Activity Backfill Engine.

Orchestrates multi-repo discovery, existence pre-checks, uniform daily-granularity
ingestion, durable checkpointing, interruption/resumability, and volume/cost/performance
metrics measurement for 1/2/3-year windows.
"""

from datetime import datetime, timedelta, timezone
import time
from typing import Any, Dict, List, Optional, Tuple

from checkpoint import Checkpoint, CheckpointManager
from github_spike import GitHubActivityItem, GitHubAPISpike
from raw_ingestion import RawActivityIngestor


def calculate_date_window(
    years: float,
    end_date: Optional[str] = None,
) -> Tuple[str, str]:
    """Calculate ISO 8601 start_time and end_time strings for a given number of years.

    Args:
        years: float number of years (e.g. 1.0, 2.0, 3.0).
        end_date: optional ISO string for end date. Defaults to current UTC time.

    Returns:
        (start_time_iso, end_time_iso)
    """
    if end_date:
        if end_date.endswith("Z"):
            end_dt = datetime.fromisoformat(end_date[:-1] + "+00:00")
        else:
            end_dt = datetime.fromisoformat(end_date)
    else:
        end_dt = datetime.now(timezone.utc)

    days = int(years * 365.25)
    start_dt = end_dt - timedelta(days=days)

    return start_dt.isoformat().replace("+00:00", "Z"), end_dt.isoformat().replace("+00:00", "Z")


class BackfillEngine:
    """Orchestrates multi-repo activity backfilling into Fulcra with durability and performance metrics."""

    def __init__(self, fulcra_client: Any, github_api: GitHubAPISpike) -> None:
        self.fulcra_client = fulcra_client
        self.github_api = github_api
        self.checkpoint_manager = CheckpointManager(fulcra_client)
        self.raw_ingestor = RawActivityIngestor(fulcra_client)

    def run_backfill(
        self,
        github_identity: str,
        start_time: str,
        end_time: str,
        repos: Optional[List[str]] = None,
        kill_after_n_records: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run backfill for `github_identity` across specified date range and repos.

        If `repos` is None, automatically discovers accessible public and private repos.
        Uses existence pre-checks to skip repos without activity in the window.
        Tracks wall-clock time, total records ingested, and GitHub API call count.
        Supports `kill_after_n_records` to simulate process termination mid-backfill.

        Returns:
            Dict containing execution summary and performance metrics.
        """
        start_wall_time = time.perf_counter()
        start_api_calls = self.github_api.api_call_count

        # Ensure custom data type exists
        self.raw_ingestor.ensure_data_type()

        # Step 1: Discover repos if not explicitly supplied
        if repos is None:
            repos = self.github_api.discover_user_repos(github_identity)

        repos_total = len(repos)
        repos_covered: List[str] = []
        repos_no_activity: List[str] = []
        repos_active: List[str] = []

        total_records_ingested = 0
        remaining_kill_budget = kill_after_n_records
        interrupted = False

        # Step 2: Process each repo
        for repo in repos:
            if interrupted:
                break

            # 2a: Check if range is already covered by a completed checkpoint
            if self.checkpoint_manager.is_range_covered(
                repo=repo,
                github_identity=github_identity,
                start_time=start_time,
                end_time=end_time,
            ):
                repos_covered.append(repo)
                continue

            # 2b: Cheap existence pre-check
            precheck = self.github_api.check_repo_existence(
                repo=repo,
                github_identity=github_identity,
                since=start_time,
                until=end_time,
            )

            if not precheck.get("has_activity"):
                # Mark repo as completed with 0 items so future runs skip pre-check
                cp = Checkpoint(
                    repo=repo,
                    github_identity=github_identity,
                    start_time=start_time,
                    end_time=end_time,
                    status="completed",
                    items_processed=0,
                )
                self.checkpoint_manager.save_checkpoint(cp)
                repos_no_activity.append(repo)
                continue

            # 2c: Activity found — fetch items and ingest
            repos_active.append(repo)
            items = self.github_api.fetch_all_repo_activity(
                repo=repo,
                github_identity=github_identity,
                since=start_time,
                until=end_time,
            )

            ingest_limit = remaining_kill_budget if remaining_kill_budget is not None else None
            count, latest_cp = self.raw_ingestor.ingest_items(
                items=items,
                repo=repo,
                github_identity=github_identity,
                start_time=start_time,
                end_time=end_time,
                kill_after_n=ingest_limit,
            )

            total_records_ingested += count

            if remaining_kill_budget is not None:
                remaining_kill_budget -= count
                if remaining_kill_budget <= 0:
                    interrupted = True
                    break

        elapsed_wall_time = time.perf_counter() - start_wall_time
        total_api_calls = self.github_api.api_call_count - start_api_calls

        return {
            "github_identity": github_identity,
            "start_time": start_time,
            "end_time": end_time,
            "repos_total": repos_total,
            "repos_covered": repos_covered,
            "repos_no_activity": repos_no_activity,
            "repos_active": repos_active,
            "records_ingested": total_records_ingested,
            "wall_time_seconds": round(elapsed_wall_time, 4),
            "api_calls_made": total_api_calls,
            "interrupted": interrupted,
        }
