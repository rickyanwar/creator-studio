import { api } from "@/lib/api";

export interface LogEntry {
  id: string;
  kind: string;
  entry_kind: string;
  entry_id: number;
  category: "publish" | "ai" | "access" | string;
  type: string;
  severity: "error" | "warning" | "info";
  title: string;
  message: string;
  account: string;
  occurred_at: string;
  link?: string | null;
  handled: boolean;
}

export interface AIStats {
  total: number;
  success: number;
  recovered: number;
  failed: number;
  success_rate: number | null;
}

export interface LogsResponse {
  logs: LogEntry[];
  total: number;
  error_count: number;
  warning_count: number;
  ai_stats: AIStats;
}

export interface GetLogsParams {
  category?: string;
  days?: number;
  fanpage_id?: number;
}

export const getLogs = (params?: GetLogsParams) =>
  api.get<LogsResponse>("/logs", { params });

export const markLogHandled = (entry_kind: string, entry_id: number) =>
  api.post<{ ok: boolean; entry_kind: string; entry_id: number; handled: boolean }>(
    "/logs/handled",
    { entry_kind, entry_id }
  );

export const unmarkLogHandled = (entry_kind: string, entry_id: number) =>
  api.delete<{ ok: boolean; entry_kind: string; entry_id: number; handled: boolean }>(
    `/logs/handled/${entry_kind}/${entry_id}`
  );
