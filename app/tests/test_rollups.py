"""Tests for Activity Rollup precomputation, hand-rolled aggregation, and Fulcra storage."""

import json
import time
import uuid
import pytest

from fulcra_client import get_fulcra_client
from github_spike import GitHubActivityItem
from rollups import (
    ROLLUP_ANNOTATION_NAME,
    ActivityRollup,
    RollupEngine,
    get_period_bounds,
)
from checkpoint import parse_iso


def test_period_bounds_calculation() -> None:
    """Verify ISO period bounds calculation across day, week, month, quarter, and year."""
    dt = parse_iso("2026-08-15T14:30:00Z")

    # Day: Aug 15 2026
    start, end = get_period_bounds(dt, "day")
    assert start == "2026-08-15T00:00:00Z"
    assert end == "2026-08-15T23:59:59Z"

    # Week: Aug 15 2026 is Saturday -> Monday Aug 10 to Sunday Aug 16
    start, end = get_period_bounds(dt, "week")
    assert start == "2026-08-10T00:00:00Z"
    assert end == "2026-08-16T23:59:59Z"

    # Month: Aug 2026 -> Aug 01 to Aug 31
    start, end = get_period_bounds(dt, "month")
    assert start == "2026-08-01T00:00:00Z"
    assert end == "2026-08-31T23:59:59Z"

    # Quarter: Q3 2026 -> July 01 to Sept 30
    start, end = get_period_bounds(dt, "quarter")
    assert start == "2026-07-01T00:00:00Z"
    assert end == "2026-09-30T23:59:59Z"

    # Year: 2026 -> Jan 01 to Dec 31
    start, end = get_period_bounds(dt, "year")
    assert start == "2026-01-01T00:00:00Z"
    assert end == "2026-12-31T23:59:59Z"


def test_ensure_data_type(mock_fulcra_client) -> None:
    """Verify RollupEngine registers 'Activity Rollup' custom DurationAnnotation type."""
    engine = RollupEngine(mock_fulcra_client)
    type_info = engine.ensure_data_type()

    assert type_info["name"] == ROLLUP_ANNOTATION_NAME
    assert type_info["annotation_type"] == "duration"

    # Subsequent call reuses cached type info
    assert engine.ensure_data_type()["id"] == type_info["id"]


def test_generate_day_rollups(mock_fulcra_client) -> None:
    """Verify day rollups compute correct activity type counts and total activity count."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "dev_user"
    repo = "acme/widget"

    items = [
        GitHubActivityItem("commit", repo, identity, "c1", "2026-08-15T09:00:00Z", "Commit 1", ""),
        GitHubActivityItem("commit", repo, identity, "c2", "2026-08-15T11:00:00Z", "Commit 2", ""),
        GitHubActivityItem("pr_opened", repo, identity, "pr1", "2026-08-15T14:00:00Z", "PR 1", ""),
        GitHubActivityItem("pr_review", repo, identity, "rev1", "2026-08-15T16:00:00Z", "Review 1", ""),
        GitHubActivityItem("comment", repo, identity, "com1", "2026-08-15T18:00:00Z", "Comment 1", ""),
        # Second day
        GitHubActivityItem("commit", repo, identity, "c3", "2026-08-16T10:00:00Z", "Commit 3", ""),
    ]

    day_rollups = engine.generate_day_rollups(items, identity, repo)
    assert len(day_rollups) == 2

    r1 = day_rollups[0]
    assert r1.period_type == "day"
    assert r1.start_time == "2026-08-15T00:00:00Z"
    assert r1.end_time == "2026-08-15T23:59:59Z"
    assert r1.counts == {"commit": 2, "pr_opened": 1, "pr_review": 1, "comment": 1}
    assert r1.total_activity_count == 5
    assert len(r1.sources) == 5
    assert "raw:acme/widget:c1" in r1.sources

    r2 = day_rollups[1]
    assert r2.period_type == "day"
    assert r2.start_time == "2026-08-16T00:00:00Z"
    assert r2.counts == {"commit": 1}
    assert r2.total_activity_count == 1


def test_generate_all_period_types_and_aggregation(mock_fulcra_client) -> None:
    """Verify rollup generation across all 5 period types (day, week, month, quarter, year)."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "multidate_dev"
    repo = "acme/multi"

    # Create items across multiple months and quarters in 2026
    raw_items = [
        # Q1: Jan
        GitHubActivityItem("commit", repo, identity, "jan_c1", "2026-01-15T10:00:00Z", "Jan C1", ""),
        GitHubActivityItem("pr_opened", repo, identity, "jan_pr1", "2026-01-15T12:00:00Z", "Jan PR1", ""),
        # Q1: Feb
        GitHubActivityItem("commit", repo, identity, "feb_c1", "2026-02-10T10:00:00Z", "Feb C1", ""),
        # Q2: May
        GitHubActivityItem("comment", repo, identity, "may_com1", "2026-05-04T10:00:00Z", "May Com1", ""),
        # Q3: Aug
        GitHubActivityItem("pr_merged", repo, identity, "aug_m1", "2026-08-20T10:00:00Z", "Aug M1", ""),
    ]

    all_rollups = engine.generate_all_rollups(raw_items, identity, repo, save_to_fulcra=True)

    assert "day" in all_rollups
    assert "week" in all_rollups
    assert "month" in all_rollups
    assert "quarter" in all_rollups
    assert "year" in all_rollups

    assert len(all_rollups["day"]) == 4  # Jan 15, Feb 10, May 04, Aug 20
    assert len(all_rollups["month"]) == 4  # Jan, Feb, May, Aug
    assert len(all_rollups["quarter"]) == 3  # Q1, Q2, Q3
    assert len(all_rollups["year"]) == 1  # 2026

    # Verify total activity sums match across levels
    total_day_activity = sum(r.total_activity_count for r in all_rollups["day"])
    total_month_activity = sum(r.total_activity_count for r in all_rollups["month"])
    total_quarter_activity = sum(r.total_activity_count for r in all_rollups["quarter"])
    total_year_activity = sum(r.total_activity_count for r in all_rollups["year"])

    assert total_day_activity == 5
    assert total_month_activity == 5
    assert total_quarter_activity == 5
    assert total_year_activity == 5

    year_r = all_rollups["year"][0]
    assert year_r.start_time == "2026-01-01T00:00:00Z"
    assert year_r.end_time == "2026-12-31T23:59:59Z"
    assert year_r.counts == {"commit": 2, "pr_opened": 1, "comment": 1, "pr_merged": 1}


