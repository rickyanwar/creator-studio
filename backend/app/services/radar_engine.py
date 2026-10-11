from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, List, Set, Tuple, Optional
from app.services.radar_time import to_naive_utc
from app.services.radar_scoring import evaluate_post, evaluate_burst, BurstMember, Thresholds, shelf_expires_at, is_stale, heat_score
from app.services.radar_clustering import assign_story, PostSig

@dataclass(frozen=True)
class SnapshotView:
    observed_at: datetime
    age_minutes: float
    like_count: Optional[int]

@dataclass(frozen=True)
class PostView:
    id: int
    account_key: str
    niche: Optional[str]
    ig_source_id: Optional[int]
    shortcode: str
    taken_at: datetime
    caption: str
    phash: Optional[str]
    latest_like_count: Optional[int]
    image_count: int
    snapshots: List[SnapshotView] = field(default_factory=list)

@dataclass(frozen=True)
class StoryView:
    id: int
    niche: Optional[str]
    first_seen_at: datetime
    last_member_at: datetime
    member_count: int
    distinct_accounts: int
    best_post_id: Optional[int]
    content_hint: str
    shelf_kind: Optional[str]
    expires_at: Optional[datetime]
    status: str
    heat_score: float
    final_max_likes_24h: Optional[int]

@dataclass(frozen=True)
class FanpageRadarConfig:
    fanpage_id: int
    radar_enabled: bool
    radar_shadow: bool
    radar_niches: Set[str]
    min_likes: int
    confirm_ratio: float
    fast_ratio: float
    fast_window_min: int
    burst_enabled: bool
    shelf_news_h: int
    shelf_evergreen_h: int
    viral_only_source_ids: Set[int] = field(default_factory=set)

@dataclass(frozen=True)
class ClusterPlanStoryUpdate:
    story_id: int
    member_count: int
    distinct_accounts: int
    last_member_at: datetime
    best_post_id: Optional[int]
    assigned_post_ids: List[int]

@dataclass(frozen=True)
class ClusterPlanNewStory:
    niche: Optional[str]
    first_seen_at: datetime
    best_post_id: int
    assigned_post_ids: List[int]

@dataclass(frozen=True)
class ClusterPlan:
    updates: List[ClusterPlanStoryUpdate]
    new_stories: List[ClusterPlanNewStory]

@dataclass(frozen=True)
class DecisionPlan:
    story_id: int
    fanpage_id: int
    rule: str
    reason: str
    post_id: int
    shadow: bool
    status: str
    ratio: Optional[float]
    projected: Optional[float]
    via_viral_only_link: bool
    new_shelf_kind: Optional[str] = None
    new_expires_at: Optional[datetime] = None

@dataclass(frozen=True)
class EvaluationPlan:
    decisions: List[DecisionPlan]
    story_updates: List[Dict[str, Any]]

@dataclass(frozen=True)
class NightlyPlan:
    leader_scores: Dict[str, float]
    final_likes: Dict[int, int]
    delete_snapshots_before: datetime


