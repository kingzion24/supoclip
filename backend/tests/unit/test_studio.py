from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import studio as studio_routes
from src.database import get_db
from src.studio import assemble, director, jobs, research, store, voice
from src.studio.models import Beat, Proposal, Scene


@pytest.fixture
def studio_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "studio_root", lambda: tmp_path / "studio")
    monkeypatch.setattr(store, "output_dir", lambda: tmp_path / "clips" / "studio")
    return tmp_path


def make_scene(number, stage="hook"):
    return Scene(
        number=number,
        stage=stage,
        narration="  Kwa nini   vijana hawaweki akiba?  ",
        narration_english="Why do young people not save?",
        setting="Dar es Salaam street at dusk",
        beats=[Beat(window="0-3s", action="a"), Beat(window="3-7s", action="b"), Beat(window="7-10s", action="c")],
        camera="slow push in",
        sound_effects="coins",
        opening_state="standing",
        ending_state="jumping",
        overlay_text=" Siri ",
    )


def make_proposal(count=2):
    return Proposal(
        title="Siri ya Akiba",
        title_english="The Secret of Saving",
        core_message="Akiba ni tabia",
        hook_title="Kwa nini huna akiba?",
        tone="warm",
        music_mood="soft piano",
        narrator="warm storyteller",
        scenes=[make_scene(9 + index) for index in range(count)],
        post_caption="Je, wewe unaweka akiba?",
        hashtags=["akiba", "#Pesa", "fedha tanzania", "#"],
        fact_check_notes=[],
    )


# --- director -----------------------------------------------------------


def test_scene_count_scales_with_duration():
    assert director.scene_count(60) == 6
    assert director.scene_count(35) == 4
    assert director.scene_count(5) == 1
    assert director.scene_count(1000) == 30


def test_normalize_proposal_renumbers_and_cleans():
    data = director.normalize_proposal(make_proposal(2), 2)
    assert [scene["number"] for scene in data["scenes"]] == [1, 2]
    assert data["scenes"][0]["narration"] == "Kwa nini vijana hawaweki akiba?"
    assert data["scenes"][0]["overlay_text"] == "Siri"
    assert data["hashtags"] == ["#akiba", "#Pesa", "#fedhatanzania"]


def test_proposal_prompt_includes_research_and_feedback():
    brief = {"idea": "akiba kwa vijana", "aspect_ratio": "9:16", "duration_seconds": 30}
    prompt = director.build_proposal_prompt(brief, "RESEARCH NOTES: x", "shorter hook", {"title": "old"})
    assert "exactly 3 scenes" in prompt
    assert "RESEARCH NOTES: x" in prompt
    assert "CREATOR FEEDBACK: shorter hook" in prompt
    assert '"title": "old"' in prompt


def test_scene_prompts_never_receive_kiswahili_narration():
    proposal = director.normalize_proposal(make_proposal(1), 1)
    prompt = director.build_prompts_prompt({"aspect_ratio": "9:16"}, proposal)
    assert "hawaweki" not in prompt
    assert "Why do young people not save?" in prompt


# --- research -----------------------------------------------------------


def test_parse_wikipedia_response_orders_and_trims():
    payload = {
        "query": {
            "pages": {
                "2": {"index": 2, "title": "Second", "extract": "b " * 2000, "fullurl": "https://en.wikipedia.org/wiki/Second"},
                "1": {"index": 1, "title": "First", "extract": "Compound  interest\n grows."},
                "3": {"index": 3, "title": "Empty", "extract": ""},
            }
        }
    }
    sources = research.parse_wikipedia_response(payload)
    assert [source["title"] for source in sources] == ["First", "Second"]
    assert sources[0]["extract"] == "Compound interest grows."
    assert sources[0]["url"] == "https://en.wikipedia.org/wiki/First"
    assert len(sources[1]["extract"]) <= research.MAX_EXTRACT_CHARS + 1


def test_gather_sources_deduplicates(monkeypatch):
    page = {"title": "A", "url": "u", "extract": "x"}
    monkeypatch.setattr(research, "search_wikipedia", lambda query: [page])
    assert research.gather_sources(["one", "two", " "]) == [page]
    assert "[1] A (u): x" in research.format_research_notes([page])
    assert research.format_research_notes([]) == ""


