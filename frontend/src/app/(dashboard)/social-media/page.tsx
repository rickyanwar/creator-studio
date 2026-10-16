"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { AccountDevicesCard } from "@/components/social-media/AccountDevicesCard";
import { SchedulePreview } from "@/components/social-media/SchedulePreview";
import {
  listSocialDestinations,
  createSocialDestination,
  updateSocialDestination,
  deleteSocialDestination,
  listContentProjects,
  listPhoneDevices,
  listDeviceAssignments,
  listSocialMediaActivity,
} from "@/lib/api";
import type {
  SocialDestination,
  SocialDestinationCreate,
  SocialDestinationUpdate,
  SocialDestinationMode,
  ContentProject,
  PhoneDevice,
  DestinationDeviceAssignment,
  DeliveryActivity,
} from "@/lib/types";

function getStateBadge(state: string) {
  switch (state) {
    case "published":
      return <span className="badge badge-green">Published</span>;
    case "repliz_scheduled":
      return <span className="badge badge-blue">Repliz Scheduled</span>;
    case "scheduled":
      return <span className="badge badge-blue">Scheduled</span>;
    case "waiting_for_slot":
      return <span className="badge badge-gray">Waiting for Slot</span>;
    case "publishing":
      return <span className="badge badge-blue animate-pulse">Publishing</span>;
    case "waiting_for_device":
      return <span className="badge badge-yellow">Waiting for Device</span>;
    case "uncertain":
      return (
        <span className="badge border border-amber-400 bg-amber-50 text-amber-700">
          Uncertain
        </span>
      );
    case "needs_human":
      return (
        <span className="badge border border-error-main bg-error-lighter text-error-dark">
          Needs Human
        </span>
      );
    case "failed":
      return <span className="badge badge-red">Failed</span>;
    case "skipped":
      return <span className="badge badge-gray">Skipped</span>;
    default:
      return <span className="badge badge-gray">{state}</span>;
  }
}

function getMethodBadge(intended: string | null, actual: string | null) {
  if (!intended && !actual) return null;
  const methodLabel = (m: string) => (m === "phone" ? "Phone" : m === "repliz" ? "Repliz" : m);
  if (actual && intended && actual !== intended) {
    return (
      <span
        className="badge badge-yellow"
        title={`Fallback: intended ${methodLabel(intended)}, used ${methodLabel(actual)}`}
      >
        {methodLabel(actual)} (fallback from {methodLabel(intended)})
      </span>
    );
  }
  const display = actual || intended;
  return (
    <span className={`badge ${display === "phone" ? "badge-green" : "badge-blue"}`}>
      {methodLabel(display!)}
    </span>
  );
}

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

