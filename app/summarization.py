"""Harness-side Rollup Summarization module.

Proves the concrete mechanism for the model running the skill to perform
the summarization step against real M6 rollups:
- Task-prompt shape generation (`build_summarization_prompt`)
- Structured-input handoff packaging (`format_rollup_summary_handoff`)
- Deterministic write-back into the rollup's `note` field via `RollupSummarizer`
- Zero bundled LLM provider dependencies or API key requirements.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from rollups import ActivityRollup, RollupEngine


def build_summarization_prompt(rollup: ActivityRollup) -> str:
    """Generate a structured task prompt for an LLM/agent to summarize a rollup.

    Args:
        rollup: The ActivityRollup instance to format.

    Returns:
        A human and agent-readable text prompt payload.
    """
    repo_str = rollup.repo if rollup.repo else "All repositories"
    
    counts_lines = []
    if rollup.counts:
        for act_type, count in sorted(rollup.counts.items()):
            counts_lines.append(f"  - {act_type}: {count}")
    else:
        counts_lines.append("  - No activity recorded")

    breakdown_str = "\n".join(counts_lines)

    prompt = (
        f"Summarize the developer activity for '{rollup.github_identity}' "
        f"during the {rollup.period_type} period ({rollup.start_time} to {rollup.end_time}):\n"
        f"- Target Repository: {repo_str}\n"
        f"- Total Activity Count: {rollup.total_activity_count}\n"
        f"- Activity Breakdown:\n{breakdown_str}\n\n"
        f"Instructions: Provide a concise 1-2 sentence high-level summary of the developer's "
        f"contributions and focus areas for this period."
    )
    return prompt


def format_rollup_summary_handoff(rollup: ActivityRollup) -> Dict[str, Any]:
    """Package a rollup and its prompt into a structured input payload for the harness/agent.

    Args:
        rollup: The ActivityRollup instance to package.

    Returns:
        Structured dictionary payload for handoff.
    """
    return {
        "rollup_id": rollup.get_source_id(),
        "record_id": rollup.record_id,
        "period_type": rollup.period_type,
        "start_time": rollup.start_time,
        "end_time": rollup.end_time,
        "github_identity": rollup.github_identity,
        "repo": rollup.repo,
        "total_activity_count": rollup.total_activity_count,
        "counts": rollup.counts,
        "prompt": build_summarization_prompt(rollup),
    }


def format_batch_summary_handoff(rollups: List[ActivityRollup]) -> List[Dict[str, Any]]:
    """Package a list of rollups into structured input payloads for batch summarization.

    Args:
        rollups: List of ActivityRollup instances.

    Returns:
        List of structured handoff payloads.
    """
    return [format_rollup_summary_handoff(r) for r in rollups]


def generate_fallback_summary(rollup: ActivityRollup) -> str:
    """Generate a deterministic template-based summary for fallbacks or non-LLM execution.

    Args:
        rollup: The ActivityRollup instance.

    Returns:
        A deterministic summary sentence.
    """
    repo_str = f"in {rollup.repo}" if rollup.repo else "across all repos"
    if not rollup.counts:
        return (
            f"During the {rollup.period_type} period ({rollup.start_time[:10]} to {rollup.end_time[:10]}), "
            f"{rollup.github_identity} had no recorded GitHub activity {repo_str}."
        )

    parts = [f"{count} {act_type}" for act_type, count in sorted(rollup.counts.items())]
    breakdown = ", ".join(parts)
    return (
        f"During the {rollup.period_type} period ({rollup.start_time[:10]} to {rollup.end_time[:10]}), "
        f"{rollup.github_identity} completed {rollup.total_activity_count} total activities {repo_str} "
        f"({breakdown})."
    )


class RollupSummarizer:
    """Orchestrates harness-side rollup summarization handoff and deterministic write-back."""

    def __init__(self, client: Any) -> None:
        """Initialize RollupSummarizer with a Fulcra client instance.

        Args:
            client: Authenticated FulcraAPI or MockFulcraClient instance.
        """
        self.client = client
        self.engine = RollupEngine(client)

    def prepare_handoff(
        self, rollups: List[ActivityRollup]
    ) -> List[Dict[str, Any]]:
        """Prepare structured-input handoff payloads for a list of rollups.

        Args:
            rollups: List of ActivityRollup instances to summarize.

        Returns:
            List of structured input dictionary payloads.
        """
        return format_batch_summary_handoff(rollups)

    def write_back_summary(
        self,
        rollup: ActivityRollup,
        summary_text: str,
        save_to_fulcra: bool = True,
    ) -> ActivityRollup:
        """Update rollup with summary_text and deterministically write back to Fulcra.

        Args:
            rollup: The ActivityRollup to update.
            summary_text: Generated summary text.
            save_to_fulcra: If True, persists the updated record to Fulcra.

        Returns:
            The updated ActivityRollup instance.
        """
        rollup.summary_text = summary_text
        if save_to_fulcra:
            self.engine.save_rollups([rollup])
        return rollup

    def batch_write_back_summaries(
        self,
        summaries: List[Tuple[ActivityRollup, str]],
        save_to_fulcra: bool = True,
    ) -> List[ActivityRollup]:
        """Update and write back a batch of rollups and their summary texts.

        Args:
            summaries: List of (rollup, summary_text) tuples.
            save_to_fulcra: If True, persists updated records to Fulcra.

        Returns:
            List of updated ActivityRollup instances.
        """
        updated_rollups: List[ActivityRollup] = []
        for r, text in summaries:
            r.summary_text = text
            updated_rollups.append(r)

        if save_to_fulcra and updated_rollups:
            self.engine.save_rollups(updated_rollups)

        return updated_rollups

    def summarize_and_write_back(
        self,
        rollups: List[ActivityRollup],
        summary_provider_fn: Optional[Callable[[ActivityRollup], str]] = None,
        save_to_fulcra: bool = True,
    ) -> List[ActivityRollup]:
        """High-level summarization workflow execution.

        1. Packages task prompts and structured handoffs.
        2. Invokes summary_provider_fn (or fallback generator if None).
        3. Deterministically writes back updated records with summary_text in note JSON.

        Args:
            rollups: List of rollups to summarize.
            summary_provider_fn: Callable returning summary text for a rollup (e.g. running agent callback).
            save_to_fulcra: If True, persists updated rollups to Fulcra.

        Returns:
            List of updated ActivityRollup records with summary_text set.
        """
        fn = summary_provider_fn or generate_fallback_summary
        tuples: List[Tuple[ActivityRollup, str]] = []

        for r in rollups:
            summary_text = fn(r)
            tuples.append((r, summary_text))

        return self.batch_write_back_summaries(tuples, save_to_fulcra=save_to_fulcra)