# --- voice --------------------------------------------------------------


def test_sentences_pauses_and_prosody():
    assert voice.split_sentences("Habari.  Je, uko tayari? Twende!") == ["Habari.", "Je, uko tayari?", "Twende!"]
    assert voice.pause_after("Je?", False) == voice.PAUSE_AFTER_QUESTION
    assert voice.pause_after("Ndiyo.", True) == voice.SCENE_TAIL_PAUSE
    assert voice.prosody_for("truth", "Ndiyo.") == ("-6%", "-2Hz")
    assert voice.prosody_for("hook", "Kwa nini?", speed=40) == ("+30%", "+4Hz")


def test_word_boundaries_become_caption_words():
    events = [
        {"offset": 1_000_000, "duration": 4_000_000, "text": "Habari"},
        {"offset": 6_000_000, "duration": 0, "text": " "},
    ]
    assert voice.words_from_boundaries(events, 2.0) == [{"text": "Habari", "start": 2.1, "end": 2.5}]


# --- assembly -----------------------------------------------------------


def test_fit_plan_trims_slows_and_holds():
    assert assemble.fit_plan(10, 8) == (1.0, 0.0)
    factor, hold = assemble.fit_plan(8, 9)
    assert factor == pytest.approx(1.125) and hold == 0
    factor, hold = assemble.fit_plan(5, 10)
    assert factor == assemble.MAX_SLOWDOWN and hold == pytest.approx(3.5)


def test_scene_command_holds_last_frame_and_adds_silence(tmp_path):
    command = assemble.build_scene_command(tmp_path / "c.mp4", tmp_path / "o.mp4", 10, 5, False, (1080, 1920))
    graph = command[command.index("-filter_complex") + 1]
    assert "setpts=1.3000*PTS" in graph
    assert "tpad=stop_mode=clone:stop_duration=3.500" in graph
    assert "crop=1080:1920" in graph
    assert "anullsrc" in " ".join(command)


def test_mix_command_ducks_music_under_voice(tmp_path):
    command = assemble.build_mix_command(
        tmp_path / "b.mp4", tmp_path / "v.wav", tmp_path / "o.mp4", 30, ["subtitles=filename=x"], tmp_path / "m.mp3"
    )
    graph = command[command.index("-filter_complex") + 1]
    assert "sidechaincompress" in graph and "amix=inputs=3" in graph
    assert "subtitles=filename=x" in graph
    no_music = assemble.build_mix_command(tmp_path / "b.mp4", tmp_path / "v.wav", tmp_path / "o.mp4", 30, [])
    assert "amix=inputs=2" in no_music[no_music.index("-filter_complex") + 1]


def test_overlay_ass_escapes_and_uppercases():
    content = assemble.build_overlay_ass([(0.3, 3.0, "siri {ya} akiba"), (4, 5, " ")], (1080, 1920))
    assert content.count("Dialogue:") == 1
    assert "SIRI" in content and "{YA}" not in content
    assert assemble.build_overlay_ass([], (1080, 1920)) is None


# --- store and jobs -----------------------------------------------------


def test_store_lists_only_the_owners_productions(studio_dirs):
    for owner in ("me", "me", "other"):
        production_id = store.new_production_id()
        with store.production_lock(production_id):
            store.save({"id": production_id, "user_id": owner, "created_at": store.now_iso(), "brief": {}})
    assert len(store.list_for_user("me")) == 2
    assert store.load("../etc") is None
    with pytest.raises(ValueError):
        store.production_dir("../../x")


def test_slugify_handles_kiswahili_titles():
    assert jobs.slugify("Siri ya Akiba: Kwa Nini?") == "siri-ya-akiba-kwa-nini"
    assert jobs.slugify("¿¿") == "studio-video"


