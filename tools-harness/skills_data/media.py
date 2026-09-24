"""External Higgsfield video-production skills.

These live as Claude-style SKILL.md files in the repo's `.agents/skills/` dir
(discoverable by the host agent, not clixen's own runtime). Registering them
here adds their metadata to the skills hub so clixen's orchestrator can list,
match, and dispatch them too. They reference Higgsfield MCP tools the
orchestrator sub-agent approximates best-effort — dispatch goes through
`load_external_skill` (external_path set), which reads the SKILL.md fresh.
"""

from __future__ import annotations

from pathlib import Path

from skills_hub import SKILLS, Skill  # noqa: F401

_REPO_SKILLS_DIR = Path(__file__).resolve().parents[2] / ".agents" / "skills"
_EXTERNAL_MODEL = "deepseek/deepseek-v4-flash"


def _register_external_skill(name: str, description: str, triggers: list[str],
                             max_rounds: int = 10) -> None:
    path = _REPO_SKILLS_DIR / name / "SKILL.md"
    SKILLS.append(Skill(
        id=f"ext.{name}",
        name=name,
        description=description,
        category="Media",
        model=_EXTERNAL_MODEL,
        tools=[],
        system_prompt="",  # read fresh from disk at dispatch time
        trigger_keywords=triggers,
        max_rounds=max_rounds,
        external_path=str(path),
    ))


_register_external_skill(
    "cartoon-scene-builder",
    "Turns a simple scene idea into a production-ready animation prompt using "
    "existing characters, locations, props, and voices. Plans the shots, camera "
    "direction, actions, timestamps, dialogue, and references while keeping every "
    "scene consistent with the world already built. Use when making an AI cartoon, "
    "animated series, or animated short in Higgsfield with reusable assets, or when "
    "changing a scene's length, setting up the show's world / Project Bible, splitting "
    "an episode into scenes, or fixing a generation where a character drifted.",
    [
        "cartoon scene", "ai cartoon", "animated series", "animated short",
        "production prompt", "project bible", "character sheet", "next scene",
        "new episode", "make a scene", "cartoon", "scene builder",
    ],
)

_register_external_skill(
    "fern-documentary",
    "Turn a single idea into a complete Fern-style 3D documentary production "
    "blueprint for a manual Higgsfield workflow (still assets → Seedance 2.5 "
    "30-second blocks with native audio → SeedAudio narration). One idea in, "
    "complete case file out — chapters, scene table, asset image prompts, "
    "continuous 30-second motion prompts with baked-in sound design, cold clinical "
    "narration, and an edit checklist.",
    [
        "fern documentary", "fern-style documentary", "fern video", "fern-style",
        "incident reconstruction", "minute-by-minute reconstruction",
        "3d documentary", "cinematic documentary", "documentary", "investigation video",
    ],
)

_register_external_skill(
    "vox-motion-graphics",
    "Produce a complete narrated motion-graphics explainer video end-to-end with "
    "Higgsfield MCP: trend/topic research, fact-checked script, a locked style key, "
    "animated clips, documentary voiceover, and one final assembled MP4. Two house "
    "styles: Vox-style Mixed Media collage and cinematic paper-diorama documentary.",
    [
        "vox-style video", "vox video", "vox pipeline", "motion graphics",
        "motion graphics explainer", "animated explainer", "data-driven video",
        "paper diorama", "newspaper collage", "explainer video", "faceless narrated",
    ],
)
