# Feature: m9_narrative_generation.md

## Status
done

## Description
Markdown narrative generator that prompts for or accepts a target date range (full history or explicit sub-range), queries stored Activity Rollup and Notability Signal records from Fulcra, produces a paced markdown story document with an explicit provenance appendix tracing back to underlying records, and saves the document named after the selected date range.

## Acceptance Criteria
- [x] Prompts or accepts range parameters (full history or sub-range start/end dates/years).
- [x] Fetches relevant Activity Rollup and Notability Signal records from Fulcra for the specified range.
- [x] Constructs a paced markdown document containing metadata, narrative sections (with period summaries and notable activity callouts), and a complete Provenance Appendix.
- [x] Provenance Appendix explicitly lists all Activity Rollup record IDs, Notability Signal record IDs, and lower-level source references backing the document.
- [x] Names output document deterministically according to the chosen range (e.g., `engineering_journey_2023_to_2025.md` or `engineering_journey_FULL.md`).
- [x] End-to-end reading and validation function parses generated documents and verifies all provenance record IDs match source records.
- [x] Structurally parses rollup/signal appendix tables (including live UUID IDs) and verifies exact completeness in both directions; missing or empty expected tables fail closed.
- [x] Preserves overall requested header bounds across multi-period rendering and uses human-readable month/quarter headings.
- [x] Starts with durable higher-level trajectory synthesis, keeps counts/record IDs subordinate to the appendix, caps turning points, and compresses unsummarized periods into clearly labelled limited transitions.
- [x] Includes a side-by-side quality regression over one shared evidence window scoring specificity, chronology, cross-repo synthesis, pacing, repetition, and unsupported claims.
- [x] Automatically uploads every generated UTF-8 markdown artifact to `/engineering-journeys/<identity>/<writing-year>/` through the Fulcra SDK. Filenames include activity start/end dates and UTC writing date; the CLI prints the path and upload failures are explicit.
- [x] Canonical agent mode accepts a validated overview plus adaptive chronological/thematic sections citing exact raw Fulcra records, then renders and publishes without persisting derived interpretation or requiring external model credentials.
- [x] Has automated tests (pytest) covering the above criteria, and the FULL test suite passes (not just this feature's own tests) — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
- `m6_activity_rollups.md`
- `m7_rollup_summarization.md`
- `m8_notability_signal.md`

## Notes
- Works with both live Fulcra client and in-memory/mock client.
- Includes fallback deterministic narrative builder for offline/agent-agnostic runs as well as prompt-builder/callback support for agent narrative generation.
