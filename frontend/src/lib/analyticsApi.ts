import { api } from "./api";
import type { AnalyticsMetricsStatus, AnalyticsTrend } from "./types";

export interface AnalyticsHourItem {
  hour: number;
  posts: number;
  n: number;
  eng_median: number | null;
}

export interface AnalyticsWeekdayItem {
  weekday: number;
  label: string;
  posts: number;
  n: number;
  eng_median: number | null;
}

export interface FanpageOverviewItem {
  fanpage_id: number;
  name: string;
  timezone: string;
  is_active: boolean;
  posts: number;
  posts_with_metrics: number;
  eng_final_median: number | null;
  trend: AnalyticsTrend;
  best_hour_local: number | null;
}

export interface AnalyticsOverview {
  days: number;
  metrics: AnalyticsMetricsStatus;
  fanpages: FanpageOverviewItem[];
  by_hour: AnalyticsHourItem[];
  by_weekday: AnalyticsWeekdayItem[];
}

export const getAnalyticsOverview = (days: number) =>
  api.get<AnalyticsOverview>("/analytics/overview", { params: { days } });
