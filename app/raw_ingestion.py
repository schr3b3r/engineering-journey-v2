"""Raw GitHub Activity ingestion into Fulcra.

Manages registering the "GitHub Activity Raw" custom data type (MomentAnnotation base per architecture.md),
ingesting raw activity items with real event time recorded_at, filterable tags, and lineage sources,
wired into M1's Checkpoint mechanism for resumability.
"""

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from checkpoint import (
    DEFAULT_PROGRESS_INTERVAL,
    Checkpoint,
    format_tag,
)
from github_spike import GitHubActivityItem
from reliability import retry_call

RAW_ACTIVITY_ANNOTATION_NAME = "GitHub Activity Raw"
RAW_ACTIVITY_ANNOTATION_TYPE = "moment"
RAW_ACTIVITY_DESCRIPTION = "Raw GitHub activity item"
RAW_ACTIVITY_TAG = "github_activity_raw"


def activity_fingerprint(item: GitHubActivityItem) -> str:
    """Stable source identity used to make partial retries ambiguity-safe."""
    value = "|".join(
        (
            item.github_identity,
            item.repo,
            item.activity_type,
            str(item.item_id),
            item.event_timestamp,
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()


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
        "fingerprint": activity_fingerprint(item),
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
        record_id=record.get("id"),
    )


class RawActivityIngestor:
    """Ingests raw GitHub activity items into Fulcra with durable checkpointing."""

    def __init__(
        self,
        client: Any,
        progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
        progress_callback: Optional[Callable[[str], None]] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.client = client
        self.progress_interval = max(1, progress_interval)
        self.progress_callback = progress_callback
        self.event_callback = event_callback
        self._type_info: Optional[Dict[str, Any]] = None
        self._tag_cache: Dict[str, str] = {}

    def _retry_event(self, event: Dict[str, Any]) -> None:
        event["stage"] = "raw_ingestion"
        if self.event_callback:
            self.event_callback(event)
        if self.progress_callback:
            self.progress_callback(
                f"[retry] {event['operation']}: attempt {event['attempt']}/"
                f"{event['max_attempts']} in {event['delay_seconds']}s ({event['error']})"
            )

    def ensure_data_type(self) -> Dict[str, Any]:
        """Ensure the 'GitHub Activity Raw' custom annotation type exists in Fulcra."""
        if self._type_info:
            return self._type_info

        def operation() -> Dict[str, Any]:
            catalog = self.client.annotations_catalog()
            for ann in catalog:
                if (
                    ann.get("deleted_at") is None
                    and ann.get("name") == RAW_ACTIVITY_ANNOTATION_NAME
                ):
                    return ann
            return self.client.create_annotation(
                annotation_type=RAW_ACTIVITY_ANNOTATION_TYPE,
                name=RAW_ACTIVITY_ANNOTATION_NAME,
                description=RAW_ACTIVITY_DESCRIPTION,
                tags=[],
            )

        self._type_info = retry_call(
            operation,
            operation_name="ensure raw activity type",
            on_retry=self._retry_event,
        )
        return self._type_info

    def _resolve_tag_ids(self, tag_names: List[str]) -> List[str]:
        """Resolve tag names to tag UUIDs using local cache and client.create_tags."""
        missing = [t for t in tag_names if t not in self._tag_cache]
        if missing:
            resolved = retry_call(
                lambda: self.client.create_tags(missing),
                operation_name="resolve raw activity tags",
                on_retry=self._retry_event,
            )
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

        # Raw records are the idempotency authority. A replay safely scans the
        # current repository and skips durable fingerprints, so no separate
        # per-repository cursor record is needed.
        existing_items = self.get_raw_activities(
            repo=repo,
            github_identity=github_identity,
            start_time=start_time,
            end_time=end_time,
        )
        existing_fingerprints = {activity_fingerprint(item) for item in existing_items}

        latest_cp: Optional[Checkpoint] = None
        remaining_items = items
        newly_processed = 0
        examined = 0
        last_examined: Optional[GitHubActivityItem] = None
        last_progress_at = time.perf_counter()

        for item in remaining_items:
            if kill_after_n is not None and newly_processed >= kill_after_n:
                break
            examined += 1
            last_examined = item
            fingerprint = activity_fingerprint(item)
            if fingerprint in existing_fingerprints:
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

            # If a write commits but its response is lost, query by stable
            # fingerprint before retrying so the retry cannot duplicate it.
            def write_record() -> Any:
                try:
                    return self.client.record_data_type(
                        "MomentAnnotation", [record], api_version="v1alpha1"
                    )
                except Exception:
                    durable = self.get_raw_activities(
                        repo=item.repo,
                        github_identity=item.github_identity,
                        start_time=start_time,
                        end_time=end_time,
                    )
                    if fingerprint in {activity_fingerprint(value) for value in durable}:
                        return {"recovered_after_ambiguous_write": True}
                    raise

            retry_call(
                write_record,
                operation_name=f"write raw activity {fingerprint[:12]}",
                on_retry=self._retry_event,
            )

            processed_log.append(item)
            newly_processed += 1
            existing_fingerprints.add(fingerprint)

            now = time.perf_counter()
            should_report = (
                newly_processed == 1
                or newly_processed % 25 == 0
                or (now - last_progress_at) >= 10
            )
            if should_report and self.progress_callback:
                self.progress_callback(
                    f"[ingest] {repo}: {newly_processed} new / "
                    f"{len(remaining_items)} considered; latest {item.activity_type}."
                )
            if should_report and self.event_callback:
                self.event_callback(
                    {
                        "event": "progress",
                        "stage": "ingest",
                        "repository": repo,
                        "records_written": newly_processed,
                        "items_considered": examined,
                        "items_total": len(remaining_items),
                        "latest_activity_type": item.activity_type,
                    }
                )
            if should_report:
                last_progress_at = now

            if newly_processed % self.progress_interval == 0:
                latest_cp = Checkpoint(
                    repo=repo,
                    github_identity=github_identity,
                    start_time=start_time,
                    end_time=end_time,
                    status="in_progress",
                    cursor=item.item_id,
                    items_processed=len(existing_fingerprints),
                )

        interrupted = kill_after_n is not None and examined < len(remaining_items)
        if interrupted and last_examined is not None:
            # Return an in-memory status for callers. Durable resume is the
            # run milestone plus raw fingerprints, not a second progress type.
            latest_cp = Checkpoint(
                repo=repo,
                github_identity=github_identity,
                start_time=start_time,
                end_time=end_time,
                status="in_progress",
                cursor=last_examined.item_id,
                items_processed=len(existing_fingerprints),
            )

        elif all(activity_fingerprint(item) in existing_fingerprints for item in items):
            cp = Checkpoint(
                repo=repo,
                github_identity=github_identity,
                start_time=start_time,
                end_time=end_time,
                status="completed",
                cursor=items[-1].item_id if items else None,
                items_processed=len(existing_fingerprints),
            )
            latest_cp = cp

        if self.progress_callback:
            status = latest_cp.status if latest_cp else "no-op"
            self.progress_callback(
                f"[ingest] {repo}: finished with {newly_processed} new records; status={status}."
            )
        if self.event_callback:
            self.event_callback(
                {
                    "event": "repository_ingest_completed",
                    "stage": "ingest",
                    "repository": repo,
                    "records_written": newly_processed,
                    "status": latest_cp.status if latest_cp else "no-op",
                }
            )

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

        required_tag_ids = set(self._resolve_tag_ids(required_tag_names))
        type_info = self.ensure_data_type()
        target_type_id = type_info.get("id", "")
        type_source_id = type_info.get("fulcra_source_id") or (
            f"com.fulcradynamics.annotation.{target_type_id}"
        )
        records = retry_call(
            lambda: self.client.moment_annotations(
                start_time=start_time, end_time=end_time, source=type_source_id
            ),
            operation_name="query raw activity",
            on_retry=self._retry_event,
        )
        items: List[GitHubActivityItem] = []

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
