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
from .models import DocumentaryOutline, PromptPackage, Proposal, ResearchPlan, SceneBatch

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
Scene 1 is always the hook and the last scene is always elevation. Distribute the middle stages over the remaining scenes. Use only these five stage names.

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


DOCUMENTARY_SYSTEM_PROMPT = DIRECTOR_SYSTEM_PROMPT.split("STORY ARCHITECTURE")[0] + """GENRE: DOCUMENTARY. You are making a narrated stickman documentary (2-10 minutes) for YouTube in the tradition of great explainer documentaries: a real story told with suspense, specific detail and a clear point of view.

DOCUMENTARY ARCHITECTURE (use these stage names):
1. cold_open: open inside the most dramatic or puzzling moment of the story, or with a question the whole film answers. No greeting, no "in this video".
2. context: the place, time and people; what the world looked like before.
3. rising: the chain of events or causes, building tension; each scene adds one new fact or turn.
4. turning_point: the revelation, decision or event that changed everything.
5. aftermath: consequences, including for Tanzania and East Africa when relevant.
6. reflection: what it means today, a memorable final line, and a question for the comments.
Split the film into chapters; end each chapter on a small cliffhanger or open question that pulls into the next.

NARRATION: measured documentary storytelling, 16-24 Kiswahili words per ~10-second scene, concrete names, places, dates and numbers from the research notes, sensory detail, varied sentence length. Write numbers, dates and currency as spoken words. One continuous story across all scenes.

FACTS: state a specific fact only if it appears in the research notes or is widely known general knowledge; never invent quotes, statistics or events. Keep the narration honest about uncertainty ("inasemekana", "wanahistoria wengi wanaamini") when sources disagree.

VISUALS (Style 2B, Cinematic Story): the red-beanie stick figure is the guide who walks through the story; other people appear as plain black stick figures distinguished only by simple props or clothing (a crown, a hat, a uniform, a bag of coins), never with faces. Use establishing shots, period-appropriate settings, icon-only maps and timelines, and recurring visual motifs. Three beats per scene with concrete actions, no visible writing, and each scene's opening_state matches the previous scene's ending_state.

Write titles, narration, captions and hashtags in Kiswahili; visual, camera, sound and setting directions in English."""

GENRES = {"explainer", "documentary"}
MAX_SCENES_PER_CALL = 10
PROMPTS_PER_CALL = 8


def scene_count(duration_seconds: int) -> int:
    return max(1, min(60, int(int(duration_seconds) / SCENE_SECONDS + 0.5)))


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


def _style_section(style_guide: Optional[str]) -> str:
    if not style_guide:
        return ""
    return (
        "STYLE INSPIRATION from channels the creator admires. Learn their techniques "
        "(hooks, pacing, structure, narration voice, visual ideas). Never copy their "
        "wording, titles, stories or catchphrases:\n" + style_guide
    )


def _common_parts(brief: Dict[str, Any], research_notes: str, style_guide: Optional[str]) -> List[str]:
    parts = [
        f"Idea from the creator: {brief['idea']}",
        f"Aspect ratio: {brief['aspect_ratio']}",
        "Style: 2B Cinematic Story (red beanie and yellow t-shirt stick figure in full-color cinematic settings).",
        research_notes or "No research notes: use only the creator's idea and widely known general knowledge.",
    ]
    style = _style_section(style_guide)
    if style:
        parts.append(style)
    return parts


def build_proposal_prompt(
    brief: Dict[str, Any],
    research_notes: str = "",
    feedback: Optional[str] = None,
    previous: Optional[Dict[str, Any]] = None,
    style_guide: Optional[str] = None,
) -> str:
    count = scene_count(brief["duration_seconds"])
    parts = [
        "Write the director's proposal for this video.",
        f"Length: {count * SCENE_SECONDS} seconds = exactly {count} scenes of about ten seconds.",
        *_common_parts(brief, research_notes, style_guide),
    ]
    if previous and feedback:
        parts.append(
            "PREVIOUS PROPOSAL (revise it; keep what the feedback does not ask to change):\n"
            + json.dumps(previous, ensure_ascii=False)
        )
        parts.append(f"CREATOR FEEDBACK: {feedback}")
    return "\n\n".join(parts)


