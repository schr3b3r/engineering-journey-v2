"""Raw GitHub Activity ingestion into Fulcra.

Manages registering the "GitHub Activity Raw" custom data type (MomentAnnotation base per architecture.md),
ingesting raw activity items with real event time recorded_at, filterable tags, and lineage sources,
wired into M1's Checkpoint mechanism for resumability.
"""

from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional, Tuple

from checkpoint import (
    DEFAULT_PROGRESS_INTERVAL,
    Checkpoint,
    CheckpointManager,
    format_tag,
)
from github_spike import GitHubActivityItem

RAW_ACTIVITY_ANNOTATION_NAME = "GitHub Activity Raw"
RAW_ACTIVITY_ANNOTATION_TYPE = "moment"
RAW_ACTIVITY_DESCRIPTION = "Raw GitHub activity item"
RAW_ACTIVITY_TAG = "github_activity_raw"


def activity_item_to_note_dict(item: GitHubActivityItem) -> Dict[str, Any]:
    """Convert a GitHubActivityItem to a dict for the `note` JSON payload."""
    return {
        "activity_type": item.activity_type,
        "repo": item.repo,
        "github_identity": item.github_identity,
        "item_id": item.item_id,
        "event_timestamp": item.event_timestamp,
        "title_or_summary": item.title_or_summary,
        "url": item.url,
        "raw_payload": item.raw_payload,
    }


def activity_item_from_record(record: Dict[str, Any]) -> GitHubActivityItem:
    """Reconstruct a GitHubActivityItem from a raw Fulcra MomentAnnotation record."""
    rec_at = record.get("recorded_at")
    if isinstance(rec_at, dict):
        event_ts = rec_at.get("value") or rec_at.get("start_time", "")
    else:
        event_ts = str(rec_at or "")

    note_str = record.get("note") or "{}"
    try:
        note_data = json.loads(note_str)
    except Exception:
        note_data = {}

    return GitHubActivityItem(
        activity_type=note_data.get("activity_type", ""),
        repo=note_data.get("repo", ""),
        github_identity=note_data.get("github_identity", ""),
        item_id=note_data.get("item_id", ""),
        event_timestamp=note_data.get("event_timestamp") or event_ts,
        title_or_summary=note_data.get("title_or_summary", ""),
        url=note_data.get("url", ""),
        raw_payload=note_data.get("raw_payload") or {},
    )


