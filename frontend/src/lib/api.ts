import axios from "axios";
import type { ScraperHealth } from "./types";

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
export const getScraperHealth = () => api.get<ScraperHealth>("/scraper-health");

// ── Fanpages ─────────────────────────────────────────────────────────────────
export const listFanpages = () => api.get("/fanpages");
export const getFanpage = (id: number) => api.get(`/fanpages/${id}`);
export const updateFanpage = (id: number, data: Record<string, unknown>) =>
  api.put(`/fanpages/${id}`, data);
export const previewAiVideoOverlay = (fanpageId: number, data: Record<string, unknown>) =>
  api.post(`/ai-video/fanpages/${fanpageId}/overlay-preview`, data, { responseType: "blob" });
export const addIGSource = (fanpageId: number, igUsername: string) =>
  api.post(`/fanpages/${fanpageId}/sources`, { ig_username: igUsername });
export const removeIGSource = (fanpageId: number, igSourceId: number) =>
  api.delete(`/fanpages/${fanpageId}/sources/${igSourceId}`);
export const removeIGSourceByUsername = (fanpageId: number, username: string) =>
  api.delete(`/fanpages/${fanpageId}/sources/by-username/${encodeURIComponent(username)}`);
export const setSourceRecreateOverride = (fanpageId: number, igSourceId: number, igRecreateEnabled: boolean | null) =>
  api.put(`/fanpages/${fanpageId}/sources/${igSourceId}/recreate`, { ig_recreate_enabled: igRecreateEnabled });
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
export const getJobStats = (params?: { fanpage_id?: number }) =>
  api.get<{
    published_today: number;
    published_this_month: number;
    published_total: number;
    failed_total: number;
  }>("/publish-jobs/stats", { params });
export const updateJobCaption = (id: number, caption: string) =>
  api.put(`/publish-jobs/${id}/caption`, { caption });
export const regenerateCaption = (id: number, provider?: string) =>
  api.post(`/publish-jobs/${id}/regenerate-caption`, { provider });
export const publishJob = (id: number) => api.post(`/publish-jobs/${id}/publish`);
export const skipJob = (id: number) => api.post(`/publish-jobs/${id}/skip`);
export const deletePublishJob = (id: number) =>
  api.delete(`/publish-jobs/${id}`) as Promise<{ data: { ok: boolean; repliz_deleted: boolean | null; repliz_error: string | null } }>;
export const reeditJob = (id: number) => api.post(`/publish-jobs/${id}/reedit`);

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
    scraper_backend?: "auto" | "instagrapi" | "viewer";
    caption_tone?: string;
    caption_language?: string;
    caption_max_length?: number | null;
    caption_hashtag_count?: number | null;
    caption_cta_text?: string;
    caption_custom_prompt?: string;
  }
) => api.patch(`/ig-sources/${sourceId}`, data);
export const deleteIGSource = (sourceId: number) =>
  api.delete(`/ig-sources/${sourceId}`);
