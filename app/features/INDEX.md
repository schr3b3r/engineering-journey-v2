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
| [m5_backward_forward_extension.md](m5_backward_forward_extension.md) | done | Backward/forward extension of existing backfills into past/future without reprocessing or duplicating already-covered ranges |
| [m6_activity_rollups.md](m6_activity_rollups.md) | done | Precomputed day/week/month/quarter/year activity rollups with hand-rolled aggregation and provenance chains |
| [m7_rollup_summarization.md](m7_rollup_summarization.md) | done | Harness-side rollup summarization with task-prompt shaping, structured handoff, and deterministic write-back |
| [m8_notability_signal.md](m8_notability_signal.md) | done | First-pass notability signal formula using NumericAnnotation custom records, score in value, and baseline comparison in note |
| [m9_narrative_generation.md](m9_narrative_generation.md) | done | Markdown narrative generator reading rollups+signals, producing paced markdown with provenance appendix |
| [m10_packaging.md](m10_packaging.md) | done | Installable, agent-agnostic skill packaging: root SKILL.md, runnable app/ CLI, OAuth device flow with gh confirmation |

## Status values
- `not_started` — described but no work done yet.
- `in_progress` — actively being built; may be partially working.
- `done` — acceptance criteria met and verified (not just claimed).
