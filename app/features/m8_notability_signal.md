# Feature: m8_notability_signal

## Status
done

## Description
Implement a first-pass notability/eventfulness formula (volume vs. personal baseline, firsts, focus switches, streaks, quiet periods) as "Notability Signal" records (`NumericAnnotation` base, score in `value`, baseline-comparison detail in `note`).

## Acceptance Criteria
- [x] Custom annotation type "Notability Signal" (`NumericAnnotation` base) created/resolved in Fulcra with `notability_signal` tag.
- [x] Concrete, documented scoring formula in `NotabilityEngine.compute_signals` calculating volume scores vs. baseline mean/std_dev, z-scores, volume ratios, and category flags (`volume_surge`, `high_activity`, `quiet_period`, `first_activity`, `focus_switch`, `streak`).
- [x] Single instant `recorded_at` matching period start timestamp (`start_time`) for chronological ordering with rollups.
- [x] Score stored in `value` field as a float scalar score.
- [x] `note` field contains JSON dictionary payload with detailed baseline statistics, raw activity count, volume ratio, z-score, breakdown by activity type, triggered categories, human-readable explanation, formula version, and sources lineage.
- [x] Filterable tags attached: `notability_signal`, `period_type:<period_type>`, `github_identity:<github_identity>`, `repo:<repo>` (if repo-scoped), and `notability_category:<category>`.
- [x] Signals can be saved to (`save_signals`) and queried back from (`get_signals`) Fulcra matching filters.
- [x] Has automated tests (`tests/test_notability.py`) covering signal computation, serialization, category triggers, custom annotation handling, mock saving/querying, and live Fulcra end-to-end integration, with the full test suite passing.

## Dependencies
- `m1_backfill_checkpoint.md`
- `m3_raw_ingestion.md`
- `m4_multi_repo_backfill.md`
- `m6_activity_rollups.md`

## Notes
- Uses `NumericAnnotation` base type per Architecture decision.
- Score in `value` field, baseline comparison and explanation detail in `note` JSON payload.
- Zero bundled LLM dependencies in the scoring/computation formula (deterministic math).
