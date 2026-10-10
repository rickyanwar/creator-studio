export type PublishMode = "auto" | "manual_review";
export type AttributionPosition = "caption_end" | "caption_start";
export type BurnerStatus = "active" | "challenged" | "rate_limited" | "banned";
export type PublishJobStatus =
  | "pending_watermark"
  | "pending_caption"
  | "pending_design"
  | "rendering"
  | "pending_review"
  | "pending_publish"
  | "publishing"
  | "published"
  | "failed"
  | "skipped";
export type ContentType = "ig_repost" | "news_content" | "ig_recreate" | "discussion" | "pinterest_content" | "facebook_recreate" | "youtube_clip";
export type AIProvider = "gemini" | "groq";
export type MediaType = "image" | "album";
export type PostStatus = "crawled" | "editing_image" | "stored" | "pending_fanout" | "done" | "cleaned";

export interface Fanpage {
  id: number;
  repliz_account_id: string;
  name: string;
  username: string | null;
  picture_url: string | null;
  platform_type: string;
  is_connected: boolean;
  is_active: boolean;
  publish_mode: PublishMode;
  // ── Publish pacing (anti-bot-detection) ──
  publish_sleep_start_hour: number | null;
  publish_sleep_end_hour: number | null;
  publish_daily_limit: number;
  caption_tone: string;
  caption_language: string;
  caption_max_length: number;
  caption_hashtag_count: number;
  caption_must_include: string[];
  caption_must_avoid: string[];
  caption_cta_text: string;
  use_attribution: boolean;
  caption_attribution_template: string;
  attribution_position: AttributionPosition;
  caption_custom_prompt: string;
  watermark_text: string | null;
  watermark_image_url: string | null;
  last_synced_at: string | null;
  created_at: string;
  
  // ── Radar (Feature 3) ──
  radar_enabled: boolean;
  radar_niches: string[];
  radar_shadow: boolean;
  radar_min_likes: number;
  radar_confirm_ratio: number;
  radar_fast_ratio: number;
  radar_fast_window_min: number;
  radar_shelf_news_h: number;
  radar_shelf_evergreen_h: number;
  radar_burst_enabled: boolean;
  radar_daily_max: number;
  visual_engine: "off" | "gflow" | "chatgpt" | "auto";

  // ── Content modes (Feature 2) ──
  mode1_ig_repost_enabled: boolean;
  mode2_news_content_enabled: boolean;
  mode2_publish_mode: PublishMode;
  mode2_gallery_keywords: string[];
  mode2_gallery_niches: string[];
  mode2_default_template_id: number | null;
  default_quote_template_id: number | null;
  default_news_template_id: number | null;
  ig_recreate_enabled: boolean;
  ig_recreate_quote_template_id: number | null;
  ig_recreate_news_template_id: number | null;
  ig_recreate_smart_layout: boolean;
  ig_recreate_split_template_id: number | null;
  design_expand: boolean;
  mode2_caption_tone: string;
  mode2_caption_language: string;
  mode2_caption_max_length: number;
  mode2_caption_hashtag_count: number;
  mode2_caption_cta_text: string;
  mode2_caption_custom_prompt: string;
  mode2_title_max_chars: number;
  mode2_badge_text: string | null;
  mode2_source_attribution: boolean;
  mode2_editorial_gate_enabled: boolean;
  // ── Mode 4: Discussion / hot-take content ──
  discussion_enabled: boolean;
  discussion_publish_mode: PublishMode;
  discussion_daily_count: number;
  discussion_topic_mode: string; // "news" | "evergreen" | "both"
  discussion_label_mode: string; // "discussion" | "hot_take" | "both"
  default_discussion_template_id: number | null;
  // ── Mode 5: Pinterest content ──
  pinterest_enabled: boolean;
  pinterest_publish_mode: PublishMode;
  pinterest_daily_count: number;
  pinterest_source_mode: string; // "curated" | "ai_keyword" | "both"
  pinterest_custom_prompt: string;
  pinterest_hashtag_count: number;
  pinterest_allow_low_quality: boolean;
  // ── Mode 6: Facebook photo recreate ──
  facebook_photo_enabled: boolean;
  facebook_photo_publish_mode: PublishMode;
  facebook_photo_daily_count: number;
  facebook_photo_topic_filter: string | null;
  // Mode 7: YouTube clips
  yt_clip_enabled: boolean;
  yt_clip_publish_mode: PublishMode;
  yt_clip_daily_count: number;
  yt_clip_min_s: number;
  yt_clip_max_s: number;
  yt_clip_per_video: number;
  yt_clip_min_score: number;
  yt_clip_max_video_age_days: number;
  yt_clip_captions: boolean;
  yt_clip_watermark: boolean;
  yt_clip_action_crop: "smart" | "fit";
}

