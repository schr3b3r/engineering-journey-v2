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

## Notes
- Built for Milestone 4.
- Uses GitHub Core REST API for repo discovery and existence pre-checks to stay within rate limits.
- Integrates with `CheckpointManager` and `RawActivityIngestor`.
