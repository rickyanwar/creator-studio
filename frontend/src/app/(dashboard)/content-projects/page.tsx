"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { listContentProjects, createContentProject } from "@/lib/api";
import type { ContentProject, ContentProjectCreate } from "@/lib/types";

function getErrorMessage(err: unknown): string {
  if (err && typeof err === "object" && "response" in err) {
    const res = (err as { response?: { data?: { detail?: unknown } } }).response;
    if (res?.data?.detail) {
      if (typeof res.data.detail === "string") return res.data.detail;
      if (Array.isArray(res.data.detail)) {
        return res.data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join(", ");
      }
    }
  }
  if (err instanceof Error) return err.message;
  return "An unexpected error occurred.";
}

export default function ContentProjectsPage() {
  const { data: projects = [], isLoading, mutate } = useSWR(
    "content-projects",
    () => listContentProjects().then((res) => res.data)
  );

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("Europe/London");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("Project name cannot be blank.");
      return;
    }
    setCreating(true);
    try {
      const payload: ContentProjectCreate = {
        name: name.trim(),
        timezone: timezone.trim() || "Europe/London",
      };
      await createContentProject(payload);
      setName("");
      setTimezone("Europe/London");
      setShowCreate(false);
      mutate();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="max-w-6xl space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Content Projects</h1>
          <p className="text-sm text-text-secondary mt-1">
            Reusable generation pipelines, source feeds, and prompt settings for social publishing.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/social-media"
            className="btn-ghost text-xs py-2 px-3 flex items-center gap-2"
          >
            <Icon icon="mingcute:facebook-fill" width={16} />
            Social Destinations
          </Link>
          <button
            onClick={() => { setShowCreate(!showCreate); setError(null); }}
            className="btn-primary text-xs py-2 px-3 flex items-center gap-2"
          >
            <Icon icon={showCreate ? "solar:close-circle-bold-duotone" : "solar:add-circle-bold-duotone"} width={16} />
            {showCreate ? "Cancel" : "New Local Project"}
          </button>
        </div>
      </div>

      {/* ── Architectural Explanation Banner ── */}
      <div className="rounded-xl border border-divider-soft bg-bg-paper p-4 text-xs text-text-secondary flex items-start gap-3.5 shadow-sm">
        <div className="w-8 h-8 rounded-lg bg-info-lighter/50 text-info-dark flex items-center justify-center flex-shrink-0 mt-0.5">
          <Icon icon="solar:info-circle-bold-duotone" width={20} />
        </div>
        <div className="space-y-1">
          <p className="font-semibold text-text-primary text-sm">How Content Projects Work</p>
          <p>
            A Content Project defines the content generation engine (Instagram/News sources, prompts, visual templates,
            and review mode). One project can generate items that are published across multiple Facebook Profiles and
            Pages simultaneously, or a Facebook Profile can operate its own dedicated project without requiring a Repliz account.
          </p>
        </div>
      </div>

      {/* ── Create Project Form ── */}
      {showCreate && (
        <form onSubmit={handleCreate} className="card space-y-4 border border-divider-soft">
          <div className="flex items-center justify-between border-b border-divider-soft pb-3">
            <div>
              <h2 className="text-base font-semibold text-text-primary">Create Local Content Project</h2>
              <p className="text-xs text-text-secondary">
                Locally managed generation project with no Repliz credential requirement.
              </p>
            </div>
            <span className="badge badge-green">Profile-Ready</span>
          </div>

          {error && (
            <div className="rounded-md border border-error-main/40 bg-error-lighter/30 p-3 text-xs text-error-darker">
              {error}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="label">Project Name *</label>
              <input
                className="input-rect"
                placeholder="e.g. F1 News & Discussions"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
              <p className="text-[11px] text-text-secondary mt-1">Must be unique across your projects.</p>
            </div>
            <div>
              <label className="label">Timezone</label>
              <input
                className="input-rect"
                placeholder="Europe/London"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                required
              />
              <p className="text-[11px] text-text-secondary mt-1">IANA Timezone (e.g. Europe/London, Asia/Jakarta, America/New_York).</p>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="btn-ghost text-xs py-2 px-4"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={creating}
              className="btn-primary text-xs py-2 px-5"
            >
              {creating ? "Creating…" : "Create Project"}
            </button>
          </div>
        </form>
      )}

      {/* ── Project List ── */}
      {isLoading ? (
        <div className="py-12 text-center text-sm text-text-secondary">Loading content projects…</div>
      ) : projects.length === 0 ? (
        <div className="card text-center py-12 space-y-3">
          <Icon icon="solar:folder-with-files-bold-duotone" width={40} className="mx-auto text-text-disabled" />
          <p className="text-base font-semibold text-text-primary">No Content Projects Yet</p>
          <p className="text-xs text-text-secondary max-w-sm mx-auto">
            Create your first local project to start setting up sources and generating social posts.
          </p>
          <button
            onClick={() => setShowCreate(true)}
            className="btn-primary text-xs py-2 px-4 inline-flex items-center gap-2"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            Create Project
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {projects.map((proj) => {
            const isLocal = !proj.repliz_account_id;
            return (
              <div
                key={proj.id}
                className="card space-y-4 hover:border-text-disabled transition-all flex flex-col justify-between"
              >
                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-2.5">
                      <div className="w-9 h-9 rounded-lg bg-bg-paper-hover border border-divider-soft flex items-center justify-center flex-shrink-0 text-text-primary">
                        <Icon icon="solar:folder-with-files-bold-duotone" width={20} />
                      </div>
                      <div>
                        <h2 className="text-sm font-bold text-text-primary">{proj.name}</h2>
                        <p className="text-xs text-text-secondary">Timezone: {proj.timezone || "Europe/London"}</p>
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      {isLocal ? (
                        <span className="badge badge-green" title="Locally managed project (no Repliz dependency)">
                          Local Project
                        </span>
                      ) : (
                        <span className="badge badge-blue" title={`Repliz Account ID: ${proj.repliz_account_id}`}>
                          Repliz Linked
                        </span>
                      )}
                      <span className={`badge ${proj.is_active ? "badge-green" : "badge-gray"}`}>
                        {proj.is_active ? "Active" : "Inactive"}
                      </span>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-xs bg-bg-paper-hover p-2.5 rounded-lg border border-divider-soft">
                    <div>
                      <span className="text-text-disabled block text-[10px] uppercase font-semibold">Destinations</span>
                      <span className="font-semibold text-text-primary">
                        {proj.destination_count} {proj.destination_count === 1 ? "destination" : "destinations"}
                      </span>
                    </div>
                    <div>
                      <span className="text-text-disabled block text-[10px] uppercase font-semibold">Created</span>
                      <span className="text-text-primary">
                        {new Date(proj.created_at).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-2 border-t border-divider-soft text-xs">
                  <Link
                    href={`/social-media?project=${proj.id}`}
                    className="text-text-secondary hover:text-primary-main inline-flex items-center gap-1.5 transition-colors font-medium"
                  >
                    <Icon icon="mingcute:facebook-fill" width={14} />
                    View Destinations
                  </Link>
                  <Link
                    href={`/fanpages/${proj.id}`}
                    className="btn-secondary text-xs py-1.5 px-3 inline-flex items-center gap-1.5"
                  >
                    <Icon icon="solar:tuning-2-bold-duotone" width={14} />
                    Configure Generation
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
