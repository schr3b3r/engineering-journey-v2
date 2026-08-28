"""Multi-Repo Multi-Year GitHub Activity Backfill Engine.

Orchestrates multi-repo discovery, existence pre-checks, uniform daily-granularity
ingestion, durable checkpointing, interruption/resumability, backward/forward extension,
and volume/cost/performance metrics measurement for 1/2/3-year windows.
"""

from datetime import datetime, timedelta, timezone
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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

    def __init__(
        self,
        fulcra_client: Any,
        github_api: GitHubAPISpike,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.fulcra_client = fulcra_client
        self.github_api = github_api
        self.checkpoint_manager = CheckpointManager(fulcra_client)
        self.progress_callback = progress_callback or (lambda message: print(message, flush=True))
        self.raw_ingestor = RawActivityIngestor(
            fulcra_client, progress_callback=self.progress_callback
        )

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
        Handles backward/forward extension without reprocessing already-covered ranges.

        Returns:
            Dict containing execution summary and performance metrics.
        """
        start_wall_time = time.perf_counter()
        start_api_calls = self.github_api.api_call_count

        # Ensure custom data type exists
        self.raw_ingestor.ensure_data_type()

        # Step 1: Discover repos if not explicitly supplied
        if repos is None:
            self.progress_callback(f"[backfill] Discovering repositories for {github_identity}...")
            repos = self.github_api.discover_user_repos(github_identity)
        self.progress_callback(f"[backfill] Discovered {len(repos)} repositories. Processing...")

        repos_total = len(repos)
        repos_covered: List[str] = []
        repos_no_activity: List[str] = []
        repos_active: List[str] = []

        total_records_ingested = 0
        remaining_kill_budget = kill_after_n_records
        interrupted = False


        # Step 2: Process each repo
        for repo_index, repo in enumerate(repos, start=1):
            if interrupted:
                break

            # Every repository gets a contextual state line. This is
            # intentionally more conversational than a sparse heartbeat: a
            # user should always know which repo and operation is current.
            self.progress_callback(
                f"[backfill {repo_index}/{repos_total}] {repo}: checking existing coverage "
                f"({len(repos_active)} active repos, {total_records_ingested} records written)."
            )


            # 2a: Calculate sub-ranges not yet covered by completed checkpoints
            uncovered_ranges = self.checkpoint_manager.get_uncovered_ranges(
                repo=repo,
                github_identity=github_identity,
                start_time=start_time,
                end_time=end_time,
            )

            if not uncovered_ranges:
                repos_covered.append(repo)
                self.progress_callback(
                    f"[backfill {repo_index}/{repos_total}] {repo}: already covered; skipping."
                )
                continue

            for sub_start, sub_end in uncovered_ranges:
                if interrupted:
                    break

                self.progress_callback(
                    f"[backfill {repo_index}/{repos_total}] {repo}: prechecking "
                    f"{sub_start[:10]} to {sub_end[:10]}."
                )
                # 2b: Cheap existence pre-check for uncovered range
                precheck = self.github_api.check_repo_existence(
                    repo=repo,
                    github_identity=github_identity,
                    since=sub_start,
                    until=sub_end,
                )

                if not precheck.get("has_activity"):
                    # Mark subrange as completed with 0 items so future runs skip pre-check
                    cp = Checkpoint(
                        repo=repo,
                        github_identity=github_identity,
                        start_time=sub_start,
                        end_time=sub_end,
                        status="completed",
                        items_processed=0,
                    )
                    self.checkpoint_manager.save_checkpoint(cp)
                    if repo not in repos_no_activity and repo not in repos_active:
                        repos_no_activity.append(repo)
                    self.progress_callback(
                        f"[backfill {repo_index}/{repos_total}] {repo}: no activity; "
                        "saved durable zero-activity coverage."
                    )
                    continue

                if repo not in repos_active:
                    repos_active.append(repo)

                # 2c: Activity found — fetch items and ingest for subrange
                self.progress_callback(
                    f"[backfill {repo_index}/{repos_total}] {repo}: activity found; fetching details."
                )
                items = self.github_api.fetch_all_repo_activity(
                    repo=repo,
                    github_identity=github_identity,
                    since=sub_start,
                    until=sub_end,
                )

                ingest_limit = remaining_kill_budget if remaining_kill_budget is not None else None
                self.progress_callback(
                    f"[backfill {repo_index}/{repos_total}] {repo}: ingesting {len(items)} items to Fulcra."
                )
                count, latest_cp = self.raw_ingestor.ingest_items(
                    items=items,
                    repo=repo,
                    github_identity=github_identity,
                    start_time=sub_start,
                    end_time=sub_end,
                    kill_after_n=ingest_limit,
                )

                total_records_ingested += count
                self.progress_callback(
                    f"[backfill {repo_index}/{repos_total}] {repo}: ingested {count} new items; "
                    f"{total_records_ingested} total so far."
                )

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