export default function SocialMediaPage() {
  const {
    data: destinations = [],
    isLoading: loadingDestinations,
    mutate: mutateDestinations,
  } = useSWR("social-destinations", () => listSocialDestinations().then((r) => r.data));

  const { data: projects = [] } = useSWR(
    "content-projects",
    () => listContentProjects().then((r) => r.data)
  );

  const { data: devices = [], mutate: mutateDevices } = useSWR(
    "phone-devices",
    () => listPhoneDevices().then((r) => r.data)
  );

  const { data: assignments = [], mutate: mutateAssignments } = useSWR(
    "device-assignments-all",
    () => listDeviceAssignments({ is_active: true }).then((r) => r.data)
  );

  // Tab state
  const [activeTab, setActiveTab] = useState<"destinations" | "activity">("destinations");

  // Activity filter state
  const [activityStateFilter, setActivityStateFilter] = useState<string>("");
  const [activityDestFilter, setActivityDestFilter] = useState<number | "">("");
  const [activityDeviceFilter, setActivityDeviceFilter] = useState<number | "">("");
  const [activityProjectFilter, setActivityProjectFilter] = useState<number | "">("");
  const [activityPage, setActivityPage] = useState<number>(0);
  const pageSize = 20;

  const activityParams = {
    state: activityStateFilter || undefined,
    destination_id: activityDestFilter !== "" ? Number(activityDestFilter) : undefined,
    device_id: activityDeviceFilter !== "" ? Number(activityDeviceFilter) : undefined,
    content_project_id: activityProjectFilter !== "" ? Number(activityProjectFilter) : undefined,
    limit: pageSize,
    offset: activityPage * pageSize,
  };

  const {
    data: activities = [],
    isLoading: loadingActivities,
    error: activityError,
    mutate: mutateActivities,
  } = useSWR(
    activeTab === "activity"
      ? [
          "social-media-activity",
          activityStateFilter,
          activityDestFilter,
          activityDeviceFilter,
          activityProjectFilter,
          activityPage,
        ]
      : null,
    () => listSocialMediaActivity(activityParams).then((r) => r.data)
  );

  const [expandedEvents, setExpandedEvents] = useState<Set<number>>(new Set());

  function toggleExpandEvents(deliveryId: number) {
    setExpandedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(deliveryId)) {
        next.delete(deliveryId);
      } else {
        next.add(deliveryId);
      }
      return next;
    });
  }

  // Modal / Form state
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Form fields
  const [kind, setKind] = useState<"facebook_profile" | "facebook_page">("facebook_profile");
  const [name, setName] = useState("");
  const [contentProjectId, setContentProjectId] = useState<number | "">("");
  const [facebookActorId, setFacebookActorId] = useState("");
  const [parentProfileId, setParentProfileId] = useState<number | "">("");
  const [replizAccountId, setReplizAccountId] = useState("");
  const [mode, setMode] = useState<SocialDestinationMode>("phone");
  const [timezone, setTimezone] = useState("Europe/London");
  const [timezoneDirty, setTimezoneDirty] = useState<boolean>(false);
  const [dailyTarget, setDailyTarget] = useState<number>(1);
  const [phoneDailyTarget, setPhoneDailyTarget] = useState<number>(1);
  const [replizDailyTarget, setReplizDailyTarget] = useState<number>(0);
  const [minGapMinutes, setMinGapMinutes] = useState<number>(5);
  const [supportedMedia, setSupportedMedia] = useState<string[]>(["image", "album", "video"]);
  const [isActive, setIsActive] = useState<boolean>(false);

  const profiles = destinations.filter((d) => d.kind === "facebook_profile");
  const projectMap = new Map<number, ContentProject>(projects.map((p) => [p.id, p]));
  const deviceMap = new Map<number, PhoneDevice>(devices.map((d) => [d.id, d]));

  // Destination assignments lookup
  const assignmentsByDest = new Map<number, DestinationDeviceAssignment[]>();
  for (const a of assignments) {
    const list = assignmentsByDest.get(a.destination_id) || [];
    list.push(a);
    assignmentsByDest.set(a.destination_id, list);
  }

  function openCreateModal(initialKind: "facebook_profile" | "facebook_page" = "facebook_profile") {
    setEditingId(null);
    setServerError(null);
    setKind(initialKind);
    setName("");
    const initialProject = projects[0];
    const initialProjectId = initialProject?.id ?? "";
    setContentProjectId(initialProjectId);
    setFacebookActorId("");
    const validProfiles = destinations.filter((d) => d.kind === "facebook_profile");
    const sameProjectProfiles = initialProjectId
      ? validProfiles.filter((d) => d.content_project_id === Number(initialProjectId))
      : [];
    setParentProfileId(sameProjectProfiles.length === 1 ? sameProjectProfiles[0].id : "");
    setReplizAccountId("");
    setMode("phone");
    setTimezone(initialProject?.timezone || "Europe/London");
    setTimezoneDirty(false);
    setDailyTarget(1);
    setPhoneDailyTarget(1);
    setReplizDailyTarget(0);
    setMinGapMinutes(5);
    setSupportedMedia(["image", "album", "video"]);
    setIsActive(false);
    setModalOpen(true);
  }

  function openEditModal(dest: SocialDestination) {
    setEditingId(dest.id);
    setServerError(null);
    setKind(dest.kind as "facebook_profile" | "facebook_page");
    setName(dest.name);
    setContentProjectId(dest.content_project_id);
    setFacebookActorId(dest.facebook_actor_id || "");
    setParentProfileId(dest.parent_profile_id ?? "");
    setReplizAccountId(dest.repliz_account_id || "");
    setMode(dest.mode);
    setTimezone(dest.timezone || "Europe/London");
    setTimezoneDirty(true);
    if (dest.kind === "facebook_page" && dest.mode === "mixed") {
      const target = Math.max(2, dest.daily_target);
      setDailyTarget(target);
      const pTarget = dest.phone_daily_target >= 1 ? dest.phone_daily_target : 1;
      const rTarget = Math.max(1, target - pTarget);
      setPhoneDailyTarget(pTarget);
      setReplizDailyTarget(rTarget);
    } else {
      setDailyTarget(dest.daily_target);
      setPhoneDailyTarget(dest.phone_daily_target);
      setReplizDailyTarget(dest.repliz_daily_target);
    }
    setMinGapMinutes(dest.min_gap_minutes);
    setSupportedMedia(dest.supported_media || ["image", "album", "video"]);
    setIsActive(dest.is_active);
    setModalOpen(true);
  }

  function handleContentProjectChange(newId: number | "") {
    setContentProjectId(newId);
    if (!editingId && !timezoneDirty && newId) {
      const p = projectMap.get(Number(newId));
      if (p?.timezone) {
        setTimezone(p.timezone);
      }
    }
    const validProfiles = destinations.filter((d) => d.kind === "facebook_profile");
    if (parentProfileId && validProfiles.some((p) => p.id === parentProfileId)) {
      // Keep valid profile selection
    } else {
      const sameProjectProfiles = newId
        ? validProfiles.filter((d) => d.content_project_id === Number(newId))
        : [];
      setParentProfileId(sameProjectProfiles.length === 1 ? sameProjectProfiles[0].id : "");
    }
  }

  function handleModeChange(newMode: SocialDestinationMode) {
    setMode(newMode);
    if (!editingId && (newMode === "phone" || newMode === "mixed")) {
      setIsActive(false);
    }
    if (newMode === "phone") {
      setPhoneDailyTarget(dailyTarget);
      setReplizDailyTarget(0);
    } else if (newMode === "repliz") {
      setReplizDailyTarget(dailyTarget);
      setPhoneDailyTarget(0);
    } else if (newMode === "mixed") {
      const target = Math.max(2, dailyTarget);
      if (target !== dailyTarget) {
        setDailyTarget(target);
      }
      const half = Math.floor(target / 2);
      const phoneVal = Math.max(1, half);
      const replizVal = Math.max(1, target - phoneVal);
      setPhoneDailyTarget(phoneVal);
      setReplizDailyTarget(replizVal);
    }
  }

  function handleDailyTargetChange(val: number) {
    const isMixed = kind === "facebook_page" && mode === "mixed";
    const minVal = isMixed ? 2 : 1;
    const target = Math.max(minVal, val);
    setDailyTarget(target);
    if (mode === "phone") {
      setPhoneDailyTarget(target);
      setReplizDailyTarget(0);
    } else if (mode === "repliz") {
      setReplizDailyTarget(target);
      setPhoneDailyTarget(0);
    } else if (mode === "mixed") {
      let phoneVal = phoneDailyTarget;
      if (phoneVal < 1) phoneVal = 1;
      if (phoneVal >= target) phoneVal = target - 1;
      const replizVal = target - phoneVal;
      setPhoneDailyTarget(phoneVal);
      setReplizDailyTarget(replizVal);
    }
  }

  async function handleSaveDestination(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);

    if (!name.trim()) {
      setServerError("Destination name is required.");
      return;
    }
    if (!contentProjectId) {
      setServerError("Please select a Content Project.");
      return;
    }
    if (kind === "facebook_page" && (mode === "phone" || mode === "mixed") && !parentProfileId) {
      setServerError("Page in Phone or Mixed mode requires a parent Facebook Profile.");
      return;
    }
    if (phoneDailyTarget + replizDailyTarget !== dailyTarget) {
      setServerError(`Daily target sum (${phoneDailyTarget} Phone + ${replizDailyTarget} Repliz) must equal total (${dailyTarget}).`);
      return;
    }

    setSaving(true);
    try {
      const effectiveIsActive = (!editingId && (mode === "phone" || (kind === "facebook_page" && mode === "mixed")))
        ? false
        : isActive;

      if (editingId) {
        const payload: SocialDestinationUpdate = {
          content_project_id: Number(contentProjectId),
          kind,
          name: name.trim(),
          facebook_actor_id: facebookActorId.trim() || null,
          parent_profile_id: kind === "facebook_page" && (mode === "phone" || mode === "mixed") ? Number(parentProfileId) : null,
          repliz_account_id: (kind === "facebook_page" && (mode === "repliz" || mode === "mixed")) ? replizAccountId.trim() || null : null,
          mode: kind === "facebook_profile" ? "phone" : mode,
          timezone: timezone.trim(),
          daily_target: dailyTarget,
          phone_daily_target: phoneDailyTarget,
          repliz_daily_target: replizDailyTarget,
          min_gap_minutes: minGapMinutes,
          supported_media: supportedMedia,
          is_active: effectiveIsActive,
        };
        await updateSocialDestination(editingId, payload);
      } else {
        const payload: SocialDestinationCreate = {
          content_project_id: Number(contentProjectId),
          kind,
          name: name.trim(),
          facebook_actor_id: facebookActorId.trim() || null,
          parent_profile_id: kind === "facebook_page" && (mode === "phone" || mode === "mixed") ? Number(parentProfileId) : null,
          repliz_account_id: (kind === "facebook_page" && (mode === "repliz" || mode === "mixed")) ? replizAccountId.trim() || null : null,
          mode: kind === "facebook_profile" ? "phone" : mode,
          timezone: timezone.trim(),
          daily_target: dailyTarget,
          phone_daily_target: phoneDailyTarget,
          repliz_daily_target: replizDailyTarget,
          min_gap_minutes: minGapMinutes,
          supported_media: supportedMedia,
          is_active: effectiveIsActive,
        };
        await createSocialDestination(payload);
      }
      setModalOpen(false);
      mutateDestinations();
    } catch (err) {
      setServerError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleActive(dest: SocialDestination) {
    setServerError(null);
    try {
      await updateSocialDestination(dest.id, { is_active: !dest.is_active });
      mutateDestinations();
    } catch (err) {
      setServerError(getErrorMessage(err));
    }
  }

  async function handleDelete(dest: SocialDestination) {
    if (!confirm(`Deactivate destination "${dest.name}"? Historical publish records will be preserved.`)) return;
    try {
      await deleteSocialDestination(dest.id);
      mutateDestinations();
    } catch (err) {
      alert(getErrorMessage(err));
    }
  }

  function getDeviceBadges(destinationId: number) {
    const list = assignmentsByDest.get(destinationId) || [];
    if (list.length === 0) {
      return <span className="text-xs text-text-disabled italic">No phone assigned</span>;
    }
    const primary = list.find((a) => a.priority === 0);
    const backups = list.filter((a) => a.priority > 0).sort((a, b) => a.priority - b.priority);

    const primaryDev = primary ? deviceMap.get(primary.device_id) : null;

    return (
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        {primaryDev ? (
          <span className="badge badge-green" title={`Primary: ${primaryDev.name} (${primaryDev.status})`}>
            Phone: {primaryDev.name}
          </span>
        ) : (
          <span className="badge badge-yellow">No Primary Phone</span>
        )}
        {backups.map((b) => {
          const bDev = deviceMap.get(b.device_id);
          return (
            <span key={b.id} className="badge badge-gray" title={`Backup p${b.priority}: ${bDev?.name}`}>
              Backup: {bDev?.name || `#${b.device_id}`}
            </span>
          );
        })}
      </div>
    );
  }

  // Independent pages without parent profile
  const unparentedPages = destinations.filter(
    (d) => d.kind === "facebook_page" && !d.parent_profile_id
  );

  return (
    <div className="max-w-6xl space-y-6">
      {/* ── Top Header ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Social Media</h1>
          <p className="text-sm text-text-secondary mt-1">
            Publishing destinations for Facebook Profiles and managed Facebook Pages.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {/* Prominent link to Content Projects per TASK_U0 */}
          <Link
            href="/content-projects"
            className="btn-secondary text-xs py-2 px-3 flex items-center gap-2"
          >
            <Icon icon="solar:folder-with-files-bold-duotone" width={16} />
            Content Projects ({projects.length})
          </Link>
          <Link
            href="/settings#devices"
            className="btn-ghost text-xs py-2 px-3 flex items-center gap-2"
          >
            <Icon icon="solar:smartphone-bold-duotone" width={16} />
            Phone Devices ({devices.length})
          </Link>
          <button
            onClick={() => openCreateModal("facebook_profile")}
            className="btn-primary text-xs py-2 px-3 flex items-center gap-2"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            Add Destination
          </button>
        </div>
      </div>

      {/* Global error banner if action failed */}
      {serverError && (
        <div className="rounded-xl border border-error-main/40 bg-error-lighter/30 p-3.5 flex items-start justify-between gap-3 text-xs text-error-darker">
          <div className="flex items-start gap-2.5">
            <Icon icon="solar:danger-triangle-bold-duotone" width={18} className="text-error-main flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">Operation Error</p>
              <p>{serverError}</p>
            </div>
          </div>
          <button onClick={() => setServerError(null)} className="text-error-darker hover:opacity-75">
            <Icon icon="solar:close-circle-bold-duotone" width={16} />
          </button>
        </div>
      )}

      {/* ── Tabs Navigation ── */}
      <div className="flex gap-2 border-b border-divider-soft overflow-x-auto">
        <button
          onClick={() => setActiveTab("destinations")}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${
            activeTab === "destinations"
              ? "border-primary-main text-primary-main"
              : "border-transparent text-text-secondary hover:text-text-primary"
          }`}
        >
          <Icon icon="solar:folder-with-files-bold-duotone" width={16} />
          Destinations ({destinations.length})
        </button>
        <button
          onClick={() => setActiveTab("activity")}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap flex items-center gap-2 ${
            activeTab === "activity"
              ? "border-primary-main text-primary-main"
              : "border-transparent text-text-secondary hover:text-text-primary"
          }`}
        >
          <Icon icon="solar:history-bold-duotone" width={16} />
          Publishing Activity
        </button>
      </div>

      {/* ── Tab: Destinations ── */}
      {activeTab === "destinations" && (
        <>
          {/* ── Explanatory Callout ── */}
      <div className="rounded-xl border border-divider-soft bg-bg-paper p-4 text-xs text-text-secondary flex items-start gap-3.5 shadow-sm">
        <div className="w-8 h-8 rounded-lg bg-primary-lighter/40 text-primary-dark flex items-center justify-center flex-shrink-0 mt-0.5">
          <Icon icon="mingcute:facebook-fill" width={20} />
        </div>
        <div className="space-y-1">
          <p className="font-semibold text-text-primary text-sm">Publishing Routing Rules</p>
          <p>
            <strong>Facebook Profiles</strong> publish exclusively via physical Phone automation.{" "}
            <strong>Facebook Pages</strong> publish via Phone, Repliz, or Mixed (split quota). Pages in Phone or
            Mixed mode must operate under an authenticated parent Facebook Profile on the assigned device.
            Unverified Phone routes cannot be activated until actor identity and App Card calibration dry-run succeed.
          </p>
        </div>
      </div>

      {/* ── Profiles and Nested Pages List ── */}
      {loadingDestinations ? (
        <div className="py-12 text-center text-sm text-text-secondary">Loading destinations…</div>
      ) : destinations.length === 0 ? (
        <div className="card text-center py-12 space-y-3">
          <Icon icon="mingcute:facebook-fill" width={44} className="mx-auto text-text-disabled" />
          <p className="text-base font-semibold text-text-primary">No Social Destinations Registered</p>
          <p className="text-xs text-text-secondary max-w-md mx-auto">
            Get started by registering a Facebook Profile or Page destination.
          </p>
          <div className="flex items-center justify-center gap-3 pt-2">
            <button
              onClick={() => openCreateModal("facebook_profile")}
              className="btn-primary text-xs py-2 px-4 flex items-center gap-2"
            >
              <Icon icon="solar:user-bold-duotone" width={16} />
              Register Profile
            </button>
            <button
              onClick={() => openCreateModal("facebook_page")}
              className="btn-secondary text-xs py-2 px-4 flex items-center gap-2"
            >
              <Icon icon="solar:flag-bold-duotone" width={16} />
              Register Page
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Section 1: Facebook Profiles & Managed Pages */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold text-text-primary uppercase tracking-wide">
                Facebook Profiles &amp; Managed Pages ({profiles.length})
              </h2>
            </div>

            {profiles.length === 0 && (
              <div className="card-sm bg-bg-paper-hover text-center py-6 text-xs text-text-secondary">
                No Facebook Profiles registered yet. Register a Profile to anchor Phone-based Page publishing.
              </div>
            )}

            {profiles.map((profile) => {
              const childPages = destinations.filter(
                (d) => d.kind === "facebook_page" && d.parent_profile_id === profile.id
              );
              const project = projectMap.get(profile.content_project_id);
              const hasVerifiedIdentity = !!profile.identity_verified_at;

              return (
                <div
                  key={profile.id}
                  className="rounded-xl border border-divider-soft bg-bg-paper p-5 space-y-4 shadow-sm"
                >
                  {/* Profile Header Row */}
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-divider-soft pb-3">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-full bg-primary-lighter/40 text-primary-dark flex items-center justify-center flex-shrink-0">
                        <Icon icon="solar:user-bold-duotone" width={22} />
                      </div>
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-base font-bold text-text-primary">{profile.name}</span>
                          <span className="badge badge-blue">Profile</span>
                          <span className="badge badge-green">Phone Only</span>
                          <span className={`badge ${profile.is_active ? "badge-green" : "badge-gray"}`}>
                            {profile.is_active ? "Active" : "Inactive Draft"}
                          </span>
                          {hasVerifiedIdentity ? (
                            <span className="badge badge-green" title={`Verified: ${profile.identity_verified_at}`}>
                              Verified
                            </span>
                          ) : (
                            <span className="badge badge-yellow" title="Requires dry-run calibration">
                              Unverified
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-text-secondary mt-0.5">
                          Actor ID:{" "}
                          <strong className="text-text-primary">{profile.facebook_actor_id || "Not assigned"}</strong>
                          {" · "}Timezone: {profile.timezone}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleToggleActive(profile)}
                        className="btn-ghost text-xs py-1 px-2.5"
                        title={profile.is_active ? "Deactivate destination" : "Activate destination"}
                      >
                        {profile.is_active ? "Deactivate" : "Activate"}
                      </button>
                      <button
                        onClick={() => openEditModal(profile)}
                        className="btn-secondary text-xs py-1 px-2.5"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(profile)}
                        className="btn-ghost text-xs py-1 px-2 text-error-main hover:bg-error-lighter/20"
                        title="Deactivate permanently"
                      >
                        <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                      </button>
                    </div>
                  </div>

                  {/* Profile Details Grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs bg-bg-paper-hover p-3 rounded-lg border border-divider-soft">
                    <div>
                      <span className="text-text-disabled block text-[10px] uppercase font-semibold">Content Project</span>
                      <Link
                        href={`/fanpages/${profile.content_project_id}`}
                        className="font-semibold text-primary-main hover:underline flex items-center gap-1 mt-0.5"
                      >
                        <Icon icon="solar:folder-with-files-bold-duotone" width={14} />
                        {project?.name || `Project #${profile.content_project_id}`}
                      </Link>
                    </div>
                    <div>
                      <span className="text-text-disabled block text-[10px] uppercase font-semibold">Pacing &amp; Quotas</span>
                      <span className="text-text-primary font-medium mt-0.5 block">
                        {profile.daily_target} post/day ({profile.phone_daily_target} Phone) · {profile.min_gap_minutes}m min gap
                      </span>
                    </div>
                    <div>
                      <span className="text-text-disabled block text-[10px] uppercase font-semibold">Device Assignment</span>
                      <div className="mt-0.5">{getDeviceBadges(profile.id)}</div>
                    </div>
                  </div>

                  {/* Nested Pages under this Profile */}
                  <div className="pt-2">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-xs font-semibold text-text-secondary uppercase tracking-wider flex items-center gap-1.5">
                        <Icon icon="solar:flag-bold-duotone" width={14} />
                        Managed Facebook Pages ({childPages.length})
                      </div>
                      <button
                        onClick={() => {
                          openCreateModal("facebook_page");
                          setParentProfileId(profile.id);
                        }}
                        className="text-xs text-primary-main hover:underline inline-flex items-center gap-1 font-medium"
                      >
                        <Icon icon="solar:add-circle-bold-duotone" width={14} />
                        Add Page under this Profile
                      </button>
                    </div>

                    {childPages.length === 0 ? (
                      <p className="text-xs text-text-disabled italic pl-4 py-1 border-l-2 border-divider-soft">
                        No pages managed under this profile.
                      </p>
                    ) : (
                      <div className="space-y-2 pl-4 border-l-2 border-primary-main/30">
                        {childPages.map((page) => {
                          const pageProject = projectMap.get(page.content_project_id);
                          return (
                            <div
                              key={page.id}
                              className="rounded-lg border border-divider-soft bg-bg-default p-3.5 space-y-2.5 hover:border-text-disabled transition-colors"
                            >
                              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                                <div className="flex items-center gap-2.5 flex-wrap">
                                  <Icon icon="solar:flag-bold-duotone" width={16} className="text-text-secondary" />
                                  <span className="text-sm font-bold text-text-primary">{page.name}</span>
                                  <span
                                    className={`badge ${
                                      page.mode === "phone"
                                        ? "badge-green"
                                        : page.mode === "repliz"
                                        ? "badge-blue"
                                        : "badge-purple"
                                    }`}
                                  >
                                    {page.mode.toUpperCase()}
                                  </span>
                                  <span className={`badge ${page.is_active ? "badge-green" : "badge-gray"}`}>
                                    {page.is_active ? "Active" : "Inactive Draft"}
                                  </span>
                                  {page.repliz_account_id && (
                                    <span className="badge badge-gray text-[10px]" title="Repliz Account ID">
                                      Repliz: {page.repliz_account_id}
                                    </span>
                                  )}
                                </div>
                                <div className="flex items-center gap-2">
                                  <button
                                    onClick={() => handleToggleActive(page)}
                                    className="btn-ghost text-[11px] py-1 px-2"
                                  >
                                    {page.is_active ? "Deactivate" : "Activate"}
                                  </button>
                                  <button
                                    onClick={() => openEditModal(page)}
                                    className="btn-secondary text-[11px] py-1 px-2"
                                  >
                                    Edit
                                  </button>
                                  <button
                                    onClick={() => handleDelete(page)}
                                    className="text-text-disabled hover:text-error-main p-1"
                                    title="Deactivate page"
                                  >
                                    <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                                  </button>
                                </div>
                              </div>

                              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px] text-text-secondary">
                                <div>
                                  <span className="text-text-disabled">Project: </span>
                                  <Link
                                    href={`/fanpages/${page.content_project_id}`}
                                    className="text-primary-main hover:underline font-semibold"
                                  >
                                    {pageProject?.name || `#${page.content_project_id}`}
                                  </Link>
                                </div>
                                <div>
                                  <span className="text-text-disabled">Daily Quota: </span>
                                  <span className="text-text-primary font-medium">
                                    {page.daily_target} total ({page.phone_daily_target} Phone / {page.repliz_daily_target} Repliz)
                                  </span>
                                </div>
                                <div>
                                  <span className="text-text-disabled">Phones: </span>
                                  {getDeviceBadges(page.id)}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Section 2: Independent / Repliz-Only Pages */}
          {unparentedPages.length > 0 && (
            <div className="space-y-4 pt-4 border-t border-divider-soft">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-bold text-text-primary uppercase tracking-wide">
                    Independent / Repliz-Only Pages ({unparentedPages.length})
                  </h2>
                  <p className="text-xs text-text-secondary">
                    Pages without a parent Facebook Profile publish exclusively via Repliz API.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {unparentedPages.map((page) => {
                  const project = projectMap.get(page.content_project_id);
                  return (
                    <div
                      key={page.id}
                      className="rounded-xl border border-divider-soft bg-bg-paper p-4 space-y-3 hover:border-text-disabled transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <Icon icon="solar:flag-bold-duotone" width={18} className="text-text-primary" />
                          <span className="text-sm font-bold text-text-primary">{page.name}</span>
                          <span className="badge badge-blue">Repliz Only</span>
                        </div>
                        <span className={`badge ${page.is_active ? "badge-green" : "badge-gray"}`}>
                          {page.is_active ? "Active" : "Inactive"}
                        </span>
                      </div>

                      <div className="text-xs space-y-1 text-text-secondary bg-bg-paper-hover p-2.5 rounded-lg border border-divider-soft">
                        <p>Repliz ID: <strong className="text-text-primary">{page.repliz_account_id || "None"}</strong></p>
                        <p>Daily Limit: <strong className="text-text-primary">{page.daily_target} posts/day</strong></p>
                        <p>
                          Content Project:{" "}
                          <Link href={`/fanpages/${page.content_project_id}`} className="text-primary-main hover:underline">
                            {project?.name || `#${page.content_project_id}`}
                          </Link>
                        </p>
                      </div>

                      <div className="flex items-center justify-end gap-2 pt-1">
                        <button
                          onClick={() => handleToggleActive(page)}
                          className="btn-ghost text-xs py-1 px-2.5"
                        >
                          {page.is_active ? "Deactivate" : "Activate"}
                        </button>
                        <button
                          onClick={() => openEditModal(page)}
                          className="btn-secondary text-xs py-1 px-2.5"
                        >
                          Edit
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

          {/* ── Account to Phone Automation Assignments ── */}
          <div id="account-devices" className="pt-2">
            <AccountDevicesCard
              destinations={destinations}
              devices={devices}
              assignments={assignments}
              mutateAssignments={mutateAssignments}
              mutateDestinations={mutateDestinations}
              mutateDevices={mutateDevices}
            />
          </div>
        </>
      )}

      {/* ── Tab: Publishing Activity ── */}
      {activeTab === "activity" && (
        <div className="space-y-4">
          {/* Filter Bar */}
          <div className="card p-4 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Icon icon="solar:filter-bold-duotone" width={18} className="text-text-secondary" />
                <span className="text-sm font-semibold text-text-primary">Filter Activity</span>
              </div>
              <button
                type="button"
                onClick={() => mutateActivities()}
                className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5 self-start sm:self-auto"
                title="Refresh Activity"
              >
                <Icon icon="solar:refresh-bold-duotone" width={14} className={loadingActivities ? "animate-spin" : ""} />
                Refresh
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1">State</label>
                <select
                  value={activityStateFilter}
                  onChange={(e) => {
                    setActivityStateFilter(e.target.value);
                    setActivityPage(0);
                  }}
                  className="input-rect w-full text-xs"
                >
                  <option value="">All States</option>
                  <option value="waiting_for_slot">Waiting for Slot</option>
                  <option value="scheduled">Scheduled</option>
                  <option value="publishing">Publishing</option>
                  <option value="waiting_for_device">Waiting for Device</option>
                  <option value="repliz_scheduled">Repliz Scheduled</option>
                  <option value="published">Published</option>
                  <option value="uncertain">Uncertain</option>
                  <option value="needs_human">Needs Human</option>
                  <option value="failed">Failed</option>
                  <option value="skipped">Skipped</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1">Destination</label>
                <select
                  value={activityDestFilter}
                  onChange={(e) => {
                    setActivityDestFilter(e.target.value === "" ? "" : Number(e.target.value));
                    setActivityPage(0);
                  }}
                  className="input-rect w-full text-xs"
                >
                  <option value="">All Destinations</option>
                  {destinations.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} ({d.kind === "facebook_profile" ? "Profile" : "Page"})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1">Device</label>
                <select
                  value={activityDeviceFilter}
                  onChange={(e) => {
                    setActivityDeviceFilter(e.target.value === "" ? "" : Number(e.target.value));
                    setActivityPage(0);
                  }}
                  className="input-rect w-full text-xs"
                >
                  <option value="">All Devices</option>
                  {devices.map((dev) => (
                    <option key={dev.id} value={dev.id}>
                      {dev.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-text-secondary mb-1">Content Project</label>
                <select
                  value={activityProjectFilter}
                  onChange={(e) => {
                    setActivityProjectFilter(e.target.value === "" ? "" : Number(e.target.value));
                    setActivityPage(0);
                  }}
                  className="input-rect w-full text-xs"
                >
                  <option value="">All Projects</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Activity List */}
          {loadingActivities ? (
            <div className="card text-center py-12 text-sm text-text-secondary flex flex-col items-center gap-2">
              <Icon icon="solar:refresh-bold-duotone" width={24} className="animate-spin text-primary-main" />
              <span>Loading publishing activity…</span>
            </div>
          ) : activityError ? (
            <div className="card text-center py-12 space-y-3">
              <Icon icon="solar:danger-triangle-bold-duotone" width={36} className="mx-auto text-error-main" />
              <p className="text-sm font-semibold text-text-primary">Failed to load activity</p>
              <p className="text-xs text-text-secondary">{getErrorMessage(activityError)}</p>
              <button
                type="button"
                onClick={() => mutateActivities()}
                className="btn-secondary text-xs py-1.5 px-3 inline-flex items-center gap-1.5"
              >
                <Icon icon="solar:refresh-bold-duotone" width={14} />
                Retry
              </button>
            </div>
          ) : activities.length === 0 ? (
            <div className="card text-center py-12 space-y-2">
              <Icon icon="solar:history-bold-duotone" width={36} className="mx-auto text-text-disabled" />
              <p className="text-base font-semibold text-text-primary">No Activity Found</p>
              <p className="text-xs text-text-secondary max-w-sm mx-auto">
                No deliveries match the selected filters. Deliveries will appear here when content jobs are scheduled or published.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {activities.map((act) => {
                const isExpanded = expandedEvents.has(act.id);
                return (
                  <div
                    key={act.id}
                    className="rounded-xl border border-divider-soft bg-bg-paper p-4 space-y-3 shadow-sm hover:border-text-disabled/50 transition-colors"
                  >
                    {/* Header Row */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-divider-soft pb-2.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        {getStateBadge(act.state)}
                        <span className="text-sm font-bold text-text-primary">
                          {act.destination_name || `Destination #${act.destination_id}`}
                        </span>
                        {act.destination_kind && (
                          <span className="badge badge-gray text-[10px]">
                            {act.destination_kind === "facebook_profile" ? "Profile" : "Page"}
                          </span>
                        )}
                        {act.content_project_id && projectMap.get(act.content_project_id) && (
                          <span className="badge badge-gray text-[10px]" title="Content Project">
                            {projectMap.get(act.content_project_id)?.name}
                          </span>
                        )}
                        {getMethodBadge(act.intended_method, act.actual_method)}
                        {act.device_name && (
                          <span className="badge badge-green text-[10px]">
                            Device: {act.device_name}
                          </span>
                        )}
                      </div>

                      <div className="text-xs text-text-secondary flex items-center gap-2">
                        <span>Job #{act.publish_job_id}</span>
                        <span>•</span>
                        <span>Delivery #{act.id}</span>
                      </div>
                    </div>

                    {/* Content & Metadata */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                      {/* Left: Snapshot / Media */}
                      <div className="space-y-1 bg-bg-paper-hover p-2.5 rounded-lg border border-divider-soft">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold text-text-primary truncate">
                            {act.title_snapshot || "Untitled Post"}
                          </span>
                          {act.media_kind && (
                            <span className="badge badge-gray text-[10px] uppercase">{act.media_kind}</span>
                          )}
                        </div>
                        {act.caption_snapshot && (
                          <p className="text-text-secondary line-clamp-2" title={act.caption_snapshot}>
                            {act.caption_snapshot}
                          </p>
                        )}
                      </div>

                      {/* Right: Timing & External IDs */}
                      <div className="space-y-1.5 bg-bg-paper-hover p-2.5 rounded-lg border border-divider-soft">
                        <div className="grid grid-cols-2 gap-1 text-[11px]">
                          <div>
                            <span className="text-text-secondary">Scheduled: </span>
                            <span className="text-text-primary font-medium">
                              {act.scheduled_for ? new Date(act.scheduled_for).toLocaleString() : "-"}
                            </span>
                          </div>
                          <div>
                            <span className="text-text-secondary">Published: </span>
                            <span className="text-text-primary font-medium">
                              {act.published_at ? new Date(act.published_at).toLocaleString() : "-"}
                            </span>
                          </div>
                        </div>

                        {act.claimed_at && (
                          <p className="text-[11px] text-text-secondary">
                            Claimed: {new Date(act.claimed_at).toLocaleString()}
                            {act.attempt_count > 0 ? ` (${act.attempt_count} attempts)` : ""}
                          </p>
                        )}

                        <div className="flex flex-wrap items-center gap-2 pt-0.5">
                          {act.facebook_permalink && (
                            <a
                              href={act.facebook_permalink}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 text-primary-main hover:underline font-medium"
                            >
                              <Icon icon="solar:link-bold-duotone" width={13} />
                              View on Facebook
                            </a>
                          )}
                          {act.facebook_post_id && !act.facebook_permalink && (
                            <span className="font-mono text-text-secondary" title="Facebook Post ID">
                              Post ID: {act.facebook_post_id}
                            </span>
                          )}
                          {act.repliz_schedule_id && (
                            <span className="font-mono text-text-disabled" title="Repliz Schedule ID">
                              Repliz ID: {act.repliz_schedule_id}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Uncertain / Error Banner */}
                    {(act.state === "uncertain" || act.uncertain_since) && (
                      <div className="rounded-lg border border-amber-400/80 bg-amber-50 p-2.5 text-xs text-amber-900 space-y-1">
                        <div className="flex items-center gap-1.5 font-semibold">
                          <Icon icon="solar:danger-triangle-bold-duotone" width={16} className="text-amber-600 flex-shrink-0" />
                          <span>Uncertain State (Blocked from Auto-Publish)</span>
                        </div>
                        <p className="text-[11px]">
                          Outcome uncertain since{" "}
                          {new Date(act.uncertain_since || act.updated_at).toLocaleString()}. Manual verification required to avoid duplicate posts.
                        </p>
                        {act.last_error && (
                          <p className="font-mono text-[11px] bg-white/70 p-1 rounded border border-amber-200">
                            {act.last_error}
                          </p>
                        )}
                      </div>
                    )}

                    {act.state === "needs_human" && (
                      <div className="rounded-lg border border-error-main/40 bg-error-lighter/40 p-2.5 text-xs text-error-darker space-y-1">
                        <div className="flex items-center gap-1.5 font-semibold">
                          <Icon icon="solar:danger-triangle-bold-duotone" width={16} className="text-error-main flex-shrink-0" />
                          <span>Human Intervention Required</span>
                        </div>
                        {act.last_error && (
                          <p className="font-mono text-[11px] bg-white/70 p-1 rounded border border-error-main/20">
                            {act.last_error}
                          </p>
                        )}
                      </div>
                    )}

                    {act.last_error && act.state !== "uncertain" && act.state !== "needs_human" && (
                      <div className="rounded-lg border border-error-main/30 bg-error-lighter/20 p-2 text-xs text-error-darker flex items-start gap-1.5">
                        <Icon icon="solar:close-circle-bold-duotone" width={14} className="text-error-main flex-shrink-0 mt-0.5" />
                        <span className="font-mono text-[11px] break-all">{act.last_error}</span>
                      </div>
                    )}

                    {/* Event History Accordion */}
                    {act.events && act.events.length > 0 && (
                      <div className="pt-1">
                        <button
                          type="button"
                          onClick={() => toggleExpandEvents(act.id)}
                          className="text-xs text-text-secondary hover:text-text-primary flex items-center gap-1 font-medium"
                        >
                          <Icon
                            icon={isExpanded ? "solar:alt-arrow-up-bold-duotone" : "solar:alt-arrow-down-bold-duotone"}
                            width={14}
                          />
                          Event History ({act.events.length})
                        </button>

                        {isExpanded && (
                          <div className="mt-2 space-y-1.5 pl-3 border-l-2 border-divider-soft text-xs">
                            {act.events.map((ev) => (
                              <div key={ev.id} className="py-1 space-y-0.5">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <span className="badge badge-gray font-mono text-[10px]">
                                    {ev.event_type}
                                  </span>
                                  <span className="text-text-disabled text-[11px]">
                                    {new Date(ev.created_at).toLocaleString()}
                                  </span>
                                </div>
                                {ev.details && Object.keys(ev.details).length > 0 && (
                                  <pre className="font-mono text-[10px] text-text-secondary bg-bg-paper-hover p-1.5 rounded overflow-x-auto">
                                    {JSON.stringify(ev.details, null, 2)}
                                  </pre>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Pagination controls */}
              <div className="flex items-center justify-between pt-2 text-xs text-text-secondary">
                <button
                  type="button"
                  disabled={activityPage === 0}
                  onClick={() => setActivityPage((p) => Math.max(0, p - 1))}
                  className="btn-secondary py-1 px-3 disabled:opacity-50"
                >
                  Previous
                </button>
                <span>
                  Page {activityPage + 1}
                  {activities.length > 0 && ` (${activities.length} items)`}
                </span>
                <button
                  type="button"
                  disabled={activities.length < pageSize}
                  onClick={() => setActivityPage((p) => p + 1)}
                  className="btn-secondary py-1 px-3 disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Destination Modal (Create / Edit) ── */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-bg-paper rounded-xl border border-divider-soft w-full max-w-xl max-h-[90vh] overflow-y-auto p-6 space-y-5 shadow-dropdown">
            <div className="flex items-center justify-between border-b border-divider-soft pb-3">
              <div>
                <h2 className="text-lg font-bold text-text-primary">
                  {editingId ? "Edit Destination" : "Add Social Destination"}
                </h2>
                <p className="text-xs text-text-secondary mt-0.5">
                  Configure Facebook Profile or Page publishing route.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="text-text-disabled hover:text-text-primary"
              >
                <Icon icon="solar:close-circle-bold-duotone" width={22} />
              </button>
            </div>

            {serverError && (
              <div className="rounded-md border border-error-main/40 bg-error-lighter/30 p-3 text-xs text-error-darker">
                {serverError}
              </div>
            )}

            <form onSubmit={handleSaveDestination} className="space-y-4">
              {/* Kind selection */}
              <div>
                <label className="label">Destination Kind</label>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => {
                      setKind("facebook_profile");
                      setMode("phone");
                    }}
                    className={`py-2 px-3 text-xs font-semibold rounded-md border flex items-center justify-center gap-2 transition-colors ${
                      kind === "facebook_profile"
                        ? "bg-primary-main/10 border-primary-main text-primary-dark"
                        : "bg-bg-paper-hover border-divider-soft text-text-secondary"
                    }`}
                  >
                    <Icon icon="solar:user-bold-duotone" width={16} />
                    Facebook Profile
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setKind("facebook_page");
                      if (mode === "mixed" && dailyTarget < 2) {
                        handleDailyTargetChange(2);
                      }
                    }}
                    className={`py-2 px-3 text-xs font-semibold rounded-md border flex items-center justify-center gap-2 transition-colors ${
                      kind === "facebook_page"
                        ? "bg-primary-main/10 border-primary-main text-primary-dark"
                        : "bg-bg-paper-hover border-divider-soft text-text-secondary"
                    }`}
                  >
                    <Icon icon="solar:flag-bold-duotone" width={16} />
                    Facebook Page
                  </button>
                </div>
              </div>

              {/* Name */}
              <div>
                <label className="label">Destination Name *</label>
                <input
                  className="input-rect text-xs py-2"
                  placeholder="e.g. Personal Profile or Motorsport News FB"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </div>

              {/* Content Project */}
              <div>
                <div className="flex items-center justify-between">
                  <label className="label">Content Project *</label>
                  <Link href="/content-projects" className="text-[11px] text-primary-main hover:underline mb-1">
                    Manage Projects
                  </Link>
                </div>
                <select
                  className="input text-xs py-2"
                  value={contentProjectId}
                  onChange={(e) => handleContentProjectChange(e.target.value ? Number(e.target.value) : "")}
                  required
                >
                  <option value="">Select content project…</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} {p.repliz_account_id ? "(Repliz)" : "(Local)"}
                    </option>
                  ))}
                </select>
                <p className="text-[10px] text-text-secondary mt-0.5">
                  Determines which generation pipeline and source feeds populate this destination.
                </p>
              </div>

              {/* Facebook Actor ID */}
              <div>
                <label className="label">Facebook Actor ID</label>
                <input
                  className="input-rect text-xs py-2"
                  placeholder="e.g. 100089123456789"
                  value={facebookActorId}
                  onChange={(e) => setFacebookActorId(e.target.value)}
                />
                <p className="text-[10px] text-text-secondary mt-0.5">
                  Stable Facebook user or page numeric ID. Required before activating Phone routes.
                </p>
              </div>

              {/* Specific fields for Pages */}
              {kind === "facebook_page" && (
                <div className="p-3 rounded-lg bg-bg-paper-hover border border-divider-soft space-y-3">
                  <div>
                    <label className="label">Publishing Method</label>
                    <div className="grid grid-cols-3 gap-2">
                      <button
                        type="button"
                        onClick={() => handleModeChange("phone")}
                        className={`py-1.5 px-2 text-xs font-semibold rounded border text-center transition-colors ${
                          mode === "phone"
                            ? "bg-primary-main text-white border-primary-main"
                            : "bg-bg-paper border-divider-soft text-text-secondary"
                        }`}
                      >
                        Phone Only
                      </button>
                      <button
                        type="button"
                        onClick={() => handleModeChange("mixed")}
                        className={`py-1.5 px-2 text-xs font-semibold rounded border text-center transition-colors ${
                          mode === "mixed"
                            ? "bg-primary-main text-white border-primary-main"
                            : "bg-bg-paper border-divider-soft text-text-secondary"
                        }`}
                      >
                        Mixed
                      </button>
                      <button
                        type="button"
                        onClick={() => handleModeChange("repliz")}
                        className={`py-1.5 px-2 text-xs font-semibold rounded border text-center transition-colors ${
                          mode === "repliz"
                            ? "bg-primary-main text-white border-primary-main"
                            : "bg-bg-paper border-divider-soft text-text-secondary"
                        }`}
                      >
                        Repliz Only
                      </button>
                    </div>
                  </div>

                  {/* Parent Profile required for Phone or Mixed */}
                  {(mode === "phone" || mode === "mixed") && (
                    <div>
                      <label className="label">Parent Facebook Profile *</label>
                      <select
                        className="input text-xs py-2 bg-bg-paper"
                        value={parentProfileId}
                        onChange={(e) => setParentProfileId(e.target.value ? Number(e.target.value) : "")}
                        required
                      >
                        <option value="">Select administering Facebook Profile…</option>
                        {destinations
                          .filter((d) => d.kind === "facebook_profile")
                          .slice()
                          .sort((a, b) => {
                            const currentProj = contentProjectId !== "" ? Number(contentProjectId) : null;
                            const aSame = currentProj !== null && a.content_project_id === currentProj;
                            const bSame = currentProj !== null && b.content_project_id === currentProj;
                            if (aSame !== bSame) return aSame ? -1 : 1;
                            return a.name.localeCompare(b.name);
                          })
                          .map((p) => {
                            const actorText = p.facebook_actor_id || "No Actor ID";
                            const proj = projectMap.get(p.content_project_id);
                            const projSuffix = proj?.name ? ` — ${proj.name}` : "";
                            return (
                              <option key={p.id} value={p.id}>
                                {p.name} ({actorText}){projSuffix}
                              </option>
                            );
                          })}
                      </select>
                      <p className="text-[10px] text-text-secondary mt-0.5">
                        The main Profile signed in on the phone that owns/administers this Page.
                      </p>
                    </div>
                  )}

                  {/* Repliz account ID for Repliz or Mixed */}
                  {(mode === "repliz" || mode === "mixed") && (
                    <div>
                      <label className="label">Repliz Account ID</label>
                      <input
                        className="input-rect text-xs py-2 bg-bg-paper"
                        placeholder="e.g. 64f1a2b3c4d5e6f7"
                        value={replizAccountId}
                        onChange={(e) => setReplizAccountId(e.target.value)}
                      />
                    </div>
                  )}
                </div>
              )}

              {/* Pacing & Quotas */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="label mb-0">Timezone</label>
                    {contentProjectId && projectMap.get(Number(contentProjectId)) && (
                      <span className="text-[10px] text-text-secondary">
                        Project default: {projectMap.get(Number(contentProjectId))?.timezone}
                        {projectMap.get(Number(contentProjectId))?.target_country ? ` (${projectMap.get(Number(contentProjectId))?.target_country})` : ""}
                      </span>
                    )}
                  </div>
                  <input
                    className="input-rect text-xs py-2"
                    value={timezone}
                    onChange={(e) => {
                      setTimezone(e.target.value);
                      setTimezoneDirty(true);
                    }}
                    required
                  />
                  <p className="text-[10px] text-text-secondary mt-0.5">
                    Target audience local timezone. Respects DST.
                  </p>
                </div>
                <div>
                  <label className="label">Daily post limit</label>
                  <input
                    type="number"
                    min={kind === "facebook_page" && mode === "mixed" ? 2 : 1}
                    className="input-rect text-xs py-2"
                    value={dailyTarget}
                    onChange={(e) =>
                      handleDailyTargetChange(
                        parseInt(e.target.value) || (kind === "facebook_page" && mode === "mixed" ? 2 : 1)
                      )
                    }
                    required
                  />
                  <p className="text-[10px] text-text-secondary mt-0.5">
                    Maximum posts per local calendar day; fewer posts may be scheduled when content or available time is limited.
                  </p>
                </div>
              </div>

              {/* Mixed Quotas Split */}
              {kind === "facebook_page" && mode === "mixed" && (
                <div className="grid grid-cols-2 gap-3 p-3 rounded-lg bg-bg-paper-hover border border-divider-soft">
                  <div>
                    <label className="label">Phone Quota</label>
                    <input
                      type="number"
                      min={1}
                      max={Math.max(1, dailyTarget - 1)}
                      className="input-rect text-xs py-2 bg-bg-paper"
                      value={phoneDailyTarget}
                      onChange={(e) => {
                        const raw = parseInt(e.target.value);
                        const val = isNaN(raw) ? 1 : Math.max(1, Math.min(dailyTarget - 1, raw));
                        setPhoneDailyTarget(val);
                        setReplizDailyTarget(dailyTarget - val);
                      }}
                    />
                  </div>
                  <div>
                    <label className="label">Repliz Quota</label>
                    <input
                      type="number"
                      min={1}
                      max={Math.max(1, dailyTarget - 1)}
                      className="input-rect text-xs py-2 bg-bg-paper"
                      value={replizDailyTarget}
                      onChange={(e) => {
                        const raw = parseInt(e.target.value);
                        const val = isNaN(raw) ? 1 : Math.max(1, Math.min(dailyTarget - 1, raw));
                        setReplizDailyTarget(val);
                        setPhoneDailyTarget(dailyTarget - val);
                      }}
                    />
                  </div>
                  <div className="col-span-2 text-[10px] text-text-secondary">
                    Sum must equal Daily Target ({dailyTarget}). Current sum: {phoneDailyTarget + replizDailyTarget}.
                  </div>
                </div>
              )}

              <div>
                <label className="label">Minimum spacing (minutes)</label>
                <input
                  type="number"
                  min={1}
                  className="input-rect text-xs py-2"
                  value={minGapMinutes}
                  onChange={(e) => setMinGapMinutes(parseInt(e.target.value) || 5)}
                  required
                />
                <p className="text-[10px] text-text-secondary mt-0.5">
                  Safety minimum between posts. Routine posts are spread through the target’s local active hours with varied intervals.
                </p>
              </div>

              {/* Phone Scheduling Note */}
              {(mode === "phone" || (kind === "facebook_page" && mode === "mixed")) && (
                <div className="p-2.5 rounded bg-bg-paper-hover border border-divider-soft text-[10px] text-text-secondary flex items-start gap-2">
                  <Icon icon="solar:smartphone-line-duotone" width={16} className="text-primary-main shrink-0 mt-0.5" />
                  <span>
                    Phone uploads start when their scheduled slot is due, after account and device verification.
                  </span>
                </div>
              )}

              {/* Active Toggle with Note */}
              {(() => {
                const isPhoneRoute = mode === "phone" || (kind === "facebook_page" && mode === "mixed");
                const isNewPhone = !editingId && isPhoneRoute;

                let hasActor = false;
                let hasVerifiedAssignment = false;
                let parentActorMissing = false;

                if (editingId && isPhoneRoute) {
                  hasActor = Boolean(facebookActorId && facebookActorId.trim());
                  if (kind === "facebook_page") {
                    const parent = destinations.find((d) => d.id === parentProfileId);
                    if (!parent || !parent.facebook_actor_id) {
                      parentActorMissing = true;
                    }
                  }

                  const activeAssignments = (assignmentsByDest.get(editingId) || []).filter((a) => a.is_active);
                  for (const a of activeAssignments) {
                    const dev = deviceMap.get(a.device_id);
                    if (a.verified_at && dev && dev.calibration_verified_at && dev.status === "available") {
                      if (kind === "facebook_profile") {
                        if (a.verified_profile_actor_id === facebookActorId.trim()) {
                          hasVerifiedAssignment = true;
                          break;
                        }
                      } else if (kind === "facebook_page") {
                        const parent = destinations.find((d) => d.id === parentProfileId);
                        if (
                          parent &&
                          a.verified_profile_actor_id === parent.facebook_actor_id &&
                          a.verified_target_actor_id === facebookActorId.trim()
                        ) {
                          hasVerifiedAssignment = true;
                          break;
                        }
                      }
                    }
                  }
                }

                return (
                  <div className="p-3 rounded-lg bg-bg-paper-hover border border-divider-soft space-y-2">
                    <label className={`flex items-center gap-2 ${isNewPhone ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}>
                      <input
                        type="checkbox"
                        checked={isNewPhone ? false : isActive}
                        disabled={isNewPhone}
                        onChange={(e) => {
                          if (!isNewPhone) {
                            setIsActive(e.target.checked);
                          }
                        }}
                        className="w-4 h-4 rounded text-primary-main focus:ring-primary-main"
                      />
                      <span className="text-xs font-semibold text-text-primary">Activate Destination</span>
                    </label>

                    {isNewPhone && (
                      <p className="text-[11px] text-amber-500/90 pl-6">
                        Save as a draft, assign a phone in Account Devices, then verify the account before activating.
                      </p>
                    )}

                    {!isNewPhone && isPhoneRoute && (!hasActor || !hasVerifiedAssignment || parentActorMissing) && (
                      <div className="pl-6 text-[11px] text-amber-500/90 space-y-1">
                        <p>Prerequisites for activating phone automation:</p>
                        <ul className="list-disc pl-4 space-y-0.5 text-[10px]">
                          {!hasActor && <li>Destination Facebook Actor ID must be set.</li>}
                          {parentActorMissing && <li>Parent Facebook Profile must have a Facebook Actor ID.</li>}
                          {!hasVerifiedAssignment && (
                            <li>
                              Verified active device assignment and calibration required. Assign and verify in{" "}
                              <a
                                href="#account-devices"
                                onClick={() => setModalOpen(false)}
                                className="underline font-medium hover:text-amber-400"
                              >
                                Account Devices
                              </a>.
                            </li>
                          )}
                        </ul>
                      </div>
                    )}

                    {!isPhoneRoute && (
                      <p className="text-[10px] text-text-secondary pl-6">
                        Repliz destination can be activated once configured.
                      </p>
                    )}
                  </div>
                );
              })()}

              {/* Schedule Preview (existing destination edit only) */}
              {editingId && (
                <div className="pt-2">
                  <SchedulePreview destinationId={editingId} />
                </div>
              )}

              {!editingId && (
                <div className="p-2.5 rounded bg-bg-paper-hover border border-divider-soft text-[10px] text-text-secondary flex items-start gap-2">
                  <Icon icon="solar:info-circle-linear" width={16} className="text-primary-main shrink-0 mt-0.5" />
                  <span>
                    Schedule preview will be available after saving this destination.
                  </span>
                </div>
              )}

              {/* Submit Buttons */}
              <div className="flex justify-end gap-2 pt-3 border-t border-divider-soft">
                <button
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="btn-ghost text-xs py-2 px-4"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="btn-primary text-xs py-2 px-5"
                >
                  {saving
                    ? "Saving…"
                    : !editingId && (mode === "phone" || (kind === "facebook_page" && mode === "mixed"))
                    ? "Save Draft"
                    : editingId
                    ? "Update Destination"
                    : "Create Destination"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