async def test_direct_job_records_errors_without_raising(studio_dirs, monkeypatch):
    production_id = store.new_production_id()
    brief = {"idea": "akiba", "aspect_ratio": "9:16", "duration_seconds": 20, "research": True}
    with store.production_lock(production_id):
        store.save({"id": production_id, "user_id": "me", "brief": brief, "research": None})
    monkeypatch.setattr(jobs, "plan_research", AsyncMock(return_value=["saving"]))
    monkeypatch.setattr(jobs, "gather_sources", lambda queries: [{"title": "S", "url": "u", "extract": "e"}])
    monkeypatch.setattr(jobs, "write_proposal", AsyncMock(side_effect=RuntimeError("model down")))
    assert (await jobs.studio_direct({}, production_id))["status"] == "error"
    saved = store.load(production_id)
    assert saved["status"] == "error" and saved["error"] == "model down"
    assert saved["research"]["sources"][0]["title"] == "S"

    jobs.write_proposal.side_effect = None
    jobs.write_proposal.return_value = {"title": "T", "scenes": [{"number": 1}]}
    assert (await jobs.studio_direct({}, production_id))["status"] == "proposal"
    jobs.plan_research.assert_awaited_once()  # research is reused on retry
    assert store.load(production_id)["revision"] == 1


# --- routes -------------------------------------------------------------


