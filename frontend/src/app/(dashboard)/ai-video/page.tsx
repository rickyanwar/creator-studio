"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import {
  listAiVideoTemplates,
  listAiVideoIdeas,
  createAiVideoIdea,
  approveAiVideoIdea,
  rejectAiVideoIdea,
  listAiVideoJobs,
  getAiVideoCounts,
  retryAiVideoJob,
  cancelAiVideoJob,
  listGflowAccounts,
  updateGflowAccount,
  listGflowWorkers,
  getPromptPolishIdeaStates,
  getPromptPolishIdea,
  getPromptPolishJob,
} from "@/lib/api";
import type {
  AiVideoCounts,
  AiVideoIdea,
  AiVideoJob,
  AiVideoTemplate,
  Fanpage,
  GflowAccount,
  GflowWorker,
  PromptPolishIdea,
  PromptPolishJob,
} from "@/lib/types";
import ClipPlayer from "@/components/ClipPlayer";
import { ResearchIdeaDetails } from "@/components/ai-video/ResearchIdeaDetails";
import { PromptPolishView } from "@/components/ai-video/PromptPolishView";
import { useFanpages } from "@/lib/useFanpages";

const statusOptions = [
  "active",
  "pilot",
  "proposed",
  "approved",
  "queued",
  "done",
  "rejected",
  "failed",
  "all",
];

const statusStyles: Record<string, { bg: string; fg: string }> = {
  pilot: { bg: "var(--warn-soft)", fg: "var(--warn)" },
  proposed: { bg: "var(--warn-soft)", fg: "var(--warn)" },
  approved: { bg: "var(--pri-soft)", fg: "var(--pri-text)" },
  queued: { bg: "var(--info-soft)", fg: "var(--info)" },
  generating: { bg: "var(--info-soft)", fg: "var(--info)" },
  claimed: { bg: "var(--info-soft)", fg: "var(--info)" },
  uploaded: { bg: "var(--info-soft)", fg: "var(--info)" },
  done: { bg: "var(--pri-soft)", fg: "var(--pri-text)" },
  rejected: { bg: "var(--surface2)", fg: "var(--text2)" },
  failed: { bg: "var(--bad-soft)", fg: "var(--bad)" },
  needs_terms: { bg: "var(--warn-soft)", fg: "var(--warn)" },
  ok: { bg: "var(--pri-soft)", fg: "var(--pri-text)" },
};

function StatusPill({ value }: { value: string }) {
  const norm = value.toLowerCase().trim();
  const style = statusStyles[norm] || { bg: "var(--surface2)", fg: "var(--text2)" };
  return (
    <span
      className="text-xs px-2.5 py-0.5 rounded-full font-semibold capitalize whitespace-nowrap"
      style={{ background: style.bg, color: style.fg }}
    >
      {value.replace(/_/g, " ")}
    </span>
  );
}

const WORKER_STEPS = [
  { key: "claimed", label: "Claimed" },
  { key: "generating", label: "Generating" },
  { key: "uploaded", label: "Uploading" },
  { key: "done", label: "Done" },
];

function getWorkerStepIndex(status: string): number {
  switch (status) {
    case "claimed":
      return 0;
    case "generating":
      return 1;
    case "uploaded":
      return 2;
    case "done":
      return 3;
    default:
      return 1;
  }
}

