"""Tests for GitHub Backfill Checkpoint functionality."""

import pytest
from checkpoint import (
    CHECKPOINT_ANNOTATION_NAME,
    Checkpoint,
    CheckpointManager,
    FakeWorkItemProcessor,
    format_tag,
)
from fulcra_client import get_fulcra_client


def test_format_tag() -> None:
    """Verify format_tag truncates tags over 30 chars deterministically."""
    short_tag = "status:in_progress"
    assert format_tag(short_tag) == "status:in_progress"
    assert len(format_tag(short_tag)) <= 30

    long_tag = "repo:very-long-organization-name/very-long-repository-name"
    formatted = format_tag(long_tag)
    assert len(formatted) == 30
    assert formatted.startswith("repo:very-long-organiza_")

    # Identical input produces identical output
    assert format_tag(long_tag) == formatted


def test_checkpoint_serialization() -> None:
    """Verify Checkpoint serialization to/from JSON note dicts and Fulcra records."""
    cp = Checkpoint(
        repo="octocat/Hello-World",
        github_identity="octocat",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-12-31T23:59:59Z",
        status="in_progress",
        cursor="item_42",
        items_processed=42,
        extra_metadata={"branch": "main"},
    )

    note_dict = cp.to_note_dict()
    assert note_dict["repo"] == "octocat/Hello-World"
    assert note_dict["github_identity"] == "octocat"
    assert note_dict["cursor"] == "item_42"
    assert note_dict["items_processed"] == 42
    assert note_dict["extra"]["branch"] == "main"

    record_dict = {
        "id": "rec-uuid-123",
        "recorded_at": {
            "start_time": "2025-01-01T00:00:00Z",
            "end_time": "2025-12-31T23:59:59Z",
        },
        "note": cp.to_note_json(),
    }

    reconstructed = Checkpoint.from_record(record_dict)
    assert reconstructed.repo == "octocat/Hello-World"
    assert reconstructed.github_identity == "octocat"
    assert reconstructed.status == "in_progress"
    assert reconstructed.cursor == "item_42"
    assert reconstructed.items_processed == 42
    assert reconstructed.record_id == "rec-uuid-123"
    assert reconstructed.extra_metadata == {"branch": "main"}


def test_ensure_data_type(mock_fulcra_client) -> None:
    """Verify CheckpointManager registers the custom annotation type if not present."""
    mgr = CheckpointManager(mock_fulcra_client)
    type_info = mgr.ensure_data_type()

    assert type_info["name"] == CHECKPOINT_ANNOTATION_NAME
    assert type_info["annotation_type"] == "duration"

    # Subsequent call reuses cached type_info
    second_call = mgr.ensure_data_type()
    assert second_call["id"] == type_info["id"]


def test_save_and_get_checkpoint(mock_fulcra_client) -> None:
    """Verify saving a checkpoint records proper DurationAnnotation fields and retrieves it."""
    mgr = CheckpointManager(mock_fulcra_client)

    cp = Checkpoint(
        repo="acme/widget",
        github_identity="dev_user",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-06-30T23:59:59Z",
        status="in_progress",
        cursor="commit_sha_123",
        items_processed=10,
    )

    resp = mgr.save_checkpoint(cp)
    assert "upload_id" in resp

    checkpoints = mgr.get_checkpoints(repo="acme/widget", github_identity="dev_user")
    assert len(checkpoints) == 1
    loaded = checkpoints[0]
    assert loaded.repo == "acme/widget"
    assert loaded.github_identity == "dev_user"
    assert loaded.cursor == "commit_sha_123"
    assert loaded.items_processed == 10
    assert loaded.status == "in_progress"


def test_kill_and_resume_simulation(mock_fulcra_client) -> None:
    """Proves correct resume after process termination (no duplicate or skipped items)."""
    # 10 fake work items to backfill
    fake_items = [{"id": f"item_{i}", "data": f"content_{i}"} for i in range(1, 11)]
    processed_log = []

    repo = "test-org/resume-repo"
    identity = "test-agent"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-12-31T23:59:59Z"

    # --- PROCESS 1: Interrupted mid-way after processing 4 items ---
    mgr_p1 = CheckpointManager(mock_fulcra_client)
    proc_p1 = FakeWorkItemProcessor(mgr_p1, repo, identity, start_time, end_time)

    p1_processed_count, p1_cp = proc_p1.process_items(
        fake_items, kill_after_n=4, processed_log=processed_log
    )

    assert p1_processed_count == 4
    assert len(processed_log) == 4
    assert [item["id"] for item in processed_log] == ["item_1", "item_2", "item_3", "item_4"]
    assert p1_cp is not None
    assert p1_cp.cursor == "item_4"
    assert p1_cp.status == "in_progress"
    assert mgr_p1.is_range_covered(repo, identity, start_time, end_time) is False

    # --- PROCESS 2: Fresh process instance restarts backfill ---
    mgr_p2 = CheckpointManager(mock_fulcra_client)
    proc_p2 = FakeWorkItemProcessor(mgr_p2, repo, identity, start_time, end_time)

    p2_processed_count, p2_cp = proc_p2.process_items(
        fake_items, kill_after_n=None, processed_log=processed_log
    )

    assert p2_processed_count == 6
    assert len(processed_log) == 10
    # Confirm exact order with NO duplicates and NO skips
    assert [item["id"] for item in processed_log] == [
        "item_1",
        "item_2",
        "item_3",
        "item_4",
        "item_5",
        "item_6",
        "item_7",
        "item_8",
        "item_9",
        "item_10",
    ]
    assert p2_cp is not None
    assert p2_cp.cursor == "item_10"
    assert p2_cp.status == "completed"
    assert mgr_p2.is_range_covered(repo, identity, start_time, end_time) is True


