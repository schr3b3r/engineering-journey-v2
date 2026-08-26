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
Milestone M4 completed — Real multi-repo, multi-year backfill engine implemented in `backfill.py` (`BackfillEngine`). Integrates public and private repo discovery (`GET /user/repos`), cheap existence pre-checks, durable per-repo checkpointing, API call tracking, and interruption/resume at scale. Full pytest test suite passing (25 tests in `tests/test_backfill.py`, `tests/test_raw_ingestion.py`, `tests/test_github_spike.py`, `tests/test_checkpoint.py`).

See `features/INDEX.md` for the full, structured feature spec — what the
app is supposed to do, broken into individually-scoped features with
acceptance criteria and status. This file (CONTEXT.md) records *why*
things are built the way they are and what's already happened; the
features/ directory records *what* the app should do, including work not
yet started. Consult both, but don't duplicate one into the other.

## Decisions Log
(Newest at the top. One entry per meaningful decision — not a full
chronological journal, just high-signal architectural notes.)

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