def cluster_posts(new_posts: List[PostView], recent_stories: List[Tuple[StoryView, List[PostView]]], judge: Optional[Callable[[str, str], bool]], now: datetime) -> ClusterPlan:
    sorted_posts = sorted(new_posts, key=lambda p: p.taken_at)
    
    story_members = {s.id: list(members) for s, members in recent_stories}
    story_views = {s.id: s for s, _ in recent_stories}
    
    updates = {}
    new_stories = []
    
    for post in sorted_posts:
        candidates = []
        for s_id, members in story_members.items():
            s = story_views[s_id]
            if (now - s.last_member_at).total_seconds() <= 48 * 3600:
                if s.niche == post.niche or (post.niche is None and post.ig_source_id is not None):
                    candidates.append((s_id, [PostSig(m.shortcode, m.account_key, m.phash, m.caption) for m in members]))
        
        assigned_id = assign_story(PostSig(post.shortcode, post.account_key, post.phash, post.caption), candidates, judge)
        
        if assigned_id is not None:
            story_members[assigned_id].append(post)
            if assigned_id not in updates:
                updates[assigned_id] = []
            updates[assigned_id].append(post)
        else:
            temp_id = -len(new_stories) - 1
            new_s = StoryView(
                id=temp_id,
                niche=post.niche,
                first_seen_at=post.taken_at,
                last_member_at=post.taken_at,
                member_count=1,
                distinct_accounts=1,
                best_post_id=post.id,
                content_hint="unknown",
                shelf_kind=None,
                expires_at=None,
                status="watching",
                heat_score=0.0,
                final_max_likes_24h=None
            )
            story_views[temp_id] = new_s
            story_members[temp_id] = [post]
            new_stories.append(ClusterPlanNewStory(
                niche=post.niche,
                first_seen_at=post.taken_at,
                best_post_id=post.id,
                assigned_post_ids=[post.id]
            ))
            
    final_updates = []
    for s_id, added_posts in updates.items():
        all_members = story_members[s_id]
        def post_sort_key(p: PostView):
            likes = p.latest_like_count if p.latest_like_count is not None else -1
            return (likes, p.image_count, -p.taken_at.timestamp())
            
        best = max(all_members, key=post_sort_key)
        distinct = len(set(m.account_key for m in all_members))
        last_at = max(m.taken_at for m in all_members)
        
        final_updates.append(ClusterPlanStoryUpdate(
            story_id=s_id,
            member_count=len(all_members),
            distinct_accounts=distinct,
            last_member_at=last_at,
            best_post_id=best.id,
            assigned_post_ids=[p.id for p in added_posts]
        ))
        
    return ClusterPlan(updates=final_updates, new_stories=new_stories)

def account_baselines(history: List[PostView], post: PostView, now: datetime) -> Tuple[Tuple[Optional[float], int], Optional[float]]:
    age_min = (now - post.taken_at).total_seconds() / 60.0
    history_sorted = sorted([h for h in history if h.id != post.id], key=lambda x: x.taken_at, reverse=True)[:20]
    
    samples = []
    mature_likes = []
    for h in history_sorted:
        for s in h.snapshots:
            if s.like_count is not None:
                samples.append((s.age_minutes, s.like_count))
                
        if (now - h.taken_at).total_seconds() >= 24 * 3600 and h.latest_like_count is not None:
            mature_likes.append(h.latest_like_count)
            
    from app.services.radar_scoring import baseline_at_age, warmup_baseline
    matched = baseline_at_age(samples, age_min)
    warmup = warmup_baseline(mature_likes, age_min)
    return matched, warmup

def account_curve(history: List[PostView]) -> Optional[Callable[[float], float]]:
    mature_posts = []
    for h in history:
        max_age = max((s.age_minutes for s in h.snapshots), default=0)
        if max_age >= 24 * 60:
            mature_posts.append(h)
            
    if len(mature_posts) < 20:
        return None
        
    buckets = [0, 15, 30, 60, 120, 240, 480, 720, 1440]
    fractions_by_bucket = {b: [] for b in buckets}
    
    for h in mature_posts:
        likes_24h = None
        for s in h.snapshots:
            if s.age_minutes >= 1440 and s.like_count is not None:
                likes_24h = s.like_count
                break
        if not likes_24h:
            continue
            
        for s in h.snapshots:
            if s.like_count is not None:
                nearest = min(buckets, key=lambda b: abs(b - s.age_minutes))
                fractions_by_bucket[nearest].append(s.like_count / likes_24h)
                
    import statistics
    medians = {}
    for b, fracs in fractions_by_bucket.items():
        if fracs: medians[b] = statistics.median(fracs)
        else:
            if b == 0: medians[b] = 0.0
            elif b == 1440: medians[b] = 1.0
            
    if 0 not in medians: medians[0] = 0.0
    if 1440 not in medians: medians[1440] = 1.0
    sorted_buckets = sorted(medians.keys())
    
    def curve(age_h: float) -> float:
        age_min = age_h * 60.0
        if age_min <= 0: return medians[0]
        if age_min >= 1440: return medians[1440]
        
        for i in range(len(sorted_buckets)-1):
            b1, b2 = sorted_buckets[i], sorted_buckets[i+1]
            if b1 <= age_min <= b2:
                v1, v2 = medians[b1], medians[b2]
                if b1 == b2: return v1
                return v1 + (v2 - v1) * (age_min - b1) / (b2 - b1)
        return medians[1440]
        
    return curve

