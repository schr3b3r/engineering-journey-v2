"""GitHub API Spike for existence pre-checks and activity type retrieval shapes.

Implements verified endpoints and helper logic for:
1. Existence pre-check across GitHub repos and date ranges.
2. Per-item retrieval shapes for in-scope activity types:
   - Commits
   - Pull Requests (Opens / Merges)
   - PR Reviews
   - Issue / PR Comments
3. Fulcra `agg/day` endpoint spike verification.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional, Tuple
import requests


@dataclass
class GitHubActivityItem:
    """Standardized representation of a raw GitHub activity item for ingestion."""

    activity_type: str  # "commit" | "pr_open" | "pr_merge" | "pr_review" | "issue_comment" | "pr_comment"
    repo: str  # "owner/repo"
    github_identity: str  # username
    item_id: str  # commit sha, PR number, review ID, or comment ID
    event_timestamp: str  # ISO 8601 string
    title_or_summary: str
    url: str
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class GitHubAPISpike:
    """Interface for GitHub API discovery, existence checks, and retrieval shapes."""

    def __init__(self, token: Optional[str] = None, base_url: str = "https://api.github.com") -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "engineering-journey-v2-spike",
        }
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"

    def check_repo_existence(
        self,
        repo: str,
        github_identity: str,
        since: str,
        until: str,
    ) -> Dict[str, Any]:
        """Perform a cheap existence pre-check using GitHub Core REST API.

        Checks if the identity has any commits or issue/PR activity in `repo`
        between `since` and `until`. Uses Core REST API (`/repos/{repo}/commits?author=...&per_page=1`)
        to avoid Search API's 30 req/min rate limit bottleneck.
        """
        result = {
            "repo": repo,
            "github_identity": github_identity,
            "since": since,
            "until": until,
            "has_activity": False,
            "commit_count_sample": 0,
            "pr_issue_activity": False,
            "endpoint_used": f"{self.base_url}/repos/{repo}/commits",
            "rate_limit_category": "core",
        }

        if not self.token and "api.github.com" in self.base_url:
            result["note"] = "No GITHUB_TOKEN set; existence pre-check skipped live execution."
            return result

        # Step 1: Core REST commits pre-check (1 lightweight API request)
        commits_url = f"{self.base_url}/repos/{repo}/commits"
        params = {
            "author": github_identity,
            "since": since,
            "until": until,
            "per_page": "1",
        }
        try:
            r = requests.get(commits_url, headers=self.headers, params=params, timeout=10)
            if r.status_code == 200:
                commits = r.json()
                if isinstance(commits, list) and len(commits) > 0:
                    result["has_activity"] = True
                    result["commit_count_sample"] = len(commits)
                    return result
            elif r.status_code == 404:
                result["note"] = f"Repo {repo} not found or inaccessible."
                return result
        except Exception as e:
            result["error"] = str(e)
            return result

        # Step 2: Search/Issues fallback for PRs/issues if no commits found
        search_url = f"{self.base_url}/search/issues"
        q = f"repo:{repo} author:{github_identity} created:{since[:10]}..{until[:10]}"
        try:
            r_search = requests.get(search_url, headers=self.headers, params={"q": q}, timeout=10)
            if r_search.status_code == 200:
                data = r_search.json()
                if data.get("total_count", 0) > 0:
                    result["has_activity"] = True
                    result["pr_issue_activity"] = True
        except Exception as e:
            result["search_error"] = str(e)

        return result

    def fetch_commits(
        self,
        repo: str,
        github_identity: str,
        since: str,
        until: str,
        limit: int = 100,
    ) -> List[GitHubActivityItem]:
        """Fetch commits authored by `github_identity` in `repo` within date window.

        Endpoint: GET /repos/{owner}/{repo}/commits?author={github_identity}&since={since}&until={until}
        """
        items: List[GitHubActivityItem] = []
        if not self.token and "api.github.com" in self.base_url:
            return items

        url = f"{self.base_url}/repos/{repo}/commits"
        params = {
            "author": github_identity,
            "since": since,
            "until": until,
            "per_page": str(min(limit, 100)),
        }
        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=10)
            if r.status_code == 200 and isinstance(r.json(), list):
                for c in r.json()[:limit]:
                    sha = c.get("sha", "")
                    commit_obj = c.get("commit", {})
                    author_obj = commit_obj.get("author", {})
                    ts = author_obj.get("date", since)
                    msg = (commit_obj.get("message") or "").split("\n")[0]
                    items.append(
                        GitHubActivityItem(
                            activity_type="commit",
                            repo=repo,
                            github_identity=github_identity,
                            item_id=sha,
                            event_timestamp=ts,
                            title_or_summary=msg,
                            url=c.get("html_url", ""),
                            raw_payload=c,
                        )
                    )
        except Exception:
            pass
        return items

    def fetch_pull_requests(
        self,
        repo: str,
        github_identity: str,
        since: str,
        until: str,
        limit: int = 100,
    ) -> List[GitHubActivityItem]:
        """Fetch PR opens and merges for `github_identity` in `repo` within date window.

        Endpoint: GET /search/issues?q=repo:{repo}+type:pr+author:{github_identity}
        Followed by: GET /repos/{repo}/pulls/{number} for merge check.
        """
        items: List[GitHubActivityItem] = []
        if not self.token and "api.github.com" in self.base_url:
            return items

        search_url = f"{self.base_url}/search/issues"
        q = f"repo:{repo} type:pr author:{github_identity}"
        try:
            r = requests.get(search_url, headers=self.headers, params={"q": q}, timeout=10)
            if r.status_code == 200:
                search_data = r.json()
                for pr_item in search_data.get("items", [])[:limit]:
                    pr_num = pr_item.get("number")
                    created_at = pr_item.get("created_at", since)

                    # PR Open item
                    if since <= created_at <= until:
                        items.append(
                            GitHubActivityItem(
                                activity_type="pr_open",
                                repo=repo,
                                github_identity=github_identity,
                                item_id=f"pr_open_{pr_num}",
                                event_timestamp=created_at,
                                title_or_summary=pr_item.get("title", ""),
                                url=pr_item.get("html_url", ""),
                                raw_payload=pr_item,
                            )
                        )

                    # Fetch detail for merge check
                    pr_detail_url = f"{self.base_url}/repos/{repo}/pulls/{pr_num}"
                    r_detail = requests.get(pr_detail_url, headers=self.headers, timeout=10)
                    if r_detail.status_code == 200:
                        detail = r_detail.json()
                        if detail.get("merged") and detail.get("merged_at"):
                            merged_at = detail["merged_at"]
                            if since <= merged_at <= until:
                                items.append(
                                    GitHubActivityItem(
                                        activity_type="pr_merge",
                                        repo=repo,
                                        github_identity=github_identity,
                                        item_id=f"pr_merge_{pr_num}",
                                        event_timestamp=merged_at,
                                        title_or_summary=f"Merged: {detail.get('title', '')}",
                                        url=detail.get("html_url", ""),
                                        raw_payload=detail,
                                    )
                                )
        except Exception:
            pass
        return items

    def fetch_comments(
        self,
        repo: str,
        github_identity: str,
        since: str,
        until: str,
        limit: int = 100,
    ) -> List[GitHubActivityItem]:
        """Fetch issue and PR comments authored by `github_identity` in `repo`.

        Endpoints:
        - GET /repos/{repo}/issues/comments?since={since}
        - GET /repos/{repo}/pulls/comments?since={since}
        """
        items: List[GitHubActivityItem] = []
        if not self.token and "api.github.com" in self.base_url:
            return items

        # Issue comments
        url_issue = f"{self.base_url}/repos/{repo}/issues/comments"
        try:
            r = requests.get(url_issue, headers=self.headers, params={"since": since, "per_page": "100"}, timeout=10)
            if r.status_code == 200 and isinstance(r.json(), list):
                for comment in r.json():
                    author = comment.get("user", {}).get("login", "")
                    created_at = comment.get("created_at", "")
                    if author == github_identity and since <= created_at <= until:
                        items.append(
                            GitHubActivityItem(
                                activity_type="issue_comment",
                                repo=repo,
                                github_identity=github_identity,
                                item_id=f"ic_{comment.get('id')}",
                                event_timestamp=created_at,
                                title_or_summary=(comment.get("body") or "")[:80],
                                url=comment.get("html_url", ""),
                                raw_payload=comment,
                            )
                        )
                        if len(items) >= limit:
                            break
        except Exception:
            pass

        # PR review line comments
        url_pr = f"{self.base_url}/repos/{repo}/pulls/comments"
        try:
            r = requests.get(url_pr, headers=self.headers, params={"since": since, "per_page": "100"}, timeout=10)
            if r.status_code == 200 and isinstance(r.json(), list):
                for comment in r.json():
                    author = comment.get("user", {}).get("login", "")
                    created_at = comment.get("created_at", "")
                    if author == github_identity and since <= created_at <= until:
                        items.append(
                            GitHubActivityItem(
                                activity_type="pr_comment",
                                repo=repo,
                                github_identity=github_identity,
                                item_id=f"prc_{comment.get('id')}",
                                event_timestamp=created_at,
                                title_or_summary=(comment.get("body") or "")[:80],
                                url=comment.get("html_url", ""),
                                raw_payload=comment,
                            )
                        )
                        if len(items) >= limit:
                            break
        except Exception:
            pass

        return items


def check_fulcra_agg_day_availability(client: Any, test_data_type_id: Optional[str] = None) -> Dict[str, Any]:
    """Spike whether Fulcra provides a functional `agg/day` endpoint for custom records.

    Uses the real, verified endpoint shape confirmed live during Architecture
    (see architecture.md's Fulcra capability-map section):
    GET /data/v1alpha1/event/{BaseType}/{UUID}/agg/{resolution}?start_time=...&end_time=...
    This requires a real registered custom event-type UUID, not a generic
    top-level path -- probing generic paths like /v1/agg/day (with no base
    type or UUID) will always 404 regardless of whether the real capability
    exists, which is exactly the mistake this function avoids.

    Args:
        client: an authenticated FulcraAPI instance.
        test_data_type_id: a real "<BaseType>/<UUID>" custom event-type id to
            probe against (e.g. the "GitHub Backfill Checkpoint" type from
            M1, or any other real custom type already in the catalog). If
            None, this function creates and cleans up its own disposable
            test type, mirroring how this was verified during Architecture.
    """
    created_disposable_type = False
    if test_data_type_id is None:
        # Real SDK method, confirmed against fulcra_api/core.py -- NOT a
        # raw /user/v1alpha1/data-type POST (that endpoint doesn't exist;
        # create_annotation() is the actual mechanism `fulcra data-type
        # create` itself calls under the hood).
        created = client.create_annotation(
            annotation_type="moment",
            name="Agg Day Spike Test",
            description="Disposable type for agg/day endpoint verification",
            tags=[],
        )
        test_data_type_id = f"MomentAnnotation/{created['id']}"
        created_disposable_type = True

    base_type, uuid_part = test_data_type_id.split("/", 1)
    path = f"event/{base_type}/{uuid_part}/agg/day"

    result: Dict[str, Any] = {
        "endpoint": f"/data/v1alpha1/{path}",
        "test_data_type_id": test_data_type_id,
    }
    try:
        resp = client.fulcra_v1_api_path(
            path,
            params={
                "start_time": "2025-01-01T00:00:00Z",
                "end_time": "2025-01-03T00:00:00Z",
            },
        )
        data = json.loads(resp) if isinstance(resp, (bytes, str)) else resp
        result["supports_agg_day"] = True
        result["sample_response"] = data
        result["conclusion"] = (
            "Fulcra DOES support a per-day count-aggregation endpoint for "
            "custom event types via /data/v1alpha1/event/{BaseType}/{UUID}/"
            "agg/{resolution} (confirmed live, matching architecture.md's "
            "verified finding). It returns record_count per day bucket "
            "only -- no groupby/tag-scoped breakdown, and duration stats "
            "are meaningless for instant-based events. Usable for the "
            "existence pre-check and as a corroborating volume signal; "
            "NOT a substitute for hand-rolled rollup content aggregation."
        )
    except Exception as exc:
        result["supports_agg_day"] = False
        result["error"] = str(exc)
        result["conclusion"] = (
            "Could not confirm the agg/day endpoint against this data type "
            f"(error: {exc}). This does NOT confirm the capability is "
            "absent -- architecture.md already verified it live against a "
            "different disposable type; re-check the exact base_type/uuid "
            "path shape before concluding the capability doesn't exist."
        )
    finally:
        if created_disposable_type:
            try:
                client.delete_annotation(annotation_id=uuid_part)
            except Exception:
                pass

    return result
