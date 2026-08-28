"""End-to-end tests for raw-history, provider-free ephemeral storytelling."""

from datetime import datetime, timezone
import inspect

import pytest

from agent_narration import (
    AgentNarrationValidationError,
    prepare_agent_handoff,
    publish_agent_narrative,
    validate_agent_response,
)
from github_spike import GitHubActivityItem
from raw_ingestion import RawActivityIngestor


def _raw_fixture(mock_fulcra_client):
    identity = "agent-author"
    items = [
        GitHubActivityItem(
            "pull_request_merged", "acme/dashboard-ui", identity, "ui-1",
            "2024-01-10T10:00:00Z", "Migrate analytics dashboard to React", "",
            raw_payload={"body": "Replace the legacy view and consume metrics-api."},
        ),
        GitHubActivityItem(
            "pull_request_merged", "acme/metrics-api", identity, "api-1",
            "2024-01-12T10:00:00Z", "Add typed dashboard metrics endpoint", "",
            raw_payload={"body": "Supply trend data to dashboard-ui."},
        ),
        GitHubActivityItem(
            "pull_request_merged", "acme/payments", identity, "pay-1",
            "2024-02-15T10:00:00Z", "Introduce idempotent payment capture", "",
            raw_payload={"body": "Add retry-safe transitions and idempotency keys."},
        ),
    ]
    ingestor = RawActivityIngestor(mock_fulcra_client)
    ingestor.ingest_items(
        items,
        repo="",  # each item retains its own repository
        github_identity=identity,
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-02-29T23:59:59Z",
    )
    durable = ingestor.get_raw_activities(
        github_identity=identity,
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-02-29T23:59:59Z",
    )
    assert all(item.record_id for item in durable)
    return identity, durable


def test_running_agent_raw_handoff_publish_end_to_end(mock_fulcra_client, tmp_path) -> None:
    identity, durable_raw = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(
        mock_fulcra_client,
        identity,
        exact_start_time="2024-01-01T00:00:00Z",
        exact_end_time="2024-02-29T23:59:59Z",
    )
    assert handoff["mode"] == "running_agent_ephemeral_storytelling"
    assert handoff["metadata"]["raw_record_count"] == 3
    assert "Migrate analytics dashboard to React" in str(handoff)
    assert "idempotent payment capture" in str(handoff)
    assert all(
        item["raw_record_id"]
        for chunk in handoff["chunks"] for item in chunk["evidence"]
    )

    by_item = {item.item_id: item.record_id for item in durable_raw}
    response = {
        "context_id": handoff["context_id"],
        "overview": (
            "Across the period, the work moved from a coordinated React dashboard "
            "and metrics API initiative to reliability work in payment capture."
        ),
        "sections": [
            {
                "section_id": "dashboard-initiative",
                "title": "January 2024 — A coordinated analytics dashboard",
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
                "raw_record_ids": [by_item["ui-1"], by_item["api-1"]],
                "narrative": (
                    "The dashboard-ui and metrics-api repositories converged on a "
                    "React analytics dashboard, pairing the UI migration with its "
                    "typed metrics endpoint."
                ),
            },
            {
                "section_id": "payment-reliability",
                "title": "February 2024 — Reliable payment capture",
                "start_time": "2024-02-01T00:00:00Z",
                "end_time": "2024-02-29T23:59:59Z",
                "raw_record_ids": [by_item["pay-1"]],
                "narrative": (
                    "Focus shifted to payments, introducing idempotent capture with "
                    "retry-safe transitions and idempotency keys."
                ),
            },
        ],
    }
    record_counts_before = (
        len(mock_fulcra_client.duration_records),
        len(mock_fulcra_client.moment_records),
        len(mock_fulcra_client.numeric_records),
    )
    published = publish_agent_narrative(
        mock_fulcra_client,
        handoff,
        response,
        output_dir=str(tmp_path),
        written_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert "React analytics dashboard" in published.document
    assert "idempotent capture" in published.document
    assert "Activity Count" not in published.document
    assert "Notability" not in published.document
    assert "Raw Fulcra Record ID" in published.document
    assert published.markdown_path.endswith("written_2026-08-28.md")
    assert published.fulcra_path in mock_fulcra_client.uploaded_files
    assert mock_fulcra_client.uploaded_files[published.fulcra_path]["data"].decode() == published.document
    # Publishing is ephemeral interpretation + file artifact only: no derived
    # annotation, rollup, signal, or summary record was added.
    assert record_counts_before == (
        len(mock_fulcra_client.duration_records),
        len(mock_fulcra_client.moment_records),
        len(mock_fulcra_client.numeric_records),
    )
    annotation_names = {item["name"] for item in mock_fulcra_client.annotations}
    assert "Activity Rollup" not in annotation_names
    assert "Notability Signal" not in annotation_names


def test_agent_response_fails_closed_on_unknown_raw_record(mock_fulcra_client) -> None:
    identity, _ = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = {
        "context_id": handoff["context_id"],
        "overview": "A sufficiently long grounded overview of the engineering trajectory.",
        "sections": [
            {
                "section_id": "bad-source",
                "title": "Unknown evidence",
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T00:00:00Z",
                "raw_record_ids": ["not-a-real-fulcra-record"],
                "narrative": "This narrative is long enough but cites evidence that does not exist.",
            }
        ],
    }
    with pytest.raises(AgentNarrationValidationError, match="unknown raw record"):
        validate_agent_response(handoff, response)


def test_canonical_agent_bridge_has_no_derived_or_provider_dependency() -> None:
    import agent_narration

    source = inspect.getsource(agent_narration)
    for forbidden in (
        "harness.providers", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY", "call_model(", "RollupEngine", "NotabilityEngine",
        "save_rollups", "save_signals", "summary_text",
    ):
        assert forbidden not in source


def test_large_raw_range_is_adaptively_chunked_without_losing_provenance(
    mock_fulcra_client,
) -> None:
    identity = "high-volume-dev"
    raw_items = [
        GitHubActivityItem(
            "commit", f"acme/repo-{index % 4}", identity, f"sha-{index}",
            f"2024-01-{(index % 28) + 1:02d}T{index % 24:02d}:00:00Z",
            f"Commit {index}", "", record_id=f"raw-uuid-{index}",
        )
        for index in range(181)
    ]
    handoff = prepare_agent_handoff(
        mock_fulcra_client,
        identity,
        raw_items=raw_items,
        exact_start_time="2024-01-01T00:00:00Z",
        exact_end_time="2024-01-31T23:59:59Z",
    )
    assert len(handoff["chunks"]) == 3
    assert all(len(chunk["evidence"]) <= 80 for chunk in handoff["chunks"])
    raw_ids = [
        item["raw_record_id"]
        for chunk in handoff["chunks"]
        for item in chunk["evidence"]
    ]
    assert len(raw_ids) == 181
    assert len(set(raw_ids)) == 181
