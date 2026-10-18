"use client";

import { createContext, useContext, useState, useEffect, ReactNode } from "react";

interface BrandFilterCtx {
  selectedBrandId: number | null;
  setSelectedBrandId: (id: number | null) => void;
}

const BrandFilterContext = createContext<BrandFilterCtx>({
  selectedBrandId: null,
  setSelectedBrandId: () => {},
});

export function BrandFilterProvider({ children }: { children: ReactNode }) {
  const [selectedBrandId, setSelectedBrandIdState] = useState<number | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("brandFilter");
      if (raw !== null && raw !== "" && raw !== "null") {
        const parsed = Number(raw);
        if (!Number.isNaN(parsed)) {
          setSelectedBrandIdState(parsed);
        }
      }
    } catch {
      // localStorage may be blocked or restricted
    }
  }, []);

  function setSelectedBrandId(id: number | null) {
    setSelectedBrandIdState(id);
    try {
      if (id === null) {
        localStorage.removeItem("brandFilter");
      } else {
        localStorage.setItem("brandFilter", String(id));
      }
    } catch {
      // localStorage may be blocked or restricted
    }
  }

  return (
    <BrandFilterContext.Provider value={{ selectedBrandId, setSelectedBrandId }}>
      {children}
    </BrandFilterContext.Provider>
  );
}

export const useBrandFilter = () => useContext(BrandFilterContext);
