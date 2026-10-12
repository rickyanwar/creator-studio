import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_ideas, api_tokens
from app.services.video_templates import allowed_video_templates, get_template, list_templates
from app.schemas.fanpage import FanpageUpdate
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_migration_c80000000013():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    rev = script.get_revision("c80000000013")
    assert rev.down_revision == "c80000000012"
    migration = rev.module

    with patch.object(migration.op, "add_column") as mock_add, \
         patch.object(migration.op, "execute") as mock_exec:
        migration.upgrade()
        assert mock_add.called
        assert mock_add.call_args[0][0] == "target_fanpages"
        exec_sqls = [call[0][0] for call in mock_exec.call_args_list]
        assert any("json_build_array(ai_video_template)" in sql for sql in exec_sqls)

    with patch.object(migration.op, "drop_column") as mock_drop:
        migration.downgrade()
        assert mock_drop.called
        assert mock_drop.call_args[0][0] == "target_fanpages"
        assert mock_drop.call_args[0][1] == "ai_video_templates"


def test_allowed_video_templates_helper():
    # 1. empty fanpage
    fp_empty = SimpleNamespace()
    assert allowed_video_templates(fp_empty) == []

    # 2. only ai_video_template set
    fp_single = SimpleNamespace(ai_video_template="motorsport_tech_explainer")
    assert allowed_video_templates(fp_single) == ["motorsport_tech_explainer"]

    # 3. ai_video_templates set and non-empty
    fp_multi = SimpleNamespace(
        ai_video_template="motorsport_tech_explainer",
        ai_video_templates=["motorsport_corner_masterclass", "motorsport_legend_innovation"]
    )
    assert allowed_video_templates(fp_multi) == [
        "motorsport_corner_masterclass",
        "motorsport_legend_innovation",
    ]

    # 4. ai_video_templates empty list falls back to ai_video_template
    fp_fallback = SimpleNamespace(
        ai_video_template="motorsport_tech_explainer",
        ai_video_templates=[]
    )
    assert allowed_video_templates(fp_fallback) == ["motorsport_tech_explainer"]


def test_fanpage_schema_validation_and_sync():
    # Invalid template key fails
    with pytest.raises(ValueError):
        FanpageUpdate(ai_video_templates=["invalid_template_xyz"])

    # Valid template list synchronizes ai_video_template to first item
    update = FanpageUpdate(ai_video_templates=["motorsport_corner_masterclass", "motorsport_tech_explainer"])
    assert update.ai_video_template == "motorsport_corner_masterclass"

    # Empty list clears ai_video_template
    update_empty = FanpageUpdate(ai_video_templates=[])
    assert update_empty.ai_video_template is None


def test_idea_creation_rejects_unallowed_template(db_session):
    fp = TargetFanpage(
        repliz_account_id=f"m8-val-{datetime.now().timestamp()}",
        name="Allowed Templates Test FP",
        is_active=True,
        ai_video_enabled=True,
        ai_video_templates=["motorsport_corner_masterclass"],
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)

    # Allowed template succeeds
    idea = ai_video_ideas.create_idea(
        db_session,
        fp.id,
        {"template_key": "motorsport_corner_masterclass", "topic": "Apex technique"},
        "owner"
    )
    assert idea.id is not None

    # Unallowed template raises ValueError (translates to 422 HTTP)
    with pytest.raises(ValueError) as exc:
        ai_video_ideas.create_idea(
            db_session,
            fp.id,
            {"template_key": "motorsport_tech_explainer", "topic": "Turbocharger cutaway"},
            "owner"
        )
    assert "not allowed" in str(exc.value)

    # Empty allowed templates keeps today's behaviour (allows any valid template)
    fp.ai_video_templates = None
    fp.ai_video_template = None
    db_session.commit()
    idea2 = ai_video_ideas.create_idea(
        db_session,
        fp.id,
        {"template_key": "motorsport_tech_explainer", "topic": "Any template allowed"},
        "owner"
    )
    assert idea2.id is not None

    db_session.delete(idea)
    db_session.delete(idea2)
    db_session.delete(fp)
    db_session.commit()


