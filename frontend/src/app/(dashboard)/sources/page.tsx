"use client";

import useSWR from "swr";
import { listIGSources } from "@/lib/api";
import { format } from "date-fns";

const fetcher = () => listIGSources().then((r) => r.data);

export default function SourcesPage() {
  const { data: sources = [], isLoading } = useSWR("ig-sources", fetcher, {
    refreshInterval: 30000,
  });

  const orphans = (sources as Record<string, unknown>[]).filter((s) => (s.active_fanpage_count as number) === 0);

  return (
    <div className="space-y-8">
      <div>
        <h1
          className="text-display-md text-ink"
          style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}
        >
          Instagram Sources
        </h1>
        <p className="text-caption text-ink-48 mt-1">
          {(sources as unknown[]).length} total sources
          {orphans.length > 0 && (
            <span className="ml-2 text-amber-600">{orphans.length} orphaned (not linked to any fanpage)</span>
          )}
        </p>
      </div>

      {isLoading ? (
        <div className="text-caption text-ink-48">Loading…</div>
      ) : (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-caption">
            <thead className="bg-parchment border-b border-hairline">
              <tr>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">IG Username</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Burner</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Fanpages</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Last Checked</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {(sources as Record<string, unknown>[]).map((s) => (
                <tr key={s.id as number} className="hover:bg-parchment/50 transition-colors">
                  <td className="px-5 py-3 text-ink font-medium">@{s.ig_username as string}</td>
                  <td className="px-5 py-3 text-ink-48">
                    {s.burner_username ? (
                      <span>
                        @{s.burner_username as string}
                        <span className={`ml-1.5 badge ${s.burner_status === "active" ? "badge-green" : "badge-yellow"}`}>
                          {s.burner_status as string}
                        </span>
                      </span>
                    ) : (
                      <span className="text-amber-600">No burner</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    {(s.active_fanpage_count as number) > 0 ? (
                      <span className="badge-green">{s.active_fanpage_count as number} active</span>
                    ) : (
                      <span className="badge-yellow">Orphaned</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-ink-48">
                    {s.last_checked_at
                      ? format(new Date(s.last_checked_at as string), "MMM d HH:mm")
                      : "Never"}
                  </td>
                  <td className="px-5 py-3">
                    <span className={(s.is_active as boolean) ? "badge-green" : "badge-gray"}>
                      {(s.is_active as boolean) ? "Active" : "Inactive"}
                    </span>
                  </td>
                </tr>
              ))}
              {(sources as unknown[]).length === 0 && (
                <tr>
                  <td colSpan={5} className="px-5 py-10 text-center text-ink-48">
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
