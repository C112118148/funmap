"use client";

import { useState } from "react";
import type { MapEvent } from "./PulseMap";
import { CrowdReport } from "./CrowdReport";

const CROWD: Record<string, { label: string; cls: string }> = {
  comfortable: { label: "🟢 舒適好逛", cls: "bg-emerald-100 text-emerald-800" },
  moderate: { label: "🟡 略顯擁擠", cls: "bg-amber-100 text-amber-800" },
  crowded: { label: "🔴 大排長龍", cls: "bg-red-100 text-red-800" },
};

export const CATEGORY_LABEL: Record<string, string> = {
  promotion: "🏷️ 快閃優惠",
  market: "🛍️ 市集",
  exhibition: "🎨 展覽",
  warning: "⚠️ 安全警示",
};

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-TW", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

interface Props {
  event: MapEvent;
  /** Expanded cards show full details incl. source link; collapsed are compact. */
  expanded?: boolean;
  /** Nearby = outside current viewport but within the expanded search radius. */
  nearby?: boolean;
  onClick?: () => void;
}

export default function EventCard({ event, expanded = false, nearby = false, onClick }: Props) {
  const [crowdOverride, setCrowdOverride] = useState<string | null>(null);
  const crowd = CROWD[crowdOverride ?? event.crowd_status] ?? CROWD.comfortable;

  return (
    <button
      onClick={onClick}
      className={`w-full rounded-xl border p-3 text-left transition ${
        expanded
          ? "border-emerald-500 bg-emerald-50 ring-1 ring-emerald-400"
          : nearby
            ? "border-dashed border-slate-300 bg-white opacity-80 hover:border-slate-400 hover:opacity-100"
            : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
      }`}
    >
      {/* title + crowd badge */}
      <div className="flex items-start justify-between gap-2">
        <span className="text-base font-bold text-slate-900">{event.title}</span>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${crowd.cls}`}>
          {crowd.label}
        </span>
      </div>

      {/* category + time */}
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-700">
        {nearby && (
          <span className="rounded bg-sky-100 px-1.5 py-0.5 text-xs font-medium text-sky-800">
            📍 附近（畫面外）
          </span>
        )}
        <span className="font-medium">{CATEGORY_LABEL[event.category] ?? event.category}</span>
        <span>🗓️ {fmtTime(event.start_time)} – {fmtTime(event.end_time)}</span>
      </div>

      {expanded ? (
        <>
          {/* badges */}
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            {!event.is_verified && (
              <span className="rounded border border-dashed border-slate-400 px-1.5 py-0.5 text-slate-700">
                社群網友情報（尚未核實）
              </span>
            )}
            {event.is_time_estimated && (
              <span className="rounded bg-yellow-100 px-1.5 py-0.5 font-medium text-yellow-800">
                🕒 主辦方未公布具體時段
              </span>
            )}
          </div>

          {/* summary */}
          {event.summary && !event.is_time_estimated && (
            <p className="mt-2 text-sm leading-relaxed text-slate-700">{event.summary}</p>
          )}

          {/* source */}
          <div className="mt-2 flex items-center gap-3 text-xs">
            <span className={event.is_verified ? "text-emerald-700" : "text-slate-600"}>
              {event.is_verified ? "✔ 已核實來源" : "❓ 未核實情報"}
            </span>
            {event.source_url && (
              <a
                href={event.source_url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="text-sky-700 underline decoration-sky-400 hover:text-sky-900"
              >
                🔗 查看原文 ↗
              </a>
            )}
          </div>

          {/* collapse hint */}
          <p className="mt-2 text-right text-[11px] text-slate-500">點選其他活動可收合</p>

          {/* PRD 5.2 crowd survey */}
          <CrowdReport event={event} onReported={(s) => setCrowdOverride(s)} />
        </>
      ) : (
        <p className="mt-1 text-xs text-slate-600">點擊展開詳細資訊 →</p>
      )}
    </button>
  );
}
