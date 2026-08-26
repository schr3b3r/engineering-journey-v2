"""Tests for multi-repo multi-year backfill engine, existence pre-checks, metrics, and resumability."""

import os
import uuid
from typing import List
import pytest

from backfill import BackfillEngine, calculate_date_window
from checkpoint import Checkpoint, CheckpointManager
from github_spike import GitHubActivityItem, GitHubAPISpike
from raw_ingestion import RawActivityIngestor
from fulcra_client import get_fulcra_client


class MockGitHubAPISpike(GitHubAPISpike):
    """Mock GitHub API Spike that simulates multi-repo responses without live HTTP calls."""

    def __init__(self, repo_activity_map: dict) -> None:
        super().__init__(token="mock_token", base_url="https://api.github.com")
        self.repo_activity_map = repo_activity_map  # repo -> List[GitHubActivityItem]

    def discover_user_repos(self, github_identity: str, limit: int = 100) -> List[str]:
        self.api_call_count += 1
        return list(self.repo_activity_map.keys())[:limit]

    def check_repo_existence(self, repo: str, github_identity: str, since: str, until: str) -> dict:
        self.api_call_count += 1
        items = self.repo_activity_map.get(repo, [])
        matching = [item for item in items if since <= item.event_timestamp <= until]
        return {
            "repo": repo,
            "github_identity": github_identity,
            "since": since,
            "until": until,
            "has_activity": len(matching) > 0,
            "commit_count_sample": len(matching),
        }

    def fetch_all_repo_activity(
        self, repo: str, github_identity: str, since: str, until: str
    ) -> List[GitHubActivityItem]:
        self.api_call_count += 3  # Simulates 3 API requests (commits, PRs, comments)
        items = self.repo_activity_map.get(repo, [])
        matching = [item for item in items if since <= item.event_timestamp <= until]
        matching.sort(key=lambda x: x.event_timestamp)
        return matching


def test_calculate_date_window() -> None:
    """Verify calculate_date_window converts float years to ISO date bounds correctly."""
    end_date = "2025-01-01T00:00:00Z"
    start_1y, end_1y = calculate_date_window(years=1.0, end_date=end_date)
    assert end_1y == end_date
    assert start_1y.startswith("2024-01-0")

    start_2y, end_2y = calculate_date_window(years=2.0, end_date=end_date)
    assert end_2y == end_date
    assert start_2y.startswith("2023-01-0")

    start_3y, end_3y = calculate_date_window(years=3.0, end_date=end_date)
    assert end_3y == end_date
    assert start_3y.startswith("2022-01-0")


def test_backfill_engine_multi_repo(mock_fulcra_client) -> None:
    """Verify BackfillEngine discovers repos, pre-checks activity, ingests active repos, and tracks metrics."""
    identity = "dev_user"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-12-31T23:59:59Z"

    repo1 = "acme/repo-active-1"
    repo2 = "acme/repo-empty-2"
    repo3 = "acme/repo-active-3"

    items1 = [
        GitHubActivityItem(
            activity_type="commit",
            repo=repo1,
            github_identity=identity,
            item_id="c1",
            event_timestamp="2025-02-01T10:00:00Z",
            title_or_summary="Commit 1",
            url="https://github.com/acme/repo-active-1/commit/c1",
        ),
        GitHubActivityItem(
            activity_type="pr_open",
            repo=repo1,
            github_identity=identity,
            item_id="pr1",
            event_timestamp="2025-02-02T10:00:00Z",
            title_or_summary="PR 1",
            url="https://github.com/acme/repo-active-1/pull/1",
        ),
    ]

    items3 = [
        GitHubActivityItem(
            activity_type="issue_comment",
            repo=repo3,
            github_identity=identity,
            item_id="ic1",
            event_timestamp="2025-03-01T12:00:00Z",
            title_or_summary="Comment 1",
            url="https://github.com/acme/repo-active-3/issues/1#comment-1",
        ),
    ]

    mock_gh = MockGitHubAPISpike({
        repo1: items1,
        repo2: [],
        repo3: items3,
    })

    engine = BackfillEngine(mock_fulcra_client, mock_gh)
    metrics = engine.run_backfill(
        github_identity=identity,
        start_time=start_time,
        end_time=end_time,
    )

    assert metrics["repos_total"] == 3
    assert repo2 in metrics["repos_no_activity"]
    assert repo1 in metrics["repos_active"]
    assert repo3 in metrics["repos_active"]
    assert metrics["records_ingested"] == 3
    assert metrics["api_calls_made"] > 0
    assert metrics["wall_time_seconds"] >= 0.0
    assert metrics["interrupted"] is False

    # Verify queried raw activities from Fulcra
    ingestor = RawActivityIngestor(mock_fulcra_client)
    r1_items = ingestor.get_raw_activities(repo=repo1, github_identity=identity, start_time=start_time, end_time=end_time)
    assert len(r1_items) == 2

    r3_items = ingestor.get_raw_activities(repo=repo3, github_identity=identity, start_time=start_time, end_time=end_time)
    assert len(r3_items) == 1


