"""The Studio director: research planning, the proposal, and scene prompts.

The rules below adapt the Stickman Video Director skill
(https://github.com/kaomei/stickman-video-director, MIT) to Kiswahili
narration and Katakata's assembly: generated clips carry no voice and no
music, because Katakata adds one continuous Kiswahili voice-over and one
music bed when it assembles the video.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput

from ..ai import (
    _build_transcript_model,
    _get_missing_llm_key_error,
    _is_transient_model_error,
    _uses_native_output,
)
from ..config import get_config
from ..runtime_settings import apply_settings_to_process_env
from .models import PromptPackage, Proposal, ResearchPlan

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SCENE_SECONDS = 10
MAX_ATTEMPTS = 3
CALL_TIMEOUT_SECONDS = 600

CHARACTER_FIRST = (
    "A minimalist 2D animated stick figure wearing a bright red beanie (smooth knit, "
    "no pom-pom) and a yellow t-shirt, with simple black stick limbs and shorts. "
    "Simple black lines, vibrant colors, smooth 2D animation style."
)
CHARACTER_REPEAT = (
    "The same minimalist 2D animated stick figure in a bright red beanie (smooth knit, "
    "no pom-pom) and yellow t-shirt, simple black stick limbs and shorts. Simple black "
    "lines, vibrant colors, smooth 2D animation style."
)
STYLE_2B_NEGATIVE = (
    "No photorealistic human skin, no 3D humanoid CGI models, no chaotic line glitches, "
    "no detailed eyes or pupils, no extra limbs, no changing line weight."
)
NO_TEXT_RULE = (
    "No visible words, letters, numbers, captions, subtitles, logos, watermarks or "
    "interface copy anywhere in the frame; icons only on signs, screens and cards."
)
AUDIO_RULE = (
    "Audio: natural ambient sound and crisp synchronized sound effects on physical "
    "actions only. No voiceover, no speech, no singing, no background music."
)

DIRECTOR_SYSTEM_PROMPT = f"""You are the director of Katakata Studio, writing short faceless stickman videos narrated in Kiswahili for TikTok, Instagram Reels and YouTube in Tanzania and East Africa. Topics are money, motivation, history, science, technology and psychology.

STORY ARCHITECTURE (5-stage high-completion arc, scaled to the number of scenes):
1. hook: an instant counter-intuitive question or visual paradox within the first 2-5 seconds. Never open with a greeting or a platitude.
2. disrupt: state what most people believe, then break it in one sentence.
3. secrets: reveal the hidden mechanism, insider detail or real obstacle.
4. truth: the core insight about money, people or the world, with a satisfying payoff.
5. elevation: a memorable closing line plus one question that invites comments.
Scene 1 is always the hook and the last scene is always elevation. Distribute the middle stages over the remaining scenes.

KISWAHILI NARRATION (it is read aloud by a text-to-speech voice, so write for the ear):
- Natural, modern Tanzanian Kiswahili as a good storyteller speaks it. Light everyday English loanwords are fine where Tanzanians use them; no Sheng unless the brief asks for it.
- 14-22 words per ~10-second scene. Short sentences. Use commas and question marks to shape the delivery.
- Write every number, date, percentage, currency amount and abbreviation as spoken words (e.g. "shilingi elfu hamsini", "mwaka elfu moja mia tisa sitini na moja", "asilimia thelathini"). No digits, no symbols, no emojis.
- Each scene's narration must make sense when heard straight after the previous one: one continuous story, not separate slogans.

FACTS:
- State a specific fact, number, date, name or quotation only if it appears in the research notes or is widely known general knowledge. Never invent statistics, studies or quotes.
- When the notes do not support a detail, say it in general terms instead.
- List every factual claim in fact_check_notes with its source number, or "general knowledge".

