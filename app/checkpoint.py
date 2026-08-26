"""GitHub Backfill Checkpoint management.

Provides the 'GitHub Backfill Checkpoint' custom record type (DurationAnnotation base)
and helper methods for saving, reading, and evaluating backfill progress across
repos and identities.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple
from fulcra_api.core import FulcraAPI

CHECKPOINT_ANNOTATION_NAME = "GitHub Backfill Checkpoint"
CHECKPOINT_ANNOTATION_TYPE = "duration"
CHECKPOINT_DESCRIPTION = "Resumable backfill progress marker"
CHECKPOINT_TAG = "github_backfill_checkpoint"


def format_tag(raw: str) -> str:
    """Format a tag string to satisfy Fulcra's <= 30 character limit.

    If the string exceeds 30 characters, truncates and appends a deterministic SHA256
    hash snippet to preserve uniqueness.
    """
    if len(raw) <= 30:
        return raw
    hash_suffix = hashlib.sha256(raw.encode()).hexdigest()[:6]
    return f"{raw[:23]}_{hash_suffix}"


def parse_iso(iso_str: str) -> datetime:
    """Parse ISO 8601 string into a UTC datetime."""
    if not iso_str:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if iso_str.endswith("Z"):
        return datetime.fromisoformat(iso_str[:-1] + "+00:00")
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_iso(dt: datetime) -> str:
    """Format UTC datetime into ISO 8601 string with 'Z' suffix."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Checkpoint:
    """Represents a backfill checkpoint for a single repo and identity."""

    repo: str
    github_identity: str
    start_time: str  # ISO 8601 string, e.g. "2025-01-01T00:00:00Z"
    end_time: str  # ISO 8601 string, e.g. "2025-12-31T23:59:59Z"
    status: str  # "in_progress" | "completed"
    cursor: Optional[str] = None
    items_processed: int = 0
    updated_at: Optional[str] = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    record_id: Optional[str] = None
    extra_metadata: Optional[Dict[str, Any]] = None

    def to_note_dict(self) -> Dict[str, Any]:
        """Convert checkpoint state into a dict suitable for JSON serialization in `note`."""
        data: Dict[str, Any] = {
            "repo": self.repo,
            "github_identity": self.github_identity,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "cursor": self.cursor,
            "items_processed": self.items_processed,
            "updated_at": self.updated_at
            or datetime.now(timezone.utc).isoformat(),
        }
        if self.extra_metadata:
            data["extra"] = self.extra_metadata
        return data

    def to_note_json(self) -> str:
        """Convert checkpoint state into a JSON string for the `note` field."""
        return json.dumps(self.to_note_dict())

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "Checkpoint":
        """Reconstruct a Checkpoint object from a raw Fulcra DurationAnnotation record."""
        note_str = record.get("note") or "{}"
        try:
            note_data = json.loads(note_str)
        except Exception:
            note_data = {}

        rec_at = record.get("recorded_at") or {}
        start_time = note_data.get("start_time") or rec_at.get("start_time", "")
        end_time = note_data.get("end_time") or rec_at.get("end_time", "")

        return cls(
            repo=note_data.get("repo", ""),
            github_identity=note_data.get("github_identity", ""),
            start_time=start_time,
            end_time=end_time,
            status=note_data.get("status", "in_progress"),
            cursor=note_data.get("cursor"),
            items_processed=note_data.get("items_processed", 0),
            updated_at=note_data.get("updated_at"),
            record_id=record.get("id"),
            extra_metadata=note_data.get("extra"),
        )


