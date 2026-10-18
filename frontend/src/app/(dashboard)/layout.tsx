"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/layout/Sidebar";
import BottomTabBar from "@/components/layout/BottomTabBar";
import TopAppBar from "@/components/layout/TopAppBar";
import { SidebarProvider, useSidebar } from "@/contexts/SidebarContext";
import { BrandFilterProvider } from "@/contexts/BrandFilterContext";

function DashboardLayoutInner({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { collapsed } = useSidebar();

  useEffect(() => {
    if (!localStorage.getItem("token")) {
      router.replace("/login");
    }
  }, [router]);

  return (
    <div className="flex min-h-screen bg-default text-text">
      {/* Fixed sidebar (desktop only) */}
      <Sidebar />

      {/* Sidebar spacer: pushes right panel past sidebar (widths stay in sync with Sidebar.tsx) */}
      <div
        className={`hidden lg:block flex-shrink-0 transition-[width] duration-300 ${
          collapsed ? "w-[80px]" : "w-[248px]"
        }`}
      />

      {/* Right panel: topbar + main content */}
      <div className="flex-1 flex flex-col min-w-0">
        <TopAppBar />

        <main className="flex-1 pb-[64px] lg:pb-0 overflow-x-clip min-w-0">
          <div className="p-4 sm:p-6 lg:p-8 max-w-7xl mx-auto">{children}</div>
        </main>
      </div>

      {/* Mobile bottom tab bar */}
      <BottomTabBar />
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      <BrandFilterProvider>
        <DashboardLayoutInner>{children}</DashboardLayoutInner>
      </BrandFilterProvider>
    </SidebarProvider>
  );
}
