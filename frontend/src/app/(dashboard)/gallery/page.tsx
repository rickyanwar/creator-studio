"use client";

import useSWR from "swr";
import { useRef, useState } from "react";
import { Icon } from "@iconify/react";
import { formatDistanceToNowStrict } from "date-fns";
import {
  listGalleryKeywords,
  createGalleryKeyword,
  updateGalleryKeyword,
  deleteGalleryKeyword,
  downloadGalleryKeywordNow,
  listGalleryImages,
  uploadGalleryImage,
  deleteGalleryImage,
} from "@/lib/api";

type Keyword = {
  id: number;
  keyword: string;
  is_active: boolean;
  max_images: number;
  min_width: number;
  min_height: number;
  source_engine: string;
  license_filter: string;
  last_downloaded_at: string | null;
  last_download_error: string | null;
  image_count: number;
};

type GalleryImage = {
  id: number;
  keyword: string;
  source_image_url: string;
  public_url: string;
  width: number;
  height: number;
  source_engine: string;
  is_used: boolean;
  downloaded_at: string | null;
};

const emptyForm = {
  keyword: "",
  is_active: true,
  max_images: 50,
  min_width: 500,
  min_height: 500,
  source_engine: "bing",
  license_filter: "commercial,modify",
};

export default function GalleryPage() {
  const { data: keywords = [], mutate: mutateKeywords } = useSWR<Keyword[]>(
    "gallery-keywords",
    () => listGalleryKeywords().then((r) => r.data as Keyword[]),
    { refreshInterval: 30000 }
  );

  const [filterKeyword, setFilterKeyword] = useState<string>("");
  const { data: imageData, mutate: mutateImages } = useSWR<{ total: number; images: GalleryImage[] }>(
    ["gallery-images", filterKeyword],
    () =>
      listGalleryImages({ keyword: filterKeyword || undefined, limit: 60 }).then(
        (r) => r.data as { total: number; images: GalleryImage[] }
      ),
    { refreshInterval: 30000 }
  );
  const images = imageData?.images ?? [];

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function set(field: string, value: unknown) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function openCreate() {
    setForm(emptyForm);
    setEditingId(null);
    setShowForm(true);
  }

  function openEdit(k: Keyword) {
    setForm({
      keyword: k.keyword,
      is_active: k.is_active,
      max_images: k.max_images,
      min_width: k.min_width,
      min_height: k.min_height,
      source_engine: k.source_engine,
      license_filter: k.license_filter,
    });
    setEditingId(k.id);
    setShowForm(true);
  }

  async function handleSave() {
    if (!form.keyword.trim()) {
      alert("Keyword is required.");
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        await updateGalleryKeyword(editingId, form);
      } else {
        await createGalleryKeyword(form);
      }
      setShowForm(false);
      mutateKeywords();
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      alert(`Save failed: ${err.response?.data?.detail ?? "unknown error"}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteKeyword(k: Keyword) {
    if (!confirm(`Delete keyword "${k.keyword}"? Downloaded images are kept.`)) return;
    await deleteGalleryKeyword(k.id);
    mutateKeywords();
  }

  async function handleDownloadNow(id: number) {
    setDownloading(id);
    try {
      await downloadGalleryKeywordNow(id);
      alert("Download queued — new images will appear within a few minutes.");
    } finally {
      setDownloading(null);
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const keyword = filterKeyword || prompt("Keyword for this image (e.g. marc marquez):")?.trim();
    if (!keyword) return;
    setUploading(true);
    try {
      await uploadGalleryImage(file, keyword);
      mutateImages();
      mutateKeywords();
    } catch (err) {
      const ex = err as { response?: { data?: { detail?: string } } };
      alert(`Upload failed: ${ex.response?.data?.detail ?? "unknown error"}`);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleDeleteImage(img: GalleryImage) {
    if (!confirm("Delete this image?")) return;
    await deleteGalleryImage(img.id);
    mutateImages();
    mutateKeywords();
  }

  const inputCls = "input-rect py-2";
  const labelCls = "block text-xs font-semibold text-ink-80 mb-1";

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1
            className="text-display-md text-ink"
            style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}
          >
            Gallery
          </h1>
          <p className="text-caption text-ink-48 mt-1">
            {imageData?.total ?? 0} image{(imageData?.total ?? 0) === 1 ? "" : "s"} — used by the News-to-Image designer
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleUpload} />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="btn btn-secondary flex items-center gap-2"
          >
            {uploading ? <Icon icon="svg-spinners:ring-resize" width={16} /> : <Icon icon="solar:upload-bold-duotone" width={16} />}
            Upload Image
          </button>
          <button onClick={openCreate} className="btn btn-primary flex items-center gap-2">
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            Add Keyword
          </button>
        </div>
      </div>

      {/* ── Add/Edit keyword form ── */}
      {showForm && (
        <div className="card p-6 space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold text-ink">{editingId ? "Edit Keyword" : "New Download Keyword"}</h2>
            <button onClick={() => setShowForm(false)} className="text-ink-48 hover:text-ink">
              <Icon icon="solar:close-circle-bold-duotone" width={20} />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className={labelCls}>Keyword</label>
              <input className={inputCls} value={form.keyword} onChange={(e) => set("keyword", e.target.value)} placeholder="marc marquez" />
            </div>
            <div>
              <label className={labelCls}>Max Images per Run</label>
              <input type="number" min={1} max={200} className={inputCls} value={form.max_images} onChange={(e) => set("max_images", parseInt(e.target.value) || 50)} />
            </div>
            <div>
              <label className={labelCls}>Fallback Engine</label>
              <select className={inputCls} value={form.source_engine} onChange={(e) => set("source_engine", e.target.value)}>
                <option value="bing">Bing (icrawler)</option>
                <option value="google">Google</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Min Width (px)</label>
              <input type="number" min={100} className={inputCls} value={form.min_width} onChange={(e) => set("min_width", parseInt(e.target.value) || 500)} />
            </div>
            <div>
              <label className={labelCls}>Min Height (px)</label>
              <input type="number" min={100} className={inputCls} value={form.min_height} onChange={(e) => set("min_height", parseInt(e.target.value) || 500)} />
            </div>
            <div>
              <label className={labelCls}>License Filter</label>
              <select className={inputCls} value={form.license_filter} onChange={(e) => set("license_filter", e.target.value)}>
                <option value="commercial,modify">Commercial + Modify</option>
                <option value="commercial">Commercial</option>
                <option value="creativecommons">Creative Commons</option>
                <option value="publicdomain">Public Domain</option>
                <option value="">No filter (risky)</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end gap-2 border-t border-hairline pt-4">
            <button onClick={() => setShowForm(false)} className="btn btn-secondary">Cancel</button>
            <button onClick={handleSave} disabled={saving} className="btn btn-primary flex items-center gap-2">
              {saving && <Icon icon="svg-spinners:ring-resize" width={14} />}
              {editingId ? "Save Changes" : "Create Keyword"}
            </button>
          </div>
        </div>
      )}

      {/* ── Keywords table ── */}
      {keywords.length > 0 && (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-caption">
            <thead className="bg-parchment border-b border-hairline">
              <tr>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Keyword</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Images</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Min Size</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">License</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Last Download</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Status</th>
                <th className="px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {keywords.map((k) => (
                <tr key={k.id} className="hover:bg-parchment/50 transition-colors">
                  <td className="px-5 py-3">
                    <button onClick={() => setFilterKeyword(filterKeyword === k.keyword ? "" : k.keyword)} className="text-ink font-medium hover:text-primary-main">
                      {k.keyword}
                    </button>
                  </td>
                  <td className="px-5 py-3 text-ink-80">{k.image_count}</td>
                  <td className="px-5 py-3 text-ink-80">{k.min_width}×{k.min_height}</td>
                  <td className="px-5 py-3 text-ink-80">{k.license_filter || "—"}</td>
                  <td className="px-5 py-3 text-ink-80">
                    {k.last_downloaded_at ? formatDistanceToNowStrict(new Date(k.last_downloaded_at), { addSuffix: true }) : "never"}
                  </td>
                  <td className="px-5 py-3">
                    {!k.is_active ? (
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-600">Inactive</span>
                    ) : k.last_download_error ? (
                      <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700" title={k.last_download_error}>Error</span>
                    ) : (
                      <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">Active</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => handleDownloadNow(k.id)}
                        disabled={downloading === k.id}
                        title="Download now"
                        className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                      >
                        {downloading === k.id ? <Icon icon="svg-spinners:ring-resize" width={16} /> : <Icon icon="solar:download-bold-duotone" width={16} />}
                      </button>
                      <button
                        onClick={() => updateGalleryKeyword(k.id, { is_active: !k.is_active }).then(() => mutateKeywords())}
                        title={k.is_active ? "Deactivate" : "Activate"}
                        className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                      >
                        <Icon icon={k.is_active ? "solar:pause-circle-bold-duotone" : "solar:play-bold-duotone"} width={16} />
                      </button>
                      <button
                        onClick={() => openEdit(k)}
                        title="Edit"
                        className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                      >
                        <Icon icon="solar:pen-bold-duotone" width={16} />
                      </button>
                      <button
                        onClick={() => handleDeleteKeyword(k)}
                        title="Delete"
                        className="p-1.5 rounded-lg text-ink-48 hover:text-red-600 hover:bg-red-50 transition-colors"
                      >
                        <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Image grid ── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-bold text-ink">Images</h2>
          {filterKeyword && (
            <button
              onClick={() => setFilterKeyword("")}
              className="inline-flex items-center gap-1 rounded-full bg-primary-main/10 text-primary-main px-2.5 py-0.5 text-[11px] font-semibold"
            >
              {filterKeyword}
              <Icon icon="solar:close-circle-bold" width={12} />
            </button>
          )}
        </div>

        {images.length === 0 ? (
          <div className="card p-10 text-center text-ink-48">
            <Icon icon="solar:gallery-wide-bold-duotone" width={40} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">
              {keywords.length === 0
                ? "No keywords yet. Add one and hit Download Now, or upload images manually."
                : "No images yet — queue a download or upload manually."}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
            {images.map((img) => (
              <div key={img.id} className="card p-0 overflow-hidden group relative">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={img.public_url} alt={img.keyword} className="w-full h-36 object-cover" loading="lazy" />
                <div className="p-2">
                  <p className="text-[11px] font-semibold text-ink truncate">{img.keyword}</p>
                  <p className="text-[10px] text-ink-48">
                    {img.width}×{img.height} · {img.source_engine}
                    {img.is_used && <span className="ml-1 text-emerald-600 font-semibold">used</span>}
                  </p>
                  {img.downloaded_at && (
                    <p className="text-[10px] text-ink-48">{formatDistanceToNowStrict(new Date(img.downloaded_at), { addSuffix: true })}</p>
                  )}
                </div>
                <button
                  onClick={() => handleDeleteImage(img)}
                  title="Delete"
                  className="absolute top-1.5 right-1.5 p-1 rounded-lg bg-black/40 text-white opacity-0 group-hover:opacity-100 hover:bg-red-600 transition-all"
                >
                  <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
