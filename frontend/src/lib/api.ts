import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

// Attach JWT token from localStorage on every request
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Redirect to /login on 401
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("token");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// ── Auth ────────────────────────────────────────────────────────────────────
export const login = (username: string, password: string) =>
  api.post<{ access_token: string }>("/auth/login", { username, password });

// ── Dashboard ────────────────────────────────────────────────────────────────
export const getDashboardStats = () => api.get("/dashboard/stats");
export const getCrawlerHealth = () => api.get("/dashboard/health");

// ── Fanpages ─────────────────────────────────────────────────────────────────
export const listFanpages = () => api.get("/fanpages");
export const getFanpage = (id: number) => api.get(`/fanpages/${id}`);
export const updateFanpage = (id: number, data: Record<string, unknown>) =>
  api.put(`/fanpages/${id}`, data);
export const addIGSource = (fanpageId: number, igUsername: string) =>
  api.post(`/fanpages/${fanpageId}/sources`, { ig_username: igUsername });
export const removeIGSource = (fanpageId: number, igSourceId: number) =>
  api.delete(`/fanpages/${fanpageId}/sources/${igSourceId}`);
export const removeIGSourceByUsername = (fanpageId: number, username: string) =>
  api.delete(`/fanpages/${fanpageId}/sources/by-username/${encodeURIComponent(username)}`);
export const previewCaption = (
  fanpageId: number,
  sourceUsername: string,
  originalCaption: string,
  provider?: string
) =>
  api.post(`/fanpages/${fanpageId}/preview-caption`, {
    source_username: sourceUsername,
    original_caption: originalCaption,
    provider,
  });

// ── Burners ──────────────────────────────────────────────────────────────────
export const listBurners = () => api.get("/burners");
export const createBurner = (data: { ig_username: string; password: string; proxy_url?: string }) =>
  api.post("/burners", data);
export const updateBurner = (id: number, data: Record<string, unknown>) =>
  api.put(`/burners/${id}`, data);
export const deleteBurner = (id: number) => api.delete(`/burners/${id}`);
export const submitOTP = (id: number, otpCode: string) =>
  api.post(`/burners/${id}/challenge`, { otp_code: otpCode });
export const testBurnerSession = (id: number) => api.post(`/burners/${id}/test-session`);
export const importBurnerSession = (id: number, sessionJson: Record<string, unknown>) =>
  api.post(`/burners/${id}/import-session`, sessionJson);
export const postStoryNow = (id: number) => api.post(`/burners/${id}/post-story-now`);
export const postCommentNow = (id: number) => api.post(`/burners/${id}/post-comment-now`);
export const resetBurner = (id: number) => api.post(`/burners/${id}/reset`);

// ── Publish Jobs ─────────────────────────────────────────────────────────────
export const listJobs = (params?: {
  status?: string;
  fanpage_id?: number;
  limit?: number;
  offset?: number;
}) => api.get("/publish-jobs", { params });
export const updateJobCaption = (id: number, caption: string) =>
  api.put(`/publish-jobs/${id}/caption`, { caption });
export const regenerateCaption = (id: number, provider?: string) =>
  api.post(`/publish-jobs/${id}/regenerate-caption`, { provider });
export const publishJob = (id: number) => api.post(`/publish-jobs/${id}/publish`);
export const skipJob = (id: number) => api.post(`/publish-jobs/${id}/skip`);

// ── IG Sources ───────────────────────────────────────────────────────────────
export const listIGSources = (orphanOnly?: boolean) =>
  api.get("/ig-sources", { params: { orphan_only: orphanOnly } });
export const assignBurnerToSource = (sourceId: number, burnerId: number | null) =>
  api.patch(`/ig-sources/${sourceId}/assign-burner`, { burner_id: burnerId });
export const updateIGSource = (
  sourceId: number,
  data: {
    ig_username?: string;
    is_active?: boolean;
    album_image_indices?: number[];
    image_edit_enabled?: boolean;
    image_edit_custom_prompt?: string;
  }
) => api.patch(`/ig-sources/${sourceId}`, data);
export const deleteIGSource = (sourceId: number) =>
  api.delete(`/ig-sources/${sourceId}`);
export const autoAssignBurners = () =>
  api.post("/ig-sources/auto-assign-burners");

// ── News Sources ─────────────────────────────────────────────────────────────
export const listNewsSources = () => api.get("/news-sources");
export const createNewsSource = (data: Record<string, unknown>) =>
  api.post("/news-sources", data);
export const updateNewsSource = (id: number, data: Record<string, unknown>) =>
  api.put(`/news-sources/${id}`, data);
export const deleteNewsSource = (id: number) => api.delete(`/news-sources/${id}`);
export const scrapeNewsSourceNow = (id: number) =>
  api.post(`/news-sources/${id}/scrape-now`);
