"use client";

import type { FanpageDetail } from "@/lib/types";
import { YtClipSection } from "@/components/fanpages/YtClipSection";

export interface YoutubeTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  refreshSources: () => Promise<void>;
  hideIdeas?: boolean;
  onNavigateTab?: (tab: string) => void;
}

export function YoutubeTab({ fanpageId, form, set, refreshSources, hideIdeas, onNavigateTab }: YoutubeTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-youtube" aria-labelledby="tab-youtube" className="space-y-8">
      {/* ── Section: Mode 7 — YouTube Clips ───────────── */}
      <YtClipSection
        fanpageId={fanpageId}
        form={form}
        set={set}
        refreshSources={refreshSources}
        hideIdeas={hideIdeas}
        onNavigateTab={onNavigateTab}
      />
    </div>
  );
}
