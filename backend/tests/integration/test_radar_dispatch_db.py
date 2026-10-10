from datetime import datetime, timezone, timedelta
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.models.radar import RadarAccount, RadarStory, RadarPost, RadarStoryDecision, DecisionStatus
from app.models.ig_sources import IGSource
from app.models.posts import Post, PostStatus
from app.models.fanpage_sources import FanpageSource
from app.models.publish_jobs import PublishJob, ContentType, PublishJobStatus
from app.models.design_templates import DesignTemplate
from app.tasks.radar_dispatch import radar_dispatch_tick
from app.tasks.ig_recreate import recreate_post_for_fanpage, render_ig_recreate
from app.tasks.fan_out import create_fanout_jobs

def setup_story(db_session, niche="f1", sharing="shared", share_max=0):
    s = Settings(id=1, radar_story_sharing=sharing, radar_share_max=share_max)
    db_session.merge(s)
    
    fp = TargetFanpage(repliz_account_id=f"rep_{id(s)}_{id(niche)}_{datetime.now().timestamp()}", name="FP1", is_active=True, radar_daily_max=5, ig_recreate_enabled=True)
    db_session.add(fp)
    db_session.flush()
    
    acc = db_session.query(RadarAccount).filter_by(niche=niche, ig_username="r_user").first()
    if not acc:
        acc = RadarAccount(niche=niche, ig_username="r_user")
        db_session.add(acc)
        db_session.flush()
    
    story = RadarStory(niche=niche, first_seen_at=datetime.utcnow(), last_member_at=datetime.utcnow(), status="triggered")
    db_session.add(story)
    db_session.flush()
    
    post = RadarPost(radar_account_id=acc.id, ig_username="r_user", shortcode=f"short_{story.id}_{id(fp)}", taken_at=datetime.utcnow(), media_type="image", story_id=story.id, image_source_urls=["https://scontent.cdninstagram.com/v/t51.2885-15/123.jpg"])
    db_session.add(post)
    db_session.flush()
    story.best_post_id = post.id
    
    dec = RadarStoryDecision(story_id=story.id, fanpage_id=fp.id, rule="confirmed", status=DecisionStatus.QUEUED, via_viral_only_link=False)
    db_session.add(dec)
    db_session.commit()
    return fp, story, dec, post

def test_niche_decision(db_session, patch_celery_and_ai):
    fp, story, dec, rpost = setup_story(db_session)
    radar_dispatch_tick()
    
    db_session.refresh(dec)
    assert dec.status == DecisionStatus.DISPATCHED
    assert dec.post_id is not None
    
    post = db_session.get(Post, dec.post_id)
    assert post.status == PostStatus.stored
    
    ig_src = db_session.get(IGSource, post.ig_source_id)
    assert ig_src.ig_username == rpost.ig_username
    assert ig_src.is_active is False
    
    calls = patch_celery_and_ai["recreate"].calls
    assert len(calls) == 1
    
    assert len(post.image_local_paths) == 1

def test_recreate_radar_mode(db_session, patch_celery_and_ai):
    fp, story, dec, rpost = setup_story(db_session)
    dec.rule = "fast"
    dec.status = DecisionStatus.DISPATCHED
    db_session.commit()
    
    ig_src = IGSource(ig_username="r_user", is_active=False)
    db_session.add(ig_src)
    db_session.flush()
    
    post = Post(
        ig_source_id=ig_src.id, ig_media_id=f"radar:{rpost.shortcode}", ig_post_url=f"https://ig/{rpost.shortcode}",
        media_type="image", original_caption="cap", taken_at=datetime.utcnow(), image_source_urls=["http://test"],
        status=PostStatus.stored, image_local_paths=["/tmp/media/test.jpg"]
    )
    db_session.add(post)
    db_session.flush()
    
    t = DesignTemplate(name="Test News", category="news", canvas_width=1080, canvas_height=1350, template_json="{}")
    db_session.add(t)
    db_session.flush()
    fp.default_news_template_id = t.id
    db_session.commit()
    
    import builtins
    original_open = builtins.open
    def mock_open(name, *args, **kwargs):
        if str(name) == "/tmp/media/test.jpg":
            from unittest.mock import mock_open as mo
            return mo(read_data=b"image")()
        return original_open(name, *args, **kwargs)
        
    import unittest.mock
    with unittest.mock.patch("builtins.open", mock_open):
        recreate_post_for_fanpage(post.id, fp.id, radar_decision_id=dec.id)
    
    db_session.refresh(dec)
    assert dec.status == DecisionStatus.CREATED
    
    job = db_session.get(PublishJob, dec.publish_job_id)
    assert job.is_breaking is True
    assert job.ai_generated_caption == "fake caption"

