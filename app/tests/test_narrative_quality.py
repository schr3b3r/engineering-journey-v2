"""End-to-end narrative quality regression on one shared activity window.

This is deliberately rubric-based rather than a file/callback smoke test.  The
v1 reference and v2 candidate are reviewed side by side against specificity,
chronology, cross-repository synthesis, pacing, repetition, and grounding.
"""

from github_spike import GitHubActivityItem
from narrative import format_narrative_document
from rollups import RollupEngine
from summarization import RollupSummarizer


def _rubric(text: str) -> dict[str, bool]:
    january = text.find("January 2024")
    february = text.find("February 2024")
    march = text.find("March 2024")
    return {
        "specificity": all(term in text for term in (
            "React dashboard", "metrics endpoint", "idempotent payment capture",
        )),
        "chronology": -1 < january < february < march,
        "cross_repo_synthesis": "dashboard-ui" in text and "metrics-api" in text,
        "pacing": text.count("January 2024") == 1 and text.count("February 2024") == 1,
        "low_repetition": text.count("During the month period") == 0,
        "grounding": "Kubernetes migration" not in text and "revenue" not in text,
    }


def test_v1_reference_and_v2_candidate_share_quality_floor(mock_fulcra_client) -> None:
    """Compare real rendered artifacts, built from the exact same evidence window."""
    identity = "quality_dev"
    items = [
        GitHubActivityItem(
            "pull_request_merged", "acme/dashboard-ui", identity, "ui1",
            "2024-01-10T10:00:00Z", "Migrate analytics dashboard to React", "",
            raw_payload={"body": "Replace the legacy view layer and consume metrics-api."},
        ),
        GitHubActivityItem(
            "pull_request_merged", "acme/metrics-api", identity, "api1",
            "2024-01-12T10:00:00Z", "Add dashboard metrics endpoint", "",
            raw_payload={"body": "Supplies typed trend data to dashboard-ui."},
        ),
        GitHubActivityItem(
            "commit", "acme/dashboard-ui", identity, "docs1",
            "2024-02-08T10:00:00Z", "Polish dashboard migration guide", "",
        ),
        GitHubActivityItem(
            "pull_request_merged", "acme/payments", identity, "pay1",
            "2024-03-15T10:00:00Z", "Introduce idempotent payment capture", "",
            raw_payload={"body": "Add retry-safe state transitions and idempotency keys."},
        ),
    ]
    engine = RollupEngine(mock_fulcra_client)
    months = engine.generate_all_rollups(items, identity)["month"]
    summarizer = RollupSummarizer(mock_fulcra_client)

    def grounded_model(prompt: str) -> str:
        # A deterministic provider double must select only facts visible in the
        # actual prompt; prompt assertions make unsupported-claim regressions fail.
        if "Migrate analytics dashboard to React" in prompt:
            assert "Add dashboard metrics endpoint" in prompt
            return (
                "The dashboard-ui and metrics-api repositories converged on a React dashboard: "
                "the UI migration consumed a new typed metrics endpoint, linking the work as one initiative."
            )
        if "Polish dashboard migration guide" in prompt:
            return "February compressed into routine follow-through: polishing the dashboard migration guide."
        assert "Introduce idempotent payment capture" in prompt
        return (
            "In March, attention shifted to payments. The payments service introduced idempotent "
            "payment capture with retry-safe state transitions and idempotency keys."
        )

    summarizer.summarize_periods_and_write_back(
        months, grounded_model, save_to_fulcra=False,
    )
    candidate = format_narrative_document(
        identity, "Q1_2024", "2024-01-01T00:00:00Z",
        "2024-03-31T23:59:59Z", months, [],
        narrative_prose=(
            "Across Q1, the work moved from a coordinated React dashboard and metrics API "
            "initiative through documentation follow-through, then shifted to reliable payment capture."
        ),
    )

    # Compact fixture transcribed in the style of the v1 quality reference,
    # grounded in the same four source items rather than a different data set.
    v1_reference = """
January 2024
The dashboard-ui and metrics-api work formed one React dashboard initiative, pairing the
UI migration with its typed metrics endpoint.
February 2024
Routine work polished the dashboard migration guide.
March 2024
Focus shifted to idempotent payment capture with retry-safe transitions in payments.
"""
    reference_score = _rubric(v1_reference)
    candidate_score = _rubric(candidate)
    assert all(reference_score.values()), reference_score
    assert all(candidate_score.values()), candidate_score
    assert len(candidate.split()) < 900  # pacing guard against the former 20k-word dump
