# Legacy checkpoint migration and cleanup

Canonical ingestion now uses:

- `GitHub History Coverage` (`DurationAnnotation`): one completed source-time
  interval per run and repository snapshot.
- `Engineering Journey Run` (`MomentAnnotation`): bounded operational stages
  and repository milestones at update time.
- Stable raw fingerprints for replay inside an interrupted repository.

The following types are read-only legacy inputs and receive no new canonical
records:

- `GitHub Backfill Coverage` (per-repository durations)
- `GitHub Backfill Progress` (cursor moments)
- `GitHub Backfill Checkpoint` (older combined duration state)

## 1. Non-destructive plan

```bash
python cli.py coverage-migration --plan
```

The plan inventories legacy completed/progress records, groups completed
per-repository records by identity and source-time window, and shows the
run-level cohort that would replace each group. It never writes or deletes.
Back up/review the inventory and inspect representative Timeline queries.

## 2. Idempotent migration

After review:

```bash
python cli.py coverage-migration --migrate --yes
```

This creates one `GitHub History Coverage` duration per legacy identity/window
cohort, including every zero-activity repository represented by legacy
coverage. Re-running is a no-op for already-created migration run IDs. Legacy
records and types remain untouched.

Verify the new durations, repository snapshot counts/hashes, gap behavior, and
Timeline appearance before considering cleanup.

## 3. Separately confirmed destructive cleanup

Cleanup is never automatic. It is refused unless every planned cohort has a
new run-level coverage record and both explicit flags are present:

```bash
python cli.py coverage-migration \
  --delete-legacy-types \
  --yes \
  --confirm-delete-legacy-checkpoints
```

This calls Fulcra's custom annotation deletion/lifecycle operation for the
legacy types listed above. Do not run it while an older application version is
active. Keep the exported inventory/backup for rollback. If any cohort is
missing, the command refuses deletion and instructs you to migrate first.
