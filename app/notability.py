"""Notability Signal layer for GitHub activity rollups.

Computes, persists, and queries "Notability Signal" records (NumericAnnotation base
per architecture.md) representing period eventfulness scores, volume vs. personal baseline
comparisons, and category flags (volume_surge, high_activity, quiet_period, first_activity,
focus_switch, streak).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from typing import Any, Dict, List, Optional, Tuple, Set
import uuid

from checkpoint import format_tag, parse_iso, format_iso
from rollups import ActivityRollup

NOTABILITY_ANNOTATION_NAME = "Notability Signal"
NOTABILITY_ANNOTATION_TYPE = "numeric"
NOTABILITY_DESCRIPTION = "Eventfulness score and baseline comparison for activity rollup periods"
NOTABILITY_TAG = "notability_signal"


@dataclass
class NotabilitySignal:
    """Precomputed notability signal record for a specific rollup period."""

    period_type: str  # "day" | "week" | "month" | "quarter" | "year"
    start_time: str  # ISO timestamp
    end_time: str  # ISO timestamp
    github_identity: str
    score: float  # Stored in value field of NumericAnnotation
    repo: Optional[str] = None
    raw_activity_count: int = 0
    baseline_mean: float = 0.0
    baseline_std: float = 0.0
    z_score: float = 0.0
    volume_ratio: float = 0.0
    categories: List[str] = field(default_factory=list)
    breakdown_by_type: Dict[str, int] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)
    explanation: str = ""
    record_id: Optional[str] = None
    formula_version: str = "v1"

    def to_note_dict(self) -> Dict[str, Any]:
        """Convert signal metadata to a dictionary for JSON serialization in `note`."""
        return {
            "period_type": self.period_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "github_identity": self.github_identity,
            "repo": self.repo,
            "score": round(self.score, 2),
            "raw_activity_count": self.raw_activity_count,
            "baseline_mean": round(self.baseline_mean, 2),
            "baseline_std": round(self.baseline_std, 2),
            "z_score": round(self.z_score, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "categories": self.categories,
            "breakdown_by_type": self.breakdown_by_type,
            "sources": self.sources,
            "explanation": self.explanation,
            "formula_version": self.formula_version,
            "record_id": self.record_id,
        }

    def to_note_json(self) -> str:
        """Convert signal state into a JSON string for the `note` field."""
        return json.dumps(self.to_note_dict())

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "NotabilitySignal":
        """Reconstruct a NotabilitySignal object from a raw NumericAnnotation record."""
        recorded_at = record.get("recorded_at") or ""
        if isinstance(recorded_at, dict):
            start_time = recorded_at.get("start_time", "")
            end_time = recorded_at.get("end_time", "")
        else:
            start_time = str(recorded_at)
            end_time = str(recorded_at)

        val = record.get("value")
        score = float(val) if val is not None else 0.0

        note_str = record.get("note") or "{}"
        try:
            note_data = json.loads(note_str)
        except Exception:
            note_data = {}

        sources = record.get("sources") or []

        return cls(
            period_type=note_data.get("period_type", ""),
            start_time=note_data.get("start_time") or start_time,
            end_time=note_data.get("end_time") or end_time,
            github_identity=note_data.get("github_identity", ""),
            score=note_data.get("score", score),
            repo=note_data.get("repo"),
            raw_activity_count=note_data.get("raw_activity_count", 0),
            baseline_mean=note_data.get("baseline_mean", 0.0),
            baseline_std=note_data.get("baseline_std", 0.0),
            z_score=note_data.get("z_score", 0.0),
            volume_ratio=note_data.get("volume_ratio", 0.0),
            categories=note_data.get("categories") or [],
            breakdown_by_type=note_data.get("breakdown_by_type") or {},
            sources=sources,
            explanation=note_data.get("explanation", ""),
            record_id=record.get("id") or note_data.get("record_id"),
            formula_version=note_data.get("formula_version", "v1"),
        )


class NotabilityEngine:
    """Computes, stores, and queries Notability Signals in Fulcra."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._type_info: Optional[Dict[str, Any]] = None
        self._tag_cache: Dict[str, str] = {}

    def ensure_data_type(self) -> Dict[str, Any]:
        """Ensure the 'Notability Signal' custom annotation type exists in Fulcra."""
        if self._type_info:
            return self._type_info

        try:
            catalog = self.client.annotations_catalog()
            for ann in catalog:
                if (
                    ann.get("deleted_at") is None
                    and ann.get("name") == NOTABILITY_ANNOTATION_NAME
                ):
                    self._type_info = ann
                    return ann
        except Exception:
            pass

        created = self.client.create_annotation(
            annotation_type=NOTABILITY_ANNOTATION_TYPE,
            name=NOTABILITY_ANNOTATION_NAME,
            description=NOTABILITY_DESCRIPTION,
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

    def compute_signals(
        self, rollups: List[ActivityRollup]
    ) -> List[NotabilitySignal]:
        """Compute Notability Signal records for a list of ActivityRollup instances.

        Groups rollups by (period_type, repo_key) to establish baseline statistical mean
        and standard deviation, then evaluates volume, category triggers, and bonus points.
        """
        if not rollups:
            return []

        # Group rollups by (period_type, repo)
        groups: Dict[Tuple[str, Optional[str]], List[ActivityRollup]] = {}
        for r in rollups:
            key = (r.period_type, r.repo)
            if key not in groups:
                groups[key] = []
            groups[key].append(r)

        signals: List[NotabilitySignal] = []

        for (period_type, repo_key), group_rollups in groups.items():
            # Sort group chronologically
            sorted_rollups = sorted(group_rollups, key=lambda r: r.start_time)

            counts = [r.total_activity_count for r in sorted_rollups]
            n_periods = len(counts)
            mean = sum(counts) / n_periods if n_periods > 0 else 0.0

            if n_periods > 1:
                variance = sum((x - mean) ** 2 for x in counts) / n_periods
                std_dev = math.sqrt(variance)
            else:
                std_dev = 0.0

            seen_repos_before: Set[str] = set()
            prev_primary_repo: Optional[str] = None
            active_streak = 0

            for r in sorted_rollups:
                raw_cnt = r.total_activity_count
                counts_by_type = r.counts

                # Calculate volume metrics vs baseline
                if mean > 0:
                    volume_ratio = raw_cnt / mean
                    z_score = (raw_cnt - mean) / std_dev if std_dev > 0 else (1.0 if raw_cnt > mean else 0.0)
                else:
                    volume_ratio = 1.0 if raw_cnt > 0 else 0.0
                    z_score = 1.0 if raw_cnt > 0 else 0.0

                # Base volume score (50 = average, +25 per std_dev)
                base_volume_score = 50.0 + (25.0 * z_score)
                base_volume_score = max(0.0, min(100.0, base_volume_score))

                categories: List[str] = []
                bonus_points = 0.0

                # 1. Volume Surge / High Activity / Quiet Period
                if raw_cnt == 0:
                    categories.append("quiet_period")
                    base_volume_score = min(base_volume_score, 10.0)
                else:
                    if volume_ratio >= 2.0 and raw_cnt >= 5:
                        categories.append("volume_surge")
                        bonus_points += 15.0
                    elif volume_ratio >= 1.5 and raw_cnt >= 3:
                        categories.append("high_activity")
                        bonus_points += 10.0

                # 2. First Activity / New Repo
                is_first = False
                if raw_cnt > 0:
                    target_repo = r.repo or "overall"
                    if target_repo not in seen_repos_before:
                        seen_repos_before.add(target_repo)
                        is_first = True
                        categories.append("first_activity")
                        bonus_points += 15.0

                # 3. Focus Switch
                # Determine primary activity repo if not already repo-scoped
                current_primary_repo = r.repo
                if current_primary_repo and prev_primary_repo and current_primary_repo != prev_primary_repo and raw_cnt > 0:
                    categories.append("focus_switch")
                    bonus_points += 10.0

                if current_primary_repo and raw_cnt > 0:
                    prev_primary_repo = current_primary_repo

                # 4. Streak & Streak Tracking
                if raw_cnt > 0:
                    active_streak += 1
                    if active_streak >= 3:
                        categories.append("streak")
                        bonus_points += 10.0
                else:
                    active_streak = 0

                # 5. Diversity bonus (PR merges, reviews, multiple activity types)
                pr_merges = counts_by_type.get("pull_request_merged", 0)
                active_types_count = len([k for k, v in counts_by_type.items() if v > 0])
                if pr_merges > 0 or active_types_count >= 3:
                    bonus_points += 10.0

                # Final score
                if raw_cnt == 0:
                    final_score = 0.0
                else:
                    final_score = min(100.0, max(0.0, base_volume_score + bonus_points))

                # Explanation string
                cat_str = ", ".join(categories) if categories else "standard_activity"
                explanation = (
                    f"Notability score {final_score:.1f}/100 ({cat_str}): "
                    f"{raw_cnt} items vs baseline avg {mean:.2f} (volume ratio {volume_ratio:.2f}x, z-score {z_score:+.2f})."
                )

                sources = [r.get_source_id()]

                signals.append(
                    NotabilitySignal(
                        period_type=r.period_type,
                        start_time=r.start_time,
                        end_time=r.end_time,
                        github_identity=r.github_identity,
                        score=final_score,
                        repo=r.repo,
                        raw_activity_count=raw_cnt,
                        baseline_mean=mean,
                        baseline_std=std_dev,
                        z_score=z_score,
                        volume_ratio=volume_ratio,
                        categories=categories,
                        breakdown_by_type=counts_by_type,
                        sources=sources,
                        explanation=explanation,
                    )
                )

        signals.sort(key=lambda s: (s.start_time, s.repo or ""))
        return signals

    def save_signals(self, signals: List[NotabilitySignal]) -> List[Any]:
        """Persist NotabilitySignal records into Fulcra as NumericAnnotation custom records."""
        if not signals:
            return []

        type_info = self.ensure_data_type()
        type_id = type_info.get("id", "")
        type_source_id = (
            type_info.get("fulcra_source_id")
            or f"com.fulcradynamics.annotation.{type_id}"
        )

        results: List[Any] = []
        for sig in signals:
            if not sig.record_id:
                sig.record_id = f"notability_{sig.period_type}_{uuid.uuid4().hex[:8]}"

            tag_names = [
                format_tag(NOTABILITY_TAG),
                format_tag(f"period_type:{sig.period_type}"),
                format_tag(f"github_identity:{sig.github_identity}"),
            ]
            if sig.repo:
                tag_names.append(format_tag(f"repo:{sig.repo}"))

            for cat in sig.categories:
                tag_names.append(format_tag(f"notability_category:{cat}"))

            tag_ids = self._resolve_tag_ids(tag_names)

            sources = [type_source_id] + [s for s in sig.sources if s != type_source_id]

            record = {
                "recorded_at": sig.start_time,  # Period start ISO string
                "value": sig.score,
                "tags": tag_ids,
                "sources": sources,
                "note": sig.to_note_json(),
            }

            resp = self.client.record_data_type(
                "NumericAnnotation", [record], api_version="v1alpha1"
            )

            results.append(resp)

        return results

    def get_signals(
        self,
        period_type: Optional[str] = None,
        repo: Optional[str] = None,
        github_identity: Optional[str] = None,
        start_time: str = "2000-01-01T00:00:00Z",
        end_time: str = "2100-01-01T00:00:00Z",
    ) -> List[NotabilitySignal]:
        """Query Notability Signal records back from Fulcra matching filters."""
        required_tag_names = [format_tag(NOTABILITY_TAG)]
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

        records = self.client.numeric_annotations(
            start_time=start_time, end_time=end_time, source=type_source_id
        )
        raw_signals: List[NotabilitySignal] = []

        for rec in records:
            raw_tags = rec.get("tags") or []
            rec_tags = set(t["id"] if isinstance(t, dict) else t for t in raw_tags)
            metadata = rec.get("metadata") or {}
            ann_name = metadata.get("name") or ""
            ann_id = metadata.get("id") or ""
            rec_sources = set(rec.get("sources") or [])

            is_type_match = (
                ann_name == NOTABILITY_ANNOTATION_NAME
                or ann_id == target_type_id
                or type_source_id in rec_sources
                or required_tag_ids.issubset(rec_tags)
            )

            if is_type_match and required_tag_ids.issubset(rec_tags):
                sig = NotabilitySignal.from_record(rec)
                if period_type and sig.period_type and sig.period_type != period_type:
                    continue
                if repo and sig.repo and sig.repo != repo:
                    continue
                if (
                    github_identity
                    and sig.github_identity
                    and sig.github_identity != github_identity
                ):
                    continue
                raw_signals.append(sig)

        # Deduplicate signals by key (period_type, repo, github_identity, start_time)
        by_key: Dict[Tuple[str, Optional[str], str, str], NotabilitySignal] = {}
        for s in raw_signals:
            key = (s.period_type, s.repo, s.github_identity, s.start_time)
            by_key[key] = s

        signals = list(by_key.values())
        signals.sort(key=lambda s: s.start_time)
        return signals
