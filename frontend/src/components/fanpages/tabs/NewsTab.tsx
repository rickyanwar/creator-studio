"use client";

import { Icon } from "@iconify/react";
import type { FanpageDetail } from "@/lib/types";

export interface NewsTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  allNewsSources: Array<{ id: number; name: string; category_url: string }>;
  allGalleryNiches: string[];
  toggleNewsSource: (sourceId: number, subscribed: boolean) => Promise<void>;
  newsPreviewTitle: string;
  setNewsPreviewTitle: (v: string) => void;
  newsPreviewContent: string;
  setNewsPreviewContent: (v: string) => void;
  newsPreviewResult: { title: string; caption: string } | null;
  newsPreviewLoading: boolean;
  handleNewsPreview: () => Promise<void>;
}

export function NewsTab({
  form,
  set,
  allNewsSources,
  allGalleryNiches,
  toggleNewsSource,
  newsPreviewTitle,
  setNewsPreviewTitle,
  newsPreviewContent,
  setNewsPreviewContent,
  newsPreviewResult,
  newsPreviewLoading,
  handleNewsPreview,
}: NewsTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-news" aria-labelledby="tab-news" className="space-y-8">
      {/* ── Section: Mode 2 — News Content ─────────────── */}
      <section className="card space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text">News</h2>
            <p className="text-xs text-text2 mt-0.5">
              Scraped news → AI copywriter → image design → publish
            </p>
          </div>
          <button
            onClick={() => set("mode2_news_content_enabled", !form.mode2_news_content_enabled)}
            className={`relative w-11 h-6 rounded-full transition-colors ${
              form.mode2_news_content_enabled ? "bg-pri" : "bg-line"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.mode2_news_content_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>

        {form.mode2_news_content_enabled && (
          <>
            <div>
              <label className="label">News AI design</label>
              <select
                className="input w-full mt-1"
                value={(form.news_visual_engine as string) ?? "off"}
                onChange={(e) => set("news_visual_engine", e.target.value)}
              >
                <option value="off">Off</option>
                <option value="chatgpt">ChatGPT</option>
              </select>
              <p className="text-[10px] text-text2 mt-1">
                1 ChatGPT image per post; falls back to the normal template on failure.
              </p>
            </div>

            <div className="flex items-center justify-between p-3 rounded-lg border border-line">
              <div>
                <label htmlFor="quote-shorten" className="text-xs font-medium text-text cursor-pointer">
                  Shorten quotes
                </label>
                <p className="text-[11px] text-text2 mt-0.5">
                  Off: quotes stay word-for-word (template shrinks the font). On: the AI may shorten a quote to fit the template, keeping its meaning — never adds words.
                </p>
              </div>
              <input
                id="quote-shorten"
                type="checkbox"
                className="w-4 h-4 rounded accent-pri shrink-0 cursor-pointer ml-3"
                checked={(form.quote_shorten_enabled as boolean) ?? false}
                onChange={(e) => set("quote_shorten_enabled", e.target.checked)}
              />
            </div>

            {/* News source subscriptions */}
            <div>
              <label className="label">News Sources</label>
              {allNewsSources.length === 0 ? (
                <p className="text-xs text-text2">
                  No news sources configured yet — add them on the News Sources page first.
                </p>
              ) : (
                <div className="space-y-1.5">
                  {allNewsSources.map((ns) => {
                    const subscribed = ((form.news_sources as FanpageDetail["news_sources"]) ?? []).some(
                      (s) => s.id === ns.id
                    );
                    return (
                      <label
                        key={ns.id}
                        className="flex items-center gap-3 p-2.5 rounded-lg border border-line hover:border-pri/30 cursor-pointer transition-colors"
                      >
                        <input
                          type="checkbox"
                          className="w-4 h-4 rounded accent-pri"
                          checked={subscribed}
                          onChange={() => toggleNewsSource(ns.id, subscribed)}
                        />
                        <div className="min-w-0">
                          <p className="text-sm text-text font-medium leading-tight">{ns.name}</p>
                          <p className="text-[11px] text-text2 truncate">{ns.category_url}</p>
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Gallery niches */}
            <div>
              <label className="label">Gallery Niches (for image selection)</label>
              <p className="text-[11px] text-text2 mb-2">
                Any image tagged with a keyword under a subscribed niche is eligible — add a new keyword to a
                niche in <a href="/gallery" className="text-pri hover:underline">Gallery</a> and every
                fanpage subscribed to it picks it up automatically, no per-fanpage list to maintain.
              </p>
              {allGalleryNiches.length === 0 ? (
                <p className="text-[11px] text-text2 italic">No niches yet — create gallery keywords with a niche first.</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {allGalleryNiches.map((n) => {
                    const active = ((form.mode2_gallery_niches as string[]) ?? []).includes(n);
                    return (
                      <button
                        key={n}
                        onClick={() => {
                          const cur = (form.mode2_gallery_niches as string[]) ?? [];
                          set("mode2_gallery_niches", active ? cur.filter((v) => v !== n) : [...cur, n]);
                        }}
                        className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${
                          active
                            ? "bg-pri text-pri-fg border-pri"
                            : "border-line text-text2 hover:border-pri hover:text-pri"
                        }`}
                      >
                        {n}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Mode 2 caption criteria note */}
            <div className="border-t border-line pt-4 space-y-4">
              <p className="text-xs font-semibold text-text2 uppercase tracking-wide">
                News Copywriting Criteria
              </p>
              <p className="text-xs text-text2 italic">
                Caption follows Global settings
              </p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Design Title Max Chars</label>
                  <input
                    className="input-rect"
                    type="number"
                    value={(form.mode2_title_max_chars as number) ?? 80}
                    onChange={(e) => set("mode2_title_max_chars", parseInt(e.target.value))}
                  />
                </div>
                <div>
                  <label className="label">News Badge Text</label>
                  <input
                    className="input-rect"
                    value={(form.mode2_badge_text as string) ?? ""}
                    onChange={(e) => set("mode2_badge_text", e.target.value || null)}
                    placeholder={
                      ((form.mode2_gallery_niches as string[]) ?? [])[0]
                        ? `${((form.mode2_gallery_niches as string[]) ?? [])[0].toUpperCase()} NEWS (default from niche)`
                        : "e.g. F1 NEWS"
                    }
                  />
                  <p className="text-[11px] text-text2 mt-1">
                    Small pill badge above the headline on News templates. Leave blank to use the fanpage&apos;s
                    first Gallery Niche + &quot;NEWS&quot; automatically.
                  </p>
                </div>
              </div>
            </div>

            {/* Editorial AI gate */}
            <div className="border-t border-line pt-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-text">AI Editorial Gate</p>
                  <p className="text-xs text-text2 mt-0.5 max-w-md">
                    Before copywriting, 9Router web-searches to fact-check each article and judges whether it&apos;s worth posting (likely engagement) — rejected articles get no post for this fanpage. Adds 2 AI calls per article.
                  </p>
                </div>
                <button
                  onClick={() => set("mode2_editorial_gate_enabled", !form.mode2_editorial_gate_enabled)}
                  className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
                    form.mode2_editorial_gate_enabled ? "bg-pri" : "bg-line"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                      form.mode2_editorial_gate_enabled ? "translate-x-5" : ""
                    }`}
                  />
                </button>
              </div>
            </div>

            {/* News copy preview */}
            <div className="border-t border-line pt-4 space-y-3">
              <p className="text-xs font-semibold text-text2 uppercase tracking-wide">
                Copywriter Preview
              </p>
              <input
                className="input-rect"
                value={newsPreviewTitle}
                onChange={(e) => setNewsPreviewTitle(e.target.value)}
                placeholder="Paste an article title…"
              />
              <textarea
                className="input-rect h-24 resize-none"
                value={newsPreviewContent}
                onChange={(e) => setNewsPreviewContent(e.target.value)}
                placeholder="Paste article content…"
              />
              <button
                onClick={handleNewsPreview}
                disabled={newsPreviewLoading || !newsPreviewTitle || !newsPreviewContent}
                className="btn-primary"
              >
                <Icon icon="solar:magic-stick-3-bold-duotone" width={16} />
                {newsPreviewLoading ? "Generating…" : "Generate Title + Caption"}
              </button>
              {newsPreviewResult && (
                <div className="p-4 bg-surface2 rounded-lg space-y-3">
                  <div>
                    <p className="text-[11px] text-text2 mb-1 font-semibold uppercase tracking-wide">
                      Design Title (goes on the image)
                    </p>
                    <p className="text-sm text-text font-semibold">{newsPreviewResult.title}</p>
                  </div>
                  <div>
                    <p className="text-[11px] text-text2 mb-1 font-semibold uppercase tracking-wide">
                      FB Caption
                    </p>
                    <p className="text-sm text-text whitespace-pre-wrap">{newsPreviewResult.caption}</p>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
