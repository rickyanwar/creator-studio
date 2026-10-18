"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import {
  listAiVideoTemplates,
  listAiVideoIdeas,
  createAiVideoIdea,
  approveAiVideoIdea,
  rejectAiVideoIdea,
  listAiVideoJobs,
  retryAiVideoJob,
  cancelAiVideoJob,
  listGflowAccounts,
  updateGflowAccount,
  listGflowWorkers,
} from "@/lib/api";
import type {
  AiVideoIdea,
  AiVideoJob,
  AiVideoTemplate,
  Fanpage,
  GflowAccount,
  GflowWorker,
} from "@/lib/types";
import ClipPlayer from "@/components/ClipPlayer";
import { ResearchIdeaDetails } from "@/components/ai-video/ResearchIdeaDetails";
import { useFanpages } from "@/lib/useFanpages";

const statusOptions = [
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

export default function AiVideoPage() {
  const [tab, setTab] = useState<"ideas" | "jobs" | "accounts">("ideas");
  const [ideaStatus, setIdeaStatus] = useState("proposed");
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
    { refreshInterval: 15000 }
  );

  const enabledFanpages = fanpages.filter((fp) => fp.ai_video_enabled);
  const credits = accounts
    .filter((account) => account.enabled && account.status === "ok")
    .reduce((sum, account) => sum + (account.credits ?? 0), 0);

  const toDecideCount = ideas.filter(
    (i) => i.status === "proposed" || i.status === "pilot"
  ).length;

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
        <div className="flex flex-wrap items-center gap-2">
          {workers.map((worker) => {
            const pausedUntil = worker.paused_until ? new Date(worker.paused_until + "Z") : null;
            const paused = !!pausedUntil && pausedUntil.getTime() > Date.now();
            return (
              <span
                key={worker.worker_id}
                className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-xs font-semibold ${
                  worker.online
                    ? paused
                      ? "bg-warn-soft text-warn"
                      : "bg-pri-soft text-pri-text"
                    : "bg-surface2 text-text2 border border-line"
                }`}
              >
                <span
                  aria-hidden="true"
                  className="w-1.5 h-1.5 rounded-full bg-current shrink-0"
                />
                <span>
                  {worker.online && paused && pausedUntil
                    ? `${worker.worker_id} paused until ${pausedUntil.toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })} · Google unusual activity`
                    : `${worker.worker_id} ${
                        worker.online
                          ? `online · ${worker.running_job_ids.length} job · ${worker.mem_available_mb ?? "?"} MB free`
                          : "offline"
                      }`}
                </span>
              </span>
            );
          })}
          <span className="inline-flex items-center h-8 px-3 rounded-full bg-surface2 text-text text-xs font-semibold border border-line">
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
            {toDecideCount > 0 ? `${toDecideCount} to decide` : ideas.length}
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
          <span className="text-xs text-text2">{jobs.length}</span>
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
                    {s[0].toUpperCase() + s.slice(1)}
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

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {jobs.map((job) => {
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

            {jobs.length === 0 && (
              <div className="col-span-full p-12 text-center text-sm text-text2 bg-surface border border-line rounded-xl">
                No video jobs found.
              </div>
            )}
          </div>
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
        <StatusPill value={account.status} />
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
