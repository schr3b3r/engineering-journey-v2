# Feature: m7_rollup_summarization

## Status
done

## Description
Prove the concrete mechanism for "the model already running the skill performs the summarization step" against real M6 rollups — task-prompt shape, structured-input handoff, deterministic write-back into the rollup's `note` field without any bundled LLM provider dependency.

## Acceptance Criteria
- [x] Task prompt shape generator (`build_summarization_prompt`) builds clear, structured text prompts for rollups describing activity counts, time bounds, repo, and identity.
- [x] Structured-input handoff helper (`format_rollup_summary_handoff`) packages rollup data and task prompt into standardized input structures for the running agent.
- [x] Deterministic write-back function (`RollupSummarizer` / `summarize_and_write_back`) updates `rollup.summary_text` and persists it into Fulcra's `note` JSON payload while preserving `recorded_at`, tags, and `sources`.
- [x] Provider-agnostic design with zero bundled LLM provider dependencies or API keys anywhere in the codebase.
- [x] Support for single and batch rollup summarization handoff and write-back workflows.
- [x] Has automated tests (pytest) covering prompt generation, handoff formatting, deterministic write-back, querying back updated rollups with summary text, and the FULL test suite passes — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m3_raw_ingestion.md`
- `m4_multi_repo_backfill.md`
- `m6_activity_rollups.md`

## Notes
- Generation Rules constraint: Ingestion/rollups are fully deterministic code. Only summary text generation invokes a model (the running agent performing the task step), and writes back via deterministic write call.
- No Gemini/OpenAI provider API key or SDK dependency permitted in app code.
