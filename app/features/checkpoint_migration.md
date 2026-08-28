# Legacy checkpoint migration and cleanup plan

Issue #9 replaces the legacy `GitHub Backfill Checkpoint` duration log with:

- `GitHub Backfill Coverage` (`DurationAnnotation`): one completed source-time
  coverage window per repository/subrange, including zero-activity checks.
- `GitHub Backfill Progress` (`MomentAnnotation`): bounded cursor milestones at
  their actual operational update time.

Readers remain backward-compatible with legacy records. No migration or cleanup
runs automatically, and normal backfills never create additional legacy records.

## Non-destructive inventory

Use `CheckpointManager.plan_legacy_cleanup()` with an authenticated Fulcra
client. It reports legacy record IDs and separates completed coverage candidates
from obsolete in-progress candidates. It always returns
`destructive_action_taken: false`.

Before cleanup:

1. Export and retain the inventory and a backup of the legacy records.
2. For each legacy completed repository/range, verify an equivalent new
   `GitHub Backfill Coverage` record exists. If it does not, rerun/extend the
   normal backfill for that range; zero-activity coverage will also be retained.
3. Verify there is no active backfill process depending on a legacy progress
   cursor.
4. Review record counts and representative Timeline queries.

## Separately confirmed cleanup

Deletion is intentionally not implemented in the application because the
currently supported Fulcra SDK surface does not establish a safe, tested
record-level lifecycle operation for these owner records. After the inventory
has been reviewed, cleanup must be performed through an owner-approved Fulcra
lifecycle tool/API in a separate, explicit operation with its own confirmation.
Delete obsolete legacy `in_progress` records first. Delete legacy completed
records only after equivalent coverage has been verified. Never delete the
custom type before its records have been inventoried and backed up.

Rollback consists of retaining/restoring the backed-up legacy records; the
reader accepts both models during the transition.
