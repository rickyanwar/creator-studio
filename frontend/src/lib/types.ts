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
export type ContentType = "ig_repost" | "news_content" | "ig_recreate" | "discussion" | "pinterest_content" | "facebook_recreate" | "youtube_clip" | "ai_video";
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
  target_country: string | null;
  timezone: string;
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
  news_visual_engine: "off" | "chatgpt";
  fb_visual_engine: "off" | "chatgpt";
  quote_shorten_enabled: boolean;
  qa_news_enabled?: boolean;
  qa_ig_enabled?: boolean;
  qa_facebook_enabled?: boolean;
  qa_ai_video_enabled?: boolean;
  ai_video_prompt_polish_enabled?: boolean;
  ig_mode?: "repost" | "recreate_template" | "recreate_chatgpt";

  // ── Content modes (Feature 2) ──
  mode1_ig_repost_enabled: boolean;
  mode2_news_content_enabled: boolean;
  mode2_publish_mode: PublishMode;
  mode2_gallery_keywords: string[];
  mode2_gallery_niches: string[];
  mode2_default_template_id: number | null;
  default_quote_template_id: number | null;
  default_news_template_id: number | null;
  template_style?: "gp" | "highlight" | "classic" | null;
  brand_override?: boolean;
  accent_color: string | null;
  accent_secondary_color?: string | null;
  badge_color?: string | null;
  badge_text_color?: string | null;
  quote_mark_color?: string | null;
  quote_streak?: boolean | null;
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
  badge_breaking_enabled?: boolean;
  badge_discussion_enabled?: boolean;
  news_badge_percent?: number;
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
  yt_clip_title_max_chars?: number;
  yt_clip_description_max_chars?: number;
  ai_video_enabled: boolean;
  ai_video_publish_mode: PublishMode;
  ai_video_daily_count: number;
  ai_video_min_score: number | null;
  ai_video_template: string | null;
  ai_video_templates: string[] | null;
  ai_video_clips: number;
  ai_video_clips_min: number | null;
  ai_video_seconds_min: number | null;
  ai_video_seconds_max: number | null;
  ai_video_max_credits_per_video: number | null;
  ai_video_max_credits_per_day: number | null;
  ai_video_narrator: string | null;
  ai_video_title_max_chars?: number;
  ai_video_description_max_chars?: number;
  ai_video_logo_enabled: boolean;
  ai_video_logo_size_pct: number;
  ai_video_logo_position: "top_right" | "top_left" | "bottom_right" | "bottom_left";
  ai_video_logo_margin_pct: number;
  ai_video_captions_enabled: boolean;
  ai_video_caption_size: number;
  ai_video_caption_bottom_pct: number;
  ai_video_caption_highlight: string;
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
  niche: string | null;
  fanpage_niches: string[];
  niche_mismatch: boolean;
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
  design_engine?: "chatgpt" | "codex" | "codex_fallback" | "template" | null;
  design_engine_note?: string | null;
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
  burners?: Array<{
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
  metrics_ingestion_enabled: boolean;
  metrics_plan_status: string | null;
  metrics_plan_checked_at: string | null;
  metrics_last_error: string | null;
  ai_video_worker_enabled: boolean;
  ai_video_vision_qc_enabled: boolean;
  ai_video_credit_reserve: number;
  ai_video_researcher?: "muse" | "hermes";
  muse_research_mode?: "pilot" | "live";
  muse_pilot_packs?: number;
  ai_video_idea_bank_days?: number;
  research_references?: ResearchReferences | null;
  research_last_report?: Record<string, unknown> | null;
  research_last_call_at?: string | null;
  gallery_labeling_enabled?: boolean;
  pro_poster_enabled?: boolean;
  pro_poster_fanpage_ids?: number[];
  gallery_labeling_daily_cap?: number;
  gallery_labeling_since?: string | null;
  gallery_labels_today?: number;
  gallery_labels_total?: number;
  gallery_labels_pending?: number;
  gallery_labels_failed?: number;
  // ── AI Image Engine (ChatGPT / Codex) ──
  visual_engine_daily_max?: number;
  gptcli_enabled?: boolean;
  gptcli_base_url?: string;
  has_gptcli_token?: boolean;
  gptcli_token?: string;
  gptcli_fanpage_ids?: number[];
  gptcli_thread?: string;
  gptcli_size?: string;
  ai_day_start?: string;
  ai_night_start?: string;
  codex_share_percent?: number;
  codex_share_window_jobs?: number;
  codex_overflow_queue_len?: number;
  codex_share_max_pct?: number;
  codex_fallback_max_pct?: number;
  codex_night_max_pct?: number;
  codex_rest_pct?: number;
  codex_window_capacity_est?: number;
  codex_night_min_gap_s?: number;
  codex_night_gap_jitter_s?: number;
  codex_usage_url?: string | null;
  prompt_polish_base_url?: string;
  has_prompt_polish_token?: boolean;
  prompt_polish_token?: string;
  prompt_polish_model?: string;
}