def build_outline_prompt(
    brief: Dict[str, Any],
    research_notes: str = "",
    feedback: Optional[str] = None,
    previous: Optional[Dict[str, Any]] = None,
    style_guide: Optional[str] = None,
) -> str:
    count = scene_count(brief["duration_seconds"])
    parts = [
        "Write the outline of this documentary: its title, tone and chapters.",
        f"Length: {count * SCENE_SECONDS} seconds = exactly {count} scenes of about ten seconds in total, "
        "split over the chapters.",
        *_common_parts(brief, research_notes, style_guide),
    ]
    if previous and feedback:
        summary = {
            key: previous.get(key)
            for key in ("title", "core_message", "hook_title", "tone", "chapters")
        }
        summary["narration"] = [scene.get("narration") for scene in previous.get("scenes", [])]
        parts.append(
            "PREVIOUS VERSION (revise it; keep what the feedback does not ask to change):\n"
            + json.dumps(summary, ensure_ascii=False)
        )
        parts.append(f"CREATOR FEEDBACK: {feedback}")
    return "\n\n".join(parts)


def build_chapter_prompt(
    brief: Dict[str, Any],
    outline: Dict[str, Any],
    chapter_index: int,
    first_number: int,
    count: int,
    previous_scenes: List[Dict[str, Any]],
    research_notes: str = "",
    feedback: Optional[str] = None,
    style_guide: Optional[str] = None,
) -> str:
    chapter = outline["chapters"][chapter_index]
    last = previous_scenes[-1] if previous_scenes else None
    recent = [scene.get("narration") for scene in previous_scenes[-3:]]
    parts = [
        f"Write scenes {first_number} to {first_number + count - 1} (exactly {count} scenes) of this documentary: "
        f"chapter {chapter_index + 1} of {len(outline['chapters'])}, \"{chapter['title']}\" ({chapter['title_english']}).",
        "OUTLINE:\n" + json.dumps(
            {key: outline.get(key) for key in ("title", "core_message", "tone", "narrator", "chapters")},
            ensure_ascii=False,
        ),
        f"This chapter: {chapter['summary']}",
        "Number the scenes from " + str(first_number) + ".",
    ]
    if last:
        parts.append(
            "The story so far ends with this narration: " + json.dumps(recent, ensure_ascii=False)
            + f"\nThe last frame shows: {last.get('ending_state')}. Open the first scene on it."
        )
    else:
        parts.append("This is the very start of the film: scene 1 is the cold open.")
    if chapter_index == len(outline["chapters"]) - 1:
        parts.append("This is the final chapter: end with the reflection and a question for the comments.")
    parts += _common_parts(brief, research_notes, style_guide)[1:]
    if feedback:
        parts.append(f"CREATOR FEEDBACK to respect: {feedback}")
    return "\n\n".join(parts)


def build_prompts_prompt(brief: Dict[str, Any], proposal: Dict[str, Any], scenes: Optional[List[Dict[str, Any]]] = None) -> str:
    scenes = proposal.get("scenes", []) if scenes is None else scenes
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
            for scene in scenes
        ],
    }
    return (
        f"Write exactly {len(storyboard['scenes'])} standalone prompts, one per scene, "
        "for this approved storyboard. The narration is given in English only so you "
        "understand each scene's meaning; it is added later as audio and must not be "
        "in the prompts.\n\n" + json.dumps(storyboard, ensure_ascii=False)
    )


def _clean_scenes(scenes: List[Dict[str, Any]], first_number: int = 1) -> List[Dict[str, Any]]:
    for number, scene in enumerate(scenes, start=first_number):
        scene["number"] = number
        scene["narration"] = " ".join(scene.get("narration", "").split())
        scene["overlay_text"] = (scene.get("overlay_text") or "").strip()
    return scenes


def _clean_hashtags(tags: List[str]) -> List[str]:
    return [
        "#" + tag.lstrip("#").replace(" ", "")
        for tag in tags or []
        if tag and tag.strip("# ")
    ][:8]


def normalize_proposal(proposal: Proposal, expected_scenes: int) -> Dict[str, Any]:
    """Check the proposal's shape and renumber scenes."""
    data = proposal.model_dump()
    scenes = data.get("scenes") or []
    if not scenes:
        raise RuntimeError("The director returned no scenes. Please try again.")
    if len(scenes) != expected_scenes:
        logger.warning("Director returned %s scenes, expected %s", len(scenes), expected_scenes)
    _clean_scenes(scenes)
    data["hashtags"] = _clean_hashtags(data.get("hashtags", []))
    data["genre"] = "explainer"
    return data


def balance_chapters(chapters: List[Dict[str, Any]], total: int) -> List[int]:
    """Scene counts per chapter that add up to ``total`` exactly."""
    if not chapters:
        return []
    counts = [max(1, int(chapter.get("scene_count") or 1)) for chapter in chapters]
    while sum(counts) > total and max(counts) > 1:
        counts[counts.index(max(counts))] -= 1
    index = 0
    while sum(counts) < total:
        counts[index % len(counts)] += 1
        index += 1
    return counts


async def plan_research(brief: Dict[str, Any]) -> List[str]:
    plan = await _run(ResearchPlan, "You plan factual research for short educational videos.", build_research_prompt(brief))
    return [query for query in plan.queries if query.strip()][:5]


