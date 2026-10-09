# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Katakata is an open-source alternative to OpusClip — an AI-powered video clipping tool that transforms long-form content into viral short clips. AGPL-3.0 licensed. It is a fork of SupoClip (https://github.com/FujiwaraChoki/supoclip): user-facing text says "Katakata", while technical identifiers keep the `supoclip` name (Python package, `x-supoclip-*` auth headers, storage keys, Docker paths, the `supoclip-mcp` package) so existing deployments and clients keep working.

## Development Commands

### Docker (recommended)

```bash
docker-compose up -d              # Start all 5 services
docker-compose up -d --build      # Rebuild after changes
docker-compose logs -f backend    # Debug backend
docker-compose logs -f worker     # Debug video processing
docker-compose down               # Stop all services
```

Services: Frontend (:3107), Backend API (:8000, docs at /docs), Worker (ARQ), PostgreSQL (:5432), Redis (:6379)

### Backend (local)

Uses `uv` (not pip/poetry). Requires Python 3.11+, ffmpeg, running PostgreSQL and Redis. MediaPipe face detection also needs the system GL libraries (`apt install libgles2 libegl1` on Debian/Ubuntu); without them it falls back to OpenCV.

```bash
cd backend
uv venv .venv && source .venv/bin/activate
uv sync

# API server (uses refactored entry point)
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Worker process (required for video processing)
arq src.workers.tasks.WorkerSettings
```

### Frontend (local)

```bash
cd frontend
pnpm install
pnpm run dev          # Dev server with Turbopack
pnpm run build        # Prisma generate + Next.js build
pnpm run lint
```

### No tests

The project currently has no test files.

## Architecture

### System Overview

```
User → Frontend (Next.js 15) → Backend API (FastAPI) → Redis Queue → ARQ Worker
                                      ↓                                  ↓
                               PostgreSQL ←───────────────────────────────┘
```

Task creation returns immediately (<100ms). Video processing happens asynchronously in the worker. Frontend connects via SSE for real-time progress updates.

### Backend: Layered Architecture

The backend uses the layered application in `main.py`; `main_refactored.py` is a compatibility import:

```
api/routes/          → HTTP handlers (tasks.py, media.py)
services/            → Business logic (task_service.py, video_service.py)
repositories/        → Raw SQL via asyncpg (task_repository.py, clip_repository.py, source_repository.py)
workers/             → ARQ job queue (tasks.py, job_queue.py, progress.py)
utils/               → Thread pool helpers for blocking operations (async_helpers.py)
```

**Key patterns:**
- All DB access goes through repository classes using raw SQL (`text()` queries), not SQLAlchemy ORM
- Blocking operations (video processing, downloads, transcription) wrapped in `run_in_thread()` to avoid blocking the async event loop
- Progress tracking uses Redis pub/sub → SSE to frontend
- Task status flow: `queued → processing → completed/error/cancelled`

### Video Processing Pipeline

1. **Input** → YouTube URL (yt-dlp) or uploaded file
2. **Transcription** → AssemblyAI word-level timestamps (cached as `.transcript_cache.json`); spoken language is auto-detected (Swahili routes to `universal-2`) unless `TRANSCRIPTION_LANGUAGE` pins it
3. **AI Analysis** → current trends for `TRENDS_REGION` (default TZ; `trends.py`: Google Trends RSS + YouTube most-popular when a YouTube Data API key is set, cached 1h, fail-soft) are added to the prompt signals; each segment also gets a `post_caption` and 3-8 `hashtags` (stored on `generated_clips`). Pydantic AI selects 3-7 viral segments (10-45s each) with virality scoring. With `TWELVELABS_API_KEY`, `visual_highlights.py` also uploads a 360p proxy to TwelveLabs Pegasus; its highlights are fed to the prompt as signals and missed ones become `hook_type="visual"` clips
4. **Clip Generation** → MoviePy creates 9:16 clips with:
   - Face-centered cropping: MediaPipe → OpenCV DNN → Haar cascade (fallback chain). `media/face_detection.py` uses the legacy `mp.solutions` API when present, else the MediaPipe Tasks `FaceDetector` with the bundled full-range BlazeFace model (`media/models/`), scanning wide frames in square tiles so small faces in two-shots are found
   - Framings: `vertical` (face-tracked), `vertical_speaker` (Speaker cuts: hard cuts to whoever is talking, from AssemblyAI speaker labels mapped to faces by face motion, `media/speaker_cuts.py`), `vertical_pan` (glides between speakers by face motion), `vertical_split`, `original`. Speaker modes need a wide two-person shot with ≤2 scene cuts, else they fall back to `vertical`
   - Word-synced subtitles from AssemblyAI
   - Custom fonts (TTF files in `backend/fonts/`)
   - Optional transition effects (`backend/transitions/`)
   - Optional B-roll overlays (Pexels API)
   - Caption templates with animation styles; templates with `motion` (Kinetic, Kinetic Green) add kinetic typography from `media/motion_graphics.py`: slam-in hook title + accent bar, keyword callouts, punch-zooms on those beats (per-frame scale+crop before the subtitle burn), and a progress bar. Podcast Pro uses `motion: "auto"`: the AI returns a per-clip `motion_level` (none for emotional/serious moments, subtle for most, full for numbers/lists/high energy) and up to 3 `callout_words` copied from the segment, modelled on how The Diary of a CEO edits clips; it also uses a white `hook_banner`
   - Optional audio layers per task (`media/audio_enhancements.py`): background music from `backend/music/` and a spoken hook (Edge TTS, Swahili voices included), mixed in one ffmpeg pass with `-c:v copy`
