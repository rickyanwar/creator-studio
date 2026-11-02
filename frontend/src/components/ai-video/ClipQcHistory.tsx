"use client";

import { useState } from "react";
import { getAiVideoClipQc } from "@/lib/api";
import type { ClipQcAttempt, ClipQcClip, ClipQcHistory } from "@/lib/types";

const OUTCOME_CONFIG: Record<ClipQcClip["outcome"], { label: string; className: string }> = {
  passed: { label: "Passed", className: "bg-pri-soft text-pri-text" },
  kept_best: { label: "Kept best attempt", className: "bg-warn-soft text-warn" },
  failed: { label: "QC unavailable", className: "bg-bad-soft text-bad" },
  pending: { label: "Checking", className: "bg-surface2 text-text2" },
  discarded: { label: "Discarded by restart", className: "bg-surface2 text-text2" },
};

function getVerdictInfo(verdict: ClipQcAttempt["verdict"]) {
  switch (verdict) {
    case "pass":
      return { label: "Passed", className: "text-pri-text" };
    case "retry":
      return { label: "Rejected", className: "text-bad" };
    case "use_attempt":
      return { label: "Rejected (limit reached)", className: "text-bad" };
    case "failed":
      return { label: "QC unavailable", className: "text-text2" };
    case null:
    default:
      return { label: "Checking", className: "text-text2" };
  }
}

export default function ClipQcHistorySection({ jobId }: { jobId: number }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ClipQcHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    if (!open && !data) {
      setLoading(true);
      setError(null);
      try {
        const res = await getAiVideoClipQc(jobId);
        setData(res.data);
        setOpen(true);
      } catch (err: any) {
        setError(err?.response?.data?.detail || "Failed to load QC history");
      } finally {
        setLoading(false);
      }
    } else {
      setOpen(!open);
    }
  };

  return (
    <div className="pt-1 min-w-0">
      <button
        type="button"
        onClick={toggle}
        disabled={loading}
        className="text-[11px] font-medium text-text2 hover:text-text cursor-pointer inline-flex items-center gap-1"
      >
        <span>{open ? "Hide QC history" : "Show QC history"}</span>
        {loading && <span className="text-[10px] text-text2 font-normal">loading…</span>}
      </button>
      {error && <p className="text-[11px] text-bad mt-1">{error}</p>}
      {open && data && (
        <div className="mt-1 min-w-0">
          {data.clips.length === 0 ? (
            <p className="text-[11px] text-text2 mt-1">No clip QC for this video.</p>
          ) : (
            data.clips.map((clip) => {
              const outcome = OUTCOME_CONFIG[clip.outcome] || {
                label: clip.outcome,
                className: "bg-surface2 text-text2",
              };
              return (
                <div
                  key={`${clip.generation}-${clip.clip_index}`}
                  className="border border-line rounded-lg p-2 mt-2 min-w-0"
                >
                  <div className="flex items-center justify-between gap-2 text-[12px] font-semibold text-text">
                    <span>
                      Clip {clip.clip_index}
                      {!clip.current_generation && ` · generation ${clip.generation}`}
                    </span>
                    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${outcome.className}`}>
                      {outcome.label}
                    </span>
                  </div>

                  <div className="flex gap-2 overflow-x-auto pb-1 mt-1.5">
                    {clip.attempts.map((att) => {
                      const verdict = getVerdictInfo(att.verdict);
                      return (
                        <div
                          key={att.id ?? att.attempt}
                          className={`w-56 shrink-0 rounded-lg border p-1.5 bg-surface ${
                            att.used_in_video ? "border-pri ring-1 ring-pri" : "border-line"
                          }`}
                        >
                          <div className="flex items-center justify-between gap-1 text-[11px] font-semibold">
                            <span className="text-text">Attempt {att.attempt}</span>
                            <span className={verdict.className}>{verdict.label}</span>
                          </div>

                          {att.used_in_video && (
                            <div className="mt-1">
                              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-pri text-pri-fg inline-block">
                                Used in video
                              </span>
                            </div>
                          )}

                          {att.clip_url ? (
                            <video
                              src={att.clip_url}
                              controls
                              preload="none"
                              playsInline
                              className="w-full aspect-[9/16] rounded bg-black object-contain mt-1"
                            />
                          ) : (
                            <div className="w-full aspect-[9/16] rounded bg-black flex items-center justify-center p-2 text-center text-[10px] text-text2 mt-1">
                              Clip file no longer kept
                            </div>
                          )}

                          {att.sheet_url && (
                            <div className="mt-1">
                              <a href={att.sheet_url} target="_blank" rel="noreferrer" className="block">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                  src={att.sheet_url}
                                  alt={`Frames QC checked, attempt ${att.attempt}`}
                                  className="w-full rounded border border-line"
                                  loading="lazy"
                                />
                              </a>
                              <p className="text-[10px] text-text2 mt-0.5">
                                Frames the QC checked (open full size)
                              </p>
                            </div>
                          )}

                          {att.issues.length === 0 && att.verdict === "pass" ? (
                            <p className="text-[11px] text-text2 mt-1">No issues found</p>
                          ) : att.issues.length > 0 ? (
                            <ul className="mt-1 space-y-0.5">
                              {att.issues.map((issue, idx) => (
                                <li key={idx} className="text-[11px] leading-snug">
                                  <span
                                    className={
                                      issue.severity === "major" ? "text-bad font-semibold" : "text-warn"
                                    }
                                  >
                                    {issue.severity}
                                  </span>{" "}
                                  <span className="text-text">{issue.problem}</span>
                                </li>
                              ))}
                            </ul>
                          ) : null}

                          {att.waiting_reason && (
                            <p className="text-[10px] text-text2 mt-1">{att.waiting_reason}</p>
                          )}

                          <details className="mt-1">
                            <summary className="text-[10px] text-text2 cursor-pointer">Prompt</summary>
                            <pre className="whitespace-pre-wrap text-[10px] text-text2 max-h-40 overflow-y-auto mt-0.5">
                              {att.prompt}
                            </pre>
                          </details>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

export { ClipQcHistorySection };
