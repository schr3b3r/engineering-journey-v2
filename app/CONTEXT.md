# Engineering Journey v2: Project Context & Architecture

This document is the durable memory for this app, maintained by the agent
itself across tasks. Read this before starting any new task. Update it
whenever you make an architectural decision, pivot, or complete a
significant milestone — so the next task (run by you or a future agent) has
accurate context without needing to re-derive it from the diff history.

This project is independent: it does not reference or depend on any other
app's code, files, or context. Record all decisions relevant to this app
here.

## The Product
Ingest a user-provided number of years of a developer's GitHub activity
(from their own authenticated account) into Fulcra as durable, queryable
records, and support turning that raw activity into different kinds of
readable output — a paced narrative "story," a resume-style overview
blurb, or the data backing a future custom dashboard. This is a ground-up
rebuild, not an iteration on any prior implementation: no existing
codebase, prior architecture, or prior lessons-learned are being...

A ground-up rebuild (see `intake/brief.md` for full scope) that backfills
a user-specified span of one GitHub identity's activity (all contribution
types, public and private repos) into Fulcra as durable, per-item, real
custom-typed records; builds precomputed day/week/month/quarter/year
rollups and a per-period notability signal on top; and ships a lightweight
markdown narrative generator reading that structure. Ingestion/rollup math
is fully deterministic; only rollup-summary text and narrative generation
invoke a model, and only whatever model is already running the skill --
no bundled LLM provider dependency.

(See `architecture.md` at the repo root for the full architecture writeup this summary was excerpted from.)

## Current State
Issues #5-#7 quality/correctness release: rollups now durably retain a bounded
semantic evidence projection from raw GitHub records, and period prompts use
titles, body excerpts, repositories, activity types, and traceable `raw:` IDs.
The harness driver rehydrates old rollups from Fulcra and limits hierarchical
synthesis to month/quarter/year instead of making hundreds of redundant calls.
Narratives use higher-level summaries for the trajectory overview,
human-readable chronology, compact turning points, and an explicitly labelled
limited fallback. Pipeline provider credentials are checked before backfill.
Provenance tables are parsed structurally for UUIDs and exact-set verification
now fails closed; overall header bounds no longer get shadowed by a period loop.

Cross-repo period summarization implemented (post-M10, in response to
real user feedback -- GitHub issue #2 comparing v1's genuinely engaging
narrative output against v2's flat, templated one). `summarize` was a
no-op preview forever: nothing ever consumed its printed prompts, so
every rollup's `summary_text` stayed `None` and `narrative` silently
fell back to one mechanical sentence per single-repo rollup. Closed
that loop for real: `summarization.py` now groups rollups by period
window ACROSS repositories (`group_rollups_by_period`) and produces one
consolidated prompt per period spanning every active repo
(`build_period_summarization_prompt`), and a new harness-side driver
(`scripts/summarize_periods.py`, using `harness/providers/`'s
multi-provider adapters -- Anthropic OAuth-preferred, Gemini, OpenAI)
actually calls a real model and writes the result back via
`summarize_periods_and_write_back`. `narrative.py`'s paced section
renders the real, consolidated cross-repo paragraph when one exists for
a period, and falls back honestly to per-repo rendering when it
doesn't (never silently claims a synthesis that didn't happen). `cli.py
pipeline` now shells out to the driver script by default (opt out with
`--skip-real-summarization`). Full pytest suite: 68 passed (was 61),
+7 new tests covering the cross-repo grouping/prompt/write-back
mechanism and narrative's dedup rendering.

