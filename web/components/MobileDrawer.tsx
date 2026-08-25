"use client";

import { useRef, useState } from "react";
import type { MapEvent } from "./PulseMap";
import { ShareButton } from "./ShareCard";

/**
 * PRD 4.4 — Mobile 3-stage gesture drawer (0% / 45% / 95%).
 * Snap points via CSS translate; drag handle toggles stages.
 * At 95% the drawer overlays the bottom banner with z-index (never display:none).
 */
export default function MobileDrawer({ event }: { event: MapEvent | null }) {
  const [stage, setStage] = useState<0 | 45 | 95>(0);
  const startY = useRef<number | null>(null);

  const heights: Record<0 | 45 | 95, string> = {
    0: "0%",
    45: "45%",
    95: "95%",
  };

  const onDragEnd = () => {
    if (startY.current === null) return;
    startY.current = null;
    // simple tap/cycle behavior: closed -> half -> full -> closed
    setStage((s) => (s === 0 ? 45 : s === 45 ? 95 : 0));
  };

  return (
    <>
      {/* peek tab when closed */}
      {stage === 0 && (
        <button
          onClick={() => setStage(45)}
          className="fixed bottom-16 left-1/2 z-40 -translate-x-1/2 rounded-full bg-slate-900 px-5 py-2 text-sm text-white shadow-lg md:hidden"
        >
          活動詳情
        </button>
      )}

      <div
        className="fixed bottom-0 left-0 right-0 z-50 rounded-t-2xl bg-white shadow-2xl transition-[height] duration-300 md:hidden"
        style={{ height: heights[stage] }}
        onTouchStart={(e) => (startY.current = e.touches[0].clientY)}
        onTouchEnd={onDragEnd}
      >
        {/* drag handle */}
        <div
          className="flex cursor-pointer items-center justify-center py-2"
          onClick={() => setStage((s) => (s === 95 ? 0 : s === 45 ? 95 : 45))}
        >
          <div className="h-1.5 w-12 rounded-full bg-slate-300" />
        </div>

        {stage > 0 && (
          <div className="h-full overflow-y-auto px-5 pb-24 [overscroll-behavior:contain]">
            {event ? (
              <>
                <h2 className="text-lg font-bold text-slate-900">{event.title}</h2>
                <p className="mt-1 text-sm font-medium text-slate-700">
                  {event.category} · 人潮{" "}
                  {
                    { comfortable: "🟢 舒適", moderate: "🟡 擁擠", crowded: "🔴 爆滿" }[
                      event.crowd_status
                    ]
                  }
                </p>
                {event.summary && (
                  <p className="mt-3 text-sm text-slate-600">{event.summary}</p>
                )}
                <div className="mt-4">
                  <ShareButton event={event} />
                </div>
              </>
            ) : (
              <p className="pt-6 text-center text-sm text-slate-400">
                🍃 周邊目前一片寧靜，暫無進行中的快閃與優惠
              </p>
            )}
          </div>
        )}
      </div>
    </>
  );
}
