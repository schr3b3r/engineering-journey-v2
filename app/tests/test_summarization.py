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


# Regression tests for cross-repo period summarization, added in response
# to a real quality gap (GitHub issue #2 against
# schr3b3r/engineering-journey-v2): per-rollup summarization alone
# produces a flat narrative (one templated sentence per single-repo
# rollup); grouping cross-repo by period window and summarizing each
# group in ONE call is what actually lets a real narrative synthesize
# work across repos the way v1's output did.


def test_group_rollups_by_period_groups_across_repos(mock_fulcra_client) -> None:
    from summarization import group_rollups_by_period

    identity = "cross_repo_dev"
    r1 = ActivityRollup(
        period_type="quarter", start_time="2024-04-01T00:00:00Z", end_time="2024-06-30T23:59:59Z",
        github_identity=identity, repo="acme/web", counts={"commit": 3}, total_activity_count=3,
    )
    r2 = ActivityRollup(
        period_type="quarter", start_time="2024-04-01T00:00:00Z", end_time="2024-06-30T23:59:59Z",
        github_identity=identity, repo="acme/api", counts={"pr_merge": 1}, total_activity_count=1,
    )
    r3 = ActivityRollup(
        period_type="quarter", start_time="2024-07-01T00:00:00Z", end_time="2024-09-30T23:59:59Z",
        github_identity=identity, repo="acme/web", counts={"commit": 1}, total_activity_count=1,
    )

    groups = group_rollups_by_period([r1, r2, r3])
    assert len(groups) == 2

    q2_key, q2_rollups = groups[0]
    assert q2_key == ("quarter", "2024-04-01T00:00:00Z", "2024-06-30T23:59:59Z")
    assert {r.repo for r in q2_rollups} == {"acme/web", "acme/api"}

    q3_key, q3_rollups = groups[1]
    assert q3_key == ("quarter", "2024-07-01T00:00:00Z", "2024-09-30T23:59:59Z")
    assert {r.repo for r in q3_rollups} == {"acme/web"}


def test_build_period_summarization_prompt_describes_all_repos() -> None:
    from summarization import build_period_summarization_prompt

    identity = "cross_repo_dev"
    key = ("quarter", "2024-04-01T00:00:00Z", "2024-06-30T23:59:59Z")
    rollups = [
        ActivityRollup(
            period_type="quarter", start_time=key[1], end_time=key[2],
            github_identity=identity, repo="acme/web", counts={"commit": 3}, total_activity_count=3,
            evidence_items=[{
                "source_id": "raw:acme/web:c1", "repo": "acme/web",
                "activity_type": "commit", "title": "Migrate dashboard to React",
                "body_excerpt": "Replace the legacy view layer", "timestamp": key[1], "url": "",
            }],
        ),
        ActivityRollup(
            period_type="quarter", start_time=key[1], end_time=key[2],
            github_identity=identity, repo="acme/api", counts={"pr_merge": 1}, total_activity_count=1,
            evidence_items=[{
                "source_id": "raw:acme/api:p1", "repo": "acme/api",
                "activity_type": "pr_merge", "title": "Add dashboard metrics endpoint",
                "body_excerpt": "Supports the React dashboard", "timestamp": key[1], "url": "",
            }],
        ),
    ]
    prompt = build_period_summarization_prompt(key, rollups)
    assert "acme/web" in prompt
    assert "acme/api" in prompt
    assert "ACROSS all repositories" in prompt
    assert "not one disconnected sentence per repo" in prompt
    # Must ask for real prose, not a template/list, since that's the
    # exact failure mode this mechanism exists to fix.
    assert "not a template" in prompt
    assert "Migrate dashboard to React" in prompt
    assert "Add dashboard metrics endpoint" in prompt
    assert "raw:acme/web:c1" in prompt
    assert "Do not invent" in prompt


def test_rollups_preserve_title_body_and_raw_provenance_as_durable_evidence(mock_fulcra_client) -> None:
    item = GitHubActivityItem(
        "pull_request_opened", "acme/payments", "evidence_dev", "pr42",
        "2024-05-10T10:00:00Z", "Introduce idempotent payment capture",
        "https://github.com/acme/payments/pull/42",
        raw_payload={"body": "Adds an idempotency key and retry-safe state machine."},
    )
    engine = RollupEngine(mock_fulcra_client)
    month = engine.generate_all_rollups(
        [item], "evidence_dev", save_to_fulcra=True,
    )["month"][0]
    assert month.evidence_items[0]["title"] == "Introduce idempotent payment capture"
    assert "retry-safe state machine" in month.evidence_items[0]["body_excerpt"]
    assert month.evidence_items[0]["source_id"] == "raw:acme/payments:pr42"

    stored = engine.get_rollups(github_identity="evidence_dev", period_type="month")[0]
    assert stored.evidence_items == month.evidence_items
    prompt = __import__("summarization").build_period_summarization_prompt(
        (month.period_type, month.start_time, month.end_time), [stored]
    )
    assert "idempotent payment capture" in prompt
    assert "retry-safe state machine" in prompt


