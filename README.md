# Engineering Journey v2

Ingest a user-provided span of a developer's GitHub activity (from their own authenticated account) into Fulcra as durable, queryable records, and support turning that raw activity into readable output — a paced narrative story with provenance traceability.

---

## Deliverables & Architecture

- **Root `SKILL.md`**: Installable agent skill specification for fresh agent sessions or human users.
- **`app/` Application Code & CLI**: Standalone, directly runnable Python application and CLI, run from inside `app/` (`cd app && python cli.py ...` or `cd app && python main.py ...`) with zero hard agent dependencies. This project's modules use flat, sibling-style imports throughout, so the CLI is invoked from inside `app/`, not via `python -m app.cli` from the repo root.
- **Durable Record Architecture in Fulcra**:
  - `GitHub Backfill Coverage` (`DurationAnnotation`): Completed repository/subrange coverage at source time.
  - `GitHub Backfill Progress` (`MomentAnnotation`): Bounded resumable cursor milestones at actual update time.
  - `GitHub Activity Raw` (`MomentAnnotation`): Uniform daily granularity for commits, PR opens/merges, reviews, comments. Real event-time `recorded_at`.
  - `Activity Rollup` (`DurationAnnotation`): Precomputed counts across Day, Week, Month, Quarter, Year periods with provenance chains.
  - `Notability Signal` (`NumericAnnotation`): Statistical eventfulness scores with baseline comparison details.
  - `Markdown Narrative`: Paced story documents with a Provenance Appendix tracing back to underlying record IDs.

---

## Getting Started

### Prerequisites
- Python 3.10+
- Fulcra API account and credentials (`~/.config/fulcra/credentials.json`)
- GitHub account access (OAuth device-code flow or active `gh` session / `GITHUB_TOKEN`)

### Quickstart

1. Check Authentication:
   ```bash
   cd app
   python cli.py auth --yes
   ```

2. Run Full Pipeline (Backfill -> Rollups -> Notability -> Summarization -> Narrative):
   ```bash
   python cli.py pipeline --years 1.0 --yes
   ```

3. Individual CLI Commands (still from inside `app/`):
   ```bash
   # Raw Ingestion
   python cli.py backfill --years 1.0 --yes

   # Precompute Rollups & Notability Signals
   python cli.py rollup --years 1.0

   # Preview cross-repo period summarization prompts (no model call)
   python cli.py summarize --years 1.0 --output summarization_handoff.json

   # Generate Narrative Document
   python cli.py narrative --range full --output my_story.md
   ```
   Narrative generation also uploads the markdown automatically to
   `/engineering-journeys/<identity>/<writing-year>/` in your Fulcra file
   store. The filename includes the activity date range and UTC writing date,
   and the CLI prints the exact saved path.

4. For a real, engaging narrative (not templated per-repo one-liners), generate and persist real cross-repo period summaries first, from the **repo root**:
   ```bash
   pip install -e .   # harness deps: anthropic, google-genai, openai
   python scripts/summarize_periods.py --identity <username> --years 1.0
   cd app && python cli.py narrative --range full --output my_story.md
   ```
   This is what `pipeline` runs automatically by default (pass `--skip-real-summarization` to opt out).
   The provider is now checked before pipeline backfill begins. The explicit
   opt-out produces a prominently labelled limited deterministic report, not
   an equivalent quality narrative. Summarization uses title/body evidence
   already stored in Fulcra and persists that evidence projection on rollups,
   so rewriting the story does not require another GitHub fetch.

---

## Testing

Run the test suite:
```bash
python -m pytest
```

Run live integration tests against Fulcra and GitHub APIs:
```bash
RUN_LIVE_TESTS=1 python -m pytest
```
