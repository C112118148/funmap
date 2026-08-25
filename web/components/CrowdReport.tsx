"use client";

import { useEffect, useRef, useState } from "react";
import { createClient } from "@supabase/supabase-js";
import type { MapEvent } from "./PulseMap";

const CROWD: Record<string, string> = {
  comfortable: "🟢 舒適好逛",
  moderate: "🟡 略顯擁擠",
  crowded: "🔴 大排長龍",
};

const DEVICE_KEY = "local-pulse-device-uuid";
const ELIGIBLE_RADIUS_M = 50;   // PRD 5.2
const DWELL_REQUIRED_S = 180;   // PRD 5.2: >3 minutes continuous presence

function getDeviceUuid(): string {
  let id = localStorage.getItem(DEVICE_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(DEVICE_KEY, id);
  }
  return id;
}

function haversineMeters(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

type GeoState =
  | { state: "checking" }
  | { state: "unsupported" }
  | { state: "denied" }
  | { state: "far"; distance: number }
  | { state: "near"; distance: number };

/**
 * PRD 5.2 full eligibility:
 *  1. viewed this event within 48h (recorded via record_event_view)
 *  2. geolocation within 50m AND continuous dwell >3min
 *     (server-signed arrival token proves when presence started)
 */
export function CrowdReport({
  event,
  onReported,
}: {
  event: MapEvent;
  onReported?: (newStatus: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<"ok" | "rate_limited" | "error" | null>(null);
  const [reward, setReward] = useState<string | null>(null);
  const [geo, setGeo] = useState<GeoState>({ state: "checking" });
  const [dwellLeft, setDwellLeft] = useState<number | null>(null);
  const arrivalToken = useRef<string | null>(null);
  const watchId = useRef<number | null>(null);
  const dwellTimer = useRef<ReturnType<typeof setInterval>>();

  const isSafetyWarning = event.category === "warning";

  useEffect(() => {
    if (isSafetyWarning) return;
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !key) return;

    let cancelled = false;
    const device = getDeviceUuid();
    const supabase = createClient(url, key);

    // register device, then record this view (48h-view eligibility)
    (async () => {
      try {
        const { error } = await supabase.rpc("register_device", {
          p_device_uuid: device,
          p_recovery_code: makeRecoveryCode(),
        });
        if (error || cancelled) return;
        await supabase.rpc("record_event_view", { p_event_id: event.id, p_device_uuid: device });
      } catch (e) {
        console.error("device/view registration failed", e);
      }
    })();

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [event.id, isSafetyWarning]);

  // presence watching with dwell timer
  useEffect(() => {
    if (isSafetyWarning) return;
    if (!("geolocation" in navigator)) {
      setGeo({ state: "unsupported" });
      return;
    }

    const onPosition = async (pos: GeolocationPosition) => {
      if (pos.coords.accuracy > 200) return; // implausible accuracy — ignore
      const dist = haversineMeters(pos.coords.latitude, pos.coords.longitude, event.lat, event.lng);
      if (dist <= ELIGIBLE_RADIUS_M) {
        setGeo({ state: "near", distance: dist });
        // first confirmation -> request server arrival token, start dwell clock
        if (!arrivalToken.current && !dwellTimer.current) {
          try {
            const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
            const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
            const { data } = await createClient(url, key).rpc("begin_presence_check", {
              p_event_id: event.id,
              p_device_uuid: getDeviceUuid(),
            });
            arrivalToken.current = data as string;
          } catch (e) {
            console.error("presence token failed", e);
            return;
          }
          const startedAt = Date.now();
          dwellTimer.current = setInterval(() => {
            const elapsed = Math.floor((Date.now() - startedAt) / 1000);
            setDwellLeft(Math.max(0, DWELL_REQUIRED_S - elapsed));
            if (elapsed >= DWELL_REQUIRED_S && dwellTimer.current) {
              clearInterval(dwellTimer.current);
              dwellTimer.current = undefined;
              setDwellLeft(0);
            }
          }, 1000);
        }
      } else {
        // left the area — reset dwell proof
        setGeo({ state: "far", distance: dist });
        if (arrivalToken.current) {
          arrivalToken.current = null;
          if (dwellTimer.current) { clearInterval(dwellTimer.current); dwellTimer.current = undefined; }
          setDwellLeft(null);
        }
      }
    };
    const onError = () => setGeo({ state: "denied" });

    watchId.current = navigator.geolocation.watchPosition(onPosition, onError, {
      enableHighAccuracy: true, timeout: 10000, maximumAge: 15000,
    });
    return () => {
      if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current);
      if (dwellTimer.current) clearInterval(dwellTimer.current);
    };
  }, [event.lat, event.lng, isSafetyWarning]);

  // gate rendering
  if (isSafetyWarning) return null;
  if (geo.state === "checking") return null;
  if (geo.state !== "near") return null;           // desktop / denied / far: no survey
  const dwellDone = dwellLeft !== null && dwellLeft <= 0;
  if (!dwellDone) {
    return (
      <div className="mt-3 border-t pt-3 text-xs text-slate-600">
        📍 偵測到你在現場——持續停留 {Math.ceil(dwellLeft ?? DWELL_REQUIRED_S)} 秒後即可回報人潮
      </div>
    );
  }

  const submit = async (status: string) => {
    setBusy(true);
    setResult(null);
    try {
      const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
      const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
      const { data, error } = await createClient(url, key).rpc("submit_crowd_report", {
        p_event_id: event.id,
        p_status: status,
        p_device_uuid: getDeviceUuid(),
        p_arrival_token: arrivalToken.current,
        p_dwell_seconds: DWELL_REQUIRED_S,
      });
      if (error) throw error;
      const res = data as { ok: boolean; reason?: string; ad_free_until?: string; reward_granted?: boolean };
      if (res.ok) {
        setResult("ok");
        setReward(res.reward_granted ? res.ad_free_until ?? null : null);
        onReported?.(status === "ended_early" ? event.crowd_status : status);
      } else {
        setResult(res.reason === "rate_limited" ? "rate_limited" : "error");
      }
    } catch (e) {
      console.error(e);
      setResult("error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 border-t pt-3">
      <p className="text-sm font-medium text-slate-800">
        你就在現場（{Math.round(geo.distance)}公尺內）——現在的狀況是？
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {(["comfortable", "moderate", "crowded", "ended_early"] as const).map((s) => (
          <button
            key={s}
            disabled={busy}
            onClick={() => void submit(s)}
            className="rounded-full border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-800 transition hover:border-emerald-500 hover:bg-emerald-50 disabled:opacity-50"
          >
            {CROWD[s] ?? "⚪ 已結束/售罄"}
          </button>
        ))}
      </div>
      <p className="mt-1.5 text-[11px] text-slate-600">
        回報可獲得 +24h 廣告免費瀏覽（每日上限 24 小時）
      </p>
      {result === "ok" && (
        <p className="mt-1 text-xs font-medium text-emerald-700">
          ✅ 感謝回報！{reward
            ? `廣告免費至 ${new Date(reward).toLocaleString("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false })}`
            : "今日獎勵已領取過"}
        </p>
      )}
      {result === "rate_limited" && (
        <p className="mt-1 text-xs font-medium text-amber-700">⏳ 你已在 3 小時內回報過此活動</p>
      )}
      {result === "error" && (
        <p className="mt-1 text-xs font-medium text-red-700">回報失敗，請稍後再試</p>
      )}
    </div>
  );
}

/** PRD 5.1 — 6-char recovery code in A7K-992X format (shown once to the user later). */
function makeRecoveryCode(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no I/O/0/1 ambiguity
  const pick = () => chars[Math.floor(Math.random() * chars.length)];
  return `${pick()}${pick()}${pick()}-${pick()}${pick()}${pick()}${pick()}`;
}
