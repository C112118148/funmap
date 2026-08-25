"use client";

import { useRef, useState } from "react";
import html2canvas from "html2canvas";
import QRCode from "qrcode";
import type { MapEvent } from "./PulseMap";

const CROWD: Record<string, string> = {
  comfortable: "🟢 舒適好逛",
  moderate: "🟡 略顯擁擠",
  crowded: "🔴 大排長龍",
};

/**
 * PRD 6.2 — Client-side card synthesis:
 * renders a hidden share card (1:1 or 4:5) with event info + QR code
 * linking to /event/[id], then downloads it via html2canvas.
 */
export function ShareButton({ event }: { event: MapEvent }) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);

  const download = async (ratio: "1:1" | "4:5") => {
    if (!cardRef.current) return;
    setBusy(true);
    try {
      const siteUrl = `${window.location.origin}/event/${event.id}`;
      const qrDataUrl = await QRCode.toDataURL(siteUrl, { margin: 1, width: 160 });
      const qrImg = cardRef.current.querySelector<HTMLImageElement>("#share-qr");
      if (qrImg) qrImg.src = qrDataUrl;

      // apply ratio height before capture
      cardRef.current.style.height = ratio === "1:1" ? "400px" : "500px";

      const canvas = await html2canvas(cardRef.current, {
        scale: 2,
        backgroundColor: "#ffffff",
      });
      const link = document.createElement("a");
      link.download = `event-${event.id.slice(0, 8)}-${ratio === "1:1" ? "square" : "portrait"}.png`;
      link.href = canvas.toDataURL("image/png");
      link.click();
    } finally {
      setBusy(false);
    }
  };

  const start = new Date(event.start_time);

  return (
    <>
      {/* offscreen render target for html2canvas */}
      <div
        ref={cardRef}
        style={{ width: 400, height: 400 }}
        className="fixed -left-[9999px] top-0 flex flex-col justify-between rounded-2xl bg-white p-6"
      >
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-emerald-600">
            在地即時情報
          </div>
          <h3 className="mt-2 text-2xl font-bold leading-snug">{event.title}</h3>
          <p className="mt-2 text-sm text-slate-500">
            🗓️ {start.toLocaleString("zh-TW", { hour12: false })}
          </p>
          <p className="mt-1 text-base">{CROWD[event.crowd_status] ?? ""}</p>
        </div>
        <div className="flex items-end justify-between">
          <img id="share-qr" alt="QR" width={120} height={120} />
          <span className="text-right text-xs text-slate-400">
            掃描查看即時人潮
            <br />
            Local Info Pulse Map
          </span>
        </div>
      </div>

      <button
        disabled={busy}
        onClick={() => void download("1:1")}
        className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
      >
        {busy ? "生成中…" : "📸 產生分享卡片"}
      </button>
    </>
  );
}