def evaluate_story_for_fanpage(
    story: StoryView, 
    members: List[PostView], 
    histories: Dict[str, List[PostView]], 
    cfg: FanpageRadarConfig, 
    now: datetime,
    existing_decisions: Set[Tuple[int, int]],
    classify_shelf: Callable[[str], str],
    shelf_cache: Dict[int, str]
) -> Optional[DecisionPlan]:
    
    if (story.id, cfg.fanpage_id) in existing_decisions:
        return None
        
    niche_eligible = cfg.radar_enabled and story.niche in cfg.radar_niches
    viral_only_members = [m for m in members if m.ig_source_id in cfg.viral_only_source_ids]
    viral_only_eligible = len(viral_only_members) > 0
    
    if not niche_eligible and not viral_only_eligible:
        return None
        
    via_viral_only = viral_only_eligible and not niche_eligible
    eval_members = viral_only_members if via_viral_only else members
    
    best_post = next((m for m in members if m.id == story.best_post_id), members[0] if members else None)
    
    thresholds = Thresholds(
        min_likes=cfg.min_likes,
        confirm_ratio=cfg.confirm_ratio,
        fast_ratio=cfg.fast_ratio,
        fast_window_min=cfg.fast_window_min
    )
    
    best_decision = None
    best_member = None
    burst_members = []
    
    for m in eval_members:
        age_min = (now - m.taken_at).total_seconds() / 60.0
        hist = histories.get(m.account_key, [])
        matched, warmup = account_baselines(hist, m, now)
        curve = account_curve(hist)
        
        d = evaluate_post(m.latest_like_count, age_min, matched, warmup, thresholds, curve)
        
        if d.rule in ('fast', 'confirmed'):
            if not best_decision or (d.rule == 'fast' and best_decision.rule != 'fast'):
                best_decision = d
                best_member = m
                
        if cfg.burst_enabled and not via_viral_only:
            burst_members.append(BurstMember(
                account_key=m.account_key,
                taken_at=m.taken_at,
                likes=m.latest_like_count,
                baseline=matched[0] if matched[0] is not None else warmup
            ))
            
    fired_rule = None
    fired_reason = None
    fired_ratio = None
    fired_projected = None
    fired_post_id = None
    
    if best_decision:
        fired_rule = best_decision.rule
        fired_reason = best_decision.reason
        fired_ratio = best_decision.ratio
        fired_projected = best_decision.projected
        fired_post_id = best_member.id if best_member else (best_post.id if best_post else 0)
    elif cfg.burst_enabled and not via_viral_only and evaluate_burst(burst_members):
        fired_rule = "burst"
        fired_reason = "burst detected"
        fired_post_id = best_post.id if best_post else 0
        
    if not fired_rule:
        return None

    new_shelf_kind = None
    new_expires_at = None
    kind = story.shelf_kind
    
    if not kind:
        kind = shelf_cache.get(story.id)
        if not kind and best_post and best_post.caption:
            kind = classify_shelf(best_post.caption)
            shelf_cache[story.id] = kind
            new_shelf_kind = kind
            
    expires_at = story.expires_at
    if kind and not expires_at:
        expires_at = to_naive_utc(shelf_expires_at(kind, story.first_seen_at, news_h=cfg.shelf_news_h, evergreen_h=cfg.shelf_evergreen_h))
        new_expires_at = expires_at

    still_rising = False
    if best_post and len(best_post.snapshots) >= 2:
        s1 = best_post.snapshots[-2]
        s2 = best_post.snapshots[-1]
        if s1.like_count is not None and s2.like_count is not None:
            inc = s2.like_count - s1.like_count
            if inc >= 50 or (s1.like_count > 0 and inc / s1.like_count >= 0.05):
                still_rising = True
                
    if expires_at and is_stale(now, expires_at, still_rising, False):
        return DecisionPlan(
            story_id=story.id,
            fanpage_id=cfg.fanpage_id,
            rule=fired_rule,
            reason="stale",
            post_id=fired_post_id,
            shadow=cfg.radar_shadow,
            status="skipped_stale",
            ratio=fired_ratio,
            projected=fired_projected,
            via_viral_only_link=via_viral_only,
            new_shelf_kind=new_shelf_kind,
            new_expires_at=new_expires_at
        )
        
    return DecisionPlan(
        story_id=story.id,
        fanpage_id=cfg.fanpage_id,
        rule=fired_rule,
        reason=fired_reason,
        post_id=fired_post_id,
        shadow=cfg.radar_shadow,
        status="shadow_logged" if cfg.radar_shadow else "queued",
        ratio=fired_ratio,
        projected=fired_projected,
        via_viral_only_link=via_viral_only,
        new_shelf_kind=new_shelf_kind,
        new_expires_at=new_expires_at
    )

