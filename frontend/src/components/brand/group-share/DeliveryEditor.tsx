"use client";

import { useRef, useState, type FormEvent } from "react";
import useSWR from "swr";
import { listPhoneDevices, updateGroupShareAccount } from "@/lib/api";
import type { GroupShareAccount, GroupShareDelivery, GroupShareDeliveryIn } from "@/lib/types";
import { Field, NumberField, Toggle, inputClass, panelClass, buttonClass, primaryClass, errorMessage } from "./Fields";
import { CalibrationPanel } from "./CalibrationPanel";

function savedInput(delivery: GroupShareDelivery): GroupShareDeliveryIn {
  const { mode, phone_device_id, pic_name, pic_contact, task_hours_start, task_hours_end, start_per_day, weekly_increase,
    max_per_day, is_active } = delivery;
  return { mode, phone_device_id, pic_name, pic_contact, task_hours_start, task_hours_end, start_per_day, weekly_increase,
    max_per_day, is_active };
}

export function DeliveryEditor({ fanpageId, account, onSaved, onClose }: {
  fanpageId: number; account: GroupShareAccount; onSaved: () => Promise<unknown>; onClose: () => void;
}) {
  const [delivery, setDelivery] = useState(account.delivery);
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
  const [calibrating, setCalibrating] = useState(false);
  const [error, setError] = useState("");
  // Set when calibration had to save the delivery inactive; Active is switched back on after a passing calibration.
  const restoreActive = useRef(false);
  const deviceMatches = delivery?.mode === "phone_auto" && delivery.phone_device_id === form.phone_device_id;
  const calibrated = deviceMatches && !!delivery?.calibrated_at;
  const set = <K extends keyof GroupShareDeliveryIn>(key: K, value: GroupShareDeliveryIn[K]) => setForm((prev) => ({ ...prev, [key]: value }));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (form.mode === "phone_auto" && form.is_active && !calibrated) {
      setError("Calibrate first.");
      return;
    }
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
      <fieldset disabled={calibrating} className="space-y-1"><legend className="font-medium text-text">Who posts</legend>
        {([['phone_auto', 'Phone (automatic)'], ['pic_whatsapp', 'PIC · WhatsApp'], ['pic_telegram', 'PIC · Telegram']] as const).map(([mode, label]) =>
          <label key={mode} className="flex min-h-[44px] items-center gap-3 text-sm text-text"><input type="radio" name={`delivery-${account.destination_id}`} checked={form.mode === mode} onChange={() => { set("mode", mode); set("is_active", false); }} required />{label}</label>)}
      </fieldset>
      {form.mode === "phone_auto" && <>
        <Field label="Phone device"><select disabled={calibrating} className={inputClass} required value={form.phone_device_id ?? ""} onChange={(e) => { set("phone_device_id", e.target.value ? Number(e.target.value) : null); set("is_active", false); }}>
          <option value="">Select a phone</option>
          {devices?.map((device) => <option key={device.id} value={device.id}>{device.name} (#{device.id})</option>)}
          {form.phone_device_id && !devices?.some((device) => device.id === form.phone_device_id) && <option value={form.phone_device_id}>Device #{form.phone_device_id}</option>}
        </select></Field>
        {devicesError && <p role="alert" className="text-sm text-red-500">{errorMessage(devicesError)}</p>}
        <CalibrationPanel fanpageId={fanpageId} destinationId={account.destination_id} delivery={delivery} deviceMatches={deviceMatches}
          onDelivery={setDelivery} onBusy={setCalibrating} prepare={async () => {
            if (!form.phone_device_id) throw new Error("Select a phone first.");
            if (deviceMatches) {
              // Mode and phone are already saved: calibrate as is, keep Active and leave unsaved edits alone.
              restoreActive.current = false;
              return delivery;
            }
            restoreActive.current = !!delivery?.is_active;
            const saved = await updateGroupShareAccount(fanpageId, account.destination_id, {
              ...form, mode: "phone_auto", pic_name: null, pic_contact: null, is_active: false,
            });
            setDelivery(saved.delivery);
            set("is_active", false);
            await onSaved();
            return saved.delivery;
          }} onPassed={async (passed) => {
            if (!restoreActive.current) return;
            restoreActive.current = false;
            const saved = await updateGroupShareAccount(fanpageId, account.destination_id, { ...savedInput(passed), is_active: true });
            setDelivery(saved.delivery);
            set("is_active", true);
            await onSaved();
          }} />
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
      <fieldset disabled={calibrating || (form.mode === "phone_auto" && !calibrated)}>
        <Toggle switchRole label="Active" checked={form.is_active && (form.mode !== "phone_auto" || calibrated)} onChange={(v) => set("is_active", v)} />
      </fieldset>
      {form.mode === "phone_auto" && !calibrated && <p className="text-sm text-text2">Group posting stays off until calibration passes.</p>}
    </fieldset>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    <div className="flex flex-wrap gap-3"><button disabled={saving || calibrating} className={primaryClass} type="submit">{saving ? "Saving…" : "Save delivery"}</button><button disabled={saving} type="button" className={buttonClass} onClick={onClose}>Cancel</button></div>
  </form>;
}
