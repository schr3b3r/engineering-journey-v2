"""Tests for GitHub Activity Raw ingestion and querying."""

import json
import time
import uuid
import pytest
from github_spike import GitHubActivityItem
from raw_ingestion import (
    RAW_ACTIVITY_ANNOTATION_NAME,
    RAW_ACTIVITY_TAG,
    RawActivityIngestor,
    activity_item_from_record,
    activity_item_to_note_dict,
)
from fulcra_client import get_fulcra_client


def test_ensure_data_type(mock_fulcra_client) -> None:
    """Verify RawActivityIngestor registers 'GitHub Activity Raw' custom type."""
    ingestor = RawActivityIngestor(mock_fulcra_client)
    type_info = ingestor.ensure_data_type()

    assert type_info["name"] == RAW_ACTIVITY_ANNOTATION_NAME
    assert type_info["annotation_type"] == "moment"

    # Subsequent call reuses cached type info
    second_call = ingestor.ensure_data_type()
    assert second_call["id"] == type_info["id"]


def test_activity_item_serialization() -> None:
    """Verify GitHubActivityItem serialization to/from JSON notes and Fulcra records."""
    item = GitHubActivityItem(
        activity_type="commit",
        repo="octocat/Hello-World",
        github_identity="octocat",
        item_id="c1234567890",
        event_timestamp="2025-03-15T14:30:00Z",
        title_or_summary="Fix issue in main module",
        url="https://github.com/octocat/Hello-World/commit/c1234567890",
        raw_payload={"sha": "c1234567890"},
    )

    note_dict = activity_item_to_note_dict(item)
    assert note_dict["activity_type"] == "commit"
    assert note_dict["repo"] == "octocat/Hello-World"
    assert note_dict["item_id"] == "c1234567890"

    rec = {
        "id": "rec-uuid-moment-1",
        "recorded_at": "2025-03-15T14:30:00Z",
        "note": json.dumps(note_dict),
    }

    reconstructed = activity_item_from_record(rec)
    assert reconstructed.activity_type == "commit"
    assert reconstructed.repo == "octocat/Hello-World"
    assert reconstructed.github_identity == "octocat"
    assert reconstructed.item_id == "c1234567890"
    assert reconstructed.event_timestamp == "2025-03-15T14:30:00Z"
    assert reconstructed.title_or_summary == "Fix issue in main module"


def test_raw_activity_ingestion_and_query(mock_fulcra_client) -> None:
    """Verify writing raw items produces records with real event time, tags, sources, and queries back."""
    ingestor = RawActivityIngestor(mock_fulcra_client)
    repo = "acme/widget"
    identity = "dev_user"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-12-31T23:59:59Z"

    items = [
        GitHubActivityItem(
            activity_type="commit",
            repo=repo,
            github_identity=identity,
            item_id="commit_1",
            event_timestamp="2025-02-10T08:00:00Z",
            title_or_summary="Initial commit",
            url="https://github.com/acme/widget/commit/1",
        ),
        GitHubActivityItem(
            activity_type="pr_open",
            repo=repo,
            github_identity=identity,
            item_id="pr_open_42",
            event_timestamp="2025-02-12T10:00:00Z",
            title_or_summary="Add new feature",
            url="https://github.com/acme/widget/pull/42",
        ),
        GitHubActivityItem(
            activity_type="pr_merge",
            repo=repo,
            github_identity=identity,
            item_id="pr_merge_42",
            event_timestamp="2025-02-12T15:00:00Z",
            title_or_summary="Merged: Add new feature",
            url="https://github.com/acme/widget/pull/42",
        ),
    ]

    count, cp = ingestor.ingest_items(items, repo, identity, start_time, end_time)
    assert count == 3
    assert cp is not None
    assert cp.status == "completed"

    # Query all raw activities back
    queried = ingestor.get_raw_activities(
        repo=repo,
        github_identity=identity,
        start_time=start_time,
        end_time=end_time,
    )

    assert len(queried) == 3
    assert [q.item_id for q in queried] == ["commit_1", "pr_open_42", "pr_merge_42"]
    # Check that real event times were preserved
    assert queried[0].event_timestamp == "2025-02-10T08:00:00Z"
    assert queried[1].event_timestamp == "2025-02-12T10:00:00Z"
    assert queried[2].event_timestamp == "2025-02-12T15:00:00Z"

    # Verify underlying record attributes
    raw_records = mock_fulcra_client.moment_records
    assert len(raw_records) == 3
    for rec in raw_records:
        assert rec["sources"][-1] == "com.fulcradynamics.cli"
        assert f"github:{repo}" in rec["sources"]
        assert rec["recorded_at"] in [
            "2025-02-10T08:00:00Z",
            "2025-02-12T10:00:00Z",
            "2025-02-12T15:00:00Z",
        ]


