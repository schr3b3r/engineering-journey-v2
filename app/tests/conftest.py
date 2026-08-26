"""Pytest configuration and shared fixtures for Engineering Journey v2 tests."""

import uuid
from typing import Any, Dict, List, Optional
import pytest


class MockFulcraClient:
    """Mock Fulcra API client that operates entirely in memory for unit testing."""

    def __init__(self) -> None:
        self.annotations: List[Dict[str, Any]] = []
        self.tags: Dict[str, str] = {}  # tag_name -> tag_uuid
        self.duration_records: List[Dict[str, Any]] = []
        self.moment_records: List[Dict[str, Any]] = []

    def annotations_catalog(self) -> List[Dict[str, Any]]:
        return self.annotations

    def create_annotation(
        self,
        annotation_type: str,
        name: str,
        description: Optional[str],
        tags: List[str],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        ann_id = str(uuid.uuid4())
        ann = {
            "id": ann_id,
            "name": name,
            "description": description or "",
            "annotation_type": annotation_type,
            "deleted_at": None,
            "fulcra_source_id": f"com.fulcradynamics.annotation.{ann_id}",
        }
        self.annotations.append(ann)
        return ann

    def create_tags(self, tag_names: List[str]) -> List[Dict[str, str]]:
        result = []
        for name in tag_names:
            if name not in self.tags:
                self.tags[name] = str(uuid.uuid4())
            result.append({"name": name, "id": self.tags[name]})
        return result

    def record_data_type(
        self, data_type: str, records: List[Dict[str, Any]], api_version: str = "v1alpha1"
    ) -> Dict[str, str]:
        if data_type == "DurationAnnotation":
            for rec in records:
                rec_copy = dict(rec)
                if "id" not in rec_copy or not rec_copy["id"]:
                    rec_copy["id"] = str(uuid.uuid4())
                self.duration_records.append(rec_copy)
        elif data_type == "MomentAnnotation":
            for rec in records:
                rec_copy = dict(rec)
                if "id" not in rec_copy or not rec_copy["id"]:
                    rec_copy["id"] = str(uuid.uuid4())
                self.moment_records.append(rec_copy)
        return {"upload_id": str(uuid.uuid4())}

    def duration_annotations(
        self,
        start_time: str,
        end_time: str,
        source: Optional[str] = None,
        fulcra_userid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        matching = []
        for rec in self.duration_records:
            if source:
                rec_sources = rec.get("sources") or []
                if source not in rec_sources:
                    continue
            rec_at = rec.get("recorded_at") or {}
            r_start = rec_at.get("start_time", "")
            r_end = rec_at.get("end_time", "")
            if r_start <= end_time and r_end >= start_time:
                matching.append(rec)
        return matching

    def moment_annotations(
        self,
        start_time: str,
        end_time: str,
        source: Optional[str] = None,
        fulcra_userid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        matching = []
        for rec in self.moment_records:
            if source:
                rec_sources = rec.get("sources") or []
                if source not in rec_sources:
                    continue
            rec_at = rec.get("recorded_at")
            ts = rec_at.get("value") if isinstance(rec_at, dict) else str(rec_at or "")
            if ts and start_time <= ts <= end_time:
                matching.append(rec)
        return matching


@pytest.fixture
def mock_fulcra_client() -> MockFulcraClient:
    """Fixture providing a fresh in-memory mock Fulcra client."""
    return MockFulcraClient()
