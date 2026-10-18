"use client";

export interface PendingAction {
  id: string;
  jobId: number;
  type: "publish" | "skip";
  title: string;
  remaining: number;
}

interface UndoToastStackProps {
  actions: PendingAction[];
  onUndo: (jobId: number) => void;
}

export default function UndoToastStack({ actions, onUndo }: UndoToastStackProps) {
  if (actions.length === 0) return null;

  return (
    <div
      role="region"
      aria-label="Pending actions"
      className="fixed bottom-6 inset-x-0 mx-auto z-50 flex flex-col items-center gap-2 pointer-events-none px-4"
    >
      {actions.map((action) => (
        <div
          key={action.id}
          role="status"
          aria-live="polite"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              onUndo(action.jobId);
            }
          }}
          className="pointer-events-auto flex items-center justify-between gap-3 px-4 py-2.5 rounded-xl bg-surface border border-line shadow-xl text-text text-sm focus:outline-none focus:ring-2 focus:ring-pri max-w-md w-full transition-all"
        >
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <span className="truncate font-medium">
              {action.type === "publish"
                ? `Publishing ${action.title}…`
                : `Skipped ${action.title} ·`}
            </span>
            <span className="shrink-0 text-xs font-mono font-semibold text-text2 bg-surface2 px-1.5 py-0.5 rounded-md">
              {action.remaining}s
            </span>
          </div>
          <button
            type="button"
            onClick={() => onUndo(action.jobId)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onUndo(action.jobId);
              }
            }}
            aria-label={`Undo ${action.type === "publish" ? "publishing" : "skipping"} ${action.title}`}
            className="shrink-0 text-sm font-bold text-pri hover:text-pri-text focus:outline-none focus:underline transition-colors"
          >
            Undo
          </button>
        </div>
      ))}
    </div>
  );
}
