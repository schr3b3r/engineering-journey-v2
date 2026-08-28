"""End-to-end tests for provider-free narration by the running skill agent."""

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
from notability import NotabilityEngine
from rollups import RollupEngine


def _grounded_fixture(mock_fulcra_client):
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
    engine = RollupEngine(mock_fulcra_client)
    rollups_by_type = engine.generate_all_rollups(
        items, identity, save_to_fulcra=True
    )
    month_rollups = rollups_by_type["month"]
    signals = NotabilityEngine(mock_fulcra_client).compute_signals(month_rollups)
    NotabilityEngine(mock_fulcra_client).save_signals(signals)
    all_rollups = [
        rollup for period_rollups in rollups_by_type.values()
        for rollup in period_rollups
    ]
    return identity, all_rollups, signals


def test_running_agent_handoff_publish_end_to_end(mock_fulcra_client, tmp_path) -> None:
    identity, rollups, signals = _grounded_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(
        mock_fulcra_client,
        identity,
        rollups=rollups,
        signals=signals,
        exact_start_time="2024-01-01T00:00:00Z",
        exact_end_time="2024-02-29T23:59:59Z",
    )
    assert handoff["mode"] == "running_agent_narration"
    assert len(handoff["periods"]) == 2
    assert "Migrate analytics dashboard to React" in str(handoff)
    assert "idempotent payment capture" in str(handoff)

    # This deterministic double stands in for the LLM already executing the
    # skill. It receives grounded context directly; no provider adapter exists.
    narratives = {
        "2024-01-01": (
            "The dashboard-ui and metrics-api repositories converged on a React "
            "analytics dashboard, pairing the UI migration with its typed metrics endpoint."
        ),
        "2024-02-01": (
            "Focus shifted to payments, introducing idempotent capture with "
            "retry-safe transitions and idempotency keys."
        ),
    }
    response_periods = []
    for period in handoff["periods"]:
        response_periods.append(
            {
                "period_id": period["period_id"],
                "source_rollup_ids": period["rollup_ids"],
                "narrative": narratives[period["start_time"][:10]],
            }
        )
    response = {
        "context_id": handoff["context_id"],
        "overview": (
            "Across the period, the work moved from a coordinated React dashboard "
            "and metrics API initiative to reliability work in payment capture."
        ),
        "periods": response_periods,
    }
    published = publish_agent_narrative(
        mock_fulcra_client,
        handoff,
        response,
        output_dir=str(tmp_path),
        rollups=rollups,
        signals=signals,
        written_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert "React dashboard" in published.document
    assert "idempotent capture" in published.document
    assert "Limited deterministic fallback" not in published.document
    assert published.markdown_path.endswith("written_2026-08-28.md")
    assert published.fulcra_path in mock_fulcra_client.uploaded_files
    assert mock_fulcra_client.uploaded_files[published.fulcra_path]["data"].decode() == published.document


def test_agent_response_fails_closed_on_missing_or_cross_run_period(mock_fulcra_client) -> None:
    identity, rollups, signals = _grounded_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(
        mock_fulcra_client, identity, rollups=rollups, signals=signals
    )
    response = {
        "context_id": "wrong-run",
        "overview": "A sufficiently long overview that belongs to a different execution context.",
        "periods": [],
    }
    with pytest.raises(AgentNarrationValidationError, match="context_id"):
        validate_agent_response(handoff, response)


def test_canonical_agent_bridge_has_no_provider_or_credential_dependency() -> None:
    import agent_narration

    source = inspect.getsource(agent_narration)
    for forbidden in (
        "harness.providers", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY", "call_model(",
    ):
        assert forbidden not in source
