"""End-to-end tests for raw-history, provider-free ephemeral storytelling."""

from datetime import datetime, timezone
import inspect
import json

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
        "narrative_plan": {
            "trajectory_thesis": (
                "Work moved from a coordinated dashboard/API integration to "
                "payment reliability."
            ),
            "dominant_arcs": [
                {
                    "arc_id": "dashboard-metrics-integration",
                    "label": "React dashboard paired with a typed metrics API",
                    "start_time": "2024-01-01T00:00:00Z",
                    "end_time": "2024-01-31T23:59:59Z",
                    "raw_record_ids": [by_item["ui-1"], by_item["api-1"]],
                    "repositories": ["acme/dashboard-ui", "acme/metrics-api"],
                },
                {
                    "arc_id": "payment-capture-reliability",
                    "label": "Idempotent payment capture",
                    "start_time": "2024-02-01T00:00:00Z",
                    "end_time": "2024-02-29T23:59:59Z",
                    "raw_record_ids": [by_item["pay-1"]],
                    "repositories": ["acme/payments"],
                },
            ],
            "turning_points": [
                {
                    "description": (
                        "Focus shifted from the dashboard/metrics integration to "
                        "payment capture reliability."
                    ),
                    "raw_record_ids": [by_item["pay-1"]],
                }
            ],
            "culmination": None,
        },
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
    # The main narrative must stay readable regardless of evidence volume:
    # no per-record raw evidence table, only a short pointer to the
    # companion sources file plus the small per-section evidence table.
    assert "Raw Fulcra Record ID |" not in published.document
    assert "Full Raw Evidence" in published.document
    assert published.sources_filename in published.document
    # The full raw-record table lives in the separate sources artifact.
    assert "Raw Fulcra Record ID |" in published.sources_document
    assert by_item["ui-1"] in published.sources_document
    assert by_item["api-1"] in published.sources_document
    assert by_item["pay-1"] in published.sources_document
    assert published.markdown_path.endswith("written_2026-08-28.md")
    assert published.sources_markdown_path.endswith("written_2026-08-28_sources.md")
    assert published.fulcra_path in mock_fulcra_client.uploaded_files
    assert mock_fulcra_client.uploaded_files[published.fulcra_path]["data"].decode() == published.document
    assert published.sources_fulcra_path in mock_fulcra_client.uploaded_files
    assert (
        mock_fulcra_client.uploaded_files[published.sources_fulcra_path]["data"].decode()
        == published.sources_document
    )
    assert published.sources_fulcra_path != published.fulcra_path
    # Files should be published as siblings in the same identity/year folder.
    assert (
        published.sources_fulcra_path.rsplit("/", 1)[0]
        == published.fulcra_path.rsplit("/", 1)[0]
    )
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


