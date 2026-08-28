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
Ensure Python 3.10+ and the required packages are installed. This repo
has two separate dependency sets, matching the app/harness boundary
described in section 2 (Rollup Summarization) below:
```bash
# app/'s own deps (zero LLM SDKs -- see app/features/m7_rollup_summarization.md)
pip install -r app/requirements.txt

# harness deps (needed only for scripts/summarize_periods.py's real
# model calls -- includes anthropic/google-genai/openai)
pip install -e .
```

### Fulcra Credentials
Fulcra authentication uses the standard SDK configuration. Credentials must exist at `~/.config/fulcra/credentials.json` (or specified via `FULCRA_CREDENTIALS_PATH`):
```bash
cd app
# Verify Fulcra auth status
python cli.py auth
```

**Known issue on first-ever login:** `fulcra-api auth login --get-auth-url`
can throw an unhandled `FileNotFoundError` if `~/.config/fulcra/` does not
already exist yet (the `fulcra-api` SDK's CLI creates it with `os.mkdir()`
instead of `os.makedirs(..., exist_ok=True)` -- a bug in the `fulcra-api`
PyPI package itself, not this repo). If you hit this on a fresh machine,
create the directory yourself first as a workaround:
```bash
mkdir -p ~/.config/fulcra
fulcra-api auth login --get-auth-url
```

### GitHub Credentials
GitHub authentication defaults to browser-based OAuth device-code flow per RFC 8628. If an existing `gh` CLI session or `GITHUB_TOKEN` is present, the CLI asks for explicit user confirmation before using it.

