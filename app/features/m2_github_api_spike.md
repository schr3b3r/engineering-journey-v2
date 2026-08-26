# Feature: M2 GitHub API Spike — Existence Pre-Check and Ingestion Shape

## Status
done

## Description
Performs a live research spike against real GitHub and Fulcra accounts to verify concrete API endpoints for:
1. Cheap existence pre-check across GitHub repos and time ranges.
2. Per-item retrieval shape for all in-scope activity types (commits, PR opens/merges, PR reviews, issue/PR comments).
3. Verification of Fulcra's `agg/day` endpoint capabilities.

## Findings & Verified Endpoints

### 1. Existence Pre-Check Endpoint
- **Goal:** For any candidate repo (`owner/repo`), determine if the user (`github_identity`) has *any* activity in `[start_date, end_date]` without exhausting rate limits across hundreds of org repos.
- **Endpoint Chosen:** GitHub Core REST Commits API: `GET /repos/{owner}/{repo}/commits?author={github_identity}&since={iso_start}&until={iso_end}&per_page=1`
- **Rate Limit & Performance:**
  - Uses GitHub Core REST API quota (5,000 req/hr authenticated).
  - Responds in ~200ms with a lightweight `[commit_obj]` if activity exists or `[]` if none.
  - Avoids GitHub Search API's restrictive 30 requests/minute secondary rate limit.
- **Fallback:** If commits are empty but PR/issue activity is suspected, `GET /search/issues?q=repo:{owner}/{repo}+author:{github_identity}` provides a secondary check.

### 2. Per-Item Retrieval Endpoints (Ingestion Shape)
- **A. Commits:**
  - Endpoint: `GET /repos/{owner}/{repo}/commits?author={github_identity}&since={iso_start}&until={iso_end}&per_page=100`
  - Extraction: `sha`, `commit.author.date`, `commit.message`, `html_url`.
  - Ingestion: `MomentAnnotation`, `recorded_at` = `commit.author.date`.
- **B. PR Opens & Merges:**
  - Endpoint: `GET /search/issues?q=repo:{owner}/{repo}+type:pr+author:{github_identity}` and `GET /repos/{owner}/{repo}/pulls/{number}`
  - Extraction: `number`, `title`, `created_at`, `merged`, `merged_at`.
  - Ingestion:
    - Open: `MomentAnnotation`, `recorded_at` = `created_at`.
    - Merge: `MomentAnnotation`, `recorded_at` = `merged_at`.
- **C. PR Reviews:**
  - Endpoint: `GET /repos/{owner}/{repo}/pulls/{number}/reviews`
  - Extraction: `id`, `user.login`, `body`, `state`, `submitted_at`, `html_url`.
  - Filter: `user.login == github_identity` and `submitted_at` in range.
  - Ingestion: `MomentAnnotation`, `recorded_at` = `submitted_at`.
- **D. Issue & PR Comments:**
  - Endpoints:
    - Issue Comments: `GET /repos/{owner}/{repo}/issues/comments?since={iso_start}&per_page=100`
    - PR Line Comments: `GET /repos/{owner}/{repo}/pulls/comments?since={iso_start}&per_page=100`
  - Filter: `user.login == github_identity` and `created_at` in `[start, end]`.
  - Ingestion: `MomentAnnotation`, `recorded_at` = `created_at`.

### 3. Fulcra `agg/day` Endpoint Spike
- **Endpoint:** `GET /data/v1alpha1/event/{BaseType}/{UUID}/agg/{resolution}` (e.g. `.../agg/day`), reachable via the SDK's generic `fulcra_v1_api_path()` (not wrapped by a named SDK method or CLI subcommand). Requires a real registered custom event-type `{BaseType}/{UUID}` — probing a generic top-level path with no type/UUID (an earlier draft of this spike's mistake) always 404s regardless of whether the capability exists.
- **Result:** Confirmed live and working — returns `record_count` per day bucket for a real custom event type's real records, matching the same finding already verified during Architecture.
- **Limitations:** No groupby/tag-scoped breakdown in one call; duration stats in the response are meaningless for instant-based (`MomentAnnotation`) events.
- **Conclusion:** Usable for the existence pre-check and as a corroborating volume signal. NOT a substitute for rollup content aggregation (per-activity-type/per-repo breakdowns remain hand-rolled application code, per `architecture.md`).

## Acceptance Criteria
- [x] Verified concrete endpoint(s) for cheap existence pre-checks (`GET /repos/{owner}/{repo}/commits?author=...`).
- [x] Verified concrete endpoint(s) for per-item retrieval of all 4 in-scope activity types (commits, PR opens/merges, PR reviews, comments).
- [x] Spiked Fulcra `agg/day` endpoint and documented verified conclusion (HTTP 404, programmatic rollups required).
- [x] Written answer and helper module (`github_spike.py`) added and tested with automated pytest suite.
- [x] Full test suite passes.

## Dependencies
- M1 Backfill Checkpoint

## Notes
- Live tested against real GitHub identity (`schr3b3r`) and real Fulcra account.