@pytest.fixture
async def client(studio_dirs, monkeypatch):
    monkeypatch.setattr(studio_routes, "resolve_authenticated_user_id", AsyncMock(return_value="me"))
    enqueue = AsyncMock(return_value="job")
    monkeypatch.setattr(studio_routes.JobQueue, "enqueue_job", enqueue)
    app = FastAPI()
    app.include_router(studio_routes.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, enqueue


async def test_studio_flow_create_edit_approve_upload_render(client, monkeypatch, studio_dirs):
    http, enqueue = client
    response = await http.post("/studio/", json={"idea": "Kwa nini vijana hawaweki akiba", "duration_seconds": 25})
    assert response.status_code == 201
    created = response.json()
    production_id = created["id"]
    assert created["brief"]["duration_seconds"] == 30  # rounded to whole 10s scenes
    assert "user_id" not in created
    enqueue.assert_awaited_with("studio_direct", production_id)

    proposal = director.normalize_proposal(make_proposal(2), 2)
    store.update(production_id, status="proposal", proposal=proposal)

    edited = await http.patch(f"/studio/{production_id}/scenes/1", json={"narration": "Habari  mpya."})
    assert edited.json()["proposal"]["scenes"][0]["narration"] == "Habari mpya."
    assert (await http.post(f"/studio/{production_id}/approve")).status_code == 202
    assert (await http.post(f"/studio/{production_id}/approve")).status_code == 409  # busy

    store.update(production_id, status="shooting")
    blocked = await http.post(f"/studio/{production_id}/render")
    assert blocked.status_code == 409 and "1, 2" in blocked.json()["detail"]

    monkeypatch.setattr(studio_routes, "ffprobe_duration", lambda path: 9.5)
    for number in (1, 2):
        uploaded = await http.post(
            f"/studio/{production_id}/scenes/{number}/clip",
            files={"clip": ("c.mp4", b"video-bytes", "video/mp4")},
        )
        assert uploaded.json() == {"number": number, "duration": 9.5}
    assert (await http.get(f"/studio/{production_id}")).json()["clips"] == {"1": True, "2": True}

    rendered = await http.post(f"/studio/{production_id}/render", json={"voice": "sw-TZ-RehemaNeural"})
    assert rendered.status_code == 202
    assert store.load(production_id)["brief"]["voice"] == "sw-TZ-RehemaNeural"
    enqueue.assert_awaited_with("studio_render", production_id)


async def test_studio_rejects_other_users_and_bad_input(client, monkeypatch):
    http, _ = client
    created = (await http.post("/studio/", json={"idea": "Historia ya Tanganyika kwa ufupi"})).json()
    assert (await http.post("/studio/", json={"idea": "short"})).status_code == 422
    assert (await http.post("/studio/", json={"idea": "Historia ya Tanganyika", "voice": "x"})).status_code == 400
    monkeypatch.setattr(studio_routes, "resolve_authenticated_user_id", AsyncMock(return_value="intruder"))
    assert (await http.get(f"/studio/{created['id']}")).status_code == 404
    assert (await http.get("/studio/")).json() == {"productions": []}
    assert (await http.get(f"/studio/{created['id']}/files/../x")).status_code == 404


async def test_unreadable_upload_is_rejected(client, monkeypatch):
    http, _ = client
    created = (await http.post("/studio/", json={"idea": "Saikolojia ya pesa na furaha"})).json()
    store.update(created["id"], status="shooting", proposal=director.normalize_proposal(make_proposal(1), 1))
    monkeypatch.setattr(studio_routes, "ffprobe_duration", lambda path: 0)
    response = await http.post(
        f"/studio/{created['id']}/scenes/1/clip", files={"clip": ("x.mp4", b"nope", "video/mp4")}
    )
    assert response.status_code == 400
    assert not (store.production_dir(created["id"]) / "scene-01.mp4").exists()


def test_estimated_words_fill_the_sentence():
    words = voice.estimate_words("Habari za leo", 1.0, 3.0)
    assert [word["text"] for word in words] == ["Habari", "za", "leo"]
    assert words[0]["start"] == 1.0 and words[-1]["end"] <= 4.0
    assert voice.estimate_words("", 0, 1) == []


def test_stale_busy_production_can_be_retried():
    old = "2020-01-01T00:00:00+00:00"
    assert not store.is_busy({"status": "rendering", "updated_at": old})
    assert store.is_busy({"status": "rendering", "updated_at": store.now_iso()})
    assert not store.is_busy({"status": "done", "updated_at": store.now_iso()})


async def test_retry_restarts_a_stuck_render(client, monkeypatch):
    http, enqueue = client
    created = (await http.post("/studio/", json={"idea": "Teknolojia ya simu Afrika Mashariki"})).json()
    path = store.production_dir(created["id"]) / store.PRODUCTION_FILE
    import json as _json
    data = _json.loads(path.read_text())
    data.update(status="rendering", updated_at="2020-01-01T00:00:00+00:00")
    path.write_text(_json.dumps(data))
    assert (await http.get(f"/studio/{created['id']}")).json()["stuck"] is True
    assert (await http.post(f"/studio/{created['id']}/retry")).status_code == 202
    enqueue.assert_awaited_with("studio_render", created["id"])


# --- Gemini clip generation ----------------------------------------------

import base64 as _base64

import httpx as _httpx

from src.studio import generate as gemini


def test_gemini_request_and_video_extraction():
    body = gemini.build_request("a stick figure runs", "16:9")
    assert body["model"] == "gemini-omni-1.1-flash"
    assert body["response_format"] == {
        "type": "video", "aspect_ratio": "16:9", "resolution": "720p", "duration": "10s", "delivery": "inline",
    }
    interaction = {"steps": [
        {"type": "user_input"},
        {"type": "model_output", "content": [{"type": "text"}, {"type": "video", "data": "QQ=="}]},
    ]}
    assert gemini.extract_video(interaction) == {"data": "QQ==", "uri": None}
    with pytest.raises(RuntimeError, match="no video"):
        gemini.extract_video({"steps": [], "status": "failed"})


def test_generate_clip_saves_inline_video(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["key"] = request.headers["x-goog-api-key"]
        seen["revision"] = request.headers["api-revision"]
        video = _base64.b64encode(b"v" * 5000).decode()
        return _httpx.Response(200, json={"status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4", "data": video}]}]})

    real_client = _httpx.Client
    monkeypatch.setattr(gemini.httpx, "Client", lambda **kw: real_client(transport=_httpx.MockTransport(handler)))
    gemini.generate_clip("prompt", "9:16", "key-1", tmp_path / "scene-01.mp4")
    assert (tmp_path / "scene-01.mp4").read_bytes() == b"v" * 5000
    assert seen == {"key": "key-1", "revision": gemini.API_REVISION}