export interface DiscussionTopicRef {
  id: number;
  seed_text: string;
  subject_hint: string | null;
  is_active: boolean;
  times_used: number;
  last_used_at: string | null;
}

export interface NewsSourceRef {
  id: number;
  name: string;
  category_url: string;
}

export interface IGSourceRef {
  id: number;
  ig_username: string;
  album_image_indices: number[];
  ig_recreate_enabled: boolean | null;
  caption_tone: string | null;
  caption_language: string | null;
  caption_max_length: number | null;
  caption_hashtag_count: number | null;
  caption_cta_text: string | null;
  caption_custom_prompt: string | null;
  watermark_text: string | null;
  watermark_image_url: string | null;
  last_used_at: string | null;
  trigger?: "every_post" | "viral_only";
}

export interface PinterestSourceRef {
  id: number;
  source_url: string;
  label: string | null;
  is_active: boolean;
  times_used: number;
  last_used_at: string | null;
}

export interface PinterestContentIdeaRef {
  id: number;
  gallery_image_id: number;
  title: string;
  description: string;
  source_type: string; // "ai_keyword" | "curated"
  status: string; // "pending" | "used"
  created_at: string;
  used_at: string | null;
}

export interface DiscussionContentIdeaRef {
  id: number;
  label: string; // "DISCUSSION" | "HOT TAKE"
  question: string;
  subject_name: string;
  caption: string;
  source_type: string; // "news" | "evergreen" | "general" | "manual"
  status: string; // "pending" | "used"
  created_at: string;
  used_at: string | null;
}

export interface FacebookPhotoSourceRef {
  id: number;
  page_url: string;
  label: string | null;
  is_active: boolean;
  times_used: number;
  last_used_at: string | null;
}

export interface FacebookPhotoIdeaRef {
  id: number;
  gallery_image_id: number; // dedup marker for the evaluated source photo, not a design photo
  category: string; // "news" | "quote" | "discussion"
  design_title: string;
  design_subtitle: string | null;
  design_caption: string | null;
  status: string; // "pending" | "used"
  created_at: string;
  used_at: string | null;
}

export interface YtClipSourceRef {
  id: number;
  url: string;
  kind: "channel" | "playlist" | "video";
  channel_id: string | null;
  playlist_id: string | null;
  video_id: string | null;
  label: string | null;
  direction: string | null;
  is_active: boolean;
  last_checked_at: string | null;
  last_error: string | null;
  videos_found: number;
}

export interface YtClipIdeaRef {
  id: number;
  yt_video_row_id: number;
  video_id: string;
  start_s: number;
  end_s: number;
  title: string;
  description: string | null;
  hook_text: string | null;
  virality_score: number;
  transcript_excerpt: string | null;
  status: string; // "pending" | "used"
  preview_url: string;
  video_title: string | null;
  created_at: string;
  used_at: string | null;
}