class CheckpointManager:
    """Manages reading, writing, and querying GitHub Backfill Checkpoints in Fulcra."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._type_info: Optional[Dict[str, Any]] = None

    def ensure_data_type(self) -> Dict[str, Any]:
        """Ensure the 'GitHub Backfill Checkpoint' custom annotation type exists."""
        if self._type_info:
            return self._type_info

        try:
            catalog = self.client.annotations_catalog()
            for ann in catalog:
                if (
                    ann.get("deleted_at") is None
                    and ann.get("name") == CHECKPOINT_ANNOTATION_NAME
                ):
                    self._type_info = ann
                    return ann
        except Exception:
            pass

        created = self.client.create_annotation(
            annotation_type=CHECKPOINT_ANNOTATION_TYPE,
            name=CHECKPOINT_ANNOTATION_NAME,
            description=CHECKPOINT_DESCRIPTION,
            tags=[],
        )
        self._type_info = created
        return created

    def save_checkpoint(self, checkpoint: Checkpoint) -> Dict[str, Any]:
        """Save a checkpoint record into Fulcra."""
        type_info = self.ensure_data_type()
        type_id = type_info.get("id", "")
        type_source_id = (
            type_info.get("fulcra_source_id")
            or f"com.fulcradynamics.annotation.{type_id}"
        )

        tag_names = [
            format_tag(CHECKPOINT_TAG),
            format_tag(f"repo:{checkpoint.repo}"),
            format_tag(f"github_identity:{checkpoint.github_identity}"),
            format_tag(f"status:{checkpoint.status}"),
        ]
        resolved_tags = self.client.create_tags(tag_names)
        tag_ids = [t["id"] for t in resolved_tags]

        if not checkpoint.updated_at:
            checkpoint.updated_at = datetime.now(timezone.utc).isoformat()

        record = {
            "recorded_at": {
                "start_time": checkpoint.start_time,
                "end_time": checkpoint.end_time,
            },
            "tags": tag_ids,
            "sources": [type_source_id, "com.fulcradynamics.cli"],
            "note": checkpoint.to_note_json(),
        }

        resp = self.client.record_data_type(
            "DurationAnnotation", [record], api_version="v1alpha1"
        )
        return resp

    def get_checkpoints(
        self,
        repo: Optional[str] = None,
        github_identity: Optional[str] = None,
        start_time: str = "2000-01-01T00:00:00Z",
        end_time: str = "2100-01-01T00:00:00Z",
    ) -> List[Checkpoint]:
        """Retrieve checkpoints for the given repo/identity and time range."""
        required_tag_names = [format_tag(CHECKPOINT_TAG)]
        if repo:
            required_tag_names.append(format_tag(f"repo:{repo}"))
        if github_identity:
            required_tag_names.append(
                format_tag(f"github_identity:{github_identity}")
            )

        resolved_tags = self.client.create_tags(required_tag_names)
        required_tag_ids = set(t["id"] for t in resolved_tags)

        records = self.client.duration_annotations(
            start_time=start_time, end_time=end_time
        )
        checkpoints: List[Checkpoint] = []

        for rec in records:
            rec_tags = set(rec.get("tags") or [])
            if required_tag_ids.issubset(rec_tags):
                cp = Checkpoint.from_record(rec)
                if repo and cp.repo and cp.repo != repo:
                    continue
                if (
                    github_identity
                    and cp.github_identity
                    and cp.github_identity != github_identity
                ):
                    continue
                checkpoints.append(cp)

        checkpoints.sort(
            key=lambda c: c.updated_at or c.start_time or "", reverse=True
        )
        return checkpoints

    def get_latest_checkpoint(
        self,
        repo: str,
        github_identity: str,
        start_time: str = "2000-01-01T00:00:00Z",
        end_time: str = "2100-01-01T00:00:00Z",
    ) -> Optional[Checkpoint]:
        """Retrieve the most recent checkpoint for a given repo and identity."""
        checkpoints = self.get_checkpoints(
            repo=repo,
            github_identity=github_identity,
            start_time=start_time,
            end_time=end_time,
        )
        return checkpoints[0] if checkpoints else None

    def get_uncovered_ranges(
        self,
        repo: str,
        github_identity: str,
        start_time: str,
        end_time: str,
    ) -> List[Tuple[str, str]]:
        """Return a list of (start_time, end_time) ISO string tuples representing sub-ranges
        within [start_time, end_time] that are not covered by any completed checkpoint for
        this repo and identity.
        """
        checkpoints = self.get_checkpoints(repo=repo, github_identity=github_identity)
        completed = [c for c in checkpoints if c.status == "completed"]

        target_start = parse_iso(start_time)
        target_end = parse_iso(end_time)

        if target_start >= target_end:
            return []

        intervals: List[Tuple[datetime, datetime]] = [(target_start, target_end)]

        for cp in completed:
            if not cp.start_time or not cp.end_time:
                continue
            cp_start = parse_iso(cp.start_time)
            cp_end = parse_iso(cp.end_time)

            next_intervals: List[Tuple[datetime, datetime]] = []
            for s, e in intervals:
                if cp_end <= s or cp_start >= e:
                    # No overlap
                    next_intervals.append((s, e))
                else:
                    # Overlap: keep parts before cp_start and after cp_end
                    if cp_start > s:
                        next_intervals.append((s, cp_start))
                    if cp_end < e:
                        next_intervals.append((cp_end, e))
            intervals = next_intervals

        # Format remaining non-empty intervals back to ISO strings
        res: List[Tuple[str, str]] = []
        for s, e in intervals:
            if (e - s).total_seconds() >= 1:
                res.append((format_iso(s), format_iso(e)))

        return res

    def is_range_covered(
        self,
        repo: str,
        github_identity: str,
        start_time: str,
        end_time: str,
    ) -> bool:
        """Check if a date range for a repo and identity is covered by completed checkpoint(s)."""
        uncovered = self.get_uncovered_ranges(
            repo=repo,
            github_identity=github_identity,
            start_time=start_time,
            end_time=end_time,
        )
        return len(uncovered) == 0


class FakeWorkItemProcessor:
    """Processes fake items with checkpointing to simulate interruptible/resumable backfills."""

    def __init__(
        self,
        checkpoint_manager: CheckpointManager,
        repo: str,
        github_identity: str,
        start_time: str,
        end_time: str,
    ) -> None:
        self.mgr = checkpoint_manager
        self.repo = repo
        self.github_identity = github_identity
        self.start_time = start_time
        self.end_time = end_time

    def process_items(
        self,
        items: List[Dict[str, Any]],
        kill_after_n: Optional[int] = None,
        processed_log: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[int, Optional[Checkpoint]]:
        """Process items, saving checkpoints after each item.

        If `kill_after_n` is set, simulates process termination after processing
        that many unprocessed items.
        """
        if processed_log is None:
            processed_log = []

        latest_cp = self.mgr.get_latest_checkpoint(
            self.repo, self.github_identity
        )
        last_cursor = latest_cp.cursor if latest_cp else None

        remaining_items = items
        if last_cursor is not None:
            start_index = len(items)
            for idx, item in enumerate(items):
                if str(item["id"]) == str(last_cursor):
                    start_index = idx + 1
                    break
            remaining_items = items[start_index:]

        newly_processed = 0
        for item in remaining_items:
            if kill_after_n is not None and newly_processed >= kill_after_n:
                break

            processed_log.append(item)
            newly_processed += 1
            item_id_str = str(item["id"])

            prev_processed = latest_cp.items_processed if latest_cp else 0
            cp = Checkpoint(
                repo=self.repo,
                github_identity=self.github_identity,
                start_time=self.start_time,
                end_time=self.end_time,
                status="in_progress",
                cursor=item_id_str,
                items_processed=prev_processed + 1,
            )
            self.mgr.save_checkpoint(cp)
            latest_cp = cp

        if remaining_items and newly_processed == len(remaining_items):
            prev_processed = latest_cp.items_processed if latest_cp else len(items)
            cp = Checkpoint(
                repo=self.repo,
                github_identity=self.github_identity,
                start_time=self.start_time,
                end_time=self.end_time,
                status="completed",
                cursor=str(items[-1]["id"]),
                items_processed=prev_processed,
            )
            self.mgr.save_checkpoint(cp)
            latest_cp = cp

        return newly_processed, latest_cp
