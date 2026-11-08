"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";
import { listPhoneDevices, updateGroupShareAccount } from "@/lib/api";
import type { GroupShareAccount, GroupShareDeliveryIn } from "@/lib/types";
import { Field, NumberField, Toggle, inputClass, panelClass, buttonClass, primaryClass, errorMessage } from "./Fields";

export function DeliveryEditor({ fanpageId, account, onSaved, onClose }: {
  fanpageId: number; account: GroupShareAccount; onSaved: () => Promise<unknown>; onClose: () => void;
}) {
  const delivery = account.delivery;
  const [form, setForm] = useState<GroupShareDeliveryIn>({
    mode: delivery?.mode ?? null,
    phone_device_id: delivery?.phone_device_id ?? null,
    pic_name: delivery?.pic_name ?? null,
    pic_contact: delivery?.pic_contact ?? null,
    task_hours_start: delivery?.task_hours_start ?? "08:00",
    task_hours_end: delivery?.task_hours_end ?? "21:00",
    start_per_day: delivery?.start_per_day ?? null,
    weekly_increase: delivery?.weekly_increase ?? null,
    max_per_day: delivery?.max_per_day ?? null,
    is_active: delivery?.is_active ?? false,
  });
  const { data: devices, error: devicesError } = useSWR("phone-devices-group-share", () => listPhoneDevices().then((r) => r.data));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = <K extends keyof GroupShareDeliveryIn>(key: K, value: GroupShareDeliveryIn[K]) => setForm((prev) => ({ ...prev, [key]: value }));

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await updateGroupShareAccount(fanpageId, account.destination_id, {
        ...form,
        phone_device_id: form.mode === "phone_auto" ? form.phone_device_id : null,
        pic_name: form.mode === "phone_auto" ? null : form.pic_name?.trim() || null,
        pic_contact: form.mode === "phone_auto" ? null : form.pic_contact?.trim() || null,
      });
      await onSaved();
      onClose();
    } catch (err) { setError(errorMessage(err)); }
    finally { setSaving(false); }
  }

  return <form onSubmit={save} className={panelClass} aria-label={`Edit delivery for ${account.name}`}>
    <h3 className="text-base font-semibold text-text">Edit delivery</h3>
    <p className="break-words text-sm text-text2">{account.name} · {account.kind === "facebook_page" ? `Facebook Page · parent profile ${account.parent_profile_name || "—"}` : "Facebook profile"}</p>
    <fieldset disabled={saving} className="min-w-0 space-y-4">
      <fieldset className="space-y-1"><legend className="font-medium text-text">Who posts</legend>
        {([['phone_auto', 'Phone (automatic)'], ['pic_whatsapp', 'PIC · WhatsApp'], ['pic_telegram', 'PIC · Telegram']] as const).map(([mode, label]) =>
          <label key={mode} className="flex min-h-[44px] items-center gap-3 text-sm text-text"><input type="radio" name={`delivery-${account.destination_id}`} checked={form.mode === mode} onChange={() => set("mode", mode)} required />{label}</label>)}
      </fieldset>
      {form.mode === "phone_auto" && <>
        <Field label="Phone device"><select className={inputClass} required value={form.phone_device_id ?? ""} onChange={(e) => set("phone_device_id", e.target.value ? Number(e.target.value) : null)}>
          <option value="">Select a phone</option>
          {devices?.map((device) => <option key={device.id} value={device.id}>{device.name} (#{device.id})</option>)}
          {form.phone_device_id && !devices?.some((device) => device.id === form.phone_device_id) && <option value={form.phone_device_id}>Device #{form.phone_device_id}</option>}
        </select></Field>
        {devicesError && <p role="alert" className="text-sm text-red-500">{errorMessage(devicesError)}</p>}
        <div className="space-y-2"><h4 className="font-medium text-text">Phone calibration</h4>
          {delivery?.calibration && Object.keys(delivery.calibration).length ? <ul className="space-y-2 text-sm text-text2">{Object.entries(delivery.calibration).map(([step, result]) =>
            <li key={step}><span aria-label={result.ok ? "Passed" : "Failed"}>{result.ok ? "✓" : "✗"}</span> {step}: {result.note}</li>)}</ul> : <p className="text-sm text-text2">Not calibrated</p>}
          <p className="text-sm text-text2">Group posting stays off until calibration passes.</p>
        </div>
      </>}
      {(form.mode === "pic_whatsapp" || form.mode === "pic_telegram") && <>
        <Field label="PIC name"><input required className={inputClass} value={form.pic_name ?? ""} onChange={(e) => set("pic_name", e.target.value)} /></Field>
        <Field label={form.mode === "pic_whatsapp" ? "WhatsApp number" : "Telegram username or chat id"}><input required type={form.mode === "pic_whatsapp" ? "tel" : "text"} className={inputClass} value={form.pic_contact ?? ""} onChange={(e) => set("pic_contact", e.target.value)} /></Field>
        <fieldset><legend className="mb-2 text-sm font-medium text-text">Send tasks between</legend><div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Start time"><input required type="time" className={inputClass} value={form.task_hours_start?.slice(0, 5) ?? ""} onChange={(e) => set("task_hours_start", e.target.value)} /></Field>
          <Field label="End time"><input required type="time" className={inputClass} value={form.task_hours_end?.slice(0, 5) ?? ""} onChange={(e) => set("task_hours_end", e.target.value)} /></Field>
        </div></fieldset>
        {form.mode === "pic_telegram" && <p className="text-sm text-text2">The PIC must open the Studio bot and press Start once.</p>}
      </>}
      <fieldset><legend className="mb-2 font-medium text-text">Limits (empty = brand caps)</legend><div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <NumberField label="Start per day" nullable value={form.start_per_day} onChange={(v) => set("start_per_day", v)} />
        <NumberField label="Weekly increase" nullable value={form.weekly_increase} onChange={(v) => set("weekly_increase", v)} />
        <NumberField label="Maximum per day" nullable value={form.max_per_day} onChange={(v) => set("max_per_day", v)} />
      </div></fieldset>
      <Toggle switchRole label="Active" checked={form.is_active} onChange={(v) => set("is_active", v)} />
    </fieldset>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    <div className="flex flex-wrap gap-3"><button disabled={saving} className={primaryClass} type="submit">{saving ? "Saving…" : "Save delivery"}</button><button disabled={saving} type="button" className={buttonClass} onClick={onClose}>Cancel</button></div>
  </form>;
}
