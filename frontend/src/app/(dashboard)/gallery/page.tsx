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
  importGalleryLinks,
  getGalleryLinkImportStatus,
  deleteGalleryImage,
  updateGalleryImageKeywords,
  updateGalleryImageLabels,
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
  attributes: Record<string, any>;
  labeled_at: string | null;
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
        (r) =>
          r.data as {
            gallery_scraping_paused: boolean;
            gallery_ai_filter_last_criteria: string | null;
          }
      )
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

  // Label filters
  const [labelPeopleFilter, setLabelPeopleFilter] = useState<string>("any");
  const [labelShotFilter, setLabelShotFilter] = useState<string>("any");
  const [hideArchival, setHideArchival] = useState<boolean>(false);

  // Add photos menu dropdown
  const [showAddMenu, setShowAddMenu] = useState<boolean>(false);

  const visibleKeywords = keywords
    .filter((k) => (filterNiche ? k.niche === filterNiche : true))
    .filter((k) => (keywordSearch ? k.keyword.toLowerCase().includes(keywordSearch.trim().toLowerCase()) : true));

  // Debounce the image free-text search
  const [imageSearchDebounced, setImageSearchDebounced] = useState<string>("");
  useEffect(() => {
    const t = setTimeout(() => setImageSearchDebounced(imageSearch.trim()), 350);
    return () => clearTimeout(t);
  }, [imageSearch]);

  // ── Infinite scroll (offset pagination) ──
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
      if (prev && prev.images.length < PAGE_SIZE) return null;
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

  const rawImages = pages ? pages.flatMap((p) => p.images) : [];
  const total = pages?.[0]?.total ?? 0;
  const lastPage = pages?.[pages.length - 1];
  const reachedEnd = !!lastPage && lastPage.images.length < PAGE_SIZE;
  const loadingMore = isValidating;

  // Reset to first page whenever the keyword/niche/search filter changes
  useEffect(() => {
    setSize(1);
  }, [filterKeyword, filterNiche, imageSearchDebounced, setSize]);

  // Client-side filtering by attributes (people count, shot, archival)
  const images = rawImages.filter((img) => {
    const attrs = img.attributes || {};
    if (hideArchival && attrs.archival_photo === true) return false;
    if (labelPeopleFilter === "1") {
      const cnt = attrs.people_count ?? attrs.people?.length;
      if (cnt !== 1) return false;
    } else if (labelPeopleFilter === "2") {
      const cnt = attrs.people_count ?? attrs.people?.length;
      if (cnt !== 2) return false;
    }
    if (labelShotFilter && labelShotFilter !== "any") {
      const shot = (attrs.shot_type || "").toLowerCase();
      if (!shot.includes(labelShotFilter.toLowerCase())) return false;
    }
    return true;
  });

  // Load next page when sentinel scrolls into view
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
  const [showLinkImport, setShowLinkImport] = useState(false);
  const [linkText, setLinkText] = useState("");
  const [linkKeyword, setLinkKeyword] = useState("");
  const [linkHints, setLinkHints] = useState({ people: "", session: "", event_name: "", year: "", note: "" });
  const [linkImporting, setLinkImporting] = useState(false);
  const [linkResults, setLinkResults] = useState<Array<{ url: string; status: string; reason?: string | null }>>([]);
  const [editingLabels, setEditingLabels] = useState(false);
  const [labelForm, setLabelForm] = useState<Record<string, string>>({});
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

  async function handleLinkImport() {
    if (!linkText.trim()) {
      notify("Paste at least one link.", "error");
      return;
    }
    setLinkImporting(true);
    try {
      const { data } = await importGalleryLinks(linkText, linkKeyword, {
        ...linkHints,
        year: linkHints.year ? Number(linkHints.year) : undefined,
      });
      let done = false;
      while (!done) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        const poll = await getGalleryLinkImportStatus(data.task_id);
        done = ["SUCCESS", "FAILURE"].includes(poll.data.state);
        if (done) setLinkResults(poll.data.results || []);
      }
      mutateImages();
      notify("Link import finished.", "success");
    } catch (err: any) {
      notify(`Link import failed: ${err.response?.data?.detail ?? "unknown error"}`, "error");
    } finally {
      setLinkImporting(false);
    }
  }

  async function saveLabels(relabel = false) {
    if (lightboxIdx === null) return;
    const image = images[lightboxIdx];
    try {
      await updateGalleryImageLabels(
        image.id,
        {
          ...labelForm,
          year: labelForm.year ? Number(labelForm.year) : undefined,
          people_count: labelForm.people_count ? Number(labelForm.people_count) : undefined,
          archival_photo:
            labelForm.archival_photo === "true"
              ? true
              : labelForm.archival_photo === "false"
              ? false
              : undefined,
        },
        relabel
      );
      await mutateImages();
      setEditingLabels(false);
      notify("Labels updated.", "success");
    } catch (err: any) {
      notify(`Label update failed: ${err.response?.data?.detail ?? "unknown error"}`, "error");
    }
  }

  // Keywords panel expand/collapse
  const [keywordsExpanded, setKeywordsExpanded] = useState(false);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState<number | null>(null);

  // AI cleanup modal
  const [showAIFilterSetup, setShowAIFilterSetup] = useState(false);
  const [aiFilterCriteria, setAIFilterCriteria] = useState("");
  const [aiFilterScope, setAIFilterScope] = useState("");
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

  const [tagTarget, setTagTarget] = useState<GalleryImage | null>(null);
  const [tagInput, setTagInput] = useState("");

  function setField(field: string, value: unknown) {
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

  async function handleSaveKeyword() {
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
    } catch (e: any) {
      notify(`Save failed: ${e.response?.data?.detail ?? "unknown error"}`, "error");
    } finally {
      setSaving(false);
    }
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
      notify("Download queued — new images will appear shortly.", "success");
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
      notify("Enter filter criteria first.", "error");
      return;
    }
    setAIFilterScanning(true);
    setAIFilterProgress(null);
    try {
      const res = await startAIFilterScan(criteria, aiFilterScope || undefined);
      const taskId = (res.data as { task_id: string }).task_id;

      let candidates: FilterCandidate[] | null = null;
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
        notify("AI filter timed out.", "error");
        return;
      }
      mutateSettings();
      if (candidates.length === 0) {
        notify("No images flagged — all match the criteria.", "success");
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
    } catch (err: any) {
      notify(`Upload failed: ${err.response?.data?.detail ?? "unknown error"}`, "error");
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
      notify("Invalid JSON — fix syntax and try again.", "error");
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
    } catch (e: any) {
      notify(`Bulk import failed: ${e.response?.data?.detail ?? "unknown error"}`, "error");
    } finally {
      setBulkSaving(false);
    }
  }

  const paused = Boolean(appSettings?.gallery_scraping_paused);
  const feeds = `${total.toLocaleString()} photos for News, Discussion, FB recreate and the Instagram two-photo layout.`;
  const subtitle = paused
    ? `${feeds} Scraping is paused for all niches.`
    : `${feeds} New ones arrive from keyword searches every day.`;

  return (
    <div className="space-y-5 max-w-[1240px] pb-24">
      {/* ── 1. Page Header ── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-[26px] font-bold text-text tracking-tight">Gallery</h1>
          <p className="text-sm text-text2">{subtitle}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleUpload} />

          {/* Pause / Resume scraping */}
          <button
            type="button"
            onClick={toggleScrapingPause}
            disabled={pauseToggling}
            title={
              paused
                ? "Resume scheduled gallery scraping"
                : "Pause scheduled gallery scraping for all niches (manual downloads still work)"
            }
            className={`h-10 px-3 rounded-[10px] text-sm font-medium border transition-colors ${
              paused
                ? "border-line bg-surface text-text hover:bg-surface2"
                : "border-bad/60 bg-surface text-bad hover:bg-bad-soft"
            }`}
          >
            {pauseToggling ? "Updating…" : paused ? "Resume scraping" : "Pause scraping"}
          </button>

          {/* Keywords panel toggle */}
          <button
            type="button"
            onClick={() => setKeywordsExpanded((v) => !v)}
            className={`h-10 px-3 rounded-[10px] border text-sm font-medium transition-colors ${
              keywordsExpanded
                ? "border-pri bg-pri-soft text-pri-text font-semibold"
                : "border-line bg-surface text-text hover:bg-surface2"
            }`}
          >
            Keywords ({keywords.length})
          </button>

          {/* AI cleanup */}
          <button
            type="button"
            onClick={openAIFilterSetup}
            className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-medium hover:bg-surface2 transition-colors"
          >
            AI cleanup
          </button>

          {/* Add photos menu */}
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowAddMenu((v) => !v)}
              className="h-10 px-4 rounded-[10px] bg-pri text-pri-fg text-sm font-semibold hover:opacity-90 transition-opacity flex items-center gap-1.5"
            >
              <span>Add photos</span>
              <Icon icon={showAddMenu ? "solar:alt-arrow-up-bold" : "solar:alt-arrow-down-bold"} width={14} />
            </button>

            {showAddMenu && (
              <div
                className="absolute right-0 top-11 w-48 rounded-xl bg-surface border border-line shadow-lg p-1.5 z-30 flex flex-col gap-0.5 text-xs font-medium"
                onClick={() => setShowAddMenu(false)}
              >
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg text-text hover:bg-surface2 text-left"
                >
                  <Icon icon="solar:upload-bold-duotone" width={16} />
                  <span>Upload file</span>
                </button>
                <button
                  type="button"
                  onClick={() => setShowLinkImport(true)}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg text-text hover:bg-surface2 text-left"
                >
                  <Icon icon="solar:link-bold-duotone" width={16} />
                  <span>Download from link</span>
                </button>
                <button
                  type="button"
                  onClick={openCreate}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg text-text hover:bg-surface2 text-left"
                >
                  <Icon icon="solar:add-circle-bold-duotone" width={16} />
                  <span>New keyword</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── 2. Collapsible Keywords Panel ── */}
      {keywordsExpanded && (
        <section className="bg-surface border border-line rounded-[14px] p-5 space-y-3.5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold text-text">
                Keywords ({visibleKeywords.length}
                {visibleKeywords.length !== keywords.length ? ` of ${keywords.length}` : ""})
              </h2>
              {filterKeyword && (
                <button
                  onClick={() => setFilterKeyword("")}
                  className="inline-flex items-center gap-1 rounded-full bg-pri-soft text-pri-text px-2 py-0.5 text-[11px] font-semibold"
                >
                  Filtered: {filterKeyword}
                  <Icon icon="solar:close-circle-bold" width={12} />
                </button>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={keywordSearch}
                onChange={(e) => setKeywordSearch(e.target.value)}
                placeholder="Search keywords…"
                className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-44"
              />
              <button
                onClick={openBulkModal}
                className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs font-medium hover:bg-surface2 flex items-center gap-1.5"
              >
                <Icon icon="solar:code-bold-duotone" width={14} /> Bulk JSON
              </button>
              <button
                onClick={openCreate}
                className="h-8 px-2.5 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 flex items-center gap-1.5"
              >
                <Icon icon="solar:add-circle-bold" width={14} /> Add keyword
              </button>
            </div>
          </div>

          {/* Add / Edit keyword form */}
          {showForm && (
            <div className="p-4 rounded-xl border border-line bg-surface2 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-text">
                  {editingId ? "Edit Keyword" : "New Download Keyword"}
                </span>
                <button onClick={() => setShowForm(false)} className="text-text2 hover:text-text">
                  <Icon icon="solar:close-circle-bold" width={16} />
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
                <div>
                  <label className="block text-[11px] font-semibold text-text mb-1">Keyword</label>
                  <input
                    className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                    value={form.keyword}
                    onChange={(e) => setField("keyword", e.target.value)}
                    placeholder="marc marquez"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-text mb-1">Niche / Category</label>
                  <input
                    className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                    list="niche-options-list"
                    value={form.niche}
                    onChange={(e) => setField("niche", e.target.value)}
                    placeholder="MotoGP, F1, UFC…"
                  />
                  <datalist id="niche-options-list">
                    {niches.map((n) => (
                      <option key={n} value={n} />
                    ))}
                  </datalist>
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-text mb-1">Max Images / Run</label>
                  <input
                    type="number"
                    min={1}
                    max={200}
                    className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                    value={form.max_images}
                    onChange={(e) => setField("max_images", parseInt(e.target.value) || 50)}
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-text mb-1">Pages to Download</label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                    value={form.max_pages}
                    onChange={(e) => setField("max_pages", parseInt(e.target.value) || 10)}
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-text mb-1">Source Engine</label>
                  <select
                    className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                    value={form.source_engine}
                    onChange={(e) => setField("source_engine", e.target.value)}
                  >
                    <option value="9router">9Router (web-fetch)</option>
                    <option value="bing">Bing (legacy)</option>
                    <option value="google">Google (legacy)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-text mb-1">Min Width (px)</label>
                  <input
                    type="number"
                    min={50}
                    className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                    value={form.min_width}
                    onChange={(e) => setField("min_width", parseInt(e.target.value) || 200)}
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-semibold text-text mb-1">Min Height (px)</label>
                  <input
                    type="number"
                    min={50}
                    className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                    value={form.min_height}
                    onChange={(e) => setField("min_height", parseInt(e.target.value) || 200)}
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setShowForm(false)}
                  className="h-7 px-2.5 rounded-[6px] border border-line bg-surface text-text text-xs hover:bg-surface2"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSaveKeyword}
                  disabled={saving}
                  className="h-7 px-3 rounded-[6px] bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 disabled:opacity-50"
                >
                  {saving ? "Saving…" : editingId ? "Save Changes" : "Create Keyword"}
                </button>
              </div>
            </div>
          )}

          {/* Keywords table */}
          <div className="max-h-[380px] overflow-y-auto rounded-lg border border-line">
            <table className="w-full text-xs">
              <thead className="bg-surface2 border-b border-line sticky top-0 z-10 text-text2 font-semibold">
                <tr>
                  <th className="px-3.5 py-2 text-left">Keyword</th>
                  <th className="px-3 py-2 text-left">Niche</th>
                  <th className="px-3 py-2 text-left">Images</th>
                  <th className="px-3 py-2 text-left">Min Size</th>
                  <th className="px-3 py-2 text-left">Pages</th>
                  <th className="px-3 py-2 text-left">Last Download</th>
                  <th className="px-3 py-2 text-left">Next Event</th>
                  <th className="px-3 py-2 text-left">Status</th>
                  <th className="px-3 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60 bg-surface">
                {visibleKeywords.map((k) => (
                  <tr key={k.id} className="hover:bg-surface2/50 transition-colors">
                    <td className="px-3.5 py-2 font-medium text-text">
                      <button
                        onClick={() => setFilterKeyword(filterKeyword === k.keyword ? "" : k.keyword)}
                        className={`hover:underline text-left ${filterKeyword === k.keyword ? "text-pri-text font-bold" : ""}`}
                      >
                        {k.keyword}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-text2">
                      {k.niche ? (
                        <button
                          onClick={() => setFilterNiche(filterNiche === k.niche ? "" : (k.niche as string))}
                          className="px-1.5 py-0.5 rounded bg-surface2 text-text text-[11px] hover:border-line border border-transparent"
                        >
                          {k.niche}
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2 text-text2">{k.image_count}</td>
                    <td className="px-3 py-2 text-text2">{k.min_width}×{k.min_height}</td>
                    <td className="px-3 py-2 text-text2">{k.max_pages}</td>
                    <td className="px-3 py-2 text-text2">
                      {k.last_downloaded_at
                        ? formatDistanceToNowStrict(new Date(k.last_downloaded_at), { addSuffix: true })
                        : "never"}
                    </td>
                    <td className="px-3 py-2 text-text2">
                      {k.next_event_date ? (
                        <span>
                          {format(new Date(`${k.next_event_date}T00:00:00`), "d MMM")}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {!k.is_active ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-surface2 text-text2">
                          Inactive
                        </span>
                      ) : k.last_download_error ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-bad-soft text-bad" title={k.last_download_error}>
                          Error
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-pri-soft text-pri-text">
                          Active
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => handleDownloadNow(k.id)}
                          disabled={downloading === k.id}
                          title="Download now"
                          className="p-1 rounded text-text2 hover:text-text hover:bg-surface2"
                        >
                          {downloading === k.id ? (
                            <Icon icon="svg-spinners:ring-resize" width={14} />
                          ) : (
                            <Icon icon="solar:download-bold-duotone" width={14} />
                          )}
                        </button>
                        <button
                          onClick={() => updateGalleryKeyword(k.id, { is_active: !k.is_active }).then(() => mutateKeywords())}
                          title={k.is_active ? "Deactivate" : "Activate"}
                          className="p-1 rounded text-text2 hover:text-text hover:bg-surface2"
                        >
                          <Icon icon={k.is_active ? "solar:pause-circle-bold-duotone" : "solar:play-bold-duotone"} width={14} />
                        </button>
                        <button
                          onClick={() => openEdit(k)}
                          title="Edit"
                          className="p-1 rounded text-text2 hover:text-text hover:bg-surface2"
                        >
                          <Icon icon="solar:pen-bold-duotone" width={14} />
                        </button>
                        <button
                          onClick={() => setConfirmDeleteKeyword(k)}
                          title="Delete"
                          className="p-1 rounded text-text2 hover:text-bad hover:bg-bad-soft"
                        >
                          <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── 3. Main Content: Left Filters Aside + Right Photos Section ── */}
      <div className="flex flex-col md:flex-row gap-5 items-start">
        {/* Left Sidebar Filters */}
        <aside
          aria-label="Filters"
          className="w-full md:w-[240px] shrink-0 bg-surface border border-line rounded-[14px] p-3.5 flex flex-col gap-4"
        >
          {/* Free-text search input */}
          <input
            type="search"
            aria-label="Search photos"
            placeholder="Search photos"
            value={imageSearch}
            onChange={(e) => setImageSearch(e.target.value)}
            className="h-[38px] px-2.5 rounded-[9px] border border-line bg-surface text-text text-[13px] placeholder:text-text2 outline-none focus:border-pri"
          />

          {/* Niche filter pills */}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold tracking-wider uppercase text-text2">Niche</span>
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setFilterNiche("")}
                className={`h-[30px] px-2.5 rounded-[8px] border text-xs font-medium transition-colors ${
                  !filterNiche
                    ? "bg-pri-soft text-pri-text font-semibold border-pri/40"
                    : "bg-surface text-text border-line hover:bg-surface2"
                }`}
              >
                All
              </button>
              {niches.map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setFilterNiche(filterNiche === n ? "" : n)}
                  className={`h-[30px] px-2.5 rounded-[8px] border text-xs font-medium transition-colors ${
                    filterNiche === n
                      ? "bg-pri-soft text-pri-text font-semibold border-pri/40"
                      : "bg-surface text-text border-line hover:bg-surface2"
                  }`}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>

          {/* Keywords list */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold tracking-wider uppercase text-text2">Keywords</span>
              {filterKeyword && (
                <button
                  onClick={() => setFilterKeyword("")}
                  className="text-[11px] text-pri-text hover:underline"
                >
                  Reset
                </button>
              )}
            </div>
            <div className="max-h-52 overflow-y-auto space-y-0.5 divide-y divide-line/40 pr-1">
              {visibleKeywords.slice(0, 12).map((k) => (
                <button
                  key={k.id}
                  type="button"
                  onClick={() => setFilterKeyword(filterKeyword === k.keyword ? "" : k.keyword)}
                  className={`w-full flex items-center justify-between py-1 px-1 rounded text-[13px] text-left transition-colors ${
                    filterKeyword === k.keyword
                      ? "bg-pri-soft text-pri-text font-semibold"
                      : "text-text hover:bg-surface2"
                  }`}
                >
                  <span className="truncate">{k.keyword}</span>
                  <span
                    className={`text-xs ml-2 ${
                      k.last_download_error ? "text-bad font-semibold" : "text-text2"
                    }`}
                  >
                    {k.image_count}
                  </span>
                </button>
              ))}
              {visibleKeywords.length > 12 && (
                <button
                  onClick={() => setKeywordsExpanded(true)}
                  className="text-xs text-pri-text hover:underline pt-1 block"
                >
                  + {visibleKeywords.length - 12} more keywords…
                </button>
              )}
            </div>
          </div>

          {/* Labels filters */}
          <div className="flex flex-col gap-2.5">
            <span className="text-xs font-semibold tracking-wider uppercase text-text2">Labels</span>
            <label className="flex flex-col gap-1 text-[13px] text-text">
              People in photo
              <select
                value={labelPeopleFilter}
                onChange={(e) => setLabelPeopleFilter(e.target.value)}
                className="h-9 px-2 rounded-[9px] border border-line bg-surface text-text text-xs"
              >
                <option value="any">Any</option>
                <option value="1">1 person</option>
                <option value="2">2 people</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[13px] text-text">
              Shot
              <select
                value={labelShotFilter}
                onChange={(e) => setLabelShotFilter(e.target.value)}
                className="h-9 px-2 rounded-[9px] border border-line bg-surface text-text text-xs"
              >
                <option value="any">Any</option>
                <option value="portrait">Portrait</option>
                <option value="action">Action</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-[13px] text-text cursor-pointer pt-0.5">
              <input
                type="checkbox"
                checked={hideArchival}
                onChange={(e) => setHideArchival(e.target.checked)}
                className="w-4 h-4 rounded accent-pri border-line"
              />
              <span>Hide archival photos</span>
            </label>
          </div>

          {/* Auto-download status */}
          <div className="flex flex-col gap-1.5 pt-3 border-t border-line text-[13px]">
            <div className="flex items-center justify-between gap-2">
              <span className="text-text font-medium">Auto-download</span>
              <span
                className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                  paused ? "bg-warn-soft text-warn" : "bg-pri-soft text-pri-text"
                }`}
              >
                {paused ? "Paused" : "Running"}
              </span>
            </div>
            <span className="text-xs text-text2 leading-relaxed">
              Pause scraping stops the scheduled keyword downloads for every niche. Add photos and manual downloads still work.
            </span>
          </div>
        </aside>

        {/* Right Section: Photos Grid */}
        <section aria-label="Photos" className="flex-1 min-w-0 w-full flex flex-col gap-3">
          {images.length === 0 ? (
            <div className="bg-surface border border-line rounded-[14px] p-10 text-center text-text2">
              <Icon icon="solar:gallery-wide-bold-duotone" width={40} className="mx-auto mb-3 opacity-40" />
              <p className="text-sm">
                {keywords.length === 0
                  ? "No keywords yet. Add one and queue a download, or upload images manually."
                  : "No images match the current filters."}
              </p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
                {images.map((img, idx) => {
                  const attrs = img.attributes || {};
                  const labelsList: string[] = [];
                  if (attrs.people && attrs.people.length > 0) {
                    labelsList.push(attrs.people.length === 1 ? "1 person" : `${attrs.people.length} people`);
                  } else if (attrs.people_count) {
                    labelsList.push(attrs.people_count === 1 ? "1 person" : `${attrs.people_count} people`);
                  }
                  if (attrs.shot_type) labelsList.push(attrs.shot_type);

                  return (
                    <div key={img.id} className="flex flex-col gap-1.5 group">
                      <div className="relative aspect-[4/3] rounded-[10px] overflow-hidden bg-surface2 border border-line">
                        {/* Used badge */}
                        {img.is_used && (
                          <span className="absolute right-2 top-2 px-1.5 py-0.5 rounded-md bg-black/70 text-white text-[11px] font-semibold z-10">
                            Used
                          </span>
                        )}

                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={img.public_url}
                          alt={img.keyword}
                          className="w-full h-full object-cover cursor-zoom-in"
                          loading="lazy"
                          onClick={() => setLightboxIdx(idx)}
                        />

                        {/* Hover delete button */}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteImage(img);
                          }}
                          title="Delete image"
                          className="absolute bottom-2 right-2 p-1.5 rounded-lg bg-black/60 text-white opacity-0 group-hover:opacity-100 hover:bg-bad transition-all z-10"
                        >
                          <Icon icon="solar:trash-bin-trash-bold" width={14} />
                        </button>
                      </div>

                      <div className="px-0.5">
                        <span className="text-[13px] font-semibold text-text truncate block">
                          {img.keyword}
                        </span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {labelsList.map((l) => (
                            <span
                              key={l}
                              className="px-1.5 py-0.5 rounded-[6px] bg-surface2 text-text2 text-[11px]"
                            >
                              {l}
                            </span>
                          ))}
                          {img.extra_keywords?.map((tag) => (
                            <span
                              key={tag}
                              className="px-1.5 py-0.5 rounded-[6px] bg-surface2 text-text2 text-[11px] flex items-center gap-0.5"
                            >
                              {tag}
                              <button
                                onClick={() => removeTag(img, tag)}
                                className="hover:text-bad"
                                title={`Remove tag ${tag}`}
                              >
                                <Icon icon="solar:close-circle-bold" width={10} />
                              </button>
                            </span>
                          ))}
                          <button
                            onClick={() => openTagDialog(img)}
                            title="Add tag"
                            className="px-1 py-0.5 rounded-[6px] border border-dashed border-line text-text2 hover:text-pri-text text-[11px] inline-flex items-center gap-0.5"
                          >
                            <Icon icon="solar:add-circle-linear" width={10} /> tag
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Infinite-scroll sentinel + counter */}
              <div ref={sentinelRef} className="h-6" />
              <div className="text-center py-3 text-xs text-text2">
                {loadingMore ? (
                  <span className="inline-flex items-center gap-2">
                    <Icon icon="svg-spinners:180-ring" width={14} /> Loading more…
                  </span>
                ) : (
                  `Showing ${images.length} of ${total.toLocaleString()} · loads more as you scroll`
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {/* ── Lightbox ── */}
      {lightboxIdx !== null && images[lightboxIdx] && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm p-4"
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
              onClick={(e) => {
                e.stopPropagation();
                showPrev();
              }}
              title="Previous (←)"
              className="absolute left-4 p-2 rounded-full bg-white/10 text-white hover:bg-white/20"
            >
              <Icon icon="solar:alt-arrow-left-bold" width={28} />
            </button>
          )}
          {lightboxIdx < images.length - 1 && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                showNext();
              }}
              title="Next (→)"
              className="absolute right-4 p-2 rounded-full bg-white/10 text-white hover:bg-white/20"
            >
              <Icon icon="solar:alt-arrow-right-bold" width={28} />
            </button>
          )}

          <div
            className="max-w-[90vw] max-h-[90vh] flex flex-col items-center"
            onClick={(e) => e.stopPropagation()}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={images[lightboxIdx].public_url}
              alt={images[lightboxIdx].keyword}
              className="max-w-[90vw] max-h-[75vh] object-contain rounded-lg"
            />
            <div className="mt-3 text-center text-white/80 text-xs">
              <span className="font-semibold text-white">{images[lightboxIdx].keyword}</span>
              {images[lightboxIdx].extra_keywords.length > 0 && (
                <span className="text-white/60"> (+ {images[lightboxIdx].extra_keywords.join(", ")})</span>
              )}
              {" · "}{images[lightboxIdx].width}×{images[lightboxIdx].height}
              {" · "}{images[lightboxIdx].source_engine}
              {" · "}{lightboxIdx + 1}/{images.length}
            </div>

            <div className="mt-3 w-full max-w-xl rounded-xl bg-black/60 border border-white/20 p-4 text-left text-xs text-white/90">
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold text-white">Labels</span>
                <button
                  className="text-xs text-white/70 hover:text-white underline"
                  onClick={() => {
                    const attrs = images[lightboxIdx].attributes || {};
                    const event = attrs.event || {};
                    setLabelForm({
                      shot_type: attrs.shot_type || "",
                      session: event.session || "",
                      year: event.year ? String(event.year) : "",
                      event_name: event.event_name || "",
                      archival_photo: attrs.archival_photo == null ? "" : String(attrs.archival_photo),
                      people_count: attrs.people_count == null ? "" : String(attrs.people_count),
                      label: "",
                    });
                    setEditingLabels(true);
                  }}
                >
                  Edit
                </button>
              </div>

              {!editingLabels ? (
                <div className="grid grid-cols-2 gap-1 text-white/80">
                  <span>People: {(images[lightboxIdx].attributes.people || []).map((p: any) => p.name).join(", ") || "—"}</span>
                  <span>Shot: {images[lightboxIdx].attributes.shot_type || "—"}</span>
                  <span>Event: {images[lightboxIdx].attributes.event?.event_name || "—"}</span>
                  <span>Session: {images[lightboxIdx].attributes.event?.session || "—"}</span>
                  <span>Year: {images[lightboxIdx].attributes.event?.year || "—"}</span>
                  <span>Archival: {images[lightboxIdx].attributes.archival_photo == null ? "—" : String(images[lightboxIdx].attributes.archival_photo)}</span>
                </div>
              ) : (
                <div className="space-y-2 text-text">
                  <input
                    className="h-8 px-2.5 rounded border border-line bg-surface text-xs w-full text-text"
                    placeholder="Shot type"
                    value={labelForm.shot_type || ""}
                    onChange={(e) => setLabelForm({ ...labelForm, shot_type: e.target.value })}
                  />
                  <div className="grid grid-cols-2 gap-2">
                    {(["session", "event_name", "year", "people_count"] as const).map((field) => (
                      <input
                        key={field}
                        className="h-8 px-2.5 rounded border border-line bg-surface text-xs w-full text-text"
                        placeholder={field.replace("_", " ")}
                        value={labelForm[field] || ""}
                        onChange={(e) => setLabelForm({ ...labelForm, [field]: e.target.value })}
                      />
                    ))}
                  </div>
                  <select
                    className="h-8 px-2 rounded border border-line bg-surface text-xs w-full text-text"
                    value={labelForm.archival_photo || ""}
                    onChange={(e) => setLabelForm({ ...labelForm, archival_photo: e.target.value })}
                  >
                    <option value="">Archival photo</option>
                    <option value="true">Yes</option>
                    <option value="false">No</option>
                  </select>
                  <div className="flex justify-end gap-2 pt-1">
                    <button
                      className="h-7 px-2.5 rounded bg-surface2 text-text text-xs"
                      onClick={() => setEditingLabels(false)}
                    >
                      Cancel
                    </button>
                    <button
                      className="h-7 px-2.5 rounded bg-surface text-text text-xs border border-line"
                      onClick={() => saveLabels(true)}
                    >
                      Save + re-label with AI
                    </button>
                    <button
                      className="h-7 px-3 rounded bg-pri text-pri-fg text-xs font-semibold"
                      onClick={() => saveLabels(false)}
                    >
                      Save
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Link Import Modal ── */}
      {showLinkImport && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => !linkImporting && setShowLinkImport(false)}
        >
          <div className="bg-surface border border-line rounded-[14px] w-full max-w-lg p-5 space-y-3.5" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h2 className="text-base font-bold text-text">Download from Link</h2>
              <button onClick={() => setShowLinkImport(false)} className="text-text2 hover:text-text">
                <Icon icon="solar:close-circle-bold" width={20} />
              </button>
            </div>
            <textarea
              className="w-full min-h-[120px] p-2.5 rounded-[10px] border border-line bg-surface text-text text-xs font-mono"
              value={linkText}
              onChange={(e) => setLinkText(e.target.value)}
              placeholder="one link per line, max 20 — Getty page links or direct image links"
            />
            <input
              className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
              value={linkKeyword}
              onChange={(e) => setLinkKeyword(e.target.value)}
              placeholder="Keyword (optional; auto-detect Getty caption)"
            />
            <div className="grid grid-cols-2 gap-2">
              <input
                className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs"
                value={linkHints.people}
                onChange={(e) => setLinkHints({ ...linkHints, people: e.target.value })}
                placeholder="People (comma separated)"
              />
              <input
                className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs"
                value={linkHints.session}
                onChange={(e) => setLinkHints({ ...linkHints, session: e.target.value })}
                placeholder="Session"
              />
              <input
                className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs"
                value={linkHints.event_name}
                onChange={(e) => setLinkHints({ ...linkHints, event_name: e.target.value })}
                placeholder="Event name"
              />
              <input
                className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs"
                value={linkHints.year}
                onChange={(e) => setLinkHints({ ...linkHints, year: e.target.value })}
                placeholder="Year"
              />
              <input
                className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs col-span-2"
                value={linkHints.note}
                onChange={(e) => setLinkHints({ ...linkHints, note: e.target.value })}
                placeholder="Owner note (optional)"
                maxLength={300}
              />
            </div>
            {linkResults.length > 0 && (
              <div className="max-h-36 overflow-auto space-y-1 text-xs">
                {linkResults.map((r) => (
                  <div key={r.url} className="flex justify-between gap-2">
                    <span className="truncate">{r.url}</span>
                    <span className={r.status === "saved" ? "text-pri-text" : "text-bad"}>
                      {r.status}{r.reason ? `: ${r.reason}` : ""}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2 border-t border-line">
              <button
                className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text text-xs"
                onClick={() => setShowLinkImport(false)}
                disabled={linkImporting}
              >
                Close
              </button>
              <button
                className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold disabled:opacity-50"
                onClick={handleLinkImport}
                disabled={linkImporting}
              >
                {linkImporting ? "Importing…" : "Download"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Bulk Import Modal ── */}
      {showBulkModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setShowBulkModal(false)}
        >
          <div
            className="bg-surface border border-line rounded-[14px] w-full max-w-3xl max-h-[85vh] flex flex-col p-5 space-y-3.5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-text">Bulk Edit / Import Keywords</h2>
                <p className="text-[11px] text-text2 mt-0.5">
                  Edit JSON directly or load from file. Matching by keyword.
                </p>
              </div>
              <button onClick={() => setShowBulkModal(false)} className="text-text2 hover:text-text">
                <Icon icon="solar:close-circle-bold" width={20} />
              </button>
            </div>

            <textarea
              className="w-full flex-1 min-h-[300px] p-2.5 rounded-[10px] border border-line bg-surface text-text text-xs font-mono resize-none"
              spellCheck={false}
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
            />

            {bulkResult && (
              <div className="rounded-lg bg-surface2 px-3 py-2 text-xs text-text">
                {bulkResult.created} created, {bulkResult.updated} updated
                {bulkResult.errors.length > 0 && (
                  <span className="text-bad"> — {bulkResult.errors.join("; ")}</span>
                )}
              </div>
            )}

            <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
              <div className="flex gap-2">
                <input ref={bulkFileRef} type="file" accept="application/json,.json" className="hidden" onChange={handleBulkFile} />
                <button
                  onClick={() => bulkFileRef.current?.click()}
                  className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs flex items-center gap-1.5"
                >
                  <Icon icon="solar:folder-open-bold-duotone" width={14} /> Load file
                </button>
                <button
                  onClick={handleBulkDownload}
                  className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs flex items-center gap-1.5"
                >
                  <Icon icon="solar:download-bold-duotone" width={14} /> Download .json
                </button>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => setShowBulkModal(false)}
                  className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text text-xs"
                >
                  Close
                </button>
                <button
                  onClick={handleBulkSave}
                  disabled={bulkSaving}
                  className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold disabled:opacity-50"
                >
                  {bulkSaving ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── AI Filter Setup Modal ── */}
      {showAIFilterSetup && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => !aiFilterScanning && setShowAIFilterSetup(false)}
        >
          <div className="bg-surface border border-line rounded-[14px] w-full max-w-lg p-5 space-y-3.5" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-text">Run AI Filter</h2>
                <p className="text-[11px] text-text2 mt-0.5">
                  Nothing is deleted automatically — review and confirm candidates first.
                </p>
              </div>
              <button
                onClick={() => !aiFilterScanning && setShowAIFilterSetup(false)}
                className="text-text2 hover:text-text"
              >
                <Icon icon="solar:close-circle-bold" width={20} />
              </button>
            </div>

            <div>
              <label className="block text-xs font-semibold text-text mb-1">Filter Criteria</label>
              <textarea
                className="w-full min-h-[80px] p-2.5 rounded-[10px] border border-line bg-surface text-text text-xs"
                rows={3}
                value={aiFilterCriteria}
                onChange={(e) => setAIFilterCriteria(e.target.value)}
                placeholder="e.g. close-up headshot of a person's face, not full-body/action/crowd shots"
                disabled={aiFilterScanning}
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-text mb-1">Scope</label>
              <select
                className="h-9 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs w-full"
                value={aiFilterScope}
                onChange={(e) => setAIFilterScope(e.target.value)}
                disabled={aiFilterScanning}
              >
                <option value="">All keywords (entire gallery)</option>
                {keywords.map((k) => (
                  <option key={k.id} value={k.keyword}>
                    {k.keyword}
                  </option>
                ))}
              </select>
            </div>

            {aiFilterScanning && (
              <div className="rounded-lg bg-surface2 px-3 py-2 text-xs text-text flex items-center gap-2">
                <Icon icon="svg-spinners:ring-resize" width={14} />
                {aiFilterProgress
                  ? `Scanning ${aiFilterProgress.done} of ${aiFilterProgress.total} images…`
                  : "Starting scan…"}
              </div>
            )}

            <div className="flex justify-end gap-2 border-t border-line pt-3">
              <button
                onClick={() => setShowAIFilterSetup(false)}
                disabled={aiFilterScanning}
                className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text text-xs"
              >
                Cancel
              </button>
              <button
                onClick={handleStartAIFilterScan}
                disabled={aiFilterScanning || !aiFilterCriteria.trim()}
                className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold disabled:opacity-50"
              >
                {aiFilterScanning ? "Scanning…" : "Scan"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── AI Filter Review Modal ── */}
      {filterReview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => !filterConfirming && setFilterReview(null)}
        >
          <div
            className="bg-surface border border-line rounded-[14px] w-full max-w-3xl max-h-[85vh] flex flex-col p-5 space-y-3.5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-bold text-text">
                  AI Filter Review — {filterReview.scopeLabel}
                </h2>
                <p className="text-[11px] text-text2 mt-0.5">
                  {filterReview.candidates.length} image(s) did NOT match criteria. Checked images will be deleted.
                </p>
              </div>
              <button
                onClick={() => !filterConfirming && setFilterReview(null)}
                className="text-text2 hover:text-text"
              >
                <Icon icon="solar:close-circle-bold" width={20} />
              </button>
            </div>

            <div className="flex items-center gap-3 text-xs">
              <button
                onClick={() =>
                  setFilterReview((prev) =>
                    prev ? { ...prev, selected: new Set(prev.candidates.map((c) => c.id)) } : prev
                  )
                }
                className="text-pri-text hover:underline font-semibold"
              >
                Select all
              </button>
              <button
                onClick={() => setFilterReview((prev) => (prev ? { ...prev, selected: new Set() } : prev))}
                className="text-pri-text hover:underline font-semibold"
              >
                Select none
              </button>
              <span className="text-text2">
                {filterReview.selected.size} of {filterReview.candidates.length} selected for deletion
              </span>
            </div>

            <div className="flex-1 overflow-y-auto">
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                {filterReview.candidates.map((c) => {
                  const checked = filterReview.selected.has(c.id);
                  return (
                    <label
                      key={c.id}
                      className={`relative cursor-pointer rounded-lg overflow-hidden border-2 transition-colors ${
                        checked ? "border-bad" : "border-transparent"
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="absolute top-1.5 left-1.5 z-10 w-4 h-4 accent-bad"
                        checked={checked}
                        onChange={() => toggleFilterCandidate(c.id)}
                      />
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={c.public_url} alt="" className="w-full h-28 object-cover" loading="lazy" />
                      <div className={`absolute inset-0 pointer-events-none ${checked ? "bg-bad/15" : ""}`} />
                      <span className="absolute bottom-1 left-1.5 text-[10px] font-medium text-white bg-black/60 rounded px-1 py-0.5 truncate max-w-[70%]">
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

            <div className="flex justify-end gap-2 border-t border-line pt-3">
              <button
                onClick={() => setFilterReview(null)}
                disabled={filterConfirming}
                className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text text-xs"
              >
                Cancel
              </button>
              <button
                onClick={confirmFilterDelete}
                disabled={filterConfirming || filterReview.selected.size === 0}
                className="h-8 px-3 rounded-[8px] bg-bad text-white text-xs font-semibold disabled:opacity-50"
              >
                {filterConfirming ? "Deleting…" : `Delete Selected (${filterReview.selected.size})`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Dialogs ── */}
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
        message="The file is removed and this photo will not be downloaded again for its keyword."
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
