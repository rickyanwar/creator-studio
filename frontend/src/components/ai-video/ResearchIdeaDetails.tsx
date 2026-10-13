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
        <div className="bg-bg-paper p-3 rounded border border-hairline space-y-1">
          {idea.title && <p className="font-semibold text-text-primary">{idea.title}</p>}
          {idea.caption && <p className="whitespace-pre-wrap text-text-secondary">{idea.caption}</p>}
        </div>
      )}

      {(evidence.references ?? []).length > 0 && (
        <div>
          <p className="font-semibold text-text-primary mb-1">References</p>
          <ul className="space-y-1">
            {(evidence.references ?? []).map((ref, i) => (
              <li key={i} className="text-text-secondary break-all">
                <a href={ref.url} target="_blank" rel="noreferrer" className="text-primary-main underline">
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

      <button type="button" onClick={() => setOpen(!open)} className="text-primary-main font-semibold">
        {open ? "Hide research and feedback" : "Show research, slots and feedback"}
      </button>

      {open && (
        <div className="space-y-3">
          {idea.research_notes && (
            <div>
              <p className="font-semibold text-text-primary mb-1">Research notes</p>
              <p className="whitespace-pre-wrap bg-bg-paper p-3 rounded border border-hairline">{idea.research_notes}</p>
            </div>
          )}
          {(evidence.sources ?? []).length > 0 && (
            <div>
              <p className="font-semibold text-text-primary mb-1">Sources</p>
              <ul className="space-y-1">
                {(evidence.sources ?? []).map((src) => (
                  <li key={src.id} className="text-text-secondary break-all">
                    [{src.id}]{" "}
                    <a href={src.url} target="_blank" rel="noreferrer" className="text-primary-main underline">
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
              <p className="font-semibold text-text-primary mb-1">Slots</p>
              <dl className="bg-bg-paper p-3 rounded border border-hairline space-y-1">
                {Object.entries(idea.variables).map(([key, value]) => (
                  <div key={key}>
                    <dt className="text-[10px] uppercase text-text-secondary">{key}</dt>
                    <dd className="text-text-primary whitespace-pre-wrap">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          <div>
            <p className="font-semibold text-text-primary mb-1">Feedback to Muse</p>
            {thread.length === 0 && <p className="text-text-secondary">No feedback yet.</p>}
            <ul className="space-y-1">
              {thread.map((item) => (
                <li
                  key={item.id}
                  className={`p-2 rounded ${item.author === "muse" ? "bg-indigo-50" : "bg-divider-soft"}`}
                >
                  <span className="text-[10px] uppercase font-bold text-text-secondary mr-1">
                    {item.author === "muse" ? "Muse" : item.username ?? "Studio"} · {item.kind}
                  </span>
                  <span className="whitespace-pre-wrap">{item.message}</span>
                </li>
              ))}
            </ul>
            {error && <p className="text-error-main mt-1">{error}</p>}
            <div className="flex gap-2 mt-2">
              <textarea
                className="input-rect w-full text-xs min-h-[60px]"
                placeholder="What should Muse fix or keep doing? Muse reads this on its next run."
                value={message}
                maxLength={2000}
                onChange={(e) => setMessage(e.target.value)}
              />
              <button type="button" disabled={sending || !message.trim()} onClick={send} className="btn-primary text-xs py-1.5 h-fit">
                Send
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
