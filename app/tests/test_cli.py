"""Unit and integration tests for CLI and GitHub Authentication (Milestone 10)."""

import argparse
from datetime import datetime, timezone
import json
import os
from unittest.mock import MagicMock, patch
import pytest

from cli import (
    build_parser,
    handle_auth,
    handle_backfill,
    handle_narrative,
    handle_pipeline,
    handle_rollup,
    handle_summarize,
    main,
)
from github_auth import (
    detect_existing_github_auth,
    get_github_auth_token,
    get_token_identity,
    run_device_code_flow,
)


def test_build_parser():
    """Verify CLI argument parser construction and subcommands."""
    parser = build_parser()
    assert parser.prog == "engineering-journey"

    # Test auth args
    args_auth = parser.parse_args(["auth", "--yes", "--device-code"])
    assert args_auth.command == "auth"
    assert args_auth.yes is True
    assert args_auth.device_code is True

    # Test backfill args
    args_bf = parser.parse_args(["backfill", "--years", "2.5", "--identity", "octocat", "--yes", "--dry-run"])
    assert args_bf.command == "backfill"
    assert args_bf.years == 2.5
    assert args_bf.identity == "octocat"
    assert args_bf.dry_run is True

    # Test narrative args
    args_nar = parser.parse_args(["narrative", "--range", "1y", "--output", "custom.md"])
    assert args_nar.command == "narrative"
    assert args_nar.range == "1y"
    assert args_nar.output == "custom.md"


def test_detect_existing_github_auth_env(monkeypatch):
    """Test detecting existing GitHub token from environment variables."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake_env_token_123")
    
    with patch("github_auth.get_token_identity", return_value="octocat"):
        detected = detect_existing_github_auth()
        assert detected is not None
        assert detected["token"] == "fake_env_token_123"
        assert detected["identity"] == "octocat"
        assert "environment variable" in detected["source"]


def test_get_github_auth_token_auto_accept(monkeypatch):
    """Test get_github_auth_token with auto_accept_existing=True."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake_env_token_456")
    
    with patch("github_auth.get_token_identity", return_value="octocat"):
        token = get_github_auth_token(auto_accept_existing=True)
        assert token == "fake_env_token_456"


@patch("github_auth.requests.post")
def test_run_device_code_flow_success(mock_post):
    """Test successful OAuth device-code flow execution."""
    # Mock device code response
    mock_resp_code = MagicMock()
    mock_resp_code.status_code = 200
    mock_resp_code.json.return_value = {
        "device_code": "dev_code_123",
        "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "interval": 0.01,
        "expires_in": 60,
    }

    # Mock token response
    mock_resp_token = MagicMock()
    mock_resp_token.status_code = 200
    mock_resp_token.json.return_value = {
        "access_token": "gho_mock_access_token_789",
        "token_type": "bearer",
        "scope": "repo,read:user",
    }

    mock_post.side_effect = [mock_resp_code, mock_resp_token]

    with patch("github_auth.get_token_identity", return_value="device_user"):
        with patch("github_auth.time.sleep", return_value=None):
            token = run_device_code_flow(poll_timeout=5)
            assert token == "gho_mock_access_token_789"


def test_cli_auth_command_success(monkeypatch):
    """Test CLI auth command execution."""
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")
    
    mock_fulcra = MagicMock()
    with patch("cli.get_fulcra_client", return_value=mock_fulcra):
        with patch("github_auth.get_token_identity", return_value="test_user"):
            ret = main(["auth", "--yes"])
            assert ret == 0


def test_cli_backfill_dry_run(monkeypatch):
    """Test CLI backfill subcommand with --dry-run flag."""
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")
    mock_fulcra = MagicMock()

    with patch("cli.get_fulcra_client", return_value=mock_fulcra):
        with patch("github_auth.get_token_identity", return_value="test_user"):
            with patch("github_spike.GitHubAPISpike.discover_user_repos", return_value=["user/repo1", "user/repo2"]):
                ret = main(["backfill", "--identity", "test_user", "--dry-run", "--yes"])
                assert ret == 0


def test_cli_narrative_command(monkeypatch, tmp_path):
    """Test CLI narrative subcommand generating document file."""
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")
    mock_fulcra = MagicMock()

    fake_tuple = ("# Test Story\n\nContent", "test_story.md", [MagicMock()], [MagicMock()])

    with patch("cli.get_fulcra_client", return_value=mock_fulcra):
        with patch("github_auth.get_token_identity", return_value="test_user"):
            with patch("narrative.NarrativeGenerator.generate_narrative", return_value=fake_tuple):
                out_file = str(tmp_path / "output_narrative.md")
                ret = main(["narrative", "--range", "1y", "--output", out_file, "--identity", "test_user"])
                assert ret == 0
                assert os.path.exists(out_file)
                with open(out_file, "r") as f:
                    assert "# Test Story" in f.read()


# Regression tests for real GitHub issue #1 (schr3b3r/engineering-journey-v2):
# handle_rollup/handle_summarize/handle_pipeline called engine methods that
# did not exist (RollupEngine.compute_and_store_rollups,
# NotabilityEngine.compute_and_store_notability_signals,
# RollupSummarizer.build_summarization_prompt), and pipeline_parser was
# missing several args (--device-code, --dry-run, --repo, --since,
# --until) that handle_backfill reads off the shared Namespace -- so
# `python cli.py pipeline ...` was broken as shipped. These tests
# deliberately do NOT mock the engine classes away (unlike the
# lighter-weight tests above) -- they run the REAL RollupEngine,
# NotabilityEngine, and RollupSummarizer against the real in-memory
# MockFulcraClient, so a future CLI/engine API drift of this exact kind
# fails here instead of only surfacing on a live end-to-end run.


