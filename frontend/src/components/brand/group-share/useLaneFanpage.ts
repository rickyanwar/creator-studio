"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import useSWR from "swr";
import {
  ensureGroupShareLane,
  getFanpage,
  updateFanpage,
  addIGSource,
  removeIGSourceByUsername,
  addFanpageNewsSource,
  removeFanpageNewsSource,
  previewNewsCopy,
  addDiscussionTopic,
  updateDiscussionTopic,
  deleteDiscussionTopic,
  addPinterestSource,
  updatePinterestSource,
  deletePinterestSource,
  addFacebookPhotoSource,
  updateFacebookPhotoSource,
  deleteFacebookPhotoSource,
} from "@/lib/api";
import type { DiscussionTopicRef, FacebookPhotoSourceRef, FanpageDetail, PinterestSourceRef } from "@/lib/types";
import { errorMessage } from "./Fields";
import { LANE_LOCKED_FIELDS } from "./laneLock";

type IgMode = "repost" | "recreate_template" | "recreate_chatgpt";

// Form keys that a tab updates after the change was already saved through its own endpoint
// (FacebookSourceCard saves the card, then reports the new list); they are not unsaved edits.
const SAVED_ON_THEIR_OWN = ["facebook_photo_sources"];

/**
 * Loads and edits a brand's hidden group lane fanpage with the same wiring the brand page uses
 * for its Content tab (brands/[id]/page.tsx), but bound to the lane id.
 *
 * The lane id comes from POST /fanpages/{brandId}/group-share/lane (creates the lane on first
 * call). Sources, topics and the IG switch save at once, like on the brand page; other fields are
 * kept in `form` until `save()`, which sends only the fields the user changed and never a field
 * owned by the brand page.
 */
