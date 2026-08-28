"""Durable, bounded pipeline-stage state for exact-window resume."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from checkpoint import format_iso, format_tag
from reliability import retry_call

RUN_ANNOTATION_NAME = "Engineering Journey Run"
RUN_ANNOTATION_TYPE = "moment"
RUN_DESCRIPTION = "Durable immutable window and stage state for resumable Engineering Journey runs"
RUN_TAG = "engineering_journey_run"
TERMINAL_STAGE = "published"


@dataclass
class PipelineRun:
    run_id: str
    github_identity: str
    start_time: str
    end_time: str
    repo: Optional[str]
    stage: str = "planned"
    repositories: List[str] = field(default_factory=list)
    next_repo_index: int = 0
    records_ingested: int = 0
    created_at: str = field(default_factory=lambda: format_iso(datetime.now(timezone.utc)))
    updated_at: str = field(default_factory=lambda: format_iso(datetime.now(timezone.utc)))
    record_id: Optional[str] = None

    @classmethod
    def create(
        cls, github_identity: str, start_time: str, end_time: str, repo: Optional[str]
    ) -> "PipelineRun":
        raw = f"{github_identity}|{start_time}|{end_time}|{repo or 'all'}"
        return cls(
            run_id=hashlib.sha256(raw.encode()).hexdigest()[:24],
            github_identity=github_identity,
            start_time=start_time,
            end_time=end_time,
            repo=repo,
        )

    def to_note(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "github_identity": self.github_identity,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "repo": self.repo,
                "stage": self.stage,
                "repositories": self.repositories,
                "next_repo_index": self.next_repo_index,
                "records_ingested": self.records_ingested,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "PipelineRun":
        data = json.loads(record.get("note") or "{}")
        return cls(
            run_id=data["run_id"],
            github_identity=data["github_identity"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            repo=data.get("repo"),
            stage=data.get("stage", "planned"),
            repositories=data.get("repositories") or [],
            next_repo_index=int(data.get("next_repo_index", 0)),
            records_ingested=int(data.get("records_ingested", 0)),
            created_at=data.get("created_at") or "",
            updated_at=data.get("updated_at") or "",
            record_id=record.get("id"),
        )


class PipelineRunManager:
    def __init__(
        self,
        client: Any,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.client = client
        self.event_callback = event_callback
        self._type_info: Optional[Dict[str, Any]] = None

    def _retry_event(self, event: Dict[str, Any]) -> None:
        event["stage"] = "run_state"
        if self.event_callback:
            self.event_callback(event)

    def ensure_type(self) -> Dict[str, Any]:
        if self._type_info:
            return self._type_info

        def operation() -> Dict[str, Any]:
            for annotation in self.client.annotations_catalog():
                if (
                    annotation.get("deleted_at") is None
                    and annotation.get("name") == RUN_ANNOTATION_NAME
                ):
                    return annotation
            return self.client.create_annotation(
                annotation_type=RUN_ANNOTATION_TYPE,
                name=RUN_ANNOTATION_NAME,
                description=RUN_DESCRIPTION,
                tags=[],
            )

        self._type_info = retry_call(
            operation,
            operation_name="ensure pipeline run type",
            on_retry=self._retry_event,
        )
        return self._type_info

    @staticmethod
    def _source_id(info: Dict[str, Any]) -> str:
        return info.get("fulcra_source_id") or f"com.fulcradynamics.annotation.{info['id']}"

    def save(self, run: PipelineRun) -> None:
        run.updated_at = format_iso(datetime.now(timezone.utc))
        info = self.ensure_type()

        def operation() -> Any:
            tags = self.client.create_tags(
                [
                    format_tag(RUN_TAG),
                    format_tag(f"run:{run.run_id}"),
                    format_tag(f"github_identity:{run.github_identity}"),
                    format_tag(f"stage:{run.stage}"),
                ]
            )
            return self.client.record_data_type(
                "MomentAnnotation",
                [
                    {
                        "recorded_at": run.updated_at,
                        "tags": [tag["id"] for tag in tags],
                        "sources": [self._source_id(info), "com.fulcradynamics.cli"],
                        "note": run.to_note(),
                    }
                ],
                api_version="v1alpha1",
            )

        retry_call(
            operation,
            operation_name=f"save pipeline run {run.run_id} stage {run.stage}",
            on_retry=self._retry_event,
        )

    def get_runs(self, github_identity: Optional[str] = None) -> List[PipelineRun]:
        info = self.ensure_type()

        def operation() -> List[Dict[str, Any]]:
            return self.client.moment_annotations(
                start_time="2000-01-01T00:00:00Z",
                end_time="2100-01-01T00:00:00Z",
                source=self._source_id(info),
            )

        records = retry_call(
            operation,
            operation_name="query pipeline run state",
            on_retry=self._retry_event,
        )
        latest: Dict[str, PipelineRun] = {}
        for record in records:
            try:
                run = PipelineRun.from_record(record)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if github_identity and run.github_identity != github_identity:
                continue
            existing = latest.get(run.run_id)
            if existing is None or run.updated_at > existing.updated_at:
                latest[run.run_id] = run
        return sorted(latest.values(), key=lambda run: run.updated_at, reverse=True)

    def latest_incomplete(
        self, github_identity: str, repo: Optional[str]
    ) -> Optional[PipelineRun]:
        return next(
            (
                run
                for run in self.get_runs(github_identity)
                if run.repo == repo and run.stage != TERMINAL_STAGE
            ),
            None,
        )