export const uploadWatermarkImage = (fanpageId: number, file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return api.post(`/fanpages/${fanpageId}/watermark-image`, fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};
export const deleteWatermarkImage = (fanpageId: number) =>
  api.delete(`/fanpages/${fanpageId}/watermark-image`);
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
export const testNewsRss = (data: { category_url: string }) =>
  api.post("/news-sources/test-rss", data);
export const listNewsArticles = (sourceId: number, limit = 20) =>
  api.get(`/news-sources/${sourceId}/articles`, { params: { limit } });

// ── Gallery ──────────────────────────────────────────────────────────────────
export const listGalleryKeywords = (niche?: string) =>
  api.get("/gallery/keywords", { params: niche ? { niche } : {} });
export const listGalleryNiches = () => api.get("/gallery/niches");
export const createGalleryKeyword = (data: Record<string, unknown>) =>
  api.post("/gallery/keywords", data);
export const updateGalleryKeyword = (id: number, data: Record<string, unknown>) =>
  api.put(`/gallery/keywords/${id}`, data);
export const deleteGalleryKeyword = (id: number) =>
  api.delete(`/gallery/keywords/${id}`);
export const downloadGalleryKeywordNow = (id: number) =>
  api.post(`/gallery/keywords/${id}/download-now`);
export const startAIFilterScan = (criteria: string, keyword?: string) =>
  api.post("/gallery/ai-filter/scan", { criteria, keyword: keyword || undefined });
export const getAIFilterScanStatus = (taskId: string) =>
  api.get(`/gallery/ai-filter/scan/${taskId}`);
export const bulkDeleteGalleryImages = (imageIds: number[]) =>
  api.post("/gallery/images/bulk-delete", { image_ids: imageIds });
export const bulkImportGalleryKeywords = (items: Record<string, unknown>[]) =>
  api.post("/gallery/keywords/bulk-import", items);
export const listGalleryImages = (params: {
  keyword?: string;
  niche?: string;
  search?: string;
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
export const importGalleryLinks = (links: string, keyword?: string, hints?: Record<string, unknown>) =>
  api.post<{ task_id: string }>("/gallery/images/import-links", { links, keyword: keyword || undefined, ...(hints || {}) });
export const getGalleryLinkImportStatus = (taskId: string) =>
  api.get(`/gallery/images/import-links/${taskId}`);
export const deleteGalleryImage = (id: number) =>
  api.delete(`/gallery/images/${id}`);
export const updateGalleryImageKeywords = (id: number, extra_keywords: string[]) =>
  api.patch(`/gallery/images/${id}/keywords`, { extra_keywords });
export const updateGalleryImageLabels = (id: number, data: Record<string, unknown>, relabel = false) =>
  api.patch(`/gallery/images/${id}/labels`, data, { params: relabel ? { relabel: true } : undefined });

// ── Fanpage Mode 2 (news content) ────────────────────────────────────────────
export const addFanpageNewsSource = (fanpageId: number, newsSourceId: number) =>
  api.post(`/fanpages/${fanpageId}/news-sources`, { news_source_id: newsSourceId });
export const removeFanpageNewsSource = (fanpageId: number, newsSourceId: number) =>
  api.delete(`/fanpages/${fanpageId}/news-sources/${newsSourceId}`);
export const previewNewsCopy = (
  fanpageId: number,
  data: { title: string; content: string; source_name?: string; provider?: string }
) => api.post(`/fanpages/${fanpageId}/preview-news-copy`, data);

// ── Fanpage Mode 4 (discussion topics) ───────────────────────────────────────
export const addDiscussionTopic = (
  fanpageId: number,
  data: { seed_text: string; subject_hint?: string }
) => api.post(`/fanpages/${fanpageId}/discussion-topics`, data);
export const updateDiscussionTopic = (
  fanpageId: number,
  topicId: number,
  data: { seed_text?: string; subject_hint?: string | null; is_active?: boolean }
) => api.put(`/fanpages/${fanpageId}/discussion-topics/${topicId}`, data);
export const deleteDiscussionTopic = (fanpageId: number, topicId: number) =>
  api.delete(`/fanpages/${fanpageId}/discussion-topics/${topicId}`);
export const listDiscussionContentIdeas = (
  fanpageId: number,
  params: { status?: string; offset?: number } = {}
) => api.get(`/fanpages/${fanpageId}/discussion-content-ideas`, { params });
export const createDiscussionContentIdea = (
  fanpageId: number,
  data: { seed_text: string; subject_hint?: string; label?: string }
) => api.post(`/fanpages/${fanpageId}/discussion-content-ideas`, data);
export const updateDiscussionContentIdea = (
  fanpageId: number,
  ideaId: number,
  data: { label?: string; question?: string; subject_name?: string; caption?: string }
) => api.put(`/fanpages/${fanpageId}/discussion-content-ideas/${ideaId}`, data);
export const deleteDiscussionContentIdea = (fanpageId: number, ideaId: number) =>
  api.delete(`/fanpages/${fanpageId}/discussion-content-ideas/${ideaId}`);

// ── Fanpage Mode 5 (Pinterest content) ───────────────────────────────────────
export const addPinterestSource = (
  fanpageId: number,
  data: { source_url: string; label?: string }
) => api.post(`/fanpages/${fanpageId}/pinterest-sources`, data);
export const updatePinterestSource = (
  fanpageId: number,
  sourceId: number,
  data: { source_url?: string; label?: string | null; is_active?: boolean }
) => api.put(`/fanpages/${fanpageId}/pinterest-sources/${sourceId}`, data);
export const deletePinterestSource = (fanpageId: number, sourceId: number) =>
  api.delete(`/fanpages/${fanpageId}/pinterest-sources/${sourceId}`);
export const listPinterestContentIdeas = (
  fanpageId: number,
  params: { status?: string; offset?: number } = {}
) => api.get(`/fanpages/${fanpageId}/pinterest-content-ideas`, { params });
export const updatePinterestContentIdea = (
  fanpageId: number,
  ideaId: number,
  data: { title?: string; description?: string }
) => api.put(`/fanpages/${fanpageId}/pinterest-content-ideas/${ideaId}`, data);
export const deletePinterestContentIdea = (fanpageId: number, ideaId: number) =>
  api.delete(`/fanpages/${fanpageId}/pinterest-content-ideas/${ideaId}`);

// ── Fanpage Mode 6 (Facebook photo recreate) ─────────────────────────────────
export const addFacebookPhotoSource = (
  fanpageId: number,
  data: { page_url: string; label?: string }
) => api.post(`/fanpages/${fanpageId}/facebook-photo-sources`, data);
export const updateFacebookPhotoSource = (
  fanpageId: number,
  sourceId: number,
  data: { page_url?: string; label?: string | null; is_active?: boolean }
) => api.put(`/fanpages/${fanpageId}/facebook-photo-sources/${sourceId}`, data);
export const deleteFacebookPhotoSource = (fanpageId: number, sourceId: number) =>
  api.delete(`/fanpages/${fanpageId}/facebook-photo-sources/${sourceId}`);
export const listFacebookPhotoIdeas = (
  fanpageId: number,
  params: { status?: string; offset?: number } = {}
) => api.get(`/fanpages/${fanpageId}/facebook-photo-ideas`, { params });
export const updateFacebookPhotoIdea = (
  fanpageId: number,
  ideaId: number,
  data: { design_title?: string; design_subtitle?: string; design_caption?: string }
) => api.put(`/fanpages/${fanpageId}/facebook-photo-ideas/${ideaId}`, data);
export const deleteFacebookPhotoIdea = (fanpageId: number, ideaId: number) =>
  api.delete(`/fanpages/${fanpageId}/facebook-photo-ideas/${ideaId}`);

// ── Fanpage Mode 7 (YouTube clips) ───────────────────────────────────────────
export const addYtClipSource = (
  fanpageId: number,
  data: { url: string; label?: string; direction?: string }
) => api.post(`/fanpages/${fanpageId}/yt-clip-sources`, data);
export const updateYtClipSource = (
  fanpageId: number,
  sourceId: number,
  data: { label?: string | null; direction?: string | null; is_active?: boolean }
) => api.put(`/fanpages/${fanpageId}/yt-clip-sources/${sourceId}`, data);
export const deleteYtClipSource = (fanpageId: number, sourceId: number) =>
  api.delete(`/fanpages/${fanpageId}/yt-clip-sources/${sourceId}`);
export const checkYtClipSource = (fanpageId: number, sourceId: number) =>
  api.post(`/fanpages/${fanpageId}/yt-clip-sources/${sourceId}/check`);
export const listYtClipIdeas = (
  fanpageId: number,
  params: { status?: string; offset?: number } = {}
) => api.get(`/fanpages/${fanpageId}/yt-clip-ideas`, { params });
export const updateYtClipIdea = (
  fanpageId: number,
  ideaId: number,
  data: { title?: string; hook_text?: string }
) => api.put(`/fanpages/${fanpageId}/yt-clip-ideas/${ideaId}`, data);
export const deleteYtClipIdea = (fanpageId: number, ideaId: number) =>
  api.delete(`/fanpages/${fanpageId}/yt-clip-ideas/${ideaId}`);
export const listYtVideos = (fanpageId: number, params: { offset?: number } = {}) =>
  api.get(`/fanpages/${fanpageId}/yt-videos`, { params });
export const retryYtVideo = (fanpageId: number, rowId: number) =>
  api.post(`/fanpages/${fanpageId}/yt-videos/${rowId}/retry`);

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
export const getAiEngineStatus = () => api.get("/settings/ai-engine-status");
export const updateSettings = (data: Record<string, unknown>) => api.put("/settings", data);
export const testYouTubeAccess = () => api.post("/settings/youtube/test");
export const clearYouTubeBlock = () => api.post("/settings/youtube/clear-block");
export const testReplizCredentials = (accessKey: string, secretKey: string) =>
  api.post("/settings/repliz/test", { access_key: accessKey, secret_key: secretKey });
export const testProxies = (proxies?: string) =>
  api.post("/settings/proxies/test", { proxies });
export const testRelays = (relays?: string) =>
  api.post("/settings/relays/test", { relays });

// ── Jobs (manual triggers) ───────────────────────────────────────────────────
export const triggerCrawl = () => api.post("/jobs/crawl-now");
export const triggerFanpageSync = () => api.post("/jobs/sync-fanpages");
export const restartBeat = () => api.post("/jobs/restart-beat");

// ── Analytics ─────────────────────────────────────────────────────────────────
export const getAnalyticsOverview = (days: number) =>
  api.get(`/analytics/overview`, { params: { days } });
export const getFanpageAnalytics = (id: number, days: number) =>
  api.get(`/analytics/fanpages/${id}`, { params: { days } });

// ── Logs ──────────────────────────────────────────────────────────────────────
export const getLogs = (params?: { category?: string; days?: number }) =>
  api.get<{
    logs: ActivityLog[];
    total: number;
    error_count: number;
    warning_count: number;
    ai_stats: AIStats;
  }>("/logs", { params });

// ── Radar (Feature 3) ────────────────────────────────────────────────────────
export const listRadarAccounts = (niche?: string) =>
  api.get("/radar/accounts", { params: niche ? { niche } : {} });
export const createRadarAccount = (data: { niche: string; ig_username: string }) =>
  api.post("/radar/accounts", data);
export const updateRadarAccount = (id: number, data: { niche?: string; is_active?: boolean }) =>
  api.patch(`/radar/accounts/${id}`, data);
export const deleteRadarAccount = (id: number) =>
  api.delete(`/radar/accounts/${id}`);
export const listRadarNiches = () => api.get("/radar/niches");
export const listRadarStories = (params?: { niche?: string; status?: string; limit?: number }) =>
  api.get("/radar/stories", { params });
export const getShadowReport = (days?: number) =>
  api.get("/radar/shadow-report", { params: days ? { days } : {} });
export const setSourceTrigger = (fanpageId: number, igSourceId: number, trigger: "every_post" | "viral_only") =>
  api.put(`/fanpages/${fanpageId}/sources/${igSourceId}/trigger`, { trigger });
export const previewRadarDecision = (decisionId: number) =>
  api.post(`/radar/decisions/${decisionId}/preview`);
export const regenerateRadarDecision = (decisionId: number) =>
  api.post(`/radar/decisions/${decisionId}/regenerate`);

export interface ActivityLog {
  id: string;
  category: "burner" | "publish" | "ai";
  type: string;
  severity: "error" | "warning";
  title: string;
  message: string;
  account: string;
  occurred_at: string;
  link: string;
}

export interface AIStats {
  total: number;
  success: number;
  recovered: number;
  failed: number;
  success_rate: number | null;
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

// ── Strategy ──────────────────────────────────────────────────────────────────
export const listRecommendations = (params?: { status?: string; fanpage_id?: number; limit?: number }) =>
  api.get("/strategy/recommendations", { params });
export const approveRecommendation = (id: number) =>
  api.post(`/strategy/recommendations/${id}/approve`);
export const rejectRecommendation = (id: number) =>
  api.post(`/strategy/recommendations/${id}/reject`);
export const getContentMemory = (fanpageId: number) =>
  api.get(`/strategy/memory/${fanpageId}`);
export const refreshContentMemory = (fanpageId: number) =>
  api.post(`/strategy/memory/${fanpageId}/refresh`);
export const listApiTokens = () => api.get("/strategy/tokens");
export const createApiToken = (data: { name: string; scopes: string[] }) =>
  api.post("/strategy/tokens", data);
export const revokeApiToken = (id: number) => api.delete(`/strategy/tokens/${id}`);

// ── AI video (Mode 8) ────────────────────────────────────────────────────────
export const listAiVideoTemplates = () => api.get("/ai-video/templates");
export const listAiVideoIdeas = (params?: { status?: string; fanpage_id?: number; limit?: number }) =>
  api.get("/ai-video/ideas", { params });
export const createAiVideoIdea = (data: Record<string, unknown>) => api.post("/ai-video/ideas", data);
export const approveAiVideoIdea = (id: number) => api.post(`/ai-video/ideas/${id}/approve`);
export const rejectAiVideoIdea = (id: number, reason?: string) =>
  api.post(`/ai-video/ideas/${id}/reject`, reason ? { reason } : undefined);
export const listIdeaFeedback = (id: number) => api.get(`/ai-video/ideas/${id}/feedback`);
export const addIdeaFeedback = (id: number, message: string) =>
  api.post(`/ai-video/ideas/${id}/feedback`, { message });
export const getResearchActivity = (limit = 50) => api.get("/settings/research-activity", { params: { limit } });
export const listAiVideoJobs = (params?: { status?: string; fanpage_id?: number; limit?: number }) =>
  api.get("/ai-video/jobs", { params });
export const retryAiVideoJob = (id: number) => api.post(`/ai-video/jobs/${id}/retry`);
export const cancelAiVideoJob = (id: number) => api.post(`/ai-video/jobs/${id}/cancel`);
export const listGflowAccounts = () => api.get("/ai-video/accounts");
export const updateGflowAccount = (id: number, data: { enabled?: boolean; label?: string; terms_accepted?: boolean }) =>
  api.patch(`/ai-video/accounts/${id}`, data);
export const listGflowWorkers = () => api.get("/ai-video/workers");
// F1 Drivers
import { F1Driver } from "./types";

export const listF1Drivers = (season?: number) =>
  api.get<F1Driver[]>("/f1-drivers", { params: { season } });

export const syncF1Drivers = () =>
  api.post<{ inserted: number; updated: number; unchanged: number }>("/f1-drivers/sync");

export const createF1Driver = (data: Record<string, unknown>) =>
  api.post<F1Driver>("/f1-drivers", data);

export const updateF1Driver = (id: number, data: Record<string, unknown>) =>
  api.patch<F1Driver>(`/f1-drivers/${id}`, data);

export const deleteF1Driver = (id: number) =>
  api.delete(`/f1-drivers/${id}`);

export const uploadF1DriverLogo = (id: number, file: File) => {
  const formData = new FormData();
  formData.append("file", file);
  return api.post<F1Driver>(`/f1-drivers/${id}/logo`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
};

// ── Content Projects (A1a/U0) ────────────────────────────────────────────────
import type {
  ContentProject,
  ContentProjectCreate,
  SocialDestination,
  SocialDestinationCreate,
  SocialDestinationUpdate,
  SchedulePreviewResponse,
  PhoneDevice,
  PhoneDeviceCreate,
  PhoneDeviceUpdate,
  PhoneDeviceDiscoveryResponse,
  DeviceDryRunRequest,
  DeviceDryRunResult,
  DestinationDeviceAssignment,
  DestinationDeviceAssignmentCreate,
  DeliveryActivity,
  DeliveryActivityQueryParams,
} from "./types";

export const listContentProjects = () =>
  api.get<ContentProject[]>("/content-projects");

export const getContentProject = (id: number) =>
  api.get<ContentProject>(`/content-projects/${id}`);

export const createContentProject = (data: ContentProjectCreate) =>
  api.post<ContentProject>("/content-projects", data);

// ── Social Media Destinations (A1a/U0) ───────────────────────────────────────
export const listSocialDestinations = (params?: {
  content_project_id?: number;
  kind?: string;
  is_active?: boolean;
}) => api.get<SocialDestination[]>("/social-media/destinations", { params });

export const getSocialDestination = (id: number) =>
  api.get<SocialDestination>(`/social-media/destinations/${id}`);

export const createSocialDestination = (data: SocialDestinationCreate) =>
  api.post<SocialDestination>("/social-media/destinations", data);

export const updateSocialDestination = (id: number, data: SocialDestinationUpdate) =>
  api.patch<SocialDestination>(`/social-media/destinations/${id}`, data);

export const deleteSocialDestination = (id: number) =>
  api.delete<SocialDestination>(`/social-media/destinations/${id}`);

export const getSchedulePreview = (destinationId: number, limit: number = 6) =>
  api.get<SchedulePreviewResponse>(`/social-media/destinations/${destinationId}/schedule-preview`, {
    params: { limit },
  });

// ── Phone Devices (A1a/U0) ───────────────────────────────────────────────────
export const listPhoneDevices = (params?: { status?: string }) =>
  api.get<PhoneDevice[]>("/social-media/devices", { params });

export const getPhoneDevice = (id: number) =>
  api.get<PhoneDevice>(`/social-media/devices/${id}`);

export const createPhoneDevice = (data: PhoneDeviceCreate) =>
  api.post<PhoneDevice>("/social-media/devices", data);

export const updatePhoneDevice = (id: number, data: PhoneDeviceUpdate) =>
  api.patch<PhoneDevice>(`/social-media/devices/${id}`, data);

export const discoverPhoneDevices = () =>
  api.get<PhoneDeviceDiscoveryResponse>("/social-media/devices/discovery");

export const runDeviceDryRun = (id: number, data?: DeviceDryRunRequest) =>
  api.post<DeviceDryRunResult>(`/social-media/devices/${id}/dry-run`, data ?? {});

// ── Device Assignments (A1a/U0) ──────────────────────────────────────────────
export const listDeviceAssignments = (params?: {
  destination_id?: number;
  device_id?: number;
  is_active?: boolean;
}) => api.get<DestinationDeviceAssignment[]>("/social-media/assignments", { params });

export const createDeviceAssignment = (data: DestinationDeviceAssignmentCreate) =>
  api.post<DestinationDeviceAssignment>("/social-media/assignments", data);

export const deleteDeviceAssignment = (destinationId: number, deviceId: number) =>
  api.delete<DestinationDeviceAssignment>(`/social-media/assignments/${destinationId}/${deviceId}`);

// ── Social Media Activity (R1/U1) ─────────────────────────────────────────────
export const listSocialMediaActivity = (params?: DeliveryActivityQueryParams) =>
  api.get<DeliveryActivity[]>("/social-media/activity", { params });
