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
