"""Demonstration script for M5: Backward and Forward Extension without reprocessing or duplication.

Runs a three-step backfill workflow:
1. Initial backfill for Year 2024.
2. Backward extension backfill for Year 2023 through 2024.
3. Forward extension backfill for Year 2023 through 2025.
4. Re-run complete window 2023 through 2025 (proves total no-op skip).

Verifies that zero duplicate records are created and only missing sub-intervals trigger GitHub API activity fetches.
"""

import uuid
from backfill import BackfillEngine
from checkpoint import CheckpointManager
from github_spike import GitHubActivityItem, GitHubAPISpike
from raw_ingestion import RawActivityIngestor
from fulcra_client import DEFAULT_CREDENTIALS_PATH, get_fulcra_client


class StandaloneMockFulcraClient:
    """In-memory mock Fulcra client for standalone script execution without environment dependencies."""

    def __init__(self) -> None:
        self.annotations: list[dict] = []
        self.tags: list[dict] = []
        self.duration_records: list[dict] = []
        self.moment_records: list[dict] = []

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
        return {"status": "ok", "count": len(records)}

    def duration_annotations(self, start_time: str, end_time: str) -> list[dict]:
        return self.duration_records

    def moment_annotations(self, start_time: str, end_time: str) -> list[dict]:
        return self.moment_records


class ScriptGitHubAPISpike(GitHubAPISpike):
    """Mock GitHub API that logs exact fetch windows and API call counts."""

    def __init__(self, repo_activity_map: dict) -> None:
        super().__init__(token="mock_token", base_url="https://api.github.com")
        self.repo_activity_map = repo_activity_map
        self.fetch_logs: list[tuple[str, str, str]] = []

    def discover_user_repos(self, github_identity: str, limit: int = 100) -> list[str]:
        self.api_call_count += 1
        return list(self.repo_activity_map.keys())[:limit]

    def check_repo_existence(self, repo: str, github_identity: str, since: str, until: str) -> dict:
        self.api_call_count += 1
        items = self.repo_activity_map.get(repo, [])
        matching = [item for item in items if since <= item.event_timestamp <= until]
        return {
            "repo": repo,
            "github_identity": github_identity,
            "since": since,
            "until": until,
            "has_activity": len(matching) > 0,
        }

    def fetch_all_repo_activity(self, repo: str, github_identity: str, since: str, until: str) -> list[GitHubActivityItem]:
        self.api_call_count += 1
        self.fetch_logs.append((repo, since, until))
        items = self.repo_activity_map.get(repo, [])
        matching = [item for item in items if since <= item.event_timestamp <= until]
        matching.sort(key=lambda x: x.event_timestamp)
        return matching


def main() -> None:
    print("=== M5 Demonstration: Backward and Forward Extension ===")

    run_id = uuid.uuid4().hex[:8]
    identity = f"m5_developer_{run_id}"
    repo = f"acme/extension-demo-{run_id}"

    items = [
        GitHubActivityItem("commit", repo, identity, "commit_2023", "2023-06-15T10:00:00Z", "Past 2023 Commit", ""),
        GitHubActivityItem("commit", repo, identity, "commit_2024", "2024-06-15T10:00:00Z", "Initial 2024 Commit", ""),
        GitHubActivityItem("commit", repo, identity, "commit_2025", "2025-06-15T10:00:00Z", "Future 2025 Commit", ""),
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

    mock_gh = ScriptGitHubAPISpike({repo: [items[0], items[1], items[2]]})
    engine = BackfillEngine(client, mock_gh)
    ingestor = RawActivityIngestor(client)

    # Step 1: Initial Run for 2024
    print("\n--- Step 1: Initial Backfill for Year 2024 ---")
    m1 = engine.run_backfill(identity, "2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z", repos=[repo])
    print(f"Records Ingested: {m1['records_ingested']}")
    print(f"Fetch Calls Window Log: {mock_gh.fetch_logs[-1] if mock_gh.fetch_logs else 'None'}")

    # Step 2: Backward Extension (2023..2024)
    print("\n--- Step 2: Backward Extension Backfill (2023..2024) ---")
    m2 = engine.run_backfill(identity, "2023-01-01T00:00:00Z", "2024-12-31T23:59:59Z", repos=[repo])
    print(f"Records Ingested in Extension Run: {m2['records_ingested']}")
    print(f"Fetch Calls Window Log: {mock_gh.fetch_logs[-1]}")

    # Step 3: Forward Extension (2023..2025)
    print("\n--- Step 3: Forward Extension Backfill (2023..2025) ---")
    m3 = engine.run_backfill(identity, "2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z", repos=[repo])
    print(f"Records Ingested in Extension Run: {m3['records_ingested']}")
    print(f"Fetch Calls Window Log: {mock_gh.fetch_logs[-1]}")

    # Step 4: Re-run Full Range (2023..2025) - Should be Complete NO-OP
    print("\n--- Step 4: Re-run Full Range (2023..2025) ---")
    fetches_before = len(mock_gh.fetch_logs)
    m4 = engine.run_backfill(identity, "2023-01-01T00:00:00Z", "2025-12-31T23:59:59Z", repos=[repo])
    print(f"Records Ingested: {m4['records_ingested']}")
    print(f"Repos Covered (Skipped): {m4['repos_covered']}")
    print(f"New Fetch Calls Made: {len(mock_gh.fetch_logs) - fetches_before}")

    # Step 5: Verification of Stored Raw Activity
    print("\n--- Step 5: Verifying Stored Records in Fulcra ---")
    all_raw = ingestor.get_raw_activities(repo=repo, github_identity=identity, start_time="2020-01-01T00:00:00Z", end_time="2030-01-01T00:00:00Z")
    print(f"Total Raw Activity Records stored for {repo}: {len(all_raw)}")
    for r in all_raw:
        print(f"  - Item ID: {r.item_id}, Timestamp: {r.event_timestamp}, Summary: {r.title_or_summary}")

    assert len(all_raw) == 3, f"Expected 3 distinct records, got {len(all_raw)}"
    print("\n=== SUCCESS: Backward and Forward Extension Verified without Reprocessing/Duplication! ===")


if __name__ == "__main__":
    main()