def evaluate_all(
    stories: List[StoryView], 
    story_members: Dict[int, List[PostView]], 
    histories: Dict[str, List[PostView]], 
    cfgs: List[FanpageRadarConfig], 
    now: datetime,
    existing_decisions: Set[Tuple[int, int]],
    classify_shelf: Callable[[str], str],
    track_max_age_h: int = 48
) -> EvaluationPlan:
    
    decisions = []
    updates = []
    local_existing = set(existing_decisions)
    shelf_cache = {}
    
    for story in stories:
        if (now - story.last_member_at).total_seconds() > track_max_age_h * 3600:
            updates.append({"id": story.id, "status": "stale"})
            continue
            
        members = story_members.get(story.id, [])
        if not members:
            continue
            
        ratios = []
        for m in members:
            age_min = (now - m.taken_at).total_seconds() / 60.0
            hist = histories.get(m.account_key, [])
            matched, warmup = account_baselines(hist, m, now)
            baseline = matched[0] if matched[1] >= 5 else warmup
            if baseline and baseline > 0 and m.latest_like_count:
                ratios.append(m.latest_like_count / baseline)
                
        h_score = heat_score(ratios, story.distinct_accounts)
        s_updates = {"id": story.id, "heat_score": h_score}
        
        for cfg in cfgs:
            plan = evaluate_story_for_fanpage(story, members, histories, cfg, now, local_existing, classify_shelf, shelf_cache)
            if plan:
                decisions.append(plan)
                local_existing.add((plan.story_id, plan.fanpage_id))
                if plan.new_shelf_kind and "shelf_kind" not in s_updates:
                    s_updates["shelf_kind"] = plan.new_shelf_kind
                if plan.new_expires_at and "expires_at" not in s_updates:
                    s_updates["expires_at"] = plan.new_expires_at
                
        updates.append(s_updates)
        
    return EvaluationPlan(decisions=decisions, story_updates=updates)


