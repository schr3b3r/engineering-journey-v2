"""Unit and live integration tests for Notability Signal computation, serialization, and Fulcra persistence."""

import json
import time
from unittest.mock import MagicMock
import pytest

from notability import (
    NotabilityEngine,
    NotabilitySignal,
    NOTABILITY_ANNOTATION_NAME,
    NOTABILITY_ANNOTATION_TYPE,
    NOTABILITY_DESCRIPTION,
    NOTABILITY_TAG,
)
from rollups import ActivityRollup
from fulcra_client import get_fulcra_client, FulcraAuthError


def test_notability_signal_dataclass_serialization():
    sig = NotabilitySignal(
        period_type="week",
        start_time="2026-01-05T00:00:00Z",
        end_time="2026-01-11T23:59:59Z",
        github_identity="testuser",
        score=85.5,
        repo="owner/repo",
        raw_activity_count=20,
        baseline_mean=10.0,
        baseline_std=2.5,
        z_score=4.0,
        volume_ratio=2.0,
        categories=["volume_surge", "high_activity"],
        breakdown_by_type={"commit": 15, "pull_request_merged": 5},
        sources=["rollup_123"],
        explanation="Test explanation",
    )

    note_dict = sig.to_note_dict()
    assert note_dict["period_type"] == "week"
    assert note_dict["score"] == 85.5
    assert note_dict["raw_activity_count"] == 20
    assert note_dict["baseline_mean"] == 10.0
    assert note_dict["categories"] == ["volume_surge", "high_activity"]

    json_str = sig.to_note_json()
    assert '"period_type": "week"' in json_str

    # Test reconstruction from record dict
    rec = {
        "id": "sig_rec_1",
        "recorded_at": "2026-01-05T00:00:00Z",
        "value": 85.5,
        "note": json_str,
        "sources": ["type_source_1", "rollup_123"],
    }
    reconstructed = NotabilitySignal.from_record(rec)
    assert reconstructed.period_type == "week"
    assert reconstructed.score == 85.5
    assert reconstructed.raw_activity_count == 20
    assert reconstructed.categories == ["volume_surge", "high_activity"]
    assert reconstructed.explanation == "Test explanation"


def test_compute_signals_formula_and_categories():
    client = MagicMock()
    engine = NotabilityEngine(client)

    rollups = [
        ActivityRollup(
            period_type="week",
            start_time="2026-01-05T00:00:00Z",
            end_time="2026-01-11T23:59:59Z",
            github_identity="testuser",
            repo="owner/repo1",
            counts={"commit": 5},
            total_activity_count=5,
        ),
        ActivityRollup(
            period_type="week",
            start_time="2026-01-12T00:00:00Z",
            end_time="2026-01-18T23:59:59Z",
            github_identity="testuser",
            repo="owner/repo1",
            counts={"commit": 25, "pull_request_merged": 5},
            total_activity_count=30,
        ),
        ActivityRollup(
            period_type="week",
            start_time="2026-01-19T00:00:00Z",
            end_time="2026-01-25T23:59:59Z",
            github_identity="testuser",
            repo="owner/repo1",
            counts={},
            total_activity_count=0,
        ),
    ]

    signals = engine.compute_signals(rollups)
    assert len(signals) == 3

    # Week 1: baseline avg = (5+30+0)/3 = 11.67
    s1 = signals[0]
    assert s1.start_time == "2026-01-05T00:00:00Z"
    assert "first_activity" in s1.categories

    # Week 2: volume surge (30 activity count vs 11.67 mean)
    s2 = signals[1]
    assert s2.start_time == "2026-01-12T00:00:00Z"
    assert "volume_surge" in s2.categories
    assert s2.score > s1.score

    # Week 3: quiet period (0 activity count)
    s3 = signals[2]
    assert s3.start_time == "2026-01-19T00:00:00Z"
    assert "quiet_period" in s3.categories
    assert s3.score == 0.0


