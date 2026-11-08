"use client";

import { useEffect, useState, type FormEvent } from "react";
import useSWR from "swr";
import { listFbGroups, listFbGroupNiches, createFbGroup, updateFbGroup, deactivateFbGroup } from "@/lib/api";
import type { FbGroup, FbGroupIn, FbGroupKind } from "@/lib/types";
import { Field, NumberField, Toggle, TableWrap, inputClass, panelClass, buttonClass, primaryClass, errorMessage } from "@/components/brand/group-share/Fields";

const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const kinds: Record<FbGroupKind, string> = { person_fan: "Person fans", team_fan: "Team fans", general: "General", meme: "Meme" };
const emptyGroup: FbGroupIn = {
  url: "", name: "", niche: "", kind: "general", subject: null, language: "en", posting_identity: "page_allowed",
  needs_admin_approval: false, allowed_days: null, banned_content: null, daily_cap: 1, notes: null,
};

function GroupForm({ group, niches, onSaved, onClose }: { group: FbGroup | null; niches: string[]; onSaved: () => Promise<unknown>; onClose: () => void }) {
  const [form, setForm] = useState<FbGroupIn>(group ? {
    url: group.url, name: group.name, niche: group.niche, kind: group.kind, subject: group.subject, language: group.language,
    posting_identity: group.posting_identity, needs_admin_approval: group.needs_admin_approval, allowed_days: group.allowed_days,
    banned_content: group.banned_content, daily_cap: group.daily_cap, notes: group.notes,
  } : { ...emptyGroup });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = <K extends keyof FbGroupIn>(key: K, value: FbGroupIn[K]) => setForm((prev) => ({ ...prev, [key]: value }));
  const needsSubject = form.kind === "person_fan" || form.kind === "team_fan";

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true); setError("");
    try {
      if (!form.name.trim() || !form.niche.trim() || (needsSubject && !form.subject?.trim())) throw new Error("Name, niche and the required subject must not be blank.");
      const url = new URL(form.url);
      if (!["https:", "http:"].includes(url.protocol)) throw new Error("Enter a valid HTTP or HTTPS group link.");
      const payload = { ...form, url: form.url.trim(), name: form.name.trim(), niche: form.niche.trim(), language: form.language.trim(), subject: needsSubject ? form.subject?.trim() || null : null };
      if (group) await updateFbGroup(group.id, payload);
      else await createFbGroup(payload);
      await onSaved(); onClose();
    } catch (err) { setError(errorMessage(err)); }
    finally { setSaving(false); }
  }

  return <form onSubmit={save} className={panelClass} aria-labelledby="group-form-heading">
    <h2 id="group-form-heading" className="text-lg font-semibold text-text">{group ? "Edit group" : "Add group"}</h2>
    <fieldset disabled={saving} className="min-w-0 space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="Group link"><input autoFocus required type="url" className={inputClass} value={form.url} onChange={(e) => set("url", e.target.value)} /></Field>
        <Field label="Name"><input required className={inputClass} value={form.name} onChange={(e) => set("name", e.target.value)} /></Field>
        <Field label="Niche"><input required className={inputClass} list="library-niches" value={form.niche} onChange={(e) => set("niche", e.target.value)} /></Field>
        <datalist id="library-niches">{niches.map((niche) => <option key={niche} value={niche} />)}</datalist>
        <Field label="Kind"><select className={inputClass} value={form.kind} onChange={(e) => set("kind", e.target.value as FbGroupKind)}>{Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
        {needsSubject && <Field label="Subject"><input required className={inputClass} value={form.subject ?? ""} onChange={(e) => set("subject", e.target.value)} /></Field>}
        <Field label="Language"><input required className={inputClass} list="group-languages" value={form.language} onChange={(e) => set("language", e.target.value)} /></Field>
        <datalist id="group-languages"><option value="en" /><option value="id" /></datalist>
        <Field label="Who can post"><select className={inputClass} value={form.posting_identity} onChange={(e) => set("posting_identity", e.target.value as FbGroupIn["posting_identity"])}><option value="page_allowed">Page or profile</option><option value="profile_only">Profile only</option></select></Field>
        <NumberField label="Cap per day" min={1} value={form.daily_cap} onChange={(value) => set("daily_cap", value ?? 1)} />
      </div>
      <Toggle label="Needs admin approval" checked={form.needs_admin_approval} onChange={(value) => set("needs_admin_approval", value)} />
      <fieldset><legend className="text-sm font-medium text-text">Allowed days (none = every day)</legend><div className="flex flex-wrap gap-x-4">{days.map((day, index) => <Toggle key={day} label={day} checked={form.allowed_days?.includes(index) ?? false} onChange={(checked) => {
        const selected = checked ? [...(form.allowed_days ?? []), index].sort((a, b) => a - b) : (form.allowed_days ?? []).filter((value) => value !== index);
        set("allowed_days", selected.length ? selected : null);
      }} />)}</div></fieldset>
      <fieldset><legend className="text-sm font-medium text-text">Banned content</legend><div className="flex flex-wrap gap-x-4">{["news", "photo", "stat", "meme", "video"].map((kind) => <Toggle key={kind} label={kind} checked={form.banned_content?.includes(kind) ?? false} onChange={(checked) => {
        const selected = checked ? [...(form.banned_content ?? []), kind] : (form.banned_content ?? []).filter((value) => value !== kind);
        set("banned_content", selected.length ? selected : null);
      }} />)}</div></fieldset>
      <Field label="Notes"><textarea className={inputClass} rows={3} value={form.notes ?? ""} onChange={(e) => set("notes", e.target.value || null)} /></Field>
    </fieldset>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    <div className="flex flex-wrap gap-3"><button disabled={saving} type="submit" className={primaryClass}>{saving ? "Saving…" : "Save group"}</button><button disabled={saving} type="button" className={buttonClass} onClick={onClose}>Cancel</button></div>
  </form>;
}

