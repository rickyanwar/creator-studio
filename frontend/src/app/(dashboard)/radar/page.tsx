"use client";

import { useState } from "react";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import {
  listRadarAccounts,
  createRadarAccount,
  updateRadarAccount,
  deleteRadarAccount,
  listRadarNiches,
  listRadarStories,
  getShadowReport,
  listFanpages,
  previewRadarDecision,
  regenerateRadarDecision,
} from "@/lib/api";
import type { RadarAccount, RadarStory, ShadowReport, Fanpage, ShadowReportSection } from "@/lib/types";

function formatAge(dateStr: string) {
  const diff = Date.now() - new Date(dateStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export default function RadarPage() {
  const [activeTab, setActiveTab] = useState<"accounts" | "stories" | "shadow">("accounts");
  
  // ── Accounts Tab State ──
  const [accNicheFilter, setAccNicheFilter] = useState<string>("");
  const { data: accountsRaw, mutate: mutateAccounts } = useSWR(
    activeTab === "accounts" ? ["radar-accounts", accNicheFilter] : null,
    () => listRadarAccounts(accNicheFilter).then((r) => r.data as RadarAccount[])
  );
  const [newAccNiche, setNewAccNiche] = useState("");
  const [newAccUsername, setNewAccUsername] = useState("");
  const [creatingAcc, setCreatingAcc] = useState(false);

  // ── Stories Tab State ──
  const [storyNicheFilter, setStoryNicheFilter] = useState<string>("");
  const [storyStatusFilter, setStoryStatusFilter] = useState<string>("");
  const { data: stories, mutate: mutateStories } = useSWR(
    activeTab === "stories" ? ["radar-stories", storyNicheFilter, storyStatusFilter] : null,
    () => listRadarStories({ niche: storyNicheFilter, status: storyStatusFilter }).then((r) => r.data as RadarStory[]),
    { 
      refreshInterval: (data) => {
        // Poll every 5s if any decision is queued or running, else every 60s
        if (data && data.some(s => s.decisions.some(d => d.preview_status === 'queued' || d.preview_status === 'running'))) {
          return 5000;
        }
        return 60000;
      }
    }
  );

  const [redesignLoading, setRedesignLoading] = useState<Set<number>>(new Set());
  const handleRedesignPreview = async (decisionId: number, isRegenerate: boolean) => {
    setRedesignLoading(prev => new Set(prev).add(decisionId));
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
      setRedesignLoading(prev => {
        const next = new Set(prev);
        next.delete(decisionId);
        return next;
      });
    }
  };

  // ── Shadow Report Tab State ──
  const [shadowDays, setShadowDays] = useState<number>(7);
  const { data: shadowReport, isValidating: loadingShadow } = useSWR(
    activeTab === "shadow" ? ["radar-shadow", shadowDays] : null,
    () => getShadowReport(shadowDays).then((r) => r.data as ShadowReport)
  );

  const { data: fanpages } = useSWR("fanpages", () => listFanpages().then(r => r.data as Fanpage[]));

  const { data: niches } = useSWR("radar-niches", () => listRadarNiches().then(r => r.data as string[]));

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
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shrink-0">
          <Icon icon="solar:radar-2-bold-duotone" width={24} className="text-white" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-text-primary tracking-tight">Viral Radar</h1>
          <p className="text-sm text-text-secondary">Scrape niche leaders and jump on viral posts early.</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-hairline overflow-x-auto">
        {(["accounts", "stories", "shadow"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab
                ? "border-primary-main text-primary-main"
                : "border-transparent text-text-secondary hover:text-text-primary"
            }`}
          >
            {tab === "accounts" ? "Accounts" : tab === "stories" ? "Rising Stories" : "Shadow Report"}
          </button>
        ))}
      </div>

      {activeTab === "accounts" && (
        <div className="space-y-6">
          <div className="card p-4 flex flex-wrap gap-4 items-end">
            <div className="flex-1 min-w-[200px]">
              <label className="label">Filter by Niche</label>
              <select
                className="input-rect w-full"
                value={accNicheFilter}
                onChange={(e) => setAccNicheFilter(e.target.value)}
              >
                <option value="">All Niches</option>
                {(niches || []).map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>
          </div>

          <form onSubmit={handleCreateAccount} className="card p-4 flex flex-wrap gap-4 items-end">
            <div>
              <label className="label">Niche</label>
              <input
                className="input-rect w-full"
                placeholder="e.g. F1"
                value={newAccNiche}
                onChange={(e) => setNewAccNiche(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="label">Instagram Username</label>
              <input
                className="input-rect w-full"
                placeholder="@username"
                value={newAccUsername}
                onChange={(e) => setNewAccUsername(e.target.value)}
                required
              />
            </div>
            <button
              type="submit"
              disabled={creatingAcc}
              className="btn-primary"
            >
              Add Account
            </button>
          </form>

          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm whitespace-nowrap">
                <thead className="bg-bg-paper-hover border-b border-hairline text-text-secondary">
                  <tr>
                    <th className="py-2.5 px-4 font-medium">Username</th>
                    <th className="py-2.5 px-4 font-medium">Niche</th>
                    <th className="py-2.5 px-4 font-medium">Active</th>
                    <th className="py-2.5 px-4 font-medium">Leader Score</th>
                    <th className="py-2.5 px-4 font-medium">Last Checked</th>
                    <th className="py-2.5 px-4 font-medium">Scrape (s)</th>
                    <th className="py-2.5 px-4 font-medium">Error</th>
                    <th className="py-2.5 px-4 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {(accountsRaw || []).map((acc) => (
                    <tr key={acc.id} className="hover:bg-bg-paper-hover/50">
                      <td className="py-2.5 px-4">
                        <a href={`https://instagram.com/${acc.ig_username}`} target="_blank" rel="noreferrer" className="text-primary-main hover:underline">
                          @{acc.ig_username}
                        </a>
                      </td>
                      <td className="py-2.5 px-4">{acc.niche}</td>
                      <td className="py-2.5 px-4">
                        <button
                          onClick={() => handleToggleAccountActive(acc.id, acc.is_active)}
                          className={`relative w-9 h-5 rounded-full transition-colors ${acc.is_active ? "bg-success-main" : "bg-hairline"}`}
                        >
                          <span className={`absolute top-[2px] left-[2px] w-4 h-4 rounded-full bg-white transition-transform ${acc.is_active ? "translate-x-4" : ""}`} />
                        </button>
                      </td>
                      <td className="py-2.5 px-4">{acc.leader_score.toFixed(2)}</td>
                      <td className="py-2.5 px-4 text-text-secondary">{acc.last_checked_at ? new Date(acc.last_checked_at).toLocaleString() : "-"}</td>
                      <td className="py-2.5 px-4 text-text-secondary">{acc.avg_scrape_seconds ? acc.avg_scrape_seconds.toFixed(1) : "-"}</td>
                      <td className="py-2.5 px-4 text-error-main max-w-[150px] truncate" title={acc.last_error || ""}>{acc.last_error || "-"}</td>
                      <td className="py-2.5 px-4 text-right">
                        <button onClick={() => handleDeleteAccount(acc.id, acc.ig_username)} className="text-text-secondary hover:text-error-main p-1 rounded-md hover:bg-error-lighter">
                          <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {accountsRaw?.length === 0 && (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-text-secondary italic">No accounts found.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {activeTab === "stories" && (
        <div className="space-y-6">
          <div className="card p-4 flex gap-4">
            <div className="flex-1">
              <label className="label">Niche</label>
              <select className="input-rect w-full" value={storyNicheFilter} onChange={(e) => setStoryNicheFilter(e.target.value)}>
                <option value="">All Niches</option>
                {(niches || []).map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <div className="flex-1">
              <label className="label">Status</label>
              <select className="input-rect w-full" value={storyStatusFilter} onChange={(e) => setStoryStatusFilter(e.target.value)}>
                <option value="">All Status</option>
                <option value="watching">Watching</option>
                <option value="triggered">Triggered</option>
                <option value="stale">Stale</option>
                <option value="skipped">Skipped</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {(stories || []).map((s) => (
              <div key={s.id} className="card p-4 space-y-4">
                <div className="flex gap-4">
                  <div className="w-24 h-24 shrink-0 rounded-md overflow-hidden bg-bg-paper-hover border border-hairline">
                    {s.members[0]?.thumbnail_url ? (
                      <img src={s.members[0].thumbnail_url} className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-text-secondary text-xs">No img</div>
                    )}
                  </div>
                  <div className="flex-1 space-y-1">
                    <div className="flex items-start justify-between">
                      <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-primary-lighter text-primary-dark">
                        {s.niche} • {s.status}
                      </span>
                      <span className="text-xs text-text-secondary flex items-center gap-1">
                        <Icon icon="solar:fire-bold-duotone" width={14} className="text-warning-main" />
                        {s.heat_score.toFixed(1)}
                      </span>
                    </div>
                    <div className="text-xs text-text-secondary mt-1">
                      <p>Members: {s.member_count} ({s.distinct_accounts} distinct)</p>
                      <p>Shelf: {s.shelf_kind || "None"} • Expires: {s.expires_at ? new Date(s.expires_at).toLocaleString() : "-"}</p>
                      <p>Final Max Likes (24h): {s.final_max_likes_24h ?? "-"}</p>
                    </div>
                  </div>
                </div>

                <div className="border-t border-hairline pt-3">
                  <p className="text-xs font-semibold mb-2">Members</p>
                  <div className="space-y-1">
                    {s.members.map((m) => (
                      <div key={m.shortcode} className="flex items-center justify-between text-xs bg-bg-paper p-1.5 rounded border border-hairline">
                        <a href={m.post_url} target="_blank" rel="noreferrer" className="text-primary-main hover:underline w-1/3 truncate">
                          @{m.ig_username}
                        </a>
                        <span className="text-text-secondary w-1/4">❤️ {m.latest_like_count ?? "-"}</span>
                        <span className="text-text-secondary w-1/6">💬 {m.latest_comment_count ?? "-"}</span>
                        <span className="text-text-secondary w-1/4 text-right">{formatAge(m.taken_at)}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {s.decisions.length > 0 && (
                  <div className="border-t border-hairline pt-3">
                    <p className="text-xs font-semibold mb-2">Fanpage Decisions</p>
                    <div className="space-y-1.5">
                      {s.decisions.map((d) => (
                        <div key={d.id} className="text-[11px] bg-bg-paper p-2 rounded border border-hairline space-y-1">
                          <div className="flex items-start justify-between">
                            <div>
                              <div className="flex items-center gap-1 mb-1">
                                <span className="font-medium text-text-primary">{d.fanpage_name}</span>
                                {d.shadow && <span className="px-1.5 py-[1px] bg-bg-paper-hover text-text-secondary rounded text-[9px]">SHADOW</span>}
                                <span className="px-1.5 py-[1px] bg-success-lighter text-success-dark rounded text-[9px] uppercase">{d.rule}</span>
                                <span className="px-1.5 py-[1px] bg-bg-paper-hover text-text-secondary rounded text-[9px] uppercase">{d.status}</span>
                                {d.preview_status && (
                                  <span className={`px-1.5 py-[1px] rounded text-[9px] uppercase ${
                                    d.preview_status === 'done' ? 'bg-success-lighter text-success-dark' : 
                                    d.preview_status === 'failed' ? 'bg-error-lighter text-error-dark' : 
                                    'bg-primary-lighter text-primary-dark'
                                  }`}>
                                    {d.preview_status === 'queued' || d.preview_status === 'running' ? 'Generating...' : d.preview_status}
                                  </span>
                                )}
                              </div>
                              {d.reason && <p className="text-text-secondary truncate">{d.reason}</p>}
                              {d.preview_error && <p className="text-error-main text-[10px] mt-0.5 line-clamp-2" title={d.preview_error}>{d.preview_error}</p>}
                            </div>
                            <div className="flex flex-col items-end gap-1">
                              {d.preview_image_path && (
                                <a href={d.preview_image_path} target="_blank" rel="noreferrer">
                                  <img 
                                    src={d.preview_image_path} 
                                    className="w-12 h-16 object-cover rounded cursor-pointer border border-hairline"
                                    alt="Preview"
                                  />
                                </a>
                              )}
                              <button
                                onClick={() => handleRedesignPreview(d.id, !d.shadow)}
                                disabled={redesignLoading.has(d.id) || d.preview_status === 'queued' || d.preview_status === 'running'}
                                className="px-2 py-1 bg-primary-main/10 hover:bg-primary-main/20 text-primary-main rounded text-[10px] font-medium disabled:opacity-50"
                              >
                                {redesignLoading.has(d.id) || d.preview_status === 'queued' || d.preview_status === 'running' ? "Generating..." : (d.shadow ? "Preview" : "Regenerate")}
                              </button>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
            {stories?.length === 0 && <p className="col-span-full text-center py-8 text-text-secondary italic">No stories found.</p>}
          </div>
        </div>
      )}

      {activeTab === "shadow" && (
        <div className="space-y-6">
          <div className="flex gap-2">
            {[1, 3, 7, 14, 30].map(d => (
              <button
                key={d}
                onClick={() => setShadowDays(d)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                  shadowDays === d ? "bg-primary-main text-white border-primary-main" : "bg-bg-paper text-text-secondary border-hairline hover:border-primary-main hover:text-primary-main"
                }`}
              >
                {d} Days
              </button>
            ))}
          </div>
          
          {loadingShadow && <div className="text-center py-8 text-text-secondary"><Icon icon="svg-spinners:ring-resize" width={24} className="mx-auto mb-2" />Loading report...</div>}
          
          {shadowReport && !loadingShadow && (
            <>
              <div className="card overflow-hidden">
                <div className="px-4 py-3 border-b border-hairline bg-bg-paper-hover flex justify-between items-center">
                  <h3 className="font-semibold text-text-primary text-sm">Summary</h3>
                  <span className="text-xs text-text-secondary">Since {shadowDays} days ago</span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm whitespace-nowrap">
                    <thead className="bg-bg-paper border-b border-hairline text-text-secondary">
                      <tr>
                        <th className="py-2.5 px-4 font-medium">Fanpage</th>
                        <th className="py-2.5 px-4 font-medium">Fast</th>
                        <th className="py-2.5 px-4 font-medium">Confirmed</th>
                        <th className="py-2.5 px-4 font-medium">Burst</th>
                        <th className="py-2.5 px-4 font-medium">Median Time (m)</th>
                        <th className="py-2.5 px-4 font-medium">p90 Time (m)</th>
                        <th className="py-2.5 px-4 font-medium">Precision</th>
                        <th className="py-2.5 px-4 font-medium">Missed</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-hairline">
                      <tr className="bg-bg-paper-hover/30 font-medium">
                        <td className="py-2.5 px-4">Overall</td>
                        <td className="py-2.5 px-4">{shadowReport.overall.decisions_by_rule?.fast ?? 0}</td>
                        <td className="py-2.5 px-4">{shadowReport.overall.decisions_by_rule?.confirmed ?? 0}</td>
                        <td className="py-2.5 px-4">{shadowReport.overall.decisions_by_rule?.burst ?? 0}</td>
                        <td className="py-2.5 px-4">{shadowReport.overall.median_minutes.toFixed(1)}</td>
                        <td className="py-2.5 px-4">{shadowReport.overall.p90_minutes.toFixed(1)}</td>
                        <td className="py-2.5 px-4">{(shadowReport.overall.precision * 100).toFixed(1)}%</td>
                        <td className="py-2.5 px-4 text-warning-main">{shadowReport.overall.missed_stories}</td>
                      </tr>
                      {Object.entries(shadowReport.fanpages || {}).map(([fpId, s]) => {
                        const fpName = fanpages?.find((f) => f.id.toString() === fpId)?.name || `Fanpage ${fpId}`;
                        return (
                          <tr key={fpId} className="hover:bg-bg-paper-hover/50">
                            <td className="py-2.5 px-4 pl-8">{fpName}</td>
                            <td className="py-2.5 px-4">{s.decisions_by_rule?.fast ?? 0}</td>
                            <td className="py-2.5 px-4">{s.decisions_by_rule?.confirmed ?? 0}</td>
                            <td className="py-2.5 px-4">{s.decisions_by_rule?.burst ?? 0}</td>
                            <td className="py-2.5 px-4">{s.median_minutes.toFixed(1)}</td>
                            <td className="py-2.5 px-4">{s.p90_minutes.toFixed(1)}</td>
                            <td className="py-2.5 px-4">{(s.precision * 100).toFixed(1)}%</td>
                            <td className="py-2.5 px-4 text-warning-main">{s.missed_stories}</td>
                          </tr>
                        );
                      })}
                      {Object.keys(shadowReport.fanpages || {}).length === 0 && (
                        <tr>
                          <td colSpan={8} className="py-4 text-center italic text-text-secondary">
                            No fanpages with Radar enabled in Shadow Mode.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="card overflow-hidden">
                <div className="px-4 py-3 border-b border-hairline bg-bg-paper-hover">
                  <h3 className="font-semibold text-text-primary text-sm">Recent Decisions</h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm whitespace-nowrap">
                    <thead className="bg-bg-paper border-b border-hairline text-text-secondary">
                      <tr>
                        <th className="py-2.5 px-4 font-medium">Story ID</th>
                        <th className="py-2.5 px-4 font-medium">Rule</th>
                        <th className="py-2.5 px-4 font-medium">Source</th>
                        <th className="py-2.5 px-4 font-medium">Final Likes</th>
                        <th className="py-2.5 px-4 font-medium">Time to Trigger</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-hairline">
                      {(shadowReport.overall?.recent_decisions || []).map((d, i) => (
                        <tr key={i} className="hover:bg-bg-paper-hover/50">
                          <td className="py-2.5 px-4">{d.story_id}</td>
                          <td className="py-2.5 px-4">
                            <span className="px-2 py-0.5 bg-bg-paper-hover rounded text-xs uppercase">{d.rule}</span>
                          </td>
                          <td className="py-2.5 px-4">
                            <a href={`https://instagram.com/p/${d.shortcode}`} target="_blank" rel="noreferrer" className="text-primary-main hover:underline">
                              @{d.username}
                            </a>
                          </td>
                          <td className="py-2.5 px-4">{d.final_likes ?? "-"}</td>
                          <td className="py-2.5 px-4">{d.minutes > 0 ? `${d.minutes.toFixed(1)}m` : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