function formatElapsed(startedAt: string | null | undefined, now: number): string {
  if (!startedAt) return "0:00";
  const startMs = new Date(startedAt.endsWith("Z") ? startedAt : startedAt + "Z").getTime();
  if (isNaN(startMs)) return "0:00";
  const elapsedSec = Math.max(0, Math.floor((now - startMs) / 1000));
  const m = Math.floor(elapsedSec / 60);
  const s = elapsedSec % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

function formatCountdown(targetAt: string | null | undefined, now: number): string {
  if (!targetAt) return "0:00";
  const targetMs = new Date(targetAt.endsWith("Z") ? targetAt : targetAt + "Z").getTime();
  if (isNaN(targetMs)) return "0:00";
  const remainSec = Math.max(0, Math.floor((targetMs - now) / 1000));
  const m = Math.floor(remainSec / 60);
  const s = remainSec % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

function formatHHmm(isoString: string | null | undefined): string {
  if (!isoString) return "";
  const dt = new Date(isoString.endsWith("Z") ? isoString : isoString + "Z");
  if (isNaN(dt.getTime())) return "";
  return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function getMinutesAgo(isoString: string | null | undefined, now: number): number | null {
  if (!isoString) return null;
  const dt = new Date(isoString.endsWith("Z") ? isoString : isoString + "Z");
  if (isNaN(dt.getTime())) return null;
  return Math.max(1, Math.round((now - dt.getTime()) / 60000));
}

function formatMinutesAgo(isoString: string | null | undefined, now: number): string {
  const mins = getMinutesAgo(isoString, now);
  if (mins === null) return "";
  return `${mins} min`;
}

function WorkerCard({ worker, now }: { worker: GflowWorker; now: number }) {
  const act = worker.activity ?? {
    state: worker.online ? "idle" : "offline",
    job: null,
    checking: null,
    resting_until: worker.paused_until,
    retry_at: null,
    queued: 0,
    next_account_free_at: null,
  };
  const state = act.state;

  const tileStyles: Record<string, string> = {
    working: "bg-pri-soft text-pri-text",
    checking: "bg-info-soft text-info",
    resting: "bg-warn-soft text-warn",
    waiting: "bg-warn-soft text-warn",
    stalled: "bg-warn-soft text-warn",
    idle: "bg-surface2 text-text2",
    offline: "bg-bad-soft text-bad",
  };

  const tileIcons: Record<string, string> = {
    working: "svg-spinners:ring-resize",
    checking: "solar:wallet-money-bold",
    resting: "solar:cup-hot-bold",
    waiting: "solar:hourglass-line-bold",
    stalled: "solar:refresh-circle-bold",
    idle: "solar:moon-sleep-bold",
    offline: "solar:cloud-cross-bold",
  };

  const stateTitles: Record<string, string> = {
    working: "Generating video",
    checking: "Checking credits",
    resting: "Resting",
    waiting: "Waiting for an account",
    stalled: "Stalled",
    idle: "Idle",
    offline: "Offline",
  };

  const job = act.job;
  const currentStepIdx = job ? getWorkerStepIndex(job.status) : 1;
  const clipsDone = job?.clips_done ?? 0;
  const clipsTotal = Math.max(job?.clips_total ?? 1, 1);
  const progressPercent = Math.min(100, Math.round((clipsDone / clipsTotal) * 100));

  const restingUntil = act.resting_until || worker.paused_until;
  const remRestMs = restingUntil
    ? Math.max(0, new Date(restingUntil.endsWith("Z") ? restingUntil : restingUntil + "Z").getTime() - now)
    : 0;
  const restPercent = Math.min(100, Math.max(0, Math.round((remRestMs / (4 * 60 * 1000)) * 100)));

  const checkingDone = act.checking?.done ?? 0;
  const checkingTotal = act.checking?.total;
  const checkingPercent =
    checkingTotal != null && checkingTotal > 0
      ? Math.min(100, Math.max(0, Math.round((checkingDone / checkingTotal) * 100)))
      : 0;

  const minsAgo = getMinutesAgo(worker.last_seen_at, now);

  return (
    <div
      role="status"
      aria-live="polite"
      className="w-full sm:w-auto min-w-0 sm:min-w-[320px] max-w-[520px] rounded-2xl border border-line bg-surface shadow-sm p-4 grid grid-cols-[44px_1fr] sm:grid-cols-[44px_1fr_auto] gap-x-3.5 gap-y-2 items-start min-h-[110px] text-xs transition-colors duration-200"
    >
      <style>{`
        @keyframes workerShimmer {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(100%); }
        }
        @media (prefers-reduced-motion: no-preference) {
          .animate-worker-shimmer {
            animation: workerShimmer 1.8s infinite;
          }
        }
      `}</style>

      {/* Column 1: Icon tile */}
      <div
        className={`w-11 h-11 rounded-xl shrink-0 flex items-center justify-center ${
          tileStyles[state] || tileStyles.idle
        }`}
      >
        <Icon icon={tileIcons[state] || tileIcons.idle} width={24} height={24} className="shrink-0" />
      </div>

      {/* Column 2: Content */}
      <div className="col-start-2 row-start-1 min-w-0 flex flex-col justify-between h-full gap-1">
        {/* Title row */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-text tracking-tight">
            {stateTitles[state] || "Worker"}
          </span>
          {state === "working" && (
            <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
              <span className="motion-safe:animate-ping absolute inline-flex h-full w-full rounded-full bg-pri opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-pri" />
            </span>
          )}
          {state === "checking" && (
            <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
              <span className="motion-safe:animate-ping absolute inline-flex h-full w-full rounded-full bg-info opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-info" />
            </span>
          )}
          {(state === "resting" || state === "waiting" || state === "stalled") && (
            <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
              <span className="motion-safe:animate-pulse relative inline-flex rounded-full h-2 w-2 bg-warn" />
            </span>
          )}
          {state === "idle" && (
            <span className="inline-flex rounded-full h-1.5 w-1.5 bg-text2/40 shrink-0" aria-hidden="true" />
          )}
          {state === "offline" && (
            <span className="inline-flex rounded-full h-1.5 w-1.5 bg-bad/40 shrink-0" aria-hidden="true" />
          )}
        </div>

        {/* Subtitle */}
        <div className="min-w-0">
          {state === "working" && (
            <div className="text-xs text-text2 truncate">
              {job ? `${job.fanpage_name || "Video"} · #${job.id} · ${job.account || "flow"}` : "Generating…"}
            </div>
          )}

          {state === "checking" && (
            <div className="flex items-center gap-1.5 text-xs text-text2 truncate">
              <Icon icon="svg-spinners:3-dots-fade" width={14} height={14} className="shrink-0 text-info" />
              <span className="truncate">
                Reading Flow balances ·{" "}
                {checkingTotal != null ? `${checkingDone}/${checkingTotal} accounts` : "Starting…"}
              </span>
            </div>
          )}

          {state === "resting" && (
            <div className="text-xs text-text2 truncate">
              Anti-bot pause · next video in {formatCountdown(restingUntil, now)}
            </div>
          )}

          {state === "waiting" && (
            <div className="text-xs text-text2 truncate">
              {act.queued} queued ·{" "}
              {act.next_account_free_at
                ? `next account free at ${formatHHmm(act.next_account_free_at)}`
                : "no free account"}
            </div>
          )}

          {state === "stalled" && (
            <div className="text-xs text-text2 truncate">
              {job ? `${job.fanpage_name || "Video"} · #${job.id} · ` : ""}
              retrying {act.retry_at ? `at ${formatHHmm(act.retry_at)}` : "soon"}
            </div>
          )}

          {state === "idle" && (
            <div className="text-xs text-text2 truncate">Queue empty</div>
          )}

          {state === "offline" && (
            <div className="text-xs text-text2 truncate">
              {minsAgo != null ? (
                <>
                  Last seen {minsAgo} min ago
                  {minsAgo < 10 && <span className="opacity-60"> · restarting?</span>}
                </>
              ) : (
                "Offline"
              )}
            </div>
          )}
        </div>

        {/* Bottom area (fixed height 28 px) */}
        <div className="mt-1">
          {state === "working" && (
            <div className="h-7 flex flex-col justify-between">
              {/* Stepper + Elapsed */}
              <div className="flex items-center justify-between gap-1 text-[10px] leading-tight">
                <div className="flex items-center gap-1 min-w-0 truncate">
                  {WORKER_STEPS.map((s, idx) => {
                    const isCurrent = idx === currentStepIdx;
                    const isPast = idx < currentStepIdx;
                    return (
                      <span key={s.key} className="inline-flex items-center gap-1 shrink-0">
                        <span
                          className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                            isPast
                              ? "bg-pri"
                              : isCurrent
                              ? "bg-pri ring-2 ring-pri-soft"
                              : "bg-line"
                          }`}
                        />
                        <span
                          className={`leading-none ${
                            isCurrent
                              ? "font-bold text-pri-text"
                              : isPast
                              ? "text-text font-medium"
                              : "text-text2/50"
                          }`}
                        >
                          {s.label}
                        </span>
                        {idx < WORKER_STEPS.length - 1 && (
                          <span className="text-line text-[9px] mx-0.5">·</span>
                        )}
                      </span>
                    );
                  })}
                </div>
                <span className="font-mono text-[10px] tabular-nums text-text2 shrink-0 ml-auto pl-1">
                  {formatElapsed(job?.started_at, now)}
                </span>
              </div>

              {/* Progress bar */}
              <div className="w-full bg-surface2 h-1.5 rounded-full overflow-hidden relative">
                <div
                  className="h-full bg-pri rounded-full transition-all duration-300 relative overflow-hidden"
                  style={{ width: `${progressPercent}%` }}
                >
                  <div className="absolute inset-0 motion-safe:animate-worker-shimmer bg-gradient-to-r from-transparent via-white/40 to-transparent" />
                </div>
              </div>
            </div>
          )}

          {state === "checking" && (
            <div className="h-7 flex flex-col justify-center">
              <div className="w-full bg-surface2 h-1.5 rounded-full overflow-hidden relative">
                {checkingTotal != null ? (
                  <div
                    className="h-full bg-info rounded-full transition-all duration-300 relative overflow-hidden"
                    style={{ width: `${checkingPercent}%` }}
                  >
                    <div className="absolute inset-0 motion-safe:animate-worker-shimmer bg-gradient-to-r from-transparent via-white/40 to-transparent" />
                  </div>
                ) : (
                  <div className="h-full w-full bg-info/20 rounded-full relative overflow-hidden">
                    <div className="absolute inset-0 motion-safe:animate-worker-shimmer bg-gradient-to-r from-transparent via-info to-transparent" />
                  </div>
                )}
              </div>
            </div>
          )}

          {state === "resting" && (
            <div className="h-7 flex flex-col justify-center">
              <div className="w-full bg-surface2 h-1.5 rounded-full overflow-hidden">
                <div
                  className="h-full bg-warn rounded-full transition-all duration-1000"
                  style={{ width: `${restPercent}%` }}
                />
              </div>
            </div>
          )}

          {state !== "working" && state !== "checking" && state !== "resting" && (
            <div className="h-7" aria-hidden="true" />
          )}
        </div>
      </div>

      {/* Column 3: Right meta (stacked under content on small screens) */}
      <div className="col-start-2 sm:col-start-3 row-start-2 sm:row-start-1 flex items-center justify-between sm:flex-col sm:items-end sm:text-right text-[11px] text-text2 mt-1 sm:mt-0 shrink-0">
        <span className="font-mono opacity-70 tracking-tight truncate max-w-[140px]" title={worker.worker_id}>
          {worker.worker_id}
        </span>
        {state !== "offline" && worker.mem_available_mb != null && (
          <span className="opacity-70 sm:mt-0.5 whitespace-nowrap">
            {worker.mem_available_mb} MB free
          </span>
        )}
      </div>
    </div>
  );
}

function IdeaPolishSection({ ideaId, state }: { ideaId: number; state: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<PromptPolishIdea | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    if (!open && !data) {
      setLoading(true);
      setError(null);
      try {
        const res = await getPromptPolishIdea(ideaId);
        setData(res.data);
        setOpen(true);
      } catch (err: any) {
        setError(err?.response?.data?.detail || "Failed to load prompts");
      } finally {
        setLoading(false);
      }
    } else {
      setOpen(!open);
    }
  };

  return (
    <div className="pt-1">
      <button
        type="button"
        onClick={toggle}
        disabled={loading}
        className="text-xs font-semibold text-text2 hover:text-text cursor-pointer inline-flex items-center gap-1"
      >
        <span>{open ? "Hide prompts" : "Show prompts"}</span>
        {loading && <span className="text-[10px] text-text2 font-normal">loading…</span>}
      </button>
      {error && <p className="text-xs text-bad mt-1">{error}</p>}
      {open && data && (
        <div className="mt-2 space-y-2">
          {state === "failed" && data.polish_error && (
            <p className="text-xs text-bad bg-bad-soft p-2 rounded-lg border border-bad/20">
              Polish error: {data.polish_error}
            </p>
          )}
          <PromptPolishView clips={data.clips} changes={data.changes} />
        </div>
      )}
    </div>
  );
}

function JobPolishSection({ jobId }: { jobId: number }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<PromptPolishJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    if (!open && !data) {
      setLoading(true);
      setError(null);
      try {
        const res = await getPromptPolishJob(jobId);
        setData(res.data);
        setOpen(true);
      } catch (err: any) {
        setError(err?.response?.data?.detail || "Failed to load prompts");
      } finally {
        setLoading(false);
      }
    } else {
      setOpen(!open);
    }
  };

  const btnLabel = data?.polished ? "Show prompts (Argon)" : "Show prompts";
  const closeLabel = data?.polished ? "Hide prompts (Argon)" : "Hide prompts";

  return (
    <div className="pt-1">
      <button
        type="button"
        onClick={toggle}
        disabled={loading}
        className="text-[11px] font-medium text-text2 hover:text-text cursor-pointer inline-flex items-center gap-1"
      >
        <span>{open ? closeLabel : btnLabel}</span>
        {loading && <span className="text-[10px] text-text2 font-normal">loading…</span>}
      </button>
      {error && <p className="text-[11px] text-bad mt-1">{error}</p>}
      {open && data && (
        <div className="mt-2">
          <PromptPolishView clips={data.clips} changes={data.changes} />
        </div>
      )}
    </div>
  );
}

export default function AiVideoPage() {
  const [tab, setTab] = useState<"ideas" | "jobs" | "accounts">("ideas");
  const [ideaStatus, setIdeaStatus] = useState("active");
  const [fanpageId, setFanpageId] = useState<number | "">("");
  const [busy, setBusy] = useState<number | null>(null);
  const [actionError, setActionError] = useState<{ id: number; message: string } | null>(null);
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});

  const { data: fanpages = [] } = useFanpages();
  const { data: templates = [] } = useSWR("ai-video-templates", () =>
    listAiVideoTemplates().then((r) => r.data as AiVideoTemplate[])
  );
  const { data: ideas = [], mutate: mutateIdeas } = useSWR(
    ["ai-video-ideas", ideaStatus, fanpageId],
    () =>
      listAiVideoIdeas({
        status: ideaStatus === "all" ? undefined : ideaStatus,
        fanpage_id: fanpageId || undefined,
        limit: 100,
      }).then((r) => r.data as AiVideoIdea[])
  );
  const ideaIds = ideas.map((i) => i.id);
  const ideaIdsKey = ideaIds.join(",");
  const { data: polishStates = {} } = useSWR(
    ideaIdsKey ? ["prompt-polish-idea-states", ideaIdsKey] : null,
    () => getPromptPolishIdeaStates(ideaIds).then((r) => r.data),
    { refreshInterval: 15000 }
  );
  const { data: jobs = [], mutate: mutateJobs } = useSWR(
    "ai-video-jobs",
    () => listAiVideoJobs({ limit: 100 }).then((r) => r.data as AiVideoJob[]),
    { refreshInterval: 15000 }
  );
  const { data: accounts = [], mutate: mutateAccounts } = useSWR(
    "gflow-accounts",
    () => listGflowAccounts().then((r) => r.data as GflowAccount[]),
    { refreshInterval: 30000 }
  );
  const { data: workers = [] } = useSWR(
    "gflow-workers",
    () => listGflowWorkers().then((r) => r.data as GflowWorker[]),
    { refreshInterval: 5000 }
  );
  const { data: counts } = useSWR(
    ["ai-video-counts", fanpageId],
    () => getAiVideoCounts(fanpageId ? { fanpage_id: fanpageId } : undefined).then((r) => r.data as AiVideoCounts),
    { refreshInterval: 15000 }
  );

  // Load-more state for jobs (first page kept by the SWR above; extra pages appended here)
  const [extraJobs, setExtraJobs] = useState<AiVideoJob[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const PAGE_SIZE = 100;

  const [ticker, setTicker] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setTicker(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const enabledFanpages = fanpages.filter((fp) => fp.ai_video_enabled);
  const credits = accounts
    .filter((account) => account.enabled && account.status === "ok")
    .reduce((sum, account) => sum + (account.credits ?? 0), 0);

  const toDecideCount = ideas.filter(
    (i) => i.status === "proposed" || i.status === "pilot"
  ).length;

  // Merge first-page jobs (auto-refreshed) with any extra pages loaded manually
  const allJobs = [...jobs, ...extraJobs];
  const jobsTotal = counts?.jobs_total ?? allJobs.length;
  const ideasActive = counts?.ideas_active ?? ideas.length;

  async function loadMoreJobs() {
    setLoadingMore(true);
    try {
      const next = await listAiVideoJobs({ limit: PAGE_SIZE, offset: allJobs.length });
      const newJobs = next.data as AiVideoJob[];
      setExtraJobs((prev) => [...prev, ...newJobs]);
    } finally {
      setLoadingMore(false);
    }
  }

  async function act(id: number, action: () => Promise<unknown>, mutate: () => void) {
    setBusy(id);
    setActionError(null);
    try {
      await action();
      mutate();
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? "Action failed";
      setActionError({ id, message: msg });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5 max-w-[1240px] w-full pb-10">
      {/* ── Page Header ── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-[26px] font-bold text-text tracking-tight">AI video</h1>
          <p className="text-sm text-text2">
            Ideas become Google Flow videos. Every scene must look real and make physical sense.
          </p>
        </div>

        {/* Worker and Credit Status Badges */}
        <div className="flex flex-wrap items-center gap-3 w-full sm:w-auto">
          {workers.map((worker) => (
            <WorkerCard key={worker.worker_id} worker={worker} now={ticker} />
          ))}
          <span className="inline-flex items-center h-8 px-3 rounded-full bg-surface2 text-text text-xs font-semibold border border-line self-start sm:self-center">
            {credits} credits left
          </span>
        </div>
      </div>

      {enabledFanpages.length === 0 && (
        <div className="bg-warn-soft border border-warn/20 text-warn p-3.5 rounded-xl text-xs font-medium">
          No brand has AI video turned on. Enable it in brand settings.
        </div>
      )}

      {/* ── Tabs Bar ── */}
      <div role="tablist" aria-label="View" className="self-start flex gap-1 p-1 rounded-xl bg-surface2 w-fit">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "ideas"}
          onClick={() => setTab("ideas")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            tab === "ideas" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>Ideas</span>
          <span className="text-xs text-text2">
            {toDecideCount > 0 ? `${toDecideCount} to decide` : ideasActive}
          </span>
        </button>

        <button
          type="button"
          role="tab"
          aria-selected={tab === "jobs"}
          onClick={() => setTab("jobs")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            tab === "jobs" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>Videos</span>
          <span className="text-xs text-text2">{jobsTotal}</span>
        </button>

        <button
          type="button"
          role="tab"
          aria-selected={tab === "accounts"}
          onClick={() => setTab("accounts")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            tab === "accounts" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>Flow accounts</span>
          <span className="text-xs text-text2">{accounts.length}</span>
        </button>
      </div>

      {/* ── TAB 1: Ideas ── */}
      {tab === "ideas" && (
        <div className="flex flex-wrap gap-4 items-start">
          {/* Left Column: Ideas list */}
          <div className="flex-[2_1_520px] min-w-0 flex flex-col gap-3">
            <p className="text-[13px] text-text2 leading-relaxed">
              Ideas that score at or above the brand&apos;s minimum (default 70) are approved automatically. The rest wait here for you. An empty minimum means always ask.
            </p>

            {/* Filter controls */}
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="h-9 px-3 rounded-lg border border-line bg-surface text-text text-xs focus:ring-1 focus:ring-pri"
                value={ideaStatus}
                onChange={(e) => setIdeaStatus(e.target.value)}
              >
                {statusOptions.map((s) => (
                  <option key={s} value={s}>
                    {s === "active" ? "Active (pilot, proposed, approved)" : s[0].toUpperCase() + s.slice(1)}
                  </option>
                ))}
              </select>

              <select
                className="h-9 px-3 rounded-lg border border-line bg-surface text-text text-xs focus:ring-1 focus:ring-pri"
                value={fanpageId}
                onChange={(e) => setFanpageId(e.target.value ? Number(e.target.value) : "")}
              >
                <option value="">All Brands</option>
                {fanpages.map((fp) => (
                  <option key={fp.id} value={fp.id}>
                    {fp.name}
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors disabled:opacity-50"
                disabled={ideas.length === 0}
                onClick={() => {
                  const blob = new Blob([JSON.stringify(ideas, null, 2)], {
                    type: "application/json",
                  });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `ai-video-ideas-${ideaStatus}-${new Date()
                    .toISOString()
                    .slice(0, 10)}.json`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                Export JSON
              </button>
            </div>

            {/* Idea cards list */}
            <div className="space-y-3">
              {ideas.map((idea) => {
                const isDecidable = idea.status === "proposed" || idea.status === "pilot";
                const polishState = polishStates[idea.id] || polishStates[String(idea.id)];
                return (
                  <article
                    key={idea.id}
                    className="bg-surface border border-line rounded-xl p-4 flex flex-col gap-2.5 shadow-sm"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-xs text-text2">
                        {idea.fanpage_name} · template {idea.template_key} · from {idea.source}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-info-soft text-info">
                          Score {idea.score ?? "—"}
                        </span>
                        <StatusPill value={idea.status} />
                        {polishState === "polished" && (
                          <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold whitespace-nowrap bg-pri-soft text-pri-text">
                            Polished (Argon)
                          </span>
                        )}
                        {polishState === "waiting" && (
                          <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold whitespace-nowrap bg-warn-soft text-warn">
                            Waiting for polish
                          </span>
                        )}
                        {polishState === "failed" && (
                          <span className="text-xs px-2.5 py-0.5 rounded-full font-semibold whitespace-nowrap bg-bad-soft text-bad">
                            Polish failed
                          </span>
                        )}
                      </div>
                    </div>

                    <h2 className="text-base font-bold text-text leading-snug">{idea.topic}</h2>

                    {idea.angle && (
                      <p className="text-sm text-text2 leading-relaxed">{idea.angle}</p>
                    )}

                    {idea.rationale && (
                      <p className="text-xs bg-surface2 p-3 rounded-lg border border-line whitespace-pre-wrap text-text">
                        {idea.rationale}
                      </p>
                    )}

                    {idea.error && (
                      <p className="text-xs text-bad bg-bad-soft p-2 rounded-lg border border-bad/20">
                        {idea.error}
                      </p>
                    )}

                    {idea.source === "muse" && <ResearchIdeaDetails idea={idea} />}

                    {(polishState === "polished" || polishState === "failed") && (
                      <IdeaPolishSection ideaId={idea.id} state={polishState} />
                    )}

                    {actionError?.id === idea.id && (
                      <p className="text-xs text-bad bg-bad-soft rounded p-2 border border-bad/20">
                        {actionError.message}
                      </p>
                    )}

                    {/* Action buttons if proposed or pilot */}
                    {isDecidable && (
                      <div className="flex flex-wrap items-center justify-end gap-2 pt-2.5 border-t border-line">
                        <input
                          type="text"
                          aria-label="Reason if rejecting"
                          placeholder="Reason, if you reject (sent to Muse)"
                          value={rejectReasons[idea.id] ?? ""}
                          onChange={(e) =>
                            setRejectReasons((prev) => ({ ...prev, [idea.id]: e.target.value }))
                          }
                          className="h-9 px-3 rounded-lg border border-line bg-surface text-text text-xs flex-[1_1_200px] focus:outline-none focus:ring-1 focus:ring-pri"
                        />
                        <button
                          type="button"
                          disabled={busy === idea.id}
                          onClick={() => {
                            const reason =
                              rejectReasons[idea.id]?.trim() ||
                              (idea.source === "muse"
                                ? window.prompt("Why is this rejected? Muse will read this.") ?? undefined
                                : undefined);
                            act(
                              idea.id,
                              () => rejectAiVideoIdea(idea.id, reason || undefined),
                              mutateIdeas
                            );
                          }}
                          className="h-9 px-3.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold transition-colors disabled:opacity-50"
                        >
                          Reject
                        </button>
                        <button
                          type="button"
                          disabled={busy === idea.id}
                          onClick={() =>
                            act(idea.id, () => approveAiVideoIdea(idea.id), mutateIdeas)
                          }
                          className="h-9 px-4 rounded-lg bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
                        >
                          Approve · make video
                        </button>
                      </div>
                    )}

                    {/* Remove button for approved ideas */}
                    {idea.status === "approved" && (
                      <div className="flex flex-wrap items-center justify-end gap-2 pt-2.5 border-t border-line">
                        <input
                          type="text"
                          aria-label="Reason for removing"
                          placeholder="Remove this idea? It will not be made."
                          value={rejectReasons[idea.id] ?? ""}
                          onChange={(e) =>
                            setRejectReasons((prev) => ({ ...prev, [idea.id]: e.target.value }))
                          }
                          className="h-9 px-3 rounded-lg border border-line bg-surface text-text text-xs flex-[1_1_200px] focus:outline-none focus:ring-1 focus:ring-pri"
                        />
                        <button
                          type="button"
                          disabled={busy === idea.id}
                          onClick={() => {
                            const reason = rejectReasons[idea.id]?.trim() || undefined;
                            act(
                              idea.id,
                              () => rejectAiVideoIdea(idea.id, reason),
                              () => { mutateIdeas(); }
                            );
                          }}
                          className="h-9 px-3.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold transition-colors disabled:opacity-50"
                        >
                          Remove
                        </button>
                      </div>
                    )}
                  </article>
                );
              })}

              {ideas.length === 0 && (
                <div className="p-10 text-center text-sm text-text2 bg-surface border border-line rounded-xl">
                  No ideas found.
                </div>
              )}
            </div>
          </div>

          {/* Right Column: New idea form */}
          <NewIdeaForm fanpages={fanpages} templates={templates} onCreated={mutateIdeas} />
        </div>
      )}

      {/* ── TAB 2: Videos (Jobs) ── */}
      {tab === "jobs" && (
        <div className="space-y-4">
          <p className="text-[13px] text-text2 leading-relaxed">
            Job steps: queued, claimed, generating, uploaded, done. A job can also end as failed or cancelled. A video that fails vision QC or the overlay is held for review, never marked failed. A retry spends credits again.
          </p>

          {allJobs.length > 0 && (
            <p className="text-xs text-text2">
              Showing {allJobs.length} of {jobsTotal} videos
            </p>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {allJobs.map((job) => {
              const progressPct =
                job.clips_total > 0 ? Math.round((job.clips_done / job.clips_total) * 100) : 0;

              return (
                <article
                  key={job.id}
                  className="bg-surface border border-line rounded-xl p-3.5 flex flex-col gap-2.5 shadow-sm"
                >
                  {/* Video preview / placeholder */}
                  <div className="w-full aspect-[9/16] max-h-64 rounded-lg overflow-hidden bg-surface2 border border-line flex items-center justify-center">
                    {job.video_url ? (
                      <ClipPlayer
                        src={job.video_url}
                        poster={job.thumbnail_url}
                        className="w-full h-full object-contain"
                      />
                    ) : (
                      <Icon
                        icon="solar:videocamera-record-bold-duotone"
                        width={36}
                        className="text-text2 opacity-40"
                      />
                    )}
                  </div>

                  <div className="flex items-center justify-between gap-1 text-xs">
                    <span className="text-text2 truncate">{job.fanpage_name}</span>
                    <StatusPill value={job.status} />
                  </div>

                  <h3 className="font-semibold text-text text-sm line-clamp-1">{job.title}</h3>

                  {/* Progress bar */}
                  <div className="h-1.5 rounded-full bg-surface2 overflow-hidden border border-line">
                    <div
                      className="h-full bg-pri rounded-full transition-all"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>

                  <div className="text-[11px] text-text2 flex justify-between">
                    <span>
                      clip {job.clips_done} of {job.clips_total}
                    </span>
                    <span>{job.credits_estimate} credits</span>
                  </div>

                  <div className="text-[11px] text-text2 truncate">
                    {job.account?.profile ?? "—"} · {job.worker_id ?? "—"}
                  </div>

                  {job.error && (
                    <p className="text-[11px] text-bad bg-bad-soft p-1.5 rounded border border-bad/20">
                      {job.error}
                    </p>
                  )}

                  {job.qc && (
                    <p
                      className={`text-[11px] ${
                        job.qc.passed ? "text-pri-text font-semibold" : "text-warn"
                      }`}
                    >
                      QC: {job.qc.passed ? "passed" : (job.qc.reasons ?? ["failed"]).join(", ")}
                    </p>
                  )}

                  <JobPolishSection jobId={job.id} />

                  {actionError?.id === job.id && (
                    <p className="text-[11px] text-bad bg-bad-soft p-1.5 rounded border border-bad/20">
                      {actionError.message}
                    </p>
                  )}

                  {/* Actions */}
                  <div className="flex flex-wrap gap-1.5 pt-1.5 border-t border-line mt-auto">
                    {["failed", "cancelled"].includes(job.status) && (
                      <button
                        type="button"
                        disabled={busy === job.id}
                        onClick={() => act(job.id, () => retryAiVideoJob(job.id), mutateJobs)}
                        className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors disabled:opacity-50"
                      >
                        Retry
                      </button>
                    )}
                    {["queued", "claimed", "generating"].includes(job.status) && (
                      <button
                        type="button"
                        disabled={busy === job.id}
                        onClick={() => act(job.id, () => cancelAiVideoJob(job.id), mutateJobs)}
                        className="h-8 px-3 rounded-lg border border-bad/30 bg-bad-soft hover:bg-bad/20 text-bad text-xs font-medium transition-colors disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    )}
                    {job.publish_job_id && (
                      <Link
                        href="/queue"
                        className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium inline-flex items-center justify-center transition-colors"
                      >
                        Review in queue
                      </Link>
                    )}
                  </div>
                </article>
              );
            })}

            {allJobs.length === 0 && (
              <div className="col-span-full p-12 text-center text-sm text-text2 bg-surface border border-line rounded-xl">
                No video jobs found.
              </div>
            )}
          </div>

          {allJobs.length < jobsTotal && (
            <div className="flex justify-center pt-2">
              <button
                type="button"
                className="h-9 px-4 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium transition-colors disabled:opacity-50"
                disabled={loadingMore}
                onClick={loadMoreJobs}
              >
                {loadingMore ? "Loading…" : `Load more (${jobsTotal - allJobs.length} remaining)`}
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── TAB 3: Flow Accounts ── */}
      {tab === "accounts" && (
        <div className="space-y-4">
          <p className="text-[13px] text-text2 leading-relaxed">
            Account status: OK · Needs terms · Auth error · No access.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {accounts.map((account) => (
              <AccountCard key={account.id} account={account} onChanged={mutateAccounts} />
            ))}
            {accounts.length === 0 && (
              <div className="col-span-full p-12 text-center text-sm text-text2 bg-surface border border-line rounded-xl">
                No GFlow accounts reported yet.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function NewIdeaForm({
  fanpages,
  templates,
  onCreated,
}: {
  fanpages: Fanpage[];
  templates: AiVideoTemplate[];
  onCreated: () => void;
}) {
  const [form, setForm] = useState({
    fanpage_id: "",
    template_key: "",
    topic: "",
    angle: "",
    research_notes: "",
    clips: "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedFanpage = fanpages.find((fp) => String(fp.id) === form.fanpage_id);
  const allowedTemplateKeys: string[] =
    selectedFanpage?.ai_video_templates ??
    (selectedFanpage?.ai_video_template ? [selectedFanpage.ai_video_template] : []);
  const availableTemplates =
    allowedTemplateKeys.length > 0
      ? templates.filter((item) => allowedTemplateKeys.includes(item.key))
      : templates;

  const template = availableTemplates.find((item) => item.key === form.template_key);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.fanpage_id || !form.template_key || !form.topic.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createAiVideoIdea({
        ...form,
        fanpage_id: Number(form.fanpage_id),
        clips: form.clips ? Number(form.clips) : undefined,
      });
      setForm({
        fanpage_id: "",
        template_key: "",
        topic: "",
        angle: "",
        research_notes: "",
        clips: "",
      });
      onCreated();
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? "Failed to create idea";
      setError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex-[1_1_300px] min-w-0 bg-surface border border-line rounded-xl p-4 sm:p-5 flex flex-col gap-3 shadow-sm h-fit"
    >
      <h2 className="text-[15px] font-bold text-text">New idea</h2>

      {error && (
        <p className="text-xs text-bad bg-bad-soft rounded-lg p-2 border border-bad/20">
          {error}
        </p>
      )}

      <label className="flex flex-col gap-1 text-xs font-semibold text-text">
        Brand
        <select
          required
          className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
          value={form.fanpage_id}
          onChange={(e) =>
            setForm({ ...form, fanpage_id: e.target.value, template_key: "", clips: "" })
          }
        >
          <option value="">Select Brand</option>
          {fanpages.map((fp) => (
            <option key={fp.id} value={fp.id}>
              {fp.name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs font-semibold text-text">
        Template
        <select
          required
          className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
          value={form.template_key}
          onChange={(e) => setForm({ ...form, template_key: e.target.value, clips: "" })}
        >
          <option value="">Select Template</option>
          {availableTemplates.map((item) => (
            <option key={item.key} value={item.key}>
              {item.name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs font-semibold text-text">
        Topic
        <input
          required
          className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
          placeholder="What the video is about"
          value={form.topic}
          onChange={(e) => setForm({ ...form, topic: e.target.value })}
        />
      </label>

      <label className="flex flex-col gap-1 text-xs font-semibold text-text">
        Angle (optional)
        <input
          className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
          placeholder="Why people will watch"
          value={form.angle}
          onChange={(e) => setForm({ ...form, angle: e.target.value })}
        />
      </label>

      <label className="flex flex-col gap-1 text-xs font-semibold text-text">
        Research notes
        <textarea
          className="p-3 rounded-lg border border-line bg-surface text-text text-xs focus:outline-none focus:ring-1 focus:ring-pri"
          rows={3}
          placeholder="Research notes (facts and details)"
          value={form.research_notes}
          onChange={(e) => setForm({ ...form, research_notes: e.target.value })}
        />
      </label>

      {template && (
        <label className="flex flex-col gap-1 text-xs font-semibold text-text">
          Length
          <select
            className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
            value={form.clips}
            onChange={(e) => setForm({ ...form, clips: e.target.value })}
          >
            <option value="">Default: {template.default_clips} clips</option>
            {template.clip_options.map((count) => (
              <option key={count} value={count}>
                {count} clips · {count * template.clip_seconds}s · ≈
                {count * template.credits_per_clip} credits
              </option>
            ))}
          </select>
        </label>
      )}

      <button
        type="submit"
        disabled={saving}
        className="h-10 px-4 mt-1 rounded-xl bg-pri text-pri-fg text-sm font-semibold hover:opacity-95 transition-opacity disabled:opacity-50"
      >
        {saving ? "Creating…" : "Add idea"}
      </button>
    </form>
  );
}

function AccountCard({
  account,
  onChanged,
}: {
  account: GflowAccount;
  onChanged: () => void;
}) {
  const [label, setLabel] = useState(account.label ?? "");
  const [error, setError] = useState<string | null>(null);

  async function save(data: { enabled?: boolean; label?: string; terms_accepted?: boolean }) {
    setError(null);
    try {
      await updateGflowAccount(account.id, data);
      onChanged();
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? "Failed to update account";
      setError(msg);
    }
  }

  return (
    <article className="bg-surface border border-line rounded-xl p-4 flex flex-col gap-2.5 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold text-text text-sm truncate">
          {account.label || account.profile}
        </h3>
        <div className="flex items-center gap-1.5">
          {account.cooldown_until && new Date(account.cooldown_until.endsWith("Z") ? account.cooldown_until : account.cooldown_until + "Z") > new Date() && (
            <span className="text-[11px] text-warn font-medium">
              Resting until {new Date(account.cooldown_until.endsWith("Z") ? account.cooldown_until : account.cooldown_until + "Z").toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <StatusPill value={account.status} />
        </div>
      </div>

      <p className="text-xs text-text2 truncate">
        {account.email || account.profile} · {account.worker_id}
      </p>

      <div className="flex justify-between items-center text-xs py-1 border-y border-line">
        <span className="text-text2">Usable credits</span>
        <b className="text-text text-sm tabular-nums">{account.credits ?? "—"}</b>
      </div>

      <p className="text-[11px] text-text2">
        Last seen:{" "}
        {account.last_seen_at
          ? new Date(account.last_seen_at + "Z").toLocaleString()
          : "Never"}
        {account.busy_job_id ? ` · Job #${account.busy_job_id} busy` : ""}
      </p>

      {account.status === "needs_terms" && (
        <div className="rounded-xl border border-warn/30 bg-warn-soft p-3 space-y-2">
          <p className="text-xs text-warn leading-relaxed">
            Open Flow on the worker browser, accept the new terms, then press the button below.
          </p>
          <button
            type="button"
            className="h-8 px-3 rounded-lg bg-pri text-pri-fg text-xs font-semibold hover:opacity-90"
            onClick={() => save({ terms_accepted: true })}
          >
            I accepted the terms
          </button>
        </div>
      )}

      {error && (
        <p className="text-xs text-bad bg-bad-soft rounded p-1.5 border border-bad/20">
          {error}
        </p>
      )}

      <div className="flex items-center gap-2 pt-1 mt-auto">
        <label className="flex items-center gap-1.5 text-xs text-text font-medium cursor-pointer select-none">
          <input
            type="checkbox"
            checked={account.enabled}
            onChange={(e) => save({ enabled: e.target.checked })}
            className="w-4 h-4 rounded text-pri focus:ring-pri"
          />
          Enabled
        </label>
        <input
          className="h-8 px-2.5 rounded-lg border border-line bg-surface text-text text-xs flex-1 focus:outline-none focus:ring-1 focus:ring-pri"
          value={label}
          placeholder="Label"
          onChange={(e) => setLabel(e.target.value)}
          onBlur={() => label !== (account.label ?? "") && save({ label })}
          onKeyDown={(e) => {
            if (e.key === "Enter" && label !== (account.label ?? "")) {
              save({ label });
            }
          }}
        />
      </div>
    </article>
  );
}
