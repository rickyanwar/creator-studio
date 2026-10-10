"""Pure functions for the Viral Radar scoring, see PLAN.md §6.1."""

import statistics
from dataclasses import dataclass
from typing import Sequence, Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def prior_fraction(age_h: float, tau_h: float = 19.0) -> float:
    """Return expected share of 24h likes reached at age_h hours."""
    if age_h >= 24.0:
        return 1.0
    if age_h <= 0.0:
        return 1e-3
    return (1.0 - 2.0 ** (-age_h / tau_h)) / (1.0 - 2.0 ** (-24.0 / tau_h))

def baseline_at_age(samples: Sequence[tuple[float, int]], age_min: float, tolerance: float = 0.25, min_samples: int = 5) -> tuple[float | None, int]:
    """Calculate median likes from samples within relative age tolerance."""
    lower_bound = age_min * (1.0 - tolerance)
    upper_bound = age_min * (1.0 + tolerance)
    valid_likes = [likes for age, likes in samples if lower_bound <= age <= upper_bound]
    
    n_used = len(valid_likes)
    if n_used < min_samples:
        return None, n_used
        
    return float(statistics.median(valid_likes)), n_used

def warmup_baseline(mature_likes: Sequence[int], age_min: float) -> float | None:
    """Estimate baseline using mature likes and prior fraction for early posts."""
    if not mature_likes:
        return None
    return float(statistics.median(mature_likes)) * prior_fraction(age_min / 60.0)

@dataclass(frozen=True)
class Thresholds:
    min_likes: int = 1000
    confirm_ratio: float = 1.5
    fast_ratio: float = 2.0
    fast_window_min: int = 60

@dataclass(frozen=True)
class Decision:
    rule: str
    reason: str
    baseline: float | None
    ratio: float | None = None
    projected: float | None = None

def evaluate_post(
    likes: int | None, 
    age_min: float, 
    matched: tuple[float | None, int], 
    warmup: float | None, 
    thresholds: Thresholds, 
    curve_fraction: Callable[[float], float] | None = None
) -> Decision:
    """Evaluate post heat rules (fast, confirmed) returning a Decision."""
    matched_value, n_used = matched
    if likes is None:
        return Decision(rule='unknown', reason='likes is None', baseline=matched_value)
        
    baseline = matched_value if n_used >= 5 else warmup
    
    # Fast path
    is_fast_window = age_min <= thresholds.fast_window_min
    if is_fast_window and n_used >= 5 and matched_value is not None:
        ratio = likes / matched_value if matched_value > 0 else float('inf')
        f = curve_fraction(age_min / 60.0) if curve_fraction else prior_fraction(age_min / 60.0)
        projected = likes / f
        if ratio >= thresholds.fast_ratio and projected >= thresholds.min_likes:
            return Decision(
                rule='fast', 
                reason='fast ratio and projected met', 
                baseline=baseline,
                ratio=ratio,
                projected=projected
            )
            
    # Confirmed path
    if likes >= thresholds.min_likes:
        if baseline is None:
            return Decision(
                rule='confirmed', 
                reason='min likes met, no baseline', 
                baseline=baseline,
                ratio=None
            )
        
        ratio = likes / baseline if baseline > 0 else float('inf')
        if likes >= thresholds.confirm_ratio * baseline:
            return Decision(
                rule='confirmed', 
                reason='min likes and confirm ratio met', 
                baseline=baseline,
                ratio=ratio
            )
            
    ratio = likes / baseline if (baseline is not None and baseline > 0) else None
    return Decision(rule='none', reason='thresholds not met', baseline=baseline, ratio=ratio)

@dataclass(frozen=True)
class BurstMember:
    account_key: str
    taken_at: datetime
    likes: int | None
    baseline: float | None

def evaluate_burst(members: Sequence[BurstMember], window_min: int = 60) -> bool:
    """Detect if multiple accounts posted the same story within a time window with engagement."""
    if not members:
        return False
        
    sorted_members = sorted(members, key=lambda m: m.taken_at)
    
    for i in range(len(sorted_members)):
        m1 = sorted_members[i]
        for j in range(i + 1, len(sorted_members)):
            m2 = sorted_members[j]
            delta = (m2.taken_at - m1.taken_at).total_seconds() / 60.0
            
            if delta > window_min:
                break
                
            if m1.account_key != m2.account_key:
                if (m1.likes is not None and m1.baseline is not None and m1.baseline > 0 and m1.likes >= 1.0 * m1.baseline) or \
                   (m2.likes is not None and m2.baseline is not None and m2.baseline > 0 and m2.likes >= 1.0 * m2.baseline):
                    return True
                    
    return False

def shelf_expires_at(kind: str, first_seen_at: datetime, audience_tz: str = "Europe/London", news_h: int = 72, evergreen_h: int = 168) -> datetime:
    """Calculate expiration time based on story kind."""
    if kind == 'breaking':
        local_tz = ZoneInfo(audience_tz)
        # Fix: ensure first_seen_at is treated as UTC before converting to local timezone
        if first_seen_at.tzinfo is None:
            first_seen_at_utc = first_seen_at.replace(tzinfo=timezone.utc)
        else:
            first_seen_at_utc = first_seen_at

        local_time = first_seen_at_utc.astimezone(local_tz)
        # End of calendar day (midnight next day) converted back to UTC
        tomorrow = local_time + timedelta(days=1)
        midnight_local = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=local_tz)
        return midnight_local.astimezone(ZoneInfo("UTC"))
        
    elif kind == 'evergreen':
        return first_seen_at + timedelta(hours=evergreen_h)
    else:
        return first_seen_at + timedelta(hours=news_h)

def is_stale(now: datetime, expires_at: datetime | None, still_rising: bool, superseded: bool) -> bool:
    """Check if a story is stale and should be retired."""
    if superseded:
        return True
    if still_rising:
        return False
    if expires_at is None:
        return False
    return now > expires_at

def heat_score(member_ratios: Sequence[float], distinct_accounts: int) -> float:
    """Calculate a single heat score for a story."""
    max_ratio = max(member_ratios) if member_ratios else 0.0
    return max(max_ratio, 0.0) + 0.5 * max(distinct_accounts - 1, 0)