def test_multi_repo_kill_and_resume(mock_fulcra_client) -> None:
    """Verify real multi-repo kill-mid-backfill and resume from a fresh session/process without duplicating items."""
    identity = "resumable_dev"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-12-31T23:59:59Z"

    repo1 = "org/repo-1"
    repo2 = "org/repo-2"

    items1 = [
        GitHubActivityItem(
            activity_type="commit",
            repo=repo1,
            github_identity=identity,
            item_id=f"r1_c_{i}",
            event_timestamp=f"2025-01-0{i}T10:00:00Z",
            title_or_summary=f"Repo 1 Commit {i}",
            url="",
        )
        for i in range(1, 4)
    ]

    items2 = [
        GitHubActivityItem(
            activity_type="commit",
            repo=repo2,
            github_identity=identity,
            item_id=f"r2_c_{i}",
            event_timestamp=f"2025-02-0{i}T10:00:00Z",
            title_or_summary=f"Repo 2 Commit {i}",
            url="",
        )
        for i in range(1, 4)
    ]

    mock_gh = MockGitHubAPISpike({repo1: items1, repo2: items2})

    # Process 1: Interrupt after 2 records
    engine1 = BackfillEngine(mock_fulcra_client, mock_gh)
    metrics1 = engine1.run_backfill(
        github_identity=identity,
        start_time=start_time,
        end_time=end_time,
        kill_after_n_records=2,
    )

    assert metrics1["interrupted"] is True
    assert metrics1["records_ingested"] == 2

    # Verify repo 1 is in progress
    cp_mgr = CheckpointManager(mock_fulcra_client)
    cp1 = cp_mgr.get_latest_checkpoint(repo1, identity, start_time, end_time)
    assert cp1 is not None
    assert cp1.status == "in_progress"
    assert cp1.cursor == "r1_c_2"

    # Process 2: Fresh session/engine instance resumes from checkpoint
    engine2 = BackfillEngine(mock_fulcra_client, mock_gh)
    metrics2 = engine2.run_backfill(
        github_identity=identity,
        start_time=start_time,
        end_time=end_time,
        kill_after_n_records=None,
    )

    assert metrics2["interrupted"] is False
    # Newly ingested in process 2 should be 1 item for repo 1 + 3 items for repo 2 = 4 items
    assert metrics2["records_ingested"] == 4

    # Check that repo 1 and repo 2 are completed
    cp1_final = cp_mgr.get_latest_checkpoint(repo1, identity, start_time, end_time)
    cp2_final = cp_mgr.get_latest_checkpoint(repo2, identity, start_time, end_time)
    assert cp1_final.status == "completed"
    assert cp2_final.status == "completed"

    # Verify all records exist in Fulcra without duplicates
    ingestor = RawActivityIngestor(mock_fulcra_client)
    r1_queried = ingestor.get_raw_activities(repo=repo1, github_identity=identity, start_time=start_time, end_time=end_time)
    assert len(r1_queried) == 3
    assert [q.item_id for q in r1_queried] == ["r1_c_1", "r1_c_2", "r1_c_3"]

    r2_queried = ingestor.get_raw_activities(repo=repo2, github_identity=identity, start_time=start_time, end_time=end_time)
    assert len(r2_queried) == 3
    assert [q.item_id for q in r2_queried] == ["r2_c_1", "r2_c_2", "r2_c_3"]


