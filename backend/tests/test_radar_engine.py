import pytest
from datetime import datetime, timedelta
from app.services.radar_engine import (
    cluster_posts, account_baselines, account_curve, evaluate_story_for_fanpage,
    evaluate_all, nightly_plan, shadow_report, PostView, StoryView, SnapshotView, FanpageRadarConfig,
    DecisionPlan, DecisionRecord
)

def make_post(id: int, key: str, taken_at: datetime, phash: str=None, caption: str="", niche="f1", ig_source_id=None, likes=None, images=1) -> PostView:
    return PostView(id, key, niche, ig_source_id, f"code{id}", taken_at, caption, phash, likes, images, [])

def make_snap(obs: datetime, age: float, likes: int) -> SnapshotView:
    return SnapshotView(obs, age, likes)

def make_story(id: int, niche: str, first: datetime, status: str="watching", hint="unknown", shelf=None, expires=None, member_count=1, distinct=1, best_id=None, final_likes=None) -> StoryView:
    return StoryView(id, niche, first, first, member_count, distinct, best_id, hint, shelf, expires, status, 0.0, final_likes)

def make_cfg(fid: int, niche: str, min_l: int=1000, fast_r: float=2.0, conf_r: float=1.5, burst: bool=True, viral_only=None) -> FanpageRadarConfig:
    return FanpageRadarConfig(fid, True, True, {niche}, min_l, conf_r, fast_r, 60, burst, 72, 168, viral_only or set())

@pytest.fixture
def now():
    return datetime(2026, 1, 1, 12, 0)

# CLUSTER TESTS
def test_cluster_phash_match(now):
    p1 = make_post(1, "a", now, phash="0000000000000000")
    s1 = make_story(10, "f1", now, best_id=100)
    p_s1 = make_post(100, "b", now, phash="0000000000000001") # dist 1
    plan = cluster_posts([p1], [(s1, [p_s1])], None, now)
    assert len(plan.updates) == 1
    assert plan.updates[0].story_id == 10

def test_cluster_caption_match(now):
    p1 = make_post(1, "a", now, caption="the quick brown fox jumps over the lazy dog")
    s1 = make_story(10, "f1", now, best_id=100)
    p_s1 = make_post(100, "b", now, caption="quick brown fox jumps over lazy dog entirely")
    plan = cluster_posts([p1], [(s1, [p_s1])], None, now)
    assert len(plan.updates) == 1
    assert plan.updates[0].story_id == 10

def test_cluster_mode1_joins_niche(now):
    # Post with no niche but ig_source_id
    p1 = make_post(1, "a", now, niche=None, ig_source_id=99, caption="match this")
    s1 = make_story(10, "f1", now, best_id=100)
    p_s1 = make_post(100, "b", now, niche="f1", caption="match this")
    plan = cluster_posts([p1], [(s1, [p_s1])], None, now)
    assert len(plan.updates) == 1
    assert plan.updates[0].story_id == 10

def test_cluster_unrelated_creates_new(now):
    p1 = make_post(1, "a", now, caption="completely different")
    s1 = make_story(10, "f1", now, best_id=100)
    p_s1 = make_post(100, "b", now, caption="another text here")
    plan = cluster_posts([p1], [(s1, [p_s1])], None, now)
    assert len(plan.new_stories) == 1
    assert len(plan.updates) == 0

def test_cluster_older_than_48h_skipped(now):
    p1 = make_post(1, "a", now, caption="match this")
    s1 = make_story(10, "f1", now - timedelta(hours=49), best_id=100) # last_member_at is 49h ago
    s1 = StoryView(10, "f1", now - timedelta(hours=49), now - timedelta(hours=49), 1, 1, 100, "unknown", None, None, "watching", 0.0, None)
    p_s1 = make_post(100, "b", now, caption="match this")
    plan = cluster_posts([p1], [(s1, [p_s1])], None, now)
    assert len(plan.new_stories) == 1

def test_cluster_best_post_and_distinct_accounts(now):
    p1 = make_post(1, "a", now, caption="match this", likes=500)
    p2 = make_post(2, "b", now, caption="match this", likes=1000)
    s1 = StoryView(10, "f1", now, now, 1, 1, 100, "unknown", None, None, "watching", 0.0, None)
    p_s1 = make_post(100, "a", now, caption="match this", likes=100) # same account as p1
    plan = cluster_posts([p1, p2], [(s1, [p_s1])], None, now)
    assert plan.updates[0].best_post_id == 2
    assert plan.updates[0].distinct_accounts == 2 # "a" and "b"

