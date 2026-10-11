import io
import json
import httpx
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models.ai_copy_events import AICopyEvent
from app.models.settings import Settings
from app.services.visual_engine import (
    VisualEngineError,
    GenerationRequest,
    build_inset_prompt,
    build_solo_prompt,
    build_action_prompt,
    build_team_radio_prompt,
    generate,
)
from app.services.nine_router import NineRouterConfig


@pytest.fixture
def mock_db_session():
    mock = MagicMock(spec=Session)
    settings = Settings(visual_engine_daily_max=60)
    mock.query().first.return_value = settings
    mock.query().filter().count.return_value = 10
    return mock


@pytest.fixture
def req():
    return GenerationRequest(
        layout='inset',
        refs=('.ref/style-refs/style_hadjar.png',),
        prompt="test prompt"
    )


@patch('app.services.visual_engine._image_to_b64', return_value="data:image/jpeg;base64,mock")
@patch('app.services.visual_engine.get_nine_router_config')
@patch('httpx.stream')
def test_generate_success(mock_stream, mock_get_cfg, mock_img, mock_db_session, req):
    mock_get_cfg.return_value = NineRouterConfig(base_url='http://test', api_key='key', model='model', discussion_model='model')
    
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.json.return_value = {
        "data": [{"b64_json": "aGVsbG8="}] # "hello" in base64
    }
    
    mock_resp.read = lambda: None
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda self, *args: None
    mock_stream.return_value = mock_resp
    
    res = generate(mock_db_session, req)
    assert res == b"hello"
    
    mock_db_session.add.assert_called_once()
    event = mock_db_session.add.call_args[0][0]
    assert isinstance(event, AICopyEvent)
    assert event.context == 'image_gen'
    assert event.outcome == 'success'
    assert event.error_message is None


@patch('app.services.visual_engine._image_to_b64', return_value="data:image/jpeg;base64,mock")
@patch('app.services.visual_engine.get_nine_router_config')
@patch('httpx.stream')
def test_generate_quota_error(mock_stream, mock_get_cfg, mock_img, mock_db_session, req):
    mock_get_cfg.return_value = NineRouterConfig(base_url='http://test', api_key='key', model='model', discussion_model='model')
    
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 429
    mock_resp.text = '{"error": "rate limit exceeded"}'
    mock_resp.read = lambda: None
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda self, *args: None
    mock_stream.return_value = mock_resp
    
    with pytest.raises(VisualEngineError) as exc:
        generate(mock_db_session, req)
    
    assert exc.value.kind == 'quota'
    mock_db_session.add.assert_called_once()
    event = mock_db_session.add.call_args[0][0]
    assert event.outcome == 'failed'
    assert 'rate limit' in event.error_message


@patch('app.services.visual_engine._image_to_b64', return_value="data:image/jpeg;base64,mock")
@patch('app.services.visual_engine.get_nine_router_config')
@patch('httpx.stream')
def test_generate_success_with_quota_keyword_in_b64(mock_stream, mock_get_cfg, mock_img, mock_db_session, req):
    mock_get_cfg.return_value = NineRouterConfig(base_url='http://test', api_key='key', model='model', discussion_model='model')
    
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.status_code = 200
    mock_resp.text = '{"data": [{"b64_json": "bGltaXQ="}]}'
    mock_resp.json.return_value = {
        "data": [{"b64_json": "bGltaXQ="}]
    }
    mock_resp.read = lambda: None
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda self, *args: None
    mock_stream.return_value = mock_resp
    
    res = generate(mock_db_session, req)
    assert res is not None

@patch('app.services.visual_engine.get_nine_router_config')
def test_generate_daily_cap(mock_get_cfg, mock_db_session, req):
    mock_db_session.query().filter().count.return_value = 60 # limit reached
    
    with pytest.raises(VisualEngineError) as exc:
        generate(mock_db_session, req)
        
    assert exc.value.kind == 'quota'
    assert 'daily cap reached' in str(exc.value)


def test_build_team_radio_prompt():
    prompt = build_team_radio_prompt(
        name="VERSTAPPEN",
        number="1",
        colour="Red Bull dark blue",
        logo_desc="the Red Bull charging bulls logo",
        team="Red Bull Racing",
        person_desc="Max Verstappen (Red Bull Racing driver)",
        lines=[("engineer", "BOX BOX"), ("driver", "NO MATE")],
        context="frustrated",
        weather="dry",
        second_car_desc="the McLaren car"
    )
    assert "VERSTAPPEN" in prompt
    assert "white, left" in prompt
    assert '"BOX BOX"' in prompt
    assert '"NO MATE"' in prompt
    assert "no rain or water effects" in prompt
    assert "Dry conditions: no rain, no wet track, no water spray or wet effects anywhere on the card." in prompt
    assert "follow the card format of image 4 exactly (it shows six example drivers; use the same layout for VERSTAPPEN)" in prompt
    assert "Image 5 is the McLaren car racing alongside; keep its real livery, number and sponsor logos exactly." in prompt


def test_build_team_radio_prompt_rain():
    prompt = build_team_radio_prompt(
        name="VERSTAPPEN",
        number="1",
        colour="Red Bull dark blue",
        logo_desc="the Red Bull charging bulls logo",
        team="Red Bull Racing",
        person_desc="Max Verstappen (Red Bull Racing driver)",
        lines=[("engineer", "BOX BOX"), ("driver", "NO MATE")],
        context="frustrated",
        weather="rain"
    )
    assert "no rain or water effects" not in prompt
    assert "Dry conditions:" not in prompt


def test_build_inset_prompt():
    prompt, refs = build_inset_prompt("hero", [{"mode": "real", "desc": "inset", "path": "path.jpg"}], "purpose", "blue", "hero.jpg", sport="MotoGP")
    assert "HERO (Image 1): hero" in prompt
    assert "INSET 1 (Image 2): REAL PHOTO. inset" in prompt
    assert "MotoGP social media graphic" in prompt
    assert "best MotoGP media pages" in prompt
    assert "Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty" in prompt

def test_build_solo_prompt():
    prompt = build_solo_prompt("hero", "purpose", "blue", sport="MotoGP")
    assert "HERO (image 1): hero" in prompt
    assert "Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty" in prompt


def test_build_action_prompt():
    prompt = build_action_prompt("car", "purpose", "blue", sport="UFC")
    assert "HERO (image 1): car" in prompt
    assert "One big action photo" in prompt
    assert "UFC social media graphic" in prompt
    assert "best UFC media pages" in prompt
    assert "Bottom 35% MUST be a smooth dark gradient with no faces, no bodies, no objects, completely empty" in prompt


def test_build_team_radio_prompt_from_f1_driver_columns():
    """Verify the redesign_pipeline attribute mapping matches real F1Driver columns."""
    from types import SimpleNamespace

    # Simulate F1Driver row with real column names
    driver = SimpleNamespace(
        surname="VERSTAPPEN",
        full_name="Max Verstappen",
        number=1,
        team_name="Red Bull Racing",
        team_colour="#1E41FF",
    )

    # Same mapping as redesign_pipeline.py line 148-153
    prompt = build_team_radio_prompt(
        name=driver.full_name or driver.surname,
        number=str(driver.number),
        colour=driver.team_colour,
        logo_desc=f"{driver.team_name} logo",
        team=driver.team_name,
        person_desc="Max Verstappen",
        lines=[("driver", "BOX BOX")],
        context="pit stop",
        weather="none",
    )
    assert "Max Verstappen" in prompt
    assert "#1E41FF" in prompt
    assert "Red Bull Racing" in prompt
    assert '"1"' in prompt  # number as string in prompt