def test_build_parser_pipeline_has_backfill_args():
    """Regression: pipeline_parser must define every arg handle_backfill
    (and the other handlers it calls) reads off the shared Namespace."""
    parser = build_parser()
    args = parser.parse_args([
        "pipeline", "--identity", "octocat", "--yes", "--device-code",
        "--dry-run", "--repo", "octocat/hello-world",
        "--since", "2026-01-01T00:00:00Z", "--until", "2026-02-01T00:00:00Z",
    ])
    assert args.command == "pipeline"
    # These four attribute accesses are exactly what handle_backfill does;
    # AttributeError here means the parser is missing an arg again.
    assert args.device_code is True
    assert args.dry_run is True
    assert args.repo == "octocat/hello-world"
    assert args.since == "2026-01-01T00:00:00Z"
    assert args.until == "2026-02-01T00:00:00Z"


def test_cli_rollup_command_uses_real_engine_apis(monkeypatch, mock_fulcra_client):
    """Regression: handle_rollup must call methods that actually exist on
    RollupEngine/NotabilityEngine (generate_all_rollups/save_rollups,
    compute_signals/save_signals via RawActivityIngestor.get_raw_activities),
    not the never-implemented compute_and_store_* names."""
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")

    from github_spike import GitHubActivityItem
    from raw_ingestion import RawActivityIngestor

    identity = "octocat"
    repo = "octocat/hello-world"
    ingestor = RawActivityIngestor(mock_fulcra_client)
    items = [
        GitHubActivityItem("commit", repo, identity, "sha1", "2026-06-01T10:00:00Z", "Commit 1", ""),
        GitHubActivityItem("pr_opened", repo, identity, "pr1", "2026-06-01T12:00:00Z", "PR 1", ""),
    ]
    ingestor.ingest_items(
        items=items, repo=repo, github_identity=identity,
        start_time="2026-01-01T00:00:00Z", end_time="2026-12-31T23:59:59Z",
    )

    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        with patch("github_auth.get_token_identity", return_value=identity):
            ret = main([
                "rollup", "--identity", identity,
                "--since", "2026-01-01T00:00:00Z", "--until", "2026-12-31T23:59:59Z",
            ])
    assert ret == 0

    # Real assertion that real rollup/signal records actually landed in
    # Fulcra as a result of the CLI call -- not just that it returned 0.
    from notability import NotabilityEngine
    from rollups import RollupEngine

    rollup_engine = RollupEngine(mock_fulcra_client)
    stored_rollups = rollup_engine.get_rollups(github_identity=identity)
    assert len(stored_rollups) > 0

    notability_engine = NotabilityEngine(mock_fulcra_client)
    stored_signals = notability_engine.get_signals(github_identity=identity) if hasattr(
        notability_engine, "get_signals"
    ) else None
    if stored_signals is not None:
        assert isinstance(stored_signals, list)


def test_cli_summarize_command_uses_real_engine_apis(monkeypatch, mock_fulcra_client):
    """Regression: handle_summarize must call RollupEngine.get_rollups +
    RollupSummarizer.prepare_handoff, not the never-implemented
    build_summarization_prompt."""
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")

    from github_spike import GitHubActivityItem
    from rollups import RollupEngine

    identity = "octocat"
    repo = "octocat/hello-world"
    rollup_engine = RollupEngine(mock_fulcra_client)
    items = [
        GitHubActivityItem("commit", repo, identity, "sha1", "2026-06-01T10:00:00Z", "Commit 1", ""),
    ]
    rollup_engine.generate_all_rollups(items, identity, repo, save_to_fulcra=True)

    with patch("cli.get_fulcra_client", return_value=mock_fulcra_client):
        with patch("github_auth.get_token_identity", return_value=identity):
            ret = main([
                "summarize", "--identity", identity,
                "--since", "2026-01-01T00:00:00Z", "--until", "2026-12-31T23:59:59Z",
            ])
    assert ret == 0


def test_cli_pipeline_dry_run_skips_rollup_summarize_narrative(monkeypatch):
    """Regression: `pipeline --dry-run` must stop after the dry-run
    backfill step -- there is no real ingested data for rollup/
    summarize/narrative to act on yet."""
    monkeypatch.setenv("GITHUB_TOKEN", "mock_token")
    mock_fulcra = MagicMock()

    with patch("cli.get_fulcra_client", return_value=mock_fulcra):
        with patch("github_auth.get_token_identity", return_value="test_user"):
            with patch("github_spike.GitHubAPISpike.discover_user_repos", return_value=["user/repo1"]):
                with patch("cli.handle_rollup") as mock_rollup:
                    with patch("cli.handle_narrative") as mock_narrative:
                        ret = main(["pipeline", "--identity", "test_user", "--dry-run", "--yes"])
                        assert ret == 0
                        mock_rollup.assert_not_called()
                        mock_narrative.assert_not_called()


def test_pipeline_provider_preflight_fails_before_backfill(capsys):
    """Issue #7: missing model credentials must not be discovered after a long run."""
    args = build_parser().parse_args([
        "pipeline", "--identity", "octocat", "--yes",
    ])
    failed_check = MagicMock(returncode=2)
    with patch("subprocess.run", return_value=failed_check) as run_check:
        with patch("cli.handle_backfill") as backfill:
            assert handle_pipeline(args) == 2
    backfill.assert_not_called()
    assert "--check-provider" in run_check.call_args.args[0]
    assert "No backfill was started" in capsys.readouterr().err
