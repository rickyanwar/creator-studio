"use client";

import { useState } from "react";
import { Icon } from "@iconify/react";
import type {
  FanpageDetail,
  AiVideoTemplate,
  DiscussionTopicRef,
  PinterestSourceRef,
  FacebookPhotoSourceRef,
} from "@/lib/types";
import { IgTab } from "@/components/fanpages/tabs/IgTab";
import { NewsTab } from "@/components/fanpages/tabs/NewsTab";
import { DiscussionTab } from "@/components/fanpages/tabs/DiscussionTab";
import { PinterestTab } from "@/components/fanpages/tabs/PinterestTab";
import { FacebookTab } from "@/components/fanpages/tabs/FacebookTab";
import { YoutubeTab } from "@/components/fanpages/tabs/YoutubeTab";
import { AiVideoTab } from "@/components/fanpages/tabs/AiVideoTab";
import { RadarTab } from "@/components/fanpages/tabs/RadarTab";

export interface ContentTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  fp: FanpageDetail;
  mutate: () => Promise<any>;

  // IG
  mode1Saving: boolean;
  handleMode1Toggle: (v: boolean) => Promise<void>;
  handleIgModeChange: (mode: "repost" | "recreate_template" | "recreate_chatgpt") => void;
  allTemplates: any[];
  handleRemoveSource: (username: string) => Promise<void>;
  newSource: string;
  setNewSource: (v: string) => void;
  handleAddSource: () => Promise<void>;

  // News
  allNewsSources: { id: number; name: string; category_url: string }[];
  allGalleryNiches: string[];
  toggleNewsSource: (sourceId: number, subscribed: boolean) => Promise<void>;
  newsPreviewTitle: string;
  setNewsPreviewTitle: (v: string) => void;
  newsPreviewContent: string;
  setNewsPreviewContent: (v: string) => void;
  newsPreviewResult: { title: string; caption: string } | null;
  newsPreviewLoading: boolean;
  handleNewsPreview: () => Promise<void>;

  // Discussion
  newTopicSeed: string;
  setNewTopicSeed: (v: string) => void;
  newTopicSubject: string;
  setNewTopicSubject: (v: string) => void;
  topicSaving: boolean;
  handleAddTopic: () => Promise<void>;
  handleToggleTopic: (t: DiscussionTopicRef) => Promise<void>;
  handleDeleteTopic: (id: number) => Promise<void>;

  // Pinterest
  newPinUrl: string;
  setNewPinUrl: (v: string) => void;
  newPinLabel: string;
  setNewPinLabel: (v: string) => void;
  pinSourceSaving: boolean;
  handleAddPinSource: () => Promise<void>;
  handleTogglePinSource: (s: PinterestSourceRef) => Promise<void>;
  handleDeletePinSource: (id: number) => Promise<void>;

  // Facebook
  newFbPhotoUrl: string;
  setNewFbPhotoUrl: (v: string) => void;
  newFbPhotoLabel: string;
  setNewFbPhotoLabel: (v: string) => void;
  fbPhotoSourceSaving: boolean;
  handleAddFbPhotoSource: () => Promise<void>;
  handleToggleFbPhotoSource: (s: FacebookPhotoSourceRef) => Promise<void>;
  handleDeleteFbPhotoSource: (id: number) => Promise<void>;

  // YouTube
  refreshSources: () => Promise<void>;

  // AI Video
  aiVideoTemplates: AiVideoTemplate[];

  // Radar
  radarNichesData?: string[];

  onNavigateTab?: (tab: any) => void;
}

