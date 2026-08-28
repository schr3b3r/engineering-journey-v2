"""Run-level history coverage and explicit legacy migration regressions."""

import argparse
from unittest.mock import patch

from checkpoint import Checkpoint, CheckpointManager
from cli import handle_coverage_migration
from history_coverage import HistoryCoverage, HistoryCoverageManager


def test_hundreds_of_repositories_produce_one_timeline_duration(mock_fulcra_client) -> None:
    repositories = [f"org/repo-{index:03d}" for index in range(313)]
    manager = HistoryCoverageManager(mock_fulcra_client)
    assert manager.save(
        HistoryCoverage(
            run_id="run-large",
            github_identity="dev",
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-12-31T23:59:59Z",
            repositories=repositories,
            raw_record_count=1063,
        )
    )
    assert not manager.save(
        HistoryCoverage(
            run_id="run-large",
            github_identity="dev",
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-12-31T23:59:59Z",
            repositories=repositories,
        )
    )
    coverage = manager.get_coverages("dev", refresh=True)
    assert len(coverage) == 1
    assert coverage[0].repository_snapshot_hash
    assert coverage[0].raw_record_count == 1063
    assert len(mock_fulcra_client.duration_records) == 1

    # A Timeline query for any day in the covered year returns one meaningful
    # run bar—not one bar per repository.
    type_info = manager.ensure_type()
    timeline = mock_fulcra_client.duration_annotations(
        "2025-06-01T00:00:00Z",
        "2025-06-01T23:59:59Z",
        source=type_info["fulcra_source_id"],
    )
    assert len(timeline) == 1


def test_snapshot_membership_and_extensions_define_gaps(mock_fulcra_client) -> None:
    manager = HistoryCoverageManager(mock_fulcra_client)
    manager.save(
        HistoryCoverage(
            run_id="run-2024",
            github_identity="dev",
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-12-31T23:59:59Z",
            repositories=["org/existing", "org/empty"],
        )
    )
    assert manager.get_uncovered_ranges(
        "org/existing", "dev", "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z"
    ) == []
    # A repository discovered later was not in the old snapshot.
    assert manager.get_uncovered_ranges(
        "org/new", "dev", "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z"
    ) == [("2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z")]
    # Extension preserves only the intervals outside the completed run.
    assert manager.get_uncovered_ranges(
        "org/existing", "dev", "2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z"
    ) == [
        ("2023-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        ("2024-12-31T23:59:59Z", "2025-12-31T23:59:59Z"),
    ]


def _legacy_records(client) -> None:
    manager = CheckpointManager(client)
    for repo in ("org/one", "org/two"):
        manager.save_checkpoint(
            Checkpoint(
                repo=repo,
                github_identity="dev",
                start_time="2024-01-01T00:00:00Z",
                end_time="2024-12-31T23:59:59Z",
                status="completed",
            )
        )
    manager.save_checkpoint(
        Checkpoint(
            repo="org/two",
            github_identity="dev",
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-12-31T23:59:59Z",
            status="in_progress",
            cursor="sha",
        )
    )


def test_migration_is_idempotent_and_deletion_is_separately_confirmed(
    mock_fulcra_client, capsys
) -> None:
    _legacy_records(mock_fulcra_client)
    manager = HistoryCoverageManager(mock_fulcra_client)
    plan = manager.migration_plan()
    assert plan["legacy_completed_records"] == 2
    assert plan["legacy_progress_records"] == 1
    assert len(plan["cohorts"]) == 1
    assert plan["cohorts"][0]["repositories"] == ["org/one", "org/two"]
    assert plan["destructive_action_taken"] is False

    assert manager.migrate_legacy() == {"created": 1, "already_present": 0}
    assert manager.migrate_legacy() == {"created": 0, "already_present": 1}

    unsafe = argparse.Namespace(
        plan=False,
        migrate=False,
        delete_legacy_types=True,
        yes=True,
        confirm_delete_legacy_checkpoints=False,
    )
    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        assert handle_coverage_migration(unsafe) == 2
    assert "Nothing was deleted" in capsys.readouterr().err

    confirmed = argparse.Namespace(
        plan=False,
        migrate=False,
        delete_legacy_types=True,
        yes=True,
        confirm_delete_legacy_checkpoints=True,
    )
    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        assert handle_coverage_migration(confirmed) == 0
    live_names = {
        annotation["name"]
        for annotation in mock_fulcra_client.annotations
        if annotation.get("deleted_at") is None
    }
    assert "GitHub Backfill Coverage" not in live_names
    assert "GitHub Backfill Progress" not in live_names
    assert "GitHub History Coverage" in live_names
