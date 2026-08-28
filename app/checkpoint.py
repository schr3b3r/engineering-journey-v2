"""Durable backfill coverage and bounded operational progress in Fulcra.

Completed GitHub windows are ``DurationAnnotation`` coverage records at source
time. In-progress cursors are ``MomentAnnotation`` records at update time. The
reader remains compatible with the legacy all-in-one duration type, but no new
legacy records are created.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

LEGACY_CHECKPOINT_ANNOTATION_NAME = "GitHub Backfill Checkpoint"
LEGACY_CHECKPOINT_TAG = "github_backfill_checkpoint"
COVERAGE_ANNOTATION_NAME = "GitHub Backfill Coverage"
COVERAGE_ANNOTATION_TYPE = "duration"
COVERAGE_DESCRIPTION = "Completed GitHub backfill coverage window"
COVERAGE_TAG = "github_backfill_coverage"
PROGRESS_ANNOTATION_NAME = "GitHub Backfill Progress"
PROGRESS_ANNOTATION_TYPE = "moment"
PROGRESS_DESCRIPTION = "Bounded resumable GitHub backfill progress update"
PROGRESS_TAG = "github_backfill_progress"
DEFAULT_PROGRESS_INTERVAL = 100

# Compatibility exports for callers that only need the current durable type.
CHECKPOINT_ANNOTATION_NAME = COVERAGE_ANNOTATION_NAME
CHECKPOINT_ANNOTATION_TYPE = COVERAGE_ANNOTATION_TYPE
CHECKPOINT_DESCRIPTION = COVERAGE_DESCRIPTION
CHECKPOINT_TAG = COVERAGE_TAG


def format_tag(raw: str) -> str:
    """Format a tag string to satisfy Fulcra's <= 30 character limit."""
    if len(raw) <= 30:
        return raw
    return f"{raw[:23]}_{hashlib.sha256(raw.encode()).hexdigest()[:6]}"


def parse_iso(iso_str: str) -> datetime:
    """Parse ISO 8601 into a timezone-aware datetime."""
    if not iso_str:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if iso_str.endswith("Z"):
        return datetime.fromisoformat(iso_str[:-1] + "+00:00")
    dt = datetime.fromisoformat(iso_str)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def format_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Checkpoint:
    """Coverage or progress state for one repository and requested range."""

    repo: str
    github_identity: str
    start_time: str
    end_time: str
    status: str
    cursor: Optional[str] = None
    items_processed: int = 0
    updated_at: Optional[str] = field(default_factory=lambda: format_iso(datetime.now(timezone.utc)))
    record_id: Optional[str] = None
    extra_metadata: Optional[Dict[str, Any]] = None
    record_kind: Optional[str] = None  # coverage | progress | legacy

    def to_note_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "repo": self.repo,
            "github_identity": self.github_identity,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "cursor": self.cursor,
            "items_processed": self.items_processed,
            "updated_at": self.updated_at or format_iso(datetime.now(timezone.utc)),
            "record_kind": self.record_kind or ("coverage" if self.status == "completed" else "progress"),
        }
        if self.extra_metadata:
            data["extra"] = self.extra_metadata
        return data

    def to_note_json(self) -> str:
        return json.dumps(self.to_note_dict())

    @classmethod
    def from_record(cls, record: Dict[str, Any], record_kind: Optional[str] = None) -> "Checkpoint":
        try:
            note_data = json.loads(record.get("note") or "{}")
        except Exception:
            note_data = {}
        rec_at = record.get("recorded_at") or {}
        if isinstance(rec_at, dict):
            rec_start = rec_at.get("start_time") or rec_at.get("value", "")
            rec_end = rec_at.get("end_time") or rec_start
        else:
            rec_start = rec_end = str(rec_at or "")
        return cls(
            repo=note_data.get("repo", ""),
            github_identity=note_data.get("github_identity", ""),
            start_time=note_data.get("start_time") or rec_start,
            end_time=note_data.get("end_time") or rec_end,
            status=note_data.get("status", "in_progress"),
            cursor=note_data.get("cursor"),
            items_processed=note_data.get("items_processed", 0),
            updated_at=note_data.get("updated_at") or (rec_start if record_kind == "progress" else None),
            record_id=record.get("id"),
            extra_metadata=note_data.get("extra"),
            record_kind=record_kind or note_data.get("record_kind"),
        )


