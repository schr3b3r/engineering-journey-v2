# Feature: Running-agent narrative synthesis

## Status
done

## Description
The LLM already running the installed skill is the canonical narrative author. Deterministic app code exports bounded grounded context and validates/publishes the agent's structured response. Normal skill use has no second model client and requires no external provider credential.

## Acceptance Criteria
- [x] `prepare_agent_handoff()` reads durable Fulcra rollups/signals/evidence without calling GitHub solely for narration.
- [x] Handoffs include exact immutable range metadata, chronological periods, pacing hints, repositories, selected raw titles/body excerpts and IDs, and exact rollup source IDs.
- [x] The response schema requires a trajectory overview and exactly one narrative for every expected period.
- [x] Validation fails closed on wrong context IDs, missing/unknown/duplicate periods, missing prose, or source-ID mismatch.
- [x] Valid period prose is written back to the corresponding durable rollups.
- [x] Publishing assembles the normal markdown/provenance appendix, verifies provenance, writes locally, and uploads to Fulcra.
- [x] Default `pipeline` mode produces an agent handoff without importing/checking `harness.providers` or requesting model credentials.
- [x] External-provider and limited modes are explicit standalone alternatives, never silent fallbacks.
- [x] End-to-end tests exercise provider-free handoff, running-agent response, validation, persistence, rendering, and upload.
- [x] The full test suite passes.

## Dependencies
- `m3_raw_ingestion.md`
- `m6_activity_rollups.md`
- `m8_notability_signal.md`
- `m9_narrative_generation.md`

## Notes
- `agent_narration.py` contains no model SDK or provider import.
- `summarization.py` retains deterministic grouping/write-back helpers.
- `scripts/summarize_periods.py` exists only for explicitly selected standalone external mode.
