# Feature: M1 Backfill Checkpoint

## Status
done

## Description
Implements the "GitHub Backfill Checkpoint" Fulcra record type (`DurationAnnotation` base per `architecture.md`) and functions to record, query, and verify backfill progress. Supports interruptible and resumable backfills across per-repo tag-based tracking dimensions, avoiding reprocessing or skipping work items.

## Acceptance Criteria
- [x] Custom data type "GitHub Backfill Checkpoint" (`DurationAnnotation` base) is ensured/registered in Fulcra with schema matching `architecture.md`.
- [x] Checkpoint records store `{start_time, end_time}` range in `recorded_at`, tags for `repo:<owner/repo>`, `github_identity:<user>`, `status:<in_progress|completed>`, and `github_backfill_checkpoint`, and JSON `note` containing metadata, cursor, and `updated_at`.
- [x] A kill-mid-process/restart-from-fresh-process simulation against fake work items proves correct resume (no reprocessing, no skipped items).
- [x] Per-repo tag-based tracking design is exercised with multiple fake repo names, verifying that progress in one repo does not interfere with another and that range coverage queries (`is_range_covered`) work as expected.
- [x] Has automated tests (pytest) covering the above criteria, and the FULL test suite passes (not just this feature's own tests) — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
none

## Notes
- Built for Milestone 1 as part of the resumability foundation.
- Uses `fulcra-api` Python SDK.