def test_multi_year_window_metrics_measurement(mock_fulcra_client) -> None:
    """Measure volume, cost, wall time, and API call count for 1, 2, and 3-year windows."""
    identity = "bench_user"
    ref_end = "2025-12-31T23:59:59Z"

    # Separate repos for each window to measure distinct runs
    for y in [1.0, 2.0, 3.0]:
        repo = f"bench/multi-year-repo-{int(y)}y"
        items = [
            GitHubActivityItem("commit", repo, identity, "c_2023", "2023-06-15T12:00:00Z", "2023 commit", ""),
            GitHubActivityItem("commit", repo, identity, "c_2024", "2024-06-15T12:00:00Z", "2024 commit", ""),
            GitHubActivityItem("commit", repo, identity, "c_2025", "2025-06-15T12:00:00Z", "2025 commit", ""),
        ]

        start_iso, end_iso = calculate_date_window(years=y, end_date=ref_end)
        mock_gh = MockGitHubAPISpike({repo: items})
        engine = BackfillEngine(mock_fulcra_client, mock_gh)

        metrics = engine.run_backfill(
            github_identity=identity,
            start_time=start_iso,
            end_time=end_iso,
        )

        assert "wall_time_seconds" in metrics
        assert "api_calls_made" in metrics
        assert "records_ingested" in metrics
        if y == 1.0:
            assert metrics["records_ingested"] == 1
        elif y == 2.0:
            assert metrics["records_ingested"] == 2
        elif y == 3.0:
            assert metrics["records_ingested"] == 3


def test_get_uncovered_ranges_calculation(mock_fulcra_client) -> None:
    """Verify CheckpointManager.get_uncovered_ranges accurately subtracts completed ranges."""
    cp_mgr = CheckpointManager(mock_fulcra_client)
    repo = "org/calc-repo"
    identity = "calc_dev"

    # Initially full range is uncovered
    uncovered = cp_mgr.get_uncovered_ranges(repo, identity, "2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z")
    assert len(uncovered) == 1
    assert uncovered[0] == ("2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z")

    # Save completed checkpoint for 2024
    cp2024 = Checkpoint(
        repo=repo,
        github_identity=identity,
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-12-31T23:59:59Z",
        status="completed",
    )
    cp_mgr.save_checkpoint(cp2024)

    # Now 2024 is covered, backward (2023) and forward (2025) are uncovered
    uncovered2 = cp_mgr.get_uncovered_ranges(repo, identity, "2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z")
    assert len(uncovered2) == 2
    assert uncovered2[0][0].startswith("2023-01-01")
    assert uncovered2[0][1].startswith("2024-01-01")
    assert uncovered2[1][0].startswith("2024-12-31")
    assert uncovered2[1][1].startswith("2025-12-31")


def test_backward_extension_no_duplication(mock_fulcra_client) -> None:
    """Verify extending backfill backward into the past fetches past items without re-fetching/duplicating present items."""
    identity = "ext_dev_back"
    repo = "ext/repo-back"

    item_2024 = GitHubActivityItem("commit", repo, identity, "c_2024", "2024-06-15T10:00:00Z", "2024 Commit", "")
    item_2023 = GitHubActivityItem("commit", repo, identity, "c_2023", "2023-06-15T10:00:00Z", "2023 Commit", "")

    mock_gh = MockGitHubAPISpike({repo: [item_2023, item_2024]})
    engine = BackfillEngine(mock_fulcra_client, mock_gh)

    # Initial run: Backfill 2024 only
    metrics_2024 = engine.run_backfill(identity, "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z", repos=[repo])
    assert metrics_2024["records_ingested"] == 1

    # Backward extension run: Backfill 2023 through 2024
    metrics_ext = engine.run_backfill(identity, "2023-01-01T00:00:00Z", "2024-12-31T23:59:59Z", repos=[repo])
    assert metrics_ext["records_ingested"] == 1  # Only 2023 item ingested!

    # Check queried activity in Fulcra for full range: should have 2 items with no duplicates
    ingestor = RawActivityIngestor(mock_fulcra_client)
    all_items = ingestor.get_raw_activities(repo=repo, github_identity=identity, start_time="2023-01-01T00:00:00Z", end_time="2024-12-31T23:59:59Z")
    assert len(all_items) == 2
    assert [i.item_id for i in all_items] == ["c_2023", "c_2024"]


