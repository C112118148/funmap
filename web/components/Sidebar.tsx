"use client";

import type { MapEvent } from "@/components/PulseMap";
import EventCard from "@/components/EventCard";

interface Bounds {
  west: number;
  east: number;
  south: number;
  north: number;
}

/** Squared distance from the viewport center (degree space is fine for ordering). */
function dist2FromCenter(ev: MapEvent, c: { lng: number; lat: number }): number {
  const dx = ev.lng - c.lng;
  const dy = ev.lat - c.lat;
  return dx * dx + dy * dy;
}

/**
 * PRD 4.4 — Desktop fixed 380px sidebar, Dual list/detail view.
 * Ordering: in-view events first (closest to viewport center on top),
 * then nearby (offscreen) events ordered by increasing distance.
 */
export default function Sidebar({
  events,
  selectedId,
  onSelect,
  viewport,
}: {
  events: MapEvent[];
  selectedId: string | null;
  onSelect: (ev: MapEvent) => void;
  viewport?: Bounds | null;
}) {
  const isNearby = (ev: MapEvent) =>
    viewport
      ? !(ev.lng >= viewport.west && ev.lng <= viewport.east &&
          ev.lat >= viewport.south && ev.lat <= viewport.north)
      : false;

  const sorted = (() => {
    if (!viewport) return events;
    const center = {
      lng: (viewport.west + viewport.east) / 2,
      lat: (viewport.south + viewport.north) / 2,
    };
    const inView = events.filter((ev) => !isNearby(ev));
    const nearby = events.filter(isNearby);
    const byDist = (a: MapEvent, b: MapEvent) =>
      dist2FromCenter(a, center) - dist2FromCenter(b, center);
    return [...inView.sort(byDist), ...nearby.sort(byDist)];
  })();

  const inViewCount = viewport ? sorted.filter((ev) => !isNearby(ev)).length : sorted.length;

  return (
    <aside className="hidden h-full w-[380px] shrink-0 flex-col border-r bg-white md:flex [overscroll-behavior:contain]">
      <div className="border-b px-4 pb-3 pt-4">
        <h1 className="text-xl font-bold text-slate-900">在地即時情報地圖</h1>
        <p className="mt-0.5 text-sm text-slate-600">快閃 · 市集 · 展覽 · 即時人潮</p>
        <p className="mt-1 text-xs text-slate-600">
          畫面內 {inViewCount} 個活動
          {sorted.length > inViewCount && ` · 附近 ${sorted.length - inViewCount} 個`}
        </p>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {sorted.length === 0 ? (
          <div className="rounded-xl border border-dashed p-6 text-center">
            <p className="text-sm font-medium text-slate-800">🍃 周邊目前一片寧靜</p>
            <p className="mt-1 text-xs text-slate-600">暫無進行中的快閃與優惠，試著縮小地圖看看其他區域</p>
          </div>
        ) : (
          sorted.map((ev) => (
            <EventCard
              key={ev.id}
              event={ev}
              expanded={selectedId === ev.id}
              nearby={isNearby(ev)}
              onClick={() => onSelect(ev)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
