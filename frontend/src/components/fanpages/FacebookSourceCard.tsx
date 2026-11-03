"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Icon } from "@iconify/react";
import { updateFacebookPhotoSource } from "@/lib/api";
import type { FacebookPhotoSourceRef } from "@/lib/types";

export function FacebookSourceCard({ source, fanpageId, onToggle, onRemove, onSaved }: {
  source: FacebookPhotoSourceRef;
  fanpageId: number;
  onToggle: () => Promise<void>;
  onRemove: () => Promise<void>;
  onSaved: (source: FacebookPhotoSourceRef) => void;
}) {
  const [localFormat, setLocalFormat] = useState(source.format ?? "follow");
  const [formatSaving, setFormatSaving] = useState(false);
  const [editPrompt, setEditPrompt] = useState(source.chatgpt_edit_prompt ?? "");
  const [promptSaving, setPromptSaving] = useState(false);
  const [actionSaving, setActionSaving] = useState(false);
  const promptDraft = useRef(editPrompt);
  const savedPrompt = useRef(editPrompt.trim());
  const promptInFlight = useRef(false);
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;

  const saveEditPrompt = useCallback(async function savePrompt() {
    const value = promptDraft.current.trim();
    if (promptInFlight.current || value === savedPrompt.current) return;
    promptInFlight.current = true;
    setPromptSaving(true);
    try {
      const response = await updateFacebookPhotoSource(fanpageId, source.id, { chatgpt_edit_prompt: value || null });
      savedPrompt.current = response.data.chatgpt_edit_prompt ?? "";
      onSavedRef.current(response.data);
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to save ChatGPT edit prompt");
    } finally {
      promptInFlight.current = false;
      setPromptSaving(false);
      if (promptDraft.current.trim() !== value) void savePrompt();
    }
  }, [fanpageId, source.id]);

  useEffect(() => {
    const timer = setTimeout(() => void saveEditPrompt(), 1200);
    return () => clearTimeout(timer);
  }, [editPrompt, saveEditPrompt]);

  useEffect(() => {
    setLocalFormat(source.format ?? "follow");
  }, [source.format]);

  async function handleFormatChange(value: FacebookPhotoSourceRef["format"]) {
    const previous = localFormat;
    setLocalFormat(value);
    setFormatSaving(true);
    try {
      const response = await updateFacebookPhotoSource(fanpageId, source.id, { format: value });
      onSavedRef.current(response.data);
    } catch (err: any) {
      setLocalFormat(previous);
      alert(err.response?.data?.detail || "Failed to update format");
    } finally {
      setFormatSaving(false);
    }
  }

  async function handleAction(action: () => Promise<void>) {
    setActionSaving(true);
    try {
      await action();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to update Facebook source");
    } finally {
      setActionSaving(false);
    }
  }

  const id = `facebook-${fanpageId}-${source.id}`;
  const minutes = source.last_fetched_at
    ? Math.max(0, Math.floor((Date.now() - new Date(source.last_fetched_at).getTime()) / 60000))
    : null;
  const lastChecked = minutes === null || !Number.isFinite(minutes) ? "never"
    : minutes < 1 ? "just now"
    : minutes < 60 ? `${minutes}m ago`
    : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago`
    : `${Math.floor(minutes / 1440)}d ago`;

  return (
    <div className="group relative rounded-lg border border-line bg-surface2 p-4 space-y-3 transition-colors hover:border-pri/30">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-full bg-surface flex items-center justify-center shrink-0">
            <Icon icon="logos:facebook" width={24} aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <a href={/^https?:\/\//i.test(source.page_url) ? source.page_url : `https://www.facebook.com/${source.page_url}`} target="_blank" rel="noopener noreferrer"
              className={`block text-sm font-semibold leading-tight truncate hover:underline ${source.is_active ? "text-text" : "text-text2 line-through"}`}>
              {source.label || source.page_url}
            </a>
            <p className="text-[11px] text-text2 leading-tight">Facebook source</p>
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <input type="checkbox" checked={source.is_active} disabled={actionSaving}
            onChange={() => void handleAction(onToggle)}
            aria-label={source.is_active ? "Pause Facebook source" : "Activate Facebook source"}
            title={source.is_active ? "Active — click to pause" : "Paused — click to activate"}
            className="w-4 h-4 rounded accent-pri disabled:opacity-50" />
          <button type="button" onClick={() => void handleAction(onRemove)} disabled={actionSaving}
            className="transition-colors text-text2 hover:text-bad p-1.5 rounded-md hover:bg-bad-soft disabled:opacity-50"
            title="Delete source" aria-label={`Delete ${source.label || source.page_url}`}>
            <Icon icon="solar:trash-bin-trash-bold-duotone" width={15} aria-hidden="true" />
          </button>
        </div>
      </div>
      <div className="border-t border-line" />
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <Icon icon="solar:magic-stick-3-bold-duotone" width={13} className="text-text2" aria-hidden="true" />
          <label htmlFor={`${id}-format`} className="text-[11px] font-medium text-text2 uppercase tracking-wide">Format</label>
          {formatSaving && <Icon icon="svg-spinners:ring-resize" width={11} className="text-pri" aria-label="Saving format" role="status" />}
        </div>
        <select id={`${id}-format`} value={localFormat} disabled={formatSaving}
          onChange={(e) => void handleFormatChange(e.target.value as FacebookPhotoSourceRef["format"])}
          aria-describedby={`${id}-format-help`}
          className="w-full text-xs rounded-md bg-surface border border-line text-text px-2.5 py-1.5 focus:outline-none focus:border-pri disabled:opacity-50">
          <option value="follow">Follow brand default (template)</option>
          <option value="repost">Repost as-is</option>
          <option value="template">Recreate with template</option>
          <option value="chatgpt">Recreate with ChatGPT edit</option>
        </select>
        <p id={`${id}-format-help`} className="text-[10px] text-text2">
          Repost = the original Facebook photo + a new AI caption. Template = text from the photo rebuilt on your template. ChatGPT = AI design, or edit the original photo with your prompt.
        </p>
        {localFormat === "chatgpt" && (
          <div className="space-y-1.5">
            <div className="flex items-center gap-1.5">
              <label htmlFor={`${id}-prompt`} className="text-[11px] font-medium text-text2">ChatGPT edit prompt</label>
              {promptSaving && <Icon icon="svg-spinners:ring-resize" width={11} className="text-pri" aria-label="Saving prompt" role="status" />}
            </div>
            <textarea id={`${id}-prompt`} rows={4} maxLength={2000} value={editPrompt}
              onChange={(e) => {
                promptDraft.current = e.target.value;
                setEditPrompt(e.target.value);
              }}
              onBlur={() => void saveEditPrompt()}
              aria-describedby={`${id}-prompt-help ${id}-prompt-count`}
              placeholder="Optional. ChatGPT edits the original Facebook photo with this prompt, e.g. remove the logo watermark, cinematic colour. Leave empty to keep the ChatGPT design."
              className="w-full text-xs rounded-md bg-surface border border-line text-text px-2.5 py-1.5 focus:outline-none focus:border-pri" />
            <p id={`${id}-prompt-count`} className="text-[10px] text-text2 text-right">{editPrompt.length}/2000</p>
            <p id={`${id}-prompt-help`} className="text-[10px] text-text2">
              With a prompt: the edited photo is posted as is (no template text). If the edit fails, the original photo is posted.
            </p>
          </div>
        )}
      </div>
      <p className="text-[10px] text-text2">Captions are written by AI from the text in the photo (Facebook hides post captions without login).</p>
      <p className="text-[10px] text-text2">Last checked: {lastChecked}</p>
    </div>
  );
}