export interface YtVideoRef {
  id: number;
  video_id: string;
  title: string | null;
  channel_name: string | null;
  published_at: string | null;
  duration_s: number | null;
  status: "discovered" | "analyzing" | "analyzed" | "skipped" | "failed";
  skip_reason: string | null;
  last_error: string | null;
  ideas_created: number;
  created_at: string;
}

export interface FanpageDetail extends Fanpage {
  ig_sources: IGSourceRef[];
  ig_source_usernames: string[];
  news_sources: NewsSourceRef[];
  discussion_topics: DiscussionTopicRef[];
  discussion_content_ideas: DiscussionContentIdeaRef[];
  pinterest_sources: PinterestSourceRef[];
  pinterest_content_ideas: PinterestContentIdeaRef[];
  facebook_photo_sources: FacebookPhotoSourceRef[];
  facebook_photo_ideas: FacebookPhotoIdeaRef[];
  yt_clip_sources: YtClipSourceRef[];
}

export interface Burner {
  id: number;
  ig_username: string;
  proxy_url: string | null;
  status: BurnerStatus;
  requests_today: number;
  last_used_at: string | null;
  cooldown_until: string | null;
  last_error: string | null;
  story_enabled: boolean;
  last_story_at: string | null;
  comment_enabled: boolean;
  last_comment_at: string | null;
  created_at: string;
}

// ── Radar (Feature 3) ──
export interface RadarAccount {
  id: number;
  niche: string;
  ig_username: string;
  is_active: boolean;
  leader_score: number;
  last_checked_at: string | null;
  last_post_seen_at: string | null;
  avg_scrape_seconds: number | null;
  last_error: string | null;
}

export interface RadarStoryMember {
  ig_username: string;
  shortcode: string;
  post_url: string;
  taken_at: string;
  latest_like_count: number | null;
  latest_comment_count: number | null;
  thumbnail_url: string | null;
}

export interface RadarDecision {
  fanpage_id: number;
  fanpage_name: string;
  rule: string;
  status: string;
  shadow: boolean;
  would_trigger_at: string | null;
  reason: string | null;
  publish_job_id: number | null;
  preview_image_path: string | null;
  preview_status: string | null;
  preview_error: string | null;
  id: number;
}

export interface RadarStory {
  id: number;
  niche: string | null;
  first_seen_at: string;
  last_member_at: string;
  member_count: number;
  distinct_accounts: number;
  heat_score: number;
  status: string;
  shelf_kind: string | null;
  expires_at: string | null;
  final_max_likes_24h: number | null;
  members: RadarStoryMember[];
  decisions: RadarDecision[];
}

export interface ShadowReportDecision {
  story_id: number;
  rule: string;
  minutes: number;
  username: string;
  shortcode: string;
  final_likes: number | null;
}

export interface ShadowReportSection {
  decisions_by_rule: Record<string, number>;
  median_minutes: number;
  p90_minutes: number;
  precision: number;
  missed_stories: number;
  recent_decisions: ShadowReportDecision[];
}

export interface ShadowReport {
  overall: ShadowReportSection;
  fanpages: Record<string, ShadowReportSection>;
}

export interface PublishJob {

  id: number;
  post_id: number | null;
  fanpage_id: number;
  content_type: ContentType;
  source_article_id: number | null;
  design_title: string | null;
  design_image_url: string | null;
  design_template_id: number | null;
  design_subtitle?: string | null;
  // Mode 7 (youtube_clip)
  video_url?: string | null;
  video_thumbnail_url?: string | null;
  video_duration_s?: number | null;
  yt_video_id?: string | null;
  clip_start_s?: number | null;
  clip_end_s?: number | null;
  ai_generated_caption: string | null;
  ai_provider_used: AIProvider | null;
  status: PublishJobStatus;
  repliz_schedule_id: string | null;
  attempt_count: number;
  last_error: string | null;
  published_at: string | null;
  scheduled_for: string | null;
  cleanup_at: string | null;
  created_at: string;
  updated_at: string;
  // Enriched
  fanpage_name: string | null;
  fanpage_picture_url: string | null;
  ig_username: string | null;
  image_public_urls: string[];
  media_type: MediaType | null;
  ig_post_url: string | null;
  article_url: string | null;
  article_source_name: string | null;
  /** S9: Mode-6 (facebook_recreate) — URL of the source FB photo used to
   *  inspire this card, or null when not recorded / gallery row deleted. */
  source_photo_url?: string | null;
}

