export type PublishMode = "auto" | "manual_review";
export type AttributionPosition = "caption_end" | "caption_start";
export type BurnerStatus = "active" | "challenged" | "rate_limited" | "banned";
export type PublishJobStatus =
  | "pending_caption"
  | "pending_review"
  | "pending_publish"
  | "published"
  | "failed"
  | "skipped";
export type AIProvider = "gemini" | "groq";
export type MediaType = "image" | "album";
export type PostStatus = "crawled" | "stored" | "pending_fanout" | "done" | "cleaned";

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
  last_synced_at: string | null;
  created_at: string;
}

export interface IGSourceRef {
  id: number;
  ig_username: string;
  album_image_indices: number[];
}

export interface FanpageDetail extends Fanpage {
  ig_sources: IGSourceRef[];
  ig_source_usernames: string[];
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
  post_id: number;
  fanpage_id: number;
  ai_generated_caption: string | null;
  ai_provider_used: AIProvider | null;
  status: PublishJobStatus;
  repliz_schedule_id: string | null;
  attempt_count: number;
  last_error: string | null;
  published_at: string | null;
  cleanup_at: string | null;
  created_at: string;
  updated_at: string;
  // Enriched
  fanpage_name: string | null;
  fanpage_picture_url: string | null;
  ig_username: string | null;
  image_public_urls: string[];
  media_type: MediaType | null;
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
}
