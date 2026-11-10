"use client";

import type { FanpageDetail, AiVideoTemplate } from "@/lib/types";
import { AiVideoSection } from "@/components/fanpages/AiVideoSection";

export interface AiVideoTabProps {
  fanpageId?: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  templates: AiVideoTemplate[];
  onNavigateTab?: (tab: string) => void;
  /** Group Share lane: fields owned by the brand page, shown read-only. */
  lockedFields?: readonly string[];
}

export function AiVideoTab({ form, set, templates, onNavigateTab, lockedFields }: AiVideoTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-aivideo" aria-labelledby="tab-aivideo" className="space-y-8">
      <AiVideoSection form={form} set={set} templates={templates} onNavigateTab={onNavigateTab} lockedFields={lockedFields} />
    </div>
  );
}
