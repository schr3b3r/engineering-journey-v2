# Feature: M3 Real Raw Ingestion

## Status
done

## Description
Ingests raw activity items for a repository into "GitHub Activity Raw" Fulcra records (`MomentAnnotation` base per `architecture.md`). Records store real event time in `recorded_at`, filterable tags for `activity_type`, `repo`, and `github_identity`, and lineage `sources`. Integrates directly with M1's Checkpoint mechanism for resumable backfilling.

## Acceptance Criteria
- [x] Custom data type "GitHub Activity Raw" (`MomentAnnotation` base) is ensured/registered in Fulcra with schema matching `architecture.md`.
- [x] Raw activity records store real GitHub event timestamps (not ingestion time) in `recorded_at`, tags for `github_activity_raw`, `activity_type:<type>`, `repo:<owner/repo>`, and `github_identity:<user>`, and lineage `sources` chain.
- [x] Ingestion is integrated with M1's `CheckpointManager`, updating progress markers during backfilling and setting `status="completed"` upon completion.
- [x] Interruption and resume tests prove that restarting backfilling after a process termination resumes from the last cursor without duplicate or skipped records.
- [x] Raw activity records can be queried back from Fulcra and filtered by repo, identity, and activity type.
- [x] Has automated tests (pytest) covering the above criteria, and the FULL test suite passes (not just this feature's own tests) — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m2_github_api_spike.md`

## Notes
- Built for Milestone 3.
- Uses `fulcra-api` Python SDK for recording `MomentAnnotation` data types and querying `moment_annotations`.