export interface AiEngineStatus {
  today?: {
    gptcli?: { success?: number; recovered?: number; failed?: number };
    "9router"?: { success?: number; recovered?: number; failed?: number };
    none?: { failed?: number };
    deferred?: number;
  };
  identity_rejects_today?: number;
  codex_day_share?: number | null;
  codex_usage?: {
    window_pct?: number;
    weekly_pct?: number | null;
    resets_at?: string | null;
    source?: "9router" | "estimate" | string;
  } | null;
  gptcli_unavailable_until?: string | null;
  gptcli_unavailable_reason?: string | null;
  visual_queue_len?: number;
  gptcli_enabled?: boolean;
  gptcli_fanpage_ids?: number[];
}

export type AiVideoIdeaStatus = "pilot" | "proposed" | "approved" | "rejected" | "queued" | "done" | "failed";

export interface AiVideoCounts {
  jobs_total: number;
  jobs_by_status: Record<string, number>;
  ideas_by_status: Record<string, number>;
  ideas_active: number;
}

export interface ResearchLink {
  url: string;
  note?: string;
}

export type ResearchReferences = Record<string, { video_style: ResearchLink[]; topic_signals: ResearchLink[] }>;

export interface ResearchFeedbackItem {
  id: number;
  created_at: string;
  author: "studio" | "muse";
  kind: "comment" | "rejected" | "note";
  message: string;
  username: string | null;
}

export interface ResearchActivity {
  calls: { id: number; created_at: string; endpoint: string; action: string; detail: Record<string, unknown> | null }[];
  notes: { id: number; created_at: string; client_ref: string | null; message: string }[];
}
export type AiVideoJobStatus = "queued" | "claimed" | "generating" | "uploaded" | "done" | "failed" | "cancelled";

export interface AiVideoIdea {
  id: number;
  fanpage_id: number;
  fanpage_name: string;
  template_key: string;
  topic: string;
  angle: string | null;
  research_notes: string | null;
  variables: Record<string, unknown> | null;
  clips: number | null;
  score: number | null;
  rationale: string | null;
  evidence: Record<string, unknown> | null;
  source: string;
  status: AiVideoIdeaStatus;
  error: string | null;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  video_job_id: number | null;
  client_ref?: string | null;
  title?: string | null;
  caption?: string | null;
}

