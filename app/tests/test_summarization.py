"""Tests for harness-side rollup summarization mechanism."""

import json
import importlib
import pytest

from github_spike import GitHubActivityItem
from rollups import ActivityRollup, RollupEngine
from summarization import (
    RollupSummarizer,
    build_summarization_prompt,
    format_batch_summary_handoff,
    format_rollup_summary_handoff,
    generate_fallback_summary,
)


def test_build_summarization_prompt() -> None:
    """Verify build_summarization_prompt constructs structured task prompts."""
    rollup = ActivityRollup(
        period_type="month",
        start_time="2026-08-01T00:00:00Z",
        end_time="2026-08-31T23:59:59Z",
        github_identity="test_dev",
        repo="acme/widget",
        counts={"commit": 5, "pr_opened": 2, "comment": 3},
        total_activity_count=10,
    )

    prompt = build_summarization_prompt(rollup)
    assert "test_dev" in prompt
    assert "month" in prompt
    assert "2026-08-01T00:00:00Z" in prompt
    assert "2026-08-31T23:59:59Z" in prompt
    assert "acme/widget" in prompt
    assert "Total Activity Count: 10" in prompt
    assert "commit: 5" in prompt
    assert "pr_opened: 2" in prompt
    assert "comment: 3" in prompt


def test_format_rollup_summary_handoff() -> None:
    """Verify format_rollup_summary_handoff constructs structured handoff dictionary."""
    rollup = ActivityRollup(
        period_type="quarter",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-03-31T23:59:59Z",
        github_identity="q_dev",
        repo=None,
        counts={"commit": 12},
        total_activity_count=12,
    )

    handoff = format_rollup_summary_handoff(rollup)
    assert handoff["period_type"] == "quarter"
    assert handoff["github_identity"] == "q_dev"
    assert handoff["repo"] is None
    assert handoff["total_activity_count"] == 12
    assert "prompt" in handoff
    assert "q_dev" in handoff["prompt"]


def test_generate_fallback_summary() -> None:
    """Verify deterministic fallback summary generation."""
    rollup = ActivityRollup(
        period_type="day",
        start_time="2026-08-15T00:00:00Z",
        end_time="2026-08-15T23:59:59Z",
        github_identity="fb_dev",
        repo="acme/core",
        counts={"commit": 3, "pr_review": 1},
        total_activity_count=4,
    )

    summary = generate_fallback_summary(rollup)
    assert "2026-08-15" in summary
    assert "fb_dev" in summary
    assert "4 total activities" in summary
    assert "3 commit" in summary
    assert "1 pr_review" in summary


def test_no_bundled_provider_dependency() -> None:
    """Verify summarization module has zero bundled LLM provider dependencies or API keys."""
    summarization_mod = importlib.import_module("summarization")
    source = importlib.util.find_spec("summarization").origin
    with open(source, "r", encoding="utf-8") as f:
        code = f.read()

    forbidden_tokens = ["google.generativeai", "openai", "anthropic", "gemini", "GEMINI_API_KEY", "OPENAI_API_KEY"]
    for token in forbidden_tokens:
        assert token not in code, f"Forbidden bundled provider token '{token}' found in summarization.py"


def test_summarizer_write_back_and_persistence(mock_fulcra_client) -> None:
    """Verify RollupSummarizer updates rollup summary_text and persists it to Fulcra."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "summ_dev"
    repo = "acme/summit"

    items = [
        GitHubActivityItem("commit", repo, identity, "c1", "2026-08-10T10:00:00Z", "Commit 1", ""),
        GitHubActivityItem("pr_opened", repo, identity, "p1", "2026-08-10T12:00:00Z", "PR 1", ""),
    ]

    rollups_dict = engine.generate_all_rollups(items, identity, repo, save_to_fulcra=True)
    month_rollup = rollups_dict["month"][0]
    assert month_rollup.summary_text is None

    summarizer = RollupSummarizer(mock_fulcra_client)

    # Simulate agent generating narrative summary string from handoff prompt
    agent_generated_summary = (
        "In August 2026, summ_dev actively contributed to acme/summit, "
        "opening 1 pull request and delivering 1 commit."
    )

    updated_rollup = summarizer.write_back_summary(month_rollup, agent_generated_summary, save_to_fulcra=True)
    assert updated_rollup.summary_text == agent_generated_summary

    # Query back month rollup from Fulcra to confirm summary_text is stored in note JSON
    queried = engine.get_rollups(
        period_type="month",
        repo=repo,
        github_identity=identity,
        start_time="2026-08-01T00:00:00Z",
        end_time="2026-08-31T23:59:59Z",
    )

    assert len(queried) == 1
    stored_rollup = queried[0]
    assert stored_rollup.summary_text == agent_generated_summary
    assert stored_rollup.counts == {"commit": 1, "pr_opened": 1}
    assert stored_rollup.total_activity_count == 2
    assert stored_rollup.start_time == "2026-08-01T00:00:00Z"
    assert stored_rollup.end_time == "2026-08-31T23:59:59Z"


def test_batch_summarize_and_write_back(mock_fulcra_client) -> None:
    """Verify batch handoff formatting, provider callback execution, and deterministic write-back across all period types."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "batch_dev"
    repo = "acme/batch"

    items = [
        GitHubActivityItem("commit", repo, identity, "c1", "2026-05-10T10:00:00Z", "Commit 1", ""),
        GitHubActivityItem("comment", repo, identity, "cm1", "2026-05-12T10:00:00Z", "Comment 1", ""),
    ]

    all_rollups_dict = engine.generate_all_rollups(items, identity, repo, save_to_fulcra=True)
    
    # Collect one rollup from each period type
    target_rollups = [
        all_rollups_dict["day"][0],
        all_rollups_dict["week"][0],
        all_rollups_dict["month"][0],
        all_rollups_dict["quarter"][0],
        all_rollups_dict["year"][0],
    ]

    summarizer = RollupSummarizer(mock_fulcra_client)

    # Prepare handoff payloads
    handoffs = summarizer.prepare_handoff(target_rollups)
    assert len(handoffs) == 5
    for h in handoffs:
        assert "prompt" in h
        assert "batch_dev" in h["prompt"]

    # Define a custom provider callback simulating agent summarization
    def mock_agent_summarizer(r: ActivityRollup) -> str:
        return f"[Agent Summary for {r.period_type}] {r.github_identity} executed {r.total_activity_count} activities."

    summarized_rollups = summarizer.summarize_and_write_back(
        target_rollups,
        summary_provider_fn=mock_agent_summarizer,
        save_to_fulcra=True,
    )

    assert len(summarized_rollups) == 5
    for r in summarized_rollups:
        assert r.summary_text is not None
        assert f"[Agent Summary for {r.period_type}]" in r.summary_text

    # Verify year rollup queried back from Fulcra
    year_queried = engine.get_rollups(
        period_type="year",
        repo=repo,
        github_identity=identity,
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-12-31T23:59:59Z",
    )

    assert len(year_queried) == 1
    assert year_queried[0].summary_text == f"[Agent Summary for year] {identity} executed 2 activities."