def test_main_narrative_stays_bounded_regardless_of_evidence_volume(
    mock_fulcra_client, tmp_path
) -> None:
    """Regression for: 'don't include such a huge table in the output'.

    A large raw-evidence corpus must not inflate the main narrative
    artifact -- the full per-record table belongs only in the companion
    sources file, which is allowed to scale with evidence volume.
    """
    identity = "volume-dev"
    raw_items = [
        GitHubActivityItem(
            "commit", f"acme/repo-{index % 5}", identity, f"sha-{index}",
            f"2024-{(index % 12) + 1:02d}-{(index % 28) + 1:02d}T00:00:00Z",
            f"Commit {index}", "", record_id=f"raw-uuid-{index}",
        )
        for index in range(400)
    ]
    handoff = prepare_agent_handoff(
        mock_fulcra_client,
        identity,
        raw_items=raw_items,
        exact_start_time="2024-01-01T00:00:00Z",
        exact_end_time="2024-12-31T23:59:59Z",
    )
    all_raw_ids = [
        item["raw_record_id"] for chunk in handoff["chunks"] for item in chunk["evidence"]
    ]
    # A realistic narrative compresses routine stretches into a handful of
    # sections, each citing a representative sample of supporting records
    # (not literally every one of the 400 raw records) -- split evenly
    # across 4 quarterly sections with a few representative citations each.
    quarter_size = len(all_raw_ids) // 4
    quarters = [
        all_raw_ids[i * quarter_size : (i + 1) * quarter_size if i < 3 else len(all_raw_ids)]
        for i in range(4)
    ]
    representative_quarters = [quarter[:5] for quarter in quarters]
    response = {
        "context_id": handoff["context_id"],
        "narrative_plan": _minimal_plan(
            "A high volume of commits was distributed across five repositories "
            "over the full year.",
            all_raw_ids[:5],
            sorted({item["repository"] for chunk in handoff["chunks"] for item in chunk["evidence"]}),
            "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z",
        ),
        "overview": "A high volume of commits was distributed across five repositories over the full year, compressed into quarterly stretches below.",
        "sections": [
            {
                "section_id": f"q{index + 1}-2024",
                "title": f"Q{index + 1} 2024 — Sustained commit activity",
                "start_time": f"2024-{index * 3 + 1:02d}-01T00:00:00Z",
                "end_time": f"2024-{index * 3 + 3:02d}-28T23:59:59Z",
                "raw_record_ids": quarter_ids,
                "narrative": f"Commits landed steadily across five repositories in Q{index + 1}, per the evidenced raw activity.",
            }
            for index, quarter_ids in enumerate(representative_quarters)
        ],
    }
    published = publish_agent_narrative(
        mock_fulcra_client,
        handoff,
        response,
        output_dir=str(tmp_path),
        written_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    # The main narrative must never contain the full per-record raw evidence
    # table, regardless of how many raw records back the story.
    assert "Raw Fulcra Record ID |" not in published.document
    # Records beyond the small per-section citation sample are absent from
    # the main narrative even though they remain valid known evidence.
    uncited_id = quarters[3][-1]
    assert uncited_id not in published.document
    # It stays dramatically smaller than the full raw evidence would make it.
    assert len(published.document) < len(published.sources_document) / 4
    # The sources file legitimately grows with evidence volume and contains
    # every cited raw record, including ones the narrative did not
    # individually cite.
    assert uncited_id in published.sources_document
    for raw_id in ("raw-uuid-0", "raw-uuid-200", "raw-uuid-399"):
        assert raw_id in published.sources_document


def test_agent_response_fails_closed_on_unknown_raw_record(mock_fulcra_client) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    known_id = durable[0].record_id
    response = {
        "context_id": handoff["context_id"],
        "narrative_plan": {
            "trajectory_thesis": "Work progressed across the evidenced repositories.",
            "dominant_arcs": [
                {
                    "arc_id": "only-arc",
                    "label": "Evidenced work",
                    "start_time": "2024-01-01T00:00:00Z",
                    "end_time": "2024-01-31T00:00:00Z",
                    "raw_record_ids": [known_id],
                    "repositories": [durable[0].repo],
                }
            ],
            "turning_points": [],
            "culmination": None,
        },
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


# --- Issue #18: Overview editorial synthesis without weakening grounding ---


def _minimal_plan(thesis, arc_raw_ids, repositories, arc_start, arc_end):
    return {
        "trajectory_thesis": thesis,
        "dominant_arcs": [
            {
                "arc_id": "arc-1",
                "label": "Evidenced technical arc",
                "start_time": arc_start,
                "end_time": arc_end,
                "raw_record_ids": arc_raw_ids,
                "repositories": repositories,
            }
        ],
        "turning_points": [],
        "culmination": None,
    }


def _base_valid_response(handoff, durable):
    by_item = {item.item_id: item.record_id for item in durable}
    all_ids = list(by_item.values())
    return {
        "context_id": handoff["context_id"],
        "narrative_plan": _minimal_plan(
            "Work progressed across the evidenced repositories over the period.",
            all_ids,
            sorted({item.repo for item in durable}),
            handoff["metadata"]["start_time"],
            handoff["metadata"]["end_time"],
        ),
        "overview": "A sufficiently long and grounded overview of the trajectory evidenced above.",
        "sections": [
            {
                "section_id": "only-section",
                "title": "The evidenced period",
                "start_time": handoff["metadata"]["start_time"],
                "end_time": handoff["metadata"]["end_time"],
                "raw_record_ids": all_ids,
                "narrative": "Grounded technical prose describing the evidenced raw activity.",
            }
        ],
    }


def test_narrative_plan_is_required_and_fails_closed_when_missing(mock_fulcra_client) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    del response["narrative_plan"]
    with pytest.raises(AgentNarrationValidationError, match="narrative_plan"):
        validate_agent_response(handoff, response)


@pytest.mark.parametrize(
    "phrase",
    ["spearheaded", "extraordinary", "architecting", "robust", "secure",
     "high-impact", "production-grade", "from the ground up", "rare combination"],
)
def test_overview_rejects_unsupported_evaluative_language(mock_fulcra_client, phrase) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    response["overview"] = f"This period involved {phrase} work across repositories."
    with pytest.raises(AgentNarrationValidationError, match="unsupported evaluative"):
        validate_agent_response(handoff, response)


def test_led_only_matches_whole_word_not_substring(mock_fulcra_client) -> None:
    """'led' must not false-positive on words like 'enabled' or 'scheduled'."""
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    response["overview"] = (
        "This period enabled a scheduled migration across the evidenced repositories, "
        "as described in the underlying commits and pull requests above."
    )
    # Should NOT raise -- "enabled"/"scheduled" contain "led" as a substring only.
    validate_agent_response(handoff, response)

    response["sections"][0]["narrative"] = "The team led the migration effort directly."
    with pytest.raises(AgentNarrationValidationError, match="unsupported evaluative"):
        validate_agent_response(handoff, response)


def test_section_narrative_rejects_unsupported_evaluative_language(mock_fulcra_client) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    response["sections"][0]["narrative"] = (
        "This section describes robust, production-grade work across the evidenced repos."
    )
    with pytest.raises(AgentNarrationValidationError, match="unsupported evaluative"):
        validate_agent_response(handoff, response)


def test_narrative_plan_dominant_arcs_are_capped(mock_fulcra_client) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    by_item = {item.item_id: item.record_id for item in durable}
    one_arc = response["narrative_plan"]["dominant_arcs"][0]
    response["narrative_plan"]["dominant_arcs"] = [
        {**one_arc, "arc_id": f"arc-{index}"} for index in range(4)
    ]
    with pytest.raises(AgentNarrationValidationError, match="dominant arcs"):
        validate_agent_response(handoff, response)


def test_narrative_plan_arc_needs_known_raw_record_ids(mock_fulcra_client) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    response["narrative_plan"]["dominant_arcs"][0]["raw_record_ids"] = ["not-a-real-record"]
    with pytest.raises(AgentNarrationValidationError, match="unknown raw_record_ids"):
        validate_agent_response(handoff, response)


def test_narrative_plan_thesis_required_and_grounded(mock_fulcra_client) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    response["narrative_plan"]["trajectory_thesis"] = "Too short."
    with pytest.raises(AgentNarrationValidationError, match="trajectory_thesis"):
        validate_agent_response(handoff, response)


def test_culmination_is_optional_but_validated_when_present(mock_fulcra_client) -> None:
    identity, durable = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(mock_fulcra_client, identity)
    response = _base_valid_response(handoff, durable)
    # Present with an unknown raw ID should fail.
    response["narrative_plan"]["culmination"] = {
        "description": "A culminating integration across the repositories.",
        "raw_record_ids": ["totally-unknown"],
    }
    with pytest.raises(AgentNarrationValidationError, match="culmination"):
        validate_agent_response(handoff, response)
    # None/absent should be fine (already covered by the base fixture).
    response["narrative_plan"]["culmination"] = None
    validate_agent_response(handoff, response)


def test_overview_brief_recommends_narrower_scope_for_short_range(mock_fulcra_client) -> None:
    identity, _ = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(
        mock_fulcra_client,
        identity,
        exact_start_time="2024-01-01T00:00:00Z",
        exact_end_time="2024-02-29T23:59:59Z",
    )
    brief = handoff["overview_brief"]
    assert brief["recommended_dominant_arcs"] == "1"
    assert "focused interval" in brief["scope_guidance"]


def test_overview_brief_recommends_broader_scope_for_multi_year_range(mock_fulcra_client) -> None:
    identity, _ = _raw_fixture(mock_fulcra_client)
    handoff = prepare_agent_handoff(
        mock_fulcra_client,
        identity,
        exact_start_time="2021-01-01T00:00:00Z",
        exact_end_time="2024-02-29T23:59:59Z",
    )
    brief = handoff["overview_brief"]
    assert brief["recommended_dominant_arcs"] == "1-3"
    assert "multi-year" in brief["scope_guidance"]


def test_evaluation_fixture_strong_arc_and_tempting_gap_are_both_present(
    mock_fulcra_client,
) -> None:
    """Regression fixture requested by issue #18's Evaluation section.

    Contains BOTH:
    - enough related evidence across two repos to support a real cross-repo
      arc with an explicit dependency relationship stated in body text; and
    - a multi-month silent gap plus an unrelated same-topic-named repo whose
      only connection is superficial naming -- the narrator must not
      embellish either the gap's cause or a cross-repo relationship that
      the evidence does not state.

    This test exercises validate_agent_response with prose that stays
    grounded (must pass) and prose that embellishes the gap or invents a
    naming-based connection (must fail via the forbidden-phrase or
    unknown-evidence checks the narrator is required to satisfy).
    """
    identity = "story-dev"
    items = [
        GitHubActivityItem(
            "pull_request_merged", "acme/auth-service", identity, "auth-1",
            "2024-01-05T09:00:00Z", "Add OAuth token refresh endpoint", "",
            raw_payload={"body": "Introduces a refresh-token flow consumed by web-portal's login screen."},
        ),
        GitHubActivityItem(
            "pull_request_merged", "acme/web-portal", identity, "portal-1",
            "2024-01-08T09:00:00Z", "Wire login screen to new refresh-token flow", "",
            raw_payload={"body": "Consumes the auth-service refresh endpoint added this week."},
        ),
        # A four-month silent gap follows (Feb-May): no evidence at all.
        GitHubActivityItem(
            "commit", "acme/auth-analytics", identity, "analytics-1",
            "2024-06-01T09:00:00Z", "Add login funnel dashboard", "",
            raw_payload={"body": "Unrelated internal analytics dashboard; no connection to auth-service documented."},
        ),
    ]
    ingestor = RawActivityIngestor(mock_fulcra_client)
    ingestor.ingest_items(
        items, repo="", github_identity=identity,
        start_time="2024-01-01T00:00:00Z", end_time="2024-06-30T23:59:59Z",
    )
    durable = ingestor.get_raw_activities(
        github_identity=identity,
        start_time="2024-01-01T00:00:00Z", end_time="2024-06-30T23:59:59Z",
    )
    by_item = {item.item_id: item.record_id for item in durable}
    handoff = prepare_agent_handoff(
        mock_fulcra_client, identity,
        exact_start_time="2024-01-01T00:00:00Z",
        exact_end_time="2024-06-30T23:59:59Z",
    )

    grounded_response = {
        "context_id": handoff["context_id"],
        "narrative_plan": _minimal_plan(
            "Authentication work connected a new token-refresh endpoint to the web "
            "portal's login flow before a separate analytics dashboard appeared later.",
            [by_item["auth-1"], by_item["portal-1"]],
            ["acme/auth-service", "acme/web-portal"],
            "2024-01-01T00:00:00Z", "2024-01-31T23:59:59Z",
        ),
        "overview": (
            "Early in the period, a new OAuth refresh-token endpoint in auth-service "
            "was wired directly into web-portal's login screen, connecting the two "
            "repositories through an explicitly evidenced integration. Activity is not "
            "recorded again until June, when a login funnel dashboard appeared in a "
            "separate analytics repository with no documented connection to the earlier work."
        ),
        "sections": [
            {
                "section_id": "auth-portal-integration",
                "title": "January 2024 — Refresh-token integration",
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
                "raw_record_ids": [by_item["auth-1"], by_item["portal-1"]],
                "narrative": (
                    "auth-service added an OAuth refresh-token endpoint, and web-portal's "
                    "login screen was wired to consume it the same week, per the evidenced "
                    "pull request bodies."
                ),
            },
            {
                "section_id": "gap-and-analytics",
                "title": "February-June 2024 — Gap, then a separate dashboard",
                "start_time": "2024-02-01T00:00:00Z",
                "end_time": "2024-06-30T23:59:59Z",
                "raw_record_ids": [by_item["analytics-1"]],
                "narrative": (
                    "No activity is recorded between February and May. In June, a login "
                    "funnel dashboard was added in a separate analytics repository; the "
                    "evidence does not document a connection to the earlier auth work."
                ),
            },
        ],
    }
    # Grounded prose that names the gap honestly and only connects
    # repositories the evidence explicitly relates must validate.
    validated = validate_agent_response(handoff, grounded_response)
    assert "February and May" not in validated["overview"]  # gap stated in section, not embellished in overview
    assert "No activity is recorded" in validated["sections"][1]["narrative"]

    # Embellished version: invents a cause for the gap and asserts a
    # cross-repo relationship the evidence never states for the analytics
    # repo. This must fail closed even though it cites only known raw IDs,
    # because the failure mode here is unsupported *interpretation*, which
    # this test documents as a currently-uncaught risk unless the section
    # text also happens to trip the forbidden-phrase list.
    embellished_response = json.loads(json.dumps(grounded_response))
    embellished_response["sections"][1]["narrative"] = (
        "The team spent February through May in planning and research before "
        "spearheading a robust new analytics initiative that extended the "
        "auth-service integration into a secure, production-grade dashboard."
    )
    with pytest.raises(AgentNarrationValidationError, match="unsupported evaluative"):
        validate_agent_response(handoff, embellished_response)
