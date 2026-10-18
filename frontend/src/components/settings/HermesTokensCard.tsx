import { useState } from "react";
import useSWR from "swr";
import { listApiTokens, createApiToken, revokeApiToken } from "@/lib/api";
import type { ApiToken } from "@/lib/types";
import { Icon } from "@iconify/react";

const fetcher = () => listApiTokens().then((r) => r.data as ApiToken[]);

export function HermesTokensCard() {
  const { data: tokens, mutate } = useSWR("apiTokens", fetcher);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const availableScopes = ["read", "memory:write", "recommendations:write", "ideas:write", "gflow:worker", "labels:write", "research", "research:review"];

  const handleToggleScope = (s: string) => {
    setScopes((prev) => prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]);
  };

  const handleCreate = async () => {
    if (!name.trim() || scopes.length === 0) return;
    setLoading(true);
    try {
      const res = await createApiToken({ name, scopes });
      setCreatedToken(res.data.token);
      setName("");
      setScopes([]);
      mutate();
    } finally {
      setLoading(false);
    }
  };

  const handleRevoke = async (id: number) => {
    if (!confirm("Revoke this token?")) return;
    await revokeApiToken(id);
    mutate();
  };

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 space-y-4">
      <h2 className="text-base font-semibold text-text">Hermes API tokens</h2>
      
      {createdToken && (
        <div className="p-4 bg-warn-soft border border-warn/30 rounded-xl space-y-2">
          <p className="text-sm font-semibold text-warn">
            Copy it now — it will not be shown again
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 p-2 bg-surface rounded-lg border border-line text-xs font-mono select-all text-text">
              {createdToken}
            </code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(createdToken);
                alert("Copied!");
              }}
              className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text text-xs hover:bg-surface2 font-medium"
            >
              Copy
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3 bg-surface2 p-3.5 rounded-xl border border-line">
        <h3 className="text-sm font-semibold text-text">Create Token</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-semibold text-text mb-1">Name</label>
            <input
              className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. VPS Hermes Worker"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-text mb-1">Scopes</label>
            <div className="flex flex-wrap gap-2 mt-1">
              {availableScopes.map((s) => (
                <label key={s} className="flex items-center gap-1.5 text-xs text-text cursor-pointer">
                  <input
                    type="checkbox"
                    className="w-3.5 h-3.5 rounded accent-pri border-line"
                    checked={scopes.includes(s)}
                    onChange={() => handleToggleScope(s)}
                  />
                  <span>{s}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
        <button
          onClick={handleCreate}
          disabled={loading || !name.trim() || scopes.length === 0}
          className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {loading ? "Creating..." : "Create Token"}
        </button>
      </div>

      <div className="space-y-2">
        {tokens?.map((t) => (
          <div key={t.id} className="flex items-center justify-between p-3 border border-line rounded-xl bg-surface">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-text">{t.name}</span>
                {t.revoked_at ? (
                  <span className="text-[10px] uppercase font-bold bg-bad-soft text-bad px-2 py-0.5 rounded-full">Revoked</span>
                ) : (
                  <span className="text-[10px] uppercase font-bold bg-pri-soft text-pri-text px-2 py-0.5 rounded-full">Active</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1 mt-1">
                {t.scopes.map((s) => (
                  <span key={s} className="text-[10px] bg-surface2 border border-line px-1.5 py-0.5 rounded text-text2">
                    {s}
                  </span>
                ))}
              </div>
            </div>
            {!t.revoked_at && (
              <button
                onClick={() => handleRevoke(t.id)}
                className="text-xs text-bad hover:underline font-medium"
              >
                Revoke
              </button>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
