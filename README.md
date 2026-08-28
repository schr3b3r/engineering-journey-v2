# Engineering Journey v2

Turn durable GitHub activity into a grounded, paced Engineering Journey written
by the LLM already running the skill, with deterministic provenance validation
and automatic Fulcra publishing.

## Core architecture

The normal skill workflow does not create a second model client:

1. Python ingests GitHub activity into durable Fulcra records.
2. Python computes rollups/notability and exports a bounded grounded handoff.
3. The LLM already running the skill writes the overview and period prose.
4. Python validates exact period/source completeness, persists summaries,
   assembles the markdown/provenance appendix, and uploads it to Fulcra.

No OpenAI, Anthropic, or Gemini API key is required for agent narration.
External-provider mode remains an explicit standalone alternative only.

Durable Fulcra records include:

- `GitHub Backfill Coverage` source-time durations;
- `GitHub Backfill Progress` operational moments;
- `GitHub Activity Raw` title/body evidence;
- day/week/month/quarter/year `Activity Rollup` records; and
- statistical `Notability Signal` records.

## Hermes direct installation

Hermes direct skill installs bundle `SKILL.md` and referenced support files,
not arbitrary `app/` directories. The skill includes a small referenced
`[scripts/bootstrap_runtime.py](scripts/bootstrap_runtime.py)` helper that
idempotently clones/updates the canonical runtime under
`~/.cache/engineering-journey-v2/` and installs only app dependencies.
The user does not need to clone the repository manually.

## Guided agent quickstart

The full procedure is in `SKILL.md`. In summary:

```bash
# Resolve APP_DIR and PYTHON (use this checkout directly when developing)
python3 scripts/bootstrap_runtime.py

cd "$APP_DIR"

# First call: review account + immutable UTC plan; safe exit in non-TTY mode
$PYTHON cli.py pipeline --years 1.0

# Only after explicit user approval
$PYTHON cli.py pipeline --years 1.0 --yes
```

The approved pipeline prepares durable data and writes a grounded JSON handoff.
The current agent reads it and writes a response JSON matching the included
schema, then publishes:

```bash
$PYTHON cli.py publish-agent-narrative \
  --handoff <handoff.json> \
  --response <agent-response.json>
```

The publisher rejects cross-run, missing-period, duplicate-period, and source-ID
mismatches. On success, it writes a local markdown file and uploads it under:

```text
/engineering-journeys/<identity>/<writing-year>/
```

The filename includes the exact activity range and UTC writing date.

## Rewriting without GitHub

Narration context comes from durable Fulcra records, so a rewrite does not need
another GitHub fetch:

```bash
cd app
python cli.py agent-handoff \
  --identity <username> \
  --since <exact-start-iso> \
  --until <exact-end-iso> \
  --output handoff.json
```

Then have the running agent author the response and call
`publish-agent-narrative`.

## Explicit standalone alternatives

```bash
# Separate provider credentials required only because this mode was requested
python cli.py pipeline --years 1.0 --narration-mode external

# Clearly labelled non-LLM report
python cli.py pipeline --years 1.0 --narration-mode limited
```

## Testing

```bash
cd app
python -m pytest

# Optional live Fulcra/GitHub integrations
RUN_LIVE_TESTS=1 python -m pytest
```
