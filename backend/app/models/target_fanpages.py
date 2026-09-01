import enum
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, Enum, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from app.database import Base


class PublishMode(str, enum.Enum):
    auto = "auto"
    manual_review = "manual_review"


class AttributionPosition(str, enum.Enum):
    caption_end = "caption_end"
    caption_start = "caption_start"


class TargetFanpage(Base):
    __tablename__ = "target_fanpages"

    id = Column(Integer, primary_key=True, index=True)

    # ── From Repliz ───────────────────────────────
    repliz_account_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(256), nullable=False)
    username = Column(String(128), nullable=True)
    picture_url = Column(Text, nullable=True)
    platform_type = Column(String(32), default="facebook", nullable=False)
    is_connected = Column(Boolean, default=True, nullable=False)

    # ── Local toggles ─────────────────────────────
    is_active = Column(Boolean, default=False, nullable=False)
    publish_mode = Column(Enum(PublishMode), default=PublishMode.manual_review, nullable=False)

    # ── Publish pacing (anti-bot-detection) ───────
    # WIB hours during which this fanpage never auto-publishes (a real page
    # admin isn't scheduling posts at 3am) — null/null = no sleep window.
    # Applies across every content mode (repost/news/ig_recreate), same as
    # the daily cap below. See publisher._next_schedule_at.
    publish_sleep_start_hour = Column(Integer, nullable=True)
    publish_sleep_end_hour = Column(Integer, nullable=True)
    # Max auto-publishes per WIB calendar day — beyond this, the next slot
    # rolls to the following day instead of continuing nonstop. A page that
    # never stops posting is itself a bot signal, independent of spacing.
    publish_daily_limit = Column(Integer, default=45, nullable=False, server_default="45")

    # ── Content modes (Feature 2) ─────────────────
    mode1_ig_repost_enabled = Column(Boolean, default=True, nullable=False, server_default="true")
    mode2_news_content_enabled = Column(Boolean, default=False, nullable=False, server_default="false")
    mode2_publish_mode = Column(Enum(PublishMode), default=PublishMode.manual_review, nullable=False, server_default="manual_review")
    mode2_gallery_keywords = Column(ARRAY(String), server_default="{}", nullable=False)  # deprecated — superseded by mode2_gallery_niches
    # Gallery images eligible for this fanpage = every active GalleryKeyword
    # under these niches (e.g. ["MotoGP"]) — see design_images.niche_keywords.
    # Replaces mode2_gallery_keywords: a new keyword added under a subscribed
    # niche is picked up automatically, no per-fanpage curation needed.
    mode2_gallery_niches = Column(ARRAY(String), server_default="{}", nullable=False)
    mode2_default_template_id = Column(Integer, nullable=True)  # deprecated — superseded by default_news_template_id

    # ── Default design templates (one setting per category, shared across
    #    modes) — a "news"-category template renders both Mode 2 news_content
    #    jobs AND Mode 3 ig_recreate posts classified "news"; a "quote"-
    #    category template renders Mode 3 ig_recreate posts classified
    #    "quote". Replaces mode2_default_template_id / ig_recreate_quote_
    #    template_id / ig_recreate_news_template_id (3 fields, set in 2
    #    different UI sections, for what's conceptually the same 2 choices).
    default_quote_template_id = Column(Integer, nullable=True)  # FK to design_templates
    default_news_template_id = Column(Integer, nullable=True)   # FK to design_templates
    default_discussion_template_id = Column(Integer, nullable=True)  # FK to design_templates (Mode 4)

    # ── Mode 2 caption criteria (separate set from Mode 1) ──
    mode2_caption_tone = Column(String(64), default="informative", nullable=False, server_default="informative")
    mode2_caption_language = Column(String(8), default="en", nullable=False, server_default="en")
    mode2_caption_max_length = Column(Integer, default=500, nullable=False, server_default="500")
    mode2_caption_hashtag_count = Column(Integer, default=5, nullable=False, server_default="5")
    mode2_caption_cta_text = Column(String(256), default="", nullable=False, server_default="")
    mode2_caption_custom_prompt = Column(Text, default="", nullable=False, server_default="")
    mode2_title_max_chars = Column(Integer, default=80, nullable=False, server_default="80")
    # Small pill badge shown above the headline on News templates (e.g.
    # "F1 NEWS") — see design_renderer.render_design. Empty/null falls back
    # to "{first mode2_gallery_niches entry} NEWS" at render time (never
    # stored here, so an admin editing niches later doesn't leave a stale
    # baked-in string); an admin-set value here always wins over that
    # fallback. Renderer hides the badge entirely (see inject.js's "label"
    # handling) when there's neither an override nor a niche to fall back to.
    mode2_badge_text = Column(String(64), nullable=True)
    mode2_source_attribution = Column(Boolean, default=True, nullable=False, server_default="true")
    # Opt-in editorial AI gate: before copywriting, 9Router web-search-fact-checks
    # the article and judges whether it's worth posting/likely engagement — a
    # rejected article gets no PublishJob for this fanpage (see
    # editorial_gate.evaluate_article, wired in news_copywriter.copywrite_article).
    mode2_editorial_gate_enabled = Column(Boolean, default=False, nullable=False, server_default="false")

    # ── Mode 3: IG content recreate ───────────────
    # Classify each scraped IG post image (9Router vision) → recreate it on a
    # per-fanpage template: quote → quote template, news → news template (title
    # AI-rewritten), other → skipped. Both use the IG image as the photo.
    ig_recreate_enabled = Column(Boolean, default=False, nullable=False, server_default="false")
    ig_recreate_quote_template_id = Column(Integer, nullable=True)  # deprecated — superseded by default_quote_template_id
    ig_recreate_news_template_id = Column(Integer, nullable=True)   # deprecated — superseded by default_news_template_id
    # Opt-in "smart" 2-photo design: AI decides 1 vs 2 people → single+inset or
    # split, picks face/action photos, face-aware crop. Off = basic single photo.
    ig_recreate_smart_layout = Column(Boolean, default=False, nullable=False, server_default="false")
    ig_recreate_split_template_id = Column(Integer, nullable=True)  # 2-person split template
    # Opt-in "expand to fill": when the photo would be hard-cropped, fill the
    # frame instead (reflect-extend for action, fit+blur for close-up faces).
    # Photos that already fit are left untouched. News + IG-recreate designs.
    design_expand = Column(Boolean, default=False, nullable=False, server_default="false")

    # ── Mode 4: Discussion / hot-take content ─────
    # AI-generated debate cards (badge + big question + athlete photo, no
    # yes/no buttons). Topics come from freshly scraped news (fact-grounded),
    # curated evergreen seeds (DiscussionTopic, opinion-only), or both. Unlike
    # Mode 2 (one card per incoming article), Mode 4 is quota-driven: the
    # scheduler creates up to discussion_daily_count cards per WIB day, spread
    # across the day (see app/tasks/discussion.py). Captions reuse the Mode 2
    # caption criteria (mode2_caption_*) to avoid a duplicate field set.
    discussion_enabled = Column(Boolean, default=False, nullable=False, server_default="false")
    discussion_publish_mode = Column(Enum(PublishMode), default=PublishMode.manual_review, nullable=False, server_default="manual_review")
    discussion_daily_count = Column(Integer, default=2, nullable=False, server_default="2")
    # "news" | "evergreen" | "both" — where the scheduler draws topics from.
    discussion_topic_mode = Column(String(16), default="both", nullable=False, server_default="both")
    # "discussion" | "hot_take" | "both" — restricts which label style the AI
    # is allowed to pick (see news_copywriter.py's discussion prompt builders).
    discussion_label_mode = Column(String(16), default="both", nullable=False, server_default="both")

    # ── Mode 5: Pinterest content ──────────────────
    # A photo is the seed instead of text: candidates are pulled from
    # Pinterest, turned into reviewable PinterestContentIdea rows (title +
    # description + a bound GalleryImage), and consumed FIFO on this
    # fanpage's own pacing (see app/tasks/pinterest.py) into a rendered
    # card. No dedicated Mode 5 template setting — it reuses the SAME
    # Quote/News template pools Mode 2/3 already have (default_quote_
    # template_id / default_news_template_id above), picking between them
    # per idea by whether the bound photo has a detected face: a face reads
    # as a quote-style portrait (badge/name treatment), no face reads as a
    # news-style scene (headline over the full photo) — see
    # render_pinterest's own face check, mirrors ig_content_classifier's
    # news/quote split for Mode 3 without needing a second AI call.
    pinterest_enabled = Column(Boolean, default=False, nullable=False, server_default="false")
    pinterest_publish_mode = Column(Enum(PublishMode), default=PublishMode.manual_review, nullable=False, server_default="manual_review")
    pinterest_daily_count = Column(Integer, default=2, nullable=False, server_default="2")
    # "curated" | "ai_keyword" | "both" — where idea candidates are pulled
    # from. On "both", ai_keyword is tried first each tick, falling back to
    # the curated PinterestSource rotation only when it yields nothing new.
    pinterest_source_mode = Column(String(16), default="both", nullable=False, server_default="both")
    # Free-text persona/angle the admin writes for this page, e.g. "You are
    # a passionate Formula 1 historian and journalist covering the 1970s-90s
    # golden era." Injected into both the AI-keyword topic pick
    # (generate_pinterest_search_keyword) and the photo description/caption
    # generation (vision_identify_pin_subject / vision_check_pin_description)
    # so every idea for this page reads in the same voice/angle — same idea
    # as caption_custom_prompt, but Mode 5 has no copywriter call to attach
    # it to, so it gets its own field threaded through the vision calls.
    pinterest_custom_prompt = Column(Text, default="", nullable=False, server_default="")
    # Hashtags appended to the Facebook post caption only (never drawn on the
    # design image) — same "N relevant hashtags" convention as
    # mode2_caption_hashtag_count, generated fresh at consume time
    # (news_copywriter.generate_pinterest_hashtags) since Mode 5 ideas carry
    # no caption field of their own to bake them into.
    pinterest_hashtag_count = Column(Integer, default=5, nullable=False, server_default="5")

    # ── Caption criteria ──────────────────────────
    caption_tone = Column(String(64), default="engaging", nullable=False)
    caption_language = Column(String(8), default="en", nullable=False)
    caption_max_length = Column(Integer, default=500, nullable=False)
    caption_hashtag_count = Column(Integer, default=5, nullable=False)
    caption_must_include = Column(ARRAY(String), server_default="{}", nullable=False)
    caption_must_avoid = Column(ARRAY(String), server_default="{}", nullable=False)
    caption_cta_text = Column(String(256), default="", nullable=False)
    use_attribution = Column(Boolean, default=True, nullable=False)
    caption_attribution_template = Column(String(128), default="via @{source_username}", nullable=False)
    attribution_position = Column(Enum(AttributionPosition), default=AttributionPosition.caption_end, nullable=False)
    caption_custom_prompt = Column(Text, default="", nullable=False)

    # ── Image watermark (per fanpage) ─────────────
    watermark_text = Column(String(128), nullable=True)
    watermark_image_url = Column(String(512), nullable=True)  # logo watermark (overrides text on designs)

    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # ── Relationships ─────────────────────────────
    source_links = relationship("FanpageSource", back_populates="fanpage", cascade="all, delete-orphan")
    news_source_links = relationship("FanpageNewsSource", back_populates="fanpage", cascade="all, delete-orphan")
    discussion_topics = relationship("DiscussionTopic", back_populates="fanpage", cascade="all, delete-orphan")
    pinterest_sources = relationship("PinterestSource", back_populates="fanpage", cascade="all, delete-orphan")
    pinterest_content_ideas = relationship("PinterestContentIdea", back_populates="fanpage", cascade="all, delete-orphan")
    discussion_content_ideas = relationship("DiscussionContentIdea", back_populates="fanpage", cascade="all, delete-orphan")
    publish_jobs = relationship("PublishJob", back_populates="fanpage")
