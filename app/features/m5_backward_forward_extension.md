# Feature: m5_backward_forward_extension

## Status
done

## Description
Extend an existing backfill further into the past (backward extension) and forward for newer activity (forward extension), without reprocessing or duplicating already-covered ranges or repos.

## Acceptance Criteria
- [x] `CheckpointManager.get_uncovered_ranges()` cleanly computes missing sub-intervals given existing completed checkpoints for a repo and identity.
- [x] `BackfillEngine.run_backfill()` uses `get_uncovered_ranges()` to process only uncovered sub-intervals, skipping already-covered date ranges.
- [x] Backward extension is demonstrated against real checkpoint state (ingesting past activity without reprocessing/duplicating existing future activity).
- [x] Forward extension is demonstrated against real checkpoint state (ingesting newer activity without reprocessing/duplicating existing past activity).
- [x] Zero duplicate raw activity records are ingested into Fulcra during backward or forward extension runs.
- [x] Has automated tests (pytest) covering the above criteria, and the FULL test suite passes (not just this feature's own tests) — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m3_raw_ingestion.md`
- `m4_multi_repo_backfill.md`

## Notes
- Built on top of `CheckpointManager` and `BackfillEngine`. Uses exact interval arithmetic to subtract covered intervals from requested backfill windows.
