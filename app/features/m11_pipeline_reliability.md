# Feature: M11 Reliable Raw Pipeline

## Status
done

## Description
Retry-safe, observable, stage-resumable execution for the canonical raw-history pipeline introduced by issue #14.

## Acceptance Criteria
- [x] One deterministic run ID binds GitHub identity, repository scope, and immutable start/end timestamps.
- [x] Bounded durable `Engineering Journey Run` moment records retain stage, discovered repositories, bounded repo milestones, and record counts.
- [x] `--resume` reuses the original window/repository list and displays a resume plan.
- [x] A `raw_complete` resume skips GitHub discovery and every repository precheck/fetch.
- [x] Progress JSONL has stable `event`, `stage`, `timestamp`, and `elapsed_seconds` fields plus contextual counts/rate/ETA.
- [x] GitHub fetch operations emit structured heartbeats; raw ingestion emits bounded record milestones.
- [x] Final JSONL event contains stage-by-stage timings/counts across resumed invocations.
- [x] `progress-status` safely collapses empty/partial/heartbeat/retry/backfill/completed streams into concise prose or JSON for agent relay.
- [x] Skill orchestration caps waits at 15 seconds and requires a user-visible natural-language update between every monitoring tool cycle.
- [x] Transient DNS/network/429/5xx Fulcra operations use bounded exponential backoff and jitter with retry events.
- [x] Raw records have deterministic source fingerprints. Ambiguous committed-but-response-lost writes are re-queried before retry and do not duplicate.
- [x] History coverage, run-state, raw query/write, and final artifact upload paths use retry handling.
- [x] Integration-style tests interrupt raw ingestion, resume without rediscovery, resume after `raw_complete` with GitHub unavailable, retain the exact window, avoid duplicates, build the handoff, and publish.
- [x] Retired rollup/notability persistence is not part of canonical recovery.
- [x] Full pytest suite passes.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m3_raw_ingestion.md`
- `m7_rollup_summarization.md`

## Notes
- Run-state records are bounded stage/repository milestones, not one record per raw event.
- Progress JSONL is a caller-facing relay channel; durable recovery state remains in Fulcra.
