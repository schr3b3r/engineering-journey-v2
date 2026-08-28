# Feature: m10_packaging.md

## Status
done

## Description
Packaging as an installable, agent-agnostic skill: root-level `SKILL.md` (sibling to `app/` and `harness/`), updated `README.md`, directly-runnable `app/` CLI with no hard agent dependency, and browser-based OAuth device-code GitHub auth with explicit user confirmation for existing `gh` sessions.

## Acceptance Criteria
- [x] Root-level `SKILL.md` exists and defines the skill installation, authentication walkthrough, CLI entry points, and programmatic SDK usage.
- [x] Directly runnable `app/` CLI (`cli.py` / `main.py`) supports `auth`, `backfill`, `rollup`, `summarize`, `narrative`, and `pipeline` subcommands without any agent runtime dependency.
- [x] GitHub authentication defaults to browser-based OAuth device-code flow (RFC 8628), with explicit user confirmation before using an existing `gh` session or `GITHUB_TOKEN` per spec requirement 8.
- [x] Fulcra authentication uses existing `fulcra_client.py` pattern (`get_fulcra_client()`).
- [x] Full end-to-end first-usage workflow validated (authentication check, dry-run backfill, rollups, notability signals, and narrative generation).
- [x] Has automated tests (pytest) covering CLI options, auth detection, device code flow mocking, and narrative file creation, and the FULL test suite passes — see `app/ENGINEERING_STANDARDS.md`.

## Dependencies
- `m1_backfill_checkpoint.md` through `m9_narrative_generation.md`

## Notes
- `SKILL.md` and `README.md` are present both at the repository root and in `app/`.
- CLI provides non-interactive options (`--yes`, `--device-code`, `--dry-run`) for automated harnesses and testing environments.
- Existing GitHub auth presents an explicit use-current/auth-different/cancel choice. Non-interactive sessions fail closed until the agent shows the detected account and exact run plan to the user; `--yes` means that review already happened.
- Backfill/pipeline shows and confirms exact UTC bounds and continuously flushes contextual stage, repository, fetch, ingestion, synthesis, narrative, and upload progress.
