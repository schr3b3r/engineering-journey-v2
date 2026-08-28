"""Human + machine-readable pipeline progress with one stable JSONL schema."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional


class ProgressReporter:
    def __init__(
        self,
        jsonl_path: Optional[str],
        human_callback: Optional[Callable[[str], None]] = None,
        append: bool = False,
    ) -> None:
        self.path = Path(jsonl_path).expanduser().resolve() if jsonl_path else None
        self.human_callback = human_callback
        self.started_at = time.perf_counter()
        self.stage_started: Dict[str, float] = {}
        self.stage_summaries: Dict[str, Dict[str, Any]] = {}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # A new invocation has one unambiguous event stream. Resume details
            # live in durable run state, not stale local event lines.
            if not append or not self.path.exists():
                self.path.write_text("", encoding="utf-8")
            elif append:
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event") == "stage_completed" and event.get("stage"):
                        self.stage_summaries[event["stage"]] = {
                            key: value
                            for key, value in event.items()
                            if key not in {"event", "stage", "timestamp", "elapsed_seconds", "message"}
                        }

    def human(self, message: str) -> None:
        if self.human_callback:
            self.human_callback(message)

    def emit(self, event: Dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("event", "progress")
        payload.setdefault("stage", "pipeline")
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        payload.setdefault("elapsed_seconds", round(time.perf_counter() - self.started_at, 3))
        if self.path:
            with self.path.open("a", encoding="utf-8") as file_handle:
                file_handle.write(json.dumps(payload, sort_keys=True) + "\n")
                file_handle.flush()


    def start_stage(self, stage: str, message: str) -> None:
        self.stage_started[stage] = time.perf_counter()
        self.human(message)
        self.emit({"event": "stage_started", "stage": stage, "message": message})

    def finish_stage(self, stage: str, message: str, **counts: Any) -> None:
        duration = round(
            time.perf_counter() - self.stage_started.get(stage, self.started_at), 3
        )
        summary = {"duration_seconds": duration, **counts}
        self.stage_summaries[stage] = summary
        self.human(message)
        self.emit(
            {
                "event": "stage_completed",
                "stage": stage,
                "message": message,
                **summary,
            }
        )

    def finish_pipeline(self) -> None:
        self.emit(
            {
                "event": "pipeline_completed",
                "stage": "pipeline",
                "stage_summaries": self.stage_summaries,
            }
        )


def read_progress_events(jsonl_path: str) -> tuple[list[Dict[str, Any]], int]:
    """Read valid events and count malformed/incomplete lines safely."""
    path = Path(jsonl_path).expanduser()
    if not path.exists():
        return [], 0
    events: list[Dict[str, Any]] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            events.append(value)
        else:
            malformed += 1
    return events, malformed


def progress_snapshot(jsonl_path: str) -> Dict[str, Any]:
    """Collapse an event stream into one stable, relay-ready snapshot."""
    events, malformed = read_progress_events(jsonl_path)
    if not events:
        return {
            "status": "waiting",
            "stage": "starting",
            "message": "No progress events have been written yet.",
            "event_count": 0,
            "malformed_lines": malformed,
        }
    latest = events[-1]
    backfill = next(
        (event for event in reversed(events) if "repos_completed" in event), {}
    )
    retry = next(
        (event for event in reversed(events) if event.get("event") == "retry"), None
    )
    completed = next(
        (
            event for event in reversed(events)
            if event.get("event") == "pipeline_completed"
        ),
        None,
    )
    repos_completed = backfill.get("repos_completed")
    repos_total = backfill.get("repos_total")
    percent = None
    if (
        isinstance(repos_completed, (int, float))
        and isinstance(repos_total, (int, float))
        and repos_total
    ):
        percent = round(100 * repos_completed / repos_total, 1)
    return {
        "status": "complete" if completed else "running",
        "stage": latest.get("stage", "pipeline"),
        "event": latest.get("event", "progress"),
        "message": latest.get("message"),
        "elapsed_seconds": latest.get("elapsed_seconds"),
        "repos_completed": repos_completed,
        "repos_total": repos_total,
        "percent_complete": percent,
        "active_repos": backfill.get("active_repos"),
        "records_written": backfill.get("records_written"),
        "rate_repos_per_second": backfill.get("rate_repos_per_second"),
        "eta_seconds": backfill.get("eta_seconds"),
        "current_repository": backfill.get("current_repository"),
        "latest_retry": retry,
        "stage_summaries": completed.get("stage_summaries") if completed else None,
        "event_count": len(events),
        "malformed_lines": malformed,
    }


def _duration(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    seconds = max(0, int(value))
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_progress_status(snapshot: Dict[str, Any]) -> str:
    """Format a concise natural-language line the agent can relay verbatim."""
    if snapshot["status"] == "waiting":
        return "Starting: no progress event has arrived yet."
    if snapshot["status"] == "complete":
        summaries = snapshot.get("stage_summaries") or {}
        stages = ", ".join(
            f"{name} {_duration(details.get('duration_seconds'))}"
            for name, details in summaries.items()
        )
        suffix = f" Stage timings: {stages}." if stages else ""
        return f"Complete after {_duration(snapshot.get('elapsed_seconds'))}.{suffix}"

    parts = [f"Still working — stage: {snapshot['stage']}"]
    if snapshot.get("repos_total") is not None:
        parts.append(
            f"repos {snapshot.get('repos_completed', 0)}/{snapshot['repos_total']}"
            + (
                f" ({snapshot['percent_complete']:.1f}%)"
                if snapshot.get("percent_complete") is not None
                else ""
            )
        )
    if snapshot.get("active_repos") is not None:
        parts.append(f"active {snapshot['active_repos']}")
    if snapshot.get("records_written") is not None:
        parts.append(f"records {snapshot['records_written']}")
    parts.append(f"elapsed {_duration(snapshot.get('elapsed_seconds'))}")
    if snapshot.get("eta_seconds") is not None:
        parts.append(f"ETA {_duration(snapshot['eta_seconds'])}")
    if snapshot.get("current_repository"):
        parts.append(f"current {snapshot['current_repository']}")
    if snapshot.get("latest_retry"):
        retry = snapshot["latest_retry"]
        parts.append(
            f"last retry {retry.get('attempt')}/{retry.get('max_attempts')}: "
            f"{retry.get('error')}"
        )
    if snapshot.get("message"):
        parts.append(str(snapshot["message"]))
    return "; ".join(parts) + "."