export const testNewsListSelector = (data: {
  category_url: string;
  article_list_selector: string;
  article_link_attribute?: string;
  render_mode?: string;
}) => api.post("/news-sources/test-list-selector", data);
export const testNewsSelectors = (data: {
  article_url: string;
  title_selector: string;
  content_selector: string;
  image_selector?: string;
  date_selector?: string;
  render_mode?: string;
}) => api.post("/news-sources/test-selectors", data);
export const listNewsArticles = (sourceId: number, limit = 20) =>
  api.get(`/news-sources/${sourceId}/articles`, { params: { limit } });

// ── Gallery ──────────────────────────────────────────────────────────────────
export const listGalleryKeywords = () => api.get("/gallery/keywords");
export const createGalleryKeyword = (data: Record<string, unknown>) =>
  api.post("/gallery/keywords", data);
export const updateGalleryKeyword = (id: number, data: Record<string, unknown>) =>
  api.put(`/gallery/keywords/${id}`, data);
export const deleteGalleryKeyword = (id: number) =>
  api.delete(`/gallery/keywords/${id}`);
export const downloadGalleryKeywordNow = (id: number) =>
  api.post(`/gallery/keywords/${id}/download-now`);
export const listGalleryImages = (params: {
  keyword?: string;
  only_unused?: boolean;
  limit?: number;
  offset?: number;
}) => api.get("/gallery/images", { params });
export const uploadGalleryImage = (file: File, keyword: string) => {
  const form = new FormData();
  form.append("file", file);
  form.append("keyword", keyword);
  return api.post("/gallery/images/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};
export const deleteGalleryImage = (id: number) =>
  api.delete(`/gallery/images/${id}`);

// ── Fanpage Mode 2 (news content) ────────────────────────────────────────────
export const addFanpageNewsSource = (fanpageId: number, newsSourceId: number) =>
  api.post(`/fanpages/${fanpageId}/news-sources`, { news_source_id: newsSourceId });
export const removeFanpageNewsSource = (fanpageId: number, newsSourceId: number) =>
  api.delete(`/fanpages/${fanpageId}/news-sources/${newsSourceId}`);
export const previewNewsCopy = (
  fanpageId: number,
  data: { title: string; content: string; source_name?: string; provider?: string }
) => api.post(`/fanpages/${fanpageId}/preview-news-copy`, data);

// ── Design Templates ─────────────────────────────────────────────────────────
export const listTemplates = (fanpageId?: number) =>
  api.get("/templates", { params: fanpageId ? { fanpage_id: fanpageId } : {} });
export const getTemplate = (id: number) => api.get(`/templates/${id}`);
export const createTemplate = (data: Record<string, unknown>) =>
  api.post("/templates", data);
export const updateTemplate = (id: number, data: Record<string, unknown>) =>
  api.put(`/templates/${id}`, data);
export const deleteTemplate = (id: number) => api.delete(`/templates/${id}`);

// ── Job design (news content) ────────────────────────────────────────────────
export const getDesignPayload = (jobId: number) =>
  api.get(`/publish-jobs/${jobId}/design-payload`);
export const uploadDesignImage = (jobId: number, blob: Blob) => {
  const form = new FormData();
  form.append("file", blob, "design.png");
  return api.post(`/publish-jobs/${jobId}/design-image`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};
export const renderJobNow = (jobId: number) =>
  api.post(`/publish-jobs/${jobId}/render-now`);
export const proxyImageUrl = async (url: string): Promise<string> => {
  const r = await api.get("/gallery/proxy", { params: { url }, responseType: "blob" });
  return URL.createObjectURL(r.data as Blob);
};

// ── Settings ─────────────────────────────────────────────────────────────────
export const getSettings = () => api.get("/settings");
export const updateSettings = (data: Record<string, unknown>) => api.put("/settings", data);
export const testReplizCredentials = (accessKey: string, secretKey: string) =>
  api.post("/settings/repliz/test", { access_key: accessKey, secret_key: secretKey });

// ── Jobs (manual triggers) ───────────────────────────────────────────────────
export const triggerCrawl = () => api.post("/jobs/crawl-now");
export const triggerFanpageSync = () => api.post("/jobs/sync-fanpages");
export const restartBeat = () => api.post("/jobs/restart-beat");

// ── Logs ──────────────────────────────────────────────────────────────────────
export const getLogs = (params?: { category?: string; days?: number }) =>
  api.get<{ logs: ActivityLog[]; total: number; error_count: number; warning_count: number }>("/logs", { params });

export interface ActivityLog {
  id: string;
  category: "burner" | "publish";
  type: string;
  severity: "error" | "warning";
  title: string;
  message: string;
  account: string;
  occurred_at: string;
  link: string;
}

// ── Notifications ─────────────────────────────────────────────────────────────
export const getNotifications = () => api.get<{ notifications: Notification[]; unread: number }>("/notifications");

export interface Notification {
  id: string;
  type: "error" | "warning" | "info";
  title: string;
  message: string;
  link: string;
  created_at: string;
}