VISUALS (Style 2B, Cinematic Story):
- One recurring character in every scene: {CHARACTER_FIRST}
- Rich full-color cinematic environments with volumetric lighting and soft depth of field. Prefer settings that feel East African when they fit the story (a Dar es Salaam street at dusk, a Kariakoo market, a dala dala, a Zanzibar shore, Kilimanjaro at sunrise, a village shamba, a small duka), and use any setting the story needs.
- Each scene has three beats (0-3s, 3-7s, 7-10s) driven by concrete character actions (running, climbing, opening doors, catching falling coins, drawing glowing lines). At least four relevant visual devices per scene and a visible change every two to three seconds. Never leave the character idle.
- No abstract liquid or shape morphing. Use clear visual metaphors for abstract ideas.
- No visible writing inside the generated scenes. Optional 2-5 word Kiswahili overlay phrases go in overlay_text and are added by Katakata afterwards.
- Every scene's opening_state matches the previous scene's ending_state.

Write the title, core message, hook title, narration, post caption and hashtags in Kiswahili; write visual, camera, sound and setting directions in English."""

PROMPT_SYSTEM_PROMPT = f"""You write production prompts for a text-to-video model (Gemini Omni Flash in Google Flow) from an approved stickman storyboard. Each prompt generates one ~10-second clip on its own, so every prompt must repeat every lock and be usable without any other context.

Write each prompt in English, as flowing text, in this order:
1. Output: approximately ten seconds, the requested aspect ratio, 720p, 24 FPS, smooth 2D animation.
2. Environment: the scene's setting as a rich full-color cinematic environment with volumetric lighting and soft depth of field.
3. Character lock, verbatim. Scene 1: "{CHARACTER_FIRST}" Later scenes: "{CHARACTER_REPEAT}"
4. Composition for the aspect ratio: for 9:16 use depth, stacked vertical motion, bold central silhouettes and keep key action in the middle band; for 16:9 stage action left, center and right with lateral camera travel.
5. First frame: the scene's opening state.
6. Three timed beats "[0-3s] ... [3-7s] ... [7-10s] ..." with concrete character actions, at least four visual devices, and a visible change every two to three seconds. Use the phrase "rapid scene changes, kinetic motion-graphic transformations, and frequent visual events, while preserving an identical stick-figure design, constant line weight, and strict temporal consistency".
7. Camera movement.
8. "{AUDIO_RULE}" followed by the scene's specific sound effects.
9. Final frame: the scene's ending state, held briefly for a clean cut.
10. Negative constraints: "{STYLE_2B_NEGATIVE} {NO_TEXT_RULE} No abstract liquid or shape morphing. No speech bubbles or dialogue boxes."