# BASELINE TESTS
def test_baselines_matched_used(now):
    p1 = make_post(1, "a", now)
    hist = []
    for i in range(5):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=1000)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 0, 1000)])
        hist.append(h)
    match, warmup = account_baselines(hist, p1, now)
    assert match[1] == 5
    assert match[0] == 1000.0
    
def test_baselines_warmup_used(now):
    p1 = make_post(1, "a", now)
    hist = []
    # 3 mature posts
    for i in range(3):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=2000)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 1440, 2000)])
        hist.append(h)
    match, warmup = account_baselines(hist, p1, now)
    assert match[1] == 0 # no near age
    assert warmup > 0 # some fraction of 2000

def test_baselines_mature_likes_one_per_post(now):
    p1 = make_post(1, "a", now)
    h = make_post(10, "a", now - timedelta(days=2), likes=1000)
    # One post with 3 mature snapshots should count once!
    h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [
        make_snap(now, 1440, 900), make_snap(now, 1500, 950), make_snap(now, 1600, 1000)
    ])
    match, warmup = account_baselines([h], p1, now)
    # the latest_like_count is 1000, should be used once
    assert match[1] == 0

# CURVE TESTS
def test_account_curve_none_under_20(now):
    hist = []
    for i in range(19):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=1000)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 1440, 1000)])
        hist.append(h)
    assert account_curve(hist) is None

def test_account_curve_valid(now):
    hist = []
    for i in range(20):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=1000)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [
            make_snap(now, 0, 100), make_snap(now, 1440, 1000)
        ])
        hist.append(h)
    curve = account_curve(hist)
    assert curve is not None
    assert curve(0) == 0.1
    assert curve(24) == 1.0

# DECISION TESTS
def test_decision_fast_beats_confirmed(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now, likes=5000)
    cfg = make_cfg(1, "f1", min_l=1000, fast_r=2.0, conf_r=1.5)
    hist = []
    for i in range(5):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=1000)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 0, 1000)])
        hist.append(h)
    
    plan = evaluate_story_for_fanpage(s, [p], {"a": hist}, cfg, now, set(), lambda x: "news", {})
    assert plan.rule == "fast"

def test_decision_confirmed_normal(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now - timedelta(hours=2), likes=2000) # age 120 > fast window
    cfg = make_cfg(1, "f1", min_l=1000, fast_r=2.0, conf_r=1.5)
    hist = []
    for i in range(5):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=1000)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 120, 1000)])
        hist.append(h)
    
    plan = evaluate_story_for_fanpage(s, [p], {"a": hist}, cfg, now, set(), lambda x: "news", {})
    assert plan.rule == "confirmed"

def test_decision_big_account_no_decision(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now - timedelta(hours=2), likes=10000)
    cfg = make_cfg(1, "f1", min_l=1000, fast_r=2.0, conf_r=1.5)
    hist = []
    for i in range(5):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=10000)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 120, 10000)])
        hist.append(h)
    
    plan = evaluate_story_for_fanpage(s, [p], {"a": hist}, cfg, now, set(), lambda x: "news", {})
    # burst might trigger if burst logic matched, but only 1 account here
    assert plan is None

def test_decision_burst_fires(now):
    s = make_story(10, "f1", now)
    p1 = make_post(1, "a", now - timedelta(minutes=10), likes=100) # no fast
    p2 = make_post(2, "b", now - timedelta(minutes=5), likes=100)
    cfg = make_cfg(1, "f1", min_l=5000, fast_r=2.0, conf_r=1.5, burst=True)
    hist = []
    for i in range(5):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=100)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 10, 100)])
        hist.append(h)
    
    plan = evaluate_story_for_fanpage(s, [p1, p2], {"a": hist}, cfg, now, set(), lambda x: "news", {})
    assert plan.rule == "burst"

def test_decision_burst_off(now):
    s = make_story(10, "f1", now)
    p1 = make_post(1, "a", now - timedelta(minutes=10), likes=100)
    p2 = make_post(2, "b", now - timedelta(minutes=5), likes=100)
    cfg = make_cfg(1, "f1", min_l=5000, burst=False)
    hist = []
    for i in range(5):
        h = make_post(10+i, "a", now - timedelta(days=2), likes=100)
        h = PostView(h.id, h.account_key, h.niche, h.ig_source_id, h.shortcode, h.taken_at, h.caption, h.phash, h.latest_like_count, h.image_count, [make_snap(now, 10, 100)])
        hist.append(h)
    
    plan = evaluate_story_for_fanpage(s, [p1, p2], {"a": hist}, cfg, now, set(), lambda x: "news", {})
    assert plan is None

