import pytest
from pydantic import ValidationError

from app.schemas.fanpage import FanpageBase, FanpageOut, FanpageUpdate


@pytest.mark.parametrize("schema", [FanpageBase, FanpageUpdate])
def test_speaker_design_toggles_default_off(schema):
    value = schema()
    assert value.news_visual_engine == "off"
    assert value.fb_visual_engine == "off"


@pytest.mark.parametrize("schema", [FanpageBase, FanpageUpdate])
@pytest.mark.parametrize("field", ["news_visual_engine", "fb_visual_engine"])
def test_speaker_design_toggles_accept_off_or_chatgpt(schema, field):
    assert getattr(schema(**{field: "off"}), field) == "off"
    assert getattr(schema(**{field: "chatgpt"}), field) == "chatgpt"


@pytest.mark.parametrize("schema", [FanpageBase, FanpageUpdate])
@pytest.mark.parametrize("field", ["news_visual_engine", "fb_visual_engine"])
def test_speaker_design_toggles_reject_other_engines(schema, field):
    with pytest.raises(ValidationError):
        schema(**{field: "gflow"})


def test_speaker_design_toggles_are_exposed_by_read_schema():
    fields = FanpageOut.model_fields
    assert fields["news_visual_engine"].annotation is str
    assert fields["fb_visual_engine"].annotation is str
