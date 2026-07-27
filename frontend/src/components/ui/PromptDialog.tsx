"use client";

import { useEffect, useRef } from "react";
import { Icon } from "@iconify/react";

type PromptDialogProps = {
  open: boolean;
  title: string;
  message?: React.ReactNode;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
};

export default function PromptDialog({
  open,
  title,
  message,
  placeholder,
  value,
  onChange,
  confirmLabel = "Continue",
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
}: PromptDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onCancel}>
      <div className="card w-full max-w-sm p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div>
          <h3 className="text-sm font-bold text-ink">{title}</h3>
          {message && <p className="text-xs text-ink-48 mt-1 leading-relaxed">{message}</p>}
        </div>

        <input
          ref={inputRef}
          className="input-rect py-2 text-sm"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && value.trim()) onConfirm();
            else if (e.key === "Escape") onCancel();
          }}
        />

        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="btn btn-secondary text-xs">
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={!value.trim()}
            className="btn btn-primary text-xs flex items-center gap-1.5"
          >
            <Icon icon="solar:arrow-right-bold" width={14} />
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