def test_per_repo_isolation_and_coverage(mock_fulcra_client) -> None:
    """Verifies per-repo tag-based tracking isolates progress and coverage between repos."""
    mgr = CheckpointManager(mock_fulcra_client)
    identity = "multirepo-user"
    start_time = "2025-01-01T00:00:00Z"
    end_time = "2025-12-31T23:59:59Z"

    repo_a = "org/repo-alpha"
    repo_b = "org/repo-beta"

    items_a = [{"id": f"a_{i}"} for i in range(1, 4)]
    items_b = [{"id": f"b_{i}"} for i in range(1, 4)]

    # Complete repo_a backfill
    proc_a = FakeWorkItemProcessor(mgr, repo_a, identity, start_time, end_time)
    proc_a.process_items(items_a)

    assert mgr.is_range_covered(repo_a, identity, start_time, end_time) is True
    assert mgr.is_range_covered(repo_b, identity, start_time, end_time) is False

    # Partially backfill repo_b
    proc_b = FakeWorkItemProcessor(mgr, repo_b, identity, start_time, end_time)
    proc_b.process_items(items_b, kill_after_n=1)

    assert mgr.is_range_covered(repo_a, identity, start_time, end_time) is True
    assert mgr.is_range_covered(repo_b, identity, start_time, end_time) is False

    cp_a = mgr.get_latest_checkpoint(repo_a, identity)
    cp_b = mgr.get_latest_checkpoint(repo_b, identity)

    assert cp_a is not None and cp_a.status == "completed" and cp_a.cursor == "a_3"
    assert cp_b is not None and cp_b.status == "in_progress" and cp_b.cursor == "b_1"


def test_real_fulcra_integration() -> None:
    """Integration test against real Fulcra API (if authenticated)."""
    try:
        client = get_fulcra_client()
    except Exception as exc:
        pytest.skip(f"Skipping live Fulcra test: {exc}")

    mgr = CheckpointManager(client)
    type_info = mgr.ensure_data_type()
    assert type_info["name"] == CHECKPOINT_ANNOTATION_NAME

    test_repo = "test-fake-org/live-test-repo"
    test_identity = "test-live-user"

    cp = Checkpoint(
        repo=test_repo,
        github_identity=test_identity,
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-31T23:59:59Z",
        status="in_progress",
        cursor="live_item_1",
        items_processed=1,
    )

    save_resp = mgr.save_checkpoint(cp)
    assert "upload_id" in save_resp


def test_save_checkpoint_waits_for_completed_checkpoint_visibility(mock_fulcra_client) -> None:
    """Regression test for a real bug found via a live M5 backward/forward
    extension run: save_checkpoint() must poll until a just-saved
    'completed' checkpoint is actually visible via get_checkpoints()
    before returning, tolerating Fulcra's real eventual-consistency write
    path. Without this, a fast, sequential caller (e.g.
    get_uncovered_ranges() immediately after a prior extension step's
    completed checkpoint write) could see stale state and wrongly treat
    an already-covered sub-range as still uncovered, causing real
    duplicate ingestion -- exactly what a real run of
    real_m5_extension.py against a live Fulcra account originally
    surfaced (3 distinct items became 4 stored records)."""
    mgr = CheckpointManager(mock_fulcra_client)
    repo = "owner/visibility-test-repo"
    identity = "some-user"

    cp = Checkpoint(
        repo=repo,
        github_identity=identity,
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-12-31T23:59:59Z",
        status="completed",
        cursor="commit_final",
        items_processed=1,
    )
    mgr.save_checkpoint(cp)

    # Immediately after save_checkpoint() returns, the checkpoint must
    # already be visible to a fresh query -- this is the property that
    # was missing before the fix (against a real backend with real write
    # latency; the in-memory mock is instantaneous either way, so this
    # test exercises the polling/matching logic itself directly rather
    # than a real network race).
    visible = mgr._wait_for_checkpoint_visible(cp, max_attempts=1, delay_seconds=0)
    assert visible is True

    uncovered = mgr.get_uncovered_ranges(
        repo=repo,
        github_identity=identity,
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-12-31T23:59:59Z",
    )
    assert uncovered == [], "The just-saved completed checkpoint's own range must read back as fully covered"


def test_wait_for_checkpoint_visible_returns_false_without_raising_when_never_visible(mock_fulcra_client) -> None:
    """_wait_for_checkpoint_visible() must degrade gracefully (return
    False) rather than raise if a checkpoint genuinely never becomes
    visible within its bounded attempt budget -- the record write itself
    already happened for real, so this is a visibility-timing signal,
    not a data-loss condition, and must not crash a real backfill run."""
    mgr = CheckpointManager(mock_fulcra_client)
    phantom_cp = Checkpoint(
        repo="owner/never-saved-repo",
        github_identity="nobody",
        start_time="2020-01-01T00:00:00Z",
        end_time="2020-01-02T00:00:00Z",
        status="completed",
        cursor="phantom",
    )
    result = mgr._wait_for_checkpoint_visible(phantom_cp, max_attempts=2, delay_seconds=0)
    assert result is False
