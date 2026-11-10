"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { listAiVideoTemplates, listGalleryNiches, listNewsSources, listRadarNiches, listTemplates } from "@/lib/api";
import type { AiVideoTemplate, FanpageDetail, GroupShareSettings, ModeKey } from "@/lib/types";
import { IgTab } from "@/components/fanpages/tabs/IgTab";
import { NewsTab } from "@/components/fanpages/tabs/NewsTab";
import { DiscussionTab } from "@/components/fanpages/tabs/DiscussionTab";
import { PinterestTab } from "@/components/fanpages/tabs/PinterestTab";
import { FacebookTab } from "@/components/fanpages/tabs/FacebookTab";
import { YoutubeTab } from "@/components/fanpages/tabs/YoutubeTab";
import { AiVideoTab } from "@/components/fanpages/tabs/AiVideoTab";
import { RadarTab } from "@/components/fanpages/tabs/RadarTab";
import { errorMessage, primaryClass } from "./Fields";
import { LANE_LOCKED_FIELDS } from "./laneLock";
import type { LaneFanpage } from "./useLaneFanpage";

type TemplateRef = { id: number; name: string; is_default: boolean; category: "quote" | "news" | "discussion" | null; canvas_width: number; canvas_height: number };
type LaneModeId = "ig" | "news" | "discussion" | "pinterest" | "facebook" | "youtube" | "aivideo" | "radar";

// Tab ids match the role="tabpanel" ids the Content-tab components render (tab-panel-<id>).
const LANE_MODES: { id: LaneModeId; label: string; enabledField: keyof FanpageDetail }[] = [
  { id: "ig", label: "IG", enabledField: "mode1_ig_repost_enabled" },
  { id: "news", label: "News", enabledField: "mode2_news_content_enabled" },
  { id: "discussion", label: "Discussion", enabledField: "discussion_enabled" },
  { id: "pinterest", label: "Pinterest", enabledField: "pinterest_enabled" },
  { id: "facebook", label: "Facebook", enabledField: "facebook_photo_enabled" },
  { id: "youtube", label: "YouTube", enabledField: "yt_clip_enabled" },
  { id: "aivideo", label: "AI video", enabledField: "ai_video_enabled" },
  { id: "radar", label: "Radar", enabledField: "radar_enabled" },
];

const MAX_GROUP_POSTS_PER_DAY = 50;
const noop = () => {};
const noopAsync = async () => {};

type ModeSource = GroupShareSettings["sources"][ModeKey];
const DEFAULT_SOURCE: ModeSource = { source: "off", groups_only_per_day: 0, follow_per_day: 0 };

/** A mode's group settings with defaults (older stored settings may miss a mode or a field). */
export function sourceOf(settings: GroupShareSettings, key: ModeKey): ModeSource {
  const stored = settings.sources[key] as Partial<ModeSource> | undefined;
  return { ...DEFAULT_SOURCE, ...stored };
}

function GroupsPerDayField({ mode, label, settings, onSettingsChange }: {
  mode: "ig" | "news"; label: string; settings: GroupShareSettings; onSettingsChange: (settings: GroupShareSettings) => void;
}) {
  const current = sourceOf(settings, mode);
  const inputId = `groups-per-day-${mode}`;
  return (
    <section className="card space-y-2">
      <label htmlFor={inputId} className="label">Posts per day for groups</label>
      <input
        id={inputId}
        type="number"
        min={0}
        max={MAX_GROUP_POSTS_PER_DAY}
        step={1}
        className="input w-full sm:w-48"
        aria-describedby={`${inputId}-help`}
        value={current.groups_only_per_day}
        onChange={(e) => {
          const value = e.target.valueAsNumber;
          const next = Number.isFinite(value) ? Math.max(0, Math.min(MAX_GROUP_POSTS_PER_DAY, Math.trunc(value))) : 0;
          onSettingsChange({ ...settings, sources: { ...settings.sources, [mode]: { ...current, groups_only_per_day: next } } });
        }}
      />
      <p id={`${inputId}-help`} className="text-[11px] text-text2">
        Most {label} posts made for groups each day (brand&apos;s local day). 0 = no {label} group posts.
      </p>
    </section>
  );
}

