"""GitHub Authentication Module for Engineering Journey v2.

Supports:
1. Detecting existing `gh` CLI sessions or environment variables (`GITHUB_TOKEN`, `GH_TOKEN`).
2. Explicitly confirming with the user before using an existing session/token (Requirement 8).
3. Defaulting to browser-based OAuth device-code flow (RFC 8628) if no token is present,
   user declines existing auth, or device-code flow is explicitly requested.
"""

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional
import requests

# Default client ID: GitHub CLI's OAuth client ID (standard for CLI device flow)
DEFAULT_GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "178c6fc778aed1577305")
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_API_URL = "https://api.github.com/user"


def get_token_identity(token: str) -> Optional[str]:
    """Fetch GitHub username associated with a given auth token."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"token {token}",
        "User-Agent": "engineering-journey-v2-auth",
    }
    try:
        resp = requests.get(USER_API_URL, headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("login")
    except Exception:
        pass
    return None


def detect_existing_github_auth() -> Optional[Dict[str, Any]]:
    """Detect existing GitHub token from environment or `gh` CLI session.

    Returns:
        Dict with keys: "token", "source", "identity" if found, else None.
    """
    # 1. Check environment variables
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token:
        identity = get_token_identity(env_token)
        return {
            "token": env_token,
            "source": "environment variable (GITHUB_TOKEN/GH_TOKEN)",
            "identity": identity,
        }

    # 2. Check `gh` CLI session
    try:
        res = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            gh_token = res.stdout.strip()
            identity = get_token_identity(gh_token)
            return {
                "token": gh_token,
                "source": "gh CLI session",
                "identity": identity,
            }
    except Exception:
        pass

    return None


def run_device_code_flow(
    client_id: Optional[str] = None,
    scopes: str = "repo,read:user",
    poll_timeout: int = 300,
) -> str:
    """Execute GitHub OAuth Device-Code authentication flow.

    Prompts the user to visit GitHub's device activation URL, enter a user code,
    and polls the GitHub token endpoint until access is granted.
    """
    cid = client_id or DEFAULT_GITHUB_CLIENT_ID
    headers = {
        "Accept": "application/json",
        "User-Agent": "engineering-journey-v2-auth",
    }
    data = {
        "client_id": cid,
        "scope": scopes,
    }

    try:
        resp = requests.post(DEVICE_CODE_URL, headers=headers, data=data, timeout=10)
        if resp.status_code == 404:
            raise RuntimeError(
                "GitHub's device-code endpoint returned 404 Not Found. If "
                "every other GitHub API call works normally from this "
                "environment, this is very likely GitHub blocking OAuth "
                "device-flow token issuance from a datacenter/cloud-"
                "provider IP range (an anti-abuse rule), not an error in "
                "this code or your request. This is common in cloud-hosted "
                "agent sandboxes and CI runners. Set GITHUB_TOKEN to a "
                "Personal Access Token (repo, read:user scopes) instead of "
                "using --device-code -- see SKILL.md's GitHub Credentials "
                "section for details."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to initiate device code flow: {resp.status_code} {resp.text}"
            )
        resp_data = resp.json()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Error connecting to GitHub OAuth device code endpoint: {exc}") from exc

    device_code = resp_data.get("device_code")
    user_code = resp_data.get("user_code")
    verification_uri = resp_data.get("verification_uri", "https://github.com/login/device")
    interval = resp_data.get("interval", 5)
    expires_in = resp_data.get("expires_in", 900)

    print("\n" + "=" * 60)
    print(" GitHub OAuth Device Authentication Required")
    print("=" * 60)
    print(f"1. Open your browser and navigate to: {verification_uri}")
    print(f"2. Enter the user code: {user_code}")
    print("=" * 60)
    print("Waiting for authorization in browser...\n")

    start_time = time.time()
    poll_deadline = min(start_time + expires_in, start_time + poll_timeout)

    token_data = {
        "client_id": cid,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }

    while time.time() < poll_deadline:
        time.sleep(interval)
        try:
            t_resp = requests.post(TOKEN_URL, headers=headers, data=token_data, timeout=10)
            if t_resp.status_code == 200:
                t_json = t_resp.json()
                if "access_token" in t_json:
                    access_token = t_json["access_token"]
                    identity = get_token_identity(access_token)
                    print(f"Successfully authenticated as GitHub user: {identity or 'unknown'}")
                    return access_token
                
                error = t_json.get("error")
                if error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    interval += 5
                    continue
                elif error == "expired_token":
                    raise RuntimeError("Device code expired before authorization. Please try again.")
                elif error == "access_denied":
                    raise RuntimeError("Authorization was cancelled by user.")
                else:
                    raise RuntimeError(f"OAuth error: {t_json.get('error_description', error)}")
        except RuntimeError:
            raise
        except Exception:
            pass

    raise RuntimeError("Timed out waiting for GitHub device code authorization.")


def get_github_auth_token(
    confirm_existing: bool = True,
    force_device_code: bool = False,
    auto_accept_existing: bool = False,
    client_id: Optional[str] = None,
) -> str:
    """Obtain an authenticated GitHub token per spec requirement 8.

    Args:
        confirm_existing: If True and an existing token/gh session is found,
            ask user for confirmation before using it.
        force_device_code: If True, bypass existing token detection and run device flow.
        auto_accept_existing: If True, automatically accept existing session without interactive prompt.
        client_id: Optional custom GitHub OAuth client ID.

    Returns:
        GitHub access token string.
    """
    if not force_device_code:
        existing = detect_existing_github_auth()
        if existing:
            token = existing["token"]
            source = existing["source"]
            identity = existing["identity"] or "unknown identity"

            if auto_accept_existing or not confirm_existing or not sys.stdin.isatty():
                print(f"Using existing GitHub authentication from {source} (User: {identity}).")
                return token

            print(f"\nDetected existing GitHub session from {source}:")
            print(f"  User identity: {identity}")
            
            try:
                ans = input(f"Do you want to use this session ({identity})? [Y/n]: ").strip().lower()
                if ans in ("", "y", "yes"):
                    print(f"Confirmed: Using existing GitHub session for {identity}.")
                    return token
                else:
                    print("Declined existing session. Initiating OAuth device-code flow...")
            except (KeyboardInterrupt, EOFError):
                print("\nUsing detected session by default.")
                return token

    # Fallback / Default: OAuth Device Code Flow
    return run_device_code_flow(client_id=client_id)
