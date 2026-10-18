"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Icon } from "@iconify/react";
import {
  getFanpage,
  updateFanpage,
  addIGSource,
  removeIGSourceByUsername,
  previewCaption,
  listNewsSources,
  listGalleryNiches,
  listTemplates,
  addFanpageNewsSource,
  removeFanpageNewsSource,
  previewNewsCopy,
  uploadWatermarkImage,
  deleteWatermarkImage,
  addDiscussionTopic,
  updateDiscussionTopic,
  deleteDiscussionTopic,
  listDiscussionContentIdeas,
  createDiscussionContentIdea,
  updateDiscussionContentIdea,
  deleteDiscussionContentIdea,
  addPinterestSource,
  updatePinterestSource,
  deletePinterestSource,
  listPinterestContentIdeas,
  updatePinterestContentIdea,
  deletePinterestContentIdea,
  addFacebookPhotoSource,
  updateFacebookPhotoSource,
  deleteFacebookPhotoSource,
  listFacebookPhotoIdeas,
  updateFacebookPhotoIdea,
  deleteFacebookPhotoIdea,
  listRadarNiches,
  listAiVideoTemplates,
  getAiEngineStatus,
} from "@/lib/api";
import { getBrand, setBrandGptcli, listReplizAccounts } from "@/lib/socialBrandsApi";
import type { BrandDetailOut, ReplizAccountsOut } from "@/lib/socialBrandsApi";
import type {
  Fanpage,
  FanpageDetail,
  DiscussionTopicRef,
  DiscussionContentIdeaRef,
  PinterestSourceRef,
  PinterestContentIdeaRef,
  FacebookPhotoSourceRef,
  FacebookPhotoIdeaRef,
  AiVideoTemplate,
} from "@/lib/types";
import { ModeChip } from "@/components/ui/ModeChip";
import { SettingsTabs, type SettingsTab, type SettingsTabId } from "@/components/fanpages/SettingsTabs";
import { TARGET_COUNTRIES, isValidTz } from "@/components/fanpages/tabs/GlobalTab";
import { ContentTab } from "@/components/fanpages/tabs/ContentTab";
import { ChannelsTab } from "@/components/fanpages/tabs/ChannelsTab";
import { PacingTab } from "@/components/fanpages/tabs/PacingTab";
import { CaptionsTab } from "@/components/fanpages/tabs/CaptionsTab";
import { DesignTab } from "@/components/fanpages/tabs/DesignTab";
import { IdeasTab } from "@/components/fanpages/tabs/IdeasTab";
import { useSidebar } from "@/contexts/SidebarContext";

const fetcher = (id: number) => getFanpage(id).then((r) => r.data as FanpageDetail);