def test_radar_dispatch_limits_exclusive(db_session):
    fp, story1, dec1, rpost1 = setup_story(db_session, sharing="exclusive")
    
    fps1 = [TargetFanpage(repliz_account_id=f"rep_ex_{i}", name=f"FP_EX_{i}", is_active=True, radar_daily_max=5, ig_recreate_enabled=True) for i in range(2)]
    db_session.add_all(fps1)
    db_session.flush()
    decs1 = [RadarStoryDecision(story_id=story1.id, fanpage_id=fp.id, rule="confirmed", status=DecisionStatus.QUEUED, via_viral_only_link=False) for fp in fps1]
    db_session.add_all(decs1)
    db_session.commit()
    
    radar_dispatch_tick()
    statuses = [db_session.get(RadarStoryDecision, d.id).status for d in [dec1] + decs1]
    assert statuses.count(DecisionStatus.DISPATCHED) == 1
    assert statuses.count(DecisionStatus.SKIPPED_DEDUP) == 2

def test_radar_dispatch_limits_share_max(db_session):
    fp, story2, dec2, rpost2 = setup_story(db_session, sharing="shared", share_max=2)
    
    fps2 = [TargetFanpage(repliz_account_id=f"rep_sm_{i}", name=f"FP_SM_{i}", is_active=True, radar_daily_max=5, ig_recreate_enabled=True) for i in range(2)]
    db_session.add_all(fps2)
    db_session.flush()
    decs2 = [RadarStoryDecision(story_id=story2.id, fanpage_id=fp.id, rule="confirmed", status=DecisionStatus.QUEUED, via_viral_only_link=False) for fp in fps2]
    db_session.add_all(decs2)
    db_session.commit()
    
    radar_dispatch_tick()
    statuses = [db_session.get(RadarStoryDecision, d.id).status for d in [dec2] + decs2]
    assert statuses.count(DecisionStatus.DISPATCHED) == 2
    assert statuses.count(DecisionStatus.SKIPPED_DEDUP) == 1

def test_radar_dispatch_limits_daily_cap(db_session):
    fp, story, dec, rpost = setup_story(db_session)
    fp.radar_daily_max = 1
    
    story_old = RadarStory(niche="f1", first_seen_at=datetime.utcnow(), last_member_at=datetime.utcnow(), status="triggered")
    db_session.add(story_old)
    db_session.flush()

    dec_old = RadarStoryDecision(story_id=story_old.id, fanpage_id=fp.id, rule="confirmed", status=DecisionStatus.CREATED, created_at=datetime.utcnow(), via_viral_only_link=False)
    db_session.add(dec_old)
    db_session.commit()
    
    radar_dispatch_tick()
    db_session.refresh(dec)
    assert dec.status == DecisionStatus.SKIPPED_DAILY_MAX

def test_radar_dedup(db_session):
    fp, story, dec, rpost = setup_story(db_session)
    rpost.caption = "This is a very specific unique caption for dedup testing"
    
    ig_src = IGSource(ig_username="r_user2", is_active=False)
    db_session.add(ig_src)
    db_session.flush()
    
    post = Post(
        ig_source_id=ig_src.id, ig_media_id="other:1", ig_post_url="https://ig/other1",
        media_type="image", status=PostStatus.done
    )
    db_session.add(post)
    db_session.flush()
    
    job = PublishJob(
        post_id=post.id, fanpage_id=fp.id, content_type=ContentType.ig_recreate,
        ai_generated_caption="This is a very specific unique caption for dedup testing indeed",
        status=PublishJobStatus.published
    )
    db_session.add(job)
    db_session.commit()
    
    radar_dispatch_tick()
    db_session.refresh(dec)
    assert dec.status == DecisionStatus.SKIPPED_DEDUP

