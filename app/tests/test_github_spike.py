"""Tests for GitHub API Spike logic and Fulcra agg/day endpoint verification."""

import os
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from github_spike import (
    GitHubAPISpike,
    GitHubActivityItem,
    check_fulcra_agg_day_availability,
)
from fulcra_client import get_fulcra_client


def test_github_activity_item_dataclass() -> None:
    """Verify GitHubActivityItem stores all required fields."""
    item = GitHubActivityItem(
        activity_type="commit",
        repo="schr3b3r/engineering-journey-v2",
        github_identity="schr3b3r",
        item_id="abc1234",
        event_timestamp="2026-08-26T12:00:00Z",
        title_or_summary="feat: test commit",
        url="https://github.com/schr3b3r/engineering-journey-v2/commit/abc1234",
    )
    assert item.activity_type == "commit"
    assert item.repo == "schr3b3r/engineering-journey-v2"
    assert item.github_identity == "schr3b3r"
    assert item.item_id == "abc1234"
    assert item.event_timestamp == "2026-08-26T12:00:00Z"


def test_existence_precheck_mocked() -> None:
    """Verify existence pre-check correctly identifies active repos using Core REST API."""
    spike = GitHubAPISpike(token="fake_token", base_url="https://fake.github.api")

    mock_commits_response = MagicMock()
    mock_commits_response.status_code = 200
    mock_commits_response.json.return_value = [
        {"sha": "12345", "commit": {"author": {"date": "2026-08-26T10:00:00Z"}}}
    ]

    with patch("requests.get", return_value=mock_commits_response) as mock_get:
        res = spike.check_repo_existence(
            repo="owner/repo",
            github_identity="user1",
            since="2026-01-01T00:00:00Z",
            until="2026-12-31T23:59:59Z",
        )
        assert res["has_activity"] is True
        assert res["rate_limit_category"] == "core"
        assert "owner/repo" in res["endpoint_used"]
        mock_get.assert_called_once()


def test_existence_precheck_empty_repo() -> None:
    """Verify pre-check returns has_activity=False when repo has no commits or PRs."""
    spike = GitHubAPISpike(token="fake_token", base_url="https://fake.github.api")

    mock_commits = MagicMock()
    mock_commits.status_code = 200
    mock_commits.json.return_value = []

    mock_search = MagicMock()
    mock_search.status_code = 200
    mock_search.json.return_value = {"total_count": 0, "items": []}

    def side_effect(url: str, **kwargs: Any) -> MagicMock:
        if "/commits" in url:
            return mock_commits
        return mock_search

    with patch("requests.get", side_effect=side_effect):
        res = spike.check_repo_existence(
            repo="owner/empty-repo",
            github_identity="user1",
            since="2026-01-01T00:00:00Z",
            until="2026-12-31T23:59:59Z",
        )
        assert res["has_activity"] is False


def test_fetch_commits_mocked() -> None:
    """Verify fetch_commits parses commit records correctly into GitHubActivityItem."""
    spike = GitHubAPISpike(token="fake_token", base_url="https://fake.github.api")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "sha": "sha_abc",
            "html_url": "https://github.com/owner/repo/commit/sha_abc",
            "commit": {
                "author": {"date": "2026-08-26T14:00:00Z"},
                "message": "Initial commit\n\nMore details",
            },
        }
    ]

    with patch("requests.get", return_value=mock_resp):
        items = spike.fetch_commits(
            repo="owner/repo",
            github_identity="dev_user",
            since="2026-01-01T00:00:00Z",
            until="2026-12-31T23:59:59Z",
        )
        assert len(items) == 1
        assert items[0].activity_type == "commit"
        assert items[0].item_id == "sha_abc"
        assert items[0].title_or_summary == "Initial commit"


def test_fetch_all_activity_reports_each_long_stage() -> None:
    progress = []
    spike = GitHubAPISpike(
        token="fake", base_url="https://fake.github.api",
        progress_callback=progress.append,
    )
    item = GitHubActivityItem(
        "commit", "owner/repo", "dev", "sha", "2025-01-02T00:00:00Z",
        "Commit", "",
    )
    with patch.object(spike, "fetch_commits", return_value=[item]):
        with patch.object(spike, "fetch_pull_requests", return_value=[]):
            with patch.object(spike, "fetch_comments", return_value=[]):
                assert spike.fetch_all_repo_activity(
                    "owner/repo", "dev", "2025-01-01T00:00:00Z",
                    "2025-02-01T00:00:00Z",
                ) == [item]
    assert any("fetching commits" in message for message in progress)
    assert any("fetching pull requests" in message for message in progress)
    assert any("fetching comments/reviews" in message for message in progress)
    assert "fetch complete" in progress[-1]


def test_fulcra_agg_day_check_failure_path() -> None:
    """Verify check_fulcra_agg_day_availability reports False and a
    non-misleading conclusion when the real endpoint call fails, without
    creating/cleaning up a real disposable type (mocked client)."""
    mock_client = MagicMock()
    mock_client.fulcra_v1_api_path.side_effect = Exception("HTTP Error 404: Not Found")

    result = check_fulcra_agg_day_availability(
        mock_client, test_data_type_id="MomentAnnotation/00000000-0000-0000-0000-000000000000"
    )
    assert result["supports_agg_day"] is False
    assert "404" in result["error"]
    # Must not overclaim the capability is absent platform-wide from one
    # failed call against one type -- this was the exact real mistake
    # this function replaced (probing generic, wrong-shaped URLs and
    # concluding "Fulcra does NOT support" from universal 404s).
    assert "does NOT confirm" in result["conclusion"] or "already verified" in result["conclusion"]
    mock_client.fulcra_v1_api_path.assert_called_once()
    call_path = mock_client.fulcra_v1_api_path.call_args[0][0]
    assert call_path == "event/MomentAnnotation/00000000-0000-0000-0000-000000000000/agg/day"


@pytest.mark.skipif(
    not os.environ.get("FULCRA_CREDENTIALS_PATH")
    and not (os.environ.get("HOME") and os.path.exists(os.path.expanduser("~/.config/fulcra/credentials.json"))),
    reason="No live Fulcra credentials available for real agg/day endpoint test",
)
def test_fulcra_agg_day_check_real_live() -> None:
    """Live verification against the real Fulcra API: confirms the
    corrected event/{BaseType}/{UUID}/agg/{resolution} endpoint shape
    genuinely works (matching architecture.md's verified finding), using
    a real disposable custom type created and cleaned up by the function
    itself."""
    client = get_fulcra_client()
    result = check_fulcra_agg_day_availability(client)
    assert result["supports_agg_day"] is True
    assert "sample_response" in result
    assert isinstance(result["sample_response"], list)


@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="GITHUB_TOKEN not available for live API test",
)
def test_live_github_api_spike() -> None:
    """Live verification against real GitHub account when GITHUB_TOKEN is present."""
    token = os.environ.get("GITHUB_TOKEN")
    spike = GitHubAPISpike(token=token)

    # Test existence pre-check on self repo
    check = spike.check_repo_existence(
        repo="schr3b3r/engineering-journey-v2",
        github_identity="schr3b3r",
        since="2026-01-01T00:00:00Z",
        until="2026-12-31T23:59:59Z",
    )
    assert check["has_activity"] is True

    # Test fetching commits
    commits = spike.fetch_commits(
        repo="schr3b3r/engineering-journey-v2",
        github_identity="schr3b3r",
        since="2026-01-01T00:00:00Z",
        until="2026-12-31T23:59:59Z",
        limit=5,
    )
    assert len(commits) > 0
    assert commits[0].activity_type == "commit"