def test_summarize_periods_and_write_back_persists_one_summary_per_period(mock_fulcra_client) -> None:
    """The core cross-repo mechanism end to end: real write-back via the
    same durable ActivityRollup.summary_text + RollupEngine.save_rollups
    path as the per-rollup mechanism, but ONE call per period group
    spanning multiple repos, not one call per single-repo rollup."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "period_writeback_dev"

    items = [
        GitHubActivityItem("commit", "acme/web", identity, "c1", "2024-04-05T10:00:00Z", "C1", ""),
        GitHubActivityItem("pr_merge", "acme/api", identity, "p1", "2024-04-10T10:00:00Z", "P1", ""),
    ]
    rollups_by_period = engine.generate_all_rollups(items, identity, save_to_fulcra=True)
    month_rollups = rollups_by_period["month"]
    assert len(month_rollups) == 2  # one per repo, same month

    summarizer = RollupSummarizer(mock_fulcra_client)

    calls = []
    progress = []

    def fake_model_call(prompt: str) -> str:
        calls.append(prompt)
        return "A real, connected cross-repo narrative paragraph, not a template."

    updated = summarizer.summarize_periods_and_write_back(
        month_rollups,
        summary_provider_fn=fake_model_call,
        save_to_fulcra=True,
        progress_callback=progress.append,
    )

    # Exactly ONE model call for the whole period group (both repos),
    # not one call per rollup -- this is the entire point of the fix.
    assert len(calls) == 1
    assert len(updated) == 2
    assert all(r.summary_text == "A real, connected cross-repo narrative paragraph, not a template." for r in updated)
    assert "[summarize 1/1]" in progress[0]
    assert "summary generated and saved" in progress[-1]

    # Verify it was actually persisted (queried back from storage), not
    # just mutated in memory.
    fresh = engine.get_rollups(github_identity=identity, period_type="month")
    assert len(fresh) == 2
    assert all(r.summary_text == "A real, connected cross-repo narrative paragraph, not a template." for r in fresh)


def test_summarize_periods_and_write_back_requires_a_real_provider_fn(mock_fulcra_client) -> None:
    """There must be no silent fallback for the cross-repo path -- a
    caller that forgets to wire in a real model call should get a loud
    TypeError (missing required argument), not a quietly-templated
    narrative (the exact bug this whole mechanism exists to fix)."""
    engine = RollupEngine(mock_fulcra_client)
    identity = "no_fallback_dev"
    items = [GitHubActivityItem("commit", "acme/web", identity, "c1", "2024-04-05T10:00:00Z", "C1", "")]
    rollups_by_period = engine.generate_all_rollups(items, identity, save_to_fulcra=True)

    summarizer = RollupSummarizer(mock_fulcra_client)
    with pytest.raises(TypeError):
        summarizer.summarize_periods_and_write_back(rollups_by_period["month"])  # missing summary_provider_fn


def test_prepare_period_handoff_matches_group_rollups_by_period(mock_fulcra_client) -> None:
    engine = RollupEngine(mock_fulcra_client)
    identity = "handoff_dev"
    items = [
        GitHubActivityItem("commit", "acme/web", identity, "c1", "2024-04-05T10:00:00Z", "C1", ""),
        GitHubActivityItem("pr_merge", "acme/api", identity, "p1", "2024-04-10T10:00:00Z", "P1", ""),
    ]
    rollups_by_period = engine.generate_all_rollups(items, identity, save_to_fulcra=True)
    month_rollups = rollups_by_period["month"]

    summarizer = RollupSummarizer(mock_fulcra_client)
    handoff = summarizer.prepare_period_handoff(month_rollups)

    assert len(handoff) == 1  # one period group (both repos same month)
    payload = handoff[0]
    assert set(payload["repos"]) == {"acme/web", "acme/api"}
    assert payload["total_activity_count"] == 2
    assert "prompt" in payload and "acme/web" in payload["prompt"] and "acme/api" in payload["prompt"]
