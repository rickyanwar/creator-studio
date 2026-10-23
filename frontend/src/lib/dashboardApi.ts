import { api } from "./api";
import type { DashboardStats } from "./types";

export interface BrandToday {
  fanpage_id: number;
  name: string;
  timezone: string;
  published_today: number;
  waiting_review: number;
  failed_today: number;
  active_channels: number;
  daily_target: number;
  modes?: string[];
  initials?: string;
  next_post?: string | null;
}

export interface LatestPost {
  id: number;
  fanpage_id: number;
  fanpage_name: string;
  content_type: string;
  title: string;
  published_at: string | null;
  go_live_at?: string | null;   // when the post really goes live (delivery slot)
  thumbnail_url: string | null;
}

export interface ExtendedDashboardStats extends DashboardStats {
  brands_today?: BrandToday[];
  latest_posts?: LatestPost[];
}

export const getDashboardStats = () => api.get<ExtendedDashboardStats>("/dashboard/stats");