class CheckpointManager:
    """Writes semantically distinct coverage durations and progress moments."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._type_info: Dict[str, Dict[str, Any]] = {}

    def _catalog_type(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            return next(
                (ann for ann in self.client.annotations_catalog()
                 if ann.get("deleted_at") is None and ann.get("name") == name),
                None,
            )
        except Exception:
            return None

    def _ensure_type(self, name: str, annotation_type: str, description: str) -> Dict[str, Any]:
        if name in self._type_info:
            return self._type_info[name]
        existing = self._catalog_type(name)
        info = existing or self.client.create_annotation(
            annotation_type=annotation_type, name=name, description=description, tags=[]
        )
        self._type_info[name] = info
        return info

    def ensure_data_type(self) -> Dict[str, Any]:
        """Compatibility API: ensure and return the completed coverage type."""
        return self._ensure_type(COVERAGE_ANNOTATION_NAME, COVERAGE_ANNOTATION_TYPE, COVERAGE_DESCRIPTION)

    def ensure_progress_data_type(self) -> Dict[str, Any]:
        return self._ensure_type(PROGRESS_ANNOTATION_NAME, PROGRESS_ANNOTATION_TYPE, PROGRESS_DESCRIPTION)

    @staticmethod
    def _source_id(info: Dict[str, Any]) -> str:
        return info.get("fulcra_source_id") or f"com.fulcradynamics.annotation.{info.get('id', '')}"

    def save_checkpoint(self, checkpoint: Checkpoint) -> Dict[str, Any]:
        """Persist completed coverage as a duration or progress as an actual-time moment."""
        now = format_iso(datetime.now(timezone.utc))
        checkpoint.updated_at = now
        completed = checkpoint.status == "completed"
        if completed:
            info = self.ensure_data_type()
            tag = COVERAGE_TAG
            data_type = "DurationAnnotation"
            recorded_at: Any = {"start_time": checkpoint.start_time, "end_time": checkpoint.end_time}
            checkpoint.record_kind = "coverage"
        else:
            info = self.ensure_progress_data_type()
            tag = PROGRESS_TAG
            data_type = "MomentAnnotation"
            recorded_at = now
            checkpoint.record_kind = "progress"
        tag_names = [
            format_tag(tag), format_tag(f"repo:{checkpoint.repo}"),
            format_tag(f"github_identity:{checkpoint.github_identity}"),
            format_tag(f"status:{checkpoint.status}"),
        ]
        tag_ids = [tag_info["id"] for tag_info in self.client.create_tags(tag_names)]
        record = {
            "recorded_at": recorded_at,
            "tags": tag_ids,
            "sources": [self._source_id(info), "com.fulcradynamics.cli"],
            "note": checkpoint.to_note_json(),
        }
        response = self.client.record_data_type(data_type, [record], api_version="v1alpha1")
        if completed:
            self._wait_for_checkpoint_visible(checkpoint)
        return response


    def _records_for_type(self, name: str, kind: str) -> List[Checkpoint]:
        info = self._catalog_type(name)
        if not info:
            return []
        source_id = self._source_id(info)
        if kind == "progress":
            records = self.client.moment_annotations(
                start_time="2000-01-01T00:00:00Z", end_time="2100-01-01T00:00:00Z", source=source_id
            )
        else:
            records = self.client.duration_annotations(
                start_time="2000-01-01T00:00:00Z", end_time="2100-01-01T00:00:00Z", source=source_id
            )
        return [Checkpoint.from_record(record, record_kind=kind) for record in records]

    def get_checkpoints(
        self, repo: Optional[str] = None, github_identity: Optional[str] = None,
        start_time: str = "2000-01-01T00:00:00Z", end_time: str = "2100-01-01T00:00:00Z",
    ) -> List[Checkpoint]:
        """Read new semantic records plus legacy records, filtered by described range."""
        checkpoints = (
            self._records_for_type(COVERAGE_ANNOTATION_NAME, "coverage")
            + self._records_for_type(PROGRESS_ANNOTATION_NAME, "progress")
            + self._records_for_type(LEGACY_CHECKPOINT_ANNOTATION_NAME, "legacy")
        )
        filtered = []
        for checkpoint in checkpoints:
            if repo and checkpoint.repo != repo:
                continue
            if github_identity and checkpoint.github_identity != github_identity:
                continue
            if checkpoint.end_time < start_time or checkpoint.start_time > end_time:
                continue
            filtered.append(checkpoint)
        filtered.sort(key=lambda cp: cp.updated_at or cp.start_time, reverse=True)
        return filtered

    def get_latest_checkpoint(
        self, repo: str, github_identity: str,
        start_time: str = "2000-01-01T00:00:00Z", end_time: str = "2100-01-01T00:00:00Z",
    ) -> Optional[Checkpoint]:
        checkpoints = self.get_checkpoints(repo, github_identity, start_time, end_time)
        return checkpoints[0] if checkpoints else None

    def _wait_for_checkpoint_visible(
        self, checkpoint: Checkpoint, max_attempts: int = 6, delay_seconds: float = 0.5
    ) -> bool:
        import time
        for _ in range(max_attempts):
            if any(
                cp.start_time == checkpoint.start_time and cp.end_time == checkpoint.end_time
                and cp.status == checkpoint.status and cp.cursor == checkpoint.cursor
                for cp in self.get_checkpoints(checkpoint.repo, checkpoint.github_identity)
            ):
                return True
            time.sleep(delay_seconds)
        return False

    def get_uncovered_ranges(
        self, repo: str, github_identity: str, start_time: str, end_time: str
    ) -> List[Tuple[str, str]]:
        completed = [
            cp for cp in self.get_checkpoints(repo, github_identity)
            if cp.status == "completed"
        ]
        target_start, target_end = parse_iso(start_time), parse_iso(end_time)
        if target_start >= target_end:
            return []
        intervals = [(target_start, target_end)]
        for checkpoint in completed:
            cp_start, cp_end = parse_iso(checkpoint.start_time), parse_iso(checkpoint.end_time)
            next_intervals = []
            for interval_start, interval_end in intervals:
                if cp_end <= interval_start or cp_start >= interval_end:
                    next_intervals.append((interval_start, interval_end))
                else:
                    if cp_start > interval_start:
                        next_intervals.append((interval_start, cp_start))
                    if cp_end < interval_end:
                        next_intervals.append((cp_end, interval_end))
            intervals = next_intervals
        return [
            (format_iso(interval_start), format_iso(interval_end))
            for interval_start, interval_end in intervals
            if (interval_end - interval_start).total_seconds() >= 1
        ]

    def is_range_covered(self, repo: str, github_identity: str, start_time: str, end_time: str) -> bool:
        return not self.get_uncovered_ranges(repo, github_identity, start_time, end_time)

    def plan_legacy_cleanup(self) -> Dict[str, Any]:
        """Return a non-destructive inventory; never deletes owner data."""
        records = self._records_for_type(LEGACY_CHECKPOINT_ANNOTATION_NAME, "legacy")
        completed = [record for record in records if record.status == "completed"]
        progress = [record for record in records if record.status != "completed"]
        return {
            "legacy_type": LEGACY_CHECKPOINT_ANNOTATION_NAME,
            "record_count": len(records),
            "completed_coverage_candidates": len(completed),
            "obsolete_progress_candidates": len(progress),
            "record_ids": [record.record_id for record in records if record.record_id],
            "destructive_action_taken": False,
            "next_step": (
                "Review this inventory, verify equivalent GitHub Backfill Coverage records, "
                "then separately confirm deletion using an owner-approved Fulcra lifecycle tool."
            ),
        }


class FakeWorkItemProcessor:
    """Simulates bounded checkpoint milestones and graceful kill/resume."""

    def __init__(
        self, checkpoint_manager: CheckpointManager, repo: str, github_identity: str,
        start_time: str, end_time: str, progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
    ) -> None:
        self.mgr = checkpoint_manager
        self.repo = repo
        self.github_identity = github_identity
        self.start_time = start_time
        self.end_time = end_time
        self.progress_interval = max(1, progress_interval)

    def process_items(
        self, items: List[Dict[str, Any]], kill_after_n: Optional[int] = None,
        processed_log: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[int, Optional[Checkpoint]]:
        if processed_log is None:
            processed_log = []
        latest = self.mgr.get_latest_checkpoint(
            self.repo, self.github_identity, self.start_time, self.end_time
        )
        if latest and latest.status == "completed":
            return 0, latest
        last_cursor = latest.cursor if latest else None
        start_index = 0
        if last_cursor is not None:
            start_index = next(
                (index + 1 for index, item in enumerate(items) if str(item["id"]) == str(last_cursor)),
                len(items),
            )
        remaining = items[start_index:]
        processed = 0
        for item in remaining:
            if kill_after_n is not None and processed >= kill_after_n:
                break
            processed_log.append(item)
            processed += 1
            latest = Checkpoint(
                self.repo, self.github_identity, self.start_time, self.end_time,
                "in_progress", str(item["id"]), (latest.items_processed if latest else 0) + 1,
            )
            if processed % self.progress_interval == 0:
                self.mgr.save_checkpoint(latest)
        interrupted = kill_after_n is not None and processed < len(remaining)
        if interrupted and processed and latest:
            self.mgr.save_checkpoint(latest)  # graceful shutdown milestone
        elif remaining and processed == len(remaining):
            latest = Checkpoint(
                self.repo, self.github_identity, self.start_time, self.end_time,
                "completed", str(items[-1]["id"]), len(items),
            )
            self.mgr.save_checkpoint(latest)
        return processed, latest