5. **Storage** → Clips to `{TEMP_DIR}/clips/`, metadata to PostgreSQL

### Studio (stickman videos from an idea)

`backend/src/studio/` turns a described idea into a narrated stickman video, adapted from the MIT [Stickman Video Director](https://github.com/kaomei/stickman-video-director) skill (Style 2B, Cinematic Story). Flow: `studio_direct` job (research in `research.py`: Claude's `web_search_20260209` server tool when `LLM` is `anthropic:*`, else or on failure Wikipedia lookups planned by the LLM; then the director's Kiswahili 5-stage proposal, `director.py`) → user edits/approves → `studio_prompts` job (one standalone Gemini Omni Flash prompt per ~10s scene; clips are generated with no voice or music) → user generates clips in Google Flow and uploads one per scene, or `studio_generate` makes them with Gemini Omni Flash (`generate.py`, `POST /v1beta/interactions`, `gemini-omni-1.1-flash`, 10s 720p inline, needs `GOOGLE_API_KEY` with billing; per-scene failures are kept in `generation_errors`) → `studio_render` job (`voice.py`: Edge TTS per sentence with stage-based pace/pitch, natural pauses, a voice polish chain and word timings from WordBoundary events, so captions need no transcription; `assemble.py`: fit each clip to its narration by trimming, ≤1.3x slow-down then a held last frame, hard-cut concat, quiet clip SFX + voice + sidechain-ducked music, word captions, headline and overlay phrases). Genres: `explainer` (20-180s, one proposal call, 5-stage arc) and `documentary` (60-600s, `DocumentaryOutline` with chapters, then `SceneBatch` calls of ≤10 scenes per chapter carrying the story so far; scene prompts are written 8 per call for every genre). Inspiration styles (`styles.py`, job `studio_style`): yt-dlp reads up to 6 public videos' metadata and subtitles (channels → 3 latest) and the LLM writes a technique-only style guide that the director follows. Cloned voices (`voice_clone.py`, job `studio_voice`): OpenVoice V2's tone-colour converter (MIT, vendored in `studio/openvoice/`, checkpoint downloaded from Hugging Face `myshell-ai/OpenVoiceV2` into `{TEMP_DIR}/models/openvoice_v2` or `OPENVOICE_DIR` on first use) re-voices each Edge scene; length is preserved so word timings stay valid; uploads require a consent flag. Styles and voices live in `{TEMP_DIR}/studio_assets/{styles,voices}/<id>/record.json`. State is file-based: `{TEMP_DIR}/studio/<id>/production.json` plus scene clips; finished videos go to `{TEMP_DIR}/clips/studio/`. Jobs record errors on the production instead of raising, so ARQ never retries paid AI calls. Frontend: `/studio` and `/studio/[id]`, proxied through `/api/studio`.

### Frontend Architecture

- **Next.js 15** with App Router, React 19, TailwindCSS v4
- **ShadCN UI** (New York style, stone base color, Radix primitives)
- **Better Auth** with Prisma adapter for email/password auth
- **No global state library** — React hooks only (`useState`, `useEffect`, `useSession`)
- All pages use `"use client"` — SSR is minimal
- Prisma client generated to `frontend/src/generated/prisma/` (custom output path)
- Build: `prisma generate && next build` (Prisma generate runs on both build and postinstall)

**Auth flow:** Frontend calls Better Auth → session cookie → passes `user_id` header to backend API

### Database

PostgreSQL 15. Schema in `init.sql`. Mixed naming conventions:
- `tasks`, `sources`, `generated_clips` → snake_case
- `session`, `account`, `verification`, `users` → camelCase (Better Auth)
- UUIDs stored as VARCHAR(36)
- Auto-update triggers on `updated_at`/`updatedAt` columns

## Key Backend Files

| File | Purpose |
|------|---------|
| `src/main_refactored.py` | Compatibility import for existing deployments |
| `src/main.py` | Canonical FastAPI application factory |
| `src/api/routes/tasks.py` | Task CRUD, SSE progress, clip editing endpoints (711 lines) |
| `src/api/routes/media.py` | Fonts, transitions, uploads, templates |
| `src/services/task_service.py` | Task orchestration, clip editing logic (574 lines) |
| `src/services/video_service.py` | Video download, transcription, AI analysis, clip generation |
| `src/workers/tasks.py` | ARQ worker task definitions |
| `src/workers/job_queue.py` | Job queue management |
| `src/workers/progress.py` | Real-time progress via Redis |
| `src/ai.py` | Pydantic AI agents, system prompt, segment validation |
| `src/video_utils.py` | Video processing, cropping, subtitles (~820 lines) |
| `src/clip_editor.py` | Clip trim, split, merge, export presets |
| `src/broll.py` | Pexels API B-roll integration |
| `src/caption_templates.py` | Caption template system |
| `src/config.py` | Environment variable configuration |

## API Endpoints (routes in `api/routes/`)

**Task lifecycle:**
- `POST /start-with-progress` — Create task, enqueue to worker (returns task_id)
- `GET /tasks/` — List user tasks
- `GET /tasks/{id}` — Get task with clips
- `GET /tasks/{id}/progress` — SSE real-time progress stream
- `POST /tasks/{id}/cancel` — Cancel processing
- `POST /tasks/{id}/resume` — Resume cancelled/errored task
- `DELETE /tasks/{id}` — Delete task

**Clip editing:**
- `PATCH /tasks/{id}/clips/{clip_id}` — Trim clip
- `POST /tasks/{id}/clips/{clip_id}/split` — Split at timestamp
- `POST /tasks/{id}/clips/merge` — Merge selected clips
- `PATCH /tasks/{id}/clips/{clip_id}/captions` — Update captions
- `GET /tasks/{id}/clips/{clip_id}/export?preset=tiktok` — Export with platform preset (`tiktok`, `reels`, `shorts`, `square`, `landscape`; the last two blur-fill)

**Media:**
- `GET /fonts`, `GET /transitions`, `GET /caption-templates`, `GET /broll/status`, `GET /music` (tracks + TTS voices)
- `GET /discover/trending?region=TZ`, `GET /discover/search?q=&kind=videos|podcasts&min_minutes=` — Discover page (`discover.py`, yt-dlp search, no key); "Clip this" opens `/?url=<video>`
- `POST /upload` — Upload video file
- `GET /clips/{filename}` — Serve generated clips

**Studio:**
- `GET /studio/options`, `GET /studio/`, `POST /studio/` (idea, genre, aspect_ratio, duration_seconds 20-180 or 60-600 for documentaries, voice, research, style_id), `GET|DELETE /studio/{id}`
- `GET|POST /studio/styles`, `POST /studio/styles/{id}/refresh`, `DELETE /studio/styles/{id}`; `GET|POST /studio/voices` (multipart `name`, `base_voice`, `consent`, `sample`), `DELETE /studio/voices/{id}`; productions take `genre` and `style_id`, and `voice: custom:<id>`
- `POST /studio/{id}/revise` (feedback), `PATCH /studio/{id}/scenes/{n}` (narration, overlay_text), `POST /studio/{id}/approve`, `POST /studio/{id}/retry`
- `POST|DELETE /studio/{id}/scenes/{n}/clip` (multipart `clip`), `POST /studio/{id}/generate` (`scenes`, `replace`; Gemini), `POST /studio/{id}/render` (voice/caption/music settings), `GET /studio/{id}/files/final|scene-N`

**API keys (programmatic access):**
- `GET /api-keys/` — List the user's API keys (metadata only)
- `POST /api-keys/` — Create a key (plaintext `sk_...` returned exactly once)
- `DELETE /api-keys/{key_id}` — Revoke a key

API keys authenticate `/tasks/*`, `/fonts` and `/upload` directly via
`Authorization: Bearer sk_...` or `x-api-key`. Resolution lives in
`auth_headers.resolve_authenticated_user_id` (API key → DB lookup, else falls
back to the frontend's HMAC-signed session headers). Only the SHA-256 hash is
stored (`api_keys` table). The frontend manages keys at `/settings/api-keys`.

## Environment Variables

Required in `.env` (root) or `backend/.env`:

```bash
ASSEMBLY_AI_API_KEY=...              # Required: video transcription
LLM=google-gla:gemini-3-flash-preview # Format: provider:model-name
GOOGLE_API_KEY=...                   # Or OPENAI_API_KEY / ANTHROPIC_API_KEY
OLLAMA_BASE_URL=http://localhost:11434/v1  # Optional for ollama:* models
OLLAMA_API_KEY=...                   # Optional; required for Ollama Cloud
TRANSCRIPTION_LANGUAGE=auto          # auto-detect, or pin a code like sw (Swahili)

# Optional
PEXELS_API_KEY=...                   # B-roll stock footage
PIXABAY_API_KEY=...                  # B-roll fallback when Pexels has no match
TWELVELABS_API_KEY=...               # Visual highlights (dance/action) via Pegasus
REDIS_HOST=localhost                 # Default: localhost
REDIS_PORT=6379                      # Default: 6379
QUEUED_TASK_TIMEOUT_SECONDS=180      # Fail-safe for stuck tasks
TEMP_DIR=/tmp                        # Temp file storage
DATABASE_URL=postgresql+asyncpg://...
BETTER_AUTH_SECRET=...               # Frontend auth secret
```

## Common Workflows

### Adding fonts/transitions

Drop `.ttf` files into `backend/fonts/` or `.mp4` files into `backend/transitions/`. They auto-appear via their respective `GET` endpoints.

### Modifying AI clip selection

Edit `backend/src/ai.py`: `simplified_system_prompt` controls selection criteria, `TranscriptSegment` defines the output model, `get_most_relevant_parts_by_transcript()` runs analysis with validation.

### Video processing constraints

- Output: 9:16 vertical format, H.264, even pixel dimensions (`round_to_even()`)
- Subtitles positioned at 75% down the frame
- Virality scoring: `hook_score`, `engagement_score`, `value_score`, `shareability_score` (0-25 each, summed to `virality_score` 0-100)
- Each segment gets an AI-written `hook_title` (3-9 words) burned into the top safe area for the first ~4s (`build_hook_title_ass` in `video_utils.py`), persisted on `generated_clips.hook_title`
- Static talking-head crops get a slow ~5% Ken Burns punch-in (`kenburns_zoom_fragment`); tracked pans and split screens keep their own motion

## iOS App

The upstream SupoClip iOS app is not part of Katakata. The web app only shows an
App Store badge, Smart App Banner and app structured data when
`NEXT_PUBLIC_APP_STORE_ID` is set to your own app's id. The notes below describe
the upstream app and its billing hooks.

A native iOS app ships on the App Store
(https://apps.apple.com/us/app/supoclip/id6784760040, app id `6784760040`). Its
source lives outside this repo. It talks to the hosted API and bills through
RevenueCat, which is what `frontend/src/app/api/billing/revenuecat-webhook/`
serves; App Store subscribers are blocked from Stripe checkout/portal by the
"managed through the App Store" guard in the billing routes. The www references
the app via `APP_STORE_ID`/`APP_STORE_URL` in `frontend/src/lib/site.ts`
(Smart App Banner meta, JSON-LD `MobileApplication`, hero badge, footer link).

## MCP Server

`mcp/` is a standalone [MCP](https://modelcontextprotocol.io) server
(`supoclip-mcp`, Python/FastMCP, stdio) that exposes SupoClip to MCP clients
(Claude Desktop/Code, Cursor, …). It is a thin client over the REST API.

- **Default target:** the hosted API `https://api.supoclip.com`. Override with
  `SUPOCLIP_API_URL` for self-hosting (e.g. `http://localhost:8000`).
- **Auth:** a per-user API key in `SUPOCLIP_API_KEY` (see API keys above).
  Self-hosters may instead use `SUPOCLIP_USER_ID` (+ `SUPOCLIP_AUTH_SECRET` when
  signing is enforced).
- **Tools:** create/list/get/wait/cancel/resume/delete tasks, list/download/
  export clips, and public discovery (templates, transitions, fonts, B-roll).
- Run with `cd mcp && uv run supoclip-mcp`. Details in `mcp/README.md`.
