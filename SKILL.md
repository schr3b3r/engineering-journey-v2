---
name: engineering-journey-v2
description: Ingest developer GitHub activity into Fulcra as durable custom records (raw, rollups, notability signals) and generate paced markdown narratives with provenance appendices.
---

# Engineering Journey v2: Skill Guide

**Engineering Journey v2** ingests a developer's GitHub activity across public and private repositories into **Fulcra** as durable, queryable custom records, computes layered day/week/month/quarter/year activity rollups and notability signals, and generates paced markdown narrative stories with full provenance traceability.

---

## 1. Environment & Prerequisites

**All commands below are run from inside the `app/` directory** (e.g.
`cd app` first) -- this project's modules use flat, sibling-style
imports throughout (`from backfill import ...`, not
`from app.backfill import ...`), consistent with every other module in
this repo, so the CLI must be invoked as `python cli.py ...` /
`python main.py ...` from within `app/`, not `python -m app.cli` from
the repo root.

### Python Dependencies
Ensure Python 3.10+ and the required packages are installed:
```bash
pip install -r requirements.txt
# or: pip install fulcra-api requests pytest
```

### Fulcra Credentials
Fulcra authentication uses the standard SDK configuration. Credentials must exist at `~/.config/fulcra/credentials.json` (or specified via `FULCRA_CREDENTIALS_PATH`):
```bash
cd app
# Verify Fulcra auth status
python cli.py auth
```

### GitHub Credentials
GitHub authentication defaults to browser-based OAuth device-code flow per RFC 8628. If an existing `gh` CLI session or `GITHUB_TOKEN` is present, the CLI asks for explicit user confirmation before using it.

---

## 2. Command-Line Interface (CLI) Usage

The application provides a standalone CLI with zero hard agent dependencies. Run every command below from inside `app/` (`cd app` first).

### Quick Start: Run Full Pipeline
To run the complete sequence (Backfill -> Activity Rollups -> Notability Signals -> Task Prompt Summarization -> Narrative Generation):
```bash
cd app
python cli.py pipeline --years 1.0 --yes
```

### Step-by-Step CLI Commands

#### 1. Authentication Status
```bash
python cli.py auth [--yes] [--device-code]
```

#### 2. Raw GitHub Activity Backfill
Ingests commits, PR opens/merges, PR reviews, and issue/PR comments into "GitHub Activity Raw" (`MomentAnnotation`) records with real event-time `recorded_at` timestamps, existence pre-checks, and durable `CheckpointManager` tracking:
```bash
python cli.py backfill --years 1.0 --identity <username> --yes
```

#### 3. Precompute Activity Rollups & Notability Signals
Aggregates activity counts into day/week/month/quarter/year "Activity Rollup" (`DurationAnnotation`) records and computes statistical baseline comparison "Notability Signal" (`NumericAnnotation`) records:
```bash
python cli.py rollup --years 1.0 --identity <username>
```

#### 4. Rollup Summarization Prompting
Generates structured task prompts for model-driven period summarization write-backs:
```bash
python cli.py summarize --years 1.0 --identity <username>
```

#### 5. Markdown Narrative Generation
Generates a paced narrative story document with a Provenance Appendix for a specified range ("full", "1y", "2024", etc.):
```bash
python cli.py narrative --range full --identity <username> --output my_story.md
```

---

## 3. Programmatic Python API

Every component is available as a clean Python library interface:

```python
from fulcra_client import get_fulcra_client
from github_auth import get_github_auth_token
from backfill import BackfillEngine
from rollups import RollupEngine
from notability import NotabilityEngine
from narrative import NarrativeGenerator, format_narrative_document

# 1. Authenticate
client = get_fulcra_client()
token = get_github_auth_token(auto_accept_existing=True)

# 2. Backfill
backfill_engine = BackfillEngine(client=client, github_token=token)
summary = backfill_engine.run_backfill_for_user(
    github_identity="gklei",
    since="2024-01-01T00:00:00Z",
    until="2025-01-01T00:00:00Z",
)

# 3. Rollups & Notability
rollup_engine = RollupEngine(client=client)
rollup_engine.compute_and_store_rollups("gklei", "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z")

notability_engine = NotabilityEngine(client=client)
notability_engine.compute_and_store_notability_signals("gklei", "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z")

# 4. Narrative
generator = NarrativeGenerator(client=client)
narrative_doc = generator.generate_narrative("gklei", range_selection="1y")
md_content = format_narrative_document(narrative_doc)

with open(narrative_doc.filename, "w") as f:
    f.write(md_content)
```

---

## 4. Architecture & Data Layers in Fulcra

1. **GitHub Backfill Checkpoint** (`DurationAnnotation`): Durable per-repo/identity progress markers tracking completed date windows.
2. **GitHub Activity Raw** (`MomentAnnotation`): Uniform daily granularity records for raw activity items (`commit`, `pr_open`, `pr_merge`, `pr_review`, `issue_comment`, `pr_comment`). Real event time as `recorded_at`.
3. **Activity Rollup** (`DurationAnnotation`): Precomputed counts across Day, Week, Month, Quarter, Year periods with provenance chains (`sources`).
4. **Notability Signal** (`NumericAnnotation`): Statistical eventfulness scores (`value`) with z-scores, volume ratios, and narrative explanations (`note`).
5. **Markdown Narrative & Provenance Appendix**: Paced markdown story documents referencing exact record IDs (`rollup_...`, `notability_...`, `raw:...`) verifiable via `verify_narrative_provenance()`.

---

## 5. Verification & Testing

Run the full pytest test suite to verify installation and functionality:
```bash
python -m pytest
```

To run live integration tests against Fulcra and GitHub:
```bash
RUN_LIVE_TESTS=1 python -m pytest
```