def test_checkpoint_integration_kill_and_resume(mock_fulcra_client) -> None:
    """Verify ingestion resume from checkpoint after mid-run interruption."""
    repo = "test-org/resume-repo"
    identity = "resuming-dev"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-12-31T23:59:59Z"

    items = [
        GitHubActivityItem(
            activity_type="commit",
            repo=repo,
            github_identity=identity,
            item_id=f"commit_{i}",
            event_timestamp=f"2025-01-0{i}T10:00:00Z",
            title_or_summary=f"Commit {i}",
            url=f"https://github.com/test-org/resume-repo/commit/{i}",
        )
        for i in range(1, 6)
    ]

    processed_log_p1 = []
    ingestor_p1 = RawActivityIngestor(mock_fulcra_client)

    # Process 1: interrupted after 2 items
    count1, cp1 = ingestor_p1.ingest_items(
        items,
        repo,
        identity,
        start_time,
        end_time,
        kill_after_n=2,
        processed_log=processed_log_p1,
    )

    assert count1 == 2
    assert len(processed_log_p1) == 2
    assert cp1.cursor == "commit_2"
    assert cp1.status == "in_progress"

    # Process 2: resume from checkpoint
    processed_log_p2 = []
    ingestor_p2 = RawActivityIngestor(mock_fulcra_client)

    count2, cp2 = ingestor_p2.ingest_items(
        items,
        repo,
        identity,
        start_time,
        end_time,
        kill_after_n=None,
        processed_log=processed_log_p2,
    )

    assert count2 == 3
    assert len(processed_log_p2) == 3
    assert [item.item_id for item in processed_log_p2] == ["commit_3", "commit_4", "commit_5"]
    assert cp2.cursor == "commit_5"
    assert cp2.status == "completed"

    # Verify querying back yields all 5 items
    queried = ingestor_p2.get_raw_activities(
        repo=repo, github_identity=identity, start_time=start_time, end_time=end_time
    )
    assert len(queried) == 5


def test_activity_type_filtering(mock_fulcra_client) -> None:
    """Verify querying raw activities with filtering by activity_type."""
    ingestor = RawActivityIngestor(mock_fulcra_client)
    repo = "acme/multi-type"
    identity = "filtering-user"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-12-31T23:59:59Z"

    items = [
        GitHubActivityItem(
            activity_type="commit",
            repo=repo,
            github_identity=identity,
            item_id="c_1",
            event_timestamp="2025-01-02T10:00:00Z",
            title_or_summary="Commit 1",
            url="",
        ),
        GitHubActivityItem(
            activity_type="pr_open",
            repo=repo,
            github_identity=identity,
            item_id="pr_1",
            event_timestamp="2025-01-03T10:00:00Z",
            title_or_summary="PR 1",
            url="",
        ),
        GitHubActivityItem(
            activity_type="issue_comment",
            repo=repo,
            github_identity=identity,
            item_id="comment_1",
            event_timestamp="2025-01-04T10:00:00Z",
            title_or_summary="Comment 1",
            url="",
        ),
    ]

    ingestor.ingest_items(items, repo, identity, start_time, end_time)

    commits = ingestor.get_raw_activities(
        repo=repo,
        github_identity=identity,
        activity_type="commit",
        start_time=start_time,
        end_time=end_time,
    )
    assert len(commits) == 1
    assert commits[0].item_id == "c_1"

    prs = ingestor.get_raw_activities(
        repo=repo,
        github_identity=identity,
        activity_type="pr_open",
        start_time=start_time,
        end_time=end_time,
    )
    assert len(prs) == 1
    assert prs[0].item_id == "pr_1"


def test_real_fulcra_integration() -> None:
    """Integration test against real Fulcra API (if authenticated)."""
    try:
        client = get_fulcra_client()
    except Exception as exc:
        pytest.skip(f"Skipping live Fulcra test: {exc}")

    ingestor = RawActivityIngestor(client)
    type_info = ingestor.ensure_data_type()
    assert type_info["name"] == RAW_ACTIVITY_ANNOTATION_NAME

    run_id = uuid.uuid4().hex[:8]
    test_repo = f"test-fake-org/live-{run_id}"
    test_identity = "test-live-user"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-01-31T23:59:59Z"

    items = [
        GitHubActivityItem(
            activity_type="commit",
            repo=test_repo,
            github_identity=test_identity,
            item_id=f"live_commit_{run_id}",
            event_timestamp="2025-01-15T12:00:00Z",
            title_or_summary="Live test commit",
            url=f"https://github.com/test-fake-org/live-{run_id}/commit/{run_id}",
        )
    ]

    count, cp = ingestor.ingest_items(items, test_repo, test_identity, start_time, end_time)
    assert count == 1
    assert cp is not None

    queried = []
    for _ in range(4):
        queried = ingestor.get_raw_activities(
            repo=test_repo,
            github_identity=test_identity,
            start_time=start_time,
            end_time=end_time,
        )
        if len(queried) >= 1:
            break
        time.sleep(0.5)

    assert len(queried) >= 1
    matched = [q for q in queried if q.item_id == f"live_commit_{run_id}"]
    assert len(matched) == 1
    assert matched[0].event_timestamp == "2025-01-15T12:00:00Z"