def test_ensure_data_type_creation():
    client = MagicMock()
    client.annotations_catalog.return_value = []
    client.create_annotation.return_value = {
        "id": "ann_123",
        "name": NOTABILITY_ANNOTATION_NAME,
        "fulcra_source_id": "com.fulcradynamics.annotation.ann_123",
    }

    engine = NotabilityEngine(client)
    res = engine.ensure_data_type()

    assert res["id"] == "ann_123"
    client.create_annotation.assert_called_once_with(
        annotation_type=NOTABILITY_ANNOTATION_TYPE,
        name=NOTABILITY_ANNOTATION_NAME,
        description=NOTABILITY_DESCRIPTION,
        tags=[],
    )


def test_save_and_get_signals_mock():
    client = MagicMock()
    client.annotations_catalog.return_value = [
        {
            "id": "ann_123",
            "name": NOTABILITY_ANNOTATION_NAME,
            "fulcra_source_id": "com.fulcradynamics.annotation.ann_123",
            "deleted_at": None,
        }
    ]
    client.create_tags.side_effect = lambda tags: [
        {"name": t, "id": f"tag_id_{t}"} for t in tags
    ]
    client.record_data_type.return_value = {"upload_id": "upload_123"}

    engine = NotabilityEngine(client)
    sig = NotabilitySignal(
        period_type="month",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-31T23:59:59Z",
        github_identity="testuser",
        score=92.0,
        repo="owner/repo",
        raw_activity_count=50,
        categories=["volume_surge"],
        sources=["rollup_month_1"],
    )

    save_res = engine.save_signals([sig])
    assert len(save_res) == 1
    client.record_data_type.assert_called_once()

    call_args = client.record_data_type.call_args
    assert call_args[0][0] == "NumericAnnotation"
    rec = call_args[0][1][0]
    assert rec["recorded_at"] == "2026-01-01T00:00:00Z"
    assert rec["value"] == 92.0
    assert "com.fulcradynamics.annotation.ann_123" in rec["sources"]


@pytest.mark.skipif(
    not pytest.importorskip("os").environ.get("RUN_LIVE_TESTS"),
    reason="RUN_LIVE_TESTS=1 not set for live Fulcra tests",
)
def test_live_notability_signal_end_to_end():
    try:
        client = get_fulcra_client()
    except FulcraAuthError as exc:
        pytest.skip(f"Fulcra auth unavailable: {exc}")

    engine = NotabilityEngine(client)
    engine.ensure_data_type()

    # Generate synthetic rollups for testing live save & query
    rollups = [
        ActivityRollup(
            period_type="day",
            start_time="2026-02-01T00:00:00Z",
            end_time="2026-02-01T23:59:59Z",
            github_identity="test_m8_identity",
            repo="owner/test_m8_repo",
            counts={"commit": 12, "pull_request_merged": 2},
            total_activity_count=14,
        ),
        ActivityRollup(
            period_type="day",
            start_time="2026-02-02T00:00:00Z",
            end_time="2026-02-02T23:59:59Z",
            github_identity="test_m8_identity",
            repo="owner/test_m8_repo",
            counts={"commit": 2},
            total_activity_count=2,
        ),
    ]

    signals = engine.compute_signals(rollups)
    assert len(signals) == 2

    # Persist live to Fulcra
    save_results = engine.save_signals(signals)
    assert len(save_results) == 2

    # Query back with poll loop to tolerate Fulcra eventual consistency
    queried_signals = []
    for _ in range(6):
        time.sleep(1)
        queried_signals = engine.get_signals(
            period_type="day",
            repo="owner/test_m8_repo",
            github_identity="test_m8_identity",
            start_time="2026-02-01T00:00:00Z",
            end_time="2026-02-03T00:00:00Z",
        )
        if len(queried_signals) >= 2:
            break

    assert len(queried_signals) >= 2
    q1 = next((s for s in queried_signals if s.start_time == "2026-02-01T00:00:00Z"), None)
    assert q1 is not None
    assert q1.raw_activity_count == 14
    assert q1.score > 50.0
    assert q1.github_identity == "test_m8_identity"