def test_viral_only_decision(db_session, patch_celery_and_ai):
    fp, story, dec, rpost = setup_story(db_session)
    dec.via_viral_only_link = True
    
    ig_src = IGSource(ig_username="r_user", is_active=True)
    db_session.add(ig_src)
    db_session.flush()
    
    link = FanpageSource(fanpage_id=fp.id, ig_source_id=ig_src.id, is_active=True, trigger='viral_only')
    db_session.add(link)
    db_session.flush()
    
    post = Post(
        ig_source_id=ig_src.id, ig_media_id=f"ig:{rpost.shortcode}", ig_post_url=f"https://ig/{rpost.shortcode}",
        media_type="image", status=PostStatus.stored
    )
    db_session.add(post)
    db_session.commit()
    
    radar_dispatch_tick()
    db_session.refresh(dec)
    assert dec.status == DecisionStatus.DISPATCHED
    
    calls = patch_celery_and_ai["recreate"].calls
    assert len(calls) == 1

def test_viral_only_decision_plain_repost(db_session, patch_celery_and_ai):
    fp, story, dec, rpost = setup_story(db_session)
    dec.via_viral_only_link = True
    
    # disable recreate for fanpage and link
    fp.ig_recreate_enabled = False
    
    ig_src = IGSource(ig_username="r_user_plain", is_active=True)
    db_session.add(ig_src)
    db_session.flush()
    
    link = FanpageSource(fanpage_id=fp.id, ig_source_id=ig_src.id, is_active=True, trigger='viral_only', ig_recreate_enabled=False)
    db_session.add(link)
    db_session.flush()
    
    # Needs to match rpost's username to pass the check in `_process_story`
    rpost.ig_username = "r_user_plain"
    
    post = Post(
        ig_source_id=ig_src.id, ig_media_id=f"ig:{rpost.shortcode}", ig_post_url=f"https://ig/{rpost.shortcode}",
        media_type="image", status=PostStatus.stored
    )
    db_session.add(post)
    db_session.commit()
    
    radar_dispatch_tick()
    db_session.refresh(dec)
    assert dec.status == DecisionStatus.CREATED
    assert dec.publish_job_id is not None
    
    job = db_session.get(PublishJob, dec.publish_job_id)
    assert job.status == PublishJobStatus.pending_caption
    
    # AI caption generation should be scheduled instead of recreate
    calls = patch_celery_and_ai["ai_gen_caption"].calls
    assert len(calls) == 1

def test_story_processing_error_isolation(db_session, monkeypatch):
    fp1, story1, dec1, rpost1 = setup_story(db_session, niche="f1_1")
    fp2, story2, dec2, rpost2 = setup_story(db_session, niche="f1_2")
    
    from app.tasks.radar_dispatch import _process_story as original_process_story
    def mock_process_story(db, story, *args):
        if story.id == story1.id:
            raise ValueError("Boom")
        original_process_story(db, story, *args)
        
    monkeypatch.setattr("app.tasks.radar_dispatch._process_story", mock_process_story)
    
    radar_dispatch_tick()
    
    db_session.refresh(dec1)
    db_session.refresh(dec2)
    assert dec1.status == DecisionStatus.FAILED
    assert dec2.status == DecisionStatus.DISPATCHED

