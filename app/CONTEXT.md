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
Overview synthesis (issue #18) now requires an ephemeral `narrative_plan`
(trajectory thesis, 1-3 dominant arcs with raw_record_ids, turning points, an
optional culmination) before the agent drafts the Overview, so prose reads as
a selective story rather than an inventory of every repository/category. The
handoff includes a range-adaptive `overview_brief` (evidence density,
recommended arc count, scope guidance) that scales with the requested range
without imposing a fixed template. The publisher deterministically rejects a
missing/malformed plan, more than 3 dominant arcs, and unsupported
evaluative/leadership language (e.g. "spearheaded", "led", "robust", "secure",
"high-impact", "production-grade") anywhere in the plan, overview, or section
prose — a word-boundary-aware scan (`_find_forbidden_phrases`) avoids
false-positives like "led" inside "enabled"/"scheduled". `AGENT_HANDOFF_VERSION`
bumped 2→3 for this schema change so stale in-flight handoffs are rejected.

Canonical ingestion now writes one `GitHub History Coverage` duration per
completed run/window and complete repository snapshot. `Engineering Journey
Run` moments are the only operational progress state. Raw fingerprints handle
replay inside an interrupted repository, so no canonical per-repo coverage or
cursor progress records are written. Timeline cardinality scales with meaningful
ingestion windows rather than repository count. Legacy per-repo types are
read-only until explicitly migrated and separately confirmed for deletion.

Agent-facing monitoring is now explicit rather than aspirational:
`progress-status` turns JSONL into one relay-ready status line, and SKILL.md
forbids waits over 15 seconds or consecutive monitoring tool batches without a
natural-language user update. Unchanged counters still produce a heartbeat.
Tool-feed rows are explicitly not treated as user communication.

Issue #13 reliability now targets the simplified raw-history path: a durable
`Engineering Journey Run` stores the immutable window, discovered repos, stage,
and bounded repo milestones. `--resume` reuses that state, skips discovery, and
skips all GitHub work after `raw_complete`. Human progress is mirrored to JSONL
with counts/rate/ETA/heartbeats and final stage summaries. Retryable Fulcra
DNS/network/429/5xx failures use bounded exponential backoff; deterministic raw
fingerprints plus post-error re-query make ambiguous retries duplicate-safe.

Canonical narration now uses the LLM already running the skill over durable raw
Fulcra history. Default `pipeline` backfills only missing raw coverage, then
exports adaptive in-memory evidence chunks; the current agent synthesizes
cross-repository overview/sections; `publish-agent-narrative` validates exact
raw IDs and chronology and uploads the final artifact. Rollups, notability
signals, and LLM summaries are neither required nor persisted. No external
model login/API key is required. External-provider code is opt-in legacy mode.
Direct Hermes URL installs include a referenced bootstrap script that obtains
the one canonical runtime instead of assuming unbundled `app/` files exist.

Interactive UX now fails closed instead of "running wild": detected GitHub
auth offers use-current/auth-different/cancel, and non-TTY agents must show the
account plus exact run plan before explicitly confirming. The plan includes
UTC bounds, duration, repo scope, write mode, and stages. Long work emits
flushed progress for each repository/subrange, GitHub fetch category, ingest
milestone, rollup/notability stage, model period, narrative, and upload.

Narrative artifact delivery is automatic: `NarrativeGenerator` uploads each
generated markdown document to the owner's Fulcra file store under
`/engineering-journeys/<identity>/<writing-year>/`. The readable filename
contains the exact activity start/end dates and UTC writing date. CLI output
prints both the local path and Fulcra path; upload errors fail clearly rather
than leaving the user to ask an agent to save the artifact afterward.


Issues #5-#7 quality/correctness release: rollups now durably retain a bounded
semantic evidence projection from raw GitHub records, and period prompts use
titles, body excerpts, repositories, activity types, and traceable `raw:` IDs.
The harness driver rehydrates old rollups from Fulcra and limits hierarchical
synthesis to month/quarter/year instead of making hundreds of redundant calls.
Narratives use higher-level summaries for the trajectory overview,
human-readable chronology, compact turning points, and an explicitly labelled
limited fallback. Provider credentials are checked only when standalone
external narration is explicitly selected.
Provenance tables are parsed structurally for UUIDs and exact-set verification
now fails closed; overall header bounds no longer get shadowed by a period loop.

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

- **(issue #18)** Overview editorial synthesis without weakening grounding: response schema gained a required, non-persisted `narrative_plan` (trajectory thesis + ≤3 dominant arcs + turning points + optional culmination, each with real `raw_record_ids`) so the agent commits to editorial selection before drafting prose, rather than mirroring chunk/month boundaries. `overview_brief` gives range-adaptive scope guidance (evidence density → recommended arc count) without forcing a fixed 3-year template onto short ranges or vice versa. A word-boundary-aware forbidden-phrase scan (`UNSUPPORTED_EVALUATIVE_PHRASES`) deterministically rejects unsupported leadership/evaluative language anywhere in the plan, overview, or sections. `AGENT_HANDOFF_VERSION` 2→3.
- **(post-#17 temporal simplification)** One completed immutable run equals one `GitHub History Coverage` source-time bar whose repository snapshot includes active and zero-activity repos. `Engineering Journey Run` is the sole canonical operational state. Per-repo coverage/progress types are retired to migration compatibility, reducing Timeline bars from O(repositories) to O(completed windows).

- **(post-#13 progress UX correction)** Structured events alone do not inform a user when an agent consumes them silently. `progress-status` provides deterministic summarization, while the skill mandates alternating monitor-tool cycles with actual commentary and caps waits at 15 seconds.
- **(issue #13 on raw-history architecture)** Reliability is concentrated in four small pieces: `reliability.py` retry classification/backoff, `progress.py` JSONL events/timings, `pipeline_run.py` durable immutable stage state, and raw fingerprint ambiguity checks. Rollup/notability recovery was deliberately not rebuilt because those records are retired from canonical mode.
- **(issues #14 / post-#12 architecture correction)** Raw Fulcra history is the durable engineering memory; interpretation is ephemeral. The running agent adaptively interprets compact raw evidence with exact record IDs/URLs. Default pipeline does not build derived records or check provider credentials. The publisher stores only the final file artifact. A referenced bootstrap fixes direct Hermes installs without duplicating app code.

- **(post-#11)** Guided, observable execution: account and run-plan approval are distinct user decisions presented together before work. EOF, cancellation, and unconfirmed non-interactive sessions fail closed. `--yes` is valid only after an agent relays the account/plan and obtains explicit approval. Optional progress callbacks keep libraries reusable while CLI/script paths flush continuous contextual output.
- **(post-#9)** Automatic narrative artifact storage: successful generation includes a Fulcra SDK `upload_file` write by default. Owner paths are organized by identity/writing year and filenames encode activity bounds plus writing date. Programmatic callers can explicitly opt out with `upload_to_fulcra=False`; CLI users receive automatic storage and a printed path.
- **(issue #9)** Coverage/progress temporal split: completed windows belong in `GitHub Backfill Coverage` DurationAnnotations at source time; bounded cursors belong in `GitHub Backfill Progress` MomentAnnotations at update time. Raw-item existence makes replay idempotent between 100-item milestones. Legacy reads are transitional and cleanup is explicitly non-destructive until separately owner-confirmed.
- **(issues #5-#7)** Grounded evidence and fail-closed provenance: `ActivityRollup.evidence_items` preserves bounded raw semantics and lineage; cross-repo periods preserve pacing and technical context; UUID-safe appendix validation checks exact completeness. Limited mode is explicitly non-equivalent. These foundations now feed the running-agent handoff.
- **(post-M10, superseded default)** External-provider cross-repo summarization first closed a flat-output defect, but proved to be the wrong default for an installed agent skill. Its grouping/write-back helpers remain; the provider driver is explicit standalone mode only.
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
