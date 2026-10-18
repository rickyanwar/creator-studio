"use client";

import type { FanpageDetail, AiVideoTemplate } from "@/lib/types";
import { AiVideoSection } from "@/components/fanpages/AiVideoSection";

export interface AiVideoTabProps {
  fanpageId?: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  templates: AiVideoTemplate[];
  onNavigateTab?: (tab: string) => void;
}

export function AiVideoTab({ form, set, templates, onNavigateTab }: AiVideoTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-aivideo" aria-labelledby="tab-aivideo" className="space-y-8">
      <AiVideoSection form={form} set={set} templates={templates} onNavigateTab={onNavigateTab} />
    </div>
  );
}