function getInitials(name: string): string {
  if (!name) return "??";
  const words = name.trim().split(/\s+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

const VALID_TABS: Record<string, SettingsTabId> = {
  content: "content",
  channels: "channels",
  pacing: "pacing",
  captions: "captions",
  design: "design",
  ideas: "ideas",
  // Old tab keys map to content:
  global: "content",
  ig: "content",
  news: "content",
  discussion: "content",
  pinterest: "content",
  facebook: "content",
  youtube: "content",
  aivideo: "content",
  radar: "content",
};

export default function BrandEditPage() {
  const { id } = useParams<{ id: string }>();
  const fanpageId = parseInt(id, 10);
  const router = useRouter();
  const searchParams = useSearchParams();
  const { collapsed } = useSidebar();

  const { data: fp, mutate } = useSWR(`fanpage-${fanpageId}`, () => fetcher(fanpageId));
  const { data: aiVideoTemplates = [] } = useSWR("ai-video-templates", () =>
    listAiVideoTemplates().then((r) => r.data as AiVideoTemplate[])
  );
  const { data: radarNichesData } = useSWR("radar-niches", () =>
    listRadarNiches().then((r) => r.data as string[])
  );
  const { data: brand, mutate: mutateBrand } = useSWR<BrandDetailOut>(
    fanpageId ? ["social-brand", fanpageId] : null,
    () => getBrand(fanpageId)
  );
  const { data: replizData } = useSWR<ReplizAccountsOut>(
    "repliz-accounts",
    listReplizAccounts
  );
  const [avatarErr, setAvatarErr] = useState(false);
  const brandReplizPicture = useMemo(() => {
    if (!fp?.repliz_account_id || !replizData?.accounts) return null;
    return (
      replizData.accounts.find((a) => a.id === fp.repliz_account_id)?.picture ||
      null
    );
  }, [fp?.repliz_account_id, replizData]);
  const { mutate: mutateFanpageSocial } = useSWR<Fanpage>(
    fanpageId ? ["fanpage", fanpageId] : null,
    () => getFanpage(fanpageId).then((r) => r.data as Fanpage)
  );
  const { data: aiEngineStatus } = useSWR(
    "ai-engine-status",
    () => getAiEngineStatus().then((r) => r.data),
    { revalidateOnFocus: false }
  );

  const [form, setForm] = useState<Partial<FanpageDetail>>({});
  const [saving, setSaving] = useState(false);
  const [activeSaving, setActiveSaving] = useState(false);
  const [mode1Saving, setMode1Saving] = useState(false);

  // Tab selection
  const queryTab = searchParams.get("tab");
  const initialTab: SettingsTabId = (queryTab && VALID_TABS[queryTab]) || "content";
  const [activeTab, setActiveTab] = useState<SettingsTabId>(initialTab);

  useEffect(() => {
    if (queryTab && VALID_TABS[queryTab] && VALID_TABS[queryTab] !== activeTab) {
      setActiveTab(VALID_TABS[queryTab]);
    }
  }, [queryTab, activeTab]);

  function selectTab(newTabId: SettingsTabId) {
    setActiveTab(newTabId);
    router.replace(`/brands/${fanpageId}?tab=${newTabId}`, { scroll: false });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const handleToggleGptcli = async (enabled: boolean) => {
    if (!brand) return;
    await mutateBrand({ ...brand, gptcli_enabled_for_brand: enabled }, false);
    try {
      await setBrandGptcli(fanpageId, enabled);
      mutateBrand();
    } catch (err) {
      mutateBrand();
      throw err;
    }
  };

  const handleRevalidateSocial = () => {
    mutateBrand();
    mutateFanpageSocial();
    mutate();
  };

  const hasEdited = useRef(false);
  const [newSource, setNewSource] = useState("");
  const [wmUploading, setWmUploading] = useState(false);

  async function handleWatermarkUpload(file: File) {
    setWmUploading(true);
    try {
      await uploadWatermarkImage(fanpageId, file);
      await mutate();
    } catch (ex: any) {
      alert(`Upload failed: ${ex.response?.data?.detail ?? "make sure the file is a valid image."}`);
    } finally {
      setWmUploading(false);
    }
  }

  async function handleWatermarkDelete() {
    setWmUploading(true);
    try {
      await deleteWatermarkImage(fanpageId);
      await mutate();
    } finally {
      setWmUploading(false);
    }
  }

  const [tzInput, setTzInput] = useState("");
  const [tzMsg, setTzMsg] = useState("");

  function handleTimezoneChange(newTz: string) {
    if (!newTz || !isValidTz(newTz)) return;
    const oldTz = form.timezone || "Europe/London";
    set("timezone", newTz);

    if (form.publish_sleep_start_hour != null && form.publish_sleep_end_hour != null) {
      try {
        const d = new Date();
        const t1 = new Date(d.toLocaleString("en-US", { timeZone: oldTz }));
        const t2 = new Date(d.toLocaleString("en-US", { timeZone: newTz }));
        const diffHours = Math.round((t2.getTime() - t1.getTime()) / (1000 * 60 * 60));

        if (diffHours !== 0) {
          const newStart = (form.publish_sleep_start_hour + diffHours + 24) % 24;
          const newEnd = (form.publish_sleep_end_hour + diffHours + 24) % 24;
          set("publish_sleep_start_hour", newStart);
          set("publish_sleep_end_hour", newEnd);
          setTzMsg(`Sleep window shifted by ${diffHours > 0 ? "+" : ""}${diffHours}h to maintain absolute time.`);
          setTimeout(() => setTzMsg(""), 5000);
        }
      } catch {}
    }
  }

  function handleCountryChange(newCode: string) {
    set("target_country", newCode || null);
    if (newCode) {
      const target = TARGET_COUNTRIES.find((c) => c.code === newCode);
      if (target && target.defaultTz && target.defaultTz !== form.timezone) {
        setTzInput(target.defaultTz);
        handleTimezoneChange(target.defaultTz);
      }
    }
  }

  const [previewSrc, setPreviewSrc] = useState("");
  const [previewOrig, setPreviewOrig] = useState("");
  const [previewResult, setPreviewResult] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [mustIncludeInput, setMustIncludeInput] = useState("");
  const [mustAvoidInput, setMustAvoidInput] = useState("");

  // Mode 2 (News) state
  const { data: allNewsSources = [] } = useSWR<{ id: number; name: string; category_url: string }[]>(
    "news-sources-picker",
    () => listNewsSources().then((r) => r.data as { id: number; name: string; category_url: string }[])
  );
  const { data: allGalleryNiches = [] } = useSWR<string[]>(
    "gallery-niches-picker",
    () => listGalleryNiches().then((r) => r.data as string[])
  );
  type TemplateRef = { id: number; name: string; is_default: boolean; category: "quote" | "news" | "discussion" | null; canvas_width: number; canvas_height: number };
  const { data: allTemplates = [] } = useSWR<TemplateRef[]>(
    `templates-picker-${fanpageId}`,
    () => listTemplates(fanpageId).then((r) => r.data as TemplateRef[])
  );
  const [newsPreviewTitle, setNewsPreviewTitle] = useState("");
  const [newsPreviewContent, setNewsPreviewContent] = useState("");
  const [newsPreviewResult, setNewsPreviewResult] = useState<{ title: string; caption: string } | null>(null);
  const [newsPreviewLoading, setNewsPreviewLoading] = useState(false);

  async function toggleNewsSource(sourceId: number, subscribed: boolean) {
    if (subscribed) await removeFanpageNewsSource(fanpageId, sourceId);
    else await addFanpageNewsSource(fanpageId, sourceId);
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, news_sources: fresh.news_sources }));
  }

  async function handleNewsPreview() {
    setNewsPreviewLoading(true);
    setNewsPreviewResult(null);
    try {
      const res = await previewNewsCopy(fanpageId, { title: newsPreviewTitle, content: newsPreviewContent });
      setNewsPreviewResult(res.data);
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      alert(`Preview failed: ${e.response?.data?.detail ?? "unknown error"}`);
    } finally {
      setNewsPreviewLoading(false);
    }
  }

  // Mode 4 (Discussion) topics state
  const [newTopicSeed, setNewTopicSeed] = useState("");
  const [newTopicSubject, setNewTopicSubject] = useState("");
  const [topicSaving, setTopicSaving] = useState(false);

  async function handleAddTopic() {
    if (!newTopicSeed.trim()) return;
    setTopicSaving(true);
    try {
      await addDiscussionTopic(fanpageId, { seed_text: newTopicSeed.trim(), subject_hint: newTopicSubject.trim() || undefined });
      setNewTopicSeed("");
      setNewTopicSubject("");
      const fresh = await mutate();
      if (fresh) setForm((prev) => ({ ...prev, discussion_topics: fresh.discussion_topics }));
    } finally {
      setTopicSaving(false);
    }
  }

  async function handleToggleTopic(t: DiscussionTopicRef) {
    await updateDiscussionTopic(fanpageId, t.id, { is_active: !t.is_active });
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, discussion_topics: fresh.discussion_topics }));
  }

  async function handleDeleteTopic(topicId: number) {
    await deleteDiscussionTopic(fanpageId, topicId);
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, discussion_topics: fresh.discussion_topics }));
  }

  // Mode 4 Ideas
  const [newDiscSeed, setNewDiscSeed] = useState("");
  const [newDiscSubjectHint, setNewDiscSubjectHint] = useState("");
  const [discIdeaSaving, setDiscIdeaSaving] = useState(false);
  const [editingDiscIdeaId, setEditingDiscIdeaId] = useState<number | null>(null);
  const [editDiscQuestion, setEditDiscQuestion] = useState("");
  const [editDiscSubject, setEditDiscSubject] = useState("");
  const [editDiscCaption, setEditDiscCaption] = useState("");
  const [editDiscLabel, setEditDiscLabel] = useState("DISCUSSION");
  const [discIdeas, setDiscIdeas] = useState<DiscussionContentIdeaRef[]>([]);
  const [discIdeasHasMore, setDiscIdeasHasMore] = useState(false);
  const [discIdeasLoadingMore, setDiscIdeasLoadingMore] = useState(false);

  const loadDiscIdeasFirstPage = useCallback(async () => {
    const res = await listDiscussionContentIdeas(fanpageId, { status: "pending" });
    const data = res.data as { items: DiscussionContentIdeaRef[]; has_more: boolean };
    setDiscIdeas(data.items);
    setDiscIdeasHasMore(data.has_more);
  }, [fanpageId]);

  async function handleLoadMoreDiscIdeas() {
    setDiscIdeasLoadingMore(true);
    try {
      const res = await listDiscussionContentIdeas(fanpageId, { status: "pending", offset: discIdeas.length });
      const data = res.data as { items: DiscussionContentIdeaRef[]; has_more: boolean };
      setDiscIdeas((prev) => [...prev, ...data.items]);
      setDiscIdeasHasMore(data.has_more);
    } finally {
      setDiscIdeasLoadingMore(false);
    }
  }

  useEffect(() => {
    loadDiscIdeasFirstPage();
  }, [loadDiscIdeasFirstPage]);

  async function handleAddDiscIdea() {
    if (!newDiscSeed.trim()) return;
    setDiscIdeaSaving(true);
    try {
      const created = await createDiscussionContentIdea(fanpageId, {
        seed_text: newDiscSeed.trim(),
        subject_hint: newDiscSubjectHint.trim() || undefined,
      });
      setDiscIdeas((prev) => [created.data as DiscussionContentIdeaRef, ...prev]);
      setNewDiscSeed("");
      setNewDiscSubjectHint("");
    } finally {
      setDiscIdeaSaving(false);
    }
  }

  function startEditDiscIdea(idea: DiscussionContentIdeaRef) {
    setEditingDiscIdeaId(idea.id);
    setEditDiscQuestion(idea.question);
    setEditDiscSubject(idea.subject_name);
    setEditDiscCaption(idea.caption);
    setEditDiscLabel(idea.label);
  }

  async function handleSaveDiscIdea(ideaId: number) {
    const updated = await updateDiscussionContentIdea(fanpageId, ideaId, {
      label: editDiscLabel,
      question: editDiscQuestion.trim(),
      subject_name: editDiscSubject.trim(),
      caption: editDiscCaption.trim(),
    });
    setEditingDiscIdeaId(null);
    setDiscIdeas((prev) => prev.map((i) => (i.id === ideaId ? (updated.data as DiscussionContentIdeaRef) : i)));
  }

  async function handleDeleteDiscIdea(ideaId: number) {
    await deleteDiscussionContentIdea(fanpageId, ideaId);
    setDiscIdeas((prev) => prev.filter((i) => i.id !== ideaId));
  }

  // Mode 5 (Pinterest) sources & ideas
  const [newPinUrl, setNewPinUrl] = useState("");
  const [newPinLabel, setNewPinLabel] = useState("");
  const [pinSourceSaving, setPinSourceSaving] = useState(false);
  const [editingIdeaId, setEditingIdeaId] = useState<number | null>(null);
  const [editIdeaTitle, setEditIdeaTitle] = useState("");
  const [editIdeaDescription, setEditIdeaDescription] = useState("");
  const [ideas, setIdeas] = useState<PinterestContentIdeaRef[]>([]);
  const [ideasHasMore, setIdeasHasMore] = useState(false);
  const [ideasLoadingMore, setIdeasLoadingMore] = useState(false);

  const loadIdeasFirstPage = useCallback(async () => {
    const res = await listPinterestContentIdeas(fanpageId, { status: "pending" });
    const data = res.data as { items: PinterestContentIdeaRef[]; has_more: boolean };
    setIdeas(data.items);
    setIdeasHasMore(data.has_more);
  }, [fanpageId]);

  async function handleLoadMoreIdeas() {
    setIdeasLoadingMore(true);
    try {
      const res = await listPinterestContentIdeas(fanpageId, { status: "pending", offset: ideas.length });
      const data = res.data as { items: PinterestContentIdeaRef[]; has_more: boolean };
      setIdeas((prev) => [...prev, ...data.items]);
      setIdeasHasMore(data.has_more);
    } finally {
      setIdeasLoadingMore(false);
    }
  }

  useEffect(() => {
    loadIdeasFirstPage();
  }, [loadIdeasFirstPage]);

  async function handleAddPinSource() {
    if (!newPinUrl.trim()) return;
    setPinSourceSaving(true);
    try {
      await addPinterestSource(fanpageId, { source_url: newPinUrl.trim(), label: newPinLabel.trim() || undefined });
      setNewPinUrl("");
      setNewPinLabel("");
      const fresh = await mutate();
      if (fresh) setForm((prev) => ({ ...prev, pinterest_sources: fresh.pinterest_sources }));
    } finally {
      setPinSourceSaving(false);
    }
  }

  async function handleTogglePinSource(s: PinterestSourceRef) {
    await updatePinterestSource(fanpageId, s.id, { is_active: !s.is_active });
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, pinterest_sources: fresh.pinterest_sources }));
  }

  async function handleDeletePinSource(sourceId: number) {
    await deletePinterestSource(fanpageId, sourceId);
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, pinterest_sources: fresh.pinterest_sources }));
  }

  function startEditIdea(idea: PinterestContentIdeaRef) {
    setEditingIdeaId(idea.id);
    setEditIdeaTitle(idea.title);
    setEditIdeaDescription(idea.description);
  }

  async function handleSaveIdea(ideaId: number) {
    const updated = await updatePinterestContentIdea(fanpageId, ideaId, {
      title: editIdeaTitle.trim(),
      description: editIdeaDescription.trim(),
    });
    setEditingIdeaId(null);
    setIdeas((prev) => prev.map((i) => (i.id === ideaId ? (updated.data as PinterestContentIdeaRef) : i)));
  }

  async function handleDeleteIdea(ideaId: number) {
    await deletePinterestContentIdea(fanpageId, ideaId);
    setIdeas((prev) => prev.filter((i) => i.id !== ideaId));
  }

  // Mode 6 (FB Photo recreate) sources & ideas
  const [newFbPhotoUrl, setNewFbPhotoUrl] = useState("");
  const [newFbPhotoLabel, setNewFbPhotoLabel] = useState("");
  const [fbPhotoSourceSaving, setFbPhotoSourceSaving] = useState(false);
  const [editingFbIdeaId, setEditingFbIdeaId] = useState<number | null>(null);
  const [editFbIdeaTitle, setEditFbIdeaTitle] = useState("");
  const [editFbIdeaSubtitle, setEditFbIdeaSubtitle] = useState("");
  const [editFbIdeaCaption, setEditFbIdeaCaption] = useState("");
  const [fbPhotoIdeas, setFbPhotoIdeas] = useState<FacebookPhotoIdeaRef[]>([]);
  const [fbPhotoIdeasHasMore, setFbPhotoIdeasHasMore] = useState(false);
  const [fbPhotoIdeasLoadingMore, setFbPhotoIdeasLoadingMore] = useState(false);

  const loadFbPhotoIdeasFirstPage = useCallback(async () => {
    const res = await listFacebookPhotoIdeas(fanpageId, { status: "pending" });
    const data = res.data as { items: FacebookPhotoIdeaRef[]; has_more: boolean };
    setFbPhotoIdeas(data.items);
    setFbPhotoIdeasHasMore(data.has_more);
  }, [fanpageId]);

  async function handleLoadMoreFbPhotoIdeas() {
    setFbPhotoIdeasLoadingMore(true);
    try {
      const res = await listFacebookPhotoIdeas(fanpageId, { status: "pending", offset: fbPhotoIdeas.length });
      const data = res.data as { items: FacebookPhotoIdeaRef[]; has_more: boolean };
      setFbPhotoIdeas((prev) => [...prev, ...data.items]);
      setFbPhotoIdeasHasMore(data.has_more);
    } finally {
      setFbPhotoIdeasLoadingMore(false);
    }
  }

  useEffect(() => {
    loadFbPhotoIdeasFirstPage();
  }, [loadFbPhotoIdeasFirstPage]);

  async function handleAddFbPhotoSource() {
    if (!newFbPhotoUrl.trim()) return;
    setFbPhotoSourceSaving(true);
    try {
      await addFacebookPhotoSource(fanpageId, { page_url: newFbPhotoUrl.trim(), label: newFbPhotoLabel.trim() || undefined });
      setNewFbPhotoUrl("");
      setNewFbPhotoLabel("");
      const fresh = await mutate();
      if (fresh) setForm((prev) => ({ ...prev, facebook_photo_sources: fresh.facebook_photo_sources }));
    } finally {
      setFbPhotoSourceSaving(false);
    }
  }

  async function handleToggleFbPhotoSource(s: FacebookPhotoSourceRef) {
    await updateFacebookPhotoSource(fanpageId, s.id, { is_active: !s.is_active });
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, facebook_photo_sources: fresh.facebook_photo_sources }));
  }

  async function handleDeleteFbPhotoSource(sourceId: number) {
    await deleteFacebookPhotoSource(fanpageId, sourceId);
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, facebook_photo_sources: fresh.facebook_photo_sources }));
  }

  function startEditFbIdea(idea: FacebookPhotoIdeaRef) {
    setEditingFbIdeaId(idea.id);
    setEditFbIdeaTitle(idea.design_title);
    setEditFbIdeaSubtitle(idea.design_subtitle || "");
    setEditFbIdeaCaption(idea.design_caption || "");
  }

  async function handleSaveFbIdea(ideaId: number) {
    const updated = await updateFacebookPhotoIdea(fanpageId, ideaId, {
      design_title: editFbIdeaTitle.trim(),
      design_subtitle: editFbIdeaSubtitle.trim(),
      design_caption: editFbIdeaCaption.trim(),
    });
    setEditingFbIdeaId(null);
    setFbPhotoIdeas((prev) => prev.map((i) => (i.id === ideaId ? (updated.data as FacebookPhotoIdeaRef) : i)));
  }

  async function handleDeleteFbIdea(ideaId: number) {
    await deleteFacebookPhotoIdea(fanpageId, ideaId);
    setFbPhotoIdeas((prev) => prev.filter((i) => i.id !== ideaId));
  }

  useEffect(() => {
    if (fp && !hasEdited.current) {
      setForm({ ...fp });
      setTzInput(fp.timezone || "Europe/London");
    }
  }, [fp]);

  function set(key: string, value: unknown) {
    hasEdited.current = true;
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const payload = { ...form, mode2_caption_language: form.caption_language };
      delete payload.accent_color; delete payload.accent_secondary_color;
      delete payload.badge_color; delete payload.badge_text_color;
      delete payload.quote_mark_color; delete payload.quote_streak;
      delete payload.brand_override; delete payload.template_style;
      delete payload.default_news_template_id; delete payload.default_quote_template_id;
      delete payload.default_discussion_template_id;
      await updateFanpage(fanpageId, payload);
      hasEdited.current = false;
      mutate(undefined, { revalidate: true });
    } catch {
      alert("Save failed. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActive() {
    if (!fp) return;
    setActiveSaving(true);
    try {
      await updateFanpage(fp.id, { is_active: !fp.is_active });
      const fresh = await mutate(undefined, { revalidate: true });
      if (fresh) setForm((prev) => ({ ...prev, is_active: fresh.is_active }));
      mutateBrand();
      mutateFanpageSocial();
    } catch {
      alert("Failed to update status. Please try again.");
    } finally {
      setActiveSaving(false);
    }
  }

  async function handleMode1Toggle(value: boolean) {
    setForm((prev) => ({ ...prev, mode1_ig_repost_enabled: value }));
    setMode1Saving(true);
    try {
      await updateFanpage(fanpageId, { mode1_ig_repost_enabled: value });
      const fresh = await mutate();
      if (fresh) setForm((prev) => ({ ...prev, mode1_ig_repost_enabled: fresh.mode1_ig_repost_enabled }));
    } catch {
      setForm((prev) => ({ ...prev, mode1_ig_repost_enabled: !value }));
      alert("Failed to update IG Mode. Please try again.");
    } finally {
      setMode1Saving(false);
    }
  }

  function handleIgModeChange(mode: "repost" | "recreate_template" | "recreate_chatgpt") {
    if (mode === "repost") {
      set("ig_recreate_enabled", false);
    } else {
      set("ig_recreate_enabled", true);
      set("visual_engine", mode === "recreate_chatgpt" ? "chatgpt" : "off");
    }
  }

  async function handleAddSource() {
    if (!newSource.trim()) return;
    await addIGSource(fanpageId, newSource.trim());
    setNewSource("");
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, ig_sources: fresh.ig_sources, ig_source_usernames: fresh.ig_source_usernames }));
  }

  async function handleRemoveSource(username: string) {
    await removeIGSourceByUsername(fanpageId, username);
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, ig_sources: fresh.ig_sources, ig_source_usernames: fresh.ig_source_usernames }));
  }

  async function handlePreview() {
    setPreviewLoading(true);
    setPreviewResult("");
    try {
      const res = await previewCaption(fanpageId, previewSrc, previewOrig);
      setPreviewResult(res.data.caption);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Preview failed";
      setPreviewResult(`Error: ${message}`);
    } finally {
      setPreviewLoading(false);
    }
  }

  function addTag(key: "caption_must_include" | "caption_must_avoid", value: string) {
    const arr = (form[key] as string[]) || [];
    if (value && !arr.includes(value)) set(key, [...arr, value]);
  }

  function removeTag(key: "caption_must_include" | "caption_must_avoid", value: string) {
    const arr = (form[key] as string[]) || [];
    set(key, arr.filter((v) => v !== value));
  }

  // Header and tab counts
  const enabledModes = useMemo(() => {
    const active: string[] = [];
    if (form.mode1_ig_repost_enabled) active.push("ig");
    if (form.mode2_news_content_enabled) active.push("news");
    if (form.discussion_enabled) active.push("discussion");
    if (form.pinterest_enabled) active.push("pinterest");
    if (form.facebook_photo_enabled) active.push("facebook");
    if (form.yt_clip_enabled) active.push("youtube");
    if (form.ai_video_enabled) active.push("aivideo");
    if (form.radar_enabled) active.push("radar");
    return active;
  }, [
    form.mode1_ig_repost_enabled,
    form.mode2_news_content_enabled,
    form.discussion_enabled,
    form.pinterest_enabled,
    form.facebook_photo_enabled,
    form.yt_clip_enabled,
    form.ai_video_enabled,
    form.radar_enabled,
  ]);

  const channelCount = brand?.channels?.length || 0;
  const publishedToday = (brand?.channels || []).reduce((acc, c) => acc + (c.today?.published || 0), 0);
  const targetToday = (brand?.channels || []).reduce((acc, c) => acc + (c.daily_target || 0), 0);

  const reviewModes = [
    form.publish_mode,
    form.mode2_publish_mode,
    form.discussion_publish_mode,
    form.pinterest_publish_mode,
    form.facebook_photo_publish_mode,
    form.yt_clip_publish_mode,
    form.ai_video_publish_mode,
  ];
  const autoCount = reviewModes.filter((m) => !m || m === "auto").length;
  const manualCount = reviewModes.filter((m) => m === "manual_review").length;
  const reviewSummary = manualCount === 0 ? "All auto" : autoCount === 0 ? "All review" : "Mixed";

  const totalIdeasCount = discIdeas.length + ideas.length + fbPhotoIdeas.length;

  const tabs: SettingsTab[] = [
    { id: "content", label: "Content", count: `${enabledModes.length} on` },
    { id: "channels", label: "Channels", count: String(channelCount) },
    { id: "pacing", label: "Pacing & review" },
    { id: "captions", label: "Captions" },
    { id: "design", label: "Design" },
    { id: "ideas", label: "Ideas", count: String(totalIdeasCount) },
  ];

  if (!fp) return <div className="text-sm text-text2 p-6">Loading…</div>;

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-24">
      {/* ── Breadcrumb & Back ───────────────────────────── */}
      <div className="flex items-center gap-2 text-xs text-text2">
        <Link href="/brands" className="hover:text-text transition-colors">
          Brands
        </Link>
        <span>/</span>
        <span className="text-text font-medium truncate">{fp.name}</span>
      </div>

      {/* ── Header ─────────────────────────────────────── */}
      <header className="bg-surface border border-line rounded-2xl p-6 space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-4 min-w-0">
            {brandReplizPicture && !avatarErr ? (
              <img
                src={brandReplizPicture}
                alt={fp.name}
                onError={() => setAvatarErr(true)}
                className="w-14 h-14 rounded-2xl object-cover shrink-0 border border-pri/20 shadow-sm"
              />
            ) : (
              <div className="w-14 h-14 rounded-2xl bg-pri-soft text-pri-text font-bold text-xl flex items-center justify-center shrink-0 border border-pri/20 shadow-sm">
                {getInitials(fp.name)}
              </div>
            )}
            <div className="min-w-0">
              <div className="flex items-center gap-2.5 flex-wrap">
                <h1 className="text-2xl font-bold text-text truncate">{fp.name}</h1>
                <span
                  className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                    fp.is_active
                      ? "bg-pri-soft text-pri-text"
                      : "bg-surface2 text-text2 border border-line"
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      fp.is_active ? "bg-pri" : "bg-text2"
                    }`}
                  />
                  {fp.is_active ? "Active" : "Inactive"}
                </span>

                <button
                  type="button"
                  role="switch"
                  aria-checked={fp.is_active}
                  onClick={handleToggleActive}
                  disabled={activeSaving}
                  title={fp.is_active ? "Pause brand" : "Activate brand"}
                  className="inline-flex items-center gap-2 px-2.5 py-1 rounded-xl border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold cursor-pointer transition-all disabled:opacity-50"
                >
                  <span
                    className={`relative inline-block w-7 h-4 rounded-full transition-colors ${
                      fp.is_active ? "bg-pri" : "bg-line"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full transition-transform shadow-sm ${
                        fp.is_active ? "bg-pri-fg translate-x-3" : "bg-surface"
                      }`}
                    />
                  </span>
                  <span>{activeSaving ? "Updating…" : fp.is_active ? "Active" : "Paused"}</span>
                </button>
              </div>
              <p className="text-xs text-text2 mt-1 truncate">
                Repliz ID: {fp.repliz_account_id || "Local brand (phone only)"}
              </p>
            </div>
          </div>
        </div>

        {/* Stats Row */}
        <dl className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
          <div className="p-4 rounded-xl bg-surface2 border border-line">
            <dt className="text-xs font-semibold text-text2">Today</dt>
            <dd className="text-xl font-bold text-text tabular-nums mt-0.5">
              {publishedToday} / {targetToday || 14}
            </dd>
            <dd className="text-xs text-text2 mt-0.5">
              Published / planned over {channelCount} active channels
            </dd>
          </div>

          <div className="p-4 rounded-xl bg-surface2 border border-line">
            <dt className="text-xs font-semibold text-text2">Active channels</dt>
            <dd className="text-xl font-bold text-text tabular-nums mt-0.5">
              {channelCount}
            </dd>
            <dd className="text-xs text-text2 mt-0.5">
              {targetToday} posts/day capacity
            </dd>
          </div>

          <div className="p-4 rounded-xl bg-surface2 border border-line">
            <dt className="text-xs font-semibold text-text2">Review</dt>
            <dd className="text-xl font-bold text-text mt-0.5">{reviewSummary}</dd>
            <dd className="text-xs text-text2 mt-0.5">
              {autoCount} auto-publish · {manualCount} review first
            </dd>
          </div>
        </dl>

        {/* Making Content Chips */}
        <div className="space-y-2 pt-1 border-t border-line">
          <span className="text-xs font-semibold text-text2">Making content</span>
          <div className="flex flex-wrap gap-2">
            {enabledModes.length === 0 ? (
              <span className="text-xs text-text2 italic">No content modes enabled.</span>
            ) : (
              enabledModes.map((mode) => <ModeChip key={mode} mode={mode} size="sm" />)
            )}
          </div>
        </div>
      </header>

      {/* ── Six Navigation Tabs ─────────────────────────── */}
      <SettingsTabs tabs={tabs} active={activeTab} onChange={selectTab} />

      {/* ── Tab Panels ─────────────────────────────────── */}
      {activeTab === "content" && (
        <ContentTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          fp={fp}
          mutate={() => mutate(undefined, { revalidate: true })}
          mode1Saving={mode1Saving}
          handleMode1Toggle={handleMode1Toggle}
          handleIgModeChange={handleIgModeChange}
          allTemplates={allTemplates}
          handleRemoveSource={handleRemoveSource}
          newSource={newSource}
          setNewSource={setNewSource}
          handleAddSource={handleAddSource}
          allNewsSources={allNewsSources}
          allGalleryNiches={allGalleryNiches}
          toggleNewsSource={toggleNewsSource}
          newsPreviewTitle={newsPreviewTitle}
          setNewsPreviewTitle={setNewsPreviewTitle}
          newsPreviewContent={newsPreviewContent}
          setNewsPreviewContent={setNewsPreviewContent}
          newsPreviewResult={newsPreviewResult}
          newsPreviewLoading={newsPreviewLoading}
          handleNewsPreview={handleNewsPreview}
          newTopicSeed={newTopicSeed}
          setNewTopicSeed={setNewTopicSeed}
          newTopicSubject={newTopicSubject}
          setNewTopicSubject={setNewTopicSubject}
          topicSaving={topicSaving}
          handleAddTopic={handleAddTopic}
          handleToggleTopic={handleToggleTopic}
          handleDeleteTopic={handleDeleteTopic}
          newPinUrl={newPinUrl}
          setNewPinUrl={setNewPinUrl}
          newPinLabel={newPinLabel}
          setNewPinLabel={setNewPinLabel}
          pinSourceSaving={pinSourceSaving}
          handleAddPinSource={handleAddPinSource}
          handleTogglePinSource={handleTogglePinSource}
          handleDeletePinSource={handleDeletePinSource}
          newFbPhotoUrl={newFbPhotoUrl}
          setNewFbPhotoUrl={setNewFbPhotoUrl}
          newFbPhotoLabel={newFbPhotoLabel}
          setNewFbPhotoLabel={setNewFbPhotoLabel}
          fbPhotoSourceSaving={fbPhotoSourceSaving}
          handleAddFbPhotoSource={handleAddFbPhotoSource}
          handleToggleFbPhotoSource={handleToggleFbPhotoSource}
          handleDeleteFbPhotoSource={handleDeleteFbPhotoSource}
          refreshSources={async () => {
            const fresh = await mutate();
            if (fresh) setForm((prev) => ({ ...prev, yt_clip_sources: fresh.yt_clip_sources }));
          }}
          aiVideoTemplates={aiVideoTemplates}
          radarNichesData={radarNichesData}
          onNavigateTab={selectTab}
        />
      )}

      {activeTab === "channels" && (
        <ChannelsTab
          fanpageId={fanpageId}
          brand={brand}
          fanpage={fp}
          onSaved={handleRevalidateSocial}
        />
      )}

      {activeTab === "pacing" && (
        <PacingTab
          form={form}
          set={set}
          handleCountryChange={handleCountryChange}
          tzInput={tzInput}
          setTzInput={setTzInput}
          handleTimezoneChange={handleTimezoneChange}
          tzMsg={tzMsg}
        />
      )}

      {activeTab === "captions" && (
        <CaptionsTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          mustIncludeInput={mustIncludeInput}
          setMustIncludeInput={setMustIncludeInput}
          mustAvoidInput={mustAvoidInput}
          setMustAvoidInput={setMustAvoidInput}
          addTag={addTag}
          removeTag={removeTag}
          previewSrc={previewSrc}
          setPreviewSrc={setPreviewSrc}
          previewOrig={previewOrig}
          setPreviewOrig={setPreviewOrig}
          previewResult={previewResult}
          previewLoading={previewLoading}
          handlePreview={handlePreview}
        />
      )}

      {activeTab === "design" && (
        <DesignTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          fp={fp}
          mutate={() => mutate(undefined, { revalidate: true })}
          wmUploading={wmUploading}
          handleWatermarkUpload={handleWatermarkUpload}
          handleWatermarkDelete={handleWatermarkDelete}
          brand={brand}
          aiEngineStatus={aiEngineStatus}
          onToggleGptcli={handleToggleGptcli}
        />
      )}

      {activeTab === "ideas" && (
        <IdeasTab
          fanpageId={fanpageId}
          form={form}
          newDiscSeed={newDiscSeed}
          setNewDiscSeed={setNewDiscSeed}
          newDiscSubjectHint={newDiscSubjectHint}
          setNewDiscSubjectHint={setNewDiscSubjectHint}
          discIdeaSaving={discIdeaSaving}
          handleAddDiscIdea={handleAddDiscIdea}
          discIdeas={discIdeas}
          editingDiscIdeaId={editingDiscIdeaId}
          setEditingDiscIdeaId={setEditingDiscIdeaId}
          editDiscLabel={editDiscLabel}
          setEditDiscLabel={setEditDiscLabel}
          editDiscQuestion={editDiscQuestion}
          setEditDiscQuestion={setEditDiscQuestion}
          editDiscSubject={editDiscSubject}
          setEditDiscSubject={setEditDiscSubject}
          editDiscCaption={editDiscCaption}
          setEditDiscCaption={setEditDiscCaption}
          handleSaveDiscIdea={handleSaveDiscIdea}
          startEditDiscIdea={startEditDiscIdea}
          handleDeleteDiscIdea={handleDeleteDiscIdea}
          discIdeasHasMore={discIdeasHasMore}
          handleLoadMoreDiscIdeas={handleLoadMoreDiscIdeas}
          discIdeasLoadingMore={discIdeasLoadingMore}
          ideas={ideas}
          editingIdeaId={editingIdeaId}
          setEditingIdeaId={setEditingIdeaId}
          editIdeaTitle={editIdeaTitle}
          setEditIdeaTitle={setEditIdeaTitle}
          editIdeaDescription={editIdeaDescription}
          setEditIdeaDescription={setEditIdeaDescription}
          handleSaveIdea={handleSaveIdea}
          startEditIdea={startEditIdea}
          handleDeleteIdea={handleDeleteIdea}
          ideasHasMore={ideasHasMore}
          handleLoadMoreIdeas={handleLoadMoreIdeas}
          ideasLoadingMore={ideasLoadingMore}
          fbPhotoIdeas={fbPhotoIdeas}
          editingFbIdeaId={editingFbIdeaId}
          setEditingFbIdeaId={setEditingFbIdeaId}
          editFbIdeaTitle={editFbIdeaTitle}
          setEditFbIdeaTitle={setEditFbIdeaTitle}
          editFbIdeaSubtitle={editFbIdeaSubtitle}
          setEditFbIdeaSubtitle={setEditFbIdeaSubtitle}
          editFbIdeaCaption={editFbIdeaCaption}
          setEditFbIdeaCaption={setEditFbIdeaCaption}
          handleSaveFbIdea={handleSaveFbIdea}
          startEditFbIdea={startEditFbIdea}
          handleDeleteFbIdea={handleDeleteFbIdea}
          fbPhotoIdeasHasMore={fbPhotoIdeasHasMore}
          handleLoadMoreFbPhotoIdeas={handleLoadMoreFbPhotoIdeas}
          fbPhotoIdeasLoadingMore={fbPhotoIdeasLoadingMore}
        />
      )}

      {/* ── Sticky Save Bar ────────────────────────────── */}
      <div
        className={`fixed bottom-[56px] lg:bottom-0 right-0 z-30 bg-surface/90 backdrop-blur-md border-t border-line p-3 sm:px-8 transition-[left] duration-300 ${
          collapsed ? "left-0 lg:left-[80px]" : "left-0 lg:left-[248px]"
        }`}
      >
        <div className="max-w-5xl mx-auto flex items-center justify-between gap-4">
          <div className="text-xs text-text2">
            {saving ? "Saving changes…" : "Brand settings are applied across all content and channels."}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <button
              type="button"
              onClick={() => router.back()}
              className="btn-secondary text-xs px-4 py-2"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="btn-primary text-xs px-4 py-2 inline-flex items-center gap-1.5"
            >
              {saving ? (
                <>
                  <Icon icon="svg-spinners:ring-resize" width={14} />
                  <span>Saving…</span>
                </>
              ) : (
                <span>Save Changes</span>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
