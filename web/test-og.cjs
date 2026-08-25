// Test: does standalone @vercel/og work on Windows? (JSX-free)
const { readFileSync } = require("fs");
const { join } = require("path");
(async () => {
  try {
    const mod = await import("@vercel/og");
    const ImageResponse = mod.ImageResponse ?? mod.default?.ImageResponse ?? mod.default;
    const font = readFileSync(
      join(__dirname, "node_modules", "next", "dist", "compiled", "@vercel", "og", "noto-sans-v27-latin-regular.ttf")
    );
    const el = {
      type: "div",
      props: {
        style: { width: "100%", height: "100%", display: "flex", background: "#065f46", color: "#ffffff", fontSize: 80 },
        children: "test",
      },
    };
    const r = new ImageResponse(el, {
      width: 1200,
      height: 630,
      fonts: [{ name: "sans-serif", data: font, style: "normal" }],
    });
    const buf = Buffer.from(await r.arrayBuffer());
    console.log("OK standalone @vercel/og:", buf.length, "bytes");
  } catch (e) {
    console.error("STANDALONE FAILED:", e.message);
  }
})();
