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
export type ContentType = "ig_repost" | "news_content";
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
  mode2_source_attribution: boolean;
  mode2_editorial_gate_enabled: boolean;
  // ── Mode 4: Discussion / hot-take content ──
  discussion_enabled: boolean;
  discussion_publish_mode: PublishMode;
  discussion_daily_count: number;
  discussion_topic_mode: string; // "news" | "evergreen" | "both"
  default_discussion_template_id: number | null;
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
}

export interface FanpageDetail extends Fanpage {
  ig_sources: IGSourceRef[];
  ig_source_usernames: string[];
  news_sources: NewsSourceRef[];
  discussion_topics: DiscussionTopicRef[];
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

export interface PublishJob {
  id: number;
  post_id: number | null;
  fanpage_id: number;
  content_type: "ig_repost" | "news_content" | "ig_recreate" | "discussion";
  source_article_id: number | null;
  design_title: string | null;
  design_image_url: string | null;
  design_template_id: number | null;
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
  scraper_mode: "auto" | "instagrapi" | "flashapi";
  has_flashapi_key: boolean;
  scraper_proxies: string | null;
  scraper_proxy_count: number;
  scraper_relays: string | null;
  scraper_relay_count: number;
  gallery_scraping_paused: boolean;
  nine_router_base_url: string | null;
  nine_router_model: string | null;
  nine_router_discussion_model: string | null;
  has_nine_router_key: boolean;
}
