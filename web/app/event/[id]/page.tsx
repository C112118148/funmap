import type { Metadata } from "next";
import { createClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

interface EventRow {
  id: string;
  title: string;
  summary: string | null;
  category: string;
  crowd_status: string;
  source_url?: string | null;
  start_time: string;
  end_time: string;
  is_verified: boolean;
}

async function getEvent(id: string): Promise<EventRow | null> {
  if (!SUPABASE_URL || !ANON_KEY) return null;
  const supabase = createClient(SUPABASE_URL, ANON_KEY);
  const { data } = await supabase
    .from("events")
    .select("*")
    .eq("id", id)
    .single();
  return (data as EventRow) ?? null;
}

// PRD 6.x — Google-compliant Event JSON-LD structured data
function eventJsonLd(ev: EventRow) {
  return {
    "@context": "https://schema.org",
    "@type": "Event",
    name: ev.title,
    description: ev.summary ?? undefined,
    startDate: ev.start_time,
    endDate: ev.end_time,
    eventStatus:
      ev.crowd_status === "crowded"
        ? "https://schema.org/EventScheduled"
        : "https://schema.org/EventScheduled",
    eventAttendanceMode: "https://schema.org/OfflineEventAttendanceMode",
  };
}

export async function generateMetadata({
  params,
}: {
  params: { id: string };
}): Promise<Metadata> {
  const ev = await getEvent(params.id);
  if (!ev) return { title: "活動不存在" };
  return {
    title: ev.title,
    description: ev.summary ?? undefined,
    openGraph: {
      title: ev.title,
      images: [
        `/api/og?${new URLSearchParams({
          title: ev.title,
          crowd: ev.crowd_status,
          category: ev.category,
        }).toString()}`,
      ],
    },
  };
}

export default async function EventPage({
  params,
}: {
  params: { id: string };
}) {
  const ev = await getEvent(params.id);

  if (!ev) {
    return (
      <main className="flex min-h-screen items-center justify-center text-slate-500">
        活動不存在或已結束
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-xl p-6">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(eventJsonLd(ev)) }}
      />
      <h1 className="text-2xl font-bold">{ev.title}</h1>
      <p className="mt-2 text-sm text-slate-500">
        🗓️{" "}
        {new Date(ev.start_time).toLocaleString("zh-TW", { hour12: false })} –{" "}
        {new Date(ev.end_time).toLocaleString("zh-TW", { hour12: false })}
      </p>
      <p className="mt-1">
        人潮：{
          { comfortable: "🟢 舒適好逛", moderate: "🟡 略顯擁擠", crowded: "🔴 大排長龍" }[
            ev.crowd_status
          ]
        }
        {!ev.is_verified && (
          <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-600">
            社群網友情報（尚未核實）
          </span>
        )}
      </p>
      {ev.summary && <p className="mt-4 text-slate-700">{ev.summary}</p>}

      {/* source attribution */}
      <div className="mt-6 border-t pt-4 text-sm">
        <span className={ev.is_verified ? "font-medium text-emerald-700" : "text-slate-600"}>
          {ev.is_verified ? "✔ 已核實來源" : "❓ 社群網友情報（尚未核實）"}
        </span>
        {ev.source_url && (
          <a
            href={ev.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-3 text-sky-700 underline decoration-sky-400 hover:text-sky-900"
          >
            🔗 查看原文 ↗
          </a>
        )}
      </div>
    </main>
  );
}
