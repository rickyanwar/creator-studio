"use client";

import React from "react";
import type { BrandSummaryOut } from "@/lib/socialBrandsApi";

interface Props {
  brands: BrandSummaryOut[];
  loadingBrands: boolean;
  selectedBrandId: number | null;
  setSelectedBrandId: (id: number) => void;
}

export function StepBrand({
  brands,
  loadingBrands,
  selectedBrandId,
  setSelectedBrandId,
}: Props) {
  return (
    <section aria-labelledby="step1-h" className="card p-5 space-y-3">
      <h2 id="step1-h" className="text-sm font-bold text-text-primary flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-primary-main/20 text-primary-main text-xs flex items-center justify-center font-bold">
          1
        </span>
        Brand
      </h2>
      <div>
        <label htmlFor="brand-select" className="label">
          This channel belongs to
        </label>
        <select
          id="brand-select"
          value={selectedBrandId ?? ""}
          onChange={(e) => setSelectedBrandId(Number(e.target.value))}
          disabled={loadingBrands}
          className="input-rect max-w-sm"
        >
          {loadingBrands ? (
            <option>Loading brands...</option>
          ) : (
            brands.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name} ({b.timezone})
              </option>
            ))
          )}
        </select>
      </div>
    </section>
  );
}
