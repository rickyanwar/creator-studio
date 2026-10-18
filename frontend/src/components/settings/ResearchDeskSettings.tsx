"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { getResearchActivity, updateSettings } from "@/lib/api";
import type { AppSettings, ResearchActivity, ResearchLink, ResearchReferences } from "@/lib/types";

const NICHES = ["f1", "motogp"] as const;
const KINDS = [
  { key: "video_style", label: "Video style (reels to learn the format from)" },
  { key: "topic_signals", label: "Topic signals (posts that show which topics are hot)" },
] as const;

function toText(links: ResearchLink[] | undefined) {
  return (links ?? []).map((l) => (l.note ? `${l.url} | ${l.note}` : l.url)).join("\n");
}

function fromText(text: string): ResearchLink[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [url, ...note] = line.split("|");
      const n = note.join("|").trim();
      return n ? { url: url.trim(), note: n } : { url: url.trim() };
    });
}

function when(value: string | null | undefined) {
  return value ? new Date(value.endsWith("Z") ? value : `${value}Z`).toLocaleString() : "never";
}

/** Who researches Mode 8 ideas (Muse or Hermes), Muse's pilot settings, references and activity. */
export function ResearchDeskSettings({
  settings,
  onChanged,
}: {
  settings: AppSettings | undefined;
  onChanged: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [packs, setPacks] = useState<number | string>(settings?.muse_pilot_packs ?? 3);
  const [bankDays, setBankDays] = useState<number | string>(settings?.ai_video_idea_bank_days ?? 3);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const { data: activity } = useSWR("research-activity", () =>
    getResearchActivity(15).then((r) => r.data as ResearchActivity), { refreshInterval: 60000 }
  );

  useEffect(() => {
    const refs = (settings?.research_references ?? {}) as ResearchReferences;
    const next: Record<string, string> = {};
    for (const niche of NICHES) {
      for (const kind of KINDS) {
        next[`${niche}.${kind.key}`] = toText(refs[niche]?.[kind.key]);
      }
    }
    setDrafts(next);
    setPacks(settings?.muse_pilot_packs ?? 3);
    setBankDays(settings?.ai_video_idea_bank_days ?? 3);
  }, [settings?.research_references, settings?.muse_pilot_packs, settings?.ai_video_idea_bank_days]);

  async function save(data: Record<string, unknown>) {
    setError(null);
    setSaved(false);
    try {
      await updateSettings(data);
      setSaved(true);
      onChanged();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Failed to save research settings");
    }
  }

  function saveReferences() {
    const refs: Record<string, Record<string, ResearchLink[]>> = {};
    for (const niche of NICHES) {
      refs[niche] = {};
      for (const kind of KINDS) {
        refs[niche][kind.key] = fromText(drafts[`${niche}.${kind.key}`] ?? "");
      }
    }
    const parsed = typeof packs === "string" ? parseInt(packs, 10) : packs;
    const parsedDays = typeof bankDays === "string" ? parseInt(bankDays, 10) : bankDays;
    save({
      research_references: refs,
      muse_pilot_packs: Math.max(0, Math.min(10, Number.isNaN(parsed) ? 3 : parsed)),
      ai_video_idea_bank_days: Math.max(1, Math.min(7, Number.isNaN(parsedDays) ? 3 : parsedDays)),
    });
  }

  const researcher = settings?.ai_video_researcher ?? "muse";
  const mode = settings?.muse_research_mode ?? "pilot";
  const report = settings?.research_last_report;

  return (
    <div className="space-y-4 border-t border-line pt-4">
      <div>
        <h3 className="text-sm font-semibold text-text">Researcher</h3>
        <p className="text-[11px] text-text2 mt-0.5">
          Who finds and researches AI video ideas. The other one stays as a fallback.
        </p>
      </div>

      {error && <p className="text-xs text-bad bg-bad-soft rounded-lg p-2">{error}</p>}

      <select
        className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full sm:w-64"
        value={researcher}
        onChange={(e) => save({ ai_video_researcher: e.target.value })}
      >
        <option value="muse">Muse (research desk API)</option>
        <option value="hermes">Hermes</option>
      </select>

      <div>
        <label className="block text-xs font-semibold text-text mb-1">Idea bank (days)</label>
        <input
          type="number"
          min={1}
          max={7}
          className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-36"
          value={bankDays}
          onChange={(e) => setBankDays(e.target.value)}
          onBlur={() => {
            const days = typeof bankDays === "string" ? parseInt(bankDays, 10) : bankDays;
            save({ ai_video_idea_bank_days: Math.max(1, Math.min(7, Number.isNaN(days) ? 3 : days)) });
          }}
        />
        <p className="text-[11px] text-text2 mt-1">
          Each AI video brand keeps videos per day × this many days of ideas waiting. The emptiest brand is filled first.
        </p>
      </div>

      {researcher === "muse" && (
        <>
          <div>
            <label className="block text-xs font-semibold text-text mb-1">Muse mode</label>
            <select
              className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full sm:w-64"
              value={settings?.muse_research_mode ?? "pilot"}
              onChange={(e) => save({ muse_research_mode: e.target.value })}
            >
              <option value="pilot">Pilot (I approve every idea)</option>
              <option value="live">Live (approve automatically)</option>
            </select>
          </div>

          <p className="text-xs bg-info-soft text-info rounded-lg p-3">
            {mode === "live" ? (
              <>
                Mode: <b>Live</b>. Muse&apos;s ideas that pass the studio&apos;s checks are approved at once when their score reaches the fanpage&apos;s minimum score, and go straight to production. Reject one on the AI Video page to tell Muse why.
              </>
            ) : (
              <>
                Mode: <b>Pilot</b>. Muse&apos;s ideas arrive with status <b>pilot</b> on the AI Video page and are never
                produced until you approve them. Reject with a reason or send feedback there; Muse reads it on its next run.
              </>
            )}
          </p>

          {mode === "pilot" && (
            <div>
              <label className="block text-xs font-semibold text-text mb-1">Pilot ideas per channel</label>
              <input
                type="number"
                min={0}
                max={10}
                className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-36"
                value={packs}
                onChange={(e) => setPacks(e.target.value)}
              />
              <p className="text-[11px] text-text2 mt-1">
                Muse stops researching for a channel once this many of its ideas wait for your review. Approving or rejecting one frees a slot.
              </p>
            </div>
          )}

          {NICHES.map((niche) => (
            <div key={niche} className="space-y-2">
              <p className="text-xs font-semibold text-text uppercase">{niche} references</p>
              {KINDS.map((kind) => (
                <div key={kind.key}>
                  <label className="text-[11px] text-text2">{kind.label}</label>
                  <textarea
                    className="w-full text-xs min-h-[70px] font-mono p-2.5 rounded-[8px] border border-line bg-surface text-text"
                    placeholder="https://www.instagram.com/account/ | optional note (one link per line)"
                    value={drafts[`${niche}.${kind.key}`] ?? ""}
                    onChange={(e) => setDrafts({ ...drafts, [`${niche}.${kind.key}`]: e.target.value })}
                  />
                </div>
              ))}
            </div>
          ))}
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 transition-opacity"
              onClick={saveReferences}
            >
              Save research settings
            </button>
            {saved && <span className="text-xs text-pri-text font-medium">Saved</span>}
          </div>

          <div className="space-y-2">
            <p className="text-xs font-semibold text-text">
              Muse activity · last contact {when(settings?.research_last_call_at)}
            </p>
            {(activity?.notes ?? []).length > 0 && (
              <div>
                <p className="text-[11px] text-text2 mb-1">Notes from Muse</p>
                <ul className="space-y-1">
                  {activity!.notes.map((note) => (
                    <li key={note.id} className="text-xs bg-surface2 rounded-lg p-2.5 whitespace-pre-wrap text-text">
                      <span className="text-[10px] text-text2 mr-1">{when(note.created_at)}</span>
                      {note.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <ul className="text-[11px] text-text2 space-y-0.5 max-h-56 overflow-y-auto font-mono">
              {(activity?.calls ?? []).map((call) => (
                <li key={call.id} className="break-all">
                  {when(call.created_at)} · {call.endpoint} · {call.action}
                  {call.detail ? ` · ${JSON.stringify(call.detail).slice(0, 160)}` : ""}
                </li>
              ))}
              {(activity?.calls ?? []).length === 0 && <li>No calls from Muse yet.</li>}
            </ul>
            {report && (
              <div>
                <p className="text-[11px] text-text2 mb-1">Last run report</p>
                <pre className="text-[11px] bg-surface2 border border-line rounded-lg p-2.5 overflow-x-auto text-text font-mono">
                  {JSON.stringify(report, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
