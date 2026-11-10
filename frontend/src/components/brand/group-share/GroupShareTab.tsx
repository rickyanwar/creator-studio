"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";
import { getGroupShare, updateGroupShare, listFbGroupNiches, listFbGroups, listGroupShareAccounts, listGroupShareMemberships, updateGroupShareMemberships, listGroupShareTasks, getGroupShareSummary } from "@/lib/api";
import type { GroupShareConfig, GroupShareSettings, GroupShareAccount, GroupShareMembershipIn, GroupShareMembershipStatus } from "@/lib/types";
import { DeliveryEditor } from "./DeliveryEditor";
import { BrandFollowTable } from "./BrandFollowTable";
import { LaneContent } from "./LaneContent";
import { useLaneFanpage } from "./useLaneFanpage";
import { Field, NumberField, Toggle, TableWrap, inputClass, panelClass, buttonClass, primaryClass, errorMessage, deliveryLabels } from "./Fields";

const membershipOptions: [GroupShareMembershipStatus, string][] = [["joined", "Joined"], ["not_member", "Not a member"], ["pending_approval", "Pending approval"], ["removed", "Removed"], ["unknown", "Unknown"], ["banned", "Banned"]];

export function GroupShareTab({ fanpageId }: { fanpageId: number }) {
  const config = useSWR(["group-share", fanpageId], () => getGroupShare(fanpageId));
  const niches = useSWR("fb-group-niches", listFbGroupNiches);
  const accounts = useSWR(["group-share-accounts", fanpageId], () => listGroupShareAccounts(fanpageId));
  const memberships = useSWR(["group-share-memberships", fanpageId], () => listGroupShareMemberships(fanpageId));
  const tasks = useSWR(["group-share-tasks", fanpageId], () => listGroupShareTasks(fanpageId, { limit: 50 }), { refreshInterval: 30000 });
  const summary = useSWR(["group-share-summary", fanpageId], () => getGroupShareSummary(fanpageId), { refreshInterval: 30000 });
  const lane = useLaneFanpage(fanpageId);
  const [draft, setDraft] = useState<GroupShareConfig | null>(null);
  const form = draft ?? config.data;
  const selectedNiches = form?.settings.niches ?? [];
  const groups = useSWR(["group-share-groups", selectedNiches], async () => {
    const lists = await Promise.all(selectedNiches.map((niche) => listFbGroups({ niche, include_inactive: false })));
    return Array.from(new Map(lists.flat().filter((group) => group.is_active).map((group) => [group.id, group])).values());
  });
  const [nicheInput, setNicheInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<GroupShareAccount | null>(null);
  const [membershipDraft, setMembershipDraft] = useState<Record<string, GroupShareMembershipIn>>({});
  const [membershipSaving, setMembershipSaving] = useState(false);
  const [membershipMessage, setMembershipMessage] = useState("");
  const [membershipError, setMembershipError] = useState("");

  function setSettings(settings: GroupShareSettings) {
    if (form) setDraft({ ...form, settings });
    setMessage("");
  }

  function addNiche() {
    const value = nicheInput.trim();
    if (!form || !value) return;
    if (!selectedNiches.includes(value)) setSettings({ ...form.settings, niches: [...selectedNiches, value] });
    setNicheInput("");
  }

  // Saves the group share settings and the group content (lane fanpage) together.
  async function saveAll() {
    if (!form) return;
    setSaving(true); setError(""); setMessage("");
    try {
      const saved = await updateGroupShare(fanpageId, form);
      await config.mutate(saved, { revalidate: false });
      setDraft(null);
      await lane.save();
      setMessage("Saved");
      await summary.mutate();
    } catch (err) { setError(errorMessage(err)); }
    finally { setSaving(false); }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    void saveAll();
  }

  async function saveMemberships(event: FormEvent) {
    event.preventDefault();
    setMembershipSaving(true); setMembershipError(""); setMembershipMessage("");
    try {
      const saved = await updateGroupShareMemberships(fanpageId, Object.values(membershipDraft));
      await memberships.mutate(saved, { revalidate: false });
      setMembershipDraft({}); setMembershipMessage("Saved");
      await summary.mutate();
    } catch (err) { setMembershipError(errorMessage(err)); }
    finally { setMembershipSaving(false); }
  }

  const loadErrors = [config.error, niches.error, accounts.error, memberships.error, groups.error, tasks.error, summary.error].filter(Boolean);
  const settings = form?.settings;
  return <div role="tabpanel" id="tab-panel-groupshare" aria-labelledby="tab-groupshare" className="min-w-0 space-y-6 pb-8">
    {loadErrors.length > 0 && <div role="alert" className="rounded-xl border border-red-500 p-4 text-sm text-red-500">{loadErrors.map(errorMessage).join("; ")}</div>}
    {!form || !settings ? <p className="text-sm text-text2">{config.error ? "Group share settings could not be loaded." : "Loading group share…"}</p> : <>
      <fieldset disabled={saving} className="min-w-0 space-y-6">
        <section className={panelClass} aria-labelledby="group-share-heading">
          <div className="flex flex-wrap items-center justify-between gap-3"><h2 id="group-share-heading" className="text-lg font-semibold text-text">Group share</h2>
            <button type="button" role="switch" aria-label="Group share on/off" aria-checked={form.enabled}
              onClick={() => { setDraft({ ...form, enabled: !form.enabled }); setMessage(""); }}
              className="inline-flex min-h-[44px] items-center gap-2 px-2.5 py-1 rounded-xl border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold cursor-pointer transition-all disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-pri">
              <span aria-hidden="true" className={`relative inline-block w-7 h-4 rounded-full transition-colors ${form.enabled ? "bg-pri" : "bg-line"}`}>
                <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full transition-transform shadow-sm ${form.enabled ? "bg-pri-fg translate-x-3" : "bg-surface"}`} />
              </span>
              <span>{form.enabled ? "ON" : "OFF"}</span>
            </button>
          </div>
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">{([
            ["Posted today", summary.data?.today_published], ["Planned", summary.data?.today_planned], ["Pending admin", summary.data?.pending_admin], ["Groups joined", summary.data?.groups_joined],
          ] as const).map(([label, value]) => <div key={label} className="rounded-xl border border-line bg-surface2 p-3"><dt className="text-xs text-text2">{label}</dt><dd className="mt-1 text-xl font-semibold text-text">{value ?? "—"}</dd></div>)}</dl>
        </section>
        <section className={panelClass} aria-labelledby="group-niches-heading"><h3 id="group-niches-heading" className="font-semibold text-text">Niches</h3>
          <div className="flex flex-wrap gap-2">{selectedNiches.map((niche) => <span key={niche} className="inline-flex max-w-full items-center rounded-xl bg-pri-soft pl-3 text-sm text-pri-text"><span className="break-all">{niche}</span><button type="button" className="min-h-[44px] min-w-[44px]" aria-label={`Remove niche ${niche}`} onClick={() => setSettings({ ...settings, niches: selectedNiches.filter((value) => value !== niche) })}>×</button></span>)}</div>
          <div className="flex flex-wrap items-end gap-3"><div className="min-w-0 flex-1"><Field label="Add niche"><input className={inputClass} list="group-share-niche-suggestions" value={nicheInput} onChange={(e) => setNicheInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addNiche(); } }} /></Field></div><button type="button" className={buttonClass} onClick={addNiche}>Add niche</button></div>
          <datalist id="group-share-niche-suggestions">{niches.data?.map((niche) => <option key={niche} value={niche} />)}</datalist>
        </section>
      </fieldset>
      {/* Outside the <form>: the Content-tab components use plain buttons that would submit it. */}
      <LaneContent brandId={fanpageId} lane={lane} settings={settings} onSettingsChange={setSettings} onSave={() => void saveAll()} saving={saving}
        status={<>{error && <p className="text-sm text-red-500">{error}</p>}{message && <p className="text-sm text-pri-text">{message}</p>}</>} />
      <form onSubmit={save} className="min-w-0 space-y-6">
      <fieldset disabled={saving} className="min-w-0 space-y-6">
        <BrandFollowTable fanpageId={fanpageId} settings={settings} onChange={setSettings} />
        <section className={panelClass} aria-labelledby="group-muse-heading"><h3 id="group-muse-heading" className="font-semibold text-text">Muse research for groups</h3>
          <Toggle switchRole label="Enable Muse research" checked={settings.muse.enabled} onChange={(enabled) => setSettings({ ...settings, muse: { ...settings.muse, enabled } })} />
          <NumberField label="Items per day" value={settings.muse.per_day} onChange={(per_day) => setSettings({ ...settings, muse: { ...settings.muse, per_day: per_day ?? 0 } })} />
        </section>
        <section className={panelClass} aria-labelledby="group-rules-heading"><h3 id="group-rules-heading" className="font-semibold text-text">Content rules</h3>
          <Toggle label="Add credit for other people's content" checked={settings.credit} onChange={(credit) => setSettings({ ...settings, credit })} />
          <Toggle label="Must be edited before sharing" checked={settings.must_edit} onChange={(must_edit) => setSettings({ ...settings, must_edit })} />
        </section>
        <section className={panelClass} aria-labelledby="group-caps-heading"><h3 id="group-caps-heading" className="font-semibold text-text">Safety caps</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">{([
            ["start_per_day", "Start per day", 0], ["weekly_increase", "Weekly increase", 0], ["max_per_day", "Maximum per day", 0], ["min_gap_minutes", "Minimum gap (minutes)", 15], ["per_group_per_day", "Posts per group per day", 0],
          ] as const).map(([key, label, min]) => <NumberField key={key} label={label} min={min} value={settings.caps[key]} onChange={(value) => setSettings({ ...settings, caps: { ...settings.caps, [key]: value ?? min } })} />)}</div>
        </section>
      </fieldset>
      {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
      <div className="flex flex-wrap items-center gap-3"><button className={primaryClass} type="submit" disabled={saving}>{saving ? "Saving…" : "Save group share"}</button><p role="status" className="text-sm text-pri-text">{message}</p></div>
      </form>
    </>}

    <section className={panelClass} aria-labelledby="group-accounts-heading"><h3 id="group-accounts-heading" className="font-semibold text-text">Who shares</h3>
      <p className="text-sm text-text2">Only this brand&apos;s own Facebook account (from Channels). A Page shares through its parent profile login.</p>
      {!accounts.data ? <p className="text-sm text-text2">{accounts.error ? "Accounts could not be loaded." : "Loading accounts…"}</p> : accounts.data.length === 0 ? <p className="text-sm text-text2">No Facebook accounts connected. Add one in Channels.</p> : <TableWrap label="Group sharing accounts">
        <thead><tr>{["Name", "Type", "Parent login", "Delivery", "Calibration", "Status", "Actions"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{accounts.data.map((account) => { const delivery = account.delivery; return <tr key={account.destination_id}>
          <td>{account.name}</td><td>{account.kind === "facebook_page" ? "Page" : "Profile"}</td><td>{account.parent_profile_name || "—"}</td><td>{delivery?.mode ? deliveryLabels[delivery.mode] : "Not set"}</td>
          <td>{delivery?.mode === "phone_auto" ? delivery.calibrated_at ? "Calibrated" : "Not calibrated" : "—"}</td>
          <td>{delivery?.paused_until && new Date(delivery.paused_until).getTime() > Date.now() ? `Paused until ${new Date(delivery.paused_until).toLocaleString()}` : delivery?.is_active ? "Active" : "Off"}</td>
          <td><button type="button" className={`${buttonClass} whitespace-nowrap`} aria-label={`Edit delivery for ${account.name}`} aria-expanded={editing?.destination_id === account.destination_id} onClick={() => setEditing(account)}>Edit delivery</button></td>
        </tr>; })}</tbody>
      </TableWrap>}
      {editing && <DeliveryEditor key={editing.destination_id} fanpageId={fanpageId} account={editing} onSaved={async () => { await Promise.all([accounts.mutate(), summary.mutate()]); }} onClose={() => setEditing(null)} />}
    </section>

    <form onSubmit={saveMemberships} className={panelClass} aria-labelledby="group-membership-heading"><h3 id="group-membership-heading" className="font-semibold text-text">Groups &amp; membership</h3>
      {accounts.data?.length === 0 ? <p className="text-sm text-text2">Add a Facebook account in Channels first.</p> : groups.isLoading || memberships.isLoading ? <p className="text-sm text-text2">Loading memberships…</p> : !groups.data?.length ? <p className="text-sm text-text2">No active groups for the selected niches.</p> : accounts.data?.map((account) => <fieldset disabled={membershipSaving || !memberships.data} key={account.destination_id} className="min-w-0 space-y-3"><legend className="mb-2 font-medium text-text">{account.name}</legend>
        {groups.data?.map((group) => {
          const key = `${account.destination_id}-${group.id}`;
          const membership = membershipDraft[key] ?? memberships.data?.find((item) => item.destination_id === account.destination_id && item.group_id === group.id);
          return <div key={group.id} className="grid min-w-0 grid-cols-1 gap-2 rounded-xl border border-line p-3 sm:grid-cols-2 sm:items-center">
            <a className="inline-flex min-h-[44px] items-center break-words text-pri-text" href={group.url} target="_blank" rel="noopener noreferrer">{group.name}</a>
            <Field label={`Membership · ${account.name} · ${group.name}`}><select className={inputClass} value={membership?.status ?? "unknown"} onChange={(e) => { setMembershipDraft((prev) => ({ ...prev, [key]: { destination_id: account.destination_id, group_id: group.id, status: e.target.value as GroupShareMembershipStatus } })); setMembershipMessage(""); }}>
              {membershipOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select></Field>
          </div>;
        })}
      </fieldset>)}
      {membershipError && <p role="alert" className="text-sm text-red-500">{membershipError}</p>}
      <div className="flex flex-wrap items-center gap-3"><button className={primaryClass} disabled={membershipSaving || Object.keys(membershipDraft).length === 0 || !memberships.data} type="submit">{membershipSaving ? "Saving…" : "Save memberships"}</button><p role="status" className="text-sm text-pri-text">{membershipMessage}</p></div>
    </form>

    <section className={panelClass} aria-labelledby="group-queue-heading"><h3 id="group-queue-heading" className="font-semibold text-text">Queue</h3>
      {!tasks.data ? <p className="text-sm text-text2">{tasks.error ? "Queue could not be loaded." : "Loading queue…"}</p> : tasks.data.length === 0 ? <p className="text-sm text-text2">No group posts yet.</p> : <TableWrap label="Group post queue">
        <thead><tr>{["Time", "Group", "Account", "Delivery", "State", "Post"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{tasks.data.map((task) => <tr key={task.id}><td className="whitespace-nowrap">{task.scheduled_for ? new Date(task.scheduled_for).toLocaleString() : "—"}</td>
          <td><a className="inline-flex min-h-[44px] items-center text-pri-text" href={task.group_url} target="_blank" rel="noopener noreferrer">{task.group_name}</a></td><td>{task.destination_name}</td><td>{task.delivery_mode ? deliveryLabels[task.delivery_mode] : "Not set"}</td>
          <td><span className="whitespace-nowrap rounded-full bg-surface2 px-2 py-1 text-xs font-medium">{task.state}</span>{task.error && <p className="mt-2 max-w-xs text-xs text-red-500">{task.error}</p>}</td>
          <td>{task.post_url ? <a className="inline-flex min-h-[44px] items-center whitespace-nowrap text-pri-text" href={task.post_url} target="_blank" rel="noopener noreferrer">View post</a> : "—"}</td>
        </tr>)}</tbody>
      </TableWrap>}
    </section>
  </div>;
}
