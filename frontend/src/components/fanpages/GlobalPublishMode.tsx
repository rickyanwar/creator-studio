"use client";

export const PUBLISH_MODE_FIELDS = [
  "publish_mode",
  "mode2_publish_mode",
  "discussion_publish_mode",
  "pinterest_publish_mode",
  "facebook_photo_publish_mode",
  "yt_clip_publish_mode",
  "ai_video_publish_mode",
] as const;

const FIELD_LABELS: Record<string, string> = {
  publish_mode: "Instagram",
  mode2_publish_mode: "News",
  discussion_publish_mode: "Discussion",
  pinterest_publish_mode: "Pinterest",
  facebook_photo_publish_mode: "Facebook",
  yt_clip_publish_mode: "YouTube Clips",
  ai_video_publish_mode: "AI Video",
};

export function GlobalPublishMode({
  form,
  set,
}: {
  form: Record<string, unknown>;
  set: (key: string, value: unknown) => void;
}) {
  const values = PUBLISH_MODE_FIELDS.map((f) => (form[f] as string) || "auto");
  const current = values.every((v) => v === values[0]) ? values[0] : null;

  return (
    <section className="card space-y-3">
      <h2 className="text-base font-semibold text-text-primary">Publish Mode</h2>
      <p className="text-xs text-text-secondary">
        Applies to every content mode of this fanpage.
      </p>
      <div className="flex gap-4">
        {(["auto", "manual_review"] as const).map((mode) => (
          <label key={mode} className="flex items-center gap-3 cursor-pointer">
            <input
              type="radio"
              name="publish-mode"
              value={mode}
              checked={current === mode}
              onChange={() => PUBLISH_MODE_FIELDS.forEach((f) => set(f, mode))}
              className="accent-primary-main"
            />
            <div>
              <span className="text-sm text-text-primary font-medium">
                {mode === "auto" ? "Auto-publish" : "Manual Review"}
              </span>
              <p className="text-xs text-text-secondary">
                {mode === "auto"
                  ? "Posts go to Repliz automatically."
                  : "You approve each post before it is published (Queue or designer)."}
              </p>
            </div>
          </label>
        ))}
      </div>
      {current === null && (
        <p className="text-xs text-amber-500">
          Currently mixed:{" "}
          {PUBLISH_MODE_FIELDS.map(
            (f) =>
              `${FIELD_LABELS[f]} = ${(form[f] as string) === "manual_review" ? "Manual review" : "Auto"}`
          ).join(", ")}
          . Pick one to apply it to all modes.
        </p>
      )}
    </section>
  );
}
