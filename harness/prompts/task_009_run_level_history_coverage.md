Simplify canonical ingestion ledger after issues #13/#14.

Current per-repository `GitHub Backfill Coverage` durations still create hundreds of identical overlapping Timeline bars. `GitHub Backfill Progress` moments duplicate the newer durable `Engineering Journey Run` state. Replace canonical usage with exactly three concepts:

1. `GitHub Activity Raw`: durable source facts with stable fingerprints.
2. `Engineering Journey Run` MomentAnnotations: the only operational progress/resume state, bounded by stages/repository milestones at actual update time.
3. New `GitHub History Coverage` DurationAnnotation: exactly one completed source-time duration per run/window. It links run_id, identity, immutable bounds, repository snapshot/list/hash/count, raw count, and completion time. No repository tag fan-out.

Canonical behavior:
- BackfillEngine computes per-repo uncovered intervals from completed run-level history coverage membership, with cached queries.
- Include old read-only per-repository coverage durations when computing gaps so upgrades do not refetch history.
- Do not create per-repo coverage records for active or zero-activity repos.
- RawActivityIngestor no longer reads/writes durable per-repo cursor checkpoints; it replays safely using raw fingerprints and returns in-memory status only.
- At raw_complete, pipeline writes one idempotent History Coverage record for the run.
- New repositories absent from an old snapshot remain uncovered; extensions create another meaningful run-level duration.

Migration:
- Keep old `GitHub Backfill Coverage`, `GitHub Backfill Progress`, and `GitHub Backfill Checkpoint` readers read-only.
- Add CLI `coverage-migration --plan`, `--migrate --yes`, and a distinct destructive `--delete-legacy-types --yes --confirm-delete-legacy-checkpoints` action.
- Plan groups legacy completed coverage by identity/window into run-level cohorts and inventories progress/legacy records.
- Migrate idempotently creates new History Coverage records without deletion.
- Deletion/archival of legacy custom types is never automatic and requires the exact separate confirmation flag.

Tests must inspect underlying mock Fulcra records and prove one run across hundreds of repos creates one history duration, zero-activity repos are members, Timeline query returns one meaningful bar, new repos/extensions remain uncovered, resume works without progress records, migration is idempotent, and deletion requires explicit confirmation. Update docs/context/architecture. Run full tests and commit app through harness gate.