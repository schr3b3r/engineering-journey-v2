"""Demonstration script for M9: Narrative Generation with Provenance Appendix.

Performs end-to-end narrative document generation:
1. Generates rollups and notability signals for activity across multiple years.
2. Prompts / resolves target range (e.g. "2023-2025" or "full").
3. Constructs a paced narrative markdown document with period summaries and notable callouts.
4. Outputs document with range-based filename (e.g. `engineering_journey_m9_dev_2023_to_2025.md`).
5. Reads generated document end-to-end and verifies every record ID in the Provenance Appendix traces back to real records.
"""

import os
import tempfile
import uuid

from fulcra_client import DEFAULT_CREDENTIALS_PATH, get_fulcra_client
from github_spike import GitHubActivityItem
from narrative import NarrativeGenerator, parse_narrative_document, verify_narrative_provenance
from notability import NotabilityEngine
from rollups import RollupEngine
from summarization import RollupSummarizer


class StandaloneMockFulcraClient:
    """In-memory mock Fulcra client for standalone script execution without environment dependencies."""

    def __init__(self) -> None:
        self.annotations: list[dict] = []
        self.tags: list[dict] = []
        self.duration_records: list[dict] = []
        self.moment_records: list[dict] = []
        self.numeric_records: list[dict] = []

    def annotations_catalog(self) -> list[dict]:
        return self.annotations

    def create_annotation(self, annotation_type: str, name: str, description: str, tags: list) -> dict:
        ann = {
            "id": f"ann-{len(self.annotations)+1}",
            "annotation_type": annotation_type,
            "name": name,
            "description": description,
            "fulcra_source_id": f"com.fulcradynamics.annotation.ann-{len(self.annotations)+1}",
            "deleted_at": None,
        }
        self.annotations.append(ann)
        return ann

    def create_tags(self, tag_names: list[str]) -> list[dict]:
        res = []
        for name in tag_names:
            existing = next((t for t in self.tags if t["name"] == name), None)
            if existing:
                res.append(existing)
            else:
                tag = {"id": f"tag-{len(self.tags)+1}", "name": name}
                self.tags.append(tag)
                res.append(tag)
        return res

    def record_data_type(self, data_type: str, records: list[dict], api_version: str = "v1alpha1") -> dict:
        if data_type == "DurationAnnotation":
            for r in records:
                self.duration_records.append(dict(r, id=f"dur-{len(self.duration_records)+1}"))
        elif data_type == "MomentAnnotation":
            for r in records:
                self.moment_records.append(dict(r, id=f"mom-{len(self.moment_records)+1}"))
        elif data_type == "NumericAnnotation":
            for r in records:
                self.numeric_records.append(dict(r, id=f"num-{len(self.numeric_records)+1}"))
        return {"status": "ok", "count": len(records)}

    def duration_annotations(self, start_time: str, end_time: str, source: str = None) -> list[dict]:
        return self.duration_records

    def moment_annotations(self, start_time: str, end_time: str, source: str = None) -> list[dict]:
        return self.moment_records

    def numeric_annotations(self, start_time: str, end_time: str, source: str = None) -> list[dict]:
        return self.numeric_records