def test_planner_rotation_and_skip_unallowed(db_session, monkeypatch):
    import app.tasks.ai_video_plan as planner
    monkeypatch.setattr(planner, "SessionLocal", lambda: db_session)

    fp = TargetFanpage(
        repliz_account_id=f"m8-plan-{datetime.now().timestamp()}",
        name="Planner Rotation FP",
        is_active=True,
        ai_video_enabled=True,
        ai_video_daily_count=5,
        ai_video_templates=["motorsport_tech_explainer", "motorsport_corner_masterclass"],
        timezone="UTC",
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    fp_id = fp.id

    # Create approved ideas for both templates, plus one for an unallowed template
    idea_tech1 = VideoIdea(
        fanpage_id=fp_id,
        template_key="motorsport_tech_explainer",
        topic="Tech 1",
        status="approved",
        created_at=datetime(2026, 10, 1, 10, 0, 0),
    )
    idea_corner1 = VideoIdea(
        fanpage_id=fp_id,
        template_key="motorsport_corner_masterclass",
        topic="Corner 1",
        status="approved",
        created_at=datetime(2026, 10, 1, 11, 0, 0),
    )
    idea_unallowed = VideoIdea(
        fanpage_id=fp_id,
        template_key="motorsport_legend_innovation",
        topic="Legend 1",
        status="approved",
        created_at=datetime(2026, 10, 1, 9, 0, 0), # older than both!
    )
    db_session.add_all([idea_tech1, idea_corner1, idea_unallowed])
    db_session.commit()

    idea_unallowed_id = idea_unallowed.id

    try:
        with patch.object(planner, "fill_idea", return_value=({}, "T", "C", [{"index": 1, "mode": "t2v", "prompt": "p"}], "mock")):
            # First planner run: unallowed template is skipped despite being oldest.
            # Neither tech nor corner has been used in video_jobs yet. Tie breaker: oldest approved idea (idea_tech1 at 10:00).
            planner.plan_ai_videos.run()
            job1 = db_session.query(VideoJob).filter_by(fanpage_id=fp_id).order_by(VideoJob.id.desc()).first()
            assert job1 is not None
            assert job1.template_key == "motorsport_tech_explainer"
            unallowed_check = db_session.query(VideoIdea).filter_by(id=idea_unallowed_id).one()
            assert unallowed_check.status == "approved" # Left approved!

            # Second planner run: motorsport_tech_explainer was just used.
            # motorsport_corner_masterclass is least recently used. So idea_corner1 should be picked!
            planner.plan_ai_videos.run()
            job2 = db_session.query(VideoJob).filter_by(fanpage_id=fp_id).order_by(VideoJob.id.desc()).first()
            assert job2 is not None
            assert job2.template_key == "motorsport_corner_masterclass"
    finally:
        # Clean up
        db_session.query(VideoJob).filter_by(fanpage_id=fp_id).delete()
        db_session.query(VideoIdea).filter_by(fanpage_id=fp_id).delete()
        fp_to_delete = db_session.query(TargetFanpage).filter_by(id=fp_id).first()
        if fp_to_delete:
            db_session.delete(fp_to_delete)
            db_session.commit()


def test_hermes_endpoints(db_session):
    _, read_token = api_tokens.create_token(db_session, "hermes-read-token", ["read"])
    _, write_token = api_tokens.create_token(db_session, "hermes-write-token", ["ideas:write"])

    fp = TargetFanpage(
        repliz_account_id=f"m8-hermes-{datetime.now().timestamp()}",
        name="Hermes FP",
        is_active=True,
        ai_video_enabled=True,
        caption_language="fr",
        ai_video_templates=["motorsport_corner_masterclass"],
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)

    client = TestClient(app)
    headers_read = {"Authorization": f"Bearer {read_token}"}
    headers_write = {"Authorization": f"Bearer {write_token}"}

    # 1. GET /hermes/fanpages includes ai_video_templates and caption_language
    res_fps = client.get("/hermes/fanpages", headers=headers_read)
    assert res_fps.status_code == 200
    fp_item = next(item for item in res_fps.json() if item["id"] == fp.id)
    assert fp_item["caption_language"] == "fr"
    assert fp_item["ai_video_templates"] == ["motorsport_corner_masterclass"]

    # 2. GET /hermes/video-templates without fanpage_id returns all templates
    res_all_templates = client.get("/hermes/video-templates", headers=headers_read)
    assert res_all_templates.status_code == 200
    all_keys = {t["key"] for t in res_all_templates.json()}
    assert "motorsport_tech_explainer" in all_keys
    assert "motorsport_corner_masterclass" in all_keys

    # 3. GET /hermes/video-templates?fanpage_id={fp.id} filters by fanpage allowed templates
    res_filtered = client.get(f"/hermes/video-templates?fanpage_id={fp.id}", headers=headers_read)
    assert res_filtered.status_code == 200
    filtered_list = res_filtered.json()
    assert len(filtered_list) == 1
    assert filtered_list[0]["key"] == "motorsport_corner_masterclass"
    # Full purpose / template info present
    assert "name" in filtered_list[0]
    assert "niches" in filtered_list[0]
    assert "description" in filtered_list[0]
    assert "purpose" in filtered_list[0]
    assert "topic_ideas" in filtered_list[0]
    assert "variables_by_clips" in filtered_list[0]
    assert "clip_options" in filtered_list[0]
    assert "credits_per_clip" in filtered_list[0]

    # 4. POST /hermes/video-ideas rejects (422) if template not in fanpage allowed list
    payload_bad = {
        "fanpage_id": fp.id,
        "template_key": "motorsport_tech_explainer",
        "topic": "Hermes forbidden topic",
        "score": 85,
        "rationale": "High potential",
    }
    res_bad = client.post("/hermes/video-ideas", json=payload_bad, headers=headers_write)
    assert res_bad.status_code == 422

    # Clean up
    db_session.delete(fp)
    db_session.commit()
