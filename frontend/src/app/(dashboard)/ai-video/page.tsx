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
import { useFanpages } from "@/lib/useFanpages";

const statusOptions = [
  "proposed",
  "approved",
  "queued",
  "done",
  "rejected",
  "failed",
  "all",
];

const badge: Record<string, string> = {
  proposed: "bg-blue-100 text-blue-800",
  approved: "bg-emerald-100 text-emerald-800",
  queued: "bg-amber-100 text-amber-800",
  done: "bg-emerald-100 text-emerald-800",
  rejected: "bg-gray-200 text-gray-700",
  failed: "bg-red-100 text-red-800",
};

function Status({ value }: { value: string }) {
  return (
    <span
      className={`text-[10px] uppercase px-2 py-1 rounded font-bold ${
        badge[value] ?? "bg-divider-soft text-text-secondary"
      }`}
    >
      {value.replace("_", " ")}
    </span>
  );
}

export default function AiVideoPage() {
  const [tab, setTab] = useState<"ideas" | "jobs" | "accounts">("ideas");
  const [ideaStatus, setIdeaStatus] = useState("proposed");
  const [fanpageId, setFanpageId] = useState<number | "">("");
  const [busy, setBusy] = useState<number | null>(null);
  const [actionError, setActionError] = useState<{ id: number; message: string } | null>(null);

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
    <div className="max-w-7xl space-y-6 pb-10">
      <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">AI Video</h1>
          <p className="text-sm text-text-secondary mt-1">
            Google Flow generation, ideas, jobs, and publishing.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {workers.map((worker) => (
            <span
              key={worker.worker_id}
              className={`text-xs px-2.5 py-1 rounded-full ${
                worker.online
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-divider-soft text-text-secondary"
              }`}
            >
              <span
                className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${
                  worker.online ? "bg-emerald-500" : "bg-gray-400"
                }`}
              />
              {worker.worker_id} ·{" "}
              {worker.online
                ? `${worker.mem_available_mb ?? "?"} MB · ${worker.running_job_ids.length} running`
                : "offline"}
            </span>
          ))}
          <span className="text-xs px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-700">
            {credits} credits available
          </span>
        </div>
      </div>

      {enabledFanpages.length === 0 && (
        <div className="bg-warning-main/10 border border-warning-main/20 text-warning-main px-4 py-3 rounded-lg text-sm">
          No fanpage has Mode 8 enabled. Enable it from a fanpage settings page.
        </div>
      )}

      <div className="flex gap-1 bg-surface-secondary p-1 rounded-lg w-fit">
        {(["ideas", "jobs", "accounts"] as const).map((item) => (
          <button
            key={item}
            onClick={() => setTab(item)}
            className={`px-4 py-1.5 text-xs font-semibold rounded-md capitalize ${
              tab === item
                ? "bg-bg-paper text-primary-main shadow-sm"
                : "text-text-secondary"
            }`}
          >
            {item}
          </button>
        ))}
      </div>

      {tab === "ideas" && (
        <div className="grid grid-cols-1 xl:grid-cols-[340px_1fr] gap-6">
          <NewIdeaForm
            fanpages={fanpages}
            templates={templates}
            onCreated={mutateIdeas}
          />
          <div className="space-y-4">
            <div className="flex flex-col sm:flex-row gap-2">
              <select
                className="input-rect text-xs"
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
                className="input-rect text-xs"
                value={fanpageId}
                onChange={(e) =>
                  setFanpageId(e.target.value ? Number(e.target.value) : "")
                }
              >
                <option value="">All Fanpages</option>
                {fanpages.map((fp) => (
                  <option key={fp.id} value={fp.id}>
                    {fp.name}
                  </option>
                ))}
              </select>
            </div>

            {ideas.map((idea) => (
              <div key={idea.id} className="card p-4 space-y-3">
                <div className="flex justify-between gap-3">
                  <div>
                    <p className="text-xs text-text-secondary">
                      {idea.fanpage_name} · {idea.template_key}
                    </p>
                    <h3 className="font-semibold text-text-primary">
                      {idea.topic}
                    </h3>
                  </div>
                  <Status value={idea.status} />
                </div>

                <div className="flex gap-2 text-[10px]">
                  <span className="bg-indigo-50 text-indigo-700 px-2 py-1 rounded">
                    Score {idea.score ?? "—"}
                  </span>
                  <span className="bg-divider-soft text-text-secondary px-2 py-1 rounded uppercase">
                    {idea.source}
                  </span>
                </div>

                {idea.angle && (
                  <p className="text-xs text-text-secondary">{idea.angle}</p>
                )}
                {idea.rationale && (
                  <p className="text-xs bg-bg-paper p-3 rounded border border-hairline whitespace-pre-wrap">
                    {idea.rationale}
                  </p>
                )}
                {idea.error && (
                  <p className="text-xs text-error-main">{idea.error}</p>
                )}

                {actionError?.id === idea.id && (
                  <p className="text-xs text-error-main bg-red-50 rounded p-2">
                    {actionError.message}
                  </p>
                )}

                {idea.status === "proposed" && (
                  <div className="flex gap-2">
                    <button
                      disabled={busy === idea.id}
                      onClick={() =>
                        act(
                          idea.id,
                          () => approveAiVideoIdea(idea.id),
                          mutateIdeas
                        )
                      }
                      className="btn-primary text-xs py-1.5"
                    >
                      Approve
                    </button>
                    <button
                      disabled={busy === idea.id}
                      onClick={() =>
                        act(
                          idea.id,
                          () => rejectAiVideoIdea(idea.id),
                          mutateIdeas
                        )
                      }
                      className="btn-secondary text-xs py-1.5 text-error-main"
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            ))}
            {ideas.length === 0 && (
              <p className="text-sm text-text-secondary text-center py-10">
                No ideas found.
              </p>
            )}
          </div>
        </div>
      )}

      {tab === "jobs" && (
        <div className="space-y-4">
          {jobs.map((job) => (
            <div key={job.id} className="card p-4">
              <div className="flex flex-col lg:flex-row gap-5">
                <div className="w-full lg:w-44 shrink-0">
                  {job.video_url ? (
                    <ClipPlayer
                      src={job.video_url}
                      poster={job.thumbnail_url}
                      className="aspect-[9/16] max-h-64 object-contain"
                    />
                  ) : (
                    <div className="aspect-[9/16] max-h-64 bg-bg-paper-hover rounded flex items-center justify-center">
                      <Icon
                        icon="solar:videocamera-record-bold-duotone"
                        width={34}
                        className="text-text-disabled"
                      />
                    </div>
                  )}
                </div>

                <div className="min-w-0 flex-1 space-y-3">
                  <div className="flex justify-between gap-3">
                    <div>
                      <p className="text-xs text-text-secondary">
                        {job.fanpage_name} · {job.template_key}
                      </p>
                      <h3 className="text-lg font-semibold text-text-primary">
                        {job.title}
                      </h3>
                    </div>
                    <Status value={job.status} />
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                    <span>
                      <b className="block text-text-primary">
                        {job.clips_done}/{job.clips_total}
                      </b>
                      clips
                    </span>
                    <span>
                      <b className="block text-text-primary">
                        {job.credits_estimate}
                      </b>
                      credits
                    </span>
                    <span>
                      <b className="block text-text-primary">
                        {job.account?.profile ?? "—"}
                      </b>
                      account
                    </span>
                    <span>
                      <b className="block text-text-primary">
                        {job.worker_id ?? "—"}
                      </b>
                      worker
                    </span>
                  </div>

                  {job.error && (
                    <p className="text-xs text-error-main bg-red-50 rounded p-2">
                      {job.error}
                    </p>
                  )}
                  {job.qc && (
                    <p
                      className={`text-xs ${
                        job.qc.passed ? "text-primary-main" : "text-error-main"
                      }`}
                    >
                      QC:{" "}
                      {job.qc.passed
                        ? "passed"
                        : (job.qc.reasons ?? ["failed"]).join(", ")}
                    </p>
                  )}

                  {actionError?.id === job.id && (
                    <p className="text-xs text-error-main bg-red-50 rounded p-2">
                      {actionError.message}
                    </p>
                  )}

                  <div className="flex flex-wrap gap-2">
                    {["failed", "cancelled"].includes(job.status) && (
                      <button
                        disabled={busy === job.id}
                        onClick={() =>
                          act(
                            job.id,
                            () => retryAiVideoJob(job.id),
                            mutateJobs
                          )
                        }
                        className="btn-secondary text-xs"
                      >
                        Retry
                      </button>
                    )}
                    {["queued", "claimed", "generating"].includes(job.status) && (
                      <button
                        disabled={busy === job.id}
                        onClick={() =>
                          act(
                            job.id,
                            () => cancelAiVideoJob(job.id),
                            mutateJobs
                          )
                        }
                        className="btn-secondary text-xs text-error-main"
                      >
                        Cancel
                      </button>
                    )}
                    {job.publish_job_id && (
                      <Link href="/queue" className="btn-ghost text-xs">
                        Review in Queue
                      </Link>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
          {jobs.length === 0 && (
            <p className="text-sm text-text-secondary text-center py-10">
              No jobs found.
            </p>
          )}
        </div>
      )}

      {tab === "accounts" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {accounts.map((account) => (
            <AccountCard
              key={account.id}
              account={account}
              onChanged={mutateAccounts}
            />
          ))}
          {accounts.length === 0 && (
            <p className="text-sm text-text-secondary">
              No GFlow accounts reported yet.
            </p>
          )}
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
  const allowedTemplateKeys: string[] = selectedFanpage?.ai_video_templates ?? (selectedFanpage?.ai_video_template ? [selectedFanpage.ai_video_template] : []);
  const availableTemplates = allowedTemplateKeys.length > 0
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
    <form onSubmit={submit} className="card space-y-4 h-fit">
      <h2 className="text-base font-semibold text-text-primary">New idea</h2>

      {error && (
        <p className="text-xs text-error-main bg-red-50 rounded p-2">
          {error}
        </p>
      )}

      <select
        required
        className="input w-full"
        value={form.fanpage_id}
        onChange={(e) => setForm({ ...form, fanpage_id: e.target.value, template_key: "", clips: "" })}
      >
        <option value="">Fanpage</option>
        {fanpages.map((fp) => (
          <option key={fp.id} value={fp.id}>
            {fp.name}
          </option>
        ))}
      </select>

      <select
        required
        className="input w-full"
        value={form.template_key}
        onChange={(e) =>
          setForm({ ...form, template_key: e.target.value, clips: "" })
        }
      >
        <option value="">Template</option>
        {availableTemplates.map((item) => (
          <option key={item.key} value={item.key}>
            {item.name}
          </option>
        ))}
      </select>

      <input
        required
        className="input w-full"
        placeholder="Topic"
        value={form.topic}
        onChange={(e) => setForm({ ...form, topic: e.target.value })}
      />

      <input
        className="input w-full"
        placeholder="Angle (optional)"
        value={form.angle}
        onChange={(e) => setForm({ ...form, angle: e.target.value })}
      />

      <textarea
        className="input w-full"
        rows={4}
        placeholder="Research notes (the only source for specific facts)"
        value={form.research_notes}
        onChange={(e) => setForm({ ...form, research_notes: e.target.value })}
      />

      {template && (
        <select
          className="input w-full"
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
      )}

      <button disabled={saving} className="btn-primary w-full justify-center">
        {saving ? "Creating…" : "Create idea"}
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

  async function save(data: { enabled?: boolean; label?: string }) {
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
    <div className="card p-4 space-y-3">
      <div className="flex justify-between">
        <div>
          <h3 className="font-semibold text-text-primary">
            {account.label || account.profile}
          </h3>
          <p className="text-xs text-text-secondary">
            {account.email || account.profile} · {account.worker_id}
          </p>
        </div>
        <Status value={account.status} />
      </div>

      <div className="flex justify-between text-sm">
        <span className="text-text-secondary">Credits</span>
        <b className="text-text-primary">{account.credits ?? "—"}</b>
      </div>

      <p className="text-xs text-text-secondary">
        Last seen:{" "}
        {account.last_seen_at
          ? new Date(account.last_seen_at + "Z").toLocaleString()
          : "Never"}
        {account.busy_job_id ? ` · Job #${account.busy_job_id} busy` : ""}
      </p>

      {error && (
        <p className="text-xs text-error-main bg-red-50 rounded p-2">
          {error}
        </p>
      )}

      <div className="flex gap-2">
        <label className="flex items-center gap-2 text-xs text-text-primary">
          <input
            type="checkbox"
            checked={account.enabled}
            onChange={(e) => save({ enabled: e.target.checked })}
          />{" "}
          Enabled
        </label>
        <input
          className="input flex-1 text-xs"
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
    </div>
  );
}
