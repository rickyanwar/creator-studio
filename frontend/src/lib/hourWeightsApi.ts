import { api } from "@/lib/api";

export type PublishHourWeights = Record<string, number>;

export interface UpdateHourWeightsPayload {
  publish_hour_weights: PublishHourWeights | null;
}

export const clearPublishHourWeights = (fanpageId: number) =>
  api.put(`/fanpages/${fanpageId}`, { publish_hour_weights: null });

export const updatePublishHourWeights = (
  fanpageId: number,
  weights: PublishHourWeights | null
) => api.put(`/fanpages/${fanpageId}`, { publish_hour_weights: weights });
