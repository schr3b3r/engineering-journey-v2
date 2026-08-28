"""Relay-ready progress snapshot regressions."""

import json

from cli import main
from progress import format_progress_status, progress_snapshot


def _write(path, events, malformed=False):
    lines = [json.dumps(event) for event in events]
    if malformed:
        lines.append('{"partial":')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_progress_status_reports_counts_eta_and_heartbeat(tmp_path) -> None:
    path = tmp_path / "progress.jsonl"
    _write(
        path,
        [
            {
                "event": "progress",
                "stage": "backfill",
                "timestamp": "2026-08-28T15:00:00Z",
                "elapsed_seconds": 120,
                "repos_completed": 18,
                "repos_total": 313,
                "active_repos": 4,
                "records_written": 101,
                "rate_repos_per_second": 0.15,
                "eta_seconds": 1966,
                "current_repository": "acme/api",
            },
            {
                "event": "heartbeat",
                "stage": "github",
                "timestamp": "2026-08-28T15:00:10Z",
                "elapsed_seconds": 130,
                "message": "acme/api: inspecting PR details 10/40...",
            },
        ],
    )
    snapshot = progress_snapshot(str(path))
    assert snapshot["status"] == "running"
    assert snapshot["repos_completed"] == 18
    assert snapshot["percent_complete"] == 5.8
    status = format_progress_status(snapshot)
    assert "Still working" in status
    assert "18/313 (5.8%)" in status
    assert "records 101" in status
    assert "ETA 32m 46s" in status
    assert "acme/api" in status
    assert "inspecting PR details" in status


def test_progress_status_surfaces_retry_and_malformed_tail(tmp_path) -> None:
    path = tmp_path / "progress.jsonl"
    _write(
        path,
        [
            {
                "event": "retry",
                "stage": "raw_ingestion",
                "timestamp": "2026-08-28T15:00:00Z",
                "elapsed_seconds": 45,
                "operation": "write raw activity",
                "attempt": 2,
                "max_attempts": 5,
                "delay_seconds": 0.5,
                "error": "temporary DNS failure",
            }
        ],
        malformed=True,
    )
    snapshot = progress_snapshot(str(path))
    assert snapshot["malformed_lines"] == 1
    status = format_progress_status(snapshot)
    assert "last retry 2/5" in status
    assert "temporary DNS failure" in status


def test_progress_status_reports_final_stage_summary(tmp_path) -> None:
    path = tmp_path / "progress.jsonl"
    _write(
        path,
        [
            {
                "event": "pipeline_completed",
                "stage": "pipeline",
                "timestamp": "2026-08-28T15:10:00Z",
                "elapsed_seconds": 620,
                "stage_summaries": {
                    "discovery": {"duration_seconds": 20},
                    "backfill": {"duration_seconds": 500, "records_written": 1063},
                    "handoff": {"duration_seconds": 10},
                    "publish": {"duration_seconds": 2},
                },
            }
        ],
    )
    status = format_progress_status(progress_snapshot(str(path)))
    assert status.startswith("Complete after 10m 20s")
    assert "backfill 8m 20s" in status
    assert "publish 2s" in status


def test_progress_status_cli_handles_missing_file(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.jsonl"
    assert main(["progress-status", "--file", str(missing)]) == 0
    assert "Starting: no progress event" in capsys.readouterr().out