**Cloud-sandboxed/agent environments should default to a PAT, not the
device-code flow.** GitHub's anti-abuse rules appear to block OAuth
device-flow token issuance (`/login/device/code`,
`/login/oauth/access_token`) from datacenter/cloud-provider IP ranges --
confirmed against a GCP-hosted sandbox, where every other GitHub API
endpoint responded normally but those two specifically returned a bare
`404 Not Found` with no explanation. If you're running this from an
agent sandbox, CI runner, or any other cloud-hosted environment (rather
than an interactive session on a personal machine), skip
`--device-code` entirely and set `GITHUB_TOKEN` to a
[Personal Access Token](https://github.com/settings/tokens) with `repo`
and `read:user` scopes instead -- `get_github_auth_token()` detects this and
shows the account for confirmation without starting device-code auth:
```bash
export GITHUB_TOKEN=ghp_your_token_here
python cli.py auth
```
A generic 404 with no other symptoms when the device-code flow otherwise
looks correctly implemented is the signature of this: it is not a bug
in this repo's `github_auth.py`, and retrying/debugging the network
request will not resolve it from that kind of environment.

### Required interaction protocol for agents

Do not start with `--yes`. First run `auth`, `backfill`, or `pipeline` without
it. In a terminal, the CLI lets the user use the detected account,
authenticate differently, or cancel, then shows the exact activity range and
run plan with a safe default of No. In a non-interactive agent shell, the CLI
prints the detected account and complete proposed plan, exits without doing
work, and instructs the agent to ask the human.

Only after the human explicitly approves both the account and the displayed
plan may the agent rerun the same command with `--yes`. If they want another
account, use `--device-code` (or have them replace the PAT/session in a cloud
environment). Never infer approval from an earlier conversation or silently
reuse an account.

Long pipeline commands must be run with streaming output, or as a managed
background process that is polled frequently. Relay stage/repository/count
updates to the user as they appear; do not block silently until completion.
The CLI flushes contextual progress for discovery, each repository/subrange,
GitHub fetch categories, ingestion milestones, rollups, LLM periods,
narrative generation, and Fulcra upload.

---

## 2. Command-Line Interface (CLI) Usage

The application provides a standalone CLI with zero hard agent dependencies. Run every command below from inside `app/` (`cd app` first).

### Quick Start: Run Full Pipeline
To run the complete sequence (Backfill -> Activity Rollups -> Notability Signals -> real cross-repo Summarization -> Narrative Generation):
```bash
cd app
python cli.py pipeline --years 1.0
```

### Step-by-Step CLI Commands

#### 1. Authentication Status
```bash
python cli.py auth [--yes] [--device-code]
```

#### 2. Raw GitHub Activity Backfill
Ingests commits, PR opens/merges, PR reviews, and issue/PR comments into "GitHub Activity Raw" (`MomentAnnotation`) records with real event-time `recorded_at` timestamps, existence pre-checks, and durable `CheckpointManager` tracking:
```bash
python cli.py backfill --years 1.0 --identity <username>
```

#### 3. Precompute Activity Rollups & Notability Signals
Aggregates activity counts into day/week/month/quarter/year "Activity Rollup" (`DurationAnnotation`) records and computes statistical baseline comparison "Notability Signal" (`NumericAnnotation`) records:
```bash
python cli.py rollup --years 1.0 --identity <username>
```

#### 4. Rollup Summarization
This is a two-step process, deliberately split because `app/` code has
zero LLM provider SDK dependencies (see
`app/features/m7_rollup_summarization.md`) -- the real model call
happens in harness-side tooling, not application code.

**4a. Preview the prompts** (no model call, no write-back; writes the
full cross-repo period prompt set to a JSON file for inspection):
```bash
python cli.py summarize --years 1.0 --identity <username> --output summarization_handoff.json
```

**4b. Generate and persist real summaries** (calls a real model via
`harness/providers/` -- Anthropic OAuth-preferred, or Gemini/OpenAI --
and writes the results back to each period's rollups in Fulcra). Run
this from the **repo root**, not `app/`:
```bash
python scripts/summarize_periods.py --identity <username> --years 1.0
```
This groups month/quarter/year rollups by period window ACROSS repositories
and gives the model grounded titles, body excerpts, and traceable raw source
IDs—not merely repository names and counts. Legacy rollups are rehydrated from
durable Fulcra raw records, so no GitHub refetch is needed solely to rewrite
the story. The hierarchy provides both period detail and trajectory synthesis.
Pass `--provider anthropic|gemini|openai` to force a specific provider,
or `--dry-run` to see how many period groups would be summarized
without calling a model.

#### 5. Markdown Narrative Generation
Generates a paced narrative story document with a Provenance Appendix for a specified range ("full", "1y", "2024", etc.). If step 4b was run first, periods with a real written-back summary render as one consolidated cross-repo paragraph. Without those summaries, output is compact and prominently labelled as a limited deterministic fallback—not presented as an equivalent narrative:
```bash
python cli.py narrative --range full --identity <username> --output my_story.md
```
Every generated narrative is also saved automatically through the Fulcra SDK
under `/engineering-journeys/<identity>/<writing-year>/`; its filename includes
the activity start/end dates and UTC writing date. The CLI prints the exact
Fulcra path, so no follow-up request to an agent is required.

**Recommended:** just run the full pipeline, which invokes step 4b automatically:
```bash
python cli.py pipeline --years 1.0
```
The pipeline checks model credentials before starting a long backfill. Pass
`--skip-real-summarization` only to explicitly accept limited fallback output,
or `--provider anthropic|gemini|openai` to force one.

---

## 3. Programmatic Python API

Every component is available as a clean Python library interface (`app/`'s own modules never import an LLM SDK; the model call for summarization lives in `scripts/summarize_periods.py`, which imports both `app/`'s data layer and `harness/providers/`):

```python
from fulcra_client import get_fulcra_client
from github_auth import get_github_auth_token
from backfill import BackfillEngine
from github_spike import GitHubAPISpike
from raw_ingestion import RawActivityIngestor
from rollups import RollupEngine
from notability import NotabilityEngine
from summarization import RollupSummarizer
from narrative import NarrativeGenerator

# 1. Authenticate
client = get_fulcra_client()
token = get_github_auth_token(auto_accept_existing=True)

# 2. Backfill
spike = GitHubAPISpike(token=token)
backfill_engine = BackfillEngine(fulcra_client=client, github_api=spike)
summary = backfill_engine.run_backfill(
    github_identity="gklei",
    start_time="2024-01-01T00:00:00Z",
    end_time="2025-01-01T00:00:00Z",
)

# 3. Rollups & Notability
raw_ingestor = RawActivityIngestor(client=client)
raw_items = raw_ingestor.get_raw_activities(github_identity="gklei")
rollup_engine = RollupEngine(client=client)
rollups_by_period = rollup_engine.generate_all_rollups(raw_items, "gklei", save_to_fulcra=True)
all_rollups = [r for period_rollups in rollups_by_period.values() for r in period_rollups]

notability_engine = NotabilityEngine(client=client)
signals = notability_engine.compute_signals(all_rollups)
notability_engine.save_signals(signals)

# 4. Cross-repo period summarization (real model call -- see
#    scripts/summarize_periods.py for the harness.providers wiring;
#    app/ itself only defines the callback shape, never an SDK import)
summarizer = RollupSummarizer(client=client)
summarizer.summarize_periods_and_write_back(
    all_rollups,
    summary_provider_fn=my_real_model_call_fn,  # Callable[[str], str]
)

# 5. Narrative
generator = NarrativeGenerator(client=client)
doc_content, filename, used_rollups, used_signals = generator.generate_narrative(
    "gklei", range_selection="1y",
)
with open(filename, "w") as f:
    f.write(doc_content)
```

---

## 4. Architecture & Data Layers in Fulcra

1. **GitHub Backfill Coverage** (`DurationAnnotation`): Durable completed per-repo/identity source-time windows; **GitHub Backfill Progress** (`MomentAnnotation`) stores bounded operational cursor milestones at update time.
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