def test_decision_status_shadow(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now, likes=5000)
    cfg = make_cfg(1, "f1", min_l=1000)
    cfg = FanpageRadarConfig(1, True, True, {"f1"}, 1000, 1.5, 2.0, 60, True, 72, 168, set())
    # no hist, confirmed due to min likes
    plan = evaluate_story_for_fanpage(s, [p], {}, cfg, now, set(), lambda x: "news", {})
    assert plan.status == "shadow_logged"

def test_decision_status_queued(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now, likes=5000)
    cfg = FanpageRadarConfig(1, True, False, {"f1"}, 1000, 1.5, 2.0, 60, True, 72, 168, set())
    plan = evaluate_story_for_fanpage(s, [p], {}, cfg, now, set(), lambda x: "news", {})
    assert plan.status == "queued"

def test_decision_existing_skipped(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now, likes=5000)
    cfg = make_cfg(1, "f1")
    plan = evaluate_story_for_fanpage(s, [p], {}, cfg, now, {(10, 1)}, lambda x: "news", {})
    assert plan is None

def test_decision_viral_only_eligible(now):
    s = make_story(10, "other", now, best_id=1)
    p1 = make_post(1, "a", now, likes=10000) # not in viral source
    p2 = make_post(2, "b", now, likes=5000, ig_source_id=99) # viral source
    cfg = FanpageRadarConfig(1, False, False, set(), 1000, 1.5, 2.0, 60, True, 72, 168, {99})
    plan = evaluate_story_for_fanpage(s, [p1, p2], {}, cfg, now, set(), lambda x: "news", {})
    assert plan is not None
    assert plan.post_id == 2
    assert plan.via_viral_only_link is True

# SHELF TESTS
def test_shelf_classify_not_called_if_no_fire(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now, likes=10, caption="abc")
    cfg = make_cfg(1, "f1")
    def fail_classify(x):
        raise ValueError("Should not be called")
    plan = evaluate_story_for_fanpage(s, [p], {}, cfg, now, set(), fail_classify, {})
    assert plan is None

def test_shelf_called_once_across_fanpages(now):
    s = make_story(10, "f1", now, best_id=1)
    p = make_post(1, "a", now, likes=5000, caption="abc")
    cfg1 = make_cfg(1, "f1")
    cfg2 = make_cfg(2, "f1")
    
    calls = 0
    def mock_classify(x):
        nonlocal calls
        calls += 1
        return "news"
        
    ep = evaluate_all([s], {10: [p]}, {}, [cfg1, cfg2], now, set(), mock_classify)
    assert calls == 1
    assert len(ep.decisions) == 2

def test_shelf_stale_skipped(now):
    # first seen 4 days ago, not rising
    s = make_story(10, "f1", now - timedelta(days=4), best_id=1)
    p = make_post(1, "a", now, likes=5000, caption="abc")
    p = PostView(p.id, p.account_key, p.niche, p.ig_source_id, p.shortcode, p.taken_at, p.caption, p.phash, p.latest_like_count, p.image_count, [make_snap(now, 0, 5000), make_snap(now, 10, 5000)])
    cfg = make_cfg(1, "f1")
    plan = evaluate_story_for_fanpage(s, [p], {}, cfg, now, set(), lambda x: "news", {})
    assert plan.status == "skipped_stale"

def test_shelf_stale_but_rising(now):
    s = make_story(10, "f1", now - timedelta(days=4), best_id=1)
    p = make_post(1, "a", now, likes=5050, caption="abc")
    p = PostView(p.id, p.account_key, p.niche, p.ig_source_id, p.shortcode, p.taken_at, p.caption, p.phash, p.latest_like_count, p.image_count, [make_snap(now, 0, 5000), make_snap(now, 10, 5050)])
    cfg = make_cfg(1, "f1")
    plan = evaluate_story_for_fanpage(s, [p], {}, cfg, now, set(), lambda x: "news", {})
    assert plan.status != "skipped_stale"
    
