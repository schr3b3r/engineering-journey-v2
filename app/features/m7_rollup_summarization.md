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
- [x] **(post-M10 addendum)** Cross-repo period summarization (`group_rollups_by_period`, `build_period_summarization_prompt`, `prepare_period_handoff`, `write_back_period_summary`, `summarize_periods_and_write_back`): the original per-rollup mechanism above proved insufficient in practice -- nothing in the CLI ever actually invoked a model against the printed prompts, so `summary_text` never got populated and the generated narrative fell back to one templated sentence per single-repo rollup (see GitHub issue #2, a real user comparing this project's earlier v1 prototype's genuinely engaging narrative output against v2's flat one). The addendum groups rollups by period window across ALL repos and produces one consolidated prompt/summary per period, and a new harness-side driver script (`scripts/summarize_periods.py`) actually completes the loop end-to-end using `harness/providers/`'s multi-provider adapters. `app/` itself still has zero LLM SDK dependencies -- the real model call lives in the harness-side script, not here.
- [x] **Grounded synthesis quality addendum (#7):** period prompts carry durable raw titles/summaries, relevant body excerpts, and `raw:` source IDs; prohibit unsupported technical/causal claims; and synthesize month/quarter/year layers rather than multiplying prompts over day/week records. Legacy rollups are rehydrated from Fulcra raw records, never GitHub.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m3_raw_ingestion.md`
- `m4_multi_repo_backfill.md`
- `m6_activity_rollups.md`

## Notes
- Generation Rules constraint: Ingestion/rollups are fully deterministic code. Only summary text generation invokes a model (the running agent performing the task step, or the harness-side `scripts/summarize_periods.py` driver for CLI-only usage), and writes back via deterministic write call.
- No Gemini/OpenAI provider API key or SDK dependency permitted in app code. (The multi-provider adapters used to actually call a model live in `harness/providers/`, imported only by `scripts/summarize_periods.py` -- never by anything under `app/`.)
