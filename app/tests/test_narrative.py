"""Tests for Narrative Generator module (narrative.py)."""

from datetime import datetime, timezone
import json
import os
import tempfile
import time
from typing import Any, List
from unittest.mock import MagicMock
import pytest

from conftest import MockFulcraClient
from fulcra_client import get_fulcra_client, FulcraAuthError
from github_spike import GitHubActivityItem
from narrative import (
    NarrativeGenerator,
    NarrativeProvenance,
    NarrativeUploadError,
    build_narrative_prompt,
    format_narrative_document,
    generate_fallback_narrative_prose,
    get_fulcra_narrative_path,
    get_narrative_filename,
    parse_narrative_document,
    parse_range_selection,
    prompt_for_range,
    upload_narrative_document,
    verify_narrative_provenance,
)
from notability import NotabilityEngine, NotabilitySignal
from raw_ingestion import RawActivityIngestor
from rollups import ActivityRollup, RollupEngine
from summarization import RollupSummarizer


def test_parse_range_selection() -> None:
    label, start, end = parse_range_selection("full")
    assert label == "FULL_HISTORY"
    assert start == "2000-01-01T00:00:00Z"
    assert end == "2100-01-01T00:00:00Z"

    label, start, end = parse_range_selection("2024")
    assert label == "year_2024"
    assert start == "2024-01-01T00:00:00Z"
    assert end == "2024-12-31T23:59:59Z"

    label, start, end = parse_range_selection("2023-2025")
    assert label == "2023_to_2025"
    assert start == "2023-01-01T00:00:00Z"
    assert end == "2025-12-31T23:59:59Z"

    label, start, end = parse_range_selection("2024-01-01 to 2024-06-30")
    assert label == "2024-01-01_to_2024-06-30"
    assert start == "2024-01-01T00:00:00Z"
    assert end == "2024-06-30T23:59:59Z"


def test_prompt_for_range() -> None:
    label, start, end = prompt_for_range(input_fn=lambda _: "2023")
    assert label == "year_2023"
    assert start == "2023-01-01T00:00:00Z"


def test_get_narrative_filename() -> None:
    fn = get_narrative_filename("testuser", "2023_to_2025")
    assert fn == "engineering_journey_testuser_2023_to_2025.md"


def test_narrative_generation_and_provenance_verification() -> None:
    # Setup mock rollups and signals
    r1 = ActivityRollup(
        period_type="month",
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-01-31T23:59:59Z",
        github_identity="octocat",
        repo="octocat/Hello-World",
        counts={"commit": 10, "pull_request_merged": 2},
        total_activity_count=12,
        sources=["raw:octocat/Hello-World:c1", "raw:octocat/Hello-World:pr1"],
        summary_text="Merged 2 PRs and pushed 10 commits to Hello-World.",
        record_id="rollup_month_001",
    )
    r2 = ActivityRollup(
        period_type="month",
        start_time="2024-02-01T00:00:00Z",
        end_time="2024-02-29T23:59:59Z",
        github_identity="octocat",
        repo="octocat/Hello-World",
        counts={"commit": 5},
        total_activity_count=5,
        sources=["raw:octocat/Hello-World:c2"],
        summary_text="Pushed 5 maintenance commits.",
        record_id="rollup_month_002",
    )

    s1 = NotabilitySignal(
        period_type="month",
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-01-31T23:59:59Z",
        github_identity="octocat",
        score=75.0,
        repo="octocat/Hello-World",
        raw_activity_count=12,
        categories=["volume_surge", "first_activity"],
        sources=["rollup_month_001"],
        explanation="Notability score 75.0/100 (volume_surge, first_activity): high commit volume.",
        record_id="notability_month_001",
    )

    doc = format_narrative_document(
        github_identity="octocat",
        range_label="year_2024",
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-12-31T23:59:59Z",
        rollups=[r1, r2],
        signals=[s1],
        narrative_prose="In early 2024, octocat engaged in high-velocity contributions.",
    )

    assert "# Engineering Journey: octocat" in doc
    assert "## Provenance Appendix" in doc
    assert "`rollup_month_001`" in doc
    assert "`rollup_month_002`" in doc
    assert "`notability_month_001`" in doc
    assert "`raw:octocat/Hello-World:c1`" in doc

    prov = parse_narrative_document(doc)
    assert "rollup_month_001" in prov.rollup_record_ids
    assert "rollup_month_002" in prov.rollup_record_ids
    assert "notability_month_001" in prov.signal_record_ids
    assert "raw:octocat/Hello-World:c1" in prov.raw_source_ids

    assert verify_narrative_provenance(doc, [r1, r2], [s1]) is True

    # Check failure on invalid record ID
    bad_doc = doc + "\n- Fake ID: `rollup_fake_999`\n"
    assert verify_narrative_provenance(bad_doc, [r1, r2], [s1]) is False


