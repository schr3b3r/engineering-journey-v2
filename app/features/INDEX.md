# Feature Index

Structured, per-feature specs for this app. Each feature lives in its own
file in this directory, following a consistent template (see any existing
feature file, or `_TEMPLATE.md`). Update this table whenever a feature's
status changes.

| Feature | Status | Description |
|---|---|---|
| [m1_backfill_checkpoint.md](m1_backfill_checkpoint.md) | done | Resumable backfill progress marker using "GitHub Backfill Checkpoint" custom annotation and tag-based per-repo tracking |
| [m2_github_api_spike.md](m2_github_api_spike.md) | done | GitHub API spike for existence pre-check, per-item retrieval shapes, and Fulcra agg/day endpoint verification |
| [m3_raw_ingestion.md](m3_raw_ingestion.md) | done | Ingestion of raw GitHub activity items into "GitHub Activity Raw" records with event-time recorded_at, filterable tags, sources, and checkpoint integration |
| [m4_multi_repo_backfill.md](m4_multi_repo_backfill.md) | done | Multi-repo discovery and uniform daily-granularity backfill across multi-year windows with existence pre-checks, resumability, and performance metrics |

## Status values
- `not_started` — described but no work done yet.
- `in_progress` — actively being built; may be partially working.
- `done` — acceptance criteria met and verified (not just claimed).
