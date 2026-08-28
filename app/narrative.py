"""Narrative Generator module for Engineering Journey v2.

Reads precomputed Activity Rollups and Notability Signals from Fulcra, formats a paced
markdown document covering a user-selected date range (full history or sub-range),
includes an explicit Provenance Appendix tracing back to underlying record IDs,
and saves/names output files deterministically.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from checkpoint import format_iso, parse_iso
from notability import NotabilityEngine, NotabilitySignal
from rollups import ActivityRollup, RollupEngine, get_period_bounds



@dataclass
class NarrativeProvenance:
    """Parsed provenance record IDs extracted from a generated narrative document."""

    rollup_record_ids: List[str]
    signal_record_ids: List[str]
    raw_source_ids: List[str]


def parse_range_selection(
    selection_str: str,
    all_rollups: Optional[List[ActivityRollup]] = None,
) -> Tuple[str, str, str]:
    """Parse user input into (range_label, start_time_iso, end_time_iso).

    Supports:
    - "full" or "all": Full history
    - "YYYY": Single year (e.g. "2024")
    - "YYYY-YYYY": Year span (e.g. "2023-2025")
    - "YYYY-MM-DD to YYYY-MM-DD" or "YYYY-MM-DD:YYYY-MM-DD": Explicit ISO dates
    """
    raw = selection_str.strip().lower()

    if raw in ("full", "all", "full history", ""):
        if all_rollups:
            starts = [r.start_time for r in all_rollups if r.start_time]
            ends = [r.end_time for r in all_rollups if r.end_time]
            min_start = min(starts) if starts else "2000-01-01T00:00:00Z"
            max_end = max(ends) if ends else "2100-01-01T00:00:00Z"
            return "FULL_HISTORY", min_start, max_end
        return "FULL_HISTORY", "2000-01-01T00:00:00Z", "2100-01-01T00:00:00Z"

    # Match single year YYYY
    if re.match(r"^\d{4}$", raw):
        year = int(raw)
        start = f"{year}-01-01T00:00:00Z"
        end = f"{year}-12-31T23:59:59Z"
        return f"year_{year}", start, end

    # Match year range YYYY-YYYY or YYYY..YYYY or YYYY to YYYY
    year_range_match = re.match(r"^(\d{4})\s*(?:-|to|\.\.)\s*(\d{4})$", raw)
    if year_range_match:
        y1, y2 = int(year_range_match.group(1)), int(year_range_match.group(2))
        start_year, end_year = min(y1, y2), max(y1, y2)
        start = f"{start_year}-01-01T00:00:00Z"
        end = f"{end_year}-12-31T23:59:59Z"
        return f"{start_year}_to_{end_year}", start, end

    # Match explicit dates YYYY-MM-DD to YYYY-MM-DD or YYYY-MM-DD:YYYY-MM-DD
    date_range_match = re.match(
        r"^(\d{4}-\d{2}-\d{2})\s*(?:to|:|\s)\s*(\d{4}-\d{2}-\d{2})$", raw
    )
    if date_range_match:
        d1, d2 = date_range_match.group(1), date_range_match.group(2)
        start = f"{d1}T00:00:00Z"
        end = f"{d2}T23:59:59Z"
        label = f"{d1}_to_{d2}"
        return label, start, end

    # Fallback to full range
    return "FULL_HISTORY", "2000-01-01T00:00:00Z", "2100-01-01T00:00:00Z"


def prompt_for_range(
    input_fn: Optional[Callable[[str], str]] = None,
    all_rollups: Optional[List[ActivityRollup]] = None,
) -> Tuple[str, str, str]:
    """Interactively ask user for target range, or call custom input_fn if provided."""
    prompt_text = (
        "Enter target range for Engineering Journey narrative "
        "(e.g., 'full', '2024', '2023-2025', or '2024-01-01 to 2024-06-30'): "
    )
    if input_fn:
        user_val = input_fn(prompt_text)
    else:
        try:
            user_val = input(prompt_text)
        except EOFError:
            user_val = "full"

    return parse_range_selection(user_val, all_rollups)


def get_narrative_filename(
    github_identity: str,
    range_label: str,
    prefix: str = "engineering_journey",
) -> str:
    """Generate deterministic filename for narrative markdown output."""
    clean_identity = re.sub(r"[^\w\-]", "_", github_identity)
    clean_label = re.sub(r"[^\w\-]", "_", range_label)
    return f"{prefix}_{clean_identity}_{clean_label}.md"


def _human_period_heading(period_type: str, start_time: str) -> str:
    """Render calendar buckets as headings people naturally scan."""
    dt = parse_iso(start_time)
    if period_type == "month":
        return dt.strftime("%B %Y")
    if period_type == "quarter":
        return f"Q{((dt.month - 1) // 3) + 1} {dt.year}"
    if period_type == "year":
        return str(dt.year)
    return f"{start_time[:10]} ({period_type})"


def build_narrative_prompt(
    rollups: List[ActivityRollup],
    signals: List[NotabilitySignal],
    github_identity: str,
    range_label: str,
    start_time: str,
    end_time: str,
) -> str:
    """Generate structured prompt for an LLM/agent to compose narrative prose."""
    total_activities = sum(r.total_activity_count for r in rollups)
    repos = sorted(list(set(r.repo for r in rollups if r.repo)))

    # Identify notable signals (score >= 60 or specific categories)
    notable_signals = [
        s for s in signals
        if s.score >= 60.0 or any(c in s.categories for c in ("volume_surge", "first_activity", "focus_switch", "streak"))
    ]

    notable_lines = []
    for sig in notable_signals[:10]:
        cats = ", ".join(sig.categories) if sig.categories else "notable"
        repo_str = f" in {sig.repo}" if sig.repo else ""
        notable_lines.append(
            f"  - [{sig.period_type.upper()} {sig.start_time[:10]}]{repo_str}: "
            f"Score {sig.score:.1f}/100 ({cats}) - {sig.explanation}"
        )

    notable_text = "\n".join(notable_lines) if notable_lines else "  - No major activity spikes recorded."

    grounded_periods = []
    for rollup in sorted(rollups, key=lambda r: (r.start_time, r.period_type, r.repo or "")):
        if rollup.summary_text and rollup.period_type in ("month", "quarter", "year"):
            grounded_periods.append(
                f"- {rollup.period_type} {rollup.start_time[:10]} ({rollup.repo or 'cross-repo'}): "
                f"{rollup.summary_text} [rollup {rollup.get_source_id()}]"
            )
    period_text = "\n".join(dict.fromkeys(grounded_periods)) or "- No grounded period summaries are available."

    prompt = (
        f"Write a concise executive overview for '{github_identity}' "
        f"covering period '{range_label}' ({start_time[:10]} to {end_time[:10]}):\n"
        f"- Repositories Involved: {', '.join(repos) if repos else 'None'}\n"
        f"- Total Activities: {total_activities}\n"
        f"- Period Highlights & Signals:\n{notable_text}\n"
        f"- Grounded chronological period summaries:\n{period_text}\n\n"
        f"Instructions: In 1-3 paragraphs, explain the trajectory, major "
        f"technical themes, and meaningful focus shifts. Name concrete work "
        f"only when present in a grounded period summary. Synthesize across "
        f"repositories only where the evidence supports it. Avoid a stats "
        f"dump, repetition, unsupported impact claims, and key/value prose."
    )
    return prompt


def generate_fallback_narrative_prose(
    rollups: List[ActivityRollup],
    signals: List[NotabilitySignal],
    github_identity: str,
    range_label: str,
) -> str:
    """Generate deterministic fallback prose when no LLM callback is provided."""
    if not rollups:
        return (
            f"During the requested period ({range_label}), {github_identity} had no recorded GitHub activity. "
            f"This represents a quiet period with no repository contributions or issue/PR interactions."
        )

    # Prefer durable, model-authored hierarchy over rebuilding an overview
    # from counts. This supports narrative rewrites without another GitHub call.
    for period_type in ("year", "quarter", "month"):
        summaries = list(dict.fromkeys(
            r.summary_text
            for r in sorted(rollups, key=lambda item: item.start_time)
            if r.period_type == period_type and r.summary_text
        ))
        if summaries:
            return " ".join(summaries)

    total_activities = sum(r.total_activity_count for r in rollups)
    repos = sorted(list(set(r.repo for r in rollups if r.repo)))
    repo_str = f"across {len(repos)} repositories ({', '.join(repos[:5])}{'...' if len(repos) > 5 else ''})" if repos else "across repositories"

    # Find top categories
    cats_set: Set[str] = set()
    for s in signals:
        cats_set.update(s.categories)

    cat_summary = f" Activity included key milestones such as {', '.join(sorted(cats_set))}." if cats_set else ""

    prose = (
        f"{github_identity}'s engineering activity for {range_label} comprised {total_activities} total actions {repo_str}."
        f"{cat_summary} Over this period, work progressed across multiple development milestones, highlighting sustained "
        f"technical contributions and focus."
    )
    return prose


def format_narrative_document(
    github_identity: str,
    range_label: str,
    start_time: str,
    end_time: str,
    rollups: List[ActivityRollup],
    signals: List[NotabilitySignal],
    narrative_prose: Optional[str] = None,
) -> str:
    """Format full Markdown narrative document with metadata, story, and Provenance Appendix."""
    now_iso = format_iso(datetime.now(timezone.utc))
    total_activities = sum(r.total_activity_count for r in rollups if r.period_type == "day") or sum(r.total_activity_count for r in rollups)
    repos = sorted(list(set(r.repo for r in rollups if r.repo)))

    prose = narrative_prose or generate_fallback_narrative_prose(
        rollups, signals, github_identity, range_label
    )

    # Sort rollups and signals chronologically
    sorted_rollups = sorted(rollups, key=lambda r: (r.start_time, r.period_type, r.repo or ""))
    sorted_signals = sorted(signals, key=lambda s: (s.start_time, s.period_type, s.repo or ""))

    # Prepare story sections for major periods (Year, Quarter, Month, or Week)
    pacing_period = "month"
    paced_rollups = [r for r in sorted_rollups if r.period_type == pacing_period]
    if not paced_rollups:
        pacing_period = "quarter"
        paced_rollups = [r for r in sorted_rollups if r.period_type == pacing_period]
    if not paced_rollups:
        pacing_period = "year"
        paced_rollups = [r for r in sorted_rollups if r.period_type == pacing_period]
    if not paced_rollups:
        pacing_period = "week"
        paced_rollups = [r for r in sorted_rollups if r.period_type == pacing_period]
    if not paced_rollups:
        paced_rollups = sorted_rollups

    # Group by period window (period_type, start_time, end_time) so a
    # period with real cross-repo summary_text (written back by
    # summarize_periods_and_write_back -- see summarization.py) renders
    # as ONE synthesized paragraph spanning every repo active that
    # period, matching the narrative structure that made v1's output
    # engaging, instead of one mechanical bullet per single-repo rollup.
    # A period with no real summary_text yet still renders per-repo (the
    # original behavior), so this degrades honestly rather than hiding
    # data when summarization hasn't been run.
    period_groups: Dict[Tuple[str, str, str], List[ActivityRollup]] = {}
    period_order: List[Tuple[str, str, str]] = []
    for r in paced_rollups:
        key = (r.period_type, r.start_time, r.end_time)
        if key not in period_groups:
            period_groups[key] = []
            period_order.append(key)
        period_groups[key].append(r)

    period_sections = []
    for key in period_order:
        group = period_groups[key]
        period_type, period_start_time, period_end_time = key
        repos_in_period = sorted({r.repo for r in group if r.repo})

        # A real, written-back cross-repo summary exists if every rollup
        # in this period group shares the same non-fallback summary_text
        # (see write_back_period_summary: it deliberately writes the
        # identical string to every rollup in a group).
        summary_texts = {r.summary_text for r in group}
        has_shared_real_summary = (
            len(summary_texts) == 1
            and next(iter(summary_texts)) is not None
            and len(group) >= 1
        )

        total_count_this_period = sum(r.total_activity_count for r in group)
        rec_ids_str = ", ".join(f"`{r.get_source_id()}`" for r in group)

        if has_shared_real_summary and len(repos_in_period) > 0:
            shared_summary = next(iter(summary_texts))
            repo_list_str = ", ".join(f"`{repo}`" for repo in repos_in_period)
            period_sections.append(
                f"### {_human_period_heading(period_type, period_start_time)}\n\n"
                f"{shared_summary}\n"
            )
        else:
            # Limited mode stays compact: one transition per calendar period,
            # never an exhaustive per-repository template. It may quote a few
            # durable titles, but does not pretend those facts were synthesized.
            evidence_titles = list(dict.fromkeys(
                item.get("title", "")
                for r in group
                for item in r.evidence_items
                if item.get("title")
            ))
            repo_names = ", ".join(repos_in_period[:4])
            more_repos = " and others" if len(repos_in_period) > 4 else ""
            if evidence_titles:
                title_text = "; ".join(f"“{title}”" for title in evidence_titles[:3])
                transition = f"Recorded work included {title_text}"
                if repo_names:
                    transition += f" across {repo_names}{more_repos}"
                transition += "."
            else:
                transition = (
                    f"Activity continued across {repo_names}{more_repos}."
                    if repo_names else "Recorded activity continued during this period."
                )
            period_sections.append(
                f"### Transition: {_human_period_heading(period_type, period_start_time)}\n\n"
                f"{transition}\n"
            )

    paced_story = "\n".join(period_sections) if period_sections else "_No period rollups available._\n"

    # Notable highlights callout section
    high_notable_signals = [
        s for s in sorted_signals
        if s.score >= 50.0 or any(c in s.categories for c in ("volume_surge", "first_activity", "focus_switch", "streak"))
    ]
    notable_items = []
    for sig in sorted(high_notable_signals, key=lambda item: item.score, reverse=True)[:5]:
        repo_part = f" in `{sig.repo}`" if sig.repo else ""
        notable_items.append(
            f"- **{_human_period_heading(sig.period_type, sig.start_time)}**{repo_part}: {sig.explanation}"
        )

    highlights_section = "\n".join(notable_items) if notable_items else "- No specific high-volume surges recorded."

    # Build Provenance Appendix
    # 1. Rollup Records Table/List
    rollup_prov_lines = []
    all_raw_sources: Set[str] = set()

    for r in sorted_rollups:
        r_id = r.get_source_id()
        repo_s = r.repo or "all"
        counts_s = json.dumps(r.counts)
        rollup_prov_lines.append(
            f"| `{r_id}` | `{r.period_type}` | `{r.start_time[:10]}` to `{r.end_time[:10]}` | `{repo_s}` | `{r.total_activity_count}` |"
        )
        for src in r.sources:
            if src.startswith("raw:"):
                all_raw_sources.add(src)
        for item in r.evidence_items:
            source_id = item.get("source_id", "")
            if source_id.startswith("raw:"):
                all_raw_sources.add(source_id)

    rollup_table = "\n".join(rollup_prov_lines) if rollup_prov_lines else "| None | - | - | - | 0 |"

    # 2. Notability Signal Records Table/List
    signal_prov_lines = []
    for s in sorted_signals:
        s_id = s.get_source_id()
        repo_s = s.repo or "all"
        cats_s = ",".join(s.categories) if s.categories else "none"
        signal_prov_lines.append(
            f"| `{s_id}` | `{s.period_type}` | `{s.start_time[:10]}` | `{s.score:.1f}` | `{cats_s}` |"
        )
        for src in s.sources:
            if src.startswith("raw:"):
                all_raw_sources.add(src)

    signal_table = "\n".join(signal_prov_lines) if signal_prov_lines else "| None | - | - | 0.0 | none |"

    # 3. Raw Source References
    raw_sources_sorted = sorted(list(all_raw_sources))
    if raw_sources_sorted:
        raw_sources_text = "\n".join([f"- `{src}`" for src in raw_sources_sorted])
    else:
        raw_sources_text = "- None explicitly linked."

    has_grounded_summaries = any(r.summary_text for r in paced_rollups)
    quality_notice = "" if has_grounded_summaries else (
        "\n> **Limited deterministic fallback:** No model-authored period summaries "
        "were available. This activity-oriented report is not equivalent to the "
        "grounded Engineering Journey narrative. Configure a harness provider and "
        "run period summarization, then regenerate from the durable records.\n"
    )

    doc = (
        f"# Engineering Journey: {github_identity}\n\n"
        f"**Range:** {range_label} (`{start_time[:10]}` to `{end_time[:10]}`)\n"
        f"**Generated At:** `{now_iso}`\n"
        f"**Repositories:** {', '.join([f'`{repo}`' for repo in repos]) if repos else 'None'}\n"
        f"**Total Activities:** {total_activities}\n"
        f"{quality_notice}\n"
        f"---\n\n"
        f"## Story Overview\n\n"
        f"{prose}\n\n"
        f"## Notable Activity Highlights\n\n"
        f"{highlights_section}\n\n"
        f"## Paced Activity Narrative\n\n"
        f"{paced_story}\n"
        f"---\n\n"
        f"## Provenance Appendix\n\n"
        f"This document is backed by durable Fulcra records. The table below lists the primary Activity Rollups, "
        f"Notability Signals, and underlying source records used to construct this narrative.\n\n"
        f"### Activity Rollup Records\n\n"
        f"| Record ID / Source ID | Period | Date Bounds | Repository | Activity Count |\n"
        f"| --- | --- | --- | --- | --- |\n"
        f"{rollup_table}\n\n"
        f"### Notability Signal Records\n\n"
        f"| Record ID / Source ID | Period | Start Time | Score | Categories |\n"
        f"| --- | --- | --- | --- | --- |\n"
        f"{signal_table}\n\n"
        f"### Raw Activity Item Source References\n\n"
        f"{raw_sources_text}\n"
    )

    return doc


def parse_narrative_document(doc_content: str) -> NarrativeProvenance:
    """Parse IDs from their structural appendix tables, including UUID IDs."""
    rollup_ids: List[str] = []
    signal_ids: List[str] = []
    raw_source_ids: List[str] = []

    # Find Provenance Appendix section
    app_idx = doc_content.find("## Provenance Appendix")
    if app_idx != -1:
        appendix_text = doc_content[app_idx:]
    else:
        appendix_text = doc_content

    def table_ids(heading: str) -> List[str]:
        match = re.search(
            rf"### {re.escape(heading)}\s*\n(.*?)(?=\n### |\Z)",
            appendix_text,
            flags=re.DOTALL,
        )
        if not match:
            return []
        ids: List[str] = []
        for line in match.group(1).splitlines():
            if not line.lstrip().startswith("|") or "---" in line or "Record ID" in line:
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not cells:
                continue
            code_match = re.fullmatch(r"`([^`]+)`", cells[0])
            if code_match and code_match.group(1) != "None":
                ids.append(code_match.group(1))
        return list(dict.fromkeys(ids))

    rollup_ids = table_ids("Activity Rollup Records")
    signal_ids = table_ids("Notability Signal Records")

    raw_match = re.search(
        r"### Raw Activity Item Source References\s*\n(.*?)(?=\n### |\Z)",
        appendix_text,
        flags=re.DOTALL,
    )
    if raw_match:
        raw_source_ids = list(dict.fromkeys(
            item for item in re.findall(r"`(raw:[^`]+)`", raw_match.group(1))
        ))

    # Legacy malformed-document detection: prefixed IDs outside their table
    # remain invalid rather than becoming invisible after the structural parser.
    for item in re.findall(r"`([^`]+)`", appendix_text):
        if (item.startswith("rollup_") or item.startswith("rollup:")) and item not in rollup_ids:
            rollup_ids.append(item)
        elif (item.startswith("notability_") or item.startswith("notability:")) and item not in signal_ids:
            signal_ids.append(item)

    return NarrativeProvenance(
        rollup_record_ids=rollup_ids,
        signal_record_ids=signal_ids,
        raw_source_ids=raw_source_ids,
    )


def verify_narrative_provenance(
    doc_content: str,
    rollups: List[ActivityRollup],
    signals: List[NotabilitySignal],
) -> bool:
    """Verify that every record ID in the document's Provenance Appendix exists in the source rollups/signals."""
    prov = parse_narrative_document(doc_content)

    rollup_source_ids = set(r.get_source_id() for r in rollups)
    signal_source_ids = set(s.get_source_id() for s in signals)

    # Exact equality catches unknown IDs, missing rows, duplicate/truncated
    # appendices, and the former vacuous-success case in both directions.
    return (
        set(prov.rollup_record_ids) == rollup_source_ids
        and set(prov.signal_record_ids) == signal_source_ids
    )


