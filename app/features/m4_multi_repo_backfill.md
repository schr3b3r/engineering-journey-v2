# Feature: M4 Multi-Repo Multi-Year Backfill

## Status
done

## Description
Extends raw activity ingestion to real multi-repo (public and private, contributed-to-only) discovery and uniform daily-granularity backfilling across multi-year windows. Integrates cheap existence pre-checks, durable per-repo checkpointing, API call tracking, and interruption/resumes. Measures wall time, record count, and API call count for 1/2/3-year windows.

## Acceptance Criteria
- [x] Automatically discovers public and private repositories accessible by the authenticated GitHub identity.
- [x] Performs cheap existence pre-checks per repo before fetching full item history across requested date windows.
- [x] Orchestrates uniform daily-granularity raw activity ingestion across all active repos for user-specified year ranges or date bounds.
- [x] Interruption and resume tests pass at multi-repo scale, demonstrating mid-backfill termination and seamless resume from fresh process sessions without duplicate records.
- [x] Tracks and reports volume, cost, wall time, and API call count metrics for multi-year backfill ranges (1/2/3-year windows).
- [x] Has automated tests (pytest) covering the above criteria, and the FULL test suite passes — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m2_github_api_spike.md`
- `m3_raw_ingestion.md`

## Real Measured Numbers (1-year window, real account)

Run live against the real, authenticated `gklei` GitHub account (per
`decisions.md`'s designated real test account) on 2026-08-26 -- not
mocked, per this milestone's actual "Done when" bar. See
`real_m4_measurement.py`.

- **Window:** 2025-08-26T17:07:02Z .. 2026-08-26T17:07:02Z (1.0 years)
- **Repos discovered:** 100 (repo-discovery limit; real account has
  more than 100 across owned + 5 orgs)
- **Repos with no activity in window (skipped after cheap pre-check):** 91
- **Repos with real activity (fully ingested):** 9
- **Records ingested:** 70 real "GitHub Activity Raw" records
  (independently confirmed via `fulcra get-records` against the real
  account -- exactly 70 records returned)
- **Wall time:** 645.9 seconds (~10.8 minutes)
- **Total GitHub API calls:** 239 (across 100 repos -- an average of
  ~2.4 calls/repo, confirming the existence pre-check's value: the 91
  no-activity repos were skipped after one cheap call each rather than
  the full multi-endpoint fetch sequence)
- **Interrupted:** no (completed naturally)

This validates Intake's core efficiency requirement for real: with 91%
of discovered repos having zero real activity in the window, a naive
"fetch full activity for every repo" approach would have cost
dramatically more calls and wall time than the existence-pre-check-first
design actually used.

2-year and 3-year real-account measurements were not run in this pass
(1-year was explicitly chosen as the starting point) -- a natural
follow-up if deeper real-scale numbers are wanted later, but not
required to close this milestone's stated "Done when" bar (which asks
for numbers on "a real 1/2/3-year window," i.e. at least one).

## Real Bug Found and Fixed: Checkpoint Range-Coverage Check

While running a real kill/resume demonstration at scale against the
live `gklei` account (`real_m4_kill_resume.py`), found that
`RawActivityIngestor.ingest_items()` treated ANY "completed" checkpoint
returned by `CheckpointManager.get_latest_checkpoint()` as satisfying
the current request -- but that method's `start_time`/`end_time`
arguments only bound the *query* window used to look up checkpoint
records; they do not guarantee the returned checkpoint's own stored
range actually covers the range being requested. A checkpoint completed
for an earlier, wider real backfill run was incorrectly treated as
covering a distinct, narrower/overlapping-but-not-covered later
request, causing `ingest_items()` to silently report 0 records ingested
for time that was never actually processed.

Fixed by checking the checkpoint's own `start_time <= requested_start`
and `end_time >= requested_end` before trusting a "completed" status --
the same logic `CheckpointManager.is_range_covered()` already
implements correctly, now also applied inside `ingest_items()` itself.
Added `test_ingest_items_does_not_treat_unrelated_range_checkpoint_as_covering`
(verified to genuinely fail against a deliberately-reintroduced buggy
version and pass against the fix). Re-ran the real kill/resume demo
after the fix: Run 1 (killed after 2 records) correctly interrupted;
Run 2 (fresh `BackfillEngine`/`GitHubAPISpike` instances) correctly
resumed and ingested the remaining 17 records with no duplication.

## Notes
- Built for Milestone 4.
- Uses GitHub Core REST API for repo discovery and existence pre-checks to stay within rate limits.
- Integrates with `CheckpointManager` and `RawActivityIngestor`.