def test_provenance_chain_tracing(mock_fulcra_client) -> None:
    """Verify real provenance chains (sources) link Year -> Quarter -> Month -> Week/Day -> Raw items."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "prov_dev"
    repo = "acme/prov"

    raw_items = [
        GitHubActivityItem("commit", repo, identity, "sha_99", "2026-03-10T10:00:00Z", "Commit 99", ""),
        GitHubActivityItem("pr_opened", repo, identity, "pr_99", "2026-03-10T11:00:00Z", "PR 99", ""),
    ]

    all_rollups = engine.generate_all_rollups(raw_items, identity, repo, save_to_fulcra=True)

    day_r = all_rollups["day"][0]
    month_r = all_rollups["month"][0]
    quarter_r = all_rollups["quarter"][0]
    year_r = all_rollups["year"][0]

    # Day sources reference raw items
    assert any("raw:acme/prov:sha_99" in s for s in day_r.sources)
    assert any("raw:acme/prov:pr_99" in s for s in day_r.sources)

    # Month sources reference day rollup ID
    assert day_r.record_id in month_r.sources

    # Quarter sources reference month rollup ID
    assert month_r.record_id in quarter_r.sources

    # Year sources reference quarter rollup ID
    assert quarter_r.record_id in year_r.sources


def test_save_and_query_rollups(mock_fulcra_client) -> None:
    """Verify saving rollups to Fulcra and querying back with filters."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "query_dev"
    repo = "acme/query"

    items = [
        GitHubActivityItem("commit", repo, identity, "q_c1", "2026-04-10T10:00:00Z", "Query commit", "")
    ]

    rollups = engine.generate_all_rollups(items, identity, repo, save_to_fulcra=True)

    # Query back month rollups
    month_rollups = engine.get_rollups(
        period_type="month",
        repo=repo,
        github_identity=identity,
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-12-31T23:59:59Z",
    )

    assert len(month_rollups) == 1
    m_r = month_rollups[0]
    assert m_r.period_type == "month"
    assert m_r.repo == repo
    assert m_r.github_identity == identity
    assert m_r.counts == {"commit": 1}
    assert m_r.total_activity_count == 1
    assert m_r.start_time == "2026-04-01T00:00:00Z"
    assert m_r.end_time == "2026-04-30T23:59:59Z"


def test_real_fulcra_integration() -> None:
    """Integration test against real Fulcra API (if authenticated)."""
    try:
        client = get_fulcra_client()
    except Exception as exc:
        pytest.skip(f"Skipping live Fulcra test: {exc}")

    engine = RollupEngine(client)
    type_info = engine.ensure_data_type()
    assert type_info["name"] == ROLLUP_ANNOTATION_NAME

    run_id = uuid.uuid4().hex[:8]
    repo = f"test-fake-org/rollup-{run_id}"
    identity = f"user-{run_id}"

    items = [
        GitHubActivityItem("commit", repo, identity, f"live_commit_{run_id}", "2026-01-15T12:00:00Z", "Live commit", "")
    ]

    all_rollups = engine.generate_all_rollups(items, identity, repo, save_to_fulcra=True)
    assert len(all_rollups["month"]) == 1

    queried = []
    for _ in range(4):
        queried = engine.get_rollups(
            period_type="month",
            repo=repo,
            github_identity=identity,
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-01-31T23:59:59Z",
        )
        if len(queried) >= 1:
            break
        time.sleep(0.5)

    assert len(queried) >= 1
    assert queried[0].counts.get("commit") == 1
