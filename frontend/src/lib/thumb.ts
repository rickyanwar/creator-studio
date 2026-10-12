const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "/api").replace(/\/$/, "");

export function getThumbUrl(url: string | null | undefined, width: 240 | 360 | 480 = 480): string | null {
  if (!url) return null;
  return `${API_BASE}/media/thumb?u=${encodeURIComponent(url)}&w=${width}`;
}
