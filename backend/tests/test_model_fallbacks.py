def test_no_dead_model_ids():
    from app.config import get_settings
    from app.services.design_images import _VISION_MODEL_FALLBACKS
    from app.services.ai_caption import ROUTER_MODEL_FALLBACKS
    
    DEAD_MODEL_IDS = {
        "ag/gemini-3.5-flash-high",
        "ag/gemini-3.7-flash-high",
        "ag/gemini-3.7-flash-low",
        "ag/gemini-3.7-flash-medium",
    }
    
    # Check config
    assert get_settings().nine_router_vision_model not in DEAD_MODEL_IDS
    
    # Check design_images
    for model_id in _VISION_MODEL_FALLBACKS:
        assert model_id not in DEAD_MODEL_IDS, f"Dead model {model_id} found in _VISION_MODEL_FALLBACKS"
        
    # Check ai_caption
    for model_id in ROUTER_MODEL_FALLBACKS:
        assert model_id not in DEAD_MODEL_IDS, f"Dead model {model_id} found in ROUTER_MODEL_FALLBACKS"


def test_no_retired_flash_model_patterns_in_fallbacks():
    from app.services.design_images import _VISION_MODEL_FALLBACKS
    from app.services.ai_caption import ROUTER_MODEL_FALLBACKS

    model_ids = _VISION_MODEL_FALLBACKS + ROUTER_MODEL_FALLBACKS
    assert all("gemini-3.6-flash" not in model for model in model_ids)
    assert all("gemini-3.7-flash" not in model for model in model_ids)
    assert all("gemini-3.5-flash-high" not in model for model in model_ids)


def test_health_check_classifies_retired_primary_errors():
    from unittest.mock import MagicMock, patch

    from app.tasks.ai_health_check import _is_retired_model_error

    assert _is_retired_model_error("404 NOT_FOUND: upstream returned an error")
    assert _is_retired_model_error("Requested entity was not found")
    assert not _is_retired_model_error("request timed out")

    response = MagicMock()
    response.choices = []
    with patch("openai.OpenAI") as openai_client, patch(
        "app.services.ai_caption.log_ai_copy_event"
    ) as log_event, patch(
        "app.services.nine_router.get_nine_router_config",
        return_value=MagicMock(base_url="https://router.test", api_key="key"),
    ):
        openai_client.return_value.chat.completions.create.side_effect = RuntimeError(
            "404 NOT_FOUND: Requested entity was not found"
        )
        from app.tasks.ai_health_check import _probe

        _probe(context="health_check_text", model="ag/retired", messages=[])

    assert "retired or unavailable" in log_event.call_args.kwargs["error_message"]