def test_generate_clip_explains_rejected_keys(tmp_path, monkeypatch):
    handler = lambda request: _httpx.Response(403, json={"error": {"message": "API key not valid"}})
    real_client = _httpx.Client
    monkeypatch.setattr(gemini.httpx, "Client", lambda **kw: real_client(transport=_httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="rejected the API key"):
        gemini.generate_clip("prompt", "9:16", "bad", tmp_path / "x.mp4")
    assert not (tmp_path / "x.mp4").exists()


async def test_generate_job_keeps_good_scenes_and_reports_failures(studio_dirs, monkeypatch):
    production_id = store.new_production_id()
    with store.production_lock(production_id):
        store.save({
            "id": production_id, "user_id": "me", "brief": {"aspect_ratio": "9:16"},
            "prompts": {"continuity": "", "prompts": [{"number": 1, "prompt": "a"}, {"number": 2, "prompt": "b"}]},
        })
    monkeypatch.setattr(jobs, "get_config", lambda: SimpleNamespace(google_api_key="k"))

    def fake_generate(prompt, aspect, key, path):
        if prompt == "b":
            raise RuntimeError("blocked")
        path.write_bytes(b"ok")

    monkeypatch.setattr(jobs, "generate_clip", fake_generate)
    result = await jobs.studio_generate({}, production_id, [1, 2])
    saved = store.load(production_id)
    assert result == {"status": "shooting", "failed": ["2"]}
    assert saved["generation_errors"] == {"2": "blocked"}
    assert (store.production_dir(production_id) / "scene-01.mp4").exists()


async def test_generate_route_needs_google_key_and_prompts(client, monkeypatch):
    http, enqueue = client
    created = (await http.post("/studio/", json={"idea": "Historia ya Zanzibar kwa ufupi"})).json()
    store.update(created["id"], status="proposal", proposal=director.normalize_proposal(make_proposal(2), 2))
    monkeypatch.setattr(studio_routes, "get_config", lambda: SimpleNamespace(google_api_key="k"))
    assert (await http.post(f"/studio/{created['id']}/generate")).status_code == 409
    store.update(created["id"], status="shooting", prompts={"continuity": "", "prompts": [
        {"number": 1, "prompt": "a"}, {"number": 2, "prompt": "b"}]})
    response = await http.post(f"/studio/{created['id']}/generate")
    assert response.status_code == 202 and response.json()["scenes"] == [1, 2]
    enqueue.assert_awaited_with("studio_generate", created["id"], [1, 2])
    store.update(created["id"], status="shooting")
    monkeypatch.setattr(studio_routes, "get_config", lambda: SimpleNamespace(google_api_key=None))
    assert (await http.post(f"/studio/{created['id']}/generate")).status_code == 400


# --- Claude web research --------------------------------------------------


def _block(**fields):
    return SimpleNamespace(**fields)


async def test_claude_research_collects_notes_and_cited_sources(monkeypatch):
    calls = []
    responses = [
        _block(stop_reason="pause_turn", content=[
            _block(type="web_search_tool_result", content=[_block(url="https://bot.go.tz", title="BoT")]),
        ]),
        _block(stop_reason="end_turn", content=[
            _block(type="text", text="1. Inflation was low.", citations=[
                _block(url="https://bot.go.tz", title="BoT", cited_text="Inflation  stood at 3%")]),
            _block(type="web_search_tool_result", content=_block(error_code="max_uses_exceeded")),
        ]),
    ]

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return responses[len(calls) - 1]

    fake = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: fake)
    result = await research.research_with_claude("Mfumuko wa bei Tanzania", "claude-opus-5-5", "key")
    assert result["method"] == "web" and result["notes"] == "1. Inflation was low."
    assert result["sources"] == [{"title": "BoT", "url": "https://bot.go.tz", "extract": "Inflation stood at 3%"}]
    assert calls[0]["tools"][0]["type"] == "web_search_20260209"
    assert calls[0]["extra_body"] == {"fallbacks": "default"}
    assert len(calls[1]["messages"]) == 2  # the paused turn was continued
    assert "Inflation was low" in research.format_web_notes(result)


async def test_research_falls_back_to_wikipedia(studio_dirs, monkeypatch):
    production_id = store.new_production_id()
    with store.production_lock(production_id):
        store.save({"id": production_id, "user_id": "me", "brief": {}})
    monkeypatch.setattr(jobs, "_anthropic_model", lambda: ("claude-opus-5-5", "key"))
    monkeypatch.setattr(jobs, "research_with_claude", AsyncMock(side_effect=RuntimeError("web search disabled")))
    monkeypatch.setattr(jobs, "plan_research", AsyncMock(return_value=["q"]))
    monkeypatch.setattr(jobs, "gather_sources", lambda queries: [{"title": "W", "url": "u", "extract": "e"}])
    result = await jobs._research(production_id, {"idea": "x"})
    assert result["method"] == "wikipedia" and result["sources"][0]["title"] == "W"
