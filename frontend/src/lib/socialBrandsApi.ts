import { api } from "./api";

export interface ContentModeOut {
  key: string;
  label: string;
  enabled: boolean;
  daily_count: number | null;
  media: string;
}

export interface ChannelDeviceOut {
  device_id: number;
  name: string;
  status: string;
  priority: number;
  last_seen_at: string | null;
}

export interface TodayCountsOut {
  published: number;
  scheduled: number;
  failed: number;
  needs_attention: number;
}

export interface AttentionOut {
  level: "error" | "warn" | "info" | string;
  message: string;
  channel_id: number | null;
}

export interface ChannelSummaryOut {
  id: number;
  content_project_id: number;
  kind: "facebook_profile" | "facebook_page" | string;
  name: string;
  facebook_actor_id: string | null;
  parent_profile_id: number | null;
  repliz_account_id: string | null;
  mode: "phone" | "repliz" | "mixed" | string;
  timezone: string;
  daily_target: number;
  phone_daily_target: number;
  repliz_daily_target: number;
  min_gap_minutes: number;
  supported_media: string[];
  is_active: boolean;
  identity_verified_at: string | null;
  devices: ChannelDeviceOut[];
  today: TodayCountsOut;
  next_post_at: string | null;
  attention: AttentionOut[];
}

export interface BrandSummaryOut {
  id: number;
  name: string;
  is_active: boolean;
  timezone: string;
  target_country: string | null;
  publish_mode: string | null;
  publish_daily_limit: number;
  publish_sleep_start_hour: number | null;
  publish_sleep_end_hour: number | null;
  template_style: string | null;
  niche: string | null;
  gptcli_enabled_for_brand: boolean;
  gptcli_global_enabled: boolean;
  content: ContentModeOut[];
  channels: ChannelSummaryOut[];
  today: TodayCountsOut;
  next_post_at: string | null;
  attention: AttentionOut[];
}

export interface AIImagesTodayOut {
  chatgpt: number;
  codex: number;
  codex_fallback: number;
  template: number;
}

export interface BrandDetailOut extends BrandSummaryOut {
  ai_images_today: AIImagesTodayOut;
  day_start_wib: string;
}

export interface BrandGptcliIn {
  enabled: boolean;
}

export interface BrandGptcliOut {
  brand_id: number;
  enabled_for_brand: boolean;
  gptcli_fanpage_ids: number[];
  gptcli_global_enabled: boolean;
}

export interface EstimatedSlotOut {
  index: number;
  at: string;
  local_time: string;
  wib_time: string;
  method: "phone" | "repliz" | null | string;
  status: string;
  delivery_id: number | null;
}

export interface EstimatedTimesOut {
  destination_id: number;
  timezone: string;
  date: string;
  daily_target: number;
  mode: string;
  phone_daily_target: number;
  repliz_daily_target: number;
  slots: EstimatedSlotOut[];
  note: string | null;
}

export interface RecentDeliveryOut {
  id: number;
  state: string;
  method: string | null;
  scheduled_for: string | null;
  published_at: string | null;
  facebook_permalink: string | null;
  last_error: string | null;
  publish_job_id: number;
  content_type: string | null;
  title: string | null;
  design_engine: string | null;
  design_engine_note: string | null;
}

export interface ReplizAccountOut {
  id: string;
  name: string;
  username: string | null;
  picture: string | null;
  type: string;
  used_by_destination_id: number | null;
  used_by_destination_name: string | null;
}

export interface ReplizAccountsOut {
  configured: boolean;
  error: string | null;
  accounts: ReplizAccountOut[];
}

// ── API Functions ─────────────────────────────────────────────────────────────

export const listBrands = (): Promise<BrandSummaryOut[]> =>
  api.get<BrandSummaryOut[]>("/social-media/brands").then((r) => r.data);

export const getBrand = (id: number): Promise<BrandDetailOut> =>
  api.get<BrandDetailOut>(`/social-media/brands/${id}`).then((r) => r.data);

export const setBrandGptcli = (id: number, enabled: boolean): Promise<BrandGptcliOut> =>
  api
    .put<BrandGptcliOut>(`/social-media/brands/${id}/gptcli`, { enabled })
    .then((r) => r.data);

export const getEstimatedTimes = (destinationId: number): Promise<EstimatedTimesOut> =>
  api
    .get<EstimatedTimesOut>(`/social-media/destinations/${destinationId}/estimated-times`)
    .then((r) => r.data);

export const getRecentDeliveries = (
  destinationId: number,
  limit: number = 20
): Promise<RecentDeliveryOut[]> =>
  api
    .get<RecentDeliveryOut[]>(`/social-media/destinations/${destinationId}/recent`, {
      params: { limit },
    })
    .then((r) => r.data);

export const listReplizAccounts = (): Promise<ReplizAccountsOut> =>
  api.get<ReplizAccountsOut>("/social-media/repliz-accounts").then((r) => r.data);
