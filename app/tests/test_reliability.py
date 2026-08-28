"""Reliability and resume regressions for issue #13's raw-history pipeline."""

import argparse
import json
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

from cli import handle_backfill, handle_pipeline, handle_publish_agent_narrative
from conftest import MockFulcraClient
from github_spike import GitHubActivityItem
from pipeline_run import PipelineRunManager
from raw_ingestion import RawActivityIngestor
from reliability import retry_call
from narrative import upload_narrative_document


class AmbiguousRawWriteClient(MockFulcraClient):
    """Commit one raw record, then lose the response exactly once."""

    def __init__(self) -> None:
        super().__init__()
        self.ambiguous_failures = 0

    def record_data_type(self, data_type, records, api_version="v1alpha1"):
        is_raw = (
            data_type == "MomentAnnotation"
            and records
            and '"activity_type"' in (records[0].get("note") or "")
        )
        if is_raw and self.ambiguous_failures == 0:
            self.ambiguous_failures += 1
            super().record_data_type(data_type, records, api_version)
            raise URLError("response lost after commit")
        return super().record_data_type(data_type, records, api_version)


class TransientRawWriteClient(MockFulcraClient):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    def record_data_type(self, data_type, records, api_version="v1alpha1"):
        is_raw = (
            data_type == "MomentAnnotation"
            and records
            and '"activity_type"' in (records[0].get("note") or "")
        )
        if is_raw and self.failures_remaining:
            self.failures_remaining -= 1
            raise URLError("temporary DNS failure")
        return super().record_data_type(data_type, records, api_version)


class TransientUploadClient(MockFulcraClient):
    def __init__(self) -> None:
        super().__init__()
        self.upload_attempts = 0

    def upload_file(self, data, file_type, file_size, filepath):
        self.upload_attempts += 1
        if self.upload_attempts == 1:
            raise URLError("temporary upload DNS failure")
        return super().upload_file(data, file_type, file_size, filepath)


def _args(tmp_path: Path, *, command: str, resume: bool) -> argparse.Namespace:
    return argparse.Namespace(
        command=command,
        years=1.0,
        since="2025-01-01T00:00:00Z" if not resume else None,
        until="2025-03-01T00:00:00Z" if not resume else None,
        range="full",
        identity="resume-dev",
        repo=None,
        output=None,
        yes=True,
        device_code=False,
        dry_run=False,
        resume=resume,
        progress_jsonl=str(tmp_path / "progress.jsonl"),
        skip_real_summarization=False,
        provider=None,
        narration_mode="agent",
        handoff_output=str(tmp_path / "handoff.json"),
        kill_after_n_records=None,
    )


def test_retry_uses_bounded_backoff_and_structured_events() -> None:
    attempts = 0
    sleeps = []
    events = []

    def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise URLError("temporary DNS failure")
        return "ok"

    assert retry_call(
        operation,
        operation_name="test operation",
        attempts=4,
        base_delay=1,
        on_retry=events.append,
        sleep_fn=sleeps.append,
        random_fn=lambda: 0,
    ) == "ok"
    assert attempts == 3
    assert sleeps == [1, 2]
    assert [event["attempt"] for event in events] == [2, 3]
    assert all(event["event"] == "retry" for event in events)


def test_ambiguous_raw_write_is_requeried_not_duplicated() -> None:
    client = AmbiguousRawWriteClient()
    item = GitHubActivityItem(
        "commit", "acme/api", "dev", "sha-1", "2025-01-02T00:00:00Z",
        "Add endpoint", "https://github.com/acme/api/commit/sha-1",
    )
    count, checkpoint = RawActivityIngestor(client).ingest_items(
        [item], "acme/api", "dev", "2025-01-01T00:00:00Z",
        "2025-02-01T00:00:00Z",
    )
    assert count == 1
    assert checkpoint is not None and checkpoint.status == "completed"
    durable = RawActivityIngestor(client).get_raw_activities(
        repo="acme/api", github_identity="dev"
    )
    assert len(durable) == 1
    assert durable[0].item_id == "sha-1"
    assert client.ambiguous_failures == 1