def main() -> None:
    print("=== M9 Demonstration: Narrative Generation & Provenance Verification ===")

    run_id = uuid.uuid4().hex[:6]
    identity = f"m9_developer_{run_id}"
    repo_a = f"acme/core-api-{run_id}"
    repo_b = f"acme/frontend-{run_id}"

    # Generate multi-year sample activity items
    raw_items = [
        GitHubActivityItem("commit", repo_a, identity, f"c1_{run_id}", "2023-04-10T10:00:00Z", "Initial API setup", f"https://github.com/{repo_a}/commit/1"),
        GitHubActivityItem("pull_request_opened", repo_a, identity, f"pr1_{run_id}", "2023-04-12T14:00:00Z", "Open core endpoints PR", f"https://github.com/{repo_a}/pull/1"),
        GitHubActivityItem("pull_request_merged", repo_a, identity, f"prm1_{run_id}", "2023-04-15T16:00:00Z", "Merge core endpoints PR", f"https://github.com/{repo_a}/pull/1"),
        GitHubActivityItem("commit", repo_a, identity, f"c2_{run_id}", "2024-02-01T09:00:00Z", "Refactor authentication layer", f"https://github.com/{repo_a}/commit/2"),
        GitHubActivityItem("commit", repo_b, identity, f"c3_{run_id}", "2024-02-05T11:00:00Z", "First frontend React component", f"https://github.com/{repo_b}/commit/1"),
        GitHubActivityItem("commit", repo_b, identity, f"c4_{run_id}", "2024-02-08T15:00:00Z", "Add dashboard view", f"https://github.com/{repo_b}/commit/2"),
        GitHubActivityItem("commit", repo_a, identity, f"c5_{run_id}", "2025-01-20T10:00:00Z", "2025 performance optimizations", f"https://github.com/{repo_a}/commit/3"),
    ]

    if DEFAULT_CREDENTIALS_PATH.is_file():
        try:
            client = get_fulcra_client()
            print("Connected to live Fulcra API.")
        except Exception as exc:
            print(f"Live Fulcra connection failed ({exc}). Using Standalone Mock Fulcra Client.")
            client = StandaloneMockFulcraClient()
    else:
        print("No local Fulcra credentials file found. Using Standalone Mock Fulcra Client.")
        client = StandaloneMockFulcraClient()

    rollup_engine = RollupEngine(client)
    notability_engine = NotabilityEngine(client)
    summarizer = RollupSummarizer(client)

    # Step 1: Precompute rollups across all periods and persist
    print("\n--- Step 1: Precomputing Activity Rollups ---")
    rollups_dict_a = rollup_engine.generate_all_rollups(raw_items, identity, repo_a, save_to_fulcra=True)
    rollups_dict_b = rollup_engine.generate_all_rollups(raw_items, identity, repo_b, save_to_fulcra=True)

    all_rollups = []
    for period in ["day", "month", "year"]:
        all_rollups.extend(rollups_dict_a[period])
        all_rollups.extend(rollups_dict_b[period])

    # Step 2: Summarize rollups
    print("--- Step 2: Summarizing Rollups ---")
    summarizer.summarize_and_write_back(all_rollups, save_to_fulcra=True)

    # Step 3: Compute and persist Notability Signals
    print("--- Step 3: Computing Notability Signals ---")
    signals = notability_engine.compute_signals(all_rollups)
    notability_engine.save_signals(signals)

    # Step 4: Generate Narrative Document for span 2023-2025
    print("\n--- Step 4: Generating Paced Narrative Document (Range: '2023-2025') ---")
    generator = NarrativeGenerator(client)

    with tempfile.TemporaryDirectory() as tmpdir:
        doc_content, filename, fetched_rollups, fetched_signals = generator.generate_narrative(
            github_identity=identity,
            range_selection="2023-2025",
            rollups=all_rollups,
            signals=signals,
            save_to_file=True,
            output_dir=tmpdir,
        )

        filepath = os.path.join(tmpdir, filename)
        print(f"Document saved to: {filename}")
        print(f"File size: {os.path.getsize(filepath)} bytes")

        # Step 5: Read document end-to-end and display contents
        print("\n--- Step 5: End-to-End Reading Generated Document ---")
        with open(filepath, "r", encoding="utf-8") as f:
            read_doc = f.read()

        print("----------------------------------------------------------------------")
        print(read_doc[:1200] + "\n...\n[Truncated Output Sample]\n")
        print("----------------------------------------------------------------------")

        # Step 6: Parse Provenance Appendix and Verify Record Tracing
        print("\n--- Step 6: Verifying Provenance Appendix Record Tracing ---")
        prov = parse_narrative_document(read_doc)
        print(f"Extracted Rollup Record IDs ({len(prov.rollup_record_ids)}): {prov.rollup_record_ids[:5]}")
        print(f"Extracted Notability Signal IDs ({len(prov.signal_record_ids)}): {prov.signal_record_ids[:5]}")
        print(f"Extracted Raw Source References ({len(prov.raw_source_ids)}): {prov.raw_source_ids[:5]}")

        is_valid = verify_narrative_provenance(read_doc, fetched_rollups, fetched_signals)
        print(f"\nProvenance Appendix Validation Result: {is_valid}")
        assert is_valid is True, "Provenance verification failed!"

    print("\n=== SUCCESS: M9 Narrative Generation and Provenance Verification Completed! ===")


if __name__ == "__main__":
    main()