def test_quote_held_incomplete_or_mismatch(db_session, monkeypatch):
    fp, story, dec, rpost = setup_story(db_session)
    dec.status = DecisionStatus.DISPATCHED
    
    ig_src = IGSource(ig_username="r_user", is_active=False)
    db_session.add(ig_src)
    db_session.flush()
    post = Post(
        ig_source_id=ig_src.id, ig_media_id=f"radar:{rpost.shortcode}", ig_post_url=f"https://ig/{rpost.shortcode}",
        media_type="image", original_caption="raw caption", taken_at=datetime.utcnow(), image_source_urls=["http://test"],
        status=PostStatus.stored, image_local_paths=["/tmp/media/test.jpg"]
    )
    db_session.add(post)
    db_session.commit()
    
    from unittest.mock import MagicMock
    monkeypatch.setattr("app.services.ig_content_classifier.analyze_ig_post", lambda b, c, niche=None, allow_team_radio=False: MagicMock(type="quote", text="This quote ends with a comma,"))
    
    import builtins
    original_open = builtins.open
    def mock_open(name, *args, **kwargs):
        if str(name) == "/tmp/media/test.jpg":
            from unittest.mock import mock_open as mo
            return mo(read_data=b"image")()
        return original_open(name, *args, **kwargs)
        
    import unittest.mock
    with unittest.mock.patch("builtins.open", mock_open):
        recreate_post_for_fanpage(post.id, fp.id, radar_decision_id=dec.id)
    
    db_session.refresh(dec)
    assert dec.status == DecisionStatus.HELD_QUOTE_MISMATCH

def test_render_excludes_photo(db_session, monkeypatch):
    fp1, story, dec1, rpost = setup_story(db_session)
    dec1.status = DecisionStatus.CREATED
    dec1.used_photo_key = "used/photo.jpg"
    
    fp2 = TargetFanpage(repliz_account_id=f"rep_2_{datetime.now().timestamp()}", name="FP2", is_active=True, radar_daily_max=5, ig_recreate_enabled=True)
    db_session.add(fp2)
    db_session.flush()
    
    dec2 = RadarStoryDecision(story_id=story.id, fanpage_id=fp2.id, rule="confirmed", status=DecisionStatus.CREATED, via_viral_only_link=False)
    db_session.add(dec2)
    db_session.flush()
    
    ig_src = IGSource(ig_username="r_user", is_active=False)
    db_session.add(ig_src)
    db_session.flush()
    post = Post(
        ig_source_id=ig_src.id, ig_media_id=f"radar:{rpost.shortcode}", ig_post_url=f"https://ig/{rpost.shortcode}",
        media_type="image", original_caption="cap", taken_at=datetime.utcnow(), image_source_urls=["http://test"],
        status=PostStatus.stored, image_local_paths=["/tmp/media/test.jpg"]
    )
    db_session.add(post)
    db_session.flush()
    
    t = DesignTemplate(name="t", category="news", canvas_width=1080, canvas_height=1350, template_json="{}")
    db_session.add(t)
    db_session.flush()
    
    job = PublishJob(post_id=post.id, fanpage_id=fp2.id, content_type=ContentType.ig_recreate, design_template_id=t.id, status=PublishJobStatus.pending_design, design_title="Test headline for exclusion")
    db_session.add(job)
    db_session.flush()
    dec2.publish_job_id = job.id
    db_session.commit()
    
    used_excludes = None
    def mock_source_news_main(db, title, niche, exclude_paths=None, known_primary=None):
        nonlocal used_excludes
        used_excludes = exclude_paths
        return None, None
        
    monkeypatch.setattr("app.services.design_images.source_news_main", mock_source_news_main)
    
    import builtins
    original_open = builtins.open
    def mock_open(name, *args, **kwargs):
        if str(name) == "/tmp/media/test.jpg":
            from unittest.mock import mock_open as mo
            return mo(read_data=b"image")()
        return original_open(name, *args, **kwargs)
        
    import unittest.mock
    with unittest.mock.patch("builtins.open", mock_open):
        render_ig_recreate(job.id)
        
    assert used_excludes == {"used/photo.jpg"}
    db_session.refresh(dec2)
    assert dec2.status == DecisionStatus.SKIPPED_NO_DISTINCT_PHOTO

