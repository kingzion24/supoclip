export type Stage = "hook" | "disrupt" | "secrets" | "truth" | "elevation";

export interface StudioScene {
  number: number;
  stage: Stage;
  narration: string;
  narration_english: string;
  setting: string;
  beats: { window: string; action: string }[];
  camera: string;
  sound_effects: string;
  opening_state: string;
  ending_state: string;
  overlay_text: string;
}

export interface StudioProposal {
  title: string;
  title_english: string;
  core_message: string;
  hook_title: string;
  tone: string;
  music_mood: string;
  narrator: string;
  scenes: StudioScene[];
  post_caption: string;
  hashtags: string[];
  fact_check_notes: string[];
}

export interface StudioBrief {
  idea: string;
  aspect_ratio: "9:16" | "16:9";
  duration_seconds: number;
  voice: string;
  voice_speed: number;
  research: boolean;
  captions: boolean;
  caption_template: string;
  hook_title: boolean;
  overlays: boolean;
  music: string | null;
  music_volume: number;
}

export type StudioStatus =
  | "researching" | "directing" | "proposal" | "prompting" | "shooting" | "generating" | "rendering" | "done" | "error";

export interface Production {
  id: string;
  status: StudioStatus;
  progress_message: string | null;
  error: string | null;
  error_stage: "direct" | "prompts" | "render" | null;
  created_at: string;
  updated_at: string;
  brief: StudioBrief;
  research: {
    method?: "web" | "wikipedia";
    notes?: string;
    queries?: string[];
    sources: { title: string; url: string; extract: string }[];
  } | null;
  generation_errors?: Record<string, string>;
  proposal: StudioProposal | null;
  prompts: { continuity: string; prompts: { number: number; prompt: string }[] } | null;
  approved_at: string | null;
  final: { filename: string; duration: number; rendered_at: string } | null;
  clips: Record<string, boolean>;
  revision: number;
  stuck?: boolean;
}

export interface ProductionSummary {
  id: string;
  title: string;
  status: StudioStatus;
  created_at: string;
  duration_seconds: number;
  aspect_ratio: string;
  final: Production["final"];
}

export interface StudioOptions {
  voices: { id: string; label: string }[];
  default_voice: string;
  caption_templates: string[];
  music: string[];
  durations: number[];
  max_duration: number;
  auto_generate: boolean;
}

export const BUSY_STATUSES: StudioStatus[] = ["researching", "directing", "prompting", "generating", "rendering"];

export const STAGE_LABELS: Record<Stage, string> = {
  hook: "Hook",
  disrupt: "Challenge",
  secrets: "Hidden detail",
  truth: "Core truth",
  elevation: "Close + question",
};

export const STATUS_LABELS: Record<StudioStatus, string> = {
  researching: "Researching",
  directing: "Writing script",
  proposal: "Review script",
  prompting: "Writing prompts",
  shooting: "Make the clips",
  generating: "Generating clips",
  rendering: "Rendering",
  done: "Done",
  error: "Needs attention",
};

export function formatDuration(seconds: number) {
  return seconds >= 60 && seconds % 60 === 0 ? `${seconds / 60} min` : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

export async function readError(response: Response, fallback: string) {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail) && data.detail[0]?.msg) return String(data.detail[0].msg);
    if (typeof data?.error === "string") return data.error;
  } catch {
    // Not JSON.
  }
  return fallback;
}
