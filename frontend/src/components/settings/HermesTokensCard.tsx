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

  const availableScopes = ["read", "memory:write", "recommendations:write"];

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
    <section className="card space-y-4">
      <h2 className="text-base font-semibold text-text-primary">Hermes API tokens</h2>
      
      {createdToken && (
        <div className="p-4 bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800 rounded-lg">
          <p className="text-sm font-semibold text-orange-800 dark:text-orange-200 mb-2">
            Copy it now — it will not be shown again
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 p-2 bg-white dark:bg-black/40 rounded border border-orange-200 dark:border-orange-800 text-sm select-all">
              {createdToken}
            </code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(createdToken);
                alert("Copied!");
              }}
              className="btn btn-secondary"
            >
              Copy
            </button>
          </div>
        </div>
      )}

      <div className="space-y-3 bg-surface-secondary p-3 rounded-lg border border-hairline">
        <h3 className="text-sm font-medium">Create Token</h3>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Name</label>
            <input
              className="input-rect"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. VPS Hermes Worker"
            />
          </div>
          <div>
            <label className="label">Scopes</label>
            <div className="flex flex-wrap gap-2 mt-1">
              {availableScopes.map((s) => (
                <label key={s} className="flex items-center gap-1 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={scopes.includes(s)}
                    onChange={() => handleToggleScope(s)}
                  />
                  {s}
                </label>
              ))}
            </div>
          </div>
        </div>
        <button
          onClick={handleCreate}
          disabled={loading || !name.trim() || scopes.length === 0}
          className="btn btn-secondary text-xs"
        >
          {loading ? "Creating..." : "Create Token"}
        </button>
      </div>

      <div className="space-y-2">
        {tokens?.map((t) => (
          <div key={t.id} className="flex items-center justify-between p-3 border border-hairline rounded-lg">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-text-primary">{t.name}</span>
                {t.revoked_at ? (
                  <span className="text-[10px] uppercase bg-red-100 text-red-800 px-1.5 py-0.5 rounded">Revoked</span>
                ) : (
                  <span className="text-[10px] uppercase bg-emerald-100 text-emerald-800 px-1.5 py-0.5 rounded">Active</span>
                )}
              </div>
              <div className="flex gap-1 mt-1">
                {t.scopes.map(s => <span key={s} className="text-[10px] bg-divider-soft px-1 rounded">{s}</span>)}
              </div>
              <p className="text-xs text-text-secondary mt-1">
                Created: {new Date(t.created_at + "Z").toLocaleString()}
                <br />
                Last used: {t.last_used_at ? new Date(t.last_used_at + "Z").toLocaleString() : "Never"}
              </p>
            </div>
            {!t.revoked_at && (
              <button onClick={() => handleRevoke(t.id)} className="btn-ghost text-red-500 text-xs">
                Revoke
              </button>
            )}
          </div>
        ))}
        {tokens?.length === 0 && <p className="text-xs text-text-secondary">No tokens found.</p>}
      </div>
    </section>
  );
}
