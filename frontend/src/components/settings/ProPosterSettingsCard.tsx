"use client";

import { useState } from "react";
import useSWR from "swr";
import type { AppSettings, Fanpage } from "@/lib/types";
import { updateSettings, listFanpages } from "@/lib/api";

export function ProPosterSettingsCard({
  settings,
  onChanged,
}: {
  settings: AppSettings | undefined;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const { data: fanpages } = useSWR<Fanpage[]>("fanpages", () =>
    listFanpages().then((r) => (Array.isArray(r.data) ? r.data : []))
  );

  async function update(key: string, value: unknown) {
    setError(null);
    try {
      await updateSettings({ [key]: value });
      onChanged();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
          ? detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ")
          : "Failed to update pro poster settings"
      );
    }
  }

  const selectedFanpageIds = settings?.pro_poster_fanpage_ids ?? [];
  const isAll = selectedFanpageIds.length === 0;

  function toggleFanpage(id: number) {
    const next = selectedFanpageIds.includes(id)
      ? selectedFanpageIds.filter((item) => item !== id)
      : [...selectedFanpageIds, id];
    update("pro_poster_fanpage_ids", next);
  }

  function setAll() {
    update("pro_poster_fanpage_ids", []);
  }

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-text">Pro poster (news)</h2>
        <p className="text-xs text-text2 mt-1">
          Story-driven ChatGPT poster for news cards: one generation per story, real verified photos, identity and event checks. Falls back to the normal card when a check fails.
        </p>
      </div>

      {error && (
        <p className="text-xs text-bad bg-bad-soft rounded-lg p-2.5">{error}</p>
      )}

      <label className="flex items-center gap-3 text-sm text-text cursor-pointer">
        <input
          type="checkbox"
          className="w-4 h-4 accent-pri rounded"
          checked={settings?.pro_poster_enabled ?? false}
          onChange={(e) => update("pro_poster_enabled", e.target.checked)}
        />
        <span>Enable Pro poster</span>
      </label>

      <div>
        <label className="block text-xs font-semibold text-text">Fan pages</label>
        <p className="text-xs text-text2 mb-2">
          Use it on all fan pages, or pick specific ones.
        </p>
        <div className="mb-2">
          <label className="flex items-center gap-2 text-xs text-text cursor-pointer">
            <input
              type="checkbox"
              className="w-4 h-4 accent-pri rounded"
              checked={isAll}
              onChange={setAll}
            />
            <span className="font-medium">All fan pages</span>
          </label>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-48 overflow-y-auto p-2 rounded-lg border border-line bg-surface2">
          {fanpages && fanpages.length > 0 ? (
            fanpages.map((fp) => {
              const checked = selectedFanpageIds.includes(fp.id);
              return (
                <label
                  key={fp.id}
                  className="flex items-center gap-2 text-xs text-text cursor-pointer"
                >
                  <input
                    type="checkbox"
                    className="w-4 h-4 accent-pri rounded"
                    checked={checked}
                    onChange={() => toggleFanpage(fp.id)}
                  />
                  <span className="truncate">{fp.name}</span>
                </label>
              );
            })
          ) : (
            <span className="text-xs text-text2">No fanpages found</span>
          )}
        </div>
      </div>
    </section>
  );
}