class NarrativeGenerator:
    """Orchestrates querying rollups + signals, producing narrative markdown documents, and saving to disk."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.rollup_engine = RollupEngine(client)
        self.notability_engine = NotabilityEngine(client)

    def generate_narrative(
        self,
        github_identity: str,
        range_selection: str = "full",
        repo: Optional[str] = None,
        rollups: Optional[List[ActivityRollup]] = None,
        signals: Optional[List[NotabilitySignal]] = None,
        prose_provider_fn: Optional[Callable[[str], str]] = None,
        save_to_file: bool = False,
        output_dir: str = ".",
    ) -> Tuple[str, str, List[ActivityRollup], List[NotabilitySignal]]:
        """Main workflow: fetch rollups + signals for range, generate document, and optionally save file.

        Args:
            github_identity: Target GitHub handle
            range_selection: "full", "2024", "2023-2025", etc.
            repo: Optional repository filter
            rollups: Optional explicit ActivityRollup instances to use (bypasses storage query if provided)
            signals: Optional explicit NotabilitySignal instances to use (bypasses storage query if provided)
            prose_provider_fn: Callback to generate custom prose from task prompt
            save_to_file: If True, writes markdown to disk
            output_dir: Target directory for file output

        Returns:
            Tuple of (doc_content, filename, rollups, signals)
        """
        if rollups is None:
            # Query rollups from storage, with short poll loop for eventual consistency
            for attempt in range(3):
                rollups = self.rollup_engine.get_rollups(
                    github_identity=github_identity,
                    repo=repo,
                )
                if rollups or attempt == 2:
                    break
                time.sleep(0.5)

        range_label, start_time, end_time = parse_range_selection(
            range_selection, rollups
        )

        # Filter rollups to range
        filtered_rollups = [
            r for r in rollups
            if r.start_time >= start_time and r.end_time <= end_time
        ] if rollups else []

        if signals is None:
            for attempt in range(3):
                signals = self.notability_engine.get_signals(
                    github_identity=github_identity,
                    repo=repo,
                    start_time=start_time,
                    end_time=end_time,
                )
                if signals or attempt == 2:
                    break
                time.sleep(0.5)

        filtered_signals = [
            s for s in signals
            if s.start_time >= start_time and s.start_time <= end_time
        ] if signals else []

        # If no signals returned from storage, compute them on the fly from rollups
        if not filtered_signals and filtered_rollups:
            filtered_signals = self.notability_engine.compute_signals(filtered_rollups)

        # Optional prose callback
        narrative_prose = None
        if prose_provider_fn:
            prompt = build_narrative_prompt(
                filtered_rollups, filtered_signals, github_identity, range_label, start_time, end_time
            )
            narrative_prose = prose_provider_fn(prompt)

        doc_content = format_narrative_document(
            github_identity=github_identity,
            range_label=range_label,
            start_time=start_time,
            end_time=end_time,
            rollups=filtered_rollups,
            signals=filtered_signals,
            narrative_prose=narrative_prose,
        )

        filename = get_narrative_filename(github_identity, range_label)

        if save_to_file:
            filepath = f"{output_dir.rstrip('/')}/{filename}"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(doc_content)

        return doc_content, filename, filtered_rollups, filtered_signals
