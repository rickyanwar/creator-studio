"use client";

import useSWR from "swr";
import { listIGSources, listBurners, assignBurnerToSource, deleteIGSource, autoAssignBurners } from "@/lib/api";
import { format } from "date-fns";
import { useState } from "react";
import { Icon } from "@iconify/react";

type IGSourceRow = {
  id: number;
  ig_username: string;
  burner_id: number | null;
  burner_username: string | null;
  burner_status: string | null;
  is_active: boolean;
  last_checked_at: string | null;
  active_fanpage_count: number;
};

type Burner = {
  id: number;
  ig_username: string;
  status: string;
};

export default function SourcesPage() {
  const { data: sources = [], isLoading, mutate } = useSWR<IGSourceRow[]>(
    "ig-sources",
    () => listIGSources().then((r) => r.data as IGSourceRow[]),
    { refreshInterval: 30000 }
  );
  const { data: burnersData } = useSWR<{ burners: Burner[] }>(
    "burners-list",
    () => listBurners().then((r) => r.data)
  );

  const burners: Burner[] = burnersData?.burners ?? (Array.isArray(burnersData) ? burnersData as Burner[] : []);
  const activeBurners = burners.filter((b) => b.status === "active");

  const [assigning, setAssigning] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [autoAssigning, setAutoAssigning] = useState(false);
  const orphans = sources.filter((s) => s.active_fanpage_count === 0);

  async function handleDelete(sourceId: number, username: string) {
    if (!confirm(`Delete @${username}? This cannot be undone.`)) return;
    setDeleting(sourceId);
    try {
      await deleteIGSource(sourceId);
      mutate();
    } finally {
      setDeleting(null);
    }
  }

  async function handleAutoAssign() {
    setAutoAssigning(true);
    try {
      const res = await autoAssignBurners();
      mutate();
      const count = res.data?.reassigned?.length ?? 0;
      alert(count > 0 ? `Reassigned ${count} source(s) to active burners.` : "All sources already have active burners.");
    } catch {
      alert("Auto-assign failed.");
    } finally {
      setAutoAssigning(false);
    }
  }

  async function handleAssign(sourceId: number, burnerId: string) {
    setAssigning(sourceId);
    try {
      await assignBurnerToSource(sourceId, burnerId ? parseInt(burnerId) : null);
      mutate();
    } finally {
      setAssigning(null);
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1
            className="text-display-md text-ink"
            style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}
          >
            Instagram Sources
          </h1>
          <p className="text-caption text-ink-48 mt-1">
            {sources.length} total sources
            {orphans.length > 0 && (
              <span className="ml-2 text-amber-600">{orphans.length} orphaned (not linked to any fanpage)</span>
            )}
          </p>
        </div>
        {activeBurners.length > 0 && (
          <button
            onClick={handleAutoAssign}
            disabled={autoAssigning}
            className="btn btn-secondary flex items-center gap-2 shrink-0"
          >
            {autoAssigning
              ? <Icon icon="svg-spinners:ring-resize" width={14} />
              : <Icon icon="solar:refresh-bold-duotone" width={14} />}
            Auto-assign Burners
          </button>
        )}
      </div>

      {activeBurners.length === 0 && !isLoading && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 flex items-start gap-3">
          <Icon icon="solar:danger-triangle-bold-duotone" className="text-amber-500 mt-0.5 shrink-0" width={18} />
          <p className="text-sm text-amber-800">
            No active burner accounts found. Go to{" "}
            <a href="/burners" className="font-semibold underline">Burners</a>{" "}
            and import a session first, then come back to assign burners to sources.
          </p>
        </div>
      )}

      {isLoading ? (
        <div className="text-caption text-ink-48">Loading…</div>
      ) : (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-caption">
            <thead className="bg-parchment border-b border-hairline">
              <tr>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">IG Username</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Assigned Burner</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Fanpages</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Last Checked</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Status</th>
                <th className="px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {sources.map((s) => (
                <tr key={s.id} className="hover:bg-parchment/50 transition-colors">
                  <td className="px-5 py-3 text-ink font-medium">@{s.ig_username}</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <select
                        className="input-rect py-1 text-xs min-w-[160px]"
                        value={s.burner_id ?? ""}
                        disabled={assigning === s.id}
                        onChange={(e) => handleAssign(s.id, e.target.value)}
                      >
                        <option value="">— No burner —</option>
                        {activeBurners.map((b) => (
                          <option key={b.id} value={b.id}>
                            @{b.ig_username}
                          </option>
                        ))}
                      </select>
                      {assigning === s.id && (
                        <Icon icon="svg-spinners:ring-resize" className="text-primary-main" width={14} />
                      )}
                      {s.burner_status && s.burner_status !== "active" && (
                        <span className="badge badge-yellow text-[10px]">{s.burner_status}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    {s.active_fanpage_count > 0 ? (
                      <span className="badge-green">{s.active_fanpage_count} active</span>
                    ) : (
                      <span className="badge-yellow">Orphaned</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-ink-48">
                    {s.last_checked_at
                      ? format(new Date(s.last_checked_at), "MMM d HH:mm")
                      : "Never"}
                  </td>
                  <td className="px-5 py-3">
                    <span className={s.is_active ? "badge-green" : "badge-gray"}>
                      {s.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <button
                      onClick={() => handleDelete(s.id, s.ig_username)}
                      disabled={deleting === s.id}
                      className="text-ink-48 hover:text-red-500 transition-colors"
                      title="Delete source"
                    >
                      {deleting === s.id
                        ? <Icon icon="svg-spinners:ring-resize" width={14} />
                        : <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />}
                    </button>
                  </td>
                </tr>
              ))}
              {sources.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-10 text-center text-ink-48">
                    No IG sources yet. Add them via the{" "}
                    <a href="/fanpages" className="text-primary">Fanpages</a> configure page.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
