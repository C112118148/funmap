"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";

export interface MapEvent {
  id: string;
  title: string;
  summary: string | null;
  category: string;
  lng: number;
  lat: number;
  start_time: string;
  end_time: string;
  is_time_estimated: boolean;
  is_verified: boolean;
  crowd_status: string;
  source_url?: string | null;
}

const CROWD_EMOJI: Record<string, string> = {
  comfortable: "🟢",
  moderate: "🟡",
  crowded: "🔴",
};
const CATEGORY_ICON: Record<string, string> = {
  promotion: "🏷️",
  market: "🛍️",
  exhibition: "🎨",
  warning: "⚠️",
};

interface Props {
  supabaseUrl: string;
  supabaseAnonKey: string;
  mapboxToken: string;
  center?: [number, number];
  zoom?: number;
  onSelect?: (ev: MapEvent) => void;
  onEventsChange?: (events: MapEvent[]) => void;
  /** When set, the map eases to this event's location (sidebar click). */
  focusEvent?: MapEvent | null;
  /** Reports current viewport bounds so the sidebar can tag "nearby" events. */
  onViewportChange?: (v: { west: number; east: number; south: number; north: number }) => void;
}

export default function PulseMap({
  supabaseUrl,
  supabaseAnonKey,
  mapboxToken,
  center = [121.5654, 25.033],
  zoom = 12,
  onSelect,
  onEventsChange,
  focusEvent,
  onViewportChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const markersRef = useRef<mapboxgl.Marker[]>([]);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const [status, setStatus] = useState<"loading" | "ready" | "empty">("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Dynamic import of @supabase/supabase-js keeps it client-only.
  // Query an expanded BBox (50% margin) so nearby-but-offscreen events also
  // appear in the sidebar list (rendered as faded "nearby" pins on the map).
  const fetchBBox = useCallback(
    async (map: mapboxgl.Map): Promise<MapEvent[]> => {
      const { createClient } = await import("@supabase/supabase-js");
      const supabase = createClient(supabaseUrl, supabaseAnonKey);
      const b = map.getBounds();
      if (!b) return [];
      const west = b.getWest(), east = b.getEast();
      const south = b.getSouth(), north = b.getNorth();
      const MARGIN = 0.5; // expand each edge by 50% of the span
      const spanX = (east - west) * MARGIN;
      const spanY = (north - south) * MARGIN;
      const { data, error } = await supabase.rpc("get_events_in_bbox", {
        min_lng: west - spanX,
        min_lat: Math.max(south - spanY, -90),
        max_lng: east + spanX,
        max_lat: Math.min(north + spanY, 90),
      });
      if (error) throw error;
      return (data ?? []) as MapEvent[];
    },
    [supabaseUrl, supabaseAnonKey]
  );

  const refresh = useCallback(async (map: mapboxgl.Map) => {
    try {
      const allEvents = await fetchBBox(map);
      const b = map.getBounds()!;
      // split into in-view vs nearby (fetched via expanded bbox)
      const inView = allEvents.filter(
        (ev) => ev.lng >= b.getWest() && ev.lng <= b.getEast() &&
                ev.lat >= b.getSouth() && ev.lat <= b.getNorth()
      );
      const nearby = allEvents.filter((ev) => !inView.includes(ev));

      markersRef.current.forEach((m) => m.remove());
      const makeMarker = (ev: MapEvent, faded: boolean) => {
        const el = document.createElement("div");
        el.textContent =
          CATEGORY_ICON[ev.category] ?? "📍" + (CROWD_EMOJI[ev.crowd_status] ?? "");
        el.style.fontSize = faded ? "18px" : ev.is_verified ? "26px" : "20px";
        el.style.cursor = "pointer";
        if (faded) {
          el.style.opacity = "0.45";
          el.title = `📍 附近：${ev.title}（點擊前往）`;
        } else {
          el.title = `${ev.title} ${CROWD_EMOJI[ev.crowd_status] ?? ""}`;
        }
        el.addEventListener("click", () => onSelect?.(ev));
        return new mapboxgl.Marker({ element: el })
          .setLngLat([ev.lng, ev.lat])
          .addTo(map);
      };
      markersRef.current = [
        ...nearby.map((ev) => makeMarker(ev, true)),
        ...inView.map((ev) => makeMarker(ev, false)),
      ];
      setStatus(allEvents.length === 0 ? "empty" : "ready");
      onEventsChange?.(allEvents);
    } catch (e) {
      console.error("bbox query failed", e);
      setStatus("empty");
    }
  }, [fetchBBox, onSelect, onEventsChange]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    mapboxgl.accessToken = mapboxToken;
    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: "mapbox://styles/mapbox/streets-v12",
      center,
      zoom,
    });
    mapRef.current = map;

    // Surface token / network / style errors instead of hanging on "loading"
    map.on("error", (e) => {
      console.error("mapbox error:", e);
      const detail =
        (e as unknown as { error?: { message?: string } })?.error?.message ??
        "地圖載入失敗";
      setErrorMsg(detail);
    });

    map.on("load", () => {
      const b = map.getBounds();
      if (b) onViewportChange?.({ west: b.getWest(), east: b.getEast(), south: b.getSouth(), north: b.getNorth() });
      void refresh(map);
    });
    // PRD 4.x: 300ms debounce BBox querying on move
    map.on("moveend", () => {
      clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        const b = map.getBounds();
        if (b) onViewportChange?.({ west: b.getWest(), east: b.getEast(), south: b.getSouth(), north: b.getNorth() });
        void refresh(map);
      }, 300);
    });

    return () => {
      clearTimeout(debounceRef.current);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sidebar selection -> ease the camera to the event (PRD 4.4 map focus)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusEvent) return;
    map.flyTo({
      center: [focusEvent.lng, focusEvent.lat],
      zoom: 16,
      speed: 1.2,
      padding: { left: 380, top: 0, right: 0, bottom: 0 }, // keep pin centered in visible area
      essential: true,
    });
  }, [focusEvent]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {status === "loading" && !errorMsg && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-black/60 px-4 py-1 text-sm text-white">
          載入中…
        </div>
      )}
      {errorMsg && (
        <div className="absolute left-1/2 top-3 max-w-[90%] -translate-x-1/2 rounded-full bg-red-600/90 px-4 py-1 text-sm text-white">
          ⚠️ 地圖錯誤：{errorMsg}
        </div>
      )}
      {status === "empty" && (
        <div className="absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-black/60 px-4 py-1 text-sm text-white">
          🍃 周邊目前一片寧靜，暫無進行中的快閃與優惠
        </div>
      )}
    </div>
  );
}