export function LaneContent({ brandId, lane, settings, onSettingsChange, onSave, saving, status }: {
  brandId: number;
  lane: LaneFanpage;
  settings: GroupShareSettings;
  onSettingsChange: (settings: GroupShareSettings) => void;
  onSave: () => void;
  saving: boolean;
  status?: ReactNode;
}) {
  const [active, setActive] = useState<LaneModeId>("ig");
  const { data: aiVideoTemplates = [] } = useSWR("ai-video-templates", () =>
    listAiVideoTemplates().then((r) => r.data as AiVideoTemplate[])
  );
  const { data: radarNichesData } = useSWR("radar-niches", () => listRadarNiches().then((r) => r.data as string[]));
  const { data: allNewsSources = [] } = useSWR<{ id: number; name: string; category_url: string }[]>(
    "news-sources-picker",
    () => listNewsSources().then((r) => r.data as { id: number; name: string; category_url: string }[])
  );
  const { data: allGalleryNiches = [] } = useSWR<string[]>("gallery-niches-picker", () =>
    listGalleryNiches().then((r) => r.data as string[])
  );
  // The lane uses the brand's templates (template choice is a brand look field).
  const { data: allTemplates = [] } = useSWR<TemplateRef[]>(`templates-picker-${brandId}`, () =>
    listTemplates(brandId).then((r) => r.data as TemplateRef[])
  );

  const { laneId, fp, form, set } = lane;

  function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const step = e.key === "ArrowRight" ? 1 : -1;
    const next = LANE_MODES[(index + step + LANE_MODES.length) % LANE_MODES.length];
    setActive(next.id);
    document.getElementById(`tab-${next.id}`)?.focus();
  }

  function renderMode(): ReactNode {
    if (laneId === null || !fp) return null;
    switch (active) {
      case "ig":
        return (
          <>
            <IgTab
              fanpageId={laneId}
              form={form}
              set={set}
              fp={fp}
              mode1Saving={lane.ig.mode1Saving}
              handleMode1Toggle={lane.ig.handleMode1Toggle}
              handleIgModeChange={lane.ig.handleIgModeChange}
              allTemplates={allTemplates}
              handleRemoveSource={lane.ig.handleRemoveSource}
              mutate={lane.mutate}
              newSource={lane.ig.newSource}
              setNewSource={lane.ig.setNewSource}
              handleAddSource={lane.ig.handleAddSource}
              lockedFields={LANE_LOCKED_FIELDS}
              toggleLabel="IG for groups"
              followDefaultLabel="Follow group default"
            />
            <GroupsPerDayField mode="ig" label="IG" settings={settings} onSettingsChange={onSettingsChange} />
          </>
        );
      case "news":
        return (
          <>
            <NewsTab
              fanpageId={laneId}
              form={form}
              set={set}
              allNewsSources={allNewsSources}
              allGalleryNiches={allGalleryNiches}
              {...lane.news}
              lockedFields={LANE_LOCKED_FIELDS}
            />
            <GroupsPerDayField mode="news" label="news" settings={settings} onSettingsChange={onSettingsChange} />
          </>
        );
      case "discussion":
        return (
          <DiscussionTab
            fanpageId={laneId}
            form={form}
            set={set}
            {...lane.discussion}
            newDiscSeed=""
            setNewDiscSeed={noop}
            newDiscSubjectHint=""
            setNewDiscSubjectHint={noop}
            discIdeaSaving={false}
            handleAddDiscIdea={noopAsync}
            discIdeas={[]}
            editingDiscIdeaId={null}
            setEditingDiscIdeaId={noop}
            editDiscLabel=""
            setEditDiscLabel={noop}
            editDiscQuestion=""
            setEditDiscQuestion={noop}
            editDiscSubject=""
            setEditDiscSubject={noop}
            editDiscCaption=""
            setEditDiscCaption={noop}
            handleSaveDiscIdea={noopAsync}
            startEditDiscIdea={noop}
            handleDeleteDiscIdea={noopAsync}
            hideIdeas={true}
          />
        );
      case "pinterest":
        return (
          <PinterestTab
            fanpageId={laneId}
            form={form}
            set={set}
            {...lane.pinterest}
            ideas={[]}
            editingIdeaId={null}
            setEditingIdeaId={noop}
            editIdeaTitle=""
            setEditIdeaTitle={noop}
            editIdeaDescription=""
            setEditIdeaDescription={noop}
            handleSaveIdea={noopAsync}
            startEditIdea={noop}
            handleDeleteIdea={noopAsync}
            hideIdeas={true}
          />
        );
      case "facebook":
        return (
          <FacebookTab
            fanpageId={laneId}
            form={form}
            set={set}
            {...lane.facebook}
            fbPhotoIdeas={[]}
            editingFbIdeaId={null}
            setEditingFbIdeaId={noop}
            editFbIdeaTitle=""
            setEditFbIdeaTitle={noop}
            editFbIdeaSubtitle=""
            setEditFbIdeaSubtitle={noop}
            editFbIdeaCaption=""
            setEditFbIdeaCaption={noop}
            handleSaveFbIdea={noopAsync}
            startEditFbIdea={noop}
            handleDeleteFbIdea={noopAsync}
            hideIdeas={true}
          />
        );
      case "youtube":
        return (
          <YoutubeTab
            fanpageId={laneId}
            form={form}
            set={set}
            refreshSources={lane.refreshSources}
            hideIdeas={true}
            lockedFields={LANE_LOCKED_FIELDS}
          />
        );
      case "aivideo":
        return (
          <AiVideoTab
            fanpageId={laneId}
            form={form}
            set={set}
            templates={aiVideoTemplates}
            lockedFields={LANE_LOCKED_FIELDS}
          />
        );
      case "radar":
        return <RadarTab fanpageId={laneId} form={form} set={set} radarNichesData={radarNichesData} />;
    }
  }

  return (
    <section aria-labelledby="group-content-heading" className="min-w-0 space-y-4">
      <div className="pb-1">
        <h2 id="group-content-heading" className="text-lg font-semibold text-text">Group content</h2>
        <p className="text-xs text-text2">
          Content made only for Facebook groups. Same settings as the Content tab, kept separate from the brand page.
          These posts never go to the page.
        </p>
      </div>

      <div className="rounded-xl border border-pri/30 bg-pri-soft p-3 flex items-start gap-2 text-xs text-text">
        <Icon icon="solar:info-circle-bold-duotone" width={18} className="text-pri-text shrink-0" aria-hidden="true" />
        <p>These settings are for group content only. Posts made here never go to the brand page.</p>
      </div>

      {lane.error ? (
        <p role="alert" className="text-sm text-red-500">Group content could not be loaded: {errorMessage(lane.error)}</p>
      ) : laneId === null || !fp ? (
        <p role="status" className="text-sm text-text2">Loading group content…</p>
      ) : (
        <>
          <div role="tablist" aria-label="Group content modes" className="flex flex-wrap gap-1 p-1 rounded-2xl bg-surface2 border border-line">
            {LANE_MODES.map((mode, index) => {
              const isSelected = active === mode.id;
              const isOn = !!form[mode.enabledField];
              return (
                <button
                  key={mode.id}
                  type="button"
                  role="tab"
                  id={`tab-${mode.id}`}
                  aria-selected={isSelected}
                  aria-controls={`tab-panel-${mode.id}`}
                  tabIndex={isSelected ? 0 : -1}
                  onClick={() => setActive(mode.id)}
                  onKeyDown={(e) => handleKeyDown(e, index)}
                  className={`flex-1 min-w-[96px] inline-flex items-center justify-center gap-2 min-h-[44px] px-3 rounded-xl text-sm transition-all cursor-pointer ${
                    isSelected
                      ? "bg-surface text-pri-text font-semibold shadow-sm border border-pri"
                      : "bg-transparent text-text font-medium border border-transparent hover:bg-surface/50"
                  }`}
                >
                  <span>{mode.label}</span>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full tabular-nums ${isOn ? "bg-pri-soft text-pri-text" : "bg-surface2 text-text2"}`}>
                    {isOn ? "On" : "Off"}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="space-y-4">{renderMode()}</div>

          <div className="flex flex-wrap items-center gap-3">
            <button type="button" className={primaryClass} onClick={onSave} disabled={saving}>
              {saving ? "Saving…" : "Save group share"}
            </button>
            {lane.dirty && !saving && <p className="text-sm text-text2">Unsaved group content changes.</p>}
            {status}
          </div>
        </>
      )}
    </section>
  );
}