export function useLaneFanpage(brandId: number) {
  const lane = useSWR(["group-share-lane", brandId], () => ensureGroupShareLane(brandId), {
    revalidateOnFocus: false,
    revalidateIfStale: false,
  });
  const laneId = lane.data?.lane_fanpage_id ?? null;
  const fanpage = useSWR(laneId ? `fanpage-${laneId}` : null, () =>
    getFanpage(laneId as number).then((r) => r.data as FanpageDetail)
  );
  const fp = fanpage.data;
  const mutate = fanpage.mutate;

  const [form, setForm] = useState<Partial<FanpageDetail>>({});
  const [dirty, setDirty] = useState(false);
  const changedKeys = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (fp && changedKeys.current.size === 0) setForm({ ...fp });
  }, [fp]);

  const set = useCallback((key: string, value: unknown) => {
    if (!SAVED_ON_THEIR_OWN.includes(key)) {
      changedKeys.current.add(key);
      setDirty(true);
    }
    setForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  /** Merge fresh server values for keys that saved on their own (sources, topics). */
  async function refreshKeys(...keys: (keyof FanpageDetail)[]) {
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, ...Object.fromEntries(keys.map((key) => [key, fresh[key]])) }));
  }

  async function run(action: () => Promise<unknown>, failure: string) {
    try {
      await action();
    } catch (err) {
      alert(`${failure}: ${errorMessage(err)}`);
    }
  }

  async function save() {
    if (laneId === null || changedKeys.current.size === 0) return;
    const keys = Array.from(changedKeys.current).filter((key) => !LANE_LOCKED_FIELDS.includes(key));
    const payload = Object.fromEntries(keys.map((key) => [key, form[key as keyof FanpageDetail]]));
    if (keys.length > 0) await updateFanpage(laneId, payload);
    changedKeys.current = new Set();
    setDirty(false);
    await mutate(undefined, { revalidate: true });
  }

  // ── IG ──
  const [mode1Saving, setMode1Saving] = useState(false);
  const [newSource, setNewSource] = useState("");

  async function handleMode1Toggle(value: boolean) {
    if (laneId === null) return;
    setForm((prev) => ({ ...prev, mode1_ig_repost_enabled: value }));
    setMode1Saving(true);
    try {
      await updateFanpage(laneId, { mode1_ig_repost_enabled: value });
      await refreshKeys("mode1_ig_repost_enabled");
    } catch (err) {
      setForm((prev) => ({ ...prev, mode1_ig_repost_enabled: !value }));
      alert(`Failed to update IG for groups: ${errorMessage(err)}`);
    } finally {
      setMode1Saving(false);
    }
  }

  function handleIgModeChange(mode: IgMode) {
    if (mode === "repost") {
      set("ig_recreate_enabled", false);
    } else {
      set("ig_recreate_enabled", true);
      set("visual_engine", mode === "recreate_chatgpt" ? "chatgpt" : "off");
    }
  }

  async function handleAddSource() {
    if (laneId === null || !newSource.trim()) return;
    await run(async () => {
      await addIGSource(laneId, newSource.trim());
      setNewSource("");
      await refreshKeys("ig_sources", "ig_source_usernames");
    }, "Failed to add Instagram source");
  }

  async function handleRemoveSource(username: string) {
    if (laneId === null) return;
    await run(async () => {
      await removeIGSourceByUsername(laneId, username);
      await refreshKeys("ig_sources", "ig_source_usernames");
    }, "Failed to remove Instagram source");
  }

  // ── News ──
  const [newsPreviewTitle, setNewsPreviewTitle] = useState("");
  const [newsPreviewContent, setNewsPreviewContent] = useState("");
  const [newsPreviewResult, setNewsPreviewResult] = useState<{ title: string; caption: string } | null>(null);
  const [newsPreviewLoading, setNewsPreviewLoading] = useState(false);

  async function toggleNewsSource(sourceId: number, subscribed: boolean) {
    if (laneId === null) return;
    await run(async () => {
      if (subscribed) await removeFanpageNewsSource(laneId, sourceId);
      else await addFanpageNewsSource(laneId, sourceId);
      await refreshKeys("news_sources");
    }, "Failed to update news source");
  }

  async function handleNewsPreview() {
    if (laneId === null) return;
    setNewsPreviewLoading(true);
    setNewsPreviewResult(null);
    try {
      const res = await previewNewsCopy(laneId, { title: newsPreviewTitle, content: newsPreviewContent });
      setNewsPreviewResult(res.data);
    } catch (err) {
      alert(`Preview failed: ${errorMessage(err)}`);
    } finally {
      setNewsPreviewLoading(false);
    }
  }

  // ── Discussion ──
  const [newTopicSeed, setNewTopicSeed] = useState("");
  const [newTopicSubject, setNewTopicSubject] = useState("");
  const [topicSaving, setTopicSaving] = useState(false);

  async function handleAddTopic() {
    if (laneId === null || !newTopicSeed.trim()) return;
    setTopicSaving(true);
    await run(async () => {
      await addDiscussionTopic(laneId, { seed_text: newTopicSeed.trim(), subject_hint: newTopicSubject.trim() || undefined });
      setNewTopicSeed("");
      setNewTopicSubject("");
      await refreshKeys("discussion_topics");
    }, "Failed to add topic");
    setTopicSaving(false);
  }

  async function handleToggleTopic(topic: DiscussionTopicRef) {
    if (laneId === null) return;
    await run(async () => {
      await updateDiscussionTopic(laneId, topic.id, { is_active: !topic.is_active });
      await refreshKeys("discussion_topics");
    }, "Failed to update topic");
  }

  async function handleDeleteTopic(topicId: number) {
    if (laneId === null) return;
    await run(async () => {
      await deleteDiscussionTopic(laneId, topicId);
      await refreshKeys("discussion_topics");
    }, "Failed to delete topic");
  }

  // ── Pinterest ──
  const [newPinUrl, setNewPinUrl] = useState("");
  const [newPinLabel, setNewPinLabel] = useState("");
  const [pinSourceSaving, setPinSourceSaving] = useState(false);

  async function handleAddPinSource() {
    if (laneId === null || !newPinUrl.trim()) return;
    setPinSourceSaving(true);
    await run(async () => {
      await addPinterestSource(laneId, { source_url: newPinUrl.trim(), label: newPinLabel.trim() || undefined });
      setNewPinUrl("");
      setNewPinLabel("");
      await refreshKeys("pinterest_sources");
    }, "Failed to add Pinterest reference");
    setPinSourceSaving(false);
  }

  async function handleTogglePinSource(source: PinterestSourceRef) {
    if (laneId === null) return;
    await run(async () => {
      await updatePinterestSource(laneId, source.id, { is_active: !source.is_active });
      await refreshKeys("pinterest_sources");
    }, "Failed to update Pinterest reference");
  }

  async function handleDeletePinSource(sourceId: number) {
    if (laneId === null) return;
    await run(async () => {
      await deletePinterestSource(laneId, sourceId);
      await refreshKeys("pinterest_sources");
    }, "Failed to delete Pinterest reference");
  }

  // ── Facebook photo recreate ──
  const [newFbPhotoUrl, setNewFbPhotoUrl] = useState("");
  const [newFbPhotoLabel, setNewFbPhotoLabel] = useState("");
  const [fbPhotoSourceSaving, setFbPhotoSourceSaving] = useState(false);

  async function handleAddFbPhotoSource() {
    if (laneId === null || !newFbPhotoUrl.trim()) return;
    setFbPhotoSourceSaving(true);
    await run(async () => {
      await addFacebookPhotoSource(laneId, { page_url: newFbPhotoUrl.trim(), label: newFbPhotoLabel.trim() || undefined });
      setNewFbPhotoUrl("");
      setNewFbPhotoLabel("");
      await refreshKeys("facebook_photo_sources");
    }, "Failed to add Facebook page");
    setFbPhotoSourceSaving(false);
  }

  async function handleToggleFbPhotoSource(source: FacebookPhotoSourceRef) {
    if (laneId === null) return;
    await run(async () => {
      await updateFacebookPhotoSource(laneId, source.id, { is_active: !source.is_active });
      await refreshKeys("facebook_photo_sources");
    }, "Failed to update Facebook page");
  }

  async function handleDeleteFbPhotoSource(sourceId: number) {
    if (laneId === null) return;
    await run(async () => {
      await deleteFacebookPhotoSource(laneId, sourceId);
      await refreshKeys("facebook_photo_sources");
    }, "Failed to delete Facebook page");
  }

  // ── YouTube ──
  async function refreshSources() {
    await refreshKeys("yt_clip_sources");
  }

  return {
    laneId,
    fp,
    form,
    set,
    dirty,
    save,
    error: lane.error ?? fanpage.error,
    mutate: () => mutate(undefined, { revalidate: true }),
    ig: { mode1Saving, handleMode1Toggle, handleIgModeChange, newSource, setNewSource, handleAddSource, handleRemoveSource },
    news: {
      toggleNewsSource, newsPreviewTitle, setNewsPreviewTitle, newsPreviewContent, setNewsPreviewContent,
      newsPreviewResult, newsPreviewLoading, handleNewsPreview,
    },
    discussion: {
      newTopicSeed, setNewTopicSeed, newTopicSubject, setNewTopicSubject, topicSaving,
      handleAddTopic, handleToggleTopic, handleDeleteTopic,
    },
    pinterest: {
      newPinUrl, setNewPinUrl, newPinLabel, setNewPinLabel, pinSourceSaving,
      handleAddPinSource, handleTogglePinSource, handleDeletePinSource,
    },
    facebook: {
      newFbPhotoUrl, setNewFbPhotoUrl, newFbPhotoLabel, setNewFbPhotoLabel, fbPhotoSourceSaving,
      handleAddFbPhotoSource, handleToggleFbPhotoSource, handleDeleteFbPhotoSource,
    },
    refreshSources,
  };
}

export type LaneFanpage = ReturnType<typeof useLaneFanpage>;