Never include the Kiswahili narration, hexadecimal or RGB colors, or any instruction to show text."""


def scene_count(duration_seconds: int) -> int:
    return max(1, min(30, int(int(duration_seconds) / SCENE_SECONDS + 0.5)))


def _build_agent(output_type: Type[T], system_prompt: str) -> Agent[None, T]:
    runtime_config = get_config()
    apply_settings_to_process_env(runtime_config.as_runtime_settings())
    config_error = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
    if config_error:
        raise RuntimeError(config_error)
    return Agent[None, T](
        model=_build_transcript_model(runtime_config),
        output_type=(
            NativeOutput(output_type, strict=True)
            if _uses_native_output(runtime_config)
            else output_type
        ),
        system_prompt=system_prompt,
        output_retries=2,
    )


async def _run(output_type: Type[T], system_prompt: str, prompt: str) -> T:
    agent = _build_agent(output_type, system_prompt)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                result = await agent.run(prompt)
            return result.output
        except Exception as error:
            if not _is_transient_model_error(error) or attempt == MAX_ATTEMPTS:
                raise
            delay = 5 * 2 ** (attempt - 1)
            logger.warning("Studio AI call failed temporarily; retrying in %ss", delay)
            await asyncio.sleep(delay)
    raise RuntimeError("Studio AI call failed")


def build_research_prompt(brief: Dict[str, Any]) -> str:
    return (
        "Plan encyclopedia lookups for this video so its facts are right.\n"
        f"Video idea (may be Kiswahili or English): {brief['idea']}"
    )


def build_proposal_prompt(
    brief: Dict[str, Any],
    research_notes: str = "",
    feedback: Optional[str] = None,
    previous: Optional[Dict[str, Any]] = None,
) -> str:
    count = scene_count(brief["duration_seconds"])
    parts = [
        f"Write the director's proposal for this video.",
        f"Idea from the creator: {brief['idea']}",
        f"Aspect ratio: {brief['aspect_ratio']}",
        f"Length: {count * SCENE_SECONDS} seconds = exactly {count} scenes of about ten seconds.",
        "Style: 2B Cinematic Story (red beanie and yellow t-shirt stick figure in full-color cinematic settings).",
    ]
    if research_notes:
        parts.append(research_notes)
    else:
        parts.append("No research notes: use only the creator's idea and widely known general knowledge.")
    if previous and feedback:
        parts.append(
            "PREVIOUS PROPOSAL (revise it; keep what the feedback does not ask to change):\n"
            + json.dumps(previous, ensure_ascii=False)
        )
        parts.append(f"CREATOR FEEDBACK: {feedback}")
    return "\n\n".join(parts)


def build_prompts_prompt(brief: Dict[str, Any], proposal: Dict[str, Any]) -> str:
    storyboard = {
        "aspect_ratio": brief["aspect_ratio"],
        "tone": proposal.get("tone"),
        "scenes": [
            {
                key: scene.get(key)
                for key in (
                    "number", "stage", "narration_english", "setting", "beats",
                    "camera", "sound_effects", "opening_state", "ending_state",
                )
            }
            for scene in proposal.get("scenes", [])
        ],
    }
    return (
        f"Write exactly {len(storyboard['scenes'])} standalone prompts, one per scene, "
        "for this approved storyboard. The narration is given in English only so you "
        "understand each scene's meaning; it is added later as audio and must not be "
        "in the prompts.\n\n" + json.dumps(storyboard, ensure_ascii=False)
    )


def normalize_proposal(proposal: Proposal, expected_scenes: int) -> Dict[str, Any]:
    """Check the proposal's shape and renumber scenes."""
    data = proposal.model_dump()
    scenes = data.get("scenes") or []
    if not scenes:
        raise RuntimeError("The director returned no scenes. Please try again.")
    if len(scenes) != expected_scenes:
        logger.warning("Director returned %s scenes, expected %s", len(scenes), expected_scenes)
    for number, scene in enumerate(scenes, start=1):
        scene["number"] = number
        scene["narration"] = " ".join(scene.get("narration", "").split())
        scene["overlay_text"] = (scene.get("overlay_text") or "").strip()
    data["hashtags"] = [
        "#" + tag.lstrip("#").replace(" ", "")
        for tag in data.get("hashtags", [])
        if tag and tag.strip("# ")
    ][:8]
    return data


async def plan_research(brief: Dict[str, Any]) -> List[str]:
    plan = await _run(ResearchPlan, "You plan factual research for short educational videos.", build_research_prompt(brief))
    return [query for query in plan.queries if query.strip()][:5]


async def write_proposal(
    brief: Dict[str, Any],
    research_notes: str = "",
    feedback: Optional[str] = None,
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    proposal = await _run(
        Proposal,
        DIRECTOR_SYSTEM_PROMPT,
        build_proposal_prompt(brief, research_notes, feedback, previous),
    )
    return normalize_proposal(proposal, scene_count(brief["duration_seconds"]))


async def write_scene_prompts(brief: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    package = await _run(PromptPackage, PROMPT_SYSTEM_PROMPT, build_prompts_prompt(brief, proposal))
    prompts = {item.number: item.prompt.strip() for item in package.prompts}
    scenes = proposal.get("scenes", [])
    missing = [scene["number"] for scene in scenes if not prompts.get(scene["number"])]
    if missing:
        raise RuntimeError(f"The director skipped prompts for scenes {missing}. Please try again.")
    return {
        "continuity": package.continuity,
        "prompts": [{"number": scene["number"], "prompt": prompts[scene["number"]]} for scene in scenes],
    }
