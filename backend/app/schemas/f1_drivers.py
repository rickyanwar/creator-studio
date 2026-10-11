from pydantic import BaseModel, Field, constr, ConfigDict
from typing import Optional
from datetime import datetime


class F1DriverBase(BaseModel):
    season: int = Field(..., ge=2020, le=2100)
    surname: constr(min_length=2, max_length=32, pattern=r"^[A-Z \-]+$")
    full_name: Optional[str] = None
    number: int = Field(..., ge=0, le=99)
    team_name: constr(max_length=64)
    team_colour: constr(pattern=r"^#[0-9A-Fa-f]{6}$")
    verified: bool = False

class F1DriverCreate(F1DriverBase):
    pass

class F1DriverUpdate(BaseModel):
    season: Optional[int] = Field(None, ge=2020, le=2100)
    surname: Optional[constr(min_length=2, max_length=32, pattern=r"^[A-Z \-]+$")] = None
    full_name: Optional[str] = None
    number: Optional[int] = Field(None, ge=0, le=99)
    team_name: Optional[constr(max_length=64)] = None
    team_colour: Optional[constr(pattern=r"^#[0-9A-Fa-f]{6}$")] = None
    verified: Optional[bool] = None

class F1DriverOut(F1DriverBase):
    id: int
    team_logo_path: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
