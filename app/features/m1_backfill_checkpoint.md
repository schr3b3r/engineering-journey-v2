# Feature: M1 Backfill Coverage and Progress

## Status
done

## Description
Durable, resumable GitHub backfill state using Fulcra's temporal model: completed source-time windows are `GitHub Backfill Coverage` duration records, while bounded operational cursor milestones are `GitHub Backfill Progress` moment records at actual update time.

## Acceptance Criteria
- [x] Completed repository/subrange coverage is a `DurationAnnotation` whose `recorded_at` is exactly the GitHub source window.
- [x] In-progress state is a `MomentAnnotation` at actual update time, clearly separated from completed coverage.
- [x] Progress writes occur at a configurable bounded milestone (default 100 raw writes), not once per item.
- [x] Raw-record replay is idempotent, preserving no-skip/no-duplicate kill and hard-crash recovery between milestones.
- [x] Zero-activity completed coverage remains durable and avoids repeated prechecks.
- [x] Per-repository isolation, backward/forward extension, and coverage-gap calculation remain correct.
- [x] Readers support legacy `GitHub Backfill Checkpoint` records but never create new legacy records.
- [x] `plan_legacy_cleanup()` provides a non-destructive inventory; `checkpoint_migration.md` requires separate owner confirmation for deletion.
- [x] Unit tests inspect underlying duration/moment records and record counts; a `RUN_LIVE_TESTS=1` integration test inspects real Fulcra temporal shape.
- [x] The full pytest suite passes—see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
none

## Notes
- `GitHub Backfill Coverage` uses tag `github_backfill_coverage`.
- `GitHub Backfill Progress` uses tag `github_backfill_progress`.
- The raw records themselves are the idempotency authority if a process dies after a raw write but before its next progress milestone.
