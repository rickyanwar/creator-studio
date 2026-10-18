import { api } from "./api";

export interface SaveDraftResponse {
  status: string;
  saved_at?: string | null;
}

export const saveDesignDraft = (
  jobId: number,
  draft: Record<string, unknown> | object
): Promise<SaveDraftResponse> =>
  api.put(`/publish-jobs/${jobId}/design-draft`, { draft }).then((r) => r.data);

export const deleteDesignDraft = (jobId: number): Promise<{ status: string }> =>
  api.delete(`/publish-jobs/${jobId}/design-draft`).then((r) => r.data);
