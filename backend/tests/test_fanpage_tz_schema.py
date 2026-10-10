import pytest
from pydantic import ValidationError
from app.schemas.fanpage import FanpageUpdate

def test_schema_valid_timezone():
    obj = FanpageUpdate(timezone="Europe/Paris", target_country="fr")
    assert obj.timezone == "Europe/Paris"
    assert obj.target_country == "FR"

def test_schema_invalid_timezone():
    with pytest.raises(ValidationError) as exc_info:
        FanpageUpdate(timezone="Mars/Olympus")
    assert "Unknown IANA timezone" in str(exc_info.value)

def test_schema_target_country_empty():
    obj = FanpageUpdate(target_country="")
    assert obj.target_country is None
    
    obj2 = FanpageUpdate(target_country=None)
    assert obj2.target_country is None
