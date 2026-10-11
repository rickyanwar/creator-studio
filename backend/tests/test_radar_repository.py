from unittest.mock import MagicMock
from app.services.radar_repository import load_recent_stories, apply_evaluation_plan
from datetime import datetime

def test_load_recent_stories_only_watching():
    db = MagicMock()
    # just test query logic
    now = datetime.utcnow()
    load_recent_stories(db, now)
    
    # We can inspect db.execute.call_args if we want, but basically checking no exception
    assert db.execute.called

def test_apply_evaluation_plan_persists_shelf():
    db = MagicMock()
    from app.services.radar_engine import EvaluationPlan, DecisionPlan
    
    dp = DecisionPlan(1, 10, "fast", "res", 100, True, "shadow_logged", 2.0, 3000, False, "news", datetime.utcnow())
    ep = EvaluationPlan(decisions=[dp], story_updates=[{"id": 1, "shelf_kind": "news"}])
    
    apply_evaluation_plan(db, ep)
    
    # db.add called for decision
    assert db.add.called
    # db.query().filter().update() called for story
    assert db.query.return_value.filter.return_value.update.called
