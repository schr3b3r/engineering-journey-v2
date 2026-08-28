"""Run-level source-time coverage for clean Timeline semantics."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from checkpoint import (
    COVERAGE_ANNOTATION_NAME as LEGACY_COVERAGE_NAME,
    LEGACY_CHECKPOINT_ANNOTATION_NAME,
    PROGRESS_ANNOTATION_NAME as LEGACY_PROGRESS_NAME,
    CheckpointManager,
    format_iso,
    format_tag,
    parse_iso,
)
from reliability import retry_call

HISTORY_COVERAGE_NAME = "GitHub History Coverage"
HISTORY_COVERAGE_TYPE = "duration"
HISTORY_COVERAGE_DESCRIPTION = (
    "One completed GitHub source-time window for an Engineering Journey run and repository snapshot"
)
HISTORY_COVERAGE_TAG = "github_history_coverage"


@dataclass
class HistoryCoverage:
    run_id: str
    github_identity: str
    start_time: str
    end_time: str
    repositories: List[str] = field(default_factory=list)
    raw_record_count: int = 0
    completed_at: str = field(
        default_factory=lambda: format_iso(datetime.now(timezone.utc))
    )
    record_id: Optional[str] = None

    @property
    def repository_snapshot_hash(self) -> str:
        encoded = "\n".join(sorted(set(self.repositories))).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_note(self) -> str:
        repositories = sorted(set(self.repositories))
        return json.dumps(
            {
                "coverage_kind": "run_repository_snapshot",
                "run_id": self.run_id,
                "github_identity": self.github_identity,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "repositories": repositories,
                "repository_count": len(repositories),
                "repository_snapshot_hash": self.repository_snapshot_hash,
                "raw_record_count": self.raw_record_count,
                "completed_at": self.completed_at,
            }
        )

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "HistoryCoverage":
        note = json.loads(record.get("note") or "{}")
        recorded_at = record.get("recorded_at") or {}
        return cls(
            run_id=note["run_id"],
            github_identity=note["github_identity"],
            start_time=note.get("start_time") or recorded_at.get("start_time", ""),
            end_time=note.get("end_time") or recorded_at.get("end_time", ""),
            repositories=note.get("repositories") or [],
            raw_record_count=int(note.get("raw_record_count", 0)),
            completed_at=note.get("completed_at") or "",
            record_id=record.get("id"),
        )


class HistoryCoverageManager:
    def __init__(
        self,
        client: Any,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.client = client
        self.event_callback = event_callback
        self._type_info: Optional[Dict[str, Any]] = None
        self._cache: Dict[str, List[HistoryCoverage]] = {}

    def _retry_event(self, event: Dict[str, Any]) -> None:
        event["stage"] = "history_coverage"
        if self.event_callback:
            self.event_callback(event)

    def ensure_type(self) -> Dict[str, Any]:
        if self._type_info:
            return self._type_info

        def operation() -> Dict[str, Any]:
            existing = next(
                (
                    annotation
                    for annotation in self.client.annotations_catalog()
                    if annotation.get("deleted_at") is None
                    and annotation.get("name") == HISTORY_COVERAGE_NAME
                ),
                None,
            )
            return existing or self.client.create_annotation(
                annotation_type=HISTORY_COVERAGE_TYPE,
                name=HISTORY_COVERAGE_NAME,
                description=HISTORY_COVERAGE_DESCRIPTION,
                tags=[],
            )

        self._type_info = retry_call(
            operation,
            operation_name="ensure history coverage type",
            on_retry=self._retry_event,
        )
        return self._type_info

    @staticmethod
    def _source_id(info: Dict[str, Any]) -> str:
        return info.get("fulcra_source_id") or f"com.fulcradynamics.annotation.{info['id']}"

    def get_coverages(
        self, github_identity: Optional[str] = None, refresh: bool = False
    ) -> List[HistoryCoverage]:
        cache_key = github_identity or "*"
        if not refresh and cache_key in self._cache:
            return self._cache[cache_key]
        info = self.ensure_type()
        records = retry_call(
            lambda: self.client.duration_annotations(
                start_time="2000-01-01T00:00:00Z",
                end_time="2100-01-01T00:00:00Z",
                source=self._source_id(info),
            ),
            operation_name="query history coverage",
            on_retry=self._retry_event,
        )
        by_run: Dict[str, HistoryCoverage] = {}
        for record in records:
            try:
                coverage = HistoryCoverage.from_record(record)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if github_identity and coverage.github_identity != github_identity:
                continue
            by_run[coverage.run_id] = coverage
        values = sorted(by_run.values(), key=lambda value: value.completed_at, reverse=True)
        self._cache[cache_key] = values
        return values

    def save(self, coverage: HistoryCoverage) -> bool:
        if any(
            existing.run_id == coverage.run_id
            for existing in self.get_coverages(coverage.github_identity, refresh=True)
        ):
            return False
        info = self.ensure_type()

        def operation() -> Any:
            try:
                tags = self.client.create_tags(
                    [
                        format_tag(HISTORY_COVERAGE_TAG),
                        format_tag(f"run:{coverage.run_id}"),
                        format_tag(f"github_identity:{coverage.github_identity}"),
                        format_tag("status:completed"),
                    ]
                )
                return self.client.record_data_type(
                    "DurationAnnotation",
                    [
                        {
                            "recorded_at": {
                                "start_time": coverage.start_time,
                                "end_time": coverage.end_time,
                            },
                            "tags": [tag["id"] for tag in tags],
                            "sources": [
                                self._source_id(info),
                                f"engineering-journey-run:{coverage.run_id}",
                                "com.fulcradynamics.cli",
                            ],
                            "note": coverage.to_note(),
                        }
                    ],
                    api_version="v1alpha1",
                )
            except Exception:
                if any(
                    existing.run_id == coverage.run_id
                    for existing in self.get_coverages(
                        coverage.github_identity, refresh=True
                    )
                ):
                    return {"recovered_after_ambiguous_write": True}
                raise

        retry_call(
            operation,
            operation_name=f"save history coverage {coverage.run_id}",
            on_retry=self._retry_event,
        )
        self._cache.clear()
        return True

    @staticmethod
    def _subtract(
        start_time: str, end_time: str, covered: List[Tuple[str, str]]
    ) -> List[Tuple[str, str]]:
        intervals = [(parse_iso(start_time), parse_iso(end_time))]
        for covered_start, covered_end in covered:
            cover_start, cover_end = parse_iso(covered_start), parse_iso(covered_end)
            next_intervals = []
            for interval_start, interval_end in intervals:
                if cover_end <= interval_start or cover_start >= interval_end:
                    next_intervals.append((interval_start, interval_end))
                else:
                    if cover_start > interval_start:
                        next_intervals.append((interval_start, cover_start))
                    if cover_end < interval_end:
                        next_intervals.append((cover_end, interval_end))
            intervals = next_intervals
        return [
            (format_iso(interval_start), format_iso(interval_end))
            for interval_start, interval_end in intervals
            if (interval_end - interval_start).total_seconds() >= 1
        ]

    def get_uncovered_ranges(
        self,
        repo: str,
        github_identity: str,
        start_time: str,
        end_time: str,
    ) -> List[Tuple[str, str]]:
        run_level = [
            (coverage.start_time, coverage.end_time)
            for coverage in self.get_coverages(github_identity)
            if repo in coverage.repositories
        ]
        # Read old per-repository records only for upgrade compatibility. No
        # canonical writer creates these types anymore.
        legacy = [
            (checkpoint.start_time, checkpoint.end_time)
            for checkpoint in CheckpointManager(
                self.client, event_callback=self.event_callback
            ).get_checkpoints(repo=repo, github_identity=github_identity)
            if checkpoint.status == "completed"
        ]
        return self._subtract(start_time, end_time, run_level + legacy)

    def migration_plan(self) -> Dict[str, Any]:
        checkpoints = CheckpointManager(
            self.client, event_callback=self.event_callback
        ).get_checkpoints()
        groups: Dict[Tuple[str, str, str], set[str]] = {}
        completed_records = 0
        progress_records = 0
        for checkpoint in checkpoints:
            if checkpoint.status == "completed" and checkpoint.repo:
                completed_records += 1
                key = (
                    checkpoint.github_identity,
                    checkpoint.start_time,
                    checkpoint.end_time,
                )
                groups.setdefault(key, set()).add(checkpoint.repo)
            elif checkpoint.status != "completed":
                progress_records += 1
        cohorts = [
            {
                "github_identity": identity,
                "start_time": start,
                "end_time": end,
                "repositories": sorted(repositories),
                "repository_count": len(repositories),
                "migration_run_id": "legacy-"
                + hashlib.sha256(
                    f"{identity}|{start}|{end}".encode()
                ).hexdigest()[:18],
            }
            for (identity, start, end), repositories in sorted(groups.items())
        ]
        catalog = self.client.annotations_catalog()
        legacy_types = [
            {
                "id": annotation.get("id"),
                "name": annotation.get("name"),
            }
            for annotation in catalog
            if annotation.get("deleted_at") is None
            and annotation.get("name")
            in {
                LEGACY_COVERAGE_NAME,
                LEGACY_PROGRESS_NAME,
                LEGACY_CHECKPOINT_ANNOTATION_NAME,
            }
        ]
        return {
            "cohorts": cohorts,
            "legacy_completed_records": completed_records,
            "legacy_progress_records": progress_records,
            "legacy_types": legacy_types,
            "destructive_action_taken": False,
        }

    def migrate_legacy(self) -> Dict[str, int]:
        plan = self.migration_plan()
        created = 0
        existing = 0
        for cohort in plan["cohorts"]:
            coverage = HistoryCoverage(
                run_id=cohort["migration_run_id"],
                github_identity=cohort["github_identity"],
                start_time=cohort["start_time"],
                end_time=cohort["end_time"],
                repositories=cohort["repositories"],
            )
            if self.save(coverage):
                created += 1
            else:
                existing += 1
        return {"created": created, "already_present": existing}

    def delete_legacy_types(self) -> List[str]:
        plan = self.migration_plan()
        deleted = []
        for annotation in plan["legacy_types"]:
            retry_call(
                lambda annotation_id=annotation["id"]: self.client.delete_annotation(
                    annotation_id
                ),
                operation_name=f"delete legacy type {annotation['name']}",
                on_retry=self._retry_event,
            )
            deleted.append(annotation["name"])
        return deleted
