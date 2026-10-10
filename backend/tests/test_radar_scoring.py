import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from app.services.radar_scoring import (
    prior_fraction,
    baseline_at_age,
    warmup_baseline,
    Thresholds,
    Decision,
    evaluate_post,
    BurstMember,
    evaluate_burst,
    shelf_expires_at,
    is_stale,
    heat_score
)

def test_prior_fraction():
    # h >= 24 -> 1.0
    assert prior_fraction(24.0) == 1.0
    assert prior_fraction(48.0) == 1.0
    # h <= 0 -> floor (e.g. 1e-3)
    assert prior_fraction(0.0) == 1e-3
    assert prior_fraction(-1.0) == 1e-3
    # mid points
    # F(h) = (1 - 2**(-h/19)) / (1 - 2**(-24/19))
    f_19 = (1 - 2**-1) / (1 - 2**(-24/19))
    assert pytest.approx(prior_fraction(19.0)) == f_19

def test_baseline_at_age():
    # Samples outside relative tolerance
    samples = [(10.0, 100), (20.0, 200), (22.0, 220), (24.0, 240), (26.0, 260), (28.0, 280), (40.0, 400)]
    # age_min = 24.0, tolerance = 0.25 -> range [18.0, 30.0]
    # valid samples: 20.0, 22.0, 24.0, 26.0, 28.0 (5 samples)
    # median of [200, 220, 240, 260, 280] = 240.0
    median, n = baseline_at_age(samples, 24.0, tolerance=0.25, min_samples=5)
    assert n == 5
    assert median == 240.0

    # Not enough samples
    median, n = baseline_at_age(samples, 24.0, tolerance=0.1, min_samples=5)
    assert n < 5
    assert median is None

def test_warmup_baseline():
    mature = [1000, 1500, 2000] # median = 1500
    # age = 60 mins -> 1 hour
    res = warmup_baseline(mature, 60.0)
    assert res == pytest.approx(1500 * prior_fraction(1.0))
    
    assert warmup_baseline([], 60.0) is None

def test_evaluate_post():
    t = Thresholds(min_likes=1000, confirm_ratio=1.5, fast_ratio=2.0, fast_window_min=60)
    
    # likes None
    d = evaluate_post(None, 30, (500.0, 5), None, t)
    assert d.rule == 'unknown'
    assert d.baseline == 500.0
    
    # FAST path
    # age_min = 30, matched n=5, matched value=500
    # ratio = 2.5 (>=2.0)
    # projected: 1250 / F(0.5) >= 1000
    # Let's provide a mock curve_fraction to make math easy
    d = evaluate_post(1250, 30, (500.0, 5), None, t, curve_fraction=lambda h: 0.1)
    assert d.rule == 'fast'
    assert d.ratio == 2.5
    assert d.projected == 12500.0
    
    # Not enough samples for FAST -> checks CONFIRMED
    d = evaluate_post(1250, 30, (500.0, 4), 600.0, t)
    # B = warmup = 600
    # likes = 1250 >= 1000 and 1250 >= 1.5 * 600 (900)
    assert d.rule == 'confirmed'
    assert d.baseline == 600.0
    assert d.ratio == 1250 / 600.0
    
    # Big account normal is 2000. Ratio needed = 3000.
    d = evaluate_post(2500, 100, (2000.0, 5), None, t)
    assert d.rule == 'none' # 2500 < 1.5 * 2000
    
    # Normal account normal is 500. Hits 1000 -> CONFIRMED
    d = evaluate_post(1000, 100, (500.0, 5), None, t)
    assert d.rule == 'confirmed'

    # Projection boundary fast
    d = evaluate_post(200, 30, (50.0, 5), None, t, curve_fraction=lambda h: 0.25)
    # 200 / 0.25 = 800 < 1000 (min likes)
    # not fast, not confirmed (200 < 1000)
    assert d.rule == 'none'

def test_evaluate_burst():
    t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    # two members, different keys, within window, one above baseline
    members = [
        BurstMember("A", t1, 150, 100.0),
        BurstMember("B", t1 + timedelta(minutes=30), 50, 100.0)
    ]
    assert evaluate_burst(members, 60) is True
    
    # Same account -> false
    members = [
        BurstMember("A", t1, 150, 100.0),
        BurstMember("A", t1 + timedelta(minutes=30), 150, 100.0)
    ]
    assert evaluate_burst(members, 60) is False

    # Outside window
    members = [
        BurstMember("A", t1, 150, 100.0),
        BurstMember("B", t1 + timedelta(minutes=61), 150, 100.0)
    ]
    assert evaluate_burst(members, 60) is False
    
    # No member above baseline
    members = [
        BurstMember("A", t1, 50, 100.0),
        BurstMember("B", t1 + timedelta(minutes=30), 50, 100.0)
    ]
    assert evaluate_burst(members, 60) is False

def test_shelf_expires_at():
    t1 = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
    # breaking -> midnight Europe/London. 2026-06-01 20:00 UTC is 21:00 BST.
    # Midnight BST is 2026-06-02 00:00 BST -> 2026-06-01 23:00 UTC
    res = shelf_expires_at('breaking', t1, 'Europe/London')
    assert res == datetime(2026, 6, 1, 23, 0, tzinfo=timezone.utc)
    
    # Test with naive datetime (should be treated as UTC, NOT local system time)
    t_naive = datetime(2026, 6, 1, 20, 0)
    res_naive = shelf_expires_at('breaking', t_naive, 'Europe/London')
    assert res_naive == datetime(2026, 6, 1, 23, 0, tzinfo=timezone.utc)
    
    res = shelf_expires_at('news', t1)
    assert res == t1 + timedelta(hours=72)
    
    res = shelf_expires_at('evergreen', t1)
    assert res == t1 + timedelta(hours=168)
    
    res = shelf_expires_at('unknown', t1)
    assert res == t1 + timedelta(hours=72)

def test_is_stale():
    now = datetime(2026, 1, 1, 12, 0)
    exp = datetime(2026, 1, 1, 10, 0)
    
    assert is_stale(now, exp, still_rising=False, superseded=True) is True
    assert is_stale(now, exp, still_rising=True, superseded=False) is False
    assert is_stale(now, None, still_rising=False, superseded=False) is False
    assert is_stale(now, exp, still_rising=False, superseded=False) is True
    assert is_stale(now, now + timedelta(hours=1), still_rising=False, superseded=False) is False

def test_heat_score():
    assert heat_score([1.5, 2.0], 2) == 2.5
    assert heat_score([], 0) == 0.0
    assert heat_score([-1.0], 1) == 0.0