export default function FacebookGroupsPage() {
  const [niche, setNiche] = useState("");
  const [showPaused, setShowPaused] = useState(false);
  const niches = useSWR("fb-group-niches", listFbGroupNiches);
  const groups = useSWR(["fb-groups", niche, showPaused], () => listFbGroups({ niche: niche || undefined, include_inactive: showPaused }));
  const [editor, setEditor] = useState<{ group: FbGroup | null } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (niche && niches.data && !niches.data.includes(niche)) setNiche("");
  }, [niche, niches.data]);

  async function refresh() { await Promise.all([groups.mutate(), niches.mutate()]); }

  async function toggleGroup(group: FbGroup) {
    setBusy(group.id); setError(""); setMessage("");
    try {
      if (group.is_active) await deactivateFbGroup(group.id);
      else await updateFbGroup(group.id, { is_active: true });
      await refresh(); setMessage(group.is_active ? "Group paused" : "Group resumed");
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(null); }
  }

  return <div className="mx-auto w-full min-w-0 max-w-7xl space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><h1 className="text-2xl font-bold text-text">Facebook groups</h1>
      <p className="mt-2 text-sm text-text2">Group library per niche, shared by all brands. Each brand picks its niches in Brand → Group share.</p></div>
      <button type="button" className={primaryClass} onClick={() => { setEditor({ group: null }); setMessage(""); }}>Add group</button>
    </header>
    {editor && <GroupForm key={editor.group?.id ?? "new"} group={editor.group} niches={niches.data ?? []} onSaved={async () => { await refresh(); setMessage("Saved"); }} onClose={() => setEditor(null)} />}
    <section className={panelClass} aria-label="Group library">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap gap-2" role="group" aria-label="Filter by niche">{["", ...(niches.data ?? [])].map((value) => <button type="button" key={value} aria-pressed={niche === value} className={`${buttonClass} max-w-full break-words ${niche === value ? "bg-pri-soft text-pri-text" : ""}`} onClick={() => setNiche(value)}>{value || "All"}</button>)}</div>
        <Toggle switchRole label="Show paused" checked={showPaused} onChange={setShowPaused} />
      </div>
      {(error || groups.error || niches.error) && <p role="alert" className="text-sm text-red-500">{error || errorMessage(groups.error || niches.error)}</p>}
      <p role="status" className="text-sm text-pri-text">{message}</p>
      {!groups.data ? <p className="text-sm text-text2">{groups.error ? "Groups could not be loaded." : "Loading groups…"}</p> : groups.data.length === 0 ? <p className="text-sm text-text2">No groups in this niche yet.</p> : <TableWrap label="Facebook group library" tableClassName="min-w-[880px] table-fixed !text-xs [&_th]:!whitespace-normal [&_th]:!px-2 [&_td]:!px-2 [&_td]:break-words">
        <colgroup><col /><col className="w-[76px]" /><col className="w-[64px]" /><col className="w-[64px]" /><col className="w-[52px]" /><col className="w-[76px]" /><col className="w-[56px]" /><col className="w-[76px]" /><col className="w-[60px]" /><col className="w-[60px]" /><col className="w-[128px]" /></colgroup>
        <thead><tr>{["Name", "Niche", "Kind", "Subject", "Language", "Who can post", "Admin approval", "Days", "Cap/day", "Status", "Actions"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{groups.data.map((group) => <tr key={group.id}>
          <td><a className="inline-flex min-h-[44px] max-w-[240px] items-center whitespace-normal break-words text-pri-text [overflow-wrap:anywhere]" href={group.url} target="_blank" rel="noopener noreferrer">{group.name}</a></td>
          <td>{group.niche}</td><td>{kinds[group.kind]}</td><td>{group.subject || "—"}</td><td>{group.language}</td>
          <td>{group.posting_identity === "page_allowed" ? "Page or profile" : "Profile only"}</td><td>{group.needs_admin_approval ? "Yes" : "No"}</td>
          <td>{group.allowed_days?.length ? [...group.allowed_days].sort((a, b) => a - b).map((day) => days[day]).join(", ") : "Every day"}</td><td>{group.daily_cap}</td>
          <td><span className="rounded-full bg-surface2 px-1 py-1 text-xs font-medium">{group.is_active ? "Active" : "Paused"}</span>{group.paused_reason && <p className="mt-2 text-xs text-text2">{group.paused_reason}</p>}</td>
          <td><div className="flex flex-nowrap gap-1"><button type="button" className="btn-secondary h-9 min-w-[44px] shrink-0 !px-2 !text-xs whitespace-nowrap" disabled={busy !== null} aria-label={`Edit ${group.name}`} onClick={() => setEditor({ group })}>Edit</button>
            <button type="button" className="btn-secondary h-9 min-w-[44px] shrink-0 !px-2 !text-xs whitespace-nowrap" disabled={busy !== null} aria-label={`${group.is_active ? "Pause" : "Resume"} ${group.name}`} onClick={() => toggleGroup(group)}>{busy === group.id ? "Saving…" : group.is_active ? "Pause" : "Resume"}</button></div></td>
        </tr>)}</tbody>
      </TableWrap>}
    </section>
  </div>;
}
