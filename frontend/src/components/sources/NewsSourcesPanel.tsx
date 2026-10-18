"use client";

import useSWR from "swr";
import { Fragment, useState, useEffect } from "react";
import { Icon } from "@iconify/react";
import { formatDistanceToNowStrict } from "date-fns";
import {
  listNewsSources,
  createNewsSource,
  updateNewsSource,
  deleteNewsSource,
  scrapeNewsSourceNow,
  testNewsListSelector,
  testNewsSelectors,
  testNewsRss,
  listNewsArticles,
} from "@/lib/api";

export type NewsSource = {
  id: number;
  name: string;
  category_url: string;
  is_active: boolean;
  scrape_interval_minutes: number;
  max_age_days: number;
  render_mode: string;
  article_list_selector: string | null;
  article_link_attribute: string;
  title_selector: string | null;
  content_selector: string | null;
  image_selector: string | null;
  date_selector: string | null;
  last_scraped_at: string | null;
  last_scrape_error: string | null;
  article_count: number;
  caption_tone: string | null;
  caption_language: string | null;
  caption_max_length: number | null;
  caption_hashtag_count: number | null;
  caption_cta_text: string | null;
  caption_custom_prompt: string | null;
};

export type Article = {
  id: number;
  article_url: string;
  scraped_title: string;
  content_preview: string;
  scraped_image_url: string | null;
  article_published_at: string | null;
  status: string;
  scraped_at: string | null;
};

const emptyForm = {
  name: "",
  category_url: "",
  is_active: true,
  scrape_interval_minutes: 60,
  max_age_days: 3,
  render_mode: "static",
  article_list_selector: "",
  article_link_attribute: "href",
  title_selector: "",
  content_selector: "",
  image_selector: "",
  date_selector: "",
  caption_tone: "",
  caption_language: "",
  caption_max_length: "",
  caption_hashtag_count: "",
  caption_cta_text: "",
  caption_custom_prompt: "",
};

interface NewsSourcesPanelProps {
  showForm?: boolean;
  setShowForm?: (open: boolean) => void;
  onCountChange?: (count: number) => void;
}