export function ContentTab({
  fanpageId,
  form,
  set,
  fp,
  mutate,
  mode1Saving,
  handleMode1Toggle,
  handleIgModeChange,
  allTemplates,
  handleRemoveSource,
  newSource,
  setNewSource,
  handleAddSource,
  allNewsSources,
  allGalleryNiches,
  toggleNewsSource,
  newsPreviewTitle,
  setNewsPreviewTitle,
  newsPreviewContent,
  setNewsPreviewContent,
  newsPreviewResult,
  newsPreviewLoading,
  handleNewsPreview,
  newTopicSeed,
  setNewTopicSeed,
  newTopicSubject,
  setNewTopicSubject,
  topicSaving,
  handleAddTopic,
  handleToggleTopic,
  handleDeleteTopic,
  newPinUrl,
  setNewPinUrl,
  newPinLabel,
  setNewPinLabel,
  pinSourceSaving,
  handleAddPinSource,
  handleTogglePinSource,
  handleDeletePinSource,
  newFbPhotoUrl,
  setNewFbPhotoUrl,
  newFbPhotoLabel,
  setNewFbPhotoLabel,
  fbPhotoSourceSaving,
  handleAddFbPhotoSource,
  handleToggleFbPhotoSource,
  handleDeleteFbPhotoSource,
  refreshSources,
  aiVideoTemplates,
  radarNichesData,
  onNavigateTab,
}: ContentTabProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    ig: true,
  });

  const toggleExpand = (key: string) => {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const modes = [
    {
      id: "ig",
      title: "Instagram",
      blurb: "Crawl IG sources · Repost or recreate with templates / AI edit",
      icon: "solar:camera-bold-duotone",
      enabled: !!form.mode1_ig_repost_enabled,
      badgeText: "Unlimited",
      reviewMode: form.publish_mode,
      toggle: () => handleMode1Toggle(!form.mode1_ig_repost_enabled),
      saving: mode1Saving,
      render: () => (
        <IgTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          fp={fp}
          mode1Saving={mode1Saving}
          handleMode1Toggle={handleMode1Toggle}
          handleIgModeChange={handleIgModeChange}
          allTemplates={allTemplates}
          handleRemoveSource={handleRemoveSource}
          mutate={mutate}
          newSource={newSource}
          setNewSource={setNewSource}
          handleAddSource={handleAddSource}
        />
      ),
    },
    {
      id: "news",
      title: "News card",
      blurb: "Articles from RSS / news sites → branded news cards",
      icon: "solar:documents-bold-duotone",
      enabled: !!form.mode2_news_content_enabled,
      badgeText: "Unlimited",
      reviewMode: form.mode2_publish_mode,
      toggle: () => set("mode2_news_content_enabled", !form.mode2_news_content_enabled),
      saving: false,
      render: () => (
        <NewsTab
          fanpageId={fanpageId}
          form={form}
          set={set}
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
        />
      ),
    },
    {
      id: "discussion",
      title: "Discussion",
      blurb: "Evergreen questions and topics → engagement cards",
      icon: "solar:chat-round-dots-bold-duotone",
      enabled: !!form.discussion_enabled,
      badgeText: `${form.discussion_daily_count ?? 1}/day`,
      reviewMode: form.discussion_publish_mode,
      toggle: () => set("discussion_enabled", !form.discussion_enabled),
      saving: false,
      render: () => (
        <DiscussionTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          newTopicSeed={newTopicSeed}
          setNewTopicSeed={setNewTopicSeed}
          newTopicSubject={newTopicSubject}
          setNewTopicSubject={setNewTopicSubject}
          topicSaving={topicSaving}
          handleAddTopic={handleAddTopic}
          handleToggleTopic={handleToggleTopic}
          handleDeleteTopic={handleDeleteTopic}
          newDiscSeed=""
          setNewDiscSeed={() => {}}
          newDiscSubjectHint=""
          setNewDiscSubjectHint={() => {}}
          discIdeaSaving={false}
          handleAddDiscIdea={async () => {}}
          discIdeas={[]}
          editingDiscIdeaId={null}
          setEditingDiscIdeaId={() => {}}
          editDiscLabel=""
          setEditDiscLabel={() => {}}
          editDiscQuestion=""
          setEditDiscQuestion={() => {}}
          editDiscSubject=""
          setEditDiscSubject={() => {}}
          editDiscCaption=""
          setEditDiscCaption={() => {}}
          handleSaveDiscIdea={async () => {}}
          startEditDiscIdea={() => {}}
          handleDeleteDiscIdea={async () => {}}
          hideIdeas={true}
        />
      ),
    },
    {
      id: "pinterest",
      title: "Pinterest",
      blurb: "Curated Pinterest boards / pins → photo cards",
      icon: "solar:pin-bold-duotone",
      enabled: !!form.pinterest_enabled,
      badgeText: `${form.pinterest_daily_count ?? 1}/day`,
      reviewMode: form.pinterest_publish_mode,
      toggle: () => set("pinterest_enabled", !form.pinterest_enabled),
      saving: false,
      render: () => (
        <PinterestTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          newPinUrl={newPinUrl}
          setNewPinUrl={setNewPinUrl}
          newPinLabel={newPinLabel}
          setNewPinLabel={setNewPinLabel}
          pinSourceSaving={pinSourceSaving}
          handleAddPinSource={handleAddPinSource}
          handleTogglePinSource={handleTogglePinSource}
          handleDeletePinSource={handleDeletePinSource}
          ideas={[]}
          editingIdeaId={null}
          setEditingIdeaId={() => {}}
          editIdeaTitle=""
          setEditIdeaTitle={() => {}}
          editIdeaDescription=""
          setEditIdeaDescription={() => {}}
          handleSaveIdea={async () => {}}
          startEditIdea={() => {}}
          handleDeleteIdea={async () => {}}
          hideIdeas={true}
        />
      ),
    },
    {
      id: "facebook",
      title: "FB photo recreate",
      blurb: "Recreate photos & quotes from Facebook pages",
      icon: "solar:gallery-bold-duotone",
      enabled: !!form.facebook_photo_enabled,
      badgeText: `${form.facebook_photo_daily_count ?? 1}/day`,
      reviewMode: form.facebook_photo_publish_mode,
      toggle: () => set("facebook_photo_enabled", !form.facebook_photo_enabled),
      saving: false,
      render: () => (
        <FacebookTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          newFbPhotoUrl={newFbPhotoUrl}
          setNewFbPhotoUrl={setNewFbPhotoUrl}
          newFbPhotoLabel={newFbPhotoLabel}
          setNewFbPhotoLabel={setNewFbPhotoLabel}
          fbPhotoSourceSaving={fbPhotoSourceSaving}
          handleAddFbPhotoSource={handleAddFbPhotoSource}
          handleToggleFbPhotoSource={handleToggleFbPhotoSource}
          handleDeleteFbPhotoSource={handleDeleteFbPhotoSource}
          fbPhotoIdeas={[]}
          editingFbIdeaId={null}
          setEditingFbIdeaId={() => {}}
          editFbIdeaTitle=""
          setEditFbIdeaTitle={() => {}}
          editFbIdeaSubtitle=""
          setEditFbIdeaSubtitle={() => {}}
          editFbIdeaCaption=""
          setEditFbIdeaCaption={() => {}}
          handleSaveFbIdea={async () => {}}
          startEditFbIdea={() => {}}
          handleDeleteFbIdea={async () => {}}
          hideIdeas={true}
        />
      ),
    },
    {
      id: "youtube",
      title: "YouTube clips",
      blurb: "Long videos → AI picks best moments → 9:16 vertical clips",
      icon: "solar:play-circle-bold-duotone",
      enabled: !!form.yt_clip_enabled,
      badgeText: `${form.yt_clip_daily_count ?? 2}/day`,
      reviewMode: form.yt_clip_publish_mode,
      toggle: () => set("yt_clip_enabled", !form.yt_clip_enabled),
      saving: false,
      render: () => (
        <YoutubeTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          refreshSources={refreshSources}
          hideIdeas={true}
          onNavigateTab={onNavigateTab}
        />
      ),
    },
    {
      id: "aivideo",
      title: "AI video",
      blurb: "Original short videos generated with Google Flow",
      icon: "solar:videocamera-record-bold-duotone",
      enabled: !!form.ai_video_enabled,
      badgeText: `${form.ai_video_daily_count ?? 1}/day`,
      reviewMode: form.ai_video_publish_mode,
      toggle: () => {
        const next = !form.ai_video_enabled;
        set("ai_video_enabled", next);
        if (next && (!form.ai_video_templates || form.ai_video_templates.length === 0) && aiVideoTemplates.length > 0) {
          const def = aiVideoTemplates[0].key;
          set("ai_video_templates", [def]);
          set("ai_video_template", def);
        }
      },
      saving: false,
      render: () => (
        <AiVideoTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          templates={aiVideoTemplates ?? []}
          onNavigateTab={onNavigateTab}
        />
      ),
    },
    {
      id: "radar",
      title: "Viral Radar",
      blurb: "Watches niche-leader accounts and recreates fast-moving posts",
      icon: "solar:radar-2-bold-duotone",
      enabled: !!form.radar_enabled,
      badgeText: form.radar_daily_max ? `Max ${form.radar_daily_max}/day` : "Max 2/day",
      reviewMode: "auto",
      toggle: () => set("radar_enabled", !form.radar_enabled),
      saving: false,
      render: () => (
        <RadarTab
          fanpageId={fanpageId}
          form={form}
          set={set}
          radarNichesData={radarNichesData}
        />
      ),
    },
  ];

  const onCount = modes.filter((m) => m.enabled).length;

  return (
    <div role="tabpanel" id="tab-panel-content" aria-labelledby="tab-content" className="space-y-4">
      <div className="flex flex-wrap justify-between items-baseline gap-2 pb-1">
        <div>
          <h2 className="text-lg font-semibold text-text">What this brand makes</h2>
          <p className="text-xs text-text2">
            Switch each mode on or off. Open Settings on a card to change how it works.
          </p>
        </div>
        <span className="text-xs text-text2 tabular-nums">
          {onCount} of {modes.length} on
        </span>
      </div>

      <div className="space-y-3">
        {modes.map((mode) => {
          const isExpanded = !!expanded[mode.id];
          return (
            <article
              key={mode.id}
              aria-label={mode.title}
              className="bg-surface border border-line rounded-2xl overflow-hidden transition-all"
            >
              <div className="flex flex-wrap items-center justify-between gap-4 p-4 sm:px-6">
                <div className="flex-1 min-w-[220px] flex items-center gap-3.5">
                  <span className="w-10 h-10 rounded-xl bg-surface2 flex items-center justify-center shrink-0 text-text border border-line">
                    <Icon icon={mode.icon} width={20} />
                  </span>
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-text leading-tight">{mode.title}</h3>
                    <p className="text-xs text-text2 leading-snug mt-0.5">{mode.blurb}</p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-semibold px-2.5 py-1 rounded-lg bg-surface2 text-text tabular-nums border border-line">
                    {mode.badgeText}
                  </span>
                  <span className="text-xs font-medium px-2.5 py-1 rounded-lg bg-surface2 text-text2 border border-line">
                    {mode.reviewMode === "manual_review" ? "Review first" : "Auto-publish"}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    role="switch"
                    aria-checked={mode.enabled}
                    onClick={mode.toggle}
                    disabled={mode.saving}
                    className="inline-flex items-center gap-2 min-h-[40px] px-3 rounded-xl border border-line bg-surface text-text text-xs font-semibold cursor-pointer hover:bg-surface2 transition-all"
                  >
                    <span
                      className={`relative w-9 h-5 rounded-full transition-colors ${
                        mode.enabled ? "bg-pri" : "bg-line"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full transition-transform shadow-sm ${
                          mode.enabled ? "bg-pri-fg translate-x-4" : "bg-surface"
                        }`}
                      />
                    </span>
                    <span>{mode.enabled ? "On" : "Off"}</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => toggleExpand(mode.id)}
                    aria-expanded={isExpanded}
                    className="inline-flex items-center gap-1.5 min-h-[40px] px-3.5 rounded-xl border border-line bg-surface text-text text-xs font-semibold cursor-pointer hover:bg-surface2 transition-all"
                  >
                    <span>Settings</span>
                    <Icon
                      icon="solar:alt-arrow-down-bold"
                      width={14}
                      className={`transition-transform duration-200 ${
                        isExpanded ? "rotate-180" : ""
                      }`}
                    />
                  </button>
                </div>
              </div>

              {isExpanded && (
                <div className="border-t border-line p-5 sm:p-6 bg-surface/50">
                  {mode.render()}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
