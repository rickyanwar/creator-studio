import { api } from "./api";
import type { DiscussionContentIdeaRef } from "./types";

export interface DiscussionDraftRequest {
  count?: number;
  topic_hint?: string;
}

export const draftDiscussionIdeas = (
  fanpageId: number,
  body: DiscussionDraftRequest = {}
) =>
  api.post<DiscussionContentIdeaRef[]>(
    `/fanpages/${fanpageId}/discussion-ideas/draft`,
    body
  );
