"""Structured outputs for the Studio director agents."""

from typing import List, Literal

from pydantic import BaseModel, Field

Stage = Literal[
    # Explainer (5-stage high-completion arc)
    "hook", "disrupt", "secrets", "truth", "elevation",
    # Documentary arc
    "cold_open", "context", "rising", "turning_point", "aftermath", "reflection",
]


class ResearchPlan(BaseModel):
    """Encyclopedia searches that would ground the video in facts."""

    queries: List[str] = Field(
        description=(
            "2-5 short English Wikipedia search queries (2-6 words each) for the "
            "people, events, concepts and numbers the video should get right."
        )
    )


class Beat(BaseModel):
    window: Literal["0-3s", "3-7s", "7-10s"] = Field(description="Time window inside the scene")
    action: str = Field(
        description=(
            "Concrete physical action of the stick figure and what changes on "
            "screen in this window (English)."
        )
    )


class Scene(BaseModel):
    number: int = Field(description="Scene number starting at 1")
    stage: Stage = Field(description="Which of the 5 narrative stages this scene serves")
    narration: str = Field(
        description=(
            "Exact Kiswahili voice-over for this ~10 second scene, written for the "
            "ear: 14-22 words, short sentences, numbers, dates, currency and "
            "abbreviations written out as spoken words."
        )
    )
    narration_english: str = Field(description="Faithful English translation of the narration, for review")
    setting: str = Field(
        description="Full-color cinematic environment for this scene, with lighting and mood (English)"
    )
    beats: List[Beat] = Field(description="Exactly three beats: 0-3s, 3-7s, 7-10s")
    camera: str = Field(description="Camera movement and framing (English)")
    sound_effects: str = Field(description="Synchronized sound effects on physical actions (English)")
    opening_state: str = Field(description="What the first frame shows; must match the previous scene's ending")
    ending_state: str = Field(description="What the last frame shows; the next scene opens on it")
    overlay_text: str = Field(
        description=(
            "Optional 2-5 word Kiswahili on-screen phrase added in post-production, "
            "or an empty string."
        )
    )


class Proposal(BaseModel):
    """The director's proposal shown to the user for approval."""

    title: str = Field(description="Video title in Kiswahili")
    title_english: str = Field(description="The title in English")
    core_message: str = Field(description="The one idea viewers should leave with (Kiswahili)")
    hook_title: str = Field(
        description="3-8 word Kiswahili headline burned on screen during the first seconds"
    )
    tone: str = Field(description="Emotional tone and pacing (English)")
    music_mood: str = Field(description="Background music mood, instruments and tempo (English)")
    narrator: str = Field(
        description="One-line narrator persona and delivery, e.g. warm, confident storyteller (English)"
    )
    scenes: List[Scene] = Field(description="Exactly the requested number of scenes, in order")
    post_caption: str = Field(
        description="1-2 sentence Kiswahili caption to post with the video, ending with a question"
    )
    hashtags: List[str] = Field(description="4-8 hashtags without spaces, mostly Kiswahili or East African")
    fact_check_notes: List[str] = Field(
        description=(
            "Each factual claim the narration makes and the research source it "
            "rests on, or 'general knowledge'. Empty if the script makes no claims."
        )
    )


class ScenePrompt(BaseModel):
    number: int = Field(description="Scene number")
    prompt: str = Field(description="The complete standalone English video-generation prompt")


class PromptPackage(BaseModel):
    continuity: str = Field(
        description="Short summary of the character, environment, audio and transition locks (English)"
    )
    prompts: List[ScenePrompt] = Field(description="One prompt per scene, in order")


class Chapter(BaseModel):
    title: str = Field(description="Short Kiswahili chapter title (2-5 words), shown on screen")
    title_english: str = Field(description="The chapter title in English")
    summary: str = Field(description="What this chapter covers and how it ends (English, 1-3 sentences)")
    scene_count: int = Field(description="Number of ~10 second scenes in this chapter (2-10)")


class DocumentaryOutline(BaseModel):
    """The documentary's spine, written before its scenes."""

    title: str = Field(description="Documentary title in Kiswahili")
    title_english: str = Field(description="The title in English")
    core_message: str = Field(description="What viewers should understand by the end (Kiswahili)")
    hook_title: str = Field(description="3-8 word Kiswahili headline burned on screen during the cold open")
    tone: str = Field(description="Emotional tone and pacing (English)")
    music_mood: str = Field(description="Score mood, instruments and tempo (English)")
    narrator: str = Field(description="One-line narrator persona and delivery (English)")
    chapters: List[Chapter] = Field(description="3-8 chapters in order; their scene counts add up to the requested total")
    post_caption: str = Field(description="1-2 sentence Kiswahili caption to post with the video, ending with a question")
    hashtags: List[str] = Field(description="4-8 hashtags without spaces, mostly Kiswahili or East African")


class SceneBatch(BaseModel):
    scenes: List[Scene] = Field(description="Exactly the requested scenes, in order")
    fact_check_notes: List[str] = Field(
        description="Each factual claim these scenes make and its research source number, or 'general knowledge'"
    )