def test_shelf_story_over_48h_stale_no_call(now):
    # last_member_at is 49h ago
    s = StoryView(10, "f1", now - timedelta(hours=49), now - timedelta(hours=49), 1, 1, 1, "unknown", None, None, "watching", 0.0, None)
    p = make_post(1, "a", now, likes=5000, caption="abc")
    cfg = make_cfg(1, "f1")
    
    def fail_classify(x): raise ValueError()
    
    ep = evaluate_all([s], {10: [p]}, {}, [cfg], now, set(), fail_classify)
    assert len(ep.decisions) == 0
    assert len(ep.story_updates) == 1
    assert ep.story_updates[0]["status"] == "stale"

# NIGHTLY TESTS
def test_nightly_plan(now):
    s1 = StoryView(1, "f1", now - timedelta(days=2), now, 1, 1, 1, "unknown", None, None, "watching", 0.0, None)
    s2 = StoryView(2, "f1", now - timedelta(days=2), now, 1, 1, 2, "unknown", None, None, "watching", 0.0, None)
    
    p1 = make_post(1, "acc1", now - timedelta(days=2), likes=100)
    p2 = make_post(2, "acc1", now - timedelta(days=2), likes=200)
    
    n_plan = nightly_plan([s1, s2], {1: [p1], 2: [p2]}, {1}, now)
    
    assert n_plan.final_likes[1] == 100
    assert n_plan.final_likes[2] == 200
    # acc1 was first poster in both (since it's only member). share_first = 1.0.
    # acc1 posts in s1 (has decision), s2 (no decision). share_dec = 0.5.
    # score = 0.5 * 1.0 + 0.5 * 0.5 = 0.75
    assert n_plan.leader_scores["acc1"] == 0.75

# SHADOW REPORT TESTS
def test_shadow_report(now):
    d1 = DecisionRecord(1, 10, "fast", 30, "acc1", "short1", 1500)
    d2 = DecisionRecord(2, 10, "confirmed", 90, "acc2", "short2", 800)
    
    stories = [
        StoryView(1, "f1", now, now, 1, 1, 1, "unknown", None, None, "watching", 0.0, 2000),
        StoryView(2, "f1", now, now, 1, 1, 2, "unknown", None, None, "watching", 0.0, 500),
        StoryView(3, "f1", now, now, 1, 1, 3, "unknown", None, None, "watching", 0.0, 5000) # missed!
    ]
    cfgs = [FanpageRadarConfig(10, True, True, {"f1"}, 1000, 1.5, 2.0, 60, True, 72, 168, set())]
    
    report = shadow_report(stories, [d1, d2], cfgs)
    assert report["overall"]["precision"] == 0.5
    assert report["overall"]["decisions_by_rule"]["fast"] == 1
    assert report["overall"]["missed_stories"] == 1

def test_evaluate_all_naive_datetime_and_breaking_shelf():
    from app.services.radar_engine import evaluate_all, StoryView, PostView, FanpageRadarConfig
    from datetime import datetime, timezone, timedelta
    
    # naive datetime representing UTC
    now_naive = datetime(2026, 1, 1, 12, 0, 0)
    taken = now_naive - timedelta(minutes=10)
    
    p = PostView(1, "acc", "f1", None, "code", taken, "cap", None, 1000, 1, [])
    s = StoryView(1, "f1", taken, taken, 1, 1, 1, "news", None, None, "watching", 0.0, None)
    
    cfg = FanpageRadarConfig(1, True, True, {"f1"}, 500, 1.5, 2.0, 60, True, 72, 168, set())
    
    # classify_shelf returning 'breaking' will return an aware datetime via shelf_expires_at
    plan = evaluate_all(
        stories=[s],
        story_members={1: [p]},
        histories={"acc": []},
        cfgs=[cfg],
        now=now_naive,
        existing_decisions=set(),
        classify_shelf=lambda t: "breaking"
    )
    
    # Should not raise TypeError. The story should get a naive expires_at.
    assert len(plan.story_updates) == 1
    assert "expires_at" in plan.story_updates[0]
    assert plan.story_updates[0]["expires_at"].tzinfo is None
    
    # If a story is older than 48 hours, it should be marked stale in is_stale even without shelf_kind
    s_old = StoryView(2, "f1", now_naive - timedelta(hours=50), now_naive - timedelta(hours=50), 1, 1, 1, "news", None, None, "watching", 0.0, None)
    plan_old = evaluate_all(
        stories=[s_old],
        story_members={2: [p]},
        histories={"acc": []},
        cfgs=[cfg],
        now=now_naive,
        existing_decisions=set(),
        classify_shelf=lambda t: "news"
    )
    assert len(plan_old.story_updates) == 1
    assert plan_old.story_updates[0]["status"] == "stale"
