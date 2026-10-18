"use client";

import { useEffect, useState, useCallback } from "react";
import { F1Driver } from "@/lib/types";
import { listF1Drivers, syncF1Drivers, createF1Driver, updateF1Driver, deleteF1Driver, uploadF1DriverLogo } from "@/lib/api";
import { Icon } from "@iconify/react";

export function F1DriversSettings() {
  const currentYear = new Date().getFullYear();
  const [season, setSeason] = useState(currentYear);
  const [drivers, setDrivers] = useState<F1Driver[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<Partial<F1Driver>>({});
  
  const loadDrivers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listF1Drivers(season);
      setDrivers(res.data);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, [season]);

  useEffect(() => {
    loadDrivers();
  }, [loadDrivers]);

  const handleSave = async (id: number) => {
    setSaving(true);
    try {
      await updateF1Driver(id, editForm);
      setEditingId(null);
      loadDrivers();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Failed to update");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this driver?")) return;
    try {
      await deleteF1Driver(id);
      loadDrivers();
    } catch (e) {
      console.error(e);
    }
  };

  const handleAdd = async () => {
    try {
      await createF1Driver({
        season,
        surname: "NEW DRIVER",
        number: 0,
        team_name: "TBA",
        team_colour: "#000000",
        verified: false,
      });
      loadDrivers();
    } catch (e) {
      console.error(e);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await syncF1Drivers();
      const { inserted, updated, unchanged } = res.data;
      alert(`Sync completed: ${inserted} inserted, ${updated} updated, ${unchanged} unchanged.`);
      loadDrivers();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const handleLogoUpload = async (id: number, file: File) => {
    if (!file) return;
    try {
      await uploadF1DriverLogo(id, file);
      loadDrivers();
    } catch (e: any) {
      alert(e.response?.data?.detail || "Upload failed");
    }
  };

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-text">F1 drivers (team radio)</h2>
          <p className="text-xs text-text2 mt-0.5">
            Driver database used by the designer for team radio cards.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs"
            value={season}
            onChange={(e) => setSeason(parseInt(e.target.value))}
          >
            {[currentYear - 1, currentYear, currentYear + 1, currentYear + 2].map((y) => (
              <option key={y} value={y}>
                {y} Season
              </option>
            ))}
          </select>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text hover:bg-surface2 text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"
          >
            <Icon icon="solar:refresh-bold-duotone" width={14} className={syncing ? "animate-spin" : ""} />
            Sync OpenF1
          </button>
          <button
            onClick={handleAdd}
            className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 flex items-center gap-1.5"
          >
            <Icon icon="solar:add-circle-bold" width={14} /> Add
          </button>
        </div>
      </div>

      <div className="p-3 rounded-lg bg-info-soft text-info text-xs flex items-center gap-2 font-medium">
        <Icon icon="solar:info-circle-bold" width={16} className="shrink-0" />
        <span>Verify numbers and teams for this season — rows marked unverified</span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-xs">
          <thead className="bg-surface2 border-b border-line text-text2 font-semibold">
            <tr>
              <th className="px-3 py-2 text-left">Verified</th>
              <th className="px-3 py-2 text-left">#</th>
              <th className="px-3 py-2 text-left">Surname</th>
              <th className="px-3 py-2 text-left">Full Name</th>
              <th className="px-3 py-2 text-left">Team</th>
              <th className="px-3 py-2 text-left">Colour</th>
              <th className="px-3 py-2 text-left">Logo</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/60 bg-surface">
            {loading ? (
              <tr><td colSpan={8} className="text-center py-4 text-text2">Loading...</td></tr>
            ) : drivers.length === 0 ? (
              <tr><td colSpan={8} className="text-center py-4 text-text2">No drivers for {season}</td></tr>
            ) : (
              drivers.map((d) => (
                <tr key={d.id} className={d.verified ? "hover:bg-surface2/50" : "bg-warn-soft/20 hover:bg-warn-soft/30"}>
                  {editingId === d.id ? (
                    <>
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          className="w-3.5 h-3.5 rounded accent-pri"
                          checked={editForm.verified ?? d.verified}
                          onChange={(e) => setEditForm({ ...editForm, verified: e.target.checked })}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="number"
                          className="h-7 px-2 rounded border border-line bg-surface text-text text-xs w-14"
                          value={editForm.number ?? d.number}
                          onChange={(e) => setEditForm({ ...editForm, number: parseInt(e.target.value) })}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="text"
                          className="h-7 px-2 rounded border border-line bg-surface text-text text-xs w-28 uppercase"
                          value={editForm.surname ?? d.surname}
                          onChange={(e) => setEditForm({ ...editForm, surname: e.target.value.toUpperCase() })}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="text"
                          className="h-7 px-2 rounded border border-line bg-surface text-text text-xs w-28"
                          value={editForm.full_name ?? d.full_name ?? ""}
                          onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="text"
                          className="h-7 px-2 rounded border border-line bg-surface text-text text-xs w-28"
                          value={editForm.team_name ?? d.team_name}
                          onChange={(e) => setEditForm({ ...editForm, team_name: e.target.value })}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1">
                          <input
                            type="color"
                            value={editForm.team_colour ?? d.team_colour}
                            onChange={(e) => setEditForm({ ...editForm, team_colour: e.target.value })}
                            className="w-6 h-6 rounded cursor-pointer border border-line"
                          />
                          <input
                            type="text"
                            className="h-7 px-1.5 rounded border border-line bg-surface text-text text-xs w-20 font-mono"
                            value={editForm.team_colour ?? d.team_colour}
                            onChange={(e) => setEditForm({ ...editForm, team_colour: e.target.value })}
                          />
                        </div>
                      </td>
                      <td className="px-3 py-2 text-text2">—</td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button onClick={() => handleSave(d.id)} disabled={saving} className="h-7 px-2.5 rounded bg-pri text-pri-fg text-xs font-semibold">Save</button>
                          <button onClick={() => setEditingId(null)} disabled={saving} className="h-7 px-2.5 rounded border border-line bg-surface text-text text-xs">Cancel</button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          className="w-3.5 h-3.5 rounded accent-pri"
                          checked={d.verified}
                          onChange={async (e) => {
                            try {
                              await updateF1Driver(d.id, { verified: e.target.checked });
                              loadDrivers();
                            } catch (err) {
                              console.error(err);
                            }
                          }}
                        />
                      </td>
                      <td className="px-3 py-2 font-mono text-text2">{d.number}</td>
                      <td className="px-3 py-2 font-bold text-text">{d.surname}</td>
                      <td className="px-3 py-2 text-text2">{d.full_name || "-"}</td>
                      <td className="px-3 py-2 text-text">{d.team_name}</td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          <div className="w-3.5 h-3.5 rounded border border-line" style={{ backgroundColor: d.team_colour }} />
                          <span className="font-mono text-[11px] text-text2">{d.team_colour}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          {d.team_logo_path ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={`${process.env.NEXT_PUBLIC_API_URL || ""}/${d.team_logo_path}`} className="h-5 object-contain" alt="logo" />
                          ) : (
                            <span className="text-[11px] text-text2">No logo</span>
                          )}
                          <input
                            type="file"
                            className="text-[10px] text-text2 w-28 file:mr-1 file:py-0.5 file:px-1.5 file:rounded file:border-0 file:bg-surface2 file:text-text file:text-[10px]"
                            accept=".png,.jpg,.webp"
                            onChange={(e) => {
                              if (e.target.files?.[0]) handleLogoUpload(d.id, e.target.files[0]);
                            }}
                          />
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => { setEditingId(d.id); setEditForm(d); }}
                            className="p-1 rounded text-text2 hover:text-text hover:bg-surface2"
                            title="Edit"
                          >
                            <Icon icon="solar:pen-bold-duotone" width={14} />
                          </button>
                          <button
                            onClick={() => handleDelete(d.id)}
                            className="p-1 rounded text-text2 hover:text-bad hover:bg-bad-soft"
                            title="Delete"
                          >
                            <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                          </button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