export interface AiVideoJob {
  id: number;
  fanpage_id: number;
  fanpage_name: string;
  idea_id: number | null;
  template_key: string;
  title: string;
  status: AiVideoJobStatus;
  clips_total: number;
  clips_done: number;
  credits_estimate: number;
  account: { id: number; profile: string; email?: string | null } | null;
  worker_id: string | null;
  generation: number;
  restarts: number;
  error: string | null;
  qc: { passed?: boolean; reasons?: string[]; technical?: { passed?: boolean }; vision?: { passed?: boolean; issues?: string[] } | null } | null;
  video_url: string | null;
  thumbnail_url: string | null;
  publish_job_id: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface GflowAccount {
  id: number;
  worker_id: string;
  profile: string;
  email: string | null;
  label: string | null;
  enabled: boolean;
  authenticated: boolean;
  status: "ok" | "auth_error" | "no_access" | "needs_terms" | "error" | string;
  credits: number | null;
  credits_checked_at: string | null;
  last_error: string | null;
  cooldown_until?: string | null;
  last_seen_at: string | null;
  last_used_at: string | null;
  busy_job_id: number | null;
}

export interface GflowWorkerActivityJob {
  id: number;
  title: string;
  fanpage_name: string;
  account: string | null;
  status: string;
  clips_done: number;
  clips_total: number;
  started_at: string | null;
  last_progress_at: string | null;
}

export interface GflowWorkerChecking {
  done: number;
  total: number | null;
}

export interface GflowWorkerActivity {
  state: "working" | "checking" | "resting" | "waiting" | "idle" | "offline" | "stalled";
  job: GflowWorkerActivityJob | null;
  checking?: GflowWorkerChecking | null;
  resting_until: string | null;
  retry_at?: string | null;
  queued: number;
  next_account_free_at: string | null;
}

export interface GflowWorker {
  worker_id: string;
  last_seen_at: string | null;
  online: boolean;
  version: string | null;
  max_concurrent: number | null;
  mem_available_mb: number | null;
  running_job_ids: number[];
  paused_until: string | null;
  activity?: GflowWorkerActivity;
}

export interface AiVideoTemplate {
  key: string;
  name: string;
  niches: string[];
  clip_options: number[];
  default_clips: number;
  clip_seconds: number;
  credits_per_clip: number;
  model: string;
  variables_by_clips: Record<string, string[]>;
  vehicle_presets: string[];
  liveries: string[];
  narrator_presets: string[];
  topic_ideas: string[];
}

export interface PromptPolishClip {
  index: number;
  mode: string;
  prompt: string;
  prompt_original?: string | null;
}

export interface PromptPolishIdea {
  idea_id: number;
  state: "off" | "polished" | "waiting" | "failed" | string;
  prepared_at: string | null;
  polish_attempts: number;
  polish_error: string | null;
  changes: string[];
  voice_sentence: string;
  ambience_sentence: string;
  clips: PromptPolishClip[];
}

export interface PromptPolishJob {
  job_id: number;
  polished: boolean;
  clips: PromptPolishClip[];
  changes: string[];
}

export interface PromptPolishAccount {
  name: string;
  status: string;
  available: boolean;
  reason?: string | null;
  used_today?: number;
  cap_today?: number;
  next_ok_at?: number | null;
  cooldown_until?: number | null;
  sleep_start?: number | string | null;
  sleep_end?: number | string | null;
  last_error_type?: string | null;
  last_used_at?: number | null;
}

export interface PromptPolishServiceHealth {
  ok?: boolean;
  proxy_ok?: boolean;
  ready_now?: number;
  accounts?: PromptPolishAccount[];
  error?: string;
}

export interface PromptPolishHealth {
  generated_at?: string;
  configured: boolean;
  brands_on: number;
  service: PromptPolishServiceHealth;
  last_run: {
    at?: string;
    prepared?: number;
    failed?: number;
    unavailable?: string | null;
    retry_after_s?: number | null;
    detail?: string;
  } | null;
  last_ok: string | null;
  ideas: {
    prepared: number;
    waiting: number;
    failed_24h: number;
  };
  starving: number[];
  problems: string[];
  ok: boolean;
}

export interface Template {
  id: number;
  name: string;
  category: "quote" | "news" | "discussion" | null;
  template_json?: unknown;
  canvas_width: number;
  canvas_height: number;
  is_default: boolean;
  fanpage_id: number | null;
  style?: string | null;
  is_hidden?: boolean;
  brand_defaults?: Record<string, unknown> | null;
  replacement_style?: string | null;
}

export interface AnalyticsMetricsStatus {
  enabled: boolean;
  plan_status: string | null;
  checked_at: string | null;
  last_error: string | null;
  snapshots_total: number;
}

export interface AnalyticsTrend {
  label: "rising" | "flat" | "falling" | "insufficient";
  change_pct: number | null;
  n_recent: number;
  n_prior: number;
}

export interface AnalyticsOverview {
  days: number;
  metrics: AnalyticsMetricsStatus;
  fanpages: Array<{
    fanpage_id: number;
    name: string;
    timezone: string;
    is_active: boolean;
    posts: number;
    posts_with_metrics: number;
    eng_final_median: number | null;
    trend: AnalyticsTrend;
    best_hour_local: number | null;
  }>;
}

export interface FanpageAnalytics {
  fanpage: {
    id: number;
    name: string;
    timezone: string;
    target_country: string;
  };
  days: number;
  metrics: AnalyticsMetricsStatus;
  totals: {
    posts: number;
    posts_with_metrics: number;
    likes: number;
    comments: number;
    shares: number;
    engagement: number;
  };
  daily: Array<{
    date: string;
    posts: number;
    n: number;
    engagement: number;
  }>;
  by_hour: Array<{
    hour: number;
    posts: number;
    n: number;
    eng_median: number | null;
  }>;
  by_weekday: Array<{
    weekday: number;
    label: string;
    posts: number;
    n: number;
    eng_median: number | null;
  }>;
  by_content_type: Array<{
    content_type: string;
    posts: number;
    n: number;
    eng_median: number | null;
  }>;
  top_posts: Array<{
    job_id: number;
    content_type: string;
    scheduled_for: string;
    title: string;
    likes: number;
    comments: number;
    shares: number;
    engagement: number;
  }>;
  trend: AnalyticsTrend;
}

// ── Strategy (Hermes) ────────────────────────────────────────────────────────
export interface ApiToken {
  id: number;
  name: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  token?: string; // only returned once on creation
}

export interface StrategyRecommendation {
  id: number;
  fanpage_id: number;
  fanpage_name: string;
  kind: "sleep_window" | "daily_cap" | "best_hours" | "content_mix" | "topic" | "other" | string;
  title: string;
  proposal: any;
  rationale: string;
  evidence: any | null;
  status: "proposed" | "approved" | "applied" | "rejected" | "failed" | "superseded" | string;
  source: string;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  applied_at: string | null;
  apply_error: string | null;
  previous_values: any | null;
  current_values: any | null;
}

export interface FanpageContentMemory {
  fanpage_id: number;
  auto: any | null;
  notes: any | null;
  auto_updated_at: string | null;
  notes_updated_at: string | null;
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

// ── Social Media & Content Projects (A1a/U0) ──────────────────────────────────
export type PlatformKind = "facebook_profile" | "facebook_page";
export type SocialDestinationMode = "phone" | "repliz" | "mixed";
export type PhoneDeviceStatus = "available" | "in_use" | "maintenance";

export interface ContentProject {
  id: number;
  name: string;
  repliz_account_id: string | null;
  is_connected: boolean;
  is_active: boolean;
  timezone: string;
  target_country: string | null;
  destination_count: number;
  created_at: string;
  updated_at: string;
}

export interface ContentProjectCreate {
  name: string;
  timezone?: string;
}

export interface SocialDestination {
  id: number;
  content_project_id: number;
  kind: "facebook_profile" | "facebook_page";
  name: string;
  facebook_actor_id: string | null;
  parent_profile_id: number | null;
  repliz_account_id: string | null;
  mode: SocialDestinationMode;
  timezone: string;
  daily_target: number;
  phone_daily_target: number;
  repliz_daily_target: number;
  min_gap_minutes: number;
  supported_media: string[];
  is_active: boolean;
  identity_verified_at: string | null;
  mode_shares?: Record<string, number> | null;
  borrow_hours_before_sleep?: number;
  news_stale_hours?: number;
  created_at: string;
  updated_at: string;
}

export interface DestinationDayPlan {
  destination_id: number;
  name: string;
  timezone: string;
  local_now: string;
  local_date: string;
  daily_target: number;
  quotas: Record<string, number>;
  used: Record<string, number>;
  remaining: Record<string, number>;
  remaining_total: number;
  upcoming_slots: string[];
  content_project_id?: number | null;
}

export interface SocialDestinationCreate {
  content_project_id: number;
  kind: string;
  name: string;
  facebook_actor_id?: string | null;
  parent_profile_id?: number | null;
  repliz_account_id?: string | null;
  mode?: SocialDestinationMode;
  timezone?: string;
  daily_target?: number;
  phone_daily_target?: number;
  repliz_daily_target?: number;
  min_gap_minutes?: number;
  supported_media?: string[];
  is_active?: boolean;
}

export interface SocialDestinationUpdate {
  content_project_id?: number;
  kind?: string;
  name?: string;
  facebook_actor_id?: string | null;
  parent_profile_id?: number | null;
  repliz_account_id?: string | null;
  mode?: SocialDestinationMode;
  timezone?: string;
  daily_target?: number;
  phone_daily_target?: number;
  repliz_daily_target?: number;
  min_gap_minutes?: number;
  supported_media?: string[];
  is_active?: boolean;
  mode_shares?: Record<string, number> | null;
  borrow_hours_before_sleep?: number;
  news_stale_hours?: number;
}

export interface SchedulePreviewSlot {
  slot_utc: string;
  slot_local: string;
}

export interface SchedulePreviewResponse {
  slots_utc: string[];
  slots_local: string[];
  slots: SchedulePreviewSlot[];
  timezone: string;
  target_country: string | null;
  daily_limit: number;
  min_gap_minutes: number;
  sleep_start: number | null;
  sleep_end: number | null;
  basis: "local_time_estimate";
  note: string;
}

export interface PhoneDevice {
  id: number;
  name: string;
  tailscale_node_id: string | null;
  tailscale_ip: string | null;
  adb_serial: string | null;
  expected_profile_actor_id: string | null;
  expected_profile_name: string | null;
  screen_width: number | null;
  screen_height: number | null;
  screen_density: number | null;
  screen_rotation: number | null;
  facebook_app_version: string | null;
  mobilerun_portal_version: string | null;
  app_card_version: string | null;
  calibration_verified_at: string | null;
  status: PhoneDeviceStatus;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PhoneDeviceCreate {
  name: string;
  tailscale_node_id?: string | null;
  tailscale_ip?: string | null;
  adb_serial?: string | null;
  expected_profile_actor_id?: string | null;
  expected_profile_name?: string | null;
  screen_width?: number | null;
  screen_height?: number | null;
  screen_density?: number | null;
  screen_rotation?: number | null;
  facebook_app_version?: string | null;
  mobilerun_portal_version?: string | null;
  app_card_version?: string | null;
  status?: PhoneDeviceStatus;
}

export interface PhoneDeviceUpdate {
  name?: string;
  tailscale_node_id?: string | null;
  tailscale_ip?: string | null;
  adb_serial?: string | null;
  expected_profile_actor_id?: string | null;
  expected_profile_name?: string | null;
  screen_width?: number | null;
  screen_height?: number | null;
  screen_density?: number | null;
  screen_rotation?: number | null;
  facebook_app_version?: string | null;
  mobilerun_portal_version?: string | null;
  app_card_version?: string | null;
  status?: PhoneDeviceStatus;
}

export interface DeviceDryRunRequest {
  destination_id?: number | null;
}

export interface DeviceDryRunResult {
  status: "success" | "failed" | string;
  device_id: number;
  calibration_verified_at: string | null;
  assignment_verified: boolean;
  result: Record<string, unknown>;
}

export type AdbState =
  | "device"
  | "offline"
  | "unauthorized"
  | "not_connected"
  | "unavailable";

export interface DiscoveredPhoneDevice {
  name: string;
  tailscale_ip: string;
  tailscale_node_id: string | null;
  online: boolean;
  adb_serial: string;
  adb_state: AdbState;
  runner_allowed: boolean;
}

export interface TailscaleConnectionInfo {
  backend_state: "Running";
  tailnet_name: string | null;
  account_name: string | null;
}

export interface PhoneDeviceDiscoveryResponse {
  devices: DiscoveredPhoneDevice[];
  warnings: ("adb_unavailable" | string)[];
  connection?: TailscaleConnectionInfo | null;
}

export interface DestinationDeviceAssignment {
  id: number;
  destination_id: number;
  device_id: number;
  priority: number;
  is_active: boolean;
  verified_profile_actor_id: string | null;
  verified_target_actor_id: string | null;
  verified_at: string | null;
  created_at: string;
}

export interface DestinationDeviceAssignmentCreate {
  destination_id: number;
  device_id: number;
  priority?: number;
  is_active?: boolean;
  verified_profile_actor_id?: string | null;
  verified_target_actor_id?: string | null;
}

// ── Social Media Activity (R1/U1) ─────────────────────────────────────────────
export interface DeliveryEvent {
  id: number;
  delivery_id: number;
  event_type: string;
  details: Record<string, any>;
  created_at: string;
}

export interface DeliveryActivity {
  id: number;
  publish_job_id: number;
  destination_id: number;
  destination_name: string | null;
  destination_kind: string | null;
  content_project_id: number | null;
  device_id: number | null;
  device_name: string | null;
  state: string;
  intended_method: string | null;
  actual_method: string | null;
  scheduled_for: string | null;
  claimed_at: string | null;
  attempt_count: number;
  caption_snapshot: string | null;
  media_kind: string | null;
  title_snapshot: string | null;
  repliz_schedule_id: string | null;
  facebook_post_id: string | null;
  facebook_permalink: string | null;
  uncertain_since: string | null;
  last_error: string | null;
  published_at: string | null;
  media_urls?: string[];
  thumbnail_url?: string | null;
  created_at: string;
  updated_at: string;
  events: DeliveryEvent[];
}

export interface DeliveryActivityQueryParams {
  content_project_id?: number;
  destination_id?: number;
  device_id?: number;
  state?: string;
  limit?: number;
  offset?: number;
}
