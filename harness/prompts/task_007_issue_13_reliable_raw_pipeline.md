Address GitHub issue #13 on top of the issue #14 raw-history architecture, without reintroducing rollup/notability complexity.

Canonical agent mode has only durable raw backfill, raw evidence handoff, running-agent prose, and deterministic artifact publishing. Build reliability for that path:

- one immutable pipeline run identity/window resolved once and reused on resume;
- a small durable Fulcra run-state record with discovered repository list and stage (`planned`, `repos_discovered`, `raw_complete`, `handoff_complete`), plus bounded repository progress milestones;
- `--resume` that finds the latest incomplete run for the confirmed identity/repo scope, displays the resume plan, reuses its exact window/repo list, skips GitHub discovery, and skips raw backfill entirely when already complete;
- machine-readable JSONL progress written to a predictable path and human output, with stage, counts, elapsed seconds, rate/ETA where meaningful, retry events, and final stage timings/counts;
- bounded exponential backoff+jitter for transient Fulcra operations in canonical raw ingestion, run-state writes, queries where appropriate, and final file upload;
- ambiguity-safe raw retries: stable raw fingerprints/IDs, precheck existing durable items, and after a write exception re-query before retry so a committed-but-response-lost write cannot duplicate;
- integration-style tests interrupt during raw ingestion and after raw completion, inject transient Fulcra failures, resume with GitHub unavailable, and assert no duplicate raw records, no rediscovery, identical time window, successful handoff, bounded events, and exact progress JSON schema.

Remove issue #13 acceptance tied only to retired canonical rollup/notability stages from the implementation scope; document those as legacy-only. Keep the implementation focused and use optional callbacks/utilities instead of broad framework abstractions. Run full tests and commit app changes via harness git_commit.