"""Tests for Muse motion self-check and server validation (TASK 4)."""
from fastapi.testclient import TestClient

from app.main import app
from tests.test_research_desk import clean, db, make_fanpage, pack, setup_db, token


def test_pack_without_motion_checked_is_rejected(db, clean):
    fp = make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)
    p = pack([fp.id])
    p["qc"].pop("motion_checked", None)

    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()
    result = res["results"][0]
    assert result["status"] == "rejected"
    assert any("qc.motion_checked" in e for e in result["errors"])


def test_pack_with_empty_motion_notes_is_rejected(db, clean):
    fp = make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)
    p = pack([fp.id])
    p["qc"]["motion_notes"] = ""

    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()
    result = res["results"][0]
    assert result["status"] == "rejected"
    assert any("qc.motion_notes" in e for e in result["errors"])


def test_variant_mechanism_action_not_mentioning_moving_part_is_rejected(db, clean):
    fp = make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)
    p = pack([fp.id])
    p["variants"][0]["variables"]["moving_part"] = "upper rear-wing flap"
    p["variants"][0]["variables"]["mechanism_action"] = "The hydraulic cylinder extends smoothly inside the bodywork."

    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()
    result = res["results"][0]
    assert result["status"] == "rejected"
    errors = " ".join(result["variants"][0]["errors"])
    assert "must move the moving_part" in errors


def test_variant_with_moving_part_in_fixed_parts_is_rejected(db, clean):
    fp = make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)
    p = pack([fp.id])
    p["variants"][0]["variables"]["moving_part"] = "upper rear-wing flap"
    p["variants"][0]["variables"]["fixed_parts"] = "the main plane and upper rear-wing flap"

    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()
    result = res["results"][0]
    assert result["status"] == "rejected"
    errors = " ".join(result["variants"][0]["errors"])
    assert "moving_part must not also be listed in fixed_parts" in errors


def test_good_pack_is_created(db, clean):
    fp = make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)
    p = pack([fp.id])

    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()
    result = res["results"][0]
    assert result["status"] == "created"
    assert result["variants"][0]["status"] == "created"


def test_brief_pack_rules_and_docs_contain_motion_check(db, clean):
    make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)

    brief = client.get("/research/brief", headers=headers).json()
    assert "motion_check" in brief["pack_rules"]

    docs = client.get("/research/docs", headers=headers).text
    assert "Motion self-check" in docs

    schema = client.get("/research/schema", headers=headers).json()
    qc_schema = schema["$defs"]["pack"]["properties"]["qc"]
    assert "motion_checked" in qc_schema["required"]
    assert "motion_notes" in qc_schema["required"]
