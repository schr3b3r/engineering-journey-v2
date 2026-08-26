"""Unit and integration tests for CLI and GitHub Authentication (Milestone 10)."""

import argparse
from datetime import datetime, timezone
import json
import os
from unittest.mock import MagicMock, patch
import pytest

from cli import build_parser, handle_auth, handle_backfill, handle_narrative, handle_rollup, main
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