class RawActivityIngestor:
    """Ingests raw GitHub activity items into Fulcra with durable checkpointing."""

    def __init__(
        self, client: Any, progress_interval: int = DEFAULT_PROGRESS_INTERVAL
    ) -> None:
        self.client = client
        self.checkpoint_manager = CheckpointManager(client)
        self.progress_interval = max(1, progress_interval)
        self._type_info: Optional[Dict[str, Any]] = None
        self._tag_cache: Dict[str, str] = {}

    def ensure_data_type(self) -> Dict[str, Any]:
        """Ensure the 'GitHub Activity Raw' custom annotation type exists in Fulcra."""
        if self._type_info:
            return self._type_info

        try:
            catalog = self.client.annotations_catalog()
            for ann in catalog:
                if (
                    ann.get("deleted_at") is None
                    and ann.get("name") == RAW_ACTIVITY_ANNOTATION_NAME
                ):
                    self._type_info = ann
                    return ann
        except Exception:
            pass

        created = self.client.create_annotation(
            annotation_type=RAW_ACTIVITY_ANNOTATION_TYPE,
            name=RAW_ACTIVITY_ANNOTATION_NAME,
            description=RAW_ACTIVITY_DESCRIPTION,
            tags=[],
        )
        self._type_info = created
        return created

    def _resolve_tag_ids(self, tag_names: List[str]) -> List[str]:
        """Resolve tag names to tag UUIDs using local cache and client.create_tags."""
        missing = [t for t in tag_names if t not in self._tag_cache]
        if missing:
            resolved = self.client.create_tags(missing)
            for item in resolved:
                name = item.get("name")
                tag_id = item.get("id")
                if name and tag_id:
                    self._tag_cache[name] = tag_id

        return [self._tag_cache[t] for t in tag_names if t in self._tag_cache]

    def ingest_items(
        self,
        items: List[GitHubActivityItem],
        repo: str,
        github_identity: str,
        start_time: str,
        end_time: str,
        kill_after_n: Optional[int] = None,
        processed_log: Optional[List[GitHubActivityItem]] = None,
    ) -> Tuple[int, Optional[Checkpoint]]:
        """Ingest activity items into Fulcra, updating checkpoints as progress is made.

        If `kill_after_n` is specified, simulates termination after processing
        that many unprocessed items.
        Returns (number of items newly ingested, latest Checkpoint).
        """
        if processed_log is None:
            processed_log = []

        type_info = self.ensure_data_type()
        type_id = type_info.get("id", "")
        type_source_id = (
            type_info.get("fulcra_source_id")
            or f"com.fulcradynamics.annotation.{type_id}"
        )

        latest_cp = self.checkpoint_manager.get_latest_checkpoint(
            repo=repo,
            github_identity=github_identity,
            start_time=start_time,
            end_time=end_time,
        )

        # BUG FIX (found via a real kill/resume run against a live GitHub
        # account, M4): get_latest_checkpoint's start_time/end_time args
        # only bound the *query* window used to look up checkpoint
        # records -- they do NOT guarantee the returned checkpoint's own
        # covered range actually matches the range being requested here.
        # A checkpoint from an earlier, wider (or otherwise different)
        # real backfill run for the same repo+identity was being reused
        # as if it applied to this call's specific start_time/end_time,
        # causing ingest_items to wrongly short-circuit to "0 ingested,
        # already completed" for a range that was never actually
        # processed. Only trust a "completed" checkpoint here if its own
        # stored range genuinely covers the requested range -- the same
        # check CheckpointManager.is_range_covered() already implements
        # correctly; reuse that logic rather than trusting whatever
        # get_latest_checkpoint happened to return.
        if latest_cp and latest_cp.status == "completed":
            covers_requested_range = (
                latest_cp.start_time <= start_time and latest_cp.end_time >= end_time
            )
            if covers_requested_range:
                return 0, latest_cp
            # Not actually a match for this request -- treat as no prior
            # checkpoint for this specific range rather than as "done."
            latest_cp = None

        # Raw records are the idempotency authority between bounded progress
        # milestones. If a process dies after writing raw data but before the
        # next progress moment, replay sees and skips those durable item IDs.
        existing_items = self.get_raw_activities(
            repo=repo,
            github_identity=github_identity,
            start_time=start_time,
            end_time=end_time,
        )
        existing_ids = {str(item.item_id) for item in existing_items if item.item_id}

        last_cursor = latest_cp.cursor if latest_cp and latest_cp.status != "completed" else None
        start_idx = 0
        if last_cursor is not None:
            start_idx = next(
                (idx + 1 for idx, item in enumerate(items) if str(item.item_id) == str(last_cursor)),
                0,
            )
        remaining_items = items[start_idx:]
        newly_processed = 0
        examined = 0
        last_examined: Optional[GitHubActivityItem] = None

        for item in remaining_items:
            if kill_after_n is not None and newly_processed >= kill_after_n:
                break
            examined += 1
            last_examined = item
            if str(item.item_id) in existing_ids:
                continue

            # Build item tags
            tag_names = [
                format_tag(RAW_ACTIVITY_TAG),
                format_tag(f"activity_type:{item.activity_type}"),
                format_tag(f"repo:{item.repo}"),
                format_tag(f"github_identity:{item.github_identity}"),
            ]
            tag_ids = self._resolve_tag_ids(tag_names)

            # Build record for MomentAnnotation
            record = {
                "recorded_at": item.event_timestamp,  # Real event time
                "tags": tag_ids,
                "sources": [
                    type_source_id,
                    f"github:{item.repo}",
                    "com.fulcradynamics.cli",
                ],
                "note": json.dumps(activity_item_to_note_dict(item)),
            }

            # Record to Fulcra
            self.client.record_data_type(
                "MomentAnnotation", [record], api_version="v1alpha1"
            )

            processed_log.append(item)
            newly_processed += 1
            existing_ids.add(str(item.item_id))

            if newly_processed % self.progress_interval == 0:
                latest_cp = Checkpoint(
                    repo=repo,
                    github_identity=github_identity,
                    start_time=start_time,
                    end_time=end_time,
                    status="in_progress",
                    cursor=item.item_id,
                    items_processed=len(existing_ids),
                )
                self.checkpoint_manager.save_checkpoint(latest_cp)

        interrupted = kill_after_n is not None and examined < len(remaining_items)
        if interrupted and last_examined is not None:
            # Graceful shutdown gets a final bounded progress moment. A hard
            # kill relies on existing_ids replay protection instead.
            latest_cp = Checkpoint(
                repo=repo,
                github_identity=github_identity,
                start_time=start_time,
                end_time=end_time,
                status="in_progress",
                cursor=last_examined.item_id,
                items_processed=len(existing_ids),
            )
            self.checkpoint_manager.save_checkpoint(latest_cp)
        elif all(str(item.item_id) in existing_ids for item in items):
            cp = Checkpoint(
                repo=repo,
                github_identity=github_identity,
                start_time=start_time,
                end_time=end_time,
                status="completed",
                cursor=items[-1].item_id if items else None,
                items_processed=len(existing_ids),
            )
            self.checkpoint_manager.save_checkpoint(cp)
            latest_cp = cp

        return newly_processed, latest_cp

    def get_raw_activities(
        self,
        repo: Optional[str] = None,
        github_identity: Optional[str] = None,
        activity_type: Optional[str] = None,
        start_time: str = "2000-01-01T00:00:00Z",
        end_time: str = "2100-01-01T00:00:00Z",
    ) -> List[GitHubActivityItem]:
        """Retrieve raw GitHub activity records back from Fulcra matching filters."""
        required_tag_names = [format_tag(RAW_ACTIVITY_TAG)]
        if repo:
            required_tag_names.append(format_tag(f"repo:{repo}"))
        if github_identity:
            required_tag_names.append(
                format_tag(f"github_identity:{github_identity}")
            )
        if activity_type:
            required_tag_names.append(
                format_tag(f"activity_type:{activity_type}")
            )

        resolved = self.client.create_tags(required_tag_names)
        required_tag_ids = set(t["id"] for t in resolved)

        records = self.client.moment_annotations(
            start_time=start_time, end_time=end_time
        )
        items: List[GitHubActivityItem] = []

        type_info = self.ensure_data_type()
        target_type_id = type_info.get("id", "")

        for rec in records:
            # Match by tag UUID set or metadata annotation name
            rec_tags = set(rec.get("tags") or [])
            metadata = rec.get("metadata") or {}
            ann_name = metadata.get("name") or ""
            ann_id = metadata.get("id") or ""

            # Check if record belongs to GitHub Activity Raw and matches tag filters
            is_type_match = (
                ann_name == RAW_ACTIVITY_ANNOTATION_NAME
                or ann_id == target_type_id
                or required_tag_ids.issubset(rec_tags)
            )

            if is_type_match and required_tag_ids.issubset(rec_tags):
                item = activity_item_from_record(rec)
                if repo and item.repo and item.repo != repo:
                    continue
                if (
                    github_identity
                    and item.github_identity
                    and item.github_identity != github_identity
                ):
                    continue
                if (
                    activity_type
                    and item.activity_type
                    and item.activity_type != activity_type
                ):
                    continue
                items.append(item)

        items.sort(key=lambda x: x.event_timestamp)
        return items