Milestone M10 completed — Packaging as an installable, agent-agnostic skill (`SKILL.md`, `README.md`, `github_auth.py`, `cli.py`, `main.py`). Delivered a root-level `SKILL.md` skill definition and a directly runnable CLI (`cd app && python cli.py ...` / `cd app && python main.py ...` -- NOT `python -m app.cli`, since this project's modules use flat sibling-style imports throughout, matching every other module) supporting `auth`, `backfill`, `rollup`, `summarize`, `narrative`, and `pipeline` subcommands with zero hard agent dependencies. Implemented GitHub OAuth device-code flow (RFC 8628) with explicit user confirmation for detected `gh` sessions or `GITHUB_TOKEN` per spec requirement 8. Full pytest test suite passing (63 tests across `tests/test_cli.py`, `tests/test_narrative.py`, `tests/test_notability.py`, `tests/test_summarization.py`, `tests/test_rollups.py`, `tests/test_backfill.py`, `tests/test_raw_ingestion.py`, `tests/test_github_spike.py`, `tests/test_checkpoint.py`).

See `features/INDEX.md` for the full, structured feature spec — what the
app is supposed to do, broken into individually-scoped features with
acceptance criteria and status. This file (CONTEXT.md) records *why*
things are built the way they are and what's already happened; the
features/ directory records *what* the app should do, including work not
yet started. Consult both, but don't duplicate one into the other.

## Decisions Log
(Newest at the top. One entry per meaningful decision — not a full
chronological journal, just high-signal architectural notes.)

- **(issues #5-#7)** Grounded hierarchical narrative synthesis and fail-closed provenance: `ActivityRollup.evidence_items` preserves a bounded semantic projection with raw lineage; old records can be hydrated from durable raw Fulcra items; model prompts use this evidence and forbid unsupported connections. Month summaries provide pacing while quarter/year summaries provide trajectory. Limited mode is explicitly non-equivalent and compact. Appendix parsing is table-structural (UUID-safe) with exact-set validation. The model provider preflight happens before pipeline backfill while retaining `--skip-real-summarization` as an explicit limited-mode opt-in.
- **(post-M10)** Cross-Repo Period Summarization (`summarization.py`'s `group_rollups_by_period`/`build_period_summarization_prompt`/`summarize_periods_and_write_back`, new `scripts/summarize_periods.py`, ported `harness/providers/` multi-provider adapters): fixed a real quality gap where `narrative` output was a flat, templated data dump instead of connected prose, because `summarize` never actually called a model or wrote anything back. The fix keeps `app/`'s zero-LLM-SDK constraint intact by putting the real model call in harness-side tooling (`scripts/summarize_periods.py`, which imports both `app/`'s data layer and `harness/providers/`), not in `app/cli.py`. Grouping is cross-repo per period window (matching how a genuinely good narrative reads -- one paragraph per quarter spanning every active repo, not one per single-repo rollup). `narrative.py` deduplicates by (period bounds, shared summary_text) to render the consolidated paragraph, falling back honestly when no real summary was written back for a period.
- **(M10)** Skill Packaging & Standalone CLI (`cli.py`, `main.py`, `github_auth.py`, `SKILL.md`): Standalone CLI with zero hard agent dependencies supporting `auth`, `backfill`, `rollup`, `summarize`, `narrative`, and end-to-end `pipeline`. GitHub auth defaults to browser OAuth device-code flow (RFC 8628) and explicitly prompts user for confirmation when existing `gh` session or `GITHUB_TOKEN` is detected. Shipped root-level `SKILL.md` and `README.md` for fresh agent or developer usage.
- **(M9)** Narrative Generation & Provenance Appendix (`narrative.py` & `NarrativeGenerator`): Paced markdown story generation supporting user-interactive range selection ("full", single year, year range, date window), task-prompt shaping for running agent prose generation with deterministic fallback prose, and explicit Provenance Appendix output. `parse_narrative_document()` and `verify_narrative_provenance()` parse code spans in the appendix and verify that every referenced Rollup, Notability Signal, and raw source ID (`raw:repo:item_id`) traces back to real underlying records.
- **(M8)** "Notability Signal" Layer (`notability.py` & `NotabilityEngine`): Uses `NumericAnnotation` custom type ("Notability Signal") with period start ISO timestamp as `recorded_at`, score in `value` field, and rich statistical baseline comparison in `note` JSON payload (mean, std dev, z-score, volume ratio, activity breakdown, category triggers, and narrative explanation). Filterable tags include `notability_signal`, `period_type`, `github_identity`, `repo`, and `notability_category:<cat>`. Uses `numeric_annotations(start_time, end_time, source=type_source_id)` for high-performance querying and deduplication.
- **(M7)** Harness-Side Rollup Summarization (`summarization.py` & `RollupSummarizer`): Implemented task-prompt shape generation (`build_summarization_prompt`), structured-input handoff packaging (`format_rollup_summary_handoff`), and deterministic write-back into Fulcra's `note` JSON payload. Completely provider-agnostic with zero bundled LLM provider dependencies or API keys. Added period-key deduplication in `RollupEngine.get_rollups()` so updated records with summary text take precedence over unsummarized initial writes.
- **(M6)** "Activity Rollup" Layer & Provenance Chaining (`RollupEngine` in `rollups.py`): Uses `DurationAnnotation` custom type ("Activity Rollup") with native `{start_time, end_time}` `recorded_at` matching exact calendar bounds (day/ISO week/month/quarter/year). Hand-rolled numeric activity counts aggregation (commits, PR opens/merges, reviews, comments). Provenance chains (`sources`) link Year -> Quarter -> Month -> Week/Day -> Raw activity items (`raw:repo:item_id`). Excludes client-side `id` from `record_data_type` payload to avoid silently dropping records on Fulcra's backend, and uses `source=type_source_id` in `duration_annotations()` query for 35x performance boost.
- **(M5)** Backward & Forward Extension Strategy (`get_uncovered_ranges` in `CheckpointManager`): Compares newly requested date windows against all existing completed checkpoints for a repository and identity. Computes uncovered sub-intervals by subtracting completed intervals from target range. `BackfillEngine` executes pre-checks and fetches ONLY for uncovered sub-intervals, guaranteeing zero duplicate raw activity records and avoiding redundant API calls.
- **(M4)** Multi-Repo & Multi-Year Backfill Engine (`BackfillEngine` in `backfill.py`): Orchestrates public + private repository discovery (`GET /user/repos`), per-repo cheap Core REST API existence pre-checks, uniform daily-granularity ingestion into "GitHub Activity Raw" records, and durable `CheckpointManager` tracking. Tracks wall-clock time, total records ingested, and GitHub API call counts. Proven interruptible and resumable mid-stream across repos.
- **(M3)** "GitHub Activity Raw" records (`MomentAnnotation` base): Ingestion creates per-item records storing real event time in `recorded_at` (never ingestion time), tags for `github_activity_raw`, `activity_type:<type>`, `repo:<owner/repo>`, and `github_identity:<user>`, and lineage `sources` (`[type_source_id, "github:<owner/repo>", "com.fulcradynamics.cli"]`). Integrated with M1's `CheckpointManager` to track cursors and update status to `completed` upon finishing range ingestion.
- **(M2)** GitHub API existence pre-check strategy: Primary pre-checks use GitHub Core REST API (`GET /repos/{owner}/{repo}/commits?author={github_identity}&since={iso_start}&until={iso_end}&per_page=1`) which consumes standard Core rate limit quota (5,000 req/hr) rather than GitHub Search REST API (`GET /search/commits` or `/search/issues`), which imposes a restrictive 30 req/min limit.
- **(M2)** Fulcra `agg/day` endpoint spike: Direct HTTP verification on `api.fulcradynamics.com` confirmed that Fulcra does not provide a general-purpose `agg/day` endpoint for custom records (HTTP 404). Existence pre-checks and rollup calculations must be computed programmatically by application logic.
- **(M1)** Tag length constraint: Fulcra API strictly limits tag names to 30 characters maximum (`String should have at most 30 characters`). `format_tag` helper truncates raw tags longer than 30 characters and appends a 6-character SHA256 hash snippet to guarantee uniqueness and deterministic matching when filtering.
- **(initial)** Scaffolded from the fulcra-rapid-prototype skill.
  Architecture, Interview, and Plan artifacts from the
  fulcra-prototype-grill-me skill's Intake/Interview/Architecture/Plan phases
  informed this file's initial content — see `intake/`, `interview/`,
  `architecture.md`, and `plan.md` at the repo root (outside `app/`, since
  they're prototyping-phase artifacts, not part of the running app) for
  the full reasoning that produced this starting point.
