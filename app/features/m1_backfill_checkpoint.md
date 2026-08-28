# Feature: M1 Ingestion Coverage and Run Progress

## Status
done

## Description
Simple temporal ledger for canonical ingestion: one `GitHub History Coverage` duration per completed run/window, while bounded `Engineering Journey Run` moments are the only operational progress/resume state.

## Acceptance Criteria
- [x] One completed run writes exactly one source-time `DurationAnnotation`, regardless of repository count.
- [x] Coverage note links run ID, immutable bounds, complete repository snapshot/list/hash/count, raw count, and completion time.
- [x] Zero-activity repositories are durable members of the completed snapshot without their own duration bars.
- [x] New repositories absent from an old snapshot remain uncovered.
- [x] Backward/forward extension subtracts completed run-level intervals by repository membership.
- [x] `Engineering Journey Run` moments retain bounded stage/repository milestones at actual operational time.
- [x] Canonical raw ingestion creates no `GitHub Backfill Progress` cursor records; raw fingerprints make repository replay idempotent.
- [x] Old `GitHub Backfill Coverage`, `GitHub Backfill Progress`, and `GitHub Backfill Checkpoint` records remain read-only inputs for gap compatibility and migration.
- [x] Migration planning is non-destructive; cohort migration is idempotent; legacy type deletion requires separate explicit confirmation.
- [x] Tests inspect Timeline shape and prove hundreds of repositories produce one meaningful duration.
- [x] Full pytest suite passes.

## Dependencies
none

## Notes
- Canonical durable types are `GitHub Activity Raw`, `Engineering Journey Run`, and `GitHub History Coverage`.
- Per-repository coverage/progress types are retired from canonical writes.
