import { api } from "./api";

export interface DeliveryCopy {
  id: number;
  publish_job_id: number;
  destination_id: number;
  destination_name: string | null;
  destination_kind: string | null;
  platform: string | null;
  kind: string | null;
  method: string | null;
  intended_method: string | null;
  actual_method: string | null;
  state: string;
  repliz_schedule_id: string | null;
  facebook_post_id: string | null;
  facebook_permalink: string | null;
  scheduled_for: string | null;
  published_at: string | null;
  last_error: string | null;
  uncertain_since: string | null;
  device_id: number | null;
  device_name: string | null;
  attempt_count: number;
}

export const getDeliveriesByJobIds = (
  jobIds: number[]
): Promise<DeliveryCopy[]> => {
  if (jobIds.length === 0) return Promise.resolve([]);
  return api
    .get<DeliveryCopy[]>("/social-media/deliveries", {
      params: { publish_job_ids: jobIds.join(",") },
    })
    .then((r) => r.data);
};

export const markDeliveryPosted = (id: number): Promise<DeliveryCopy> =>
  api
    .post<DeliveryCopy>(`/social-media/deliveries/${id}/mark-posted`)
    .then((r) => r.data);

export const markDeliveryNotPosted = (id: number): Promise<DeliveryCopy> =>
  api
    .post<DeliveryCopy>(`/social-media/deliveries/${id}/mark-not-posted`)
    .then((r) => r.data);

export const retryDelivery = (id: number): Promise<DeliveryCopy> =>
  api
    .post<DeliveryCopy>(`/social-media/deliveries/${id}/retry`)
    .then((r) => r.data);
