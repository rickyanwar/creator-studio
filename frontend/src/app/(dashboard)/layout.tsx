"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/layout/Sidebar";
import BottomTabBar from "@/components/layout/BottomTabBar";
import TopAppBar from "@/components/layout/TopAppBar";
import { SidebarProvider, useSidebar } from "@/contexts/SidebarContext";

function DashboardLayoutInner({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { collapsed } = useSidebar();

  useEffect(() => {
    if (!localStorage.getItem("token")) {
      router.replace("/login");
    }
  }, [router]);

  return (
    <div className="flex min-h-screen bg-bg-default">

      {/* ── Fixed sidebar (desktop only) ── */}
      <Sidebar />

      {/* ── Sidebar spacer: pushes right panel past the sidebar ── */}
      <div className={`hidden lg:block flex-shrink-0 transition-[width] duration-300 ${
        collapsed ? "w-[88px]" : "w-[280px]"
      }`} />

      {/* ── Right panel: topbar + content stacked vertically ── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Topbar sits at the top of the right panel — sticky so it scrolls with panel */}
        <TopAppBar />

        {/* Main content */}
        <main className="flex-1 pb-[60px] lg:pb-0 overflow-x-hidden">
          <div className="p-6 lg:p-8 max-w-7xl mx-auto">{children}</div>
        </main>
      </div>

      {/* ── Mobile bottom tab bar ── */}
      <BottomTabBar />
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      <DashboardLayoutInner>{children}</DashboardLayoutInner>
    </SidebarProvider>
  );
}
