"use client";

import { useId, type ReactNode } from "react";
import { isAxiosError } from "axios";

export const inputClass = "w-full min-w-0 min-h-[44px] rounded-xl border border-line bg-surface px-3 py-2 text-sm text-text";
export const panelClass = "min-w-0 rounded-2xl border border-line bg-surface p-4 sm:p-6 space-y-4";
export const buttonClass = "btn-secondary min-h-[44px] min-w-[44px] px-4 text-sm";
export const primaryClass = "btn-primary min-h-[44px] px-4 text-sm";

export function errorMessage(error: unknown): string {
  if (isAxiosError(error)) {
    if (error.response?.status === 409) return "This group is already in the library";
    const detail: unknown = error.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((entry) => {
      const field = entry as { loc?: (string | number)[]; msg?: string };
      return `${field.loc?.filter((part) => part !== "body").join(".") || "Input"}: ${field.msg || "Invalid value"}`;
    }).join("; ");
  }
  return error instanceof Error ? error.message : "Request failed. Please try again.";
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="flex min-w-0 flex-col gap-1.5 text-sm text-text"><span className="font-medium">{label}</span>{children}</label>;
}

export function NumberField({ label, value, onChange, min = 0, max, nullable = false }: {
  label: string; value: number | null; onChange: (value: number | null) => void; min?: number; max?: number; nullable?: boolean;
}) {
  return <Field label={label}><input className={inputClass} type="number" min={min} max={max} step={1} required={!nullable}
    value={value ?? ""} placeholder={nullable ? "Brand caps" : undefined}
    onChange={(e) => onChange(Number.isNaN(e.target.valueAsNumber) ? nullable ? null : min : e.target.valueAsNumber)} /></Field>;
}

export function Toggle({ label, checked, onChange, switchRole = false }: { label: string; checked: boolean; onChange: (value: boolean) => void; switchRole?: boolean }) {
  const id = useId();
  return <label htmlFor={id} className="flex min-h-[44px] cursor-pointer items-center gap-3 text-sm text-text">
    <input id={id} type="checkbox" role={switchRole ? "switch" : undefined} checked={checked} onChange={(e) => onChange(e.target.checked)} className="h-5 w-5 accent-pri" />
    <span>{label}</span>
  </label>;
}

export function TableWrap({ label, children, tableClassName = "" }: { label: string; children: ReactNode; tableClassName?: string }) {
  return <div role="region" aria-label={label} tabIndex={0} className="min-w-0 max-w-full overflow-x-auto rounded-xl border border-line focus-visible:outline focus-visible:outline-2 focus-visible:outline-pri">
    <table className={`w-full text-left text-sm text-text [&_th]:whitespace-nowrap [&_th]:bg-surface2 [&_th]:p-3 [&_td]:p-3 [&_td]:border-t [&_td]:border-line ${tableClassName}`}>{children}</table>
  </div>;
}

export const deliveryLabels = { phone_auto: "Phone", pic_whatsapp: "PIC WhatsApp", pic_telegram: "PIC Telegram" };