def test_transient_raw_write_retries_and_reports_event() -> None:
    client = TransientRawWriteClient()
    events = []
    item = GitHubActivityItem(
        "commit", "acme/api", "dev", "sha-retry", "2025-01-03T00:00:00Z",
        "Retry-safe endpoint", "",
    )
    count, _ = RawActivityIngestor(client, event_callback=events.append).ingest_items(
        [item], "acme/api", "dev", "2025-01-01T00:00:00Z",
        "2025-02-01T00:00:00Z",
    )
    assert count == 1
    assert client.failures_remaining == 0
    retry_events = [event for event in events if event["event"] == "retry"]
    assert len(retry_events) == 1
    assert retry_events[0]["stage"] == "raw_ingestion"
    assert retry_events[0]["attempt"] == 2


def test_final_artifact_upload_retries_transient_failure() -> None:
    client = TransientUploadClient()
    events = []
    path = upload_narrative_document(
        client,
        "# Journey\n\nGrounded prose.",
        "dev",
        "2025-01-01T00:00:00Z",
        "2025-02-01T00:00:00Z",
        event_callback=events.append,
    )
    assert client.upload_attempts == 2
    assert path in client.uploaded_files
    assert [event["event"] for event in events] == ["retry"]


def test_raw_complete_resume_skips_github_reuses_window_and_publishes(
    tmp_path, mock_fulcra_client
) -> None:
    first = _args(tmp_path, command="backfill", resume=False)
    repositories = ["acme/api", "acme/web"]

    def activity(repo, github_identity, since, until):
        suffix = repo.split("/")[-1]
        return [
            GitHubActivityItem(
                "commit", repo, github_identity, f"sha-{suffix}",
                "2025-01-15T00:00:00Z", f"Build {suffix} capability",
                f"https://github.com/{repo}/commit/sha-{suffix}",
            )
        ]

    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        with patch("cli.get_github_auth_token", return_value="token"):
            with patch("cli.get_token_identity", return_value="resume-dev"):
                with patch("github_spike.GitHubAPISpike.discover_user_repos", return_value=repositories) as discover:
                    with patch("github_spike.GitHubAPISpike.check_repo_existence", return_value={"has_activity": True}):
                        with patch("github_spike.GitHubAPISpike.fetch_all_repo_activity", side_effect=activity):
                            assert handle_backfill(first) == 0
    discover.assert_called_once()
    original_window = (first.since, first.until)
    runs = PipelineRunManager(mock_fulcra_client).get_runs("resume-dev")
    assert runs[0].stage == "raw_complete"
    assert runs[0].repositories == repositories

    resumed = _args(tmp_path, command="pipeline", resume=True)
    resumed.years = 9.0  # Must be ignored in favor of the durable original window.
    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        with patch("cli.get_github_auth_token", return_value="token"):
            with patch("cli.get_token_identity", return_value="resume-dev"):
                with patch("github_spike.GitHubAPISpike.discover_user_repos", side_effect=AssertionError("must not rediscover")):
                    with patch("github_spike.GitHubAPISpike.check_repo_existence", side_effect=AssertionError("must not recheck repos")):
                        with patch("github_spike.GitHubAPISpike.fetch_all_repo_activity", side_effect=AssertionError("must not call GitHub")):
                            assert handle_pipeline(resumed) == 0
    assert (resumed.since, resumed.until) == original_window
    handoff = json.loads((tmp_path / "handoff.json").read_text(encoding="utf-8"))
    assert (handoff["metadata"]["start_time"], handoff["metadata"]["end_time"]) == original_window
    assert handoff["pipeline_run_id"] == runs[0].run_id

    raw_ids = [
        item["raw_record_id"]
        for chunk in handoff["chunks"]
        for item in chunk["evidence"]
    ]
    response = {
        "context_id": handoff["context_id"],
        "overview": "The period connected API and web capability work into one grounded engineering trajectory.",
        "sections": [
            {
                "section_id": "combined-work",
                "title": "January 2025 — Coordinated product capability",
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-02-01T00:00:00Z",
                "raw_record_ids": raw_ids,
                "narrative": "Work across the API and web repositories built the capabilities named in the underlying commits.",
            }
        ],
    }
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps(response), encoding="utf-8")
    publish_args = argparse.Namespace(
        command="publish-agent-narrative",
        handoff=str(tmp_path / "handoff.json"),
        response=str(response_path),
        output=str(tmp_path / "journey.md"),
        progress_jsonl=None,
        resume=False,
    )
    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        assert handle_publish_agent_narrative(publish_args) == 0
    assert (tmp_path / "journey.md").is_file()
    assert PipelineRunManager(mock_fulcra_client).get_runs("resume-dev")[0].stage == "published"

    events = [
        json.loads(line)
        for line in (tmp_path / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["event"] == "resume_plan" for event in events)
    assert any(event["event"] == "stage_skipped" for event in events)
    assert any(event["event"] == "pipeline_completed" for event in events)
    assert any("repos_completed" in event for event in events if event["stage"] == "backfill")
    assert all(
        {"event", "stage", "timestamp", "elapsed_seconds"}.issubset(event)
        for event in events
    )
    final = next(event for event in reversed(events) if event["event"] == "pipeline_completed")
    assert {"discovery", "backfill", "handoff", "publish"}.issubset(
        final["stage_summaries"]
    )


def test_interrupted_raw_stage_resumes_without_rediscovery_or_duplicates(
    tmp_path, mock_fulcra_client
) -> None:
    repositories = ["acme/one", "acme/two"]

    def activity(repo, github_identity, since, until):
        name = repo.rsplit("/", 1)[-1]
        return [
            GitHubActivityItem(
                "commit", repo, github_identity, f"sha-{name}",
                "2025-01-15T00:00:00Z", f"Build {name}", "",
            )
        ]

    interrupted = _args(tmp_path, command="backfill", resume=False)
    interrupted.kill_after_n_records = 1
    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        with patch("cli.get_github_auth_token", return_value="token"):
            with patch("cli.get_token_identity", return_value="resume-dev"):
                with patch("github_spike.GitHubAPISpike.discover_user_repos", return_value=repositories):
                    with patch("github_spike.GitHubAPISpike.check_repo_existence", return_value={"has_activity": True}):
                        with patch("github_spike.GitHubAPISpike.fetch_all_repo_activity", side_effect=activity):
                            assert handle_backfill(interrupted) == 130

    resumed = _args(tmp_path, command="backfill", resume=True)
    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        with patch("cli.get_github_auth_token", return_value="token"):
            with patch("cli.get_token_identity", return_value="resume-dev"):
                with patch("github_spike.GitHubAPISpike.discover_user_repos", side_effect=AssertionError("must reuse durable repos")):
                    with patch("github_spike.GitHubAPISpike.check_repo_existence", return_value={"has_activity": True}):
                        with patch("github_spike.GitHubAPISpike.fetch_all_repo_activity", side_effect=activity):
                            assert handle_backfill(resumed) == 0

    durable = RawActivityIngestor(mock_fulcra_client).get_raw_activities(
        github_identity="resume-dev",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-03-01T00:00:00Z",
    )
    assert sorted(item.item_id for item in durable) == ["sha-one", "sha-two"]
    assert len({item.record_id for item in durable}) == 2
    latest = PipelineRunManager(mock_fulcra_client).get_runs("resume-dev")[0]
    assert latest.stage == "raw_complete"
    assert (latest.start_time, latest.end_time) == (
        "2025-01-01T00:00:00Z", "2025-03-01T00:00:00Z"
    )
