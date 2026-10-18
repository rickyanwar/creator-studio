"use client";

import React, { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { SocialTabs } from "@/components/social-media/SocialTabs";
import { AccountDevicesCard } from "@/components/social-media/AccountDevicesCard";
import {
  listSocialDestinations,
  listContentProjects,
  listPhoneDevices,
  listDeviceAssignments,
} from "@/lib/api";
import { listReplizAccounts } from "@/lib/socialBrandsApi";
import type {
  SocialDestination,
  ContentProject,
  PhoneDevice,
  DestinationDeviceAssignment,
} from "@/lib/types";
import type { ReplizAccountsOut } from "@/lib/socialBrandsApi";

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

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return "never";
  try {
    const d = new Date(dateString);
    if (isNaN(d.getTime())) return dateString;
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diffSec < 60) return "just now";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}h ago`;
    const diffDay = Math.floor(diffHour / 24);
    return `${diffDay}d ago`;
  } catch {
    return dateString;
  }
}

function getInitials(name: string): string {
  if (!name) return "??";
  const words = name.trim().split(/\s+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export default function BrandsAccountsPage() {
  const {
    data: destinations = [],
    isLoading: loadingDestinations,
    error: destinationsError,
    mutate: mutateDestinations,
  } = useSWR<SocialDestination[]>("social-destinations", () =>
    listSocialDestinations().then((r) => r.data)
  );

  const { data: projects = [] } = useSWR<ContentProject[]>(
    "content-projects",
    () => listContentProjects().then((r) => r.data)
  );

  const {
    data: devices = [],
    isLoading: loadingDevices,
    error: devicesError,
    mutate: mutateDevices,
  } = useSWR<PhoneDevice[]>("phone-devices", () =>
    listPhoneDevices().then((r) => r.data)
  );

  const {
    data: assignments = [],
    mutate: mutateAssignments,
  } = useSWR<DestinationDeviceAssignment[]>("device-assignments-all", () =>
    listDeviceAssignments({ is_active: true }).then((r) => r.data)
  );

  const {
    data: replizData,
    isLoading: loadingRepliz,
    error: replizFetchError,
    mutate: mutateRepliz,
  } = useSWR<ReplizAccountsOut>("repliz-accounts", listReplizAccounts);

  const projectMap = new Map<number, ContentProject>(projects.map((p) => [p.id, p]));
  const destinationMap = new Map<number, SocialDestination>(destinations.map((d) => [d.id, d]));

  const profiles = destinations.filter((d) => d.kind === "facebook_profile");
  const replizAccounts = replizData?.accounts || [];

  return (
    <div className="flex flex-col gap-6 max-w-[1240px] w-full">
      {/* ── Top Header ── */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1 min-w-0">
          <span className="text-xs font-semibold tracking-wider uppercase text-text2">
            Content &amp; channels
          </span>
          <h1 className="text-2xl sm:text-[26px] font-bold tracking-tight text-text">
            Brands
          </h1>
          <p className="text-sm text-text2 max-w-2xl leading-relaxed">
            Each brand’s content, channels and settings live here. Every post a brand makes goes to all of its active channels, each at its own time.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Link
            href="/brands/channels/new"
            className="h-11 px-4 rounded-xl border-0 bg-pri hover:opacity-90 text-pri-fg text-sm font-semibold inline-flex items-center gap-2 transition cursor-pointer"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            <span>Add channel</span>
          </Link>
        </div>
      </header>

      {/* ── Tabs Navigation ── */}
      <SocialTabs
        accounts={profiles.length + replizAccounts.length}
        devices={devices.length}
      />

      {/* ── Section 1: Phones (Tailscale) ── */}
      <section aria-labelledby="phones-h" className="flex flex-col gap-3">
        <div className="flex flex-wrap justify-between items-end gap-2">
          <div className="flex flex-col gap-0.5 min-w-0">
            <h2 id="phones-h" className="text-lg font-bold text-text">
              Phones (Tailscale)
            </h2>
            <p className="text-xs text-text2 leading-relaxed">
              Each phone is signed in as exactly one Facebook profile and posts the channels it is assigned to, in their priority order.
            </p>
          </div>
          <Link
            href="/settings#devices"
            className="h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5 transition"
          >
            <Icon icon="solar:smartphone-bold" width={14} />
            <span>Manage devices</span>
          </Link>
        </div>

        {loadingDevices && (
          <div className="p-8 text-center text-xs text-text2 flex items-center justify-center gap-2">
            <Icon icon="solar:refresh-bold-duotone" width={18} className="animate-spin text-pri" />
            <span>Loading phone devices…</span>
          </div>
        )}

        {devicesError && !loadingDevices && (
          <div className="p-4 rounded-xl border border-bad/30 bg-bad-soft text-bad text-xs">
            <p className="font-semibold">Could not load devices</p>
            <p className="mt-0.5">{getErrorMessage(devicesError)}</p>
          </div>
        )}

        {!loadingDevices && !devicesError && devices.length === 0 && (
          <div className="bg-surface border border-line rounded-2xl p-8 text-center text-xs text-text2">
            No phone devices registered yet. Connect an Android phone via Tailscale ADB in Settings.
          </div>
        )}

        {/* Phones Grid */}
        {!loadingDevices && !devicesError && devices.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {devices.map((p) => {
              const isAvailable = p.status === "available";
              const isMaintenance = p.status === "maintenance";
              const isCalibrated = Boolean(p.calibration_verified_at);

              // Find assignments for this phone
              const phoneAssignments = assignments.filter((a) => a.device_id === p.id && a.is_active);

              return (
                <article
                  key={p.id}
                  aria-label={p.name}
                  className="bg-surface border border-line rounded-2xl p-4 flex flex-col gap-3 shadow-sm"
                >
                  <div className="flex items-start gap-3">
                    <span
                      aria-hidden="true"
                      className="w-10 h-10 rounded-xl bg-surface2 text-text flex items-center justify-center shrink-0"
                    >
                      <Icon icon="solar:smartphone-bold" width={20} />
                    </span>

                    <div className="flex-1 min-w-0 flex flex-col">
                      <h3 className="text-sm font-bold text-text truncate">{p.name}</h3>
                      <span className="font-mono text-xs text-text2 truncate">
                        {p.tailscale_ip || "No IP"}
                      </span>
                    </div>

                    <span
                      className={`h-6 px-2.5 rounded-full inline-flex items-center gap-1.5 text-xs font-semibold shrink-0 ${
                        isAvailable
                          ? "bg-pri-soft text-pri-text"
                          : isMaintenance
                          ? "bg-warn-soft text-warn"
                          : "bg-info-soft text-info"
                      }`}
                    >
                      <Icon
                        icon={isAvailable ? "solar:check-circle-bold" : "solar:clock-circle-bold"}
                        width={13}
                      />
                      <span>{isAvailable ? "Available" : isMaintenance ? "Maintenance" : p.status}</span>
                    </span>
                  </div>

                  <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
                    <dt className="text-text2">Signed in as</dt>
                    <dd className="m-0 font-semibold text-text truncate">
                      {p.expected_profile_name || "—"}
                    </dd>

                    <dt className="text-text2">Last seen</dt>
                    <dd className="m-0 font-semibold text-text">
                      {formatRelativeTime(p.last_seen_at)}
                    </dd>

                    <dt className="text-text2">Calibration</dt>
                    <dd
                      className={`m-0 font-semibold flex items-center gap-1 ${
                        isCalibrated ? "text-pri" : "text-warn"
                      }`}
                    >
                      <Icon
                        icon={isCalibrated ? "solar:check-circle-bold" : "solar:danger-triangle-bold"}
                        width={13}
                      />
                      <span>{isCalibrated ? "Calibrated (dry-run passed)" : "Needs dry-run"}</span>
                    </dd>

                    <dt className="text-text2">FB App Version</dt>
                    <dd className="m-0 font-mono text-text truncate">
                      {p.facebook_app_version || "—"}
                    </dd>
                  </dl>

                  <div className="flex flex-col gap-1.5 pt-1 border-t border-line">
                    <span className="text-[11px] font-semibold text-text2">
                      Assigned to ({phoneAssignments.length})
                    </span>
                    {phoneAssignments.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {phoneAssignments.map((a) => {
                          const dest = destinationMap.get(a.destination_id);
                          return (
                            <span
                              key={a.id}
                              className="h-6 px-2 rounded-lg bg-surface2 text-text text-xs font-medium inline-flex items-center gap-1"
                            >
                              <Icon icon="logos:facebook" width={11} className="text-pri" />
                              <span>{dest?.name || `Channel #${a.destination_id}`}</span>
                            </span>
                          );
                        })}
                      </div>
                    ) : (
                      <span className="text-xs text-text2 italic">No channel assigned</span>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}

        <span className="text-xs text-text2 leading-relaxed">
          Available = ready to post · In use = posting now, locked for up to 45 min · Maintenance = not used for posting. Each phone is signed in as exactly one Facebook profile.
        </span>
      </section>

      {/* ── Section 2: Profiles & Repliz Grid ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {/* Facebook Profiles Column */}
        <section
          aria-labelledby="profiles-h"
          className="bg-surface border border-line rounded-2xl p-4 sm:p-5 flex flex-col gap-4 shadow-sm"
        >
          <div className="flex flex-wrap justify-between items-start gap-2">
            <div className="flex flex-col gap-0.5">
              <h2 id="profiles-h" className="text-base font-bold text-text">
                Facebook profiles ({profiles.length})
              </h2>
              <p className="text-xs text-text2 max-w-md leading-relaxed">
                A profile is a Profile channel of one brand. A phone is signed in as it and posts its Pages by switching to them; those Pages may belong to any brand. Repliz can never post a profile.
              </p>
            </div>
            <Link
              href="/brands/channels/new"
              className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5 transition"
            >
              <Icon icon="solar:add-circle-bold" width={14} />
              <span>Add profile</span>
            </Link>
          </div>

          {loadingDestinations && (
            <div className="py-8 text-center text-xs text-text2">Loading profiles…</div>
          )}

          {destinationsError && (
            <div className="p-3 rounded-xl bg-bad-soft text-bad text-xs">
              {getErrorMessage(destinationsError)}
            </div>
          )}

          {!loadingDestinations && profiles.length === 0 && (
            <div className="p-6 text-center text-xs text-text2 italic border border-dashed border-line rounded-xl">
              No Facebook profiles added yet.
            </div>
          )}

          {!loadingDestinations &&
            profiles.map((profile) => {
              const childPages = destinations.filter(
                (d) => d.kind === "facebook_page" && d.parent_profile_id === profile.id
              );
              const brand = projectMap.get(profile.content_project_id);

              const profileAssignments = assignments.filter(
                (a) => a.destination_id === profile.id && a.is_active
              );
              const assignedPhoneNames = profileAssignments
                .map((a) => devices.find((d) => d.id === a.device_id)?.name)
                .filter(Boolean);

              return (
                <article
                  key={profile.id}
                  className="border border-line rounded-xl p-4 flex flex-col gap-3 bg-surface"
                >
                  <div className="flex items-start gap-3">
                    <span
                      aria-hidden="true"
                      className="w-10 h-10 rounded-full bg-info-soft text-info flex items-center justify-center shrink-0"
                    >
                      <Icon icon="solar:user-bold" width={20} />
                    </span>

                    <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-bold text-text truncate">{profile.name}</h3>
                        <span
                          className={`h-5 px-2 rounded-full inline-flex items-center gap-1 text-[11px] font-semibold ${
                            profile.is_active
                              ? "bg-pri-soft text-pri-text"
                              : "bg-surface2 text-text2 border border-line"
                          }`}
                        >
                          <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" />
                          <span>{profile.is_active ? "Active" : "Paused"}</span>
                        </span>
                      </div>

                      <span className="text-xs text-text2 flex items-center gap-1">
                        <span>Profile channel of</span>
                        <strong className="text-text font-semibold">{brand?.name || "Brand"}</strong>
                      </span>

                      <span className="font-mono text-[11px] text-text2">
                        ID {profile.facebook_actor_id || "Not assigned"}
                      </span>
                    </div>
                  </div>

                  <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
                    <dt className="text-text2">Phones signed in</dt>
                    <dd className="m-0 font-semibold text-text truncate">
                      {assignedPhoneNames.join(", ") || "none yet"}
                    </dd>

                    <dt className="text-text2">Managed Pages</dt>
                    <dd className="m-0 font-semibold text-text truncate">
                      {childPages.length} {childPages.length === 1 ? "page" : "pages"}
                    </dd>
                  </dl>

                  {/* Active vs Paused Explanation */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                    <div
                      className={`p-2.5 rounded-lg border flex flex-col gap-1 ${
                        profile.is_active
                          ? "border-pri/40 bg-pri-soft/40 text-text"
                          : "border-line bg-surface2 text-text2"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-text">Active</span>
                        {profile.is_active && (
                          <span className="px-1.5 py-0.2 rounded-full bg-pri text-pri-fg text-[10px] font-bold">
                            Now
                          </span>
                        )}
                      </div>
                      <span className="text-[11px] leading-tight">
                        Posts on its own timeline ({profile.daily_target}/day) and manages its Pages.
                      </span>
                    </div>

                    <div
                      className={`p-2.5 rounded-lg border flex flex-col gap-1 ${
                        !profile.is_active
                          ? "border-pri/40 bg-pri-soft/40 text-text"
                          : "border-line bg-surface2 text-text2"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-text">Paused</span>
                        {!profile.is_active && (
                          <span className="px-1.5 py-0.2 rounded-full bg-pri text-pri-fg text-[10px] font-bold">
                            Now
                          </span>
                        )}
                      </div>
                      <span className="text-[11px] leading-tight">
                        Does not post on timeline; phone still posts its managed Pages.
                      </span>
                    </div>
                  </div>

                  {/* Pages this profile manages */}
                  <div className="space-y-2.5 pt-1">
                    <div className="flex items-center justify-between">
                      <h4 className="text-xs font-bold uppercase tracking-wider text-text">
                        Pages this profile manages ({childPages.length})
                      </h4>
                    </div>

                    {childPages.length === 0 ? (
                      <div className="p-3 rounded-xl bg-surface2 border border-line text-xs text-text2 italic">
                        No managed pages under this profile.
                      </div>
                    ) : (
                      <div className="overflow-x-auto rounded-xl border border-line">
                        <table className="w-full text-xs text-left min-w-[560px]">
                          <thead>
                            <tr className="bg-surface2 text-text2 border-b border-line font-semibold">
                              <th scope="col" className="py-2.5 px-3">
                                Page
                              </th>
                              <th scope="col" className="py-2.5 px-3">
                                Brand
                              </th>
                              <th scope="col" className="py-2.5 px-3">
                                Page ID on phone
                              </th>
                              <th scope="col" className="py-2.5 px-3">
                                Identity check
                              </th>
                              <th scope="col" className="py-2.5 px-3">
                                Phone posts / day
                              </th>
                              <th scope="col" className="py-2.5 px-3 text-right">
                                Action
                              </th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-line bg-surface">
                            {childPages.map((page) => {
                              const pageVerified = Boolean(page.identity_verified_at);
                              return (
                                <tr key={page.id} className="hover:bg-surface2 transition-colors">
                                <td className="py-2.5 px-3 font-semibold text-text">
                                  {page.name}
                                </td>
                                <td className="py-2.5 px-3 text-text2">
                                  <Link
                                    href={`/brands/${page.content_project_id}`}
                                    className="text-pri hover:underline"
                                  >
                                    {projectMap.get(page.content_project_id)?.name || "—"}
                                  </Link>
                                </td>
                                <td className="py-2.5 px-3 font-mono text-text">
                                  {page.facebook_actor_id || "—"}
                                </td>
                                <td className="py-2.5 px-3">
                                  {pageVerified ? (
                                    <span className="h-5 px-2 rounded-full inline-flex items-center gap-1 text-[11px] font-semibold bg-pri-soft text-pri-text">
                                      Verified
                                    </span>
                                  ) : (
                                    <span className="h-5 px-2 rounded-full inline-flex items-center gap-1 text-[11px] font-semibold bg-warn-soft text-warn">
                                      Not verified
                                    </span>
                                  )}
                                </td>
                                <td className="py-2.5 px-3 text-text font-medium">
                                  {page.phone_daily_target} / day
                                </td>
                                <td className="py-2.5 px-3 text-right">
                                  <Link
                                    href={`/brands/channels/${page.id}`}
                                    className="text-pri hover:underline font-medium inline-flex items-center gap-1"
                                  >
                                    Edit
                                  </Link>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}

                  <p className="text-[11px] text-text2">
                    A Page route on the phone stays off until the identity check and the App Card
                    dry-run both pass.
                  </p>
                </div>

                  <div className="pt-1">
                    <Link
                      href={`/brands/channels/${profile.id}`}
                      className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5 transition"
                    >
                      <span>Open Profile channel</span>
                      <Icon icon="solar:alt-arrow-right-bold" width={12} />
                    </Link>
                  </div>
                </article>
              );
            })}
        </section>

        {/* Repliz Accounts Column */}
        <section
          aria-labelledby="repliz-acc-h"
          className="bg-surface border border-line rounded-2xl p-4 sm:p-5 flex flex-col gap-4 shadow-sm"
        >
          <div className="flex flex-wrap justify-between items-start gap-2">
            <div className="flex flex-col gap-0.5">
              <h2 id="repliz-acc-h" className="text-base font-bold text-text">
                Repliz accounts ({replizAccounts.length})
              </h2>
              <p className="text-xs text-text2 leading-relaxed">
                Synced from Repliz every 6 h. One channel per account.
              </p>
            </div>
            <Link
              href="/settings"
              className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5 transition"
            >
              <Icon icon="solar:settings-bold" width={14} />
              <span>Repliz settings</span>
            </Link>
          </div>

          {loadingRepliz && (
            <div className="py-8 text-center text-xs text-text2">Loading Repliz accounts…</div>
          )}

          {replizFetchError && (
            <div className="p-3 rounded-xl bg-bad-soft text-bad text-xs">
              {getErrorMessage(replizFetchError)}
            </div>
          )}

          {!loadingRepliz && replizAccounts.length === 0 && (
            <div className="p-6 text-center text-xs text-text2 italic border border-dashed border-line rounded-xl">
              No Repliz accounts connected. Link your credentials in Settings.
            </div>
          )}

          {!loadingRepliz && replizAccounts.length > 0 && (
            <ul className="m-0 p-0 list-none flex flex-col divide-y divide-line border border-line rounded-xl overflow-hidden">
              {replizAccounts.map((r) => (
                <li
                  key={r.id}
                  className="p-3 bg-surface hover:bg-surface2 transition flex flex-wrap items-center gap-3 text-xs"
                >
                  {r.picture ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={r.picture}
                      alt={r.name}
                      className="w-8 h-8 rounded-lg object-cover shrink-0"
                    />
                  ) : (
                    <span
                      aria-hidden="true"
                      className="w-8 h-8 rounded-lg bg-info-soft text-info flex items-center justify-center shrink-0 font-bold"
                    >
                      {getInitials(r.name)}
                    </span>
                  )}

                  <div className="flex-1 min-w-[180px] flex flex-col gap-0.5">
                    <span className="font-semibold text-text text-sm">
                      {r.name}{" "}
                      <span className="font-normal text-text2 text-xs">· {r.type}</span>
                    </span>
                    <span className="text-text2">
                      {r.used_by_destination_name && r.used_by_destination_id ? (
                        <span>
                          Used by{" "}
                          <Link
                            href={`/brands/channels/${r.used_by_destination_id}`}
                            className="font-semibold text-pri hover:underline"
                          >
                            {r.used_by_destination_name}
                          </Link>
                        </span>
                      ) : (
                        <span className="italic text-text2">Not used by any channel</span>
                      )}
                    </span>
                  </div>

                  <span className="h-6 px-2.5 rounded-full inline-flex items-center gap-1.5 text-xs font-semibold bg-pri-soft text-pri-text shrink-0">
                    <Icon icon="solar:check-circle-bold" width={12} />
                    <span>Connected</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* ── Section 3: Interactive Account & Device Management ── */}
      <section aria-label="Device assignments" className="pt-2">
        <AccountDevicesCard
          destinations={destinations}
          devices={devices}
          assignments={assignments}
          mutateAssignments={mutateAssignments}
          mutateDestinations={mutateDestinations}
          mutateDevices={mutateDevices}
        />
      </section>
    </div>
  );
}
