# Feature: Raw-history running-agent storytelling

## Status
done

## Description
The LLM already running the installed skill interprets durable raw GitHub history at request time. Fulcra remains the source-fact, coverage, progress, and provenance system; derived interpretation is ephemeral.

## Acceptance Criteria
- [x] `prepare_agent_handoff()` queries previously ingested `GitHub Activity Raw` records without GitHub access.
- [x] Every evidence item carries its exact raw Fulcra record ID, timestamp, repository, activity type, compact title/body context, and GitHub URL.
- [x] Retrieval adapts to range density: small ranges are analyzed directly; larger ranges use temporary volume-bounded monthly chunks.
- [x] Chunks are cross-repository context-management aids, not persisted records or mandatory final section boundaries.
- [x] The running LLM returns a trajectory overview and chronological/thematic sections citing exact raw record IDs.
- [x] Validation rejects modified/wrong-run context, malformed chronology, and missing/duplicate/unknown evidence IDs.
- [x] Publishing renders the markdown and raw-record provenance appendix and uploads the final file without writing rollups, signals, or summaries.
- [x] Default pipeline agent mode skips rollup/notability construction and never imports/checks model providers.
- [x] Re-running with a different prompt/model requires no GitHub ingestion.
- [x] End-to-end tests run with GitHub unavailable after raw ingestion and assert no derived annotation records are created.
- [x] The full test suite passes.

## Dependencies
- `m3_raw_ingestion.md`
- `m1_backfill_checkpoint.md`

## Notes
- `agent_narration.py` has no model SDK, rollup, notability, or summary persistence dependency.
- Legacy rollup/notability modules remain temporarily available to explicit standalone compatibility modes but are outside the canonical skill architecture.
