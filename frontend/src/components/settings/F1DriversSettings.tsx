"use client";

import { useEffect, useState, useCallback } from "react";
import { F1Driver } from "@/lib/types";
import { listF1Drivers, createF1Driver, updateF1Driver, deleteF1Driver, uploadF1DriverLogo } from "@/lib/api";
import { Icon } from "@iconify/react";

export function F1DriversSettings() {
  const currentYear = new Date().getFullYear();
  const [season, setSeason] = useState(currentYear);
  const [drivers, setDrivers] = useState<F1Driver[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
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
    <div className="card mt-6 p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xl font-bold">F1 drivers (Team Radio)</h3>
        <div className="flex gap-2">
          <select
            className="select select-sm select-bordered"
            value={season}
            onChange={(e) => setSeason(parseInt(e.target.value))}
          >
            {[currentYear - 1, currentYear, currentYear + 1, currentYear + 2].map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
          <button onClick={handleAdd} className="btn btn-sm btn-primary">
            <Icon icon="mdi:plus" /> Add
          </button>
        </div>
      </div>

      <div className="alert alert-info mb-4">
        <Icon icon="mdi:information" className="text-xl" />
        <span>Verify numbers/teams for this season — rows marked unverified</span>
      </div>

      <div className="overflow-x-auto">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Verified</th>
              <th>#</th>
              <th>Surname</th>
              <th>Full Name</th>
              <th>Team</th>
              <th>Colour</th>
              <th>Logo</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="text-center py-4">Loading...</td></tr>
            ) : drivers.length === 0 ? (
              <tr><td colSpan={8} className="text-center py-4 text-gray-400">No drivers for {season}</td></tr>
            ) : (
              drivers.map((d) => (
                <tr key={d.id} className={d.verified ? "" : "bg-base-200"}>
                  {editingId === d.id ? (
                    <>
                      <td>
                        <input
                          type="checkbox"
                          className="checkbox checkbox-sm"
                          checked={editForm.verified ?? d.verified}
                          onChange={(e) => setEditForm({ ...editForm, verified: e.target.checked })}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          className="input input-sm input-bordered w-16"
                          value={editForm.number ?? d.number}
                          onChange={(e) => setEditForm({ ...editForm, number: parseInt(e.target.value) })}
                        />
                      </td>
                      <td>
                        <input
                          type="text"
                          className="input input-sm input-bordered w-32"
                          value={editForm.surname ?? d.surname}
                          onChange={(e) => setEditForm({ ...editForm, surname: e.target.value.toUpperCase() })}
                        />
                      </td>
                      <td>
                        <input
                          type="text"
                          className="input input-sm input-bordered w-32"
                          value={editForm.full_name ?? d.full_name ?? ""}
                          onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          type="text"
                          className="input input-sm input-bordered w-32"
                          value={editForm.team_name ?? d.team_name}
                          onChange={(e) => setEditForm({ ...editForm, team_name: e.target.value })}
                        />
                      </td>
                      <td>
                        <div className="flex items-center gap-1">
                          <input
                            type="color"
                            value={editForm.team_colour ?? d.team_colour}
                            onChange={(e) => setEditForm({ ...editForm, team_colour: e.target.value })}
                          />
                          <input
                            type="text"
                            className="input input-sm input-bordered w-24"
                            value={editForm.team_colour ?? d.team_colour}
                            onChange={(e) => setEditForm({ ...editForm, team_colour: e.target.value })}
                          />
                        </div>
                      </td>
                      <td>-</td>
                      <td>
                        <button onClick={() => handleSave(d.id)} disabled={saving} className="btn btn-sm btn-success mr-2">Save</button>
                        <button onClick={() => setEditingId(null)} disabled={saving} className="btn btn-sm">Cancel</button>
                      </td>
                    </>
                  ) : (
                    <>
                      <td>
                        <input
                          type="checkbox"
                          className="checkbox checkbox-sm"
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
                      <td>{d.number}</td>
                      <td className="font-bold">{d.surname}</td>
                      <td>{d.full_name || "-"}</td>
                      <td>{d.team_name}</td>
                      <td>
                        <div className="flex items-center gap-2">
                          <div className="w-4 h-4 rounded" style={{ backgroundColor: d.team_colour }} />
                          <span className="text-xs">{d.team_colour}</span>
                        </div>
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          {d.team_logo_path ? (
                            <img src={`${process.env.NEXT_PUBLIC_API_URL || ""}/${d.team_logo_path}`} className="h-6 object-contain" alt="logo" />
                          ) : (
                            <span className="text-xs opacity-50">No logo</span>
                          )}
                          <input
                            type="file"
                            className="file-input file-input-xs w-full max-w-xs"
                            accept=".png,.jpg,.webp"
                            onChange={(e) => {
                              if (e.target.files?.[0]) handleLogoUpload(d.id, e.target.files[0]);
                            }}
                          />
                        </div>
                      </td>
                      <td>
                        <button onClick={() => { setEditingId(d.id); setEditForm(d); }} className="btn btn-sm btn-ghost p-1">
                          <Icon icon="mdi:pencil" />
                        </button>
                        <button onClick={() => handleDelete(d.id)} className="btn btn-sm btn-ghost text-red-500 p-1">
                          <Icon icon="mdi:trash-can" />
                        </button>
                      </td>
                    </>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
