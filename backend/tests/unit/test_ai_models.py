from src.ai import TranscriptAnalysis


def test_transcript_segment_accepts_minimal_local_llm_shape():
    analysis = TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:15",
                    "segment": "This is a standalone clip candidate from a local model.",
                }
            ],
            "summary": "A short summary.",
            "key_topics": ["local model output"],
        }
    )

    segment = analysis.most_relevant_segments[0]

    assert segment.text == "This is a standalone clip candidate from a local model."
    assert segment.relevance_score == 0.75
    assert segment.reasoning == "Selected by the AI model as a clip candidate."
    assert segment.virality.total_score == 60


def test_transcript_analysis_accepts_local_llm_broll_shape():
    analysis = TranscriptAnalysis.model_validate(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:15",
                    "text": "This clip has enough words to pass the text validation.",
                }
            ],
            "summary": "A short summary.",
            "key_topics": ["local model output"],
            "broll_opportunities": [
                {
                    "segment_start_time": "00:00",
                    "segment_end_time": "00:15",
                    "broll": ["programming tutorial channels", "AI comparison graphic"],
                }
            ],
        }
    )

    broll = analysis.broll_opportunities[0]

    assert broll.timestamp == "00:00"
    assert broll.duration == 3.0
    assert broll.search_term == "programming tutorial channels, AI comparison graphic"


def test_current_claude_models_use_structured_output_not_forced_tools():
    from types import SimpleNamespace

    from pydantic_ai.models.anthropic import AnthropicModel

    from src import ai

    config = SimpleNamespace(llm="anthropic:claude-opus-5-5", anthropic_api_key="sk-ant-test")
    model = ai._build_transcript_model(config)
    assert isinstance(model, AnthropicModel)
    assert ai._uses_native_output(config)
    assert model.profile.supports_json_schema_output
    assert model.settings["anthropic_effort"] == "medium"
    assert model.settings["extra_body"] == {"fallbacks": "default"}

    older = SimpleNamespace(llm="anthropic:claude-haiku-4-5", anthropic_api_key="sk-ant-test")
    older_model = ai._build_transcript_model(older)
    assert "anthropic_effort" not in older_model.settings
