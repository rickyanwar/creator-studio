"use client";

import useSWR from "swr";
import { Fragment, useState } from "react";
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
  listNewsArticles,
} from "@/lib/api";

type NewsSource = {
  id: number;
  name: string;
  category_url: string;
  is_active: boolean;
  scrape_interval_minutes: number;
  render_mode: string;
  article_list_selector: string;
  article_link_attribute: string;
  title_selector: string;
  content_selector: string;
  image_selector: string | null;
  date_selector: string | null;
  last_scraped_at: string | null;
  last_scrape_error: string | null;
  article_count: number;
};

type Article = {
  id: number;
  article_url: string;
  scraped_title: string;
  content_preview: string;
  scraped_image_url: string | null;
  status: string;
  scraped_at: string | null;
};

const emptyForm = {
  name: "",
  category_url: "",
  is_active: true,
  scrape_interval_minutes: 60,
  render_mode: "static",
  article_list_selector: "",
  article_link_attribute: "href",
  title_selector: "",
  content_selector: "",
  image_selector: "",
  date_selector: "",
};

export default function NewsSourcesPage() {
  const { data: sources = [], isLoading, mutate } = useSWR<NewsSource[]>(
    "news-sources",
    () => listNewsSources().then((r) => r.data as NewsSource[]),
    { refreshInterval: 30000 }
  );

  const [showForm, setShowForm] = useState(false);
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

  function set(field: string, value: unknown) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function openCreate() {
    setForm(emptyForm);
    setEditingId(null);
    setTestResult(null);
    setListResult(null);
    setTestError(null);
    setShowForm(true);
  }

  function openEdit(s: NewsSource) {
    setForm({
      name: s.name,
      category_url: s.category_url,
      is_active: s.is_active,
      scrape_interval_minutes: s.scrape_interval_minutes,
      render_mode: s.render_mode,
      article_list_selector: s.article_list_selector,
      article_link_attribute: s.article_link_attribute,
      title_selector: s.title_selector,
      content_selector: s.content_selector,
      image_selector: s.image_selector ?? "",
      date_selector: s.date_selector ?? "",
    });
    setEditingId(s.id);
    setTestResult(null);
    setListResult(null);
    setTestError(null);
    setShowForm(true);
  }

  async function handleSave() {
    if (!form.name || !form.category_url || !form.article_list_selector || !form.title_selector || !form.content_selector) {
      alert("Name, category URL, list selector, title selector and content selector are required.");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        ...form,
        image_selector: form.image_selector || null,
        date_selector: form.date_selector || null,
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

  const inputCls = "input-rect py-2";
  const labelCls = "block text-xs font-semibold text-ink-80 mb-1";

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1
            className="text-display-md text-ink"
            style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}
          >
            News Sources
          </h1>
          <p className="text-caption text-ink-48 mt-1">
            {sources.length} configured site{sources.length === 1 ? "" : "s"} — scraped articles feed the News-to-Image pipeline
          </p>
        </div>
        <button onClick={openCreate} className="btn btn-primary flex items-center gap-2 shrink-0">
          <Icon icon="solar:add-circle-bold-duotone" width={16} />
          Add News Source
        </button>
      </div>

      {/* ── Add/Edit form ── */}
      {showForm && (
        <div className="card p-6 space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold text-ink">{editingId ? "Edit News Source" : "New News Source"}</h2>
            <button onClick={() => setShowForm(false)} className="text-ink-48 hover:text-ink">
              <Icon icon="solar:close-circle-bold-duotone" width={20} />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className={labelCls}>Name</label>
              <input className={inputCls} value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Motosan MotoGP" />
            </div>
            <div>
              <label className={labelCls}>Category URL</label>
              <input className={inputCls} value={form.category_url} onChange={(e) => set("category_url", e.target.value)} placeholder="https://www.motosan.es/motogp" />
            </div>
            <div>
              <label className={labelCls}>Render Mode</label>
              <select className={inputCls} value={form.render_mode} onChange={(e) => set("render_mode", e.target.value)}>
                <option value="static">Static (fast, BeautifulSoup)</option>
                <option value="js">JS-heavy (Playwright)</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Scrape Interval (minutes, min 15)</label>
              <input type="number" min={15} className={inputCls} value={form.scrape_interval_minutes} onChange={(e) => set("scrape_interval_minutes", parseInt(e.target.value) || 60)} />
            </div>
          </div>

          <div className="border-t border-hairline pt-4">
            <h3 className="text-sm font-bold text-ink mb-3">Category Page — Article List</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
              <div className="md:col-span-2">
                <label className={labelCls}>Article List Selector (targets links on category page)</label>
                <input className={inputCls} value={form.article_list_selector} onChange={(e) => set("article_list_selector", e.target.value)} placeholder="article.post h2 a" />
              </div>
              <div>
                <label className={labelCls}>Link Attribute</label>
                <input className={inputCls} value={form.article_link_attribute} onChange={(e) => set("article_link_attribute", e.target.value)} placeholder="href" />
              </div>
            </div>
            <button
              onClick={handleTestListSelector}
              disabled={listTesting || !form.category_url || !form.article_list_selector}
              className="btn btn-secondary mt-3 flex items-center gap-2"
            >
              {listTesting ? <Icon icon="svg-spinners:ring-resize" width={14} /> : <Icon icon="solar:test-tube-bold-duotone" width={14} />}
              Test List Selector
            </button>
            {listResult && (
              <div className="mt-3 rounded-lg bg-parchment border border-hairline p-3 text-xs text-ink-80">
                <p className="font-semibold mb-1">{listResult.link_count} link(s) found — first {Math.min(20, listResult.links.length)}:</p>
                <ul className="space-y-0.5 max-h-40 overflow-y-auto">
                  {listResult.links.map((l) => (
                    <li key={l} className="truncate">
                      <a href={l} target="_blank" rel="noreferrer" className="text-primary-main hover:underline">{l}</a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div className="border-t border-hairline pt-4">
            <h3 className="text-sm font-bold text-ink mb-3">Article Page — Extraction Selectors</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className={labelCls}>Title Selector</label>
                <input className={inputCls} value={form.title_selector} onChange={(e) => set("title_selector", e.target.value)} placeholder="h1.article-title" />
              </div>
              <div>
                <label className={labelCls}>Content Selector</label>
                <input className={inputCls} value={form.content_selector} onChange={(e) => set("content_selector", e.target.value)} placeholder="div.article-body" />
              </div>
              <div>
                <label className={labelCls}>Image Selector (optional, falls back to og:image)</label>
                <input className={inputCls} value={form.image_selector} onChange={(e) => set("image_selector", e.target.value)} placeholder="div.article-body img" />
              </div>
              <div>
                <label className={labelCls}>Date Selector (optional)</label>
                <input className={inputCls} value={form.date_selector} onChange={(e) => set("date_selector", e.target.value)} placeholder="time.published" />
              </div>
            </div>

            {/* Selector tester */}
            <div className="mt-4 rounded-xl border border-dashed border-hairline p-4 space-y-3">
              <p className="text-xs font-semibold text-ink-80 flex items-center gap-1.5">
                <Icon icon="solar:test-tube-bold-duotone" width={14} />
                Selector Tester — paste a sample article URL to validate before saving
              </p>
              <div className="flex gap-2">
                <input className={inputCls} value={testUrl} onChange={(e) => setTestUrl(e.target.value)} placeholder="https://www.motosan.es/motogp/some-article/" />
                <button
                  onClick={handleTestSelectors}
                  disabled={testing || !testUrl || !form.title_selector || !form.content_selector}
                  className="btn btn-secondary shrink-0 flex items-center gap-2"
                >
                  {testing ? <Icon icon="svg-spinners:ring-resize" width={14} /> : "Test"}
                </button>
              </div>
              {testError && (
                <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg p-2">{testError}</p>
              )}
              {testResult && (
                <div className="rounded-lg bg-parchment border border-hairline p-3 text-xs text-ink-80 space-y-2">
                  {(testResult.errors as string[])?.length > 0 && (
                    <div className="text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                      {(testResult.errors as string[]).map((er, i) => <p key={i}>⚠ {er}</p>)}
                    </div>
                  )}
                  <p><span className="font-semibold">Title:</span> {(testResult.title as string) || <em className="text-red-600">— nothing extracted —</em>}</p>
                  <p><span className="font-semibold">Content ({testResult.content_length as number} chars):</span></p>
                  <p className="whitespace-pre-wrap max-h-48 overflow-y-auto bg-canvas rounded p-2 border border-hairline">
                    {(testResult.content as string) || <em className="text-red-600">— nothing extracted —</em>}
                  </p>
                  {testResult.image_url ? (
                    <div>
                      <p className="font-semibold mb-1">Image:</p>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={testResult.image_url as string} alt="extracted" className="max-h-40 rounded-lg border border-hairline" />
                    </div>
                  ) : (
                    <p><span className="font-semibold">Image:</span> <em>none found</em></p>
                  )}
                  {testResult.date_text ? <p><span className="font-semibold">Date:</span> {testResult.date_text as string}</p> : null}
                </div>
              )}
            </div>
          </div>

          <div className="flex justify-end gap-2 border-t border-hairline pt-4">
            <button onClick={() => setShowForm(false)} className="btn btn-secondary">Cancel</button>
            <button onClick={handleSave} disabled={saving} className="btn btn-primary flex items-center gap-2">
              {saving && <Icon icon="svg-spinners:ring-resize" width={14} />}
              {editingId ? "Save Changes" : "Create Source"}
            </button>
          </div>
        </div>
      )}

      {/* ── Sources table ── */}
      {isLoading ? (
        <div className="text-caption text-ink-48">Loading…</div>
      ) : sources.length === 0 && !showForm ? (
        <div className="card p-10 text-center text-ink-48">
          <Icon icon="solar:documents-bold-duotone" width={40} className="mx-auto mb-3 opacity-40" />
          <p className="text-sm">No news sources yet. Add one to start scraping articles.</p>
        </div>
      ) : (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-caption">
            <thead className="bg-parchment border-b border-hairline">
              <tr>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Source</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Interval</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Articles</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Last Scraped</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Status</th>
                <th className="px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {sources.map((s) => (
                <Fragment key={s.id}>
                  <tr className="hover:bg-parchment/50 transition-colors">
                    <td className="px-5 py-3">
                      <p className="text-ink font-medium">{s.name}</p>
                      <a href={s.category_url} target="_blank" rel="noreferrer" className="text-[11px] text-ink-48 hover:text-primary-main truncate block max-w-[260px]">
                        {s.category_url}
                      </a>
                    </td>
                    <td className="px-5 py-3 text-ink-80">{s.scrape_interval_minutes}m</td>
                    <td className="px-5 py-3">
                      <button onClick={() => toggleArticles(s.id)} className="text-primary-main hover:underline font-medium">
                        {s.article_count} {expandedId === s.id ? "▾" : "▸"}
                      </button>
                    </td>
                    <td className="px-5 py-3 text-ink-80">
                      {s.last_scraped_at ? formatDistanceToNowStrict(new Date(s.last_scraped_at), { addSuffix: true }) : "never"}
                    </td>
                    <td className="px-5 py-3">
                      {!s.is_active ? (
                        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-600">Inactive</span>
                      ) : s.last_scrape_error ? (
                        <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700" title={s.last_scrape_error}>Error</span>
                      ) : (
                        <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">Active</span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => handleScrapeNow(s.id)}
                          disabled={scraping === s.id}
                          title="Scrape now"
                          className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                        >
                          {scraping === s.id ? <Icon icon="svg-spinners:ring-resize" width={16} /> : <Icon icon="solar:play-circle-bold-duotone" width={16} />}
                        </button>
                        <button
                          onClick={() => updateNewsSource(s.id, { is_active: !s.is_active }).then(() => mutate())}
                          title={s.is_active ? "Deactivate" : "Activate"}
                          className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                        >
                          <Icon icon={s.is_active ? "solar:pause-circle-bold-duotone" : "solar:play-bold-duotone"} width={16} />
                        </button>
                        <button
                          onClick={() => openEdit(s)}
                          title="Edit"
                          className="p-1.5 rounded-lg text-ink-48 hover:text-primary-main hover:bg-parchment transition-colors"
                        >
                          <Icon icon="solar:pen-bold-duotone" width={16} />
                        </button>
                        <button
                          onClick={() => handleDelete(s)}
                          title="Delete"
                          className="p-1.5 rounded-lg text-ink-48 hover:text-red-600 hover:bg-red-50 transition-colors"
                        >
                          <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === s.id && (
                    <tr>
                      <td colSpan={6} className="bg-parchment/40 px-5 py-4">
                        {articlesLoading ? (
                          <p className="text-xs text-ink-48">Loading articles…</p>
                        ) : articles.length === 0 ? (
                          <p className="text-xs text-ink-48">No articles scraped yet.</p>
                        ) : (
                          <div className="space-y-2">
                            {articles.map((a) => (
                              <div key={a.id} className="flex items-start gap-3 rounded-lg bg-canvas border border-hairline p-3">
                                {a.scraped_image_url && (
                                  /* eslint-disable-next-line @next/next/no-img-element */
                                  <img src={a.scraped_image_url} alt="" className="w-16 h-16 rounded object-cover shrink-0" />
                                )}
                                <div className="min-w-0">
                                  <a href={a.article_url} target="_blank" rel="noreferrer" className="text-sm font-semibold text-ink hover:text-primary-main line-clamp-1">
                                    {a.scraped_title}
                                  </a>
                                  <p className="text-xs text-ink-48 line-clamp-2 mt-0.5">{a.content_preview}</p>
                                  <p className="text-[11px] text-ink-48 mt-1">
                                    <span className="uppercase font-semibold">{a.status}</span>
                                    {a.scraped_at && <> · {formatDistanceToNowStrict(new Date(a.scraped_at), { addSuffix: true })}</>}
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
    </div>
  );
}
