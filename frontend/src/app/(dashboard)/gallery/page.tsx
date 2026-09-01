"use client";

import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import { useRef, useState, useEffect, useCallback } from "react";
import { Icon } from "@iconify/react";
import { formatDistanceToNowStrict, format, differenceInCalendarDays } from "date-fns";
import {
  listGalleryKeywords,
  listGalleryNiches,
  createGalleryKeyword,
  updateGalleryKeyword,
  deleteGalleryKeyword,
  downloadGalleryKeywordNow,
  startAIFilterScan,
  getAIFilterScanStatus,
  bulkDeleteGalleryImages,
  bulkImportGalleryKeywords,
  listGalleryImages,
  uploadGalleryImage,
  deleteGalleryImage,
  updateGalleryImageKeywords,
  getSettings,
  updateSettings,
} from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import PromptDialog from "@/components/ui/PromptDialog";
import Toast, { type ToastData } from "@/components/ui/Toast";

type Keyword = {
  id: number;
  keyword: string;
  niche: string | null;
  is_active: boolean;
  max_images: number;
  max_pages: number;
  min_width: number;
  min_height: number;
  source_engine: string;
  license_filter: string;
  last_downloaded_at: string | null;
  last_download_error: string | null;
  image_count: number;
  next_event_date: string | null;
};

type GalleryImage = {
  id: number;
  keyword: string;
  extra_keywords: string[];
  source_image_url: string;
  public_url: string;
  width: number;
  height: number;
  source_engine: string;
  is_used: boolean;
  downloaded_at: string | null;
};

type FilterCandidate = { id: number; public_url: string; keyword: string; confidence: number };

const emptyForm = {
  keyword: "",
  niche: "",
  is_active: true,
  max_images: 50,
  max_pages: 10,
  min_width: 200,
  min_height: 200,
  source_engine: "9router",
  license_filter: "commercial,modify",
};

