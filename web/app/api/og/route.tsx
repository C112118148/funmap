import { ImageResponse } from "next/og";
import { readFileSync } from "fs";
import { join } from "path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Windows bug workaround: @vercel/og's internal fileURLToPath mangles the
// bundled font path (".\file:\C:\..."), so we load the font bytes ourselves.
const fontPath = join(
  process.cwd(),
  "node_modules",
  "next",
  "dist",
  "compiled",
  "@vercel",
  "og",
  "noto-sans-v27-latin-regular.ttf"
);
const notoSans = readFileSync(fontPath);

const CROWD: Record<string, string> = {
  comfortable: "🟢 舒適好逛",
  moderate: "🟡 略顯擁擠",
  crowded: "🔴 大排長龍",
};
const ICON: Record<string, string> = {
  promotion: "🏷️",
  market: "🛍️",
  exhibition: "🎨",
  warning: "⚠️",
};

export async function GET(req: Request): Promise<Response> {
  const { searchParams } = new URL(req.url);
  const title = (searchParams.get("title") ?? "在地即時情報地圖").slice(0, 60);
  const crowd = searchParams.get("crowd") ?? "comfortable";
  const category = searchParams.get("category") ?? "market";

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "linear-gradient(135deg,#064e3b 0%,#0f766e 100%)",
          padding: 64,
          color: "white",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ fontSize: 28, opacity: 0.85 }}>
          📍 在地即時情報地圖 · Local Info Pulse Map
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div style={{ fontSize: 30 }}>{ICON[category] ?? "📍"}</div>
          <div style={{ fontSize: 64, fontWeight: 700, lineHeight: 1.15 }}>
            {title}
          </div>
          <div style={{ fontSize: 36 }}>{CROWD[crowd] ?? ""}</div>
        </div>
      </div>
    ),
    { width: 1200, height: 630, fonts: [{ name: "sans-serif", data: notoSans, style: "normal" }] }
  );
}
