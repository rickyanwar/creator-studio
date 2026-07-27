"use client";

import { Icon } from "@iconify/react";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  message?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onCancel}>
      <div className="card w-full max-w-sm p-6 space-y-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3">
          <div
            className={`shrink-0 w-10 h-10 rounded-full flex items-center justify-center ${
              danger ? "bg-error-main/10 text-error-main" : "bg-primary-main/10 text-primary-main"
            }`}
          >
            <Icon icon={danger ? "solar:trash-bin-trash-bold-duotone" : "solar:question-circle-bold-duotone"} width={20} />
          </div>
          <div className="flex-1 pt-1">
            <h3 className="text-sm font-bold text-ink">{title}</h3>
            {message && <p className="text-xs text-ink-48 mt-1.5 leading-relaxed">{message}</p>}
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <button onClick={onCancel} disabled={loading} className="btn btn-secondary text-xs">
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className={`btn text-xs flex items-center gap-1.5 ${danger ? "btn-error" : "btn-primary"}`}
          >
            {loading && <Icon icon="svg-spinners:ring-resize" width={14} />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
