Fix the remaining agent-facing progress UX gap without changing pipeline architecture.

Observed live Hermes behavior: the agent starts pipeline in a background process, repeatedly polls/reads progress JSONL, then waits 60 seconds, but gives the user no natural-language updates. Hermes tool-progress lines are not user communication.

Implement a minimal deterministic `progress-status` CLI command that reads the progress JSONL and prints one concise relay-ready status line (and optional JSON): current stage/event, repos completed/total and percent, active repos, records, elapsed, rate/ETA, current repository/message, latest retry, and whether complete. Handle empty/partial/malformed files safely. Add tests.

Rewrite SKILL.md orchestration rules structurally:
- after launching the managed background process, immediately tell the user it started and where progress lives;
- never call process wait with more than 15 seconds;
- never make two consecutive poll/read/wait tool calls without an intervening user-facing natural-language progress message;
- after each bounded poll, call progress-status (or read only new JSONL lines), compare with the prior snapshot, then communicate a concise update before polling again;
- when no counters changed, still say the process is alive, name current stage/repo, and report elapsed time;
- track offsets/snapshots; never reread the same line range in a loop;
- distinguish tool feed from actual user communication;
- give exact example text/cadence and completion/failure behavior.

Update README and context. Exercise the formatter against representative backfill, heartbeat, retry, stage-complete, and completed streams. Run full tests and commit app changes through harness git_commit.