# Engineering Journey v2

Ingest a user-provided span of a developer's GitHub activity (from their own authenticated account) into Fulcra as durable, queryable records, and support turning that raw activity into readable output — a paced narrative story with provenance traceability.

---

## Deliverables & Architecture

- **Root `SKILL.md`**: Installable agent skill specification for fresh agent sessions or human users.
- **`app/` Application Code & CLI**: Standalone, directly runnable Python application and CLI (`python -m app.cli` or `python main.py`) with zero hard agent dependencies.
- **Durable Record Architecture in Fulcra**:
  - `GitHub Backfill Checkpoint` (`DurationAnnotation`): Resumable progress tracking.
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
   python -m app.cli auth --yes
   ```

2. Run Full Pipeline (Backfill -> Rollups -> Notability -> Summarization -> Narrative):
   ```bash
   python -m app.cli pipeline --years 1.0 --yes
   ```

3. Individual CLI Commands:
   ```bash
   # Raw Ingestion
   python -m app.cli backfill --years 1.0 --yes

   # Precompute Rollups & Notability Signals
   python -m app.cli rollup --years 1.0

   # Generate Narrative Document
   python -m app.cli narrative --range full --output my_story.md
   ```

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
