export const GROUP_ORDER = [
  "ig",
  "news",
  "discussion",
  "pinterest",
  "facebook_photo",
  "yt_clip",
  "ai_video",
] as const;

export type GroupId = (typeof GROUP_ORDER)[number] | string;

export const GROUP_LABELS: Record<string, string> = {
  ig: "Instagram",
  news: "News",
  discussion: "Discussion",
  pinterest: "Pinterest",
  facebook_photo: "FB photo",
  yt_clip: "YouTube clips",
  ai_video: "AI video",
};

export function evenShares(groups: string[]): Record<string, number> {
  if (!groups || groups.length === 0) return {};

  const ordered = [...groups].sort((a, b) => {
    const ia = GROUP_ORDER.indexOf(a as any);
    const ib = GROUP_ORDER.indexOf(b as any);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });

  const n = ordered.length;
  const base = Math.floor(100 / n);
  const remainder = 100 % n;

  const result: Record<string, number> = {};
  ordered.forEach((g, idx) => {
    result[g] = base + (idx < remainder ? 1 : 0);
  });
  return result;
}

export function groupQuotas(
  dailyTarget: number,
  shares?: Record<string, number> | null,
  enabledGroups?: string[] | null
): Record<string, number> {
  const target = dailyTarget <= 0 ? 0 : dailyTarget;

  if (!shares || Object.keys(shares).length === 0) {
    return {};
  }

  // Explicit shares are given: match backend and use groups in shares (in GROUP_ORDER)
  const explicitGroups = GROUP_ORDER.filter(
    (g) => g in shares
  );
  if (explicitGroups.length === 0) return {};
  if (target === 0) {
    return Object.fromEntries(explicitGroups.map((g) => [g, 0]));
  }

  const totalShare = explicitGroups.reduce(
    (acc, g) => acc + (shares![g] ?? 0),
    0
  );
  const denom = totalShare > 0 ? totalShare : 100.0;

  const floors: Record<string, number> = {};
  const rem: Record<string, number> = {};

  explicitGroups.forEach((g) => {
    const s = shares![g] ?? 0;
    if (s <= 0) {
      floors[g] = 0;
      rem[g] = -1.0;
    } else {
      const ideal = (target * s) / denom;
      const fl = Math.floor(ideal);
      floors[g] = fl;
      rem[g] = ideal - fl;
    }
  });

  const allocated = Object.values(floors).reduce((a, b) => a + b, 0);
  const remaining = target - allocated;
  if (remaining > 0) {
    const eligible = explicitGroups.filter((g) => (shares![g] ?? 0) > 0);
    const sortedEligible = [...eligible].sort((a, b) => {
      const diff = rem[b] - rem[a];
      if (Math.abs(diff) > 1e-9) return diff;
      return GROUP_ORDER.indexOf(a) - GROUP_ORDER.indexOf(b);
    });
    for (let i = 0; i < remaining && i < sortedEligible.length; i++) {
      floors[sortedEligible[i]] += 1;
    }
  }

  return floors;
}

export function quotaSummary(quotas: Record<string, number>): string {
  if (!quotas) return "0 posts per day";

  const entries = Object.entries(quotas)
    .filter(([_, count]) => count > 0)
    .sort(([a], [b]) => {
      const ia = GROUP_ORDER.indexOf(a as any);
      const ib = GROUP_ORDER.indexOf(b as any);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });

  if (entries.length === 0) return "0 posts per day";

  const parts = entries.map(([group, count]) => {
    const label = GROUP_LABELS[group] ?? group;
    return `${count} ${label}`;
  });

  return `${parts.join(" · ")} per day`;
}