def test_paced_narrative_renders_one_cross_repo_paragraph_when_summaries_match() -> None:
    """Regression for GitHub issue #2: when multiple repos' rollups in
    the SAME period window share one real, written-back summary_text
    (the shape summarize_periods_and_write_back produces), the paced
    narrative must render ONE consolidated cross-repo paragraph, not one
    bullet per single-repo rollup."""
    shared_summary = (
        "octocat drove a coordinated push across web and api this month, "
        "shipping a UI overhaul alongside a matching backend endpoint."
    )
    r1 = ActivityRollup(
        period_type="month", start_time="2024-04-01T00:00:00Z", end_time="2024-04-30T23:59:59Z",
        github_identity="octocat", repo="octocat/web", counts={"commit": 3}, total_activity_count=3,
        summary_text=shared_summary, record_id="rollup_web_001",
    )
    r2 = ActivityRollup(
        period_type="month", start_time="2024-04-01T00:00:00Z", end_time="2024-04-30T23:59:59Z",
        github_identity="octocat", repo="octocat/api", counts={"pr_merge": 1}, total_activity_count=1,
        summary_text=shared_summary, record_id="rollup_api_001",
    )

    doc = format_narrative_document(
        github_identity="octocat",
        range_label="year_2024",
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-12-31T23:59:59Z",
        rollups=[r1, r2],
        signals=[],
    )

    start = doc.find("## Paced Activity Narrative")
    end = doc.find("---", start)
    paced_section = doc[start:end]

    # Exactly one consolidated heading for the period, not two per-repo
    # "### Period: ..." headings.
    assert paced_section.count("### April 2024") == 1
    assert "### Period: 2024-04-01" not in paced_section
    assert shared_summary in paced_section
    # Only ONE copy of the shared summary text should appear (not
    # duplicated once per repo).
    assert paced_section.count(shared_summary) == 1
    # Counts, record IDs, and repo key/value fields stay in provenance rather
    # than interrupting the technical story.
    assert "Total Activity Across Repos" not in paced_section
    assert "Source Rollup Records" not in paced_section


def test_paced_narrative_falls_back_to_per_repo_when_no_shared_summary() -> None:
    """When rollups in the same period DON'T share a real summary (no
    summarize_periods_and_write_back run yet), the narrative must
    honestly fall back to per-repo rendering with the deterministic
    fallback summary -- not silently claim a cross-repo synthesis that
    never happened."""
    r1 = ActivityRollup(
        period_type="month", start_time="2024-04-01T00:00:00Z", end_time="2024-04-30T23:59:59Z",
        github_identity="octocat", repo="octocat/web", counts={"commit": 3}, total_activity_count=3,
        record_id="rollup_web_002",
    )
    r2 = ActivityRollup(
        period_type="month", start_time="2024-04-01T00:00:00Z", end_time="2024-04-30T23:59:59Z",
        github_identity="octocat", repo="octocat/api", counts={"pr_merge": 1}, total_activity_count=1,
        record_id="rollup_api_002",
    )

    doc = format_narrative_document(
        github_identity="octocat",
        range_label="year_2024",
        start_time="2024-01-01T00:00:00Z",
        end_time="2024-12-31T23:59:59Z",
        rollups=[r1, r2],
        signals=[],
    )

    start = doc.find("## Paced Activity Narrative")
    end = doc.find("---", start)
    paced_section = doc[start:end]

    # Limited mode compresses the whole period rather than multiplying a
    # template by repository.
    assert paced_section.count("### Transition: April 2024") == 1
    assert "Activity Count" not in paced_section
    assert "Record ID" not in paced_section
    assert "Limited deterministic fallback" in doc