def test_forward_extension_no_duplication(mock_fulcra_client) -> None:
    """Verify extending backfill forward into the future fetches future items without re-fetching/duplicating past items."""
    identity = "ext_dev_fwd"
    repo = "ext/repo-fwd"

    item_2024 = GitHubActivityItem("commit", repo, identity, "c_2024", "2024-06-15T10:00:00Z", "2024 Commit", "")
    item_2025 = GitHubActivityItem("commit", repo, identity, "c_2025", "2025-06-15T10:00:00Z", "2025 Commit", "")

    mock_gh = MockGitHubAPISpike({repo: [item_2024, item_2025]})
    engine = BackfillEngine(mock_fulcra_client, mock_gh)

    # Initial run: Backfill 2024 only
    metrics_2024 = engine.run_backfill(identity, "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z", repos=[repo])
    assert metrics_2024["records_ingested"] == 1

    # Forward extension run: Backfill 2024 through 2025
    metrics_ext = engine.run_backfill(identity, "2024-01-01T00:00:00Z", "2025-12-31T23:59:59Z", repos=[repo])
    assert metrics_ext["records_ingested"] == 1  # Only 2025 item ingested!

    # Check queried activity in Fulcra for full range
    ingestor = RawActivityIngestor(mock_fulcra_client)
    all_items = ingestor.get_raw_activities(repo=repo, github_identity=identity, start_time="2024-01-01T00:00:00Z", end_time="2025-12-31T23:59:59Z")
    assert len(all_items) == 2
    assert [i.item_id for i in all_items] == ["c_2024", "c_2025"]


def test_dual_extension_and_re_run_noop(mock_fulcra_client) -> None:
    """Verify dual backward/forward extension in a single call, followed by re-run noop."""
    identity = "ext_dev_dual"
    repo = "ext/repo-dual"

    items = [
        GitHubActivityItem("commit", repo, identity, "c_2023", "2023-06-15T10:00:00Z", "2023 Commit", ""),
        GitHubActivityItem("commit", repo, identity, "c_2024", "2024-06-15T10:00:00Z", "2024 Commit", ""),
        GitHubActivityItem("commit", repo, identity, "c_2025", "2025-06-15T10:00:00Z", "2025 Commit", ""),
    ]

    mock_gh = MockGitHubAPISpike({repo: items})
    engine = BackfillEngine(mock_fulcra_client, mock_gh)

    # Initial run: 2024
    m1 = engine.run_backfill(identity, "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z", repos=[repo])
    assert m1["records_ingested"] == 1

    # Dual extension: 2023..2025
    m2 = engine.run_backfill(identity, "2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z", repos=[repo])
    assert m2["records_ingested"] == 2  # 2023 and 2025 items ingested!

    # Re-run full range: 2023..2025 (should be complete no-op)
    calls_before = mock_gh.api_call_count
    m3 = engine.run_backfill(identity, "2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z", repos=[repo])
    assert m3["records_ingested"] == 0
    assert repo in m3["repos_covered"]
    assert mock_gh.api_call_count == calls_before  # No API calls made!


def test_real_fulcra_multi_repo_integration() -> None:
    """Integration test against real Fulcra API (if authenticated)."""
    if os.environ.get("RUN_LIVE_TESTS") != "1":
        pytest.skip("Skipping live network test by default. Set RUN_LIVE_TESTS=1 to run.")

    try:
        client = get_fulcra_client()
    except Exception as exc:
        pytest.skip(f"Skipping live Fulcra test: {exc}")

    run_id = uuid.uuid4().hex[:8]
    identity = "live-multi-user"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-01-31T23:59:59Z"

    repo1 = f"live-org/repo-1-{run_id}"
    repo2 = f"live-org/repo-2-{run_id}"

    items1 = [
        GitHubActivityItem("commit", repo1, identity, f"live_c1_{run_id}", "2025-01-10T12:00:00Z", "Live commit 1", "")
    ]
    items2 = [
        GitHubActivityItem("pr_open", repo2, identity, f"live_pr1_{run_id}", "2025-01-15T12:00:00Z", "Live PR 1", "")
    ]

    mock_gh = MockGitHubAPISpike({repo1: items1, repo2: items2})
    engine = BackfillEngine(client, mock_gh)

    metrics = engine.run_backfill(
        github_identity=identity,
        start_time=start_time,
        end_time=end_time,
    )

    assert metrics["records_ingested"] == 2
    assert metrics["repos_total"] == 2
    assert len(metrics["repos_active"]) == 2