export interface DashboardStats {
  published_today: number;
  failed_today: number;
  pending_review: number;
  active_fanpages: number;
  total_fanpages: number;
  burners: Array<{
    id: number;
    ig_username: string;
    status: BurnerStatus;
    requests_today: number;
    cooldown_until: string | null;
    last_error: string | null;
  }>;
  disk_used_mb: number;
  disk_total_mb: number;
  disk_gallery_mb: number;
  ai_stats: {
    total: number;
    success: number;
    recovered: number;
    failed: number;
    success_rate: number | null;
  };
  gallery_fetch_stats: {
    total: number;
    success: number;
    failed: number;
  };
}

export interface CrawlerHealth {
  beat_healthy: boolean;
  last_crawl_at: string | null;
  minutes_since_crawl: number | null;
  in_sleep_window: boolean;
  sleep_start_wib: number;
  sleep_end_wib: number;
  crawl_interval_minutes: number;
  server_time_utc: string;
  server_time_wib: string;
  active_sources: number;
}

export interface ScraperHealth {
  available: boolean;
  scraper_mode: "auto" | "instagrapi" | "viewer";
  generated_at: string;
  tiers: Partial<Record<"gramsnap" | "anonyig" | "igstoryviewer", {
    status: "healthy" | "degraded" | "unhealthy" | "unknown";
    last_success_at: string;
    last_failure_at: string;
    last_failure_username: string;
    consecutive_failures: number;
    distinct_users: number;
    last_error_kind: string;
    last_error: string;
    unhealthy: boolean;
    events: Array<{
      at: string;
      username: string;
      ok: boolean;
      kind: string | null;
      reason: string;
      detail: string | null;
    }>;
  }>>;
}

export interface AppSettings {
  crawl_interval_minutes: number;
  max_post_age_days: number;
  ai_provider_primary: string;
  ai_provider_fallback: string;
  storage_base_url: string | null;
  storage_base_path: string | null;
  ai_fallback_after_failures: number;
  ai_fallback_reset_after_minutes: number;
  has_gemini_key: boolean;
  has_groq_key: boolean;
  has_repliz_keys: boolean;
  has_telegram_token: boolean;
  telegram_chat_id: string | null;
  scraper_mode: "auto" | "instagrapi" | "viewer" | "flashapi"; // keep flashapi in type for fallback check
  scraper_proxies: string | null;
  scraper_proxy_count: number;
  scraper_relays: string | null;
  scraper_relay_count: number;
  gallery_scraping_paused: boolean;
  nine_router_base_url: string | null;
  nine_router_model: string | null;
  nine_router_discussion_model: string | null;
  has_nine_router_key: boolean;
  has_youtube_cookies?: boolean;
  youtube_proxy?: string | null;
  youtube_blocked_until?: string | null;
  youtube_last_error?: string | null;

  // ── Radar (Feature 3) ──
  radar_sleep_start_wib: number | null;
  radar_sleep_end_wib: number | null;
  radar_very_hot_interval_min: number;
  radar_hot_interval_min: number;
  radar_cold_interval_min: number;
  radar_hot_window_h: number;
  radar_track_max_age_h: number;
  radar_story_sharing: "shared" | "exclusive";
  radar_share_max: number;
  radar_news_stagger_max_min: number;
}

export interface F1Driver {
  id: number;
  season: number;
  surname: string;
  full_name?: string;
  number: number;
  team_name: string;
  team_colour: string;
  team_logo_path?: string;
  verified: boolean;
  updated_at?: string;
}
