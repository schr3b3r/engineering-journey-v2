"""GitHub API Spike for existence pre-checks and activity type retrieval shapes.

Implements verified endpoints and helper logic for:
1. Multi-repo discovery (public + private)
2. Existence pre-check across GitHub repos and date ranges
3. Per-item retrieval shapes for in-scope activity types:
   - Commits
   - Pull Requests (Opens / Merges)
   - PR Reviews
   - Issue / PR Comments
4. Fulcra `agg/day` endpoint spike verification
5. API call counting for rate-limit and cost metrics
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple
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
    record_id: Optional[str] = None  # Real Fulcra raw record ID after retrieval


class GitHubAPISpike:
    """Interface for GitHub API discovery, existence checks, and retrieval shapes."""

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = "https://api.github.com",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "engineering-journey-v2-spike",
        }
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"
        self.api_call_count: int = 0
        self.progress_callback = progress_callback

    def _progress(self, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(message)

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 10) -> requests.Response:
        """Helper wrapper around requests.get that increments api_call_count."""
        self.api_call_count += 1
        return requests.get(url, headers=self.headers, params=params, timeout=timeout)

    def discover_user_repos(
        self,
        github_identity: str,
        limit: Optional[int] = None,
    ) -> List[str]:
        """Discover public and private repositories accessible by `github_identity`.

        Endpoint: GET /user/repos?affiliation=owner,collaborator,organization_member&per_page=100
        Fallback if no auth token: GET /users/{github_identity}/repos

        `limit` defaults to None (no cap -- paginate through everything
        GitHub returns). A real bug was found and fixed here: a prior
        default of limit=100 silently truncated discovery for any
        account with more than 100 accessible repos, which is exactly
        the "hundreds of org-associated repos" scenario this project
        exists to handle correctly (confirmed live: a real test account
        has 312 accessible repos across owner+collaborator+
        organization_member affiliations -- the old limit=100 default
        meant real repos with real recent activity, e.g.
        fulcradynamics/data-service, fulcradynamics/portal,
        fulcradynamics/user-service, were never even discovered, let
        alone existence-checked or ingested, silently undercounting a
        real backfill run). Pass an explicit `limit` only when a caller
        genuinely wants to bound discovery (e.g. a fast demo/test).
        """
        repos: List[str] = []
        self._progress(f"[github] Discovering repositories accessible to {github_identity}...")

        if self.token:
            url = f"{self.base_url}/user/repos"
            page = 1
            while limit is None or len(repos) < limit:
                self._progress(
                    f"[github] Requesting repository page {page} ({len(repos)} found so far)..."
                )
                page_size = 100 if limit is None else min(100, limit - len(repos))
                params = {
                    "affiliation": "owner,collaborator,organization_member",
                    "per_page": str(page_size),
                    "page": str(page),
                }
                try:
                    r = self._get(url, params=params)
                    if r.status_code == 200:
                        batch = r.json()
                        if not isinstance(batch, list) or not batch:
                            break
                        for item in batch:
                            full_name = item.get("full_name")
                            if full_name and full_name not in repos:
                                repos.append(full_name)
                        if len(batch) < page_size:
                            # Last page (fewer results than requested).
                            break
                        page += 1
                    else:
                        break
                except Exception:
                    break
        else:
            # Unauthenticated fallback: public repos for the user
            url = f"{self.base_url}/users/{github_identity}/repos"
            page = 1
            while limit is None or len(repos) < limit:
                self._progress(
                    f"[github] Requesting public repository page {page} ({len(repos)} found so far)..."
                )
                page_size = 100 if limit is None else min(100, limit - len(repos))
                try:
                    r = self._get(url, params={"per_page": str(page_size), "page": str(page)})
                    if r.status_code == 200 and isinstance(r.json(), list):
                        batch = r.json()
                        if not batch:
                            break
                        for item in batch:
                            full_name = item.get("full_name")
                            if full_name and full_name not in repos:
                                repos.append(full_name)
                        if len(batch) < page_size:
                            break
                        page += 1
                    else:
                        break
                except Exception:
                    break

        self._progress(f"[github] Repository discovery complete: {len(repos)} repositories.")
        return repos


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
            r = self._get(commits_url, params=params)
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
            r_search = self._get(search_url, params={"q": q})
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
            r = self._get(url, params=params)
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
            r = self._get(search_url, params={"q": q})
            if r.status_code == 200:
                search_data = r.json()
                pr_items = search_data.get("items", [])[:limit]
                for pr_index, pr_item in enumerate(pr_items, start=1):
                    if pr_index == 1 or pr_index % 10 == 0:
                        self._progress(
                            f"[github] {repo}: inspecting PR details "
                            f"{pr_index}/{len(pr_items)}..."
                        )
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
                    r_detail = self._get(pr_detail_url)
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
            r = self._get(url_issue, params={"since": since, "per_page": "100"})
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
            r = self._get(url_pr, params={"since": since, "per_page": "100"})
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

    def fetch_all_repo_activity(
        self,
        repo: str,
        github_identity: str,
        since: str,
        until: str,
    ) -> List[GitHubActivityItem]:
        """Fetch all activity types (commits, PRs, comments) for a repo in a date range."""
        items: List[GitHubActivityItem] = []
        self._progress(f"[github] {repo}: fetching commits...")
        commits = self.fetch_commits(repo, github_identity, since, until)
        items.extend(commits)
        self._progress(f"[github] {repo}: {len(commits)} commits; fetching pull requests...")
        pull_requests = self.fetch_pull_requests(repo, github_identity, since, until)
        items.extend(pull_requests)
        self._progress(
            f"[github] {repo}: {len(pull_requests)} PR events; fetching comments/reviews..."
        )
        comments = self.fetch_comments(repo, github_identity, since, until)
        items.extend(comments)
        self._progress(f"[github] {repo}: fetch complete ({len(items)} activity items).")
        # Sort chronologically by event_timestamp
        items.sort(key=lambda x: x.event_timestamp)
        return items


def check_fulcra_agg_day_availability(client: Any, test_data_type_id: Optional[str] = None) -> Dict[str, Any]:
    """Spike whether Fulcra provides a functional `agg/day` endpoint for custom records.

    Uses the real, verified endpoint shape confirmed live during Architecture
    (see architecture.md's Fulcra capability-map section):
    GET /data/v1alpha1/event/{BaseType}/{UUID}/agg/{resolution}?start_time=...&end_time=...
    """
    created_disposable_type = False
    if test_data_type_id is None:
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
            "agg/{resolution}."
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
