"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import type { MapEvent } from "@/components/PulseMap";
import Sidebar from "@/components/Sidebar";
import MobileDrawer from "@/components/MobileDrawer";

// PRD: dynamic loading to prevent SSR hydration mismatch (mapbox-gl touches window)
const PulseMap = dynamic(() => import("@/components/PulseMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-slate-100 text-slate-600">
      地圖載入中…
    </div>
  ),
});

export default function Home() {
  const [selected, setSelected] = useState<MapEvent | null>(null);
  const [events, setEvents] = useState<MapEvent[]>([]);
  const [viewport, setViewport] = useState<{ west: number; east: number; south: number; north: number } | null>(null);

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
  const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
  const mapboxToken = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";

  // Sidebar click: select + collapse/expand handled by selectedId; map flies via focusEvent
  const handleSelect = (ev: MapEvent) => setSelected(ev);

  return (
    <main className="flex h-screen w-screen">
      {/* PRD 4.4: fixed 380px sidebar, dual list/detail view */}
      <Sidebar
        events={events}
        selectedId={selected?.id ?? null}
        onSelect={handleSelect}
        viewport={viewport}
      />

      <div className="relative h-full flex-1">
        {supabaseUrl && mapboxToken ? (
          <PulseMap
            supabaseUrl={supabaseUrl}
            supabaseAnonKey={supabaseKey}
            mapboxToken={mapboxToken}
            onSelect={setSelected}
            onEventsChange={setEvents}
            focusEvent={selected}
            onViewportChange={setViewport}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-8 text-center text-sm text-slate-600">
            請在 web/.env.local 設定 NEXT_PUBLIC_SUPABASE_URL、
            NEXT_PUBLIC_SUPABASE_ANON_KEY、NEXT_PUBLIC_MAPBOX_TOKEN
          </div>
        )}
        {/* Mobile: 3-stage gesture drawer (PRD 4.4) */}
        <MobileDrawer event={selected} />
      </div>
    </main>
  );
}