def test_header_preserves_overall_range_across_multiple_months() -> None:
    rollups = [
        ActivityRollup(
            period_type="month", start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-31T23:59:59Z", github_identity="octocat",
            repo="acme/api", total_activity_count=1, summary_text="January work.",
        ),
        ActivityRollup(
            period_type="month", start_time="2024-02-01T00:00:00Z",
            end_time="2024-02-29T23:59:59Z", github_identity="octocat",
            repo="acme/api", total_activity_count=1, summary_text="February work.",
        ),
    ]
    doc = format_narrative_document(
        "octocat", "custom", "2024-01-10T00:00:00Z",
        "2024-02-20T23:59:59Z", rollups, [],
    )
    assert "**Range:** custom (`2024-01-10` to `2024-02-20`)" in doc


def test_uuid_provenance_is_structural_complete_and_non_vacuous() -> None:
    rollup_uuid = "e48d6614-b6ad-5546-af3d-ea8469dcaf0e"
    signal_uuid = "2bc58c53-dd3f-41df-9d41-f81282f799f1"
    rollup = ActivityRollup(
        period_type="month", start_time="2024-01-01T00:00:00Z",
        end_time="2024-01-31T23:59:59Z", github_identity="octocat",
        repo="acme/api", total_activity_count=1, record_id=rollup_uuid,
    )
    signal = NotabilitySignal(
        period_type="month", start_time=rollup.start_time, end_time=rollup.end_time,
        github_identity="octocat", repo="acme/api", score=70,
        raw_activity_count=1, record_id=signal_uuid,
    )
    doc = format_narrative_document(
        "octocat", "year_2024", rollup.start_time, rollup.end_time,
        [rollup], [signal],
    )
    parsed = parse_narrative_document(doc)
    assert parsed.rollup_record_ids == [rollup_uuid]
    assert parsed.signal_record_ids == [signal_uuid]
    assert verify_narrative_provenance(doc, [rollup], [signal])

    missing_rollup_row = "\n".join(
        line for line in doc.splitlines() if rollup_uuid not in line
    )
    assert not verify_narrative_provenance(missing_rollup_row, [rollup], [signal])

    wrong_uuid = doc.replace(signal_uuid, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert not verify_narrative_provenance(wrong_uuid, [rollup], [signal])

    no_tables = doc.replace("### Activity Rollup Records", "### Missing Rollups").replace(
        "### Notability Signal Records", "### Missing Signals"
    )
    assert not verify_narrative_provenance(no_tables, [rollup], [signal])


def test_narrative_generator_end_to_end_mock(mock_fulcra_client: MockFulcraClient) -> None:
    client = mock_fulcra_client
    rollup_engine = RollupEngine(client)
    notability_engine = NotabilityEngine(client)

    raw_items = [
        GitHubActivityItem(
            activity_type="commit",
            repo="octocat/Hello-World",
            github_identity="octocat",
            event_timestamp="2024-03-10T12:00:00Z",
            item_id="c100",
            title_or_summary="Feature commit",
            url="https://github.com/octocat/Hello-World/commit/c100",
        ),
        GitHubActivityItem(
            activity_type="pull_request_opened",
            repo="octocat/Hello-World",
            github_identity="octocat",
            event_timestamp="2024-03-12T14:00:00Z",
            item_id="pr100",
            title_or_summary="Open core PR",
            url="https://github.com/octocat/Hello-World/pull/100",
        ),
    ]

    all_rollups_dict = rollup_engine.generate_all_rollups(
        raw_items, "octocat", "octocat/Hello-World", save_to_fulcra=True
    )
    month_rollups = all_rollups_dict["month"]
    signals = notability_engine.compute_signals(month_rollups)
    notability_engine.save_signals(signals)

    gen = NarrativeGenerator(client)

    with tempfile.TemporaryDirectory() as tmpdir:
        doc_content, filename, rollups, fetched_signals = gen.generate_narrative(
            github_identity="octocat",
            range_selection="2024",
            repo="octocat/Hello-World",
            save_to_file=True,
            output_dir=tmpdir,
            written_at=datetime(2026, 8, 28, 13, 0, tzinfo=timezone.utc),
        )

        assert filename == "engineering_journey_octocat_2024-01-01_to_2024-12-31_written_2026-08-28.md"
        filepath = os.path.join(tmpdir, filename)
        assert os.path.exists(filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            read_content = f.read()

        assert read_content == doc_content
        assert "# Engineering Journey: octocat" in read_content
        assert "## Provenance Appendix" in read_content

        assert verify_narrative_provenance(read_content, rollups, fetched_signals) is True
        expected_fulcra_path = (
            "/engineering-journeys/octocat/2026/"
            "engineering_journey_octocat_2024-01-01_to_2024-12-31_written_2026-08-28.md"
        )
        assert gen.last_fulcra_path == expected_fulcra_path
        uploaded = client.uploaded_files[expected_fulcra_path]
        assert uploaded["data"].decode("utf-8") == doc_content
        assert uploaded["file_type"] == "text/markdown; charset=utf-8"
        assert uploaded["file_size"] == len(doc_content.encode("utf-8"))


def test_fulcra_path_is_organized_sanitized_and_upload_can_be_disabled(
    mock_fulcra_client: MockFulcraClient,
) -> None:
    written_at = datetime(2027, 2, 3, 12, 0, tzinfo=timezone.utc)
    path = get_fulcra_narrative_path(
        "octo cat/../", "2024-02-01T00:00:00Z", "2024-05-31T23:59:59Z",
        written_at=written_at,
    )
    assert path == (
        "/engineering-journeys/octo_cat/2027/"
        "engineering_journey_octo_cat_2024-02-01_to_2024-05-31_written_2027-02-03.md"
    )
    generator = NarrativeGenerator(mock_fulcra_client)
    generator.generate_narrative(
        "octocat", "2024", rollups=[], signals=[], upload_to_fulcra=False,
        written_at=written_at,
    )
    assert generator.last_fulcra_path is None
    assert mock_fulcra_client.uploaded_files == {}


def test_upload_failure_names_the_intended_fulcra_path() -> None:
    client = MagicMock()
    client.upload_file.side_effect = RuntimeError("storage unavailable")
    with pytest.raises(NarrativeUploadError) as exc_info:
        upload_narrative_document(
            client, "# Journey", "octocat", "2024-01-01T00:00:00Z",
            "2024-12-31T23:59:59Z",
            written_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
    assert "/engineering-journeys/octocat/2026/" in str(exc_info.value)
    assert "storage unavailable" in str(exc_info.value)


@pytest.mark.skipif(
    not pytest.importorskip("os").environ.get("RUN_LIVE_TESTS"),
    reason="RUN_LIVE_TESTS=1 not set for live Fulcra tests",
)
def test_live_narrative_generation_end_to_end() -> None:
    try:
        client = get_fulcra_client()
    except FulcraAuthError as exc:
        pytest.skip(f"Fulcra auth unavailable: {exc}")

    rollup_engine = RollupEngine(client)
    notability_engine = NotabilityEngine(client)

    test_identity = "live_m9_identity"
    test_repo = "owner/live_m9_repo"

    raw_items = [
        GitHubActivityItem(
            activity_type="commit",
            repo=test_repo,
            github_identity=test_identity,
            event_timestamp="2024-05-15T10:00:00Z",
            item_id="c_live_901",
            title_or_summary="Live M9 commit",
            url=f"https://github.com/{test_repo}/commit/c_live_901",
        )
    ]

    rollups_dict = rollup_engine.generate_all_rollups(
        raw_items, test_identity, test_repo, save_to_fulcra=True
    )
    month_rollups = rollups_dict["month"]
    signals = notability_engine.compute_signals(month_rollups)
    notability_engine.save_signals(signals)

    # Poll loop for eventual consistency
    gen = NarrativeGenerator(client)
    time.sleep(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        doc_content, filename, fetched_rollups, fetched_signals = gen.generate_narrative(
            github_identity=test_identity,
            range_selection="2024",
            repo=test_repo,
            save_to_file=True,
            output_dir=tmpdir,
        )

        assert "# Engineering Journey: live_m9_identity" in doc_content
        assert "## Provenance Appendix" in doc_content
        filepath = os.path.join(tmpdir, filename)
        assert os.path.exists(filepath)

        with open(filepath, "r", encoding="utf-8") as f:
            read_doc = f.read()

        assert verify_narrative_provenance(read_doc, fetched_rollups, fetched_signals) is True
