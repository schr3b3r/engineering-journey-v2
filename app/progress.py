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
