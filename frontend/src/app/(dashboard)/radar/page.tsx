"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Icon } from "@iconify/react";
import {
  listRadarAccounts,
  createRadarAccount,
  updateRadarAccount,
  deleteRadarAccount,
  listRadarNiches,
  listRadarStories,
  getShadowReport,
  previewRadarDecision,
  regenerateRadarDecision,
} from "@/lib/api";
import type { RadarAccount, RadarStory, ShadowReport } from "@/lib/types";
import { useFanpages } from "@/lib/useFanpages";
import { formatDistanceToNowStrict } from "date-fns";

function formatAge(dateStr: string) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

const statusToneMap: Record<string, { bg: string; fg: string }> = {
  triggered: { bg: "var(--bad-soft)", fg: "var(--bad)" },
  watching: { bg: "var(--info-soft)", fg: "var(--info)" },
  stale: { bg: "var(--surface2)", fg: "var(--text2)" },
  skipped: { bg: "var(--surface2)", fg: "var(--text2)" },
};

export default function RadarPage() {
  const [activeTab, setActiveTab] = useState<"stories" | "accounts" | "shadow">("stories");

  // Niche list
  const { data: niches } = useSWR("radar-niches", () =>
    listRadarNiches().then((r) => r.data as string[])
  );

  // ── Stories Tab State ──
  const [storyNicheFilter, setStoryNicheFilter] = useState<string>("");
  const [storyStatusFilter, setStoryStatusFilter] = useState<string>("");
  const { data: stories, mutate: mutateStories } = useSWR(
    activeTab === "stories" ? ["radar-stories", storyNicheFilter, storyStatusFilter] : null,
    () =>
      listRadarStories({ niche: storyNicheFilter, status: storyStatusFilter }).then(
        (r) => r.data as RadarStory[]
      ),
    {
      refreshInterval: (data) => {
        if (
          data &&
          data.some((s) =>
            s.decisions.some(
              (d) => d.preview_status === "queued" || d.preview_status === "running"
            )
          )
        ) {
          return 5000;
        }
        return 60000;
      },
    }
  );

  const [redesignLoading, setRedesignLoading] = useState<Set<number>>(new Set());
  const handleRedesignPreview = async (decisionId: number, isRegenerate: boolean) => {
    setRedesignLoading((prev) => new Set(prev).add(decisionId));
    try {
      if (isRegenerate) {
        await regenerateRadarDecision(decisionId);
      } else {
        await previewRadarDecision(decisionId);
      }
      await mutateStories();
    } catch (err: any) {
      alert("Redesign failed: " + (err.response?.data?.detail || err.message));
    } finally {
      setRedesignLoading((prev) => {
        const next = new Set(prev);
        next.delete(decisionId);
        return next;
      });
    }
  };

  // ── Accounts Tab State ──
  const [accNicheFilter, setAccNicheFilter] = useState<string>("");
  const { data: accountsRaw, mutate: mutateAccounts } = useSWR(
    activeTab === "accounts" ? ["radar-accounts", accNicheFilter] : null,
    () => listRadarAccounts(accNicheFilter).then((r) => r.data as RadarAccount[])
  );
  const [newAccNiche, setNewAccNiche] = useState("");
  const [newAccUsername, setNewAccUsername] = useState("");
  const [creatingAcc, setCreatingAcc] = useState(false);

  // ── Shadow Report Tab State ──
  const [shadowDays, setShadowDays] = useState<number>(7);
  const { data: shadowReport, isValidating: loadingShadow } = useSWR(
    activeTab === "shadow" ? ["radar-shadow", shadowDays] : null,
    () => getShadowReport(shadowDays).then((r) => r.data as ShadowReport)
  );

  const { data: fanpages = [] } = useFanpages();

  async function handleCreateAccount(e: React.FormEvent) {
    e.preventDefault();
    let cleanedUsername = newAccUsername.trim();
    if (cleanedUsername.startsWith("@")) cleanedUsername = cleanedUsername.substring(1);

    if (!/^[A-Za-z0-9._]{1,30}$/.test(cleanedUsername)) {
      alert("Invalid Instagram username format.");
      return;
    }

    setCreatingAcc(true);
    try {
      await createRadarAccount({ niche: newAccNiche, ig_username: cleanedUsername });
      setNewAccUsername("");
      await mutateAccounts();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Error creating account");
    } finally {
      setCreatingAcc(false);
    }
  }

  async function handleToggleAccountActive(id: number, current: boolean) {
    await updateRadarAccount(id, { is_active: !current });
    mutateAccounts();
  }

  async function handleDeleteAccount(id: number, username: string) {
    if (confirm(`Remove @${username} from Radar?`)) {
      await deleteRadarAccount(id);
      mutateAccounts();
    }
  }

  return (
    <div className="space-y-5 max-w-[1240px] w-full">
      {/* ── Page Header ── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-[26px] font-bold text-text tracking-tight">Viral radar</h1>
          <p className="text-sm text-text2">
            Spots posts from niche leaders that start to take off, then recreates them for the brands that follow that niche.
          </p>
        </div>

        {/* Global / active tab niche filter */}
        {activeTab === "stories" && (
          <div className="flex items-center gap-2">
            <select
              aria-label="Status filter"
              className="h-10 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-1 focus:ring-pri"
              value={storyStatusFilter}
              onChange={(e) => setStoryStatusFilter(e.target.value)}
            >
              <option value="">All statuses</option>
              <option value="watching">Watching</option>
              <option value="triggered">Triggered</option>
              <option value="stale">Stale</option>
              <option value="skipped">Skipped</option>
            </select>
            <select
              aria-label="Niche filter"
              className="h-10 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-1 focus:ring-pri"
              value={storyNicheFilter}
              onChange={(e) => setStoryNicheFilter(e.target.value)}
            >
              <option value="">All niches</option>
              {(niches || []).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        )}

        {activeTab === "accounts" && (
          <select
            aria-label="Filter accounts by niche"
            className="h-10 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-1 focus:ring-pri"
            value={accNicheFilter}
            onChange={(e) => setAccNicheFilter(e.target.value)}
          >
            <option value="">All niches</option>
            {(niches || []).map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* ── Tabs Bar ── */}
      <div role="tablist" aria-label="View" className="self-start flex gap-1 p-1 rounded-xl bg-surface2 w-fit">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "stories"}
          onClick={() => setActiveTab("stories")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            activeTab === "stories" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>Rising now</span>
          <span className="text-xs text-text2">{stories ? stories.length : ""}</span>
        </button>

        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "accounts"}
          onClick={() => setActiveTab("accounts")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            activeTab === "accounts" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>Tracked accounts</span>
          <span className="text-xs text-text2">{accountsRaw ? accountsRaw.length : ""}</span>
        </button>

        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "shadow"}
          onClick={() => setActiveTab("shadow")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            activeTab === "shadow" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>Trigger report</span>
        </button>
      </div>

      {/* ── TAB 1: Rising Stories ── */}
      {activeTab === "stories" && (
        <div className="space-y-4">
          {/* Note Banner */}
          <div
            role="note"
            className="p-3.5 rounded-xl bg-info-soft text-text text-[13px] leading-relaxed flex flex-col gap-1.5 border border-info/20"
          >
            <span>
              <strong className="font-semibold">What Radar makes:</strong> an Instagram recreate card. A brand needs its Instagram format set to Recreate (template or ChatGPT edit). The card is reviewed by that brand&apos;s Instagram setting, and Radar makes at most the brand&apos;s Radar max per day (a maximum, not a target). Shadow mode is on by default: decisions are logged, never posted.
            </span>
            <span className="text-text2">
              Rules: Fast · Confirmed · Burst · No rule yet · Stale. Results: Logged (shadow) · Queued · Dispatched · Card created · Skipped (duplicate, stale, no distinct photo, similar caption, daily max) · Waiting for the image engine · Held (quote mismatch) · Failed.
            </span>
          </div>

          <div className="space-y-3">
            {(stories || []).map((s) => {
              const tone = statusToneMap[s.status.toLowerCase()] || {
                bg: "var(--surface2)",
                fg: "var(--text2)",
              };
              const heatPct = Math.min(100, Math.round(s.heat_score));
              const heatColor =
                s.heat_score >= 75 ? "var(--bad)" : s.heat_score >= 50 ? "var(--warn)" : "var(--info)";

              return (
                <article
                  key={s.id}
                  className="bg-surface border border-line rounded-xl p-4 flex flex-wrap gap-4 items-start shadow-sm"
                >
                  {/* Thumbnail */}
                  <div className="w-24 h-28 shrink-0 rounded-lg overflow-hidden bg-surface2 border border-line flex items-center justify-center">
                    {s.members[0]?.thumbnail_url ? (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <img src={s.members[0].thumbnail_url} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <Icon icon="solar:camera-bold-duotone" width={28} className="text-text2 opacity-40" />
                    )}
                  </div>

                  {/* Story Details */}
                  <div className="flex-[1_1_280px] min-w-0 flex flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className="px-2.5 py-0.5 rounded-full text-xs font-semibold"
                        style={{ background: tone.bg, color: tone.fg }}
                      >
                        {s.status}
                      </span>
                      <span className="text-xs text-text2">
                        {s.niche} · {s.shelf_kind || "news"} · expires{" "}
                        {s.expires_at ? formatAge(s.expires_at) : "—"}
                      </span>
                    </div>

                    <h2 className="text-[15px] font-semibold text-text line-clamp-2">
                      {s.niche ? `${s.niche} story (${s.member_count} post${s.member_count === 1 ? "" : "s"})` : `Story #${s.id}`}
                    </h2>

                    {/* Heat Score Bar */}
                    <div className="flex items-center gap-2.5 pt-0.5">
                      <span className="text-xs text-text2 min-w-[54px] flex items-center gap-1">
                        <Icon icon="solar:fire-bold-duotone" width={14} className="text-warn" />
                        <span>Heat {s.heat_score.toFixed(1)}</span>
                      </span>
                      <div className="flex-1 max-w-[220px] h-2 rounded-full bg-surface2 overflow-hidden border border-line">
                        <div
                          className="h-full rounded-full transition-all"
                          style={{ width: `${heatPct}%`, background: heatColor }}
                        />
                      </div>
                    </div>

                    {/* Member details */}
                    <div className="text-xs text-text2">
                      <span>
                        {s.member_count} account{s.member_count === 1 ? "" : "s"} ({s.distinct_accounts} distinct)
                      </span>
                      {s.final_max_likes_24h != null && <span> · max {s.final_max_likes_24h} likes</span>}
                    </div>

                    {/* Member handles preview */}
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {s.members.map((m) => (
                        <a
                          key={m.shortcode}
                          href={m.post_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[11px] px-2 py-0.5 rounded bg-surface2 hover:bg-line text-text2 hover:text-text transition-colors"
                        >
                          @{m.ig_username} ({m.latest_like_count ?? 0} likes)
                        </a>
                      ))}
                    </div>
                  </div>

                  {/* Fanpage Decisions */}
                  <div className="flex-[1_1_320px] min-w-0 flex flex-col gap-2 border-t md:border-t-0 md:border-l border-line md:pl-4 pt-3 md:pt-0">
                    <span className="text-xs font-semibold text-text">Brand decisions</span>
                    {s.decisions.length === 0 ? (
                      <p className="text-xs text-text2 italic">No decisions recorded yet.</p>
                    ) : (
                      <div className="space-y-2">
                        {s.decisions.map((d) => (
                          <div
                            key={d.id}
                            className="p-2.5 rounded-lg bg-surface2 border border-line flex flex-col gap-1.5 text-xs"
                          >
                            <div className="flex items-center justify-between gap-2 flex-wrap">
                              <span className="font-semibold text-text">{d.fanpage_name}</span>
                              <div className="flex items-center gap-1.5">
                                {d.shadow && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold border border-dashed border-text2 text-text2">
                                    Shadow
                                  </span>
                                )}
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-surface border border-line text-text uppercase">
                                  {d.rule}
                                </span>
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-surface border border-line text-pri-text uppercase">
                                  {d.status}
                                </span>
                              </div>
                            </div>

                            {d.reason && (
                              <p className="text-[11px] text-text2 leading-snug">{d.reason}</p>
                            )}

                            {d.preview_error && (
                              <p className="text-[11px] text-bad bg-bad-soft p-1 rounded border border-bad/20">
                                {d.preview_error}
                              </p>
                            )}

                            <div className="flex items-center justify-between gap-2 pt-1 border-t border-line/60">
                              <div>
                                {d.preview_image_path ? (
                                  <a href={d.preview_image_path} target="_blank" rel="noreferrer">
                                    {/* eslint-disable-next-line @next/next/no-img-element */}
                                    <img
                                      src={d.preview_image_path}
                                      alt="Preview"
                                      className="w-10 h-14 object-cover rounded border border-line"
                                    />
                                  </a>
                                ) : null}
                              </div>

                              <div className="flex items-center gap-2">
                                {d.publish_job_id ? (
                                  <Link
                                    href="/queue"
                                    className="h-7 px-2.5 rounded-md border border-line bg-surface text-text hover:bg-surface2 text-[11px] font-medium inline-flex items-center gap-1"
                                  >
                                    <span>Open in queue</span>
                                  </Link>
                                ) : null}

                                <button
                                  type="button"
                                  onClick={() => handleRedesignPreview(d.id, !d.shadow)}
                                  disabled={
                                    redesignLoading.has(d.id) ||
                                    d.preview_status === "queued" ||
                                    d.preview_status === "running"
                                  }
                                  className="h-7 px-2.5 rounded-md border border-line bg-surface text-text hover:bg-surface2 text-[11px] font-medium disabled:opacity-50"
                                >
                                  {redesignLoading.has(d.id) ||
                                  d.preview_status === "queued" ||
                                  d.preview_status === "running"
                                    ? "Generating…"
                                    : d.shadow
                                    ? "Preview"
                                    : "Regenerate"}
                                </button>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </article>
              );
            })}

            {stories?.length === 0 && (
              <div className="p-12 text-center text-sm text-text2 bg-surface border border-line rounded-xl">
                No rising stories found for current filters.
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB 2: Tracked Accounts ── */}
      {activeTab === "accounts" && (
        <section className="bg-surface border border-line rounded-xl overflow-hidden shadow-sm">
          {/* Add account form */}
          <form
            onSubmit={handleCreateAccount}
            className="flex flex-wrap gap-2.5 p-3.5 sm:p-4 border-b border-line bg-surface2/60 items-end"
          >
            <div className="flex-[2_1_200px]">
              <label className="block text-xs font-semibold text-text mb-1">Instagram username</label>
              <input
                type="text"
                placeholder="@username"
                value={newAccUsername}
                onChange={(e) => setNewAccUsername(e.target.value)}
                className="w-full h-9 px-3 rounded-lg border border-line bg-surface text-text text-sm focus:outline-none focus:ring-1 focus:ring-pri"
                required
              />
            </div>
            <div className="flex-[1_1_160px]">
              <label className="block text-xs font-semibold text-text mb-1">Niche</label>
              <input
                type="text"
                placeholder="e.g. MotoGP"
                value={newAccNiche}
                onChange={(e) => setNewAccNiche(e.target.value)}
                className="w-full h-9 px-3 rounded-lg border border-line bg-surface text-text text-sm focus:outline-none focus:ring-1 focus:ring-pri"
                required
              />
            </div>
            <button
              type="submit"
              disabled={creatingAcc}
              className="h-9 px-4 rounded-lg bg-pri text-pri-fg text-xs font-semibold hover:opacity-95 transition-opacity disabled:opacity-50"
            >
              {creatingAcc ? "Adding…" : "Track account"}
            </button>
          </form>

          {/* Accounts Table */}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] border-collapse text-sm">
              <thead>
                <tr className="text-left text-xs text-text2 border-b border-line bg-surface2/30">
                  <th scope="col" className="py-2.5 px-4 font-semibold">Account</th>
                  <th scope="col" className="py-2.5 px-3 font-semibold">Niche</th>
                  <th scope="col" className="py-2.5 px-3 font-semibold">Leader score</th>
                  <th scope="col" className="py-2.5 px-3 font-semibold">Checked</th>
                  <th scope="col" className="py-2.5 px-3 font-semibold">Tracking</th>
                  <th scope="col" className="py-2.5 px-4 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {(accountsRaw || []).map((acc) => (
                  <tr key={acc.id} className="hover:bg-surface2/40 transition-colors">
                    <td className="py-3 px-4 font-semibold text-text">
                      <a
                        href={`https://instagram.com/${acc.ig_username}`}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:text-pri-text"
                      >
                        @{acc.ig_username}
                      </a>
                    </td>
                    <td className="py-3 px-3 text-xs text-text2">{acc.niche}</td>
                    <td className="py-3 px-3 tabular-nums font-medium">{acc.leader_score.toFixed(2)}</td>
                    <td className="py-3 px-3 text-xs text-text2 whitespace-nowrap">
                      {acc.last_checked_at
                        ? formatDistanceToNowStrict(new Date(acc.last_checked_at), { addSuffix: true })
                        : "—"}
                    </td>
                    <td className="py-3 px-3">
                      <label className="flex items-center gap-2 cursor-pointer text-xs select-none">
                        <input
                          type="checkbox"
                          checked={acc.is_active}
                          onChange={() => handleToggleAccountActive(acc.id, acc.is_active)}
                          className="w-4 h-4 rounded text-pri focus:ring-pri"
                        />
                        <span className={acc.is_active ? "text-pri-text font-semibold" : "text-text2"}>
                          {acc.is_active ? "On" : "Off"}
                        </span>
                      </label>
                    </td>
                    <td className="py-3 px-4 text-right whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => handleDeleteAccount(acc.id, acc.ig_username)}
                        title="Remove account"
                        className="p-1.5 rounded-lg text-text2 hover:text-bad hover:bg-bad-soft transition-colors"
                      >
                        <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                      </button>
                    </td>
                  </tr>
                ))}
                {accountsRaw?.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-10 text-center text-text2 italic">
                      No tracked accounts found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── TAB 3: Shadow / Trigger Report ── */}
      {activeTab === "shadow" && (
        <section className="bg-surface border border-line rounded-xl p-5 space-y-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-[15px] font-semibold text-text">
                How good were the triggers? (last {shadowDays} days)
              </h2>
              <span className="text-xs text-text2">Shadow decisions are logged but never posted.</span>
            </div>

            {/* Days pills */}
            <div className="flex gap-1.5">
              {[1, 3, 7, 14, 30].map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setShadowDays(d)}
                  className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                    shadowDays === d
                      ? "bg-pri text-pri-fg border-pri font-semibold"
                      : "bg-surface text-text2 border-line hover:text-text hover:bg-surface2"
                  }`}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>

          {loadingShadow && (
            <div className="text-center py-8 text-text2 text-sm">
              <Icon icon="svg-spinners:ring-resize" width={22} className="mx-auto mb-2" />
              Loading report…
            </div>
          )}

          {shadowReport && !loadingShadow && (
            <div className="space-y-5">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[700px] border-collapse text-sm">
                  <thead>
                    <tr className="text-left text-xs text-text2 border-b border-line bg-surface2/40">
                      <th scope="col" className="py-2.5 px-3 font-semibold">Brand</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Fast</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Confirmed</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Burst</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Median trigger</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">p90 trigger</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Precision</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Missed</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    <tr className="bg-surface2/30 font-semibold text-text">
                      <td className="py-2.5 px-3">All</td>
                      <td className="py-2.5 px-3">{shadowReport.overall.decisions_by_rule?.fast ?? 0}</td>
                      <td className="py-2.5 px-3">{shadowReport.overall.decisions_by_rule?.confirmed ?? 0}</td>
                      <td className="py-2.5 px-3">{shadowReport.overall.decisions_by_rule?.burst ?? 0}</td>
                      <td className="py-2.5 px-3">{shadowReport.overall.median_minutes.toFixed(1)} min</td>
                      <td className="py-2.5 px-3">{shadowReport.overall.p90_minutes.toFixed(1)} min</td>
                      <td className="py-2.5 px-3 text-pri-text font-bold">
                        {(shadowReport.overall.precision * 100).toFixed(0)} %
                      </td>
                      <td className="py-2.5 px-3 text-warn">{shadowReport.overall.missed_stories}</td>
                    </tr>
                    {Object.entries(shadowReport.fanpages || {}).map(([fpId, s]) => {
                      const fpName =
                        fanpages?.find((f) => f.id.toString() === fpId)?.name || `Brand #${fpId}`;
                      return (
                        <tr key={fpId} className="hover:bg-surface2/40 transition-colors">
                          <td className="py-2.5 px-3">{fpName}</td>
                          <td className="py-2.5 px-3">{s.decisions_by_rule?.fast ?? 0}</td>
                          <td className="py-2.5 px-3">{s.decisions_by_rule?.confirmed ?? 0}</td>
                          <td className="py-2.5 px-3">{s.decisions_by_rule?.burst ?? 0}</td>
                          <td className="py-2.5 px-3">{s.median_minutes.toFixed(1)} min</td>
                          <td className="py-2.5 px-3">{s.p90_minutes.toFixed(1)} min</td>
                          <td className="py-2.5 px-3 font-semibold text-pri-text">
                            {(s.precision * 100).toFixed(0)} %
                          </td>
                          <td className="py-2.5 px-3 text-warn">{s.missed_stories}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Recent decisions table */}
              <div className="space-y-2 pt-2 border-t border-line">
                <h3 className="text-xs font-semibold text-text">Recent decisions</h3>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[600px] border-collapse text-xs">
                    <thead>
                      <tr className="text-left text-text2 border-b border-line bg-surface2/30">
                        <th scope="col" className="py-2 px-3 font-semibold">Story ID</th>
                        <th scope="col" className="py-2 px-3 font-semibold">Rule</th>
                        <th scope="col" className="py-2 px-3 font-semibold">Source</th>
                        <th scope="col" className="py-2 px-3 font-semibold">Final likes</th>
                        <th scope="col" className="py-2 px-3 font-semibold">Time to trigger</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {(shadowReport.overall?.recent_decisions || []).map((d, i) => (
                        <tr key={i} className="hover:bg-surface2/30">
                          <td className="py-2 px-3">{d.story_id}</td>
                          <td className="py-2 px-3 uppercase">{d.rule}</td>
                          <td className="py-2 px-3">
                            <a
                              href={`https://instagram.com/p/${d.shortcode}`}
                              target="_blank"
                              rel="noreferrer"
                              className="text-pri-text hover:underline"
                            >
                              @{d.username}
                            </a>
                          </td>
                          <td className="py-2 px-3 tabular-nums">{d.final_likes ?? "-"}</td>
                          <td className="py-2 px-3">{d.minutes > 0 ? `${d.minutes.toFixed(1)} min` : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