export default function GalleryPage() {
  const { data: keywords = [], mutate: mutateKeywords } = useSWR<Keyword[]>(
    "gallery-keywords",
    () => listGalleryKeywords().then((r) => r.data as Keyword[]),
    { refreshInterval: 30000 }
  );
  const { data: appSettings, mutate: mutateSettings } = useSWR(
    "settings",
    () =>
      getSettings().then(
        (r) => r.data as { gallery_scraping_paused: boolean; gallery_ai_filter_last_criteria: string | null }
      ),
  );
  const [pauseToggling, setPauseToggling] = useState(false);
  async function toggleScrapingPause() {
    if (!appSettings) return;
    setPauseToggling(true);
    try {
      await updateSettings({ gallery_scraping_paused: !appSettings.gallery_scraping_paused });
      mutateSettings();
    } finally {
      setPauseToggling(false);
    }
  }
  const { data: niches = [], mutate: mutateNiches } = useSWR<string[]>(
    "gallery-niches",
    () => listGalleryNiches().then((r) => r.data as string[])
  );

  const [filterKeyword, setFilterKeyword] = useState<string>("");
  const [filterNiche, setFilterNiche] = useState<string>("");
  const [imageSearch, setImageSearch] = useState<string>("");
  const [keywordSearch, setKeywordSearch] = useState<string>("");
  const visibleKeywords = keywords
    .filter((k) => (filterNiche ? k.niche === filterNiche : true))
    .filter((k) => (keywordSearch ? k.keyword.toLowerCase().includes(keywordSearch.trim().toLowerCase()) : true));
  const keywordsFiltered = !!filterNiche || !!keywordSearch;

  // Debounce the image free-text search so we don't hit the API on every keystroke
  const [imageSearchDebounced, setImageSearchDebounced] = useState<string>("");
  useEffect(() => {
    const t = setTimeout(() => setImageSearchDebounced(imageSearch.trim()), 350);
    return () => clearTimeout(t);
  }, [imageSearch]);

  // ── Infinite scroll (offset pagination) — handles thousands of images ──
  const PAGE_SIZE = 60;
  type ImagePage = { total: number; images: GalleryImage[] };
  const {
    data: pages,
    size,
    setSize,
    mutate: mutateImages,
    isValidating,
  } = useSWRInfinite<ImagePage>(
    (index, prev: ImagePage | null) => {
      if (prev && prev.images.length < PAGE_SIZE) return null; // no more pages
      return ["gallery-images", filterKeyword, filterNiche, imageSearchDebounced, index] as const;
    },
    ([, kw, nc, s, index]) =>
      listGalleryImages({
        keyword: (kw as string) || undefined,
        niche: (nc as string) || undefined,
        search: (s as string) || undefined,
        limit: PAGE_SIZE,
        offset: (index as number) * PAGE_SIZE,
      }).then((r) => r.data as ImagePage),
    { revalidateFirstPage: false }
  );

  const images = pages ? pages.flatMap((p) => p.images) : [];
  const total = pages?.[0]?.total ?? 0;
  const lastPage = pages?.[pages.length - 1];
  const reachedEnd = !!lastPage && lastPage.images.length < PAGE_SIZE;
  const loadingMore = isValidating;

  // Reset to first page whenever the keyword/niche/search filter changes
  useEffect(() => {
    setSize(1);
  }, [filterKeyword, filterNiche, imageSearchDebounced, setSize]);

  // Load next page when the sentinel scrolls into view
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || reachedEnd) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !loadingMore) setSize((s) => s + 1);
      },
      { rootMargin: "600px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [reachedEnd, loadingMore, setSize]);

  // ── Lightbox ──
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);
  const closeLightbox = useCallback(() => setLightboxIdx(null), []);
  const showPrev = useCallback(() => setLightboxIdx((i) => (i === null ? i : Math.max(0, i - 1))), []);
  const showNext = useCallback(
    () => setLightboxIdx((i) => (i === null ? i : Math.min(images.length - 1, i + 1))),
    [images.length]
  );
  useEffect(() => {
    if (lightboxIdx === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeLightbox();
      else if (e.key === "ArrowLeft") showPrev();
      else if (e.key === "ArrowRight") showNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightboxIdx, closeLightbox, showPrev, showNext]);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState<number | null>(null);

  // ── Manual AI closeup filter (standalone tool, not tied to any keyword's
  // config) — setup modal (criteria + scope) -> scanning -> review modal. ──
  const [showAIFilterSetup, setShowAIFilterSetup] = useState(false);
  const [aiFilterCriteria, setAIFilterCriteria] = useState("");
  const [aiFilterScope, setAIFilterScope] = useState(""); // "" = all keywords
  const [aiFilterScanning, setAIFilterScanning] = useState(false);
  const [aiFilterProgress, setAIFilterProgress] = useState<{ done: number; total: number } | null>(null);
  const [filterReview, setFilterReview] = useState<{
    scopeLabel: string;
    candidates: FilterCandidate[];
    selected: Set<number>;
  } | null>(null);
  const [filterConfirming, setFilterConfirming] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [toast, setToast] = useState<ToastData | null>(null);
  const notify = (message: string, type?: ToastData["type"]) => setToast({ message, type });

  const [confirmDeleteKeyword, setConfirmDeleteKeyword] = useState<Keyword | null>(null);
  const [deletingKeyword, setDeletingKeyword] = useState(false);
  const [confirmDeleteImage, setConfirmDeleteImage] = useState<GalleryImage | null>(null);
  const [deletingImage, setDeletingImage] = useState(false);
  const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);
  const [uploadKeywordInput, setUploadKeywordInput] = useState("");

  // Collapsed by default — with many keywords the table pushed the image
  // grid far down the page; collapse + an internal scroll box keep it in reach.
  const [keywordsExpanded, setKeywordsExpanded] = useState(false);

  // Extra keyword tags per image — a photo can feature more than one person.
  const [tagTarget, setTagTarget] = useState<GalleryImage | null>(null);
  const [tagInput, setTagInput] = useState("");

  function set(field: string, value: unknown) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function openCreate() {
    setForm(emptyForm);
    setEditingId(null);
    setShowForm(true);
  }

  function openEdit(k: Keyword) {
    setForm({
      keyword: k.keyword,
      niche: k.niche ?? "",
      is_active: k.is_active,
      max_images: k.max_images,
      max_pages: k.max_pages,
      min_width: k.min_width,
      min_height: k.min_height,
      source_engine: k.source_engine,
      license_filter: k.license_filter,
    });
    setEditingId(k.id);
    setShowForm(true);
  }

  async function handleSave() {
    if (!form.keyword.trim()) {
      notify("Keyword is required.", "error");
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        await updateGalleryKeyword(editingId, form);
      } else {
        await createGalleryKeyword(form);
      }
      setShowForm(false);
      mutateKeywords();
      mutateNiches();
      notify(editingId ? "Keyword updated." : "Keyword created.", "success");
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      notify(`Save failed: ${err.response?.data?.detail ?? "unknown error"}`, "error");
    } finally {
      setSaving(false);
    }
  }

  function handleDeleteKeyword(k: Keyword) {
    setConfirmDeleteKeyword(k);
  }

  async function confirmDeleteKeywordAction() {
    if (!confirmDeleteKeyword) return;
    setDeletingKeyword(true);
    try {
      await deleteGalleryKeyword(confirmDeleteKeyword.id);
      mutateKeywords();
      notify(`Deleted keyword "${confirmDeleteKeyword.keyword}".`, "success");
    } catch {
      notify("Delete failed. Please try again.", "error");
    } finally {
      setDeletingKeyword(false);
      setConfirmDeleteKeyword(null);
    }
  }

  async function handleDownloadNow(id: number) {
    setDownloading(id);
    try {
      await downloadGalleryKeywordNow(id);
      notify("Download queued — new images will appear within a few minutes.", "success");
    } finally {
      setDownloading(null);
    }
  }

  async function sleep(ms: number) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function openAIFilterSetup() {
    setAIFilterCriteria(appSettings?.gallery_ai_filter_last_criteria ?? "");
    setAIFilterScope("");
    setAIFilterProgress(null);
    setShowAIFilterSetup(true);
  }

  async function handleStartAIFilterScan() {
    const criteria = aiFilterCriteria.trim();
    if (!criteria) {
      notify("Enter a filter criteria first.", "error");
      return;
    }
    setAIFilterScanning(true);
    setAIFilterProgress(null);
    try {
      const res = await startAIFilterScan(criteria, aiFilterScope || undefined);
      const taskId = (res.data as { task_id: string }).task_id;

      let candidates: FilterCandidate[] | null = null;
      // Poll for up to ~10 minutes — a global scan can span the whole
      // gallery (thousands of images), each needing its own AI vision call.
      for (let i = 0; i < 300; i++) {
        const r = await getAIFilterScanStatus(taskId);
        const data = r.data as { status: string; done: number; total: number; candidates: FilterCandidate[] | null };
        if (data.status === "SUCCESS") {
          candidates = data.candidates ?? [];
          break;
        }
        if (data.status === "FAILURE") {
          throw new Error("AI filter task failed");
        }
        if (data.status === "PROGRESS") {
          setAIFilterProgress({ done: data.done, total: data.total });
        }
        await sleep(2000);
      }

      if (candidates === null) {
        notify("AI filter is taking longer than expected — try again shortly.", "error");
        return;
      }
      mutateSettings();
      if (candidates.length === 0) {
        notify("No images flagged — everything already matches the criteria.", "success");
        setShowAIFilterSetup(false);
        return;
      }
      setShowAIFilterSetup(false);
      setFilterReview({
        scopeLabel: aiFilterScope || "all keywords",
        candidates,
        selected: new Set(candidates.map((c) => c.id)),
      });
    } catch {
      notify("Failed to run AI filter.", "error");
    } finally {
      setAIFilterScanning(false);
      setAIFilterProgress(null);
    }
  }

  function toggleFilterCandidate(id: number) {
    setFilterReview((prev) => {
      if (!prev) return prev;
      const next = new Set(prev.selected);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...prev, selected: next };
    });
  }

  async function confirmFilterDelete() {
    if (!filterReview) return;
    const ids = Array.from(filterReview.selected);
    if (ids.length === 0) {
      setFilterReview(null);
      return;
    }
    setFilterConfirming(true);
    try {
      const res = await bulkDeleteGalleryImages(ids);
      const deleted = (res.data as { deleted: number }).deleted;
      notify(`Deleted ${deleted} image(s).`, "success");
      setFilterReview(null);
      mutateKeywords();
      mutateImages();
    } catch {
      notify("Bulk delete failed. Please try again.", "error");
    } finally {
      setFilterConfirming(false);
    }
  }

  async function doUpload(file: File, keyword: string) {
    setUploading(true);
    try {
      await uploadGalleryImage(file, keyword);
      mutateImages();
      mutateKeywords();
      notify(`Uploaded to "${keyword}".`, "success");
    } catch (err) {
      const ex = err as { response?: { data?: { detail?: string } } };
      notify(`Upload failed: ${ex.response?.data?.detail ?? "unknown error"}`, "error");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (filterKeyword) {
      doUpload(file, filterKeyword);
    } else {
      setUploadKeywordInput("");
      setPendingUploadFile(file);
    }
  }

  function confirmUploadKeyword() {
    if (!pendingUploadFile || !uploadKeywordInput.trim()) return;
    const file = pendingUploadFile;
    const keyword = uploadKeywordInput.trim();
    setPendingUploadFile(null);
    doUpload(file, keyword);
  }

  function cancelUploadKeyword() {
    setPendingUploadFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleDeleteImage(img: GalleryImage) {
    setConfirmDeleteImage(img);
  }

  async function confirmDeleteImageAction() {
    if (!confirmDeleteImage) return;
    setDeletingImage(true);
    try {
      await deleteGalleryImage(confirmDeleteImage.id);
      mutateImages();
      mutateKeywords();
    } catch {
      notify("Delete failed. Please try again.", "error");
    } finally {
      setDeletingImage(false);
      setConfirmDeleteImage(null);
    }
  }

  function openTagDialog(img: GalleryImage) {
    setTagInput("");
    setTagTarget(img);
  }

  async function confirmAddTag() {
    if (!tagTarget || !tagInput.trim()) return;
    const next = [...tagTarget.extra_keywords, tagInput.trim().toLowerCase()];
    setTagTarget(null);
    try {
      await updateGalleryImageKeywords(tagTarget.id, next);
      mutateImages();
    } catch {
      notify("Could not add tag. Please try again.", "error");
    }
  }

  async function removeTag(img: GalleryImage, tag: string) {
    try {
      await updateGalleryImageKeywords(img.id, img.extra_keywords.filter((t) => t !== tag));
      mutateImages();
    } catch {
      notify("Could not remove tag. Please try again.", "error");
    }
  }

  // ── Bulk edit / import keywords via JSON ──
  const BULK_FIELDS = [
    "keyword", "niche", "is_active", "max_images", "max_pages",
    "min_width", "min_height", "source_engine", "license_filter",
  ] as const;

  const [showBulkModal, setShowBulkModal] = useState(false);
  const [bulkText, setBulkText] = useState("");
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkResult, setBulkResult] = useState<{ created: number; updated: number; errors: string[] } | null>(null);
  const bulkFileRef = useRef<HTMLInputElement>(null);

  function keywordsToJson(list: Keyword[]) {
    return JSON.stringify(
      list.map((k) =>
        Object.fromEntries(BULK_FIELDS.map((f) => [f, (k as unknown as Record<string, unknown>)[f] ?? null]))
      ),
      null,
      2
    );
  }

  function openBulkModal() {
    setBulkText(keywordsToJson(keywords));
    setBulkResult(null);
    setShowBulkModal(true);
  }

  function handleBulkFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setBulkText(String(reader.result));
    reader.readAsText(file);
    if (bulkFileRef.current) bulkFileRef.current.value = "";
  }

  function handleBulkDownload() {
    const blob = new Blob([bulkText], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "gallery-keywords.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleBulkSave() {
    let items: unknown;
    try {
      items = JSON.parse(bulkText);
    } catch {
      notify("Invalid JSON — please fix the syntax and try again.", "error");
      return;
    }
    if (!Array.isArray(items)) {
      notify("JSON must be an array of keyword objects.", "error");
      return;
    }
    setBulkSaving(true);
    setBulkResult(null);
    try {
      const res = await bulkImportGalleryKeywords(items as Record<string, unknown>[]);
      setBulkResult(res.data as { created: number; updated: number; errors: string[] });
      mutateKeywords();
      mutateNiches();
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      notify(`Bulk import failed: ${err.response?.data?.detail ?? "unknown error"}`, "error");
    } finally {
      setBulkSaving(false);
    }
  }

  const inputCls = "input-rect py-2";
  const labelCls = "block text-xs font-semibold text-ink-80 mb-1";

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1
            className="text-display-md text-ink"
            style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}
          >
            Gallery
          </h1>
          <p className="text-caption text-ink-48 mt-1">
            {total} image{total === 1 ? "" : "s"} — used by the News-to-Image designer
            {appSettings?.gallery_scraping_paused && (
              <span className="ml-2 text-red-500 font-semibold">· Scraping paused globally</span>
            )}
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleUpload} />
          {appSettings && (
            <button
              onClick={toggleScrapingPause}
              disabled={pauseToggling}
              title={appSettings.gallery_scraping_paused ? "Resume scheduled gallery scraping" : "Pause scheduled gallery scraping (Download Now still works)"}
              className={`btn flex items-center gap-2 ${appSettings.gallery_scraping_paused ? "btn-secondary" : "border border-hairline text-ink-80 hover:border-red-400 hover:text-red-500"}`}
            >
              {pauseToggling ? (
                <Icon icon="svg-spinners:ring-resize" width={16} />
              ) : (
                <Icon icon={appSettings.gallery_scraping_paused ? "solar:play-bold-duotone" : "solar:pause-bold-duotone"} width={16} />
              )}
              {appSettings.gallery_scraping_paused ? "Resume Scraping" : "Pause Scraping"}
            </button>
          )}
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="btn btn-secondary flex items-center gap-2"
          >
            {uploading ? <Icon icon="svg-spinners:ring-resize" width={16} /> : <Icon icon="solar:upload-bold-duotone" width={16} />}
            Upload Image
          </button>
          <button onClick={openAIFilterSetup} className="btn btn-secondary flex items-center gap-2">
            <Icon icon="solar:filter-bold-duotone" width={16} />
            Run AI Filter
          </button>
          <button onClick={openBulkModal} className="btn btn-secondary flex items-center gap-2">
            <Icon icon="solar:code-bold-duotone" width={16} />
            Bulk Edit (JSON)
          </button>
          <button onClick={openCreate} className="btn btn-primary flex items-center gap-2">
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            Add Keyword
          </button>
        </div>
      </div>

      {/* ── Add/Edit keyword form ── */}
      {showForm && (
        <div className="card p-6 space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold text-ink">{editingId ? "Edit Keyword" : "New Download Keyword"}</h2>
            <button onClick={() => setShowForm(false)} className="text-ink-48 hover:text-ink">
              <Icon icon="solar:close-circle-bold-duotone" width={20} />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className={labelCls}>Keyword</label>
              <input className={inputCls} value={form.keyword} onChange={(e) => set("keyword", e.target.value)} placeholder="marc marquez" />
            </div>
            <div>
              <label className={labelCls}>Niche / Category</label>
              <input
                className={inputCls}
                list="niche-options"
                value={form.niche}
                onChange={(e) => set("niche", e.target.value)}
                placeholder="F1, MotoGP, UFC…"
              />
              <datalist id="niche-options">
                {niches.map((n) => (
                  <option key={n} value={n} />
                ))}
              </datalist>
            </div>
            <div>
              <label className={labelCls}>Max Images per Run</label>
              <input type="number" min={1} max={200} className={inputCls} value={form.max_images} onChange={(e) => set("max_images", parseInt(e.target.value) || 50)} />
            </div>
            <div>
              <label className={labelCls}>Pages to Download</label>
              <input type="number" min={1} max={50} className={inputCls} value={form.max_pages} onChange={(e) => set("max_pages", parseInt(e.target.value) || 10)} />
            </div>
            <div>
              <label className={labelCls}>Source Engine</label>
              <select className={inputCls} value={form.source_engine} onChange={(e) => set("source_engine", e.target.value)}>
                <option value="9router">9Router (web-fetch)</option>
                <option value="bing">Bing (legacy)</option>
                <option value="google">Google (legacy)</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Min Width (px)</label>
              <input type="number" min={50} className={inputCls} value={form.min_width} onChange={(e) => set("min_width", parseInt(e.target.value) || 200)} />
            </div>
            <div>
              <label className={labelCls}>Min Height (px)</label>
              <input type="number" min={50} className={inputCls} value={form.min_height} onChange={(e) => set("min_height", parseInt(e.target.value) || 200)} />
            </div>
          </div>

          <div className="flex justify-end gap-2 border-t border-hairline pt-4">
            <button onClick={() => setShowForm(false)} className="btn btn-secondary">Cancel</button>
            <button onClick={handleSave} disabled={saving} className="btn btn-primary flex items-center gap-2">
              {saving && <Icon icon="svg-spinners:ring-resize" width={14} />}
              {editingId ? "Save Changes" : "Create Keyword"}
            </button>
          </div>
        </div>
      )}

      {/* ── Keywords table (collapsible — can get long) ── */}
      {keywords.length > 0 && (
        <div className="card overflow-hidden p-0">
          <button
            onClick={() => setKeywordsExpanded((v) => !v)}
            className="w-full flex items-center justify-between px-5 py-3 border-b border-hairline bg-parchment/50 hover:bg-parchment transition-colors"
          >
            <span className="text-sm font-bold text-ink flex items-center gap-2">
              Keywords
              <span className="text-ink-48 font-normal">
                ({visibleKeywords.length}{keywordsFiltered ? ` of ${keywords.length}` : ""})
              </span>
            </span>
            <span className="flex items-center gap-1 text-ink-48 text-xs font-medium">
              {keywordsExpanded ? "Collapse" : "Expand"}
              <Icon icon={keywordsExpanded ? "solar:alt-arrow-up-bold" : "solar:alt-arrow-down-bold"} width={16} />
            </span>
          </button>

          {keywordsExpanded && (
            <>
              <div className="flex items-center gap-2 px-5 py-3 border-b border-hairline bg-parchment/50">
                <div className="relative">
                  <Icon icon="solar:magnifer-linear" width={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-48" />
                  <input
                    type="text"
                    value={keywordSearch}
                    onChange={(e) => setKeywordSearch(e.target.value)}
                    placeholder="Search keywords…"
                    className="input-rect py-1 pl-7 text-xs w-52"
                  />
                  {keywordSearch && (
                    <button
                      onClick={() => setKeywordSearch("")}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 text-ink-48 hover:text-primary-main"
                      title="Clear search"
                    >
                      <Icon icon="solar:close-circle-bold" width={14} />
                    </button>
                  )}
                </div>
                <label className="text-xs font-semibold text-ink-80">Niche</label>
                <select
                  className="input-rect py-1 text-xs w-44"
                  value={filterNiche}
                  onChange={(e) => setFilterNiche(e.target.value)}
                >
                  <option value="">All niches</option>
                  {niches.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
                {filterNiche && (
                  <button
                    onClick={() => setFilterNiche("")}
                    className="inline-flex items-center gap-1 rounded-full bg-primary-main/10 text-primary-main px-2.5 py-0.5 text-[11px] font-semibold"
                  >
                    {filterNiche}
                    <Icon icon="solar:close-circle-bold" width={12} />
                  </button>
                )}
              </div>
              <div className="max-h-[420px] overflow-y-auto">
              <table className="w-full text-caption">
                <thead className="bg-parchment border-b border-hairline sticky top-0 z-10">
                  <tr>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Keyword</th>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Niche</th>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Images</th>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Min Size</th>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Pages</th>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Last Download</th>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Next Event</th>
                    <th className="px-5 py-3 text-left text-ink-80 font-semibold">Status</th>
                    <th className="px-5 py-3"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {visibleKeywords.map((k) => (
                <tr key={k.id} className="hover:bg-parchment/50 transition-colors">
                  <td className="px-5 py-3">
                    <button onClick={() => setFilterKeyword(filterKeyword === k.keyword ? "" : k.keyword)} className="text-ink font-medium hover:text-primary-main">
                      {k.keyword}
                    </button>
                  </td>
                  <td className="px-5 py-3">
                    {k.niche ? (
                      <button
                        onClick={() => setFilterNiche(filterNiche === k.niche ? "" : (k.niche as string))}
                        className="inline-flex items-center rounded-full bg-parchment px-2 py-0.5 text-[11px] font-semibold text-ink-80 hover:text-primary-main"
                      >
                        {k.niche}
                      </button>
                    ) : (
                      <span className="text-ink-48">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-ink-80">{k.image_count}</td>
                  <td className="px-5 py-3 text-ink-80">{k.min_width}×{k.min_height}</td>
                  <td className="px-5 py-3 text-ink-80">{k.max_pages}</td>
                  <td className="px-5 py-3 text-ink-80">
                    {k.last_downloaded_at ? formatDistanceToNowStrict(new Date(k.last_downloaded_at), { addSuffix: true }) : "never"}
                  </td>
                  <td className="px-5 py-3 text-ink-80">
                    {k.next_event_date ? (
                      <span title="Detected automatically — see event_calendar / gallery_downloader">
                        {format(new Date(`${k.next_event_date}T00:00:00`), "d MMM")}{" "}
                        <span className="text-[11px] text-ink-48">
                          ({(() => {
                            const days = differenceInCalendarDays(new Date(`${k.next_event_date}T00:00:00`), new Date());
                            return days === 0 ? "today" : days > 0 ? `in ${days}d` : `${-days}d ago`;
                          })()})
                        </span>
                      </span>
                    ) : (
                      <span className="text-ink-48">—</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    {!k.is_active ? (
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-600">Inactive</span>
                    ) : k.last_download_error ? (
                      <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700" title={k.last_download_error}>Error</span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">Active</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => handleDownloadNow(k.id)}
                        disabled={downloading === k.id}
                        title="Download now"
                        className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                      >
                        {downloading === k.id ? <Icon icon="svg-spinners:ring-resize" width={16} /> : <Icon icon="solar:download-bold-duotone" width={16} />}
                      </button>
                      <button
                        onClick={() => updateGalleryKeyword(k.id, { is_active: !k.is_active }).then(() => mutateKeywords())}
                        title={k.is_active ? "Deactivate" : "Activate"}
                        className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                      >
                        <Icon icon={k.is_active ? "solar:pause-circle-bold-duotone" : "solar:play-bold-duotone"} width={16} />
                      </button>
                      <button
                        onClick={() => openEdit(k)}
                        title="Edit"
                        className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                      >
                        <Icon icon="solar:pen-bold-duotone" width={16} />
                      </button>
                      <button
                        onClick={() => handleDeleteKeyword(k)}
                        title="Delete"
                        className="p-1.5 rounded-lg text-ink-48 hover:text-red-600 hover:bg-red-50 transition-colors"
                      >
                        <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
                </tbody>
              </table>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Image grid ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-bold text-ink">
              Images
              {pages && <span className="text-ink-48 font-normal text-sm ml-1">({total})</span>}
            </h2>
            {filterKeyword && (
              <button
                onClick={() => setFilterKeyword("")}
                className="inline-flex items-center gap-1 rounded-full bg-primary-main/10 text-primary-main px-2.5 py-0.5 text-[11px] font-semibold"
              >
                {filterKeyword}
                <Icon icon="solar:close-circle-bold" width={12} />
              </button>
            )}
            {filterNiche && (
              <button
                onClick={() => setFilterNiche("")}
                className="inline-flex items-center gap-1 rounded-full bg-primary-main/10 text-primary-main px-2.5 py-0.5 text-[11px] font-semibold"
              >
                {filterNiche}
                <Icon icon="solar:close-circle-bold" width={12} />
              </button>
            )}
          </div>
          <div className="relative">
            <Icon icon="solar:magnifer-linear" width={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-48" />
            <input
              type="text"
              value={imageSearch}
              onChange={(e) => setImageSearch(e.target.value)}
              placeholder="Search images by keyword…"
              className="input-rect py-1.5 pl-7 text-xs w-64"
            />
            {imageSearch && (
              <button
                onClick={() => setImageSearch("")}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-ink-48 hover:text-primary-main"
                title="Clear search"
              >
                <Icon icon="solar:close-circle-bold" width={14} />
              </button>
            )}
          </div>
        </div>

        {images.length === 0 ? (
          <div className="card p-10 text-center text-ink-48">
            <Icon icon="solar:gallery-wide-bold-duotone" width={40} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">
              {keywords.length === 0
                ? "No keywords yet. Add one and hit Download Now, or upload images manually."
                : "No images yet — queue a download or upload manually."}
            </p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
              {images.map((img, idx) => (
                <div key={img.id} className="card p-0 overflow-hidden group relative">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={img.public_url}
                    alt={img.keyword}
                    className="w-full h-36 object-cover cursor-zoom-in"
                    loading="lazy"
                    onClick={() => setLightboxIdx(idx)}
                  />
                  <div className="p-2">
                    <p className="text-[11px] font-semibold text-ink truncate">{img.keyword}</p>
                    <p className="text-[10px] text-ink-48">
                      {img.width}×{img.height} · {img.source_engine}
                      {img.is_used && <span className="ml-1 text-emerald-600 font-semibold">used</span>}
                    </p>
                    {img.downloaded_at && (
                      <p className="text-[10px] text-ink-48">{formatDistanceToNowStrict(new Date(img.downloaded_at), { addSuffix: true })}</p>
                    )}
                    {/* Extra keyword tags — a photo can feature more than one person */}
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {img.extra_keywords.map((tag) => (
                        <span
                          key={tag}
                          className="inline-flex items-center gap-0.5 rounded-full bg-parchment px-1.5 py-0.5 text-[9px] font-medium text-ink-80"
                        >
                          {tag}
                          <button onClick={() => removeTag(img, tag)} title={`Remove "${tag}"`} className="hover:text-red-600">
                            <Icon icon="solar:close-circle-bold" width={9} />
                          </button>
                        </span>
                      ))}
                      <button
                        onClick={() => openTagDialog(img)}
                        title="Tag another person in this photo"
                        className="inline-flex items-center gap-0.5 rounded-full border border-dashed border-hairline px-1.5 py-0.5 text-[9px] font-medium text-ink-48 hover:text-primary-main hover:border-primary-main transition-colors"
                      >
                        <Icon icon="solar:add-circle-bold" width={9} /> tag
                      </button>
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteImage(img)}
                    title="Delete"
                    className="absolute top-1.5 right-1.5 p-1 rounded-lg bg-black/40 text-white opacity-0 group-hover:opacity-100 hover:bg-red-600 transition-all"
                  >
                    <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                  </button>
                </div>
              ))}
            </div>

            {/* Infinite-scroll sentinel + status */}
            <div ref={sentinelRef} className="h-10" />
            <div className="text-center py-4 text-caption text-ink-48">
              {loadingMore ? (
                <span className="inline-flex items-center gap-2">
                  <Icon icon="svg-spinners:180-ring" width={16} /> Loading more…
                </span>
              ) : reachedEnd ? (
                `All ${images.length} images loaded`
              ) : null}
            </div>
          </>
        )}
      </div>

      {/* ── Lightbox ── */}
      {lightboxIdx !== null && images[lightboxIdx] && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm"
          onClick={closeLightbox}
        >
          <button
            onClick={closeLightbox}
            title="Close (Esc)"
            className="absolute top-4 right-4 p-2 rounded-lg bg-white/10 text-white hover:bg-white/20"
          >
            <Icon icon="solar:close-circle-bold" width={28} />
          </button>

          {lightboxIdx > 0 && (
            <button
              onClick={(e) => { e.stopPropagation(); showPrev(); }}
              title="Previous (←)"
              className="absolute left-4 p-2 rounded-full bg-white/10 text-white hover:bg-white/20"
            >
              <Icon icon="solar:alt-arrow-left-bold" width={28} />
            </button>
          )}
          {lightboxIdx < images.length - 1 && (
            <button
              onClick={(e) => { e.stopPropagation(); showNext(); }}
              title="Next (→)"
              className="absolute right-4 p-2 rounded-full bg-white/10 text-white hover:bg-white/20"
            >
              <Icon icon="solar:alt-arrow-right-bold" width={28} />
            </button>
          )}

          <div className="max-w-[90vw] max-h-[90vh] flex flex-col items-center" onClick={(e) => e.stopPropagation()}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={images[lightboxIdx].public_url}
              alt={images[lightboxIdx].keyword}
              className="max-w-[90vw] max-h-[82vh] object-contain rounded-lg"
            />
            <div className="mt-3 text-center text-white/80 text-caption">
              <span className="font-semibold text-white">{images[lightboxIdx].keyword}</span>
              {images[lightboxIdx].extra_keywords.length > 0 && (
                <span className="text-white/60"> (+ {images[lightboxIdx].extra_keywords.join(", ")})</span>
              )}
              {" · "}{images[lightboxIdx].width}×{images[lightboxIdx].height}
              {" · "}{images[lightboxIdx].source_engine}
              {" · "}{lightboxIdx + 1}/{images.length}
            </div>
          </div>
        </div>
      )}

      {/* ── Bulk edit / import keywords (JSON) ── */}
      {showBulkModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setShowBulkModal(false)}>
          <div className="card w-full max-w-3xl max-h-[85vh] flex flex-col p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-ink">Bulk Edit / Import Keywords</h2>
                <p className="text-[11px] text-ink-48 mt-0.5">
                  Edit this JSON directly, or replace it with an uploaded file, then Save. Matching is by
                  <code className="mx-1 px-1 py-0.5 rounded bg-parchment">keyword</code>
                  — existing keywords are updated in place, new ones are created. Nothing is deleted by an import.
                </p>
              </div>
              <button onClick={() => setShowBulkModal(false)} className="text-ink-48 hover:text-ink shrink-0">
                <Icon icon="solar:close-circle-bold-duotone" width={20} />
              </button>
            </div>

            <textarea
              className="input-rect font-mono text-xs flex-1 min-h-[320px] resize-none"
              spellCheck={false}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
            />

            {bulkResult && (
              <div className="rounded-lg bg-parchment px-3 py-2 text-xs text-ink-80">
                {bulkResult.created} created, {bulkResult.updated} updated
                {bulkResult.errors.length > 0 && (
                  <span className="text-red-600"> — {bulkResult.errors.length} error(s): {bulkResult.errors.join("; ")}</span>
                )}
              </div>
            )}

            <div className="flex items-center justify-between gap-2 border-t border-hairline pt-4">
              <div className="flex gap-2">
                <input ref={bulkFileRef} type="file" accept="application/json,.json" className="hidden" onChange={handleBulkFile} />
                <button onClick={() => bulkFileRef.current?.click()} className="btn btn-secondary text-xs flex items-center gap-1.5">
                  <Icon icon="solar:folder-open-bold-duotone" width={14} /> Load file
                </button>
                <button onClick={handleBulkDownload} className="btn btn-secondary text-xs flex items-center gap-1.5">
                  <Icon icon="solar:download-bold-duotone" width={14} /> Download .json
                </button>
              </div>
              <div className="flex gap-2">
                <button onClick={() => setShowBulkModal(false)} className="btn btn-secondary">Close</button>
                <button onClick={handleBulkSave} disabled={bulkSaving} className="btn btn-primary flex items-center gap-2">
                  {bulkSaving && <Icon icon="svg-spinners:ring-resize" width={14} />}
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── AI filter setup modal (criteria + scope, then scan) ── */}
      {showAIFilterSetup && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => !aiFilterScanning && setShowAIFilterSetup(false)}
        >
          <div className="card w-full max-w-lg p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-ink">Run AI Filter</h2>
                <p className="text-[11px] text-ink-48 mt-0.5">
                  Nothing is deleted automatically — you&apos;ll get a list to review and confirm first.
                </p>
              </div>
              <button
                onClick={() => !aiFilterScanning && setShowAIFilterSetup(false)}
                className="text-ink-48 hover:text-ink shrink-0"
              >
                <Icon icon="solar:close-circle-bold-duotone" width={20} />
              </button>
            </div>

            <div>
              <label className={labelCls}>Filter Criteria</label>
              <textarea
                className={inputCls}
                rows={3}
                value={aiFilterCriteria}
                onChange={(e) => setAIFilterCriteria(e.target.value)}
                placeholder="e.g. close-up headshot of a person's face, not full-body/action/crowd shots"
                disabled={aiFilterScanning}
              />
            </div>

            <div>
              <label className={labelCls}>Scope</label>
              <select
                className={inputCls}
                value={aiFilterScope}
                onChange={(e) => setAIFilterScope(e.target.value)}
                disabled={aiFilterScanning}
              >
                <option value="">All keywords (entire gallery)</option>
                {keywords.map((k) => (
                  <option key={k.id} value={k.keyword}>{k.keyword}</option>
                ))}
              </select>
            </div>

            {aiFilterScanning && (
              <div className="rounded-lg bg-parchment px-3 py-2 text-xs text-ink-80 flex items-center gap-2">
                <Icon icon="svg-spinners:ring-resize" width={14} />
                {aiFilterProgress
                  ? `Scanning ${aiFilterProgress.done} of ${aiFilterProgress.total} images…`
                  : "Starting scan…"}
              </div>
            )}

            <div className="flex justify-end gap-2 border-t border-hairline pt-4">
              <button
                onClick={() => setShowAIFilterSetup(false)}
                disabled={aiFilterScanning}
                className="btn btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={handleStartAIFilterScan}
                disabled={aiFilterScanning || !aiFilterCriteria.trim()}
                className="btn btn-primary flex items-center gap-2"
              >
                {aiFilterScanning && <Icon icon="svg-spinners:ring-resize" width={14} />}
                Scan
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── AI closeup filter review modal ── */}
      {filterReview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => !filterConfirming && setFilterReview(null)}
        >
          <div
            className="card w-full max-w-3xl max-h-[85vh] flex flex-col p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-ink">
                  AI Filter Review — {filterReview.scopeLabel}
                </h2>
                <p className="text-[11px] text-ink-48 mt-0.5">
                  {filterReview.candidates.length} image(s) did NOT match the filter criteria. Checked images
                  will be deleted — uncheck any you want to keep.
                </p>
              </div>
              <button
                onClick={() => !filterConfirming && setFilterReview(null)}
                className="text-ink-48 hover:text-ink shrink-0"
              >
                <Icon icon="solar:close-circle-bold-duotone" width={20} />
              </button>
            </div>

            <div className="flex items-center gap-3 text-xs">
              <button
                onClick={() =>
                  setFilterReview((prev) =>
                    prev ? { ...prev, selected: new Set(prev.candidates.map((c) => c.id)) } : prev
                  )
                }
                className="text-primary-main hover:underline font-medium"
              >
                Select all
              </button>
              <button
                onClick={() => setFilterReview((prev) => (prev ? { ...prev, selected: new Set() } : prev))}
                className="text-primary-main hover:underline font-medium"
              >
                Select none
              </button>
              <span className="text-ink-48">{filterReview.selected.size} of {filterReview.candidates.length} selected for deletion</span>
            </div>

            <div className="flex-1 overflow-y-auto">
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                {filterReview.candidates.map((c) => {
                  const checked = filterReview.selected.has(c.id);
                  return (
                    <label
                      key={c.id}
                      className={`relative cursor-pointer rounded-lg overflow-hidden border-2 transition-colors ${
                        checked ? "border-red-500" : "border-transparent"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="absolute top-1.5 left-1.5 z-10 w-4 h-4 accent-red-600"
                        checked={checked}
                        onChange={() => toggleFilterCandidate(c.id)}
                      />
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={c.public_url} alt="" className="w-full h-28 object-cover" loading="lazy" />
                      <div className={`absolute inset-0 pointer-events-none ${checked ? "bg-red-600/20" : ""}`} />
                      <span className="absolute bottom-1 left-1.5 text-[9px] font-medium text-white bg-black/60 rounded px-1 py-0.5 truncate max-w-[70%]">
                        {c.keyword}
                      </span>
                      <span className="absolute bottom-1 right-1.5 text-[9px] font-semibold text-white bg-black/60 rounded px-1 py-0.5">
                        {Math.round(c.confidence * 100)}%
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="flex justify-end gap-2 border-t border-hairline pt-4">
              <button
                onClick={() => setFilterReview(null)}
                disabled={filterConfirming}
                className="btn btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={confirmFilterDelete}
                disabled={filterConfirming || filterReview.selected.size === 0}
                className="btn btn-primary bg-red-600 hover:bg-red-700 flex items-center gap-2"
              >
                {filterConfirming && <Icon icon="svg-spinners:ring-resize" width={14} />}
                Delete Selected ({filterReview.selected.size})
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!confirmDeleteKeyword}
        title={`Delete keyword "${confirmDeleteKeyword?.keyword ?? ""}"?`}
        message="Downloaded images under this keyword are kept — only the download config is removed."
        confirmLabel="Delete"
        danger
        loading={deletingKeyword}
        onConfirm={confirmDeleteKeywordAction}
        onCancel={() => setConfirmDeleteKeyword(null)}
      />

      <ConfirmDialog
        open={!!confirmDeleteImage}
        title="Delete this image?"
        message="The file is removed and this exact photo will never be downloaded again for its keyword."
        confirmLabel="Delete"
        danger
        loading={deletingImage}
        onConfirm={confirmDeleteImageAction}
        onCancel={() => setConfirmDeleteImage(null)}
      />

      <PromptDialog
        open={!!pendingUploadFile}
        title="Keyword for this image"
        message="Which keyword should this upload be filed under?"
        placeholder="marc marquez"
        value={uploadKeywordInput}
        onChange={setUploadKeywordInput}
        confirmLabel="Upload"
        onConfirm={confirmUploadKeyword}
        onCancel={cancelUploadKeyword}
      />

      <PromptDialog
        open={!!tagTarget}
        title="Tag another person in this photo"
        message={`Add a keyword this "${tagTarget?.keyword ?? ""}" image should also match under.`}
        placeholder="valentino rossi"
        value={tagInput}
        onChange={setTagInput}
        confirmLabel="Add tag"
        onConfirm={confirmAddTag}
        onCancel={() => setTagTarget(null)}
      />

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