def nightly_plan(
    all_stories: List[StoryView],
    story_members: Dict[int, List[PostView]],
    story_has_decision: Set[int],
    now: datetime
) -> NightlyPlan:
    
    acc_story_count = {}
    acc_first_count = {}
    acc_post_count = {}
    acc_decision_post_count = {}
    
    for s in all_stories:
        members = story_members.get(s.id, [])
        if not members:
            continue
        
        sorted_m = sorted(members, key=lambda m: m.taken_at)
        first_poster = sorted_m[0].account_key
        
        for m in members:
            ak = m.account_key
            acc_post_count[ak] = acc_post_count.get(ak, 0) + 1
            acc_story_count[ak] = acc_story_count.get(ak, 0) + 1
            
            if ak == first_poster:
                acc_first_count[ak] = acc_first_count.get(ak, 0) + 1
                
            if s.id in story_has_decision:
                acc_decision_post_count[ak] = acc_decision_post_count.get(ak, 0) + 1

    leader_scores = {}
    for ak in acc_post_count.keys():
        share_first = acc_first_count.get(ak, 0) / acc_story_count[ak] if acc_story_count.get(ak, 0) > 0 else 0
        share_dec = acc_decision_post_count.get(ak, 0) / acc_post_count[ak] if acc_post_count.get(ak, 0) > 0 else 0
        leader_scores[ak] = 0.5 * share_first + 0.5 * share_dec

    final_likes = {}
    for s in all_stories:
        if (now - s.first_seen_at).total_seconds() > 24 * 3600:
            members = story_members.get(s.id, [])
            max_l = max([m.latest_like_count or 0 for m in members], default=None)
            if max_l is not None:
                final_likes[s.id] = max_l
                
    return NightlyPlan(
        leader_scores=leader_scores,
        final_likes=final_likes,
        delete_snapshots_before=now - timedelta(days=30)
    )

@dataclass(frozen=True)
class DecisionRecord:
    story_id: int
    fanpage_id: int
    rule: str
    minutes_to_trigger: float
    best_ig_username: str
    shortcode: str
    final_likes: Optional[int]

def shadow_report(stories: List[StoryView], decisions: List[DecisionRecord], cfgs: List[FanpageRadarConfig]) -> Dict[str, Any]:
    def process_decisions(decs: List[DecisionRecord], min_likes: int, decs_set: Set[int]):
        import statistics
        rules = {}
        minutes = []
        recent = sorted(decs, key=lambda d: d.story_id, reverse=True)[:20]
        
        prec_num = 0
        prec_den = 0
        
        for d in decs:
            rules[d.rule] = rules.get(d.rule, 0) + 1
            if d.minutes_to_trigger >= 0:
                minutes.append(d.minutes_to_trigger)
                
            if d.final_likes is not None:
                prec_den += 1
                if d.final_likes >= min_likes:
                    prec_num += 1
                    
        median_m = statistics.median(minutes) if minutes else 0
        sorted_m = sorted(minutes)
        p90_m = sorted_m[int(0.9 * len(sorted_m))] if sorted_m else 0
        precision = prec_num / prec_den if prec_den > 0 else 0
        
        missed = 0
        for s in stories:
            if s.id not in decs_set and s.final_max_likes_24h is not None and s.final_max_likes_24h >= min_likes:
                missed += 1
        
        return {
            "decisions_by_rule": rules,
            "median_minutes": median_m,
            "p90_minutes": p90_m,
            "precision": precision,
            "missed_stories": missed,
            "recent_decisions": [
                {"story_id": r.story_id, "rule": r.rule, "minutes": r.minutes_to_trigger, "username": r.best_ig_username, "shortcode": r.shortcode, "final_likes": r.final_likes}
                for r in recent
            ]
        }
        
    report = {"overall": {}, "fanpages": {}}
    all_min_likes = min([c.min_likes for c in cfgs], default=1000)
    all_decs_set = set(d.story_id for d in decisions)
    report["overall"] = process_decisions(decisions, all_min_likes, all_decs_set)
    
    for c in cfgs:
        fp_decs = [d for d in decisions if d.fanpage_id == c.fanpage_id]
        fp_decs_set = set(d.story_id for d in fp_decs)
        report["fanpages"][c.fanpage_id] = process_decisions(fp_decs, c.min_likes, fp_decs_set)

    return report
