import { api } from "./api";

export interface AudienceCountryOption {
  code: string | null;
  label: string;
  defaultTimezone?: string;
}

export const AUDIENCE_COUNTRIES: AudienceCountryOption[] = [
  { code: null, label: "— None (Global) —", defaultTimezone: "Europe/London" },
  { code: "GB", label: "GB (United Kingdom)", defaultTimezone: "Europe/London" },
  { code: "US", label: "US (United States)", defaultTimezone: "America/New_York" },
  { code: "FR", label: "FR (France)", defaultTimezone: "Europe/Paris" },
  { code: "ID", label: "ID (Indonesia)", defaultTimezone: "Asia/Jakarta" },
  { code: "AU", label: "AU (Australia)", defaultTimezone: "Australia/Sydney" },
  { code: "DE", label: "DE (Germany)", defaultTimezone: "Europe/Berlin" },
  { code: "IN", label: "IN (India)", defaultTimezone: "Asia/Kolkata" },
  { code: "IT", label: "IT (Italy)", defaultTimezone: "Europe/Rome" },
];

export const COUNTRY_TIMEZONE_MAP: Record<string, string> = {
  GB: "Europe/London",
  US: "America/New_York",
  FR: "Europe/Paris",
  ID: "Asia/Jakarta",
  AU: "Australia/Sydney",
  DE: "Europe/Berlin",
  IN: "Asia/Kolkata",
  IT: "Europe/Rome",
};

export const CAPTION_LANGUAGES = [
  { code: "en", label: "English (en)" },
  { code: "fr", label: "French (fr)" },
  { code: "es", label: "Spanish (es)" },
  { code: "de", label: "German (de)" },
  { code: "it", label: "Italian (it)" },
  { code: "pt", label: "Portuguese (pt)" },
  { code: "id", label: "Indonesian (id)" },
  { code: "nl", label: "Dutch (nl)" },
];

export interface BrandCreateInput {
  name: string;
  timezone?: string;
  target_country?: string | null;
  caption_language?: string | null;
  caption_tone?: string | null;
}

export interface BrandCreateOut {
  id: number;
  name: string;
  repliz_account_id?: string | null;
  is_connected: boolean;
  is_active: boolean;
  timezone: string;
  target_country?: string | null;
  caption_language?: string | null;
  caption_tone?: string | null;
  destination_count: number;
  created_at: string;
  updated_at: string;
}

export async function createBrand(payload: BrandCreateInput): Promise<BrandCreateOut> {
  const res = await api.post<BrandCreateOut>("/content-projects", payload);
  return res.data;
}
