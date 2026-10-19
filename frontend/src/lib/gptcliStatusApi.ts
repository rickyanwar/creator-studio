import { api } from "@/lib/api";

export interface GptcliStatus {
  reachable: boolean;
  ok: boolean;
  sleeping: boolean;
  queue_len: number;
  running: boolean;
  running_for_s: number | null;
  today_count: number | null;
  daily_cap: number | null;
  quota_until: string | null;
  logged_in: boolean | null;
  heartbeat_age_s: number | null;
}

export const getGptcliStatus = () =>
  api.get<GptcliStatus>("/settings/gptcli/status");
