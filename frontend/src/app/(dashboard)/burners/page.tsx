"use client";

import { useState } from "react";
import useSWR from "swr";
import { listBurners, createBurner, deleteBurner, submitOTP, testBurnerSession, importBurnerSession, updateBurner, postStoryNow, postCommentNow, resetBurner } from "@/lib/api";
import type { Burner, BurnerStatus } from "@/lib/types";
import { Icon } from "@iconify/react";
import { clsx } from "clsx";

const STATUS_ICON: Record<BurnerStatus, string> = {
  active:       "solar:wifi-bold-duotone",
  challenged:   "solar:danger-circle-bold-duotone",
  rate_limited: "solar:danger-circle-bold-duotone",
  banned:       "solar:shield-cross-bold-duotone",
};

const STATUS_ICON_CLASS: Record<BurnerStatus, string> = {
  active:       "text-primary-main",
  challenged:   "text-warning-main",
  rate_limited: "text-error-light",
  banned:       "text-error-main",
};

const STATUS_LABEL: Record<BurnerStatus, string> = {
  active:       "Active",
  challenged:   "Challenged",
  rate_limited: "Rate Limited",
  banned:       "Banned",
};

const fetcher = () => listBurners().then((r) => r.data as Burner[]);

export default function BurnersPage() {
  const { data: burners = [], mutate } = useSWR("burners", fetcher);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ ig_username: "", password: "", proxy_url: "" });
  const [otp, setOtp]                   = useState<{ burnerId: number; code: string } | null>(null);
  const [testResult, setTestResult]     = useState<Record<number, string>>({});
  const [loadingCreate, setLoadingCreate] = useState(false);
  const [deletingId, setDeletingId]     = useState<number | null>(null);
  const [loadingOTP, setLoadingOTP]     = useState(false);
  const [testingId, setTestingId]       = useState<number | null>(null);
  const [importingId, setImportingId]   = useState<number | null>(null);
  const [importJson, setImportJson]     = useState<{ burnerId: number; text: string } | null>(null);
  const [togglingStory, setTogglingStory] = useState<number | null>(null);
  const [postingStoryId, setPostingStoryId] = useState<number | null>(null);
  const [togglingComment, setTogglingComment] = useState<number | null>(null);
  const [postingCommentId, setPostingCommentId] = useState<number | null>(null);
  const [editProxy, setEditProxy] = useState<{ burnerId: number; value: string } | null>(null);
  const [savingProxy, setSavingProxy] = useState<number | null>(null);
  const [resettingId, setResettingId] = useState<number | null>(null);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setLoadingCreate(true);
    try {
      await createBurner({ ig_username: form.ig_username, password: form.password, proxy_url: form.proxy_url || undefined });
      setForm({ ig_username: "", password: "", proxy_url: "" });
      setShowForm(false);
      mutate();
    } finally { setLoadingCreate(false); }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this burner account?")) return;
    setDeletingId(id);
    try { await deleteBurner(id); mutate(); }
    finally { setDeletingId(null); }
  }

  async function handleOTP(burnerId: number) {
    if (!otp?.code) return;
    setLoadingOTP(true);
    try { await submitOTP(burnerId, otp.code); setOtp(null); mutate(); }
    finally { setLoadingOTP(false); }
  }

  async function handleToggleStory(b: Burner) {
    setTogglingStory(b.id);
    try { await updateBurner(b.id, { story_enabled: !b.story_enabled }); mutate(); }
    finally { setTogglingStory(null); }
  }

  async function handleToggleComment(b: Burner) {
    setTogglingComment(b.id);
    try { await updateBurner(b.id, { comment_enabled: !b.comment_enabled }); mutate(); }
    finally { setTogglingComment(null); }
  }

  async function handlePostCommentNow(id: number) {
    setPostingCommentId(id);
    try {
      await postCommentNow(id);
      setTestResult((prev) => ({ ...prev, [id]: "Comment queued — check worker logs" }));
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestResult((prev) => ({ ...prev, [id]: detail || "Comment post failed" }));
    } finally { setPostingCommentId(null); }
  }

  async function handlePostStoryNow(id: number) {
    setPostingStoryId(id);
    try {
      await postStoryNow(id);
      setTestResult((prev) => ({ ...prev, [id]: "Story queued — check worker logs" }));
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestResult((prev) => ({ ...prev, [id]: detail || "Story post failed" }));
    } finally { setPostingStoryId(null); }
  }

  async function handleImportSession(burnerId: number) {
    if (!importJson?.text) return;
    setImportingId(burnerId);
    try {
      const parsed = JSON.parse(importJson.text);
      const res = await importBurnerSession(burnerId, parsed);
      setTestResult((prev) => ({ ...prev, [burnerId]: `Imported OK: @${res.data.ig_username}` }));
      setImportJson(null);
      mutate();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestResult((prev) => ({ ...prev, [burnerId]: detail || "Import failed — invalid session JSON" }));
    } finally { setImportingId(null); }
  }

  function maskProxy(url: string): string {
    try {
      const u = new URL(url);
      if (u.password) u.password = "***";
      if (u.username) u.username = u.username.slice(0, 3) + "***";
      return u.toString();
    } catch {
      return url;
    }
  }

  async function handleReset(id: number) {
    setResettingId(id);
    try {
      await resetBurner(id);
      setTestResult((prev) => ({ ...prev, [id]: "Status reset — burner is active again" }));
      mutate();
    } catch {
      setTestResult((prev) => ({ ...prev, [id]: "Reset failed" }));
    } finally { setResettingId(null); }
  }

  async function handleSaveProxy(burnerId: number) {
    setSavingProxy(burnerId);
    try {
      await updateBurner(burnerId, { proxy_url: editProxy?.value || undefined });
      setEditProxy(null);
      mutate();
    } finally { setSavingProxy(null); }
  }

  async function handleTest(id: number) {
    setTestingId(id);
    setTestResult((prev) => ({ ...prev, [id]: "Testing…" }));
    try {
      const res = await testBurnerSession(id);
      setTestResult((prev) => ({ ...prev, [id]: `OK: @${res.data.ig_username}` }));
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setTestResult((prev) => ({ ...prev, [id]: detail || "Session invalid" }));
      mutate(); // refresh so challenged status + OTP button appear
    } finally { setTestingId(null); }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Burner Accounts</h1>
          <p className="text-sm text-text-secondary mt-1">
            {burners.length}/5 burner accounts — 1 sticky proxy IP per burner required
          </p>
        </div>
        {burners.length < 5 && (
          <button onClick={() => setShowForm(!showForm)} className="btn-primary">
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            Add Burner
          </button>
        )}
      </div>

      {/* Add form */}
      {showForm && (
        <form onSubmit={handleCreate} className="card space-y-4 max-w-md">
          <h3 className="text-base font-semibold text-text-primary">New Burner Account</h3>
          <div>
            <label className="label">Instagram Username</label>
            <input
              className="input-rect"
              value={form.ig_username}
              onChange={(e) => setForm({ ...form, ig_username: e.target.value })}
              placeholder="ig_username (no @)"
              required
            />
          </div>
          <div>
            <label className="label">Password</label>
            <input
              className="input-rect"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="••••••••"
              required
            />
          </div>
          <div>
            <label className="label">Proxy URL (required for production)</label>
            <input
              className="input-rect"
              value={form.proxy_url}
              onChange={(e) => setForm({ ...form, proxy_url: e.target.value })}
              placeholder="http://user:pass@host:port"
            />
          </div>
          <div className="flex gap-3">
            <button type="submit" disabled={loadingCreate} className="btn-primary">
              <Icon icon="solar:refresh-bold-duotone" width={14} className={loadingCreate ? "animate-spin" : "hidden"} />
              {loadingCreate ? "Saving…" : "Save Burner"}
            </button>
            <button type="button" onClick={() => setShowForm(false)} className="btn-secondary">Cancel</button>
          </div>
        </form>
      )}

      {/* Burner cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {burners.map((b) => (
          <div key={b.id} className="card space-y-3">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <Icon
                    icon={STATUS_ICON[b.status]}
                    width={16}
                    className={STATUS_ICON_CLASS[b.status]}
                  />
                  <span className="text-sm font-semibold text-text-primary">@{b.ig_username}</span>
                  <span
                    className={clsx("badge", {
                      "badge-green":  b.status === "active",
                      "badge-yellow": b.status === "challenged" || b.status === "rate_limited",
                      "badge-red":    b.status === "banned",
                    })}
                  >
                    {STATUS_LABEL[b.status]}
                  </span>
                </div>
                <p className="text-xs text-text-secondary mt-0.5">
                  {b.requests_today} requests today
                </p>
                <div className="flex items-center gap-1 mt-1">
                  <Icon icon="solar:server-square-bold-duotone" width={12} className="text-text-secondary flex-shrink-0" />
                  {b.proxy_url ? (
                    <span className="text-[11px] text-primary-main font-mono truncate max-w-[180px]" title={maskProxy(b.proxy_url)}>
                      {maskProxy(b.proxy_url)}
                    </span>
                  ) : (
                    <span className="text-[11px] text-text-disabled">No proxy</span>
                  )}
                  <button
                    onClick={() => setEditProxy(editProxy?.burnerId === b.id ? null : { burnerId: b.id, value: b.proxy_url || "" })}
                    className="ml-1 text-text-secondary hover:text-primary-main transition-colors"
                    title="Edit proxy"
                  >
                    <Icon icon="solar:pen-2-bold-duotone" width={12} />
                  </button>
                </div>
              </div>
              <button
                onClick={() => handleDelete(b.id)}
                disabled={deletingId === b.id}
                className="text-text-secondary hover:text-error-main transition-colors p-1 disabled:opacity-50"
              >
                <Icon icon={deletingId === b.id ? "solar:refresh-bold-duotone" : "solar:trash-bin-trash-bold-duotone"} width={16} className={deletingId === b.id ? "animate-spin" : ""} />
              </button>
            </div>

            {b.last_error && (
              <p className="text-xs text-error-main bg-[rgba(255,86,48,0.08)] px-3 py-2 rounded-md">
                {b.last_error}
              </p>
            )}

            {editProxy?.burnerId === b.id && (
              <div className="space-y-2 px-3 py-2 rounded bg-bg-default">
                <p className="text-xs font-semibold text-text-primary">Edit Proxy URL</p>
                <input
                  className="input-rect w-full font-mono text-xs"
                  placeholder="http://user:pass@host:port"
                  value={editProxy.value}
                  onChange={(e) => setEditProxy({ ...editProxy, value: e.target.value })}
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => handleSaveProxy(b.id)}
                    disabled={savingProxy === b.id}
                    className="btn-primary text-xs"
                  >
                    <Icon icon="solar:refresh-bold-duotone" width={12} className={savingProxy === b.id ? "animate-spin" : "hidden"} />
                    {savingProxy === b.id ? "Saving…" : "Save"}
                  </button>
                  <button onClick={() => setEditProxy(null)} className="btn-secondary text-xs">Cancel</button>
                </div>
              </div>
            )}

            {/* ── Story warmer ── */}
            <div className="flex items-center justify-between px-3 py-2 rounded bg-bg-default">
              <div className="flex items-center gap-2">
                <Icon icon="solar:camera-add-bold-duotone" width={15} className="text-text-secondary" />
                <div>
                  <p className="text-xs font-semibold text-text-primary">Auto Story</p>
                  <p className="text-[11px] text-text-secondary">
                    {b.last_story_at
                      ? `Last: ${Math.round((Date.now() - new Date(b.last_story_at).getTime()) / 86400000)}d ago`
                      : "Never posted"}
                    {" · "}every 2–3 days
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handlePostStoryNow(b.id)}
                  disabled={postingStoryId === b.id || b.status !== "active"}
                  className="btn-ghost text-[11px] py-1 px-2 disabled:opacity-40"
                  title="Post a story right now (test)"
                >
                  <Icon icon="solar:refresh-bold-duotone" width={11} className={postingStoryId === b.id ? "animate-spin" : "hidden"} />
                  {postingStoryId === b.id ? "Posting…" : "Post Now"}
                </button>
                <button
                  onClick={() => handleToggleStory(b)}
                  disabled={togglingStory === b.id}
                  className="flex-shrink-0"
                  title={b.story_enabled ? "Disable auto story" : "Enable auto story"}
                >
                  {togglingStory === b.id ? (
                    <Icon icon="solar:refresh-bold-duotone" width={20} className="animate-spin text-text-disabled" />
                  ) : (
                    <Icon
                      icon={b.story_enabled ? "solar:toggle-on-bold-duotone" : "solar:toggle-off-bold-duotone"}
                      width={24}
                      className={b.story_enabled ? "text-primary-main" : "text-text-disabled"}
                    />
                  )}
                </button>
              </div>
            </div>

            {/* ── Comment warmer ── */}
            <div className="flex items-center justify-between px-3 py-2 rounded bg-bg-default">
              <div className="flex items-center gap-2">
                <Icon icon="solar:chat-dots-bold-duotone" width={15} className="text-text-secondary" />
                <div>
                  <p className="text-xs font-semibold text-text-primary">Auto Comment</p>
                  <p className="text-[11px] text-text-secondary">
                    {b.last_comment_at
                      ? `Last: ${Math.round((Date.now() - new Date(b.last_comment_at).getTime()) / 86400000)}d ago`
                      : "Never commented"}
                    {" · "}every 2–3 days
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handlePostCommentNow(b.id)}
                  disabled={postingCommentId === b.id || b.status !== "active"}
                  className="btn-ghost text-[11px] py-1 px-2 disabled:opacity-40"
                  title="Post a comment right now (test)"
                >
                  <Icon icon="solar:refresh-bold-duotone" width={11} className={postingCommentId === b.id ? "animate-spin" : "hidden"} />
                  {postingCommentId === b.id ? "Posting…" : "Post Now"}
                </button>
                <button
                  onClick={() => handleToggleComment(b)}
                  disabled={togglingComment === b.id}
                  className="flex-shrink-0"
                  title={b.comment_enabled ? "Disable auto comment" : "Enable auto comment"}
                >
                  {togglingComment === b.id ? (
                    <Icon icon="solar:refresh-bold-duotone" width={20} className="animate-spin text-text-disabled" />
                  ) : (
                    <Icon
                      icon={b.comment_enabled ? "solar:toggle-on-bold-duotone" : "solar:toggle-off-bold-duotone"}
                      width={24}
                      className={b.comment_enabled ? "text-primary-main" : "text-text-disabled"}
                    />
                  )}
                </button>
              </div>
            </div>

            {testResult[b.id] && (
              <p className={clsx("text-xs", testResult[b.id].startsWith("OK") || testResult[b.id].includes("queued") || testResult[b.id].includes("Imported") ? "text-primary-main" : "text-error-main")}>
                {testResult[b.id]}
              </p>
            )}

            <div className="flex gap-2 pt-1 flex-wrap">
              <button onClick={() => handleTest(b.id)} disabled={testingId === b.id} className="btn-ghost text-xs">
                <Icon icon="solar:refresh-bold-duotone" width={12} className={testingId === b.id ? "animate-spin" : "hidden"} />
                {testingId === b.id ? "Testing…" : "Test Session"}
              </button>
              <button
                onClick={() => setImportJson(importJson?.burnerId === b.id ? null : { burnerId: b.id, text: "" })}
                className="btn-ghost text-xs"
              >
                <Icon icon="solar:import-bold-duotone" width={12} />
                Import Session
              </button>
              {b.status === "challenged" && (
                <button
                  onClick={() => setOtp({ burnerId: b.id, code: "" })}
                  className="btn-ghost text-xs text-warning-main"
                >
                  Submit OTP
                </button>
              )}
              {b.status === "rate_limited" && (
                <button
                  onClick={() => handleReset(b.id)}
                  disabled={resettingId === b.id}
                  className="btn-ghost text-xs text-warning-main"
                >
                  <Icon icon="solar:refresh-bold-duotone" width={12} className={resettingId === b.id ? "animate-spin" : "hidden"} />
                  {resettingId === b.id ? "Resetting…" : "Reset Status"}
                </button>
              )}
            </div>

            {importJson?.burnerId === b.id && (
              <div className="space-y-2 pt-1">
                <p className="text-xs text-text-secondary">Paste the session JSON from <code>ig_login_export.py</code></p>
                <textarea
                  className="input-rect w-full font-mono text-xs"
                  rows={4}
                  placeholder='{"uuids": {...}, "cookies": {...}, ...}'
                  value={importJson.text}
                  onChange={(e) => setImportJson({ ...importJson, text: e.target.value })}
                />
                <div className="flex gap-2">
                  <button onClick={() => handleImportSession(b.id)} disabled={importingId === b.id || !importJson.text} className="btn-primary text-xs">
                    <Icon icon="solar:refresh-bold-duotone" width={12} className={importingId === b.id ? "animate-spin" : "hidden"} />
                    {importingId === b.id ? "Importing…" : "Import"}
                  </button>
                  <button onClick={() => setImportJson(null)} className="btn-secondary text-xs">Cancel</button>
                </div>
              </div>
            )}

            {otp?.burnerId === b.id && (
              <div className="flex gap-2">
                <input
                  className="input-rect flex-1"
                  placeholder="6-digit OTP code"
                  value={otp.code}
                  onChange={(e) => setOtp({ ...otp, code: e.target.value })}
                />
                <button onClick={() => handleOTP(b.id)} disabled={loadingOTP} className="btn-primary text-xs">
                  <Icon icon="solar:refresh-bold-duotone" width={12} className={loadingOTP ? "animate-spin" : "hidden"} />
                  {loadingOTP ? "Submitting…" : "Submit"}
                </button>
                <button onClick={() => setOtp(null)} className="btn-secondary text-xs">Cancel</button>
              </div>
            )}
          </div>
        ))}
      </div>

      {burners.length === 0 && (
        <div className="card text-center py-12">
          <p className="text-sm text-text-secondary mb-2">No burner accounts yet.</p>
          <p className="text-xs text-text-secondary">
            Add up to 5 Instagram accounts. Age each account 2-4 weeks before using.
          </p>
        </div>
      )}
    </div>
  );
}
