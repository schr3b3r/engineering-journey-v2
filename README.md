# Engineering Journey v2

Turn durable GitHub activity into a grounded, paced Engineering Journey written
by the LLM already running the skill, with deterministic provenance validation
and automatic Fulcra publishing.

## Core architecture

The normal skill workflow does not create a second model client:

1. Python ingests normalized raw GitHub activity into durable Fulcra records.
2. Python adaptively chunks compact raw evidence in memory for the requested range.
3. The LLM already running the skill writes cross-repository trajectory prose.
4. Python validates section/raw-source grounding, assembles provenance, and
   uploads the final artifact—without persisting derived interpretation.

No OpenAI, Anthropic, or Gemini API key is required for agent narration.
External-provider mode remains an explicit standalone alternative only.

Durable Fulcra records include:

- `GitHub History Coverage`: one source-time duration per completed run/window
  and repository snapshot;
- `Engineering Journey Run`: bounded operational stage/repository moments;
- `GitHub Activity Raw` title/body evidence with exact record IDs and GitHub URLs.

Rollups, notability scores, and LLM summaries are not required or persisted by
the canonical workflow. The same raw history can be reinterpreted by future
models and prompts.

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

The publisher rejects modified/cross-run context, malformed chronology, and
missing/duplicate/unknown raw IDs. On success, it writes a local markdown file
and uploads it under:

```text
/engineering-journeys/<identity>/<writing-year>/
```

The filename includes the exact activity range and UTC writing date.

## Reliability and resume

Long runs can emit a machine-readable stream suitable for an agent or
background caller:

```bash
$PYTHON cli.py pipeline --years 1.0 --yes \
  --progress-jsonl engineering_journey_progress.jsonl
```

Events include stable event/stage/timestamp/elapsed fields, repository counts,
records written, rate/ETA, GitHub heartbeats, Fulcra retry attempts, and final
stage timings. Transient DNS/network/429/5xx failures use bounded exponential
backoff. Stable raw fingerprints plus post-error re-query make ambiguous write
retries duplicate-safe.

Agents should not repeatedly parse the raw JSONL. This command produces one
concise status line ready to relay to the user:

```bash
$PYTHON cli.py progress-status --file engineering_journey_progress.jsonl
```

The installed skill requires a user-facing update between every bounded
monitoring cycle, caps process waits at 15 seconds, and explicitly treats tool
feed rows as internal activity rather than communication.

To resume, first run without `--yes` to review the durable saved plan, then run
again after approval:

```bash
$PYTHON cli.py pipeline --resume --identity <username> \
  --progress-jsonl engineering_journey_progress.jsonl

$PYTHON cli.py pipeline --resume --identity <username> --yes \
  --progress-jsonl engineering_journey_progress.jsonl
```

Resume reuses the original immutable window and durable repository list. A run
whose raw stage completed skips GitHub entirely and rebuilds only the ephemeral
narrative handoff.

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
