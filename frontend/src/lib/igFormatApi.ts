import { api } from "./api";

export type IGFormat = "follow" | "repost" | "template" | "chatgpt";

export interface IGFormatUpdateResponse {
  ok: boolean;
  ig_recreate_enabled: boolean | null;
  visual_engine_override: string | null;
  format: IGFormat;
  chatgpt_edit_prompt: string | null;
}

export const setSourceFormat = (
  fanpageId: number,
  igSourceId: number,
  format: IGFormat
) =>
  api.put<IGFormatUpdateResponse>(
    `/fanpages/${fanpageId}/sources/${igSourceId}/recreate`,
    { format }
  );

export const setSourceEditPrompt = (
  fanpageId: number,
  igSourceId: number,
  prompt: string | null
) =>
  api.put<IGFormatUpdateResponse>(
    `/fanpages/${fanpageId}/sources/${igSourceId}/recreate`,
    { chatgpt_edit_prompt: prompt }
  );
