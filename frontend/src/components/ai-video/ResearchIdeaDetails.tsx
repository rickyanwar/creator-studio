"use client";

import { useState } from "react";
import useSWR from "swr";
import { addIdeaFeedback, listIdeaFeedback } from "@/lib/api";
import type { AiVideoIdea, ResearchFeedbackItem } from "@/lib/types";

type Source = { id: string; url: string; publisher?: string; title?: string; published_at?: string | null };
type Reference = {
  url: string;
  platform?: string;
  kind?: string;
  views?: number | null;
  views_approx?: boolean | null;
  likes?: number | null;
  comments?: number | null;
  why?: string | null;
};

function count(value: number | null | undefined, approx?: boolean | null) {
  if (value === null || value === undefined) return null;
  return `${approx ? "~" : ""}${value.toLocaleString()}`;
}

/** What the research desk (Muse) sent for this idea, plus the feedback thread both ways. */
export function ResearchIdeaDetails({ idea }: { idea: AiVideoIdea }) {
  const evidence = (idea.evidence ?? {}) as { sources?: Source[]; references?: Reference[] };
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: thread = [], mutate } = useSWR(open ? ["idea-feedback", idea.id] : null, () =>
    listIdeaFeedback(idea.id).then((r) => r.data as ResearchFeedbackItem[])
  );

  async function send() {
    if (!message.trim()) return;
    setSending(true);
    setError(null);
    try {
      await addIdeaFeedback(idea.id, message.trim());
      setMessage("");
      mutate();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Failed to send feedback");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-2 text-xs">
      {(idea.title || idea.caption) && (
        <div className="bg-surface2 p-3 rounded-lg border border-line space-y-1">
          {idea.title && <p className="font-semibold text-text">{idea.title}</p>}
          {idea.caption && <p className="whitespace-pre-wrap text-text2">{idea.caption}</p>}
        </div>
      )}

      {(evidence.references ?? []).length > 0 && (
        <div>
          <p className="font-semibold text-text mb-1">References</p>
          <ul className="space-y-1">
            {(evidence.references ?? []).map((ref, i) => (
              <li key={i} className="text-text2 break-all">
                <a href={ref.url} target="_blank" rel="noreferrer" className="text-pri-text underline">
                  {ref.platform ?? "link"}
                </a>{" "}
                {[
                  count(ref.views, ref.views_approx) && `${count(ref.views, ref.views_approx)} views`,
                  count(ref.likes) && `${count(ref.likes)} likes`,
                  count(ref.comments) && `${count(ref.comments)} comments`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                {ref.why ? ` — ${ref.why}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-pri-text font-semibold hover:underline"
      >
        {open ? "Hide research and feedback" : "Show research, slots and feedback"}
      </button>

      {open && (
        <div className="space-y-3 pt-1">
          {idea.research_notes && (
            <div>
              <p className="font-semibold text-text mb-1">Research notes</p>
              <p className="whitespace-pre-wrap bg-surface2 p-3 rounded-lg border border-line text-text2">
                {idea.research_notes}
              </p>
            </div>
          )}
          {(evidence.sources ?? []).length > 0 && (
            <div>
              <p className="font-semibold text-text mb-1">Sources</p>
              <ul className="space-y-1">
                {(evidence.sources ?? []).map((src) => (
                  <li key={src.id} className="text-text2 break-all">
                    [{src.id}]{" "}
                    <a href={src.url} target="_blank" rel="noreferrer" className="text-pri-text underline">
                      {src.publisher ?? src.url}
                    </a>
                    {src.title ? ` — ${src.title}` : ""}
                    {src.published_at ? ` (${src.published_at})` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {idea.variables && (
            <div>
              <p className="font-semibold text-text mb-1">Slots</p>
              <dl className="bg-surface2 p-3 rounded-lg border border-line space-y-1">
                {Object.entries(idea.variables).map(([key, value]) => (
                  <div key={key}>
                    <dt className="text-[10px] uppercase font-semibold text-text2">{key}</dt>
                    <dd className="text-text whitespace-pre-wrap">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          <div className="pt-1">
            <p className="font-semibold text-text mb-1">Feedback to Muse</p>
            {thread.length === 0 && <p className="text-text2">No feedback yet.</p>}
            <ul className="space-y-1.5 mb-2">
              {thread.map((item) => (
                <li
                  key={item.id}
                  className={`p-2 rounded-lg border border-line ${
                    item.author === "muse" ? "bg-info-soft" : "bg-surface2"
                  }`}
                >
                  <span className="text-[10px] uppercase font-bold text-text2 mr-1">
                    {item.author === "muse" ? "Muse" : item.username ?? "Studio"} · {item.kind}
                  </span>
                  <span className="whitespace-pre-wrap text-text">{item.message}</span>
                </li>
              ))}
            </ul>
            {error && <p className="text-bad text-xs mb-1.5">{error}</p>}
            <div className="flex gap-2">
              <textarea
                className="w-full text-xs min-h-[60px] p-2.5 rounded-lg border border-line bg-surface text-text focus:outline-none focus:ring-1 focus:ring-pri"
                placeholder="What should Muse fix or keep doing? Muse reads this on its next run."
                value={message}
                maxLength={2000}
                onChange={(e) => setMessage(e.target.value)}
              />
              <button
                type="button"
                disabled={sending || !message.trim()}
                onClick={send}
                className="h-9 px-3 rounded-lg bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 disabled:opacity-50 self-end"
              >
                Send
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
