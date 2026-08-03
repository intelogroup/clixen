"""harness.py helper: render a specialist DispatchResult into a user-facing string."""

from __future__ import annotations

def _render_dispatch_result(dr) -> str:
    """Convert a DispatchResult into a user-facing string."""
    from agents.specialists.path_specialist import PathResult
    from agents.specialists.read_specialist import ReadResult
    from agents.specialists.data_specialist import DataResult
    from agents.specialists.form_specialist import FormResult
    from agents.specialists.write_specialist import WriteResult
    from agents.specialists.research_specialist import ResearchResult
    from agents.specialists.audio_specialist import AudioResult
    from agents.specialists.video_specialist import VideoResult
    from agents.specialists.scraper_specialist import ScraperResult
    from agents.specialists.transport_specialist import TransportResult

    r = dr.result
    lines: list[str] = []

    if isinstance(r, PathResult):
        if r.error:
            return f"[path] {r.error}"
        if r.paths:
            lines.append(f"Found {len(r.paths)} path(s):")
            for p in r.paths[:10]:
                lines.append(f"  {p}")
            if len(r.paths) > 10:
                lines.append(f"  ... and {len(r.paths) - 10} more")
        if r.summary:
            lines.append(r.summary)

    elif isinstance(r, ReadResult):
        if r.error:
            return f"[read] {r.error}"
        lines.append(f"Read: {r.path} (via {r.tool_used})")
        lines.append(r.text[:3000])

    elif isinstance(r, DataResult):
        if r.error:
            return f"[data] {r.error}"
        text = r.text[:3000] if r.text else ""
        if r.chart_path and f"![Chart]({r.chart_path})" not in text:
            text += f"\n\n![Chart]({r.chart_path})"
        if text:
            lines.append(text)

    elif isinstance(r, FormResult):
        if r.fields_detected:
            lines.append(f"Detected {len(r.fields_detected)} field(s):")
            for name, meta in list(r.fields_detected.items())[:10]:
                lines.append(f"  {name}: {meta}")
        if r.fields_filled:
            lines.append(f"Filled {len(r.fields_filled)} field(s)")
        if r.output_path:
            lines.append(f"Output: {r.output_path}")
        if r.verified:
            lines.append("Verified: fields match expected values")

    elif isinstance(r, WriteResult):
        if r.error:
            return f"[write] {r.error}"
        lines.append(f"Written to {len(r.paths)} path(s):")
        for p in r.paths:
            lines.append(f"  {p}")

    elif isinstance(r, ResearchResult):
        if r.error:
            return f"[research] {r.error}"
        lines.append(r.text[:3000])
        if r.sources:
            lines.append(f"Sources ({len(r.sources)}):")
            for s in r.sources[:5]:
                lines.append(f"  {s}")

    elif isinstance(r, AudioResult):
        if r.error:
            return f"[audio] {r.error}"
        if r.transcript:
            lines.append(f"Transcript ({r.path}):")
            lines.append(r.transcript[:2000])
        if r.output_path:
            lines.append(f"Output: {r.output_path}")

    elif isinstance(r, VideoResult):
        if r.error:
            return f"[video] {r.error}"
        if r.info:
            lines.append(r.info[:2000])
        if r.transcript:
            lines.append(f"Transcript: {r.transcript[:2000]}")
        lines.append(f"File: {r.file_path or 'N/A'}")

    elif isinstance(r, ScraperResult):
        if r.error:
            return f"[scraper] {r.error}"
        if r.content:
            lines.append(f"Content ({r.url}):")
            lines.append(r.content[:3000])
        if r.screenshot_path:
            lines.append(f"Screenshot: {r.screenshot_path}")

    elif isinstance(r, TransportResult):
        if r.error:
            return f"[transport] {r.error}"
        if r.text:
            lines.append(r.text)
        if r.services:
            lines.append(f"Services: {', '.join(r.services)}")

    else:
        return str(r) if r else "[dispatch] unknown result type"

    return "\n".join(lines) if lines else "[dispatch] completed"


