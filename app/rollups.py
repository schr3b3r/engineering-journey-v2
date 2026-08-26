"""Activity Rollup layer for day, week, month, quarter, and year periods.

Precomputes and durably stores "Activity Rollup" records (DurationAnnotation base per architecture.md)
for day, week, month, quarter, and year periods with hand-rolled numeric aggregation
and real sources provenance chains referencing raw or lower-layer records.
"""

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, List, Optional, Tuple
import uuid

from checkpoint import format_tag, parse_iso, format_iso
from github_spike import GitHubActivityItem

ROLLUP_ANNOTATION_NAME = "Activity Rollup"
ROLLUP_ANNOTATION_TYPE = "duration"
ROLLUP_DESCRIPTION = "Precomputed activity rollups over day, week, month, quarter, and year periods"
ROLLUP_TAG = "activity_rollup"
VALID_PERIOD_TYPES = {"day", "week", "month", "quarter", "year"}


def get_period_bounds(dt: datetime, period_type: str) -> Tuple[str, str]:
    """Calculate ISO start_time and end_time strings for a given period_type containing dt."""
    dt = dt.astimezone(timezone.utc)

    if period_type == "day":
        start = datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(dt.year, dt.month, dt.day, 23, 59, 59, tzinfo=timezone.utc)
    elif period_type == "week":
        monday = dt - timedelta(days=dt.weekday())
        start = datetime(monday.year, monday.month, monday.day, 0, 0, 0, tzinfo=timezone.utc)
        sunday = monday + timedelta(days=6)
        end = datetime(sunday.year, sunday.month, sunday.day, 23, 59, 59, tzinfo=timezone.utc)
    elif period_type == "month":
        start = datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=timezone.utc)
        last_day = calendar.monthrange(dt.year, dt.month)[1]
        end = datetime(dt.year, dt.month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    elif period_type == "quarter":
        q_start_month = 3 * ((dt.month - 1) // 3) + 1
        q_end_month = q_start_month + 2
        start = datetime(dt.year, q_start_month, 1, 0, 0, 0, tzinfo=timezone.utc)
        last_day = calendar.monthrange(dt.year, q_end_month)[1]
        end = datetime(dt.year, q_end_month, last_day, 23, 59, 59, tzinfo=timezone.utc)
    elif period_type == "year":
        start = datetime(dt.year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(dt.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    else:
        raise ValueError(f"Unknown period_type: {period_type}")

    return format_iso(start), format_iso(end)


@dataclass
class ActivityRollup:
    """Precomputed activity rollup record for a specific period."""

    period_type: str  # "day" | "week" | "month" | "quarter" | "year"
    start_time: str  # ISO string
    end_time: str  # ISO string
    github_identity: str
    repo: Optional[str] = None
    counts: Dict[str, int] = field(default_factory=dict)
    total_activity_count: int = 0
    sources: List[str] = field(default_factory=list)
    summary_text: Optional[str] = None
    record_id: Optional[str] = None

    def get_source_id(self) -> str:
        """Return record_id if available, otherwise a deterministic rollup source identifier."""
        if self.record_id:
            return self.record_id
        repo_part = self.repo or "all"
        return f"rollup:{self.period_type}:{self.github_identity}:{repo_part}:{self.start_time}"

    def to_note_dict(self) -> Dict[str, Any]:
        """Convert rollup metadata to a dictionary for JSON serialization in `note`."""
        return {
            "period_type": self.period_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "github_identity": self.github_identity,
            "repo": self.repo,
            "counts": self.counts,
            "total_activity_count": self.total_activity_count,
            "summary_text": self.summary_text,
            "record_id": self.record_id,
        }

    def to_note_json(self) -> str:
        """Convert rollup state into a JSON string for the `note` field."""
        return json.dumps(self.to_note_dict())

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "ActivityRollup":
        """Reconstruct an ActivityRollup object from a raw DurationAnnotation record."""
        rec_at = record.get("recorded_at") or {}
        start_time = rec_at.get("start_time", "")
        end_time = rec_at.get("end_time", "")

        note_str = record.get("note") or "{}"
        try:
            note_data = json.loads(note_str)
        except Exception:
            note_data = {}

        sources = record.get("sources") or []

        counts = note_data.get("counts") or {}
        total = note_data.get("total_activity_count", sum(counts.values()))

        return cls(
            period_type=note_data.get("period_type", ""),
            start_time=note_data.get("start_time") or start_time,
            end_time=note_data.get("end_time") or end_time,
            github_identity=note_data.get("github_identity", ""),
            repo=note_data.get("repo"),
            counts=counts,
            total_activity_count=total,
            sources=sources,
            summary_text=note_data.get("summary_text"),
            record_id=record.get("id") or note_data.get("record_id"),
        )


class RollupEngine:
    """Computes, stores, and queries Activity Rollups in Fulcra."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._type_info: Optional[Dict[str, Any]] = None
        self._tag_cache: Dict[str, str] = {}

    def ensure_data_type(self) -> Dict[str, Any]:
        """Ensure the 'Activity Rollup' custom annotation type exists in Fulcra."""
        if self._type_info:
            return self._type_info

        try:
            catalog = self.client.annotations_catalog()
            for ann in catalog:
                if (
                    ann.get("deleted_at") is None
                    and ann.get("name") == ROLLUP_ANNOTATION_NAME
                ):
                    self._type_info = ann
                    return ann
        except Exception:
            pass

        created = self.client.create_annotation(
            annotation_type=ROLLUP_ANNOTATION_TYPE,
            name=ROLLUP_ANNOTATION_NAME,
            description=ROLLUP_DESCRIPTION,
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

    def generate_day_rollups(
        self,
        raw_items: List[GitHubActivityItem],
        github_identity: str,
        repo: Optional[str] = None,
        by_repo: bool = True,
    ) -> List[ActivityRollup]:
        """Generate day-level activity rollups from raw activity items."""
        filtered_items = [
            item for item in raw_items
            if (not github_identity or item.github_identity == github_identity)
            and (not repo or item.repo == repo)
        ]

        # Group items by (day_start, day_end, repo_key)
        groups: Dict[Tuple[str, str, Optional[str]], List[GitHubActivityItem]] = {}
        for item in filtered_items:
            dt = parse_iso(item.event_timestamp)
            start_iso, end_iso = get_period_bounds(dt, "day")
            item_repo = item.repo if by_repo else repo
            key = (start_iso, end_iso, item_repo)
            if key not in groups:
                groups[key] = []
            groups[key].append(item)

        rollups: List[ActivityRollup] = []
        for (start_iso, end_iso, grp_repo), items in groups.items():
            counts: Dict[str, int] = {}
            sources: List[str] = []
            for it in items:
                counts[it.activity_type] = counts.get(it.activity_type, 0) + 1
                if it.item_id:
                    sources.append(f"raw:{it.repo}:{it.item_id}")

            total = sum(counts.values())
            rollups.append(
                ActivityRollup(
                    period_type="day",
                    start_time=start_iso,
                    end_time=end_iso,
                    github_identity=github_identity,
                    repo=grp_repo,
                    counts=counts,
                    total_activity_count=total,
                    sources=sources,
                )
            )

        rollups.sort(key=lambda r: (r.start_time, r.repo or ""))
        return rollups

    def _aggregate_higher_period(
        self,
        lower_rollups: List[ActivityRollup],
        target_period_type: str,
        github_identity: str,
        repo: Optional[str] = None,
    ) -> List[ActivityRollup]:
        """Aggregate lower-level rollups into target_period_type rollups."""
        filtered = [
            r for r in lower_rollups
            if (not github_identity or r.github_identity == github_identity)
            and (repo is None or r.repo == repo)
        ]

        groups: Dict[Tuple[str, str, Optional[str]], List[ActivityRollup]] = {}
        for r in filtered:
            dt = parse_iso(r.start_time)
            start_iso, end_iso = get_period_bounds(dt, target_period_type)
            key = (start_iso, end_iso, r.repo)
            if key not in groups:
                groups[key] = []
            groups[key].append(r)

        rollups: List[ActivityRollup] = []
        for (start_iso, end_iso, grp_repo), constituent in groups.items():
            counts: Dict[str, int] = {}
            sources: List[str] = []
            for sub_r in constituent:
                for act_type, cnt in sub_r.counts.items():
                    counts[act_type] = counts.get(act_type, 0) + cnt
                sources.append(sub_r.get_source_id())

            total = sum(counts.values())
            rollups.append(
                ActivityRollup(
                    period_type=target_period_type,
                    start_time=start_iso,
                    end_time=end_iso,
                    github_identity=github_identity,
                    repo=grp_repo,
                    counts=counts,
                    total_activity_count=total,
                    sources=sources,
                )
            )

        rollups.sort(key=lambda r: (r.start_time, r.repo or ""))
        return rollups

    def generate_week_rollups(
        self,
        day_rollups: List[ActivityRollup],
        github_identity: str,
        repo: Optional[str] = None,
    ) -> List[ActivityRollup]:
        """Generate week-level rollups from day rollups."""
        return self._aggregate_higher_period(
            day_rollups, "week", github_identity, repo
        )

    def generate_month_rollups(
        self,
        lower_rollups: List[ActivityRollup],
        github_identity: str,
        repo: Optional[str] = None,
    ) -> List[ActivityRollup]:
        """Generate month-level rollups from lower (day or week) rollups."""
        return self._aggregate_higher_period(
            lower_rollups, "month", github_identity, repo
        )

    def generate_quarter_rollups(
        self,
        month_rollups: List[ActivityRollup],
        github_identity: str,
        repo: Optional[str] = None,
    ) -> List[ActivityRollup]:
        """Generate quarter-level rollups from month rollups."""
        return self._aggregate_higher_period(
            month_rollups, "quarter", github_identity, repo
        )

    def generate_year_rollups(
        self,
        quarter_rollups: List[ActivityRollup],
        github_identity: str,
        repo: Optional[str] = None,
    ) -> List[ActivityRollup]:
        """Generate year-level rollups from quarter rollups."""
        return self._aggregate_higher_period(
            quarter_rollups, "year", github_identity, repo
        )

    def generate_all_rollups(
        self,
        raw_items: List[GitHubActivityItem],
        github_identity: str,
        repo: Optional[str] = None,
        save_to_fulcra: bool = False,
    ) -> Dict[str, List[ActivityRollup]]:
        """Generate rollups for all 5 period types (day, week, month, quarter, year).

        If save_to_fulcra is True, persists each layer to Fulcra sequentially
        so higher-layer rollups capture real lower-layer record IDs in sources.
        """
        day_rollups = self.generate_day_rollups(raw_items, github_identity, repo)
        if save_to_fulcra and day_rollups:
            self.save_rollups(day_rollups)

        week_rollups = self.generate_week_rollups(day_rollups, github_identity, repo)
        if save_to_fulcra and week_rollups:
            self.save_rollups(week_rollups)

        month_rollups = self.generate_month_rollups(day_rollups, github_identity, repo)
        if save_to_fulcra and month_rollups:
            self.save_rollups(month_rollups)

        quarter_rollups = self.generate_quarter_rollups(month_rollups, github_identity, repo)
        if save_to_fulcra and quarter_rollups:
            self.save_rollups(quarter_rollups)

        year_rollups = self.generate_year_rollups(quarter_rollups, github_identity, repo)
        if save_to_fulcra and year_rollups:
            self.save_rollups(year_rollups)

        return {
            "day": day_rollups,
            "week": week_rollups,
            "month": month_rollups,
            "quarter": quarter_rollups,
            "year": year_rollups,
        }

    def save_rollups(self, rollups: List[ActivityRollup]) -> List[Any]:
        """Persist ActivityRollup records into Fulcra."""
        if not rollups:
            return []

        type_info = self.ensure_data_type()
        type_id = type_info.get("id", "")
        type_source_id = (
            type_info.get("fulcra_source_id")
            or f"com.fulcradynamics.annotation.{type_id}"
        )

        results: List[Any] = []
        for r in rollups:
            if not r.record_id:
                r.record_id = f"rollup_{r.period_type}_{uuid.uuid4().hex[:8]}"

            tag_names = [
                format_tag(ROLLUP_TAG),
                format_tag(f"period_type:{r.period_type}"),
                format_tag(f"github_identity:{r.github_identity}"),
            ]
            if r.repo:
                tag_names.append(format_tag(f"repo:{r.repo}"))

            tag_ids = self._resolve_tag_ids(tag_names)

            sources = [type_source_id] + [s for s in r.sources if s != type_source_id]

            record = {
                "recorded_at": {
                    "start_time": r.start_time,
                    "end_time": r.end_time,
                },
                "tags": tag_ids,
                "sources": sources,
                "note": r.to_note_json(),
            }

            resp = self.client.record_data_type(
                "DurationAnnotation", [record], api_version="v1alpha1"
            )

            results.append(resp)

        return results

    def get_rollups(
        self,
        period_type: Optional[str] = None,
        repo: Optional[str] = None,
        github_identity: Optional[str] = None,
        start_time: str = "2000-01-01T00:00:00Z",
        end_time: str = "2100-01-01T00:00:00Z",
    ) -> List[ActivityRollup]:
        """Query Activity Rollup records back from Fulcra matching filters."""
        required_tag_names = [format_tag(ROLLUP_TAG)]
        if period_type:
            required_tag_names.append(format_tag(f"period_type:{period_type}"))
        if repo:
            required_tag_names.append(format_tag(f"repo:{repo}"))
        if github_identity:
            required_tag_names.append(format_tag(f"github_identity:{github_identity}"))

        resolved = self.client.create_tags(required_tag_names)
        required_tag_ids = set(t["id"] for t in resolved)

        type_info = self.ensure_data_type()
        target_type_id = type_info.get("id", "")
        type_source_id = (
            type_info.get("fulcra_source_id")
            or f"com.fulcradynamics.annotation.{target_type_id}"
        )

        records = self.client.duration_annotations(
            start_time=start_time, end_time=end_time, source=type_source_id
        )
        raw_rollups: List[ActivityRollup] = []

        for rec in records:
            raw_tags = rec.get("tags") or []
            rec_tags = set(t["id"] if isinstance(t, dict) else t for t in raw_tags)
            metadata = rec.get("metadata") or {}
            ann_name = metadata.get("name") or ""
            ann_id = metadata.get("id") or ""
            rec_sources = set(rec.get("sources") or [])

            is_type_match = (
                ann_name == ROLLUP_ANNOTATION_NAME
                or ann_id == target_type_id
                or type_source_id in rec_sources
                or required_tag_ids.issubset(rec_tags)
            )

            if is_type_match and required_tag_ids.issubset(rec_tags):
                rollup = ActivityRollup.from_record(rec)
                if period_type and rollup.period_type and rollup.period_type != period_type:
                    continue
                if repo and rollup.repo and rollup.repo != repo:
                    continue
                if (
                    github_identity
                    and rollup.github_identity
                    and rollup.github_identity != github_identity
                ):
                    continue
                raw_rollups.append(rollup)

        # Deduplicate rollups by key (period_type, repo, github_identity, start_time),
        # preferring records with summary_text set or later record writes.
        by_key: Dict[Tuple[str, Optional[str], str, str], ActivityRollup] = {}
        for r in raw_rollups:
            key = (r.period_type, r.repo, r.github_identity, r.start_time)
            if key not in by_key:
                by_key[key] = r
            else:
                existing = by_key[key]
                if r.summary_text and not existing.summary_text:
                    by_key[key] = r
                elif r.summary_text and existing.summary_text:
                    by_key[key] = r
                elif not existing.summary_text:
                    by_key[key] = r

        rollups = list(by_key.values())
        rollups.sort(key=lambda r: r.start_time)
        return rollups
