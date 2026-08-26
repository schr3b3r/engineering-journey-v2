# Feature: m6_activity_rollups

## Status
done

## Description
Precompute and durably store "Activity Rollup" records (`DurationAnnotation` base) for day, week, month, quarter, and year periods with hand-rolled numeric aggregation and real `sources` provenance chains referencing raw/lower-layer records.

## Acceptance Criteria
- [x] Registered custom annotation type "Activity Rollup" (`DurationAnnotation` base) with tag `activity_rollup`.
- [x] Hand-rolled aggregation logic computes activity type counts and total activity count across 5 period types (`day`, `week`, `month`, `quarter`, `year`).
- [x] `recorded_at` uses `DurationAnnotation` native range (`{start_time, end_time}`) matching period bounds.
- [x] Real Fulcra tags attached for `period_type`, `github_identity`, and `repo` (when scoped).
- [x] Real provenance chains (`sources`) reference raw activity records (for day rollups) or lower-layer rollup records (for higher periods).
- [x] Rollup records are saved to and queryable from Fulcra durably.
- [x] Has automated tests (pytest) covering rollup generation across all period types, provenance tracing, saving/querying, and the FULL test suite passes — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m3_raw_ingestion.md`
- `m4_multi_repo_backfill.md`

## Notes
- `DurationAnnotation` base type provides native `{start_time, end_time}` in `recorded_at`.
- No model calls required for numeric aggregation; `summary_text` field is optional/reserved for future harness summarization step.