export function NewsSourcesPanel({
  showForm: controlledShowForm,
  setShowForm: setControlledShowForm,
  onCountChange,
}: NewsSourcesPanelProps) {
  const { data: sources = [], isLoading, mutate } = useSWR<NewsSource[]>(
    "news-sources",
    () => listNewsSources().then((r) => r.data as NewsSource[]),
    { refreshInterval: 30000 }
  );

  useEffect(() => {
    if (onCountChange) {
      onCountChange(sources.length);
    }
  }, [sources.length, onCountChange]);

  const [internalShowForm, setInternalShowForm] = useState(false);
  const showForm = controlledShowForm !== undefined ? controlledShowForm : internalShowForm;
  const setShowForm = (val: boolean) => {
    if (setControlledShowForm) setControlledShowForm(val);
    setInternalShowForm(val);
  };

  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [scraping, setScraping] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [articles, setArticles] = useState<Article[]>([]);
  const [articlesLoading, setArticlesLoading] = useState(false);

  // Selector tester state
  const [testUrl, setTestUrl] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [listTesting, setListTesting] = useState(false);
  const [listResult, setListResult] = useState<{ link_count: number; links: string[] } | null>(null);

  // RSS feed tester state (render_mode="rss")
  const [rssTesting, setRssTesting] = useState(false);
  const [rssResult, setRssResult] = useState<{
    item_count: number;
    items: {
      url: string; title: string; content_length: number; content_preview: string;
      image_url: string | null; published_at: string | null; errors: string[];
    }[];
  } | null>(null);
  const [rssError, setRssError] = useState<string | null>(null);

  function set(field: string, value: unknown) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function openCreate() {
    setForm(emptyForm);
    setEditingId(null);
    setTestResult(null);
    setListResult(null);
    setTestError(null);
    setRssResult(null);
    setRssError(null);
    setShowForm(true);
  }

  function openEdit(s: NewsSource) {
    setForm({
      name: s.name,
      category_url: s.category_url,
      is_active: s.is_active,
      scrape_interval_minutes: s.scrape_interval_minutes,
      max_age_days: s.max_age_days,
      render_mode: s.render_mode,
      article_list_selector: s.article_list_selector ?? "",
      article_link_attribute: s.article_link_attribute,
      title_selector: s.title_selector ?? "",
      content_selector: s.content_selector ?? "",
      image_selector: s.image_selector ?? "",
      date_selector: s.date_selector ?? "",
      caption_tone: s.caption_tone ?? "",
      caption_language: s.caption_language ?? "",
      caption_max_length: s.caption_max_length != null ? String(s.caption_max_length) : "",
      caption_hashtag_count: s.caption_hashtag_count != null ? String(s.caption_hashtag_count) : "",
      caption_cta_text: s.caption_cta_text ?? "",
      caption_custom_prompt: s.caption_custom_prompt ?? "",
    });
    setEditingId(s.id);
    setTestResult(null);
    setListResult(null);
    setTestError(null);
    setRssResult(null);
    setRssError(null);
    setShowForm(true);
  }

  async function handleSave() {
    if (!form.name || !form.category_url) {
      alert("Name and category URL are required.");
      return;
    }
    if (form.render_mode !== "rss" && (!form.article_list_selector || !form.title_selector || !form.content_selector)) {
      alert("List selector, title selector and content selector are required for static/JS render modes.");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        ...form,
        image_selector: form.image_selector || null,
        date_selector: form.date_selector || null,
        caption_max_length: form.caption_max_length === "" ? null : parseInt(form.caption_max_length),
        caption_hashtag_count: form.caption_hashtag_count === "" ? null : parseInt(form.caption_hashtag_count),
      };
      if (editingId) {
        await updateNewsSource(editingId, payload);
      } else {
        await createNewsSource(payload);
      }
      setShowForm(false);
      mutate();
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      alert(`Save failed: ${err.response?.data?.detail ?? "unknown error"}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(s: NewsSource) {
    if (!confirm(`Delete "${s.name}" and all its ${s.article_count} scraped articles?`)) return;
    await deleteNewsSource(s.id);
    mutate();
  }

  async function handleScrapeNow(id: number) {
    setScraping(id);
    try {
      await scrapeNewsSourceNow(id);
      alert("Scrape queued — new articles will appear within a few minutes.");
    } finally {
      setScraping(null);
    }
  }

  async function toggleArticles(id: number) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    setArticlesLoading(true);
    try {
      const r = await listNewsArticles(id);
      setArticles(r.data as Article[]);
    } finally {
      setArticlesLoading(false);
    }
  }

  async function handleTestListSelector() {
    setListTesting(true);
    setListResult(null);
    setTestError(null);
    try {
      const r = await testNewsListSelector({
        category_url: form.category_url,
        article_list_selector: form.article_list_selector,
        article_link_attribute: form.article_link_attribute,
        render_mode: form.render_mode,
      });
      setListResult(r.data);
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      setTestError(err.response?.data?.detail ?? "Test failed");
    } finally {
      setListTesting(false);
    }
  }

  async function handleTestSelectors() {
    if (!testUrl) {
      alert("Paste a sample article URL first.");
      return;
    }
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const r = await testNewsSelectors({
        article_url: testUrl,
        title_selector: form.title_selector,
        content_selector: form.content_selector,
        image_selector: form.image_selector || undefined,
        date_selector: form.date_selector || undefined,
        render_mode: form.render_mode,
      });
      setTestResult(r.data);
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      setTestError(err.response?.data?.detail ?? "Test failed");
    } finally {
      setTesting(false);
    }
  }

  async function handleTestRss() {
    if (!form.category_url) {
      alert("Enter the feed URL in Category URL first.");
      return;
    }
    setRssTesting(true);
    setRssResult(null);
    setRssError(null);
    try {
      const r = await testNewsRss({ category_url: form.category_url });
      setRssResult(r.data);
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      setRssError(err.response?.data?.detail ?? "Test failed");
    } finally {
      setRssTesting(false);
    }
  }

  const renderModeLabel = (mode: string) => {
    if (mode === "rss") return "RSS";
    if (mode === "js") return "JS page";
    return "Web page";
  };

  return (
    <div className="space-y-4">
      {/* ── Error log banner (surfaces scrape errors at the top) ── */}
      {sources.some((s) => s.last_scrape_error) && (
        <div className="rounded-xl border border-bad/30 bg-bad-soft p-4 space-y-2 text-bad">
          <div className="flex items-center gap-2 font-semibold text-sm">
            <Icon icon="solar:danger-triangle-bold-duotone" width={18} />
            <span>{sources.filter((s) => s.last_scrape_error).length} source(s) with scrape errors</span>
          </div>
          <ul className="space-y-1.5">
            {sources.filter((s) => s.last_scrape_error).map((s) => (
              <li key={s.id} className="text-xs flex flex-col gap-0.5 border-t border-bad/10 pt-1.5 first:border-0 first:pt-0">
                <span className="font-semibold text-text">{s.name}</span>
                <span className="font-mono break-words whitespace-pre-wrap text-bad">{s.last_scrape_error}</span>
                {s.last_scraped_at && (
                  <span className="text-text2">last tried {formatDistanceToNowStrict(new Date(s.last_scraped_at), { addSuffix: true })}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap gap-4 items-start">
        {/* Table section */}
        <section className="flex-[2_1_520px] min-w-0 bg-surface border border-line rounded-xl overflow-hidden">
          {isLoading ? (
            <div className="p-8 text-center text-sm text-text2">Loading news sources…</div>
          ) : sources.length === 0 ? (
            <div className="p-10 text-center text-text2 space-y-2">
              <Icon icon="solar:documents-bold-duotone" width={40} className="mx-auto opacity-40 text-text2" />
              <p className="text-sm">No news sources yet.</p>
              <button
                type="button"
                onClick={openCreate}
                className="mt-2 h-9 px-4 rounded-lg bg-pri text-pri-fg font-medium text-xs"
              >
                Add news site
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] border-collapse text-sm">
                <thead>
                  <tr className="text-left text-xs text-text2 border-b border-line bg-surface2/50">
                    <th scope="col" className="py-2.5 px-4 font-semibold">Site</th>
                    <th scope="col" className="py-2.5 px-3 font-semibold">Reads</th>
                    <th scope="col" className="py-2.5 px-3 font-semibold">Articles</th>
                    <th scope="col" className="py-2.5 px-3 font-semibold">Last read</th>
                    <th scope="col" className="py-2.5 px-3 font-semibold">Status</th>
                    <th scope="col" className="py-2.5 px-4 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {sources.map((s) => (
                    <Fragment key={s.id}>
                      <tr className="hover:bg-surface2/40 transition-colors">
                        <td className="py-3 px-4">
                          <div className="flex flex-col">
                            <span className="font-semibold text-text">{s.name}</span>
                            <a
                              href={s.category_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-xs text-text2 hover:text-pri-text truncate max-w-[240px]"
                            >
                              {s.category_url}
                            </a>
                          </div>
                        </td>
                        <td className="py-3 px-3 text-xs text-text2 whitespace-nowrap">
                          {renderModeLabel(s.render_mode)} · {s.scrape_interval_minutes}m
                        </td>
                        <td className="py-3 px-3 tabular-nums">
                          <button
                            type="button"
                            onClick={() => toggleArticles(s.id)}
                            className="text-pri-text hover:underline font-medium inline-flex items-center gap-1"
                          >
                            <span>{s.article_count}</span>
                            <span className="text-[10px]">{expandedId === s.id ? "▾" : "▸"}</span>
                          </button>
                        </td>
                        <td className="py-3 px-3 text-xs text-text2 whitespace-nowrap">
                          {s.last_scraped_at
                            ? formatDistanceToNowStrict(new Date(s.last_scraped_at), { addSuffix: true })
                            : "never"}
                        </td>
                        <td className="py-3 px-3 whitespace-nowrap">
                          {!s.is_active ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2 border border-line">
                              Paused
                            </span>
                          ) : s.last_scrape_error ? (
                            <span
                              className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-bad-soft text-bad"
                              title={s.last_scrape_error}
                            >
                              Error
                            </span>
                          ) : (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                              Active
                            </span>
                          )}
                        </td>
                        <td className="py-3 px-4 text-right whitespace-nowrap">
                          <div className="inline-flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => handleScrapeNow(s.id)}
                              disabled={scraping === s.id}
                              title="Scrape now"
                              className="p-1.5 rounded-lg text-text2 hover:text-text hover:bg-surface2 transition-colors disabled:opacity-50"
                            >
                              {scraping === s.id ? (
                                <Icon icon="svg-spinners:ring-resize" width={16} />
                              ) : (
                                <Icon icon="solar:play-circle-bold-duotone" width={16} />
                              )}
                            </button>
                            <button
                              type="button"
                              onClick={() => updateNewsSource(s.id, { is_active: !s.is_active }).then(() => mutate())}
                              title={s.is_active ? "Pause" : "Resume"}
                              className="p-1.5 rounded-lg text-text2 hover:text-text hover:bg-surface2 transition-colors"
                            >
                              <Icon icon={s.is_active ? "solar:pause-circle-bold-duotone" : "solar:play-bold-duotone"} width={16} />
                            </button>
                            <button
                              type="button"
                              onClick={() => openEdit(s)}
                              title="Edit"
                              className="p-1.5 rounded-lg text-text2 hover:text-text hover:bg-surface2 transition-colors"
                            >
                              <Icon icon="solar:pen-bold-duotone" width={16} />
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDelete(s)}
                              title="Delete"
                              className="p-1.5 rounded-lg text-text2 hover:text-bad hover:bg-bad-soft transition-colors"
                            >
                              <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                            </button>
                          </div>
                        </td>
                      </tr>
                      {expandedId === s.id && (
                        <tr>
                          <td colSpan={6} className="bg-surface2/30 px-5 py-4 border-t border-line">
                            {articlesLoading ? (
                              <p className="text-xs text-text2">Loading articles…</p>
                            ) : articles.length === 0 ? (
                              <p className="text-xs text-text2">No articles scraped yet.</p>
                            ) : (
                              <div className="space-y-2">
                                <p className="text-xs font-semibold text-text">Recent scraped articles ({articles.length}):</p>
                                {articles.map((a) => (
                                  <div key={a.id} className="flex items-start gap-3 rounded-lg bg-surface border border-line p-3">
                                    {a.scraped_image_url && (
                                      /* eslint-disable-next-line @next/next/no-img-element */
                                      <img src={a.scraped_image_url} alt="" className="w-16 h-16 rounded object-cover shrink-0 border border-line" />
                                    )}
                                    <div className="min-w-0 flex-1">
                                      <a href={a.article_url} target="_blank" rel="noreferrer" className="text-sm font-semibold text-text hover:text-pri-text line-clamp-1">
                                        {a.scraped_title}
                                      </a>
                                      <p className="text-xs text-text2 line-clamp-2 mt-0.5">{a.content_preview}</p>
                                      <p className="text-[11px] text-text2 mt-1">
                                        <span className="uppercase font-semibold text-pri-text">{a.status}</span>
                                        {a.article_published_at && <> · published {formatDistanceToNowStrict(new Date(a.article_published_at), { addSuffix: true })}</>}
                                        {a.scraped_at && <> · scraped {formatDistanceToNowStrict(new Date(a.scraped_at), { addSuffix: true })}</>}
                                      </p>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Drawer / Aside Form */}
        {showForm && (
          <aside aria-label="Add news site" className="flex-[1_1_340px] min-w-0 bg-surface border border-line rounded-xl p-5 space-y-4 shadow-sm">
            <div className="flex justify-between items-center pb-2 border-b border-line">
              <h2 className="text-base font-bold text-text">
                {editingId ? "Edit news site" : "Add news site"}
              </h2>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="h-8 px-2.5 rounded-lg border border-line bg-surface text-text text-xs font-medium hover:bg-surface2 transition-colors"
              >
                Close
              </button>
            </div>

            <label className="flex flex-col gap-1 text-xs font-semibold text-text">
              Name
              <input
                type="text"
                className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="Autosport"
              />
            </label>

            <div className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-text">How to read it</span>
              <div role="radiogroup" aria-label="How to read it" className="grid grid-cols-3 gap-2">
                <button
                  type="button"
                  onClick={() => set("render_mode", "rss")}
                  className={`flex flex-col gap-0.5 p-2 rounded-lg text-left border transition-colors ${
                    form.render_mode === "rss"
                      ? "border-pri bg-pri-soft text-text font-semibold"
                      : "border-line bg-surface text-text hover:bg-surface2"
                  }`}
                >
                  <span className="text-xs font-semibold">RSS feed</span>
                  <span className="text-[11px] text-text2 font-normal">Easiest</span>
                </button>
                <button
                  type="button"
                  onClick={() => set("render_mode", "static")}
                  className={`flex flex-col gap-0.5 p-2 rounded-lg text-left border transition-colors ${
                    form.render_mode === "static"
                      ? "border-pri bg-pri-soft text-text font-semibold"
                      : "border-line bg-surface text-text hover:bg-surface2"
                  }`}
                >
                  <span className="text-xs font-semibold">Web page</span>
                  <span className="text-[11px] text-text2 font-normal">Selectors</span>
                </button>
                <button
                  type="button"
                  onClick={() => set("render_mode", "js")}
                  className={`flex flex-col gap-0.5 p-2 rounded-lg text-left border transition-colors ${
                    form.render_mode === "js"
                      ? "border-pri bg-pri-soft text-text font-semibold"
                      : "border-line bg-surface text-text hover:bg-surface2"
                  }`}
                >
                  <span className="text-xs font-semibold">JS page</span>
                  <span className="text-[11px] text-text2 font-normal">Browser</span>
                </button>
              </div>
            </div>

            <label className="flex flex-col gap-1 text-xs font-semibold text-text">
              {form.render_mode === "rss" ? "Feed URL" : "Category URL"}
              <input
                type="text"
                className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
                value={form.category_url}
                onChange={(e) => set("category_url", e.target.value)}
                placeholder={form.render_mode === "rss" ? "https://www.autosport.com/rss/motogp/news/" : "https://www.autosport.com/motogp/news/"}
              />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs font-semibold text-text">
                Read every (min)
                <input
                  type="number"
                  min={15}
                  className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
                  value={form.scrape_interval_minutes}
                  onChange={(e) => set("scrape_interval_minutes", parseInt(e.target.value) || 60)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs font-semibold text-text">
                Skip older than (days)
                <input
                  type="number"
                  min={1}
                  className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-normal focus:outline-none focus:ring-1 focus:ring-pri"
                  value={form.max_age_days}
                  onChange={(e) => set("max_age_days", parseInt(e.target.value) || 3)}
                />
              </label>
            </div>

            {form.render_mode === "rss" ? (
              <div className="border-t border-line pt-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-text">RSS feed test</span>
                  <button
                    type="button"
                    onClick={handleTestRss}
                    disabled={rssTesting || !form.category_url}
                    className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-xs font-medium inline-flex items-center gap-1.5 disabled:opacity-50"
                  >
                    {rssTesting ? <Icon icon="svg-spinners:ring-resize" width={14} /> : <Icon icon="solar:test-tube-bold-duotone" width={14} />}
                    <span>Test feed</span>
                  </button>
                </div>
                {rssError && (
                  <p className="text-xs text-bad bg-bad-soft border border-bad/20 rounded-lg p-2">{rssError}</p>
                )}
                {rssResult && (
                  <div className="rounded-lg bg-surface2 border border-line p-2.5 text-xs text-text space-y-2 max-h-48 overflow-y-auto">
                    <p className="font-semibold">{rssResult.item_count} item(s) found:</p>
                    {rssResult.items.slice(0, 5).map((it) => (
                      <div key={it.url} className="border-t border-line pt-1.5 first:border-0 first:pt-0">
                        <a href={it.url} target="_blank" rel="noreferrer" className="font-semibold text-pri-text hover:underline block truncate">
                          {it.title || "— no title —"}
                        </a>
                        <p className="text-[11px] text-text2">{it.content_length} chars</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="border-t border-line pt-3 space-y-3">
                <span className="text-xs font-semibold text-text">Article extraction selectors</span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <label className="flex flex-col gap-1 text-xs text-text">
                    List selector
                    <input
                      type="text"
                      className="h-8 px-2 rounded-lg border border-line bg-surface text-xs focus:ring-1 focus:ring-pri"
                      value={form.article_list_selector}
                      onChange={(e) => set("article_list_selector", e.target.value)}
                      placeholder="article.post h2 a"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-text">
                    Link attr
                    <input
                      type="text"
                      className="h-8 px-2 rounded-lg border border-line bg-surface text-xs focus:ring-1 focus:ring-pri"
                      value={form.article_link_attribute}
                      onChange={(e) => set("article_link_attribute", e.target.value)}
                      placeholder="href"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-text">
                    Title selector
                    <input
                      type="text"
                      className="h-8 px-2 rounded-lg border border-line bg-surface text-xs focus:ring-1 focus:ring-pri"
                      value={form.title_selector}
                      onChange={(e) => set("title_selector", e.target.value)}
                      placeholder="h1.article-title"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-text">
                    Content selector
                    <input
                      type="text"
                      className="h-8 px-2 rounded-lg border border-line bg-surface text-xs focus:ring-1 focus:ring-pri"
                      value={form.content_selector}
                      onChange={(e) => set("content_selector", e.target.value)}
                      placeholder="div.article-body"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-text">
                    Image selector
                    <input
                      type="text"
                      className="h-8 px-2 rounded-lg border border-line bg-surface text-xs focus:ring-1 focus:ring-pri"
                      value={form.image_selector}
                      onChange={(e) => set("image_selector", e.target.value)}
                      placeholder="div.article-body img"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-text">
                    Date selector
                    <input
                      type="text"
                      className="h-8 px-2 rounded-lg border border-line bg-surface text-xs focus:ring-1 focus:ring-pri"
                      value={form.date_selector}
                      onChange={(e) => set("date_selector", e.target.value)}
                      placeholder="time.published"
                    />
                  </label>
                </div>

                <div className="flex items-center gap-2 pt-1">
                  <button
                    type="button"
                    onClick={handleTestListSelector}
                    disabled={listTesting || !form.category_url || !form.article_list_selector}
                    className="h-8 px-2.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-xs font-medium inline-flex items-center gap-1 disabled:opacity-50"
                  >
                    {listTesting ? <Icon icon="svg-spinners:ring-resize" width={14} /> : <Icon icon="solar:test-tube-bold-duotone" width={14} />}
                    <span>Test list</span>
                  </button>
                </div>
                {listResult && (
                  <div className="rounded-lg bg-surface2 border border-line p-2 text-xs text-text max-h-32 overflow-y-auto">
                    <p className="font-semibold">{listResult.link_count} link(s) found:</p>
                    <ul className="space-y-0.5 mt-1">
                      {listResult.links.slice(0, 5).map((l) => (
                        <li key={l} className="truncate text-pri-text">{l}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Tester for article URL */}
                <div className="rounded-lg border border-dashed border-line p-2.5 space-y-2">
                  <span className="text-[11px] font-semibold text-text2 block">Validate sample article</span>
                  <div className="flex gap-1.5">
                    <input
                      type="text"
                      className="h-8 px-2 flex-1 rounded-lg border border-line bg-surface text-xs focus:ring-1 focus:ring-pri"
                      value={testUrl}
                      onChange={(e) => setTestUrl(e.target.value)}
                      placeholder="https://.../sample-article"
                    />
                    <button
                      type="button"
                      onClick={handleTestSelectors}
                      disabled={testing || !testUrl || !form.title_selector || !form.content_selector}
                      className="h-8 px-2.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-xs font-medium disabled:opacity-50"
                    >
                      {testing ? <Icon icon="svg-spinners:ring-resize" width={14} /> : "Test"}
                    </button>
                  </div>
                  {testError && (
                    <p className="text-xs text-bad bg-bad-soft border border-bad/20 rounded p-1.5">{testError}</p>
                  )}
                  {testResult && (
                    <div className="rounded bg-surface2 border border-line p-2 text-xs text-text space-y-1">
                      <p><span className="font-semibold">Title:</span> {(testResult.title as string) || <em className="text-bad">— none —</em>}</p>
                      <p><span className="font-semibold">Length:</span> {testResult.content_length as number} chars</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2 border-t border-line">
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="h-9 px-3 rounded-lg border border-line bg-surface text-text text-xs font-medium hover:bg-surface2 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="h-9 px-4 rounded-lg bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 inline-flex items-center gap-1.5 disabled:opacity-50"
              >
                {saving && <Icon icon="svg-spinners:ring-resize" width={14} />}
                <span>{editingId ? "Save changes" : "Create source"}</span>
              </button>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
