"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login } from "@/lib/authApi";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await login(username, password, remember);
      localStorage.setItem("token", res.data.access_token);
      router.push("/dashboard");
    } catch {
      setError("That username and password do not match.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-default text-text flex items-center justify-center p-4 sm:p-6">
      <div className="w-full max-w-[400px] flex flex-col gap-[22px]">
        {/* Logo / Title */}
        <div className="flex flex-col items-center gap-3 text-center">
          <span
            aria-hidden="true"
            className="w-12 h-12 rounded-[14px] bg-pri text-pri-fg flex items-center justify-center shadow-sm"
          >
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M17 7.5C16 5.5 14.2 4.5 12 4.5c-2.8 0-4.6 1.5-4.6 3.6 0 4.9 9.6 2.9 9.6 7.9 0 2.2-2 3.6-4.9 3.6-2.5 0-4.3-1.1-5.1-3.1" />
            </svg>
          </span>
          <h1 className="text-2xl font-bold text-text tracking-tight m-0">Sign in to Studio</h1>
          <p className="text-sm text-text2 m-0">The content factory for your brands.</p>
        </div>

        {/* Form Card */}
        <form
          onSubmit={handleSubmit}
          className="bg-surface border border-line rounded-2xl p-6 flex flex-col gap-4 shadow-sm"
        >
          {error && (
            <div
              role="alert"
              className="p-2.5 px-3 rounded-[10px] bg-bad-soft text-bad text-sm font-medium border border-bad/20"
            >
              {error}
            </div>
          )}

          <label htmlFor="login-username" className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
            Username
            <input
              id="login-username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin"
              required
              className="h-11 px-3 rounded-[10px] border border-line bg-surface text-text text-[15px] font-normal focus:outline-none focus:border-pri transition-colors"
            />
          </label>

          <label htmlFor="login-password" className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
            Password
            <span className="flex items-center gap-1.5 h-11 pr-1.5 pl-3 rounded-[10px] border border-line bg-surface focus-within:border-pri transition-colors">
              <input
                id="login-password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
                className="flex-1 min-w-0 border-0 bg-transparent text-text text-[15px] font-normal outline-none"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                className="h-[34px] px-2.5 rounded-lg border-0 bg-surface2 text-text2 hover:text-text text-[13px] font-medium transition-colors cursor-pointer"
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </span>
          </label>

          <label className="flex items-center gap-2.5 min-h-10 text-sm text-text cursor-pointer select-none">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="w-[18px] h-[18px] accent-pri rounded cursor-pointer"
            />
            Keep me signed in
          </label>

          <button
            type="submit"
            disabled={loading}
            className="h-[46px] rounded-[10px] bg-pri text-pri-fg text-[15px] font-semibold flex items-center justify-center hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50 mt-1"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="m-0 text-center text-[13px] text-text2">Admin access only.</p>
      </div>
    </div>
  );
}