async def _write_documentary(
    brief: Dict[str, Any],
    research_notes: str,
    feedback: Optional[str],
    previous: Optional[Dict[str, Any]],
    style_guide: Optional[str],
    on_progress=None,
) -> Dict[str, Any]:
    total = scene_count(brief["duration_seconds"])
    outline = (
        await _run(
            DocumentaryOutline,
            DOCUMENTARY_SYSTEM_PROMPT,
            build_outline_prompt(brief, research_notes, feedback, previous, style_guide),
        )
    ).model_dump()
    if not outline.get("chapters"):
        raise RuntimeError("The director returned no chapters. Please try again.")
    counts = balance_chapters(outline["chapters"], total)
    scenes: List[Dict[str, Any]] = []
    notes: List[str] = []
    for index, count in enumerate(counts):
        outline["chapters"][index]["scene_count"] = count
        written = 0
        while written < count:
            batch_size = min(MAX_SCENES_PER_CALL, count - written)
            if on_progress:
                await on_progress(
                    f"Writing chapter {index + 1} of {len(counts)}: "
                    f"{outline['chapters'][index]['title']}…"
                )
            batch = await _run(
                SceneBatch,
                DOCUMENTARY_SYSTEM_PROMPT,
                build_chapter_prompt(
                    brief, outline, index, len(scenes) + 1, batch_size, scenes,
                    research_notes, feedback, style_guide,
                ),
            )
            new_scenes = [scene.model_dump() for scene in batch.scenes][:batch_size]
            if not new_scenes:
                raise RuntimeError("The director returned an empty chapter. Please try again.")
            _clean_scenes(new_scenes, len(scenes) + 1)
            for scene in new_scenes:
                scene["chapter"] = index
            if written == 0 and not new_scenes[0]["overlay_text"] and index > 0:
                new_scenes[0]["overlay_text"] = outline["chapters"][index]["title"]
            scenes += new_scenes
            notes += batch.fact_check_notes
            written += len(new_scenes)
    start = 1
    for chapter, count in zip(outline["chapters"], counts):
        chapter["first_scene"] = start
        start += count
    return {
        **{key: outline[key] for key in (
            "title", "title_english", "core_message", "hook_title", "tone", "music_mood",
            "narrator", "chapters", "post_caption",
        )},
        "hashtags": _clean_hashtags(outline.get("hashtags", [])),
        "scenes": scenes,
        "fact_check_notes": notes,
        "genre": "documentary",
    }


async def write_proposal(
    brief: Dict[str, Any],
    research_notes: str = "",
    feedback: Optional[str] = None,
    previous: Optional[Dict[str, Any]] = None,
    style_guide: Optional[str] = None,
    on_progress=None,
) -> Dict[str, Any]:
    if brief.get("genre") == "documentary":
        return await _write_documentary(brief, research_notes, feedback, previous, style_guide, on_progress)
    proposal = await _run(
        Proposal,
        DIRECTOR_SYSTEM_PROMPT,
        build_proposal_prompt(brief, research_notes, feedback, previous, style_guide),
    )
    return normalize_proposal(proposal, scene_count(brief["duration_seconds"]))


async def write_scene_prompts(brief: Dict[str, Any], proposal: Dict[str, Any], on_progress=None) -> Dict[str, Any]:
    """One prompt per scene, written in batches so long documentaries fit."""
    scenes = proposal.get("scenes", [])
    prompts: Dict[int, str] = {}
    continuity = ""
    for start in range(0, len(scenes), PROMPTS_PER_CALL):
        chunk = scenes[start:start + PROMPTS_PER_CALL]
        if on_progress and len(scenes) > PROMPTS_PER_CALL:
            await on_progress(f"Writing scene prompts {start + 1}-{start + len(chunk)} of {len(scenes)}…")
        package = await _run(PromptPackage, PROMPT_SYSTEM_PROMPT, build_prompts_prompt(brief, proposal, chunk))
        continuity = continuity or package.continuity
        wanted = {scene["number"] for scene in chunk}
        for item in package.prompts:
            if item.number in wanted and item.prompt.strip():
                prompts[item.number] = item.prompt.strip()
        # Some models renumber from 1 inside a batch; map by position then.
        if not wanted & set(prompts) and len(package.prompts) == len(chunk):
            for scene, item in zip(chunk, package.prompts):
                prompts[scene["number"]] = item.prompt.strip()
    missing = [scene["number"] for scene in scenes if not prompts.get(scene["number"])]
    if missing:
        raise RuntimeError(f"The director skipped prompts for scenes {missing}. Please try again.")
    return {
        "continuity": continuity,
        "prompts": [{"number": scene["number"], "prompt": prompts[scene["number"]]} for scene in scenes],
    }
