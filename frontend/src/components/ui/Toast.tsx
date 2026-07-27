"use client";

import { useEffect } from "react";
import { Icon } from "@iconify/react";

export type ToastData = { message: string; type?: "success" | "error" };

export default function Toast({ toast, onClose }: { toast: ToastData | null; onClose: () => void }) {
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [toast, onClose]);

  if (!toast) return null;
  const isError = toast.type === "error";

  return (
    <div className="fixed bottom-6 right-6 z-[60] max-w-sm">
      <div
        className={`flex items-start gap-2.5 rounded-lg shadow-lg px-4 py-3 text-sm ${
          isError ? "bg-error-main text-white" : "bg-ink text-white"
        }`}
      >
        <Icon
          icon={isError ? "solar:danger-triangle-bold-duotone" : "solar:check-circle-bold-duotone"}
          width={18}
          className="shrink-0 mt-0.5"
        />
        <p className="flex-1">{toast.message}</p>
        <button onClick={onClose} className="shrink-0 opacity-70 hover:opacity-100">
          <Icon icon="solar:close-circle-bold" width={16} />
        </button>
      </div>
    </div>
  );
}
