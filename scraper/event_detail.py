"""Event detail-page parsers — deterministic time/venue extraction.

Ported from bouob/tickets_hunter selector knowledge (src/platforms/*.py,
src/util.py). The hunter is a buying bot, but its value for this project is
hard-won DOM knowledge: which selectors actually hold the session datetime
and venue on each ticketing site.

Strategy per platform:
  - KKTIX event pages are server-rendered: JSON-LD Event block +++
    span.timezoneSuffix elements. Plain httpx works, no browser needed.
  - TixCraft / TicketPlus / iBon / others are JS-rendered: they need a
    headless browser (tickets_hunter uses zendriver). Stubs are registered
    in DETAIL_PARSERS so they plug in later; fetch_* raises NotImplementedError
    until a browser backend lands.

KKTIX page facts (verified 2026-09-15 against live HTML):
  - <script type="application/ld+json"> with @type Event:
    name, startDate/endDate (ISO +08:00), location{name,address},
    offers[].name per session, e.g. "台中場 8/10(一)" (= venue hint + date).
  - span.timezoneSuffix: "2026/08/10(周一) 10:00(+0800)".
  - Multi-session list pages (tickets_hunter kktix.py): div.event-list
    ul.clearfix > li, date in span.timezoneSuffix (fallback .event-info > a > p).

Normalization ported from tickets_hunter src/util.py:
  full2half (全形→半形 incl. U+3000), whitespace collapse. Needed because
  pages mix 全形 digits/parens with 半形.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}
TZ8 = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# Normalization (tickets_hunter util.full2half + whitespace collapse)
# ---------------------------------------------------------------------------

def full2half(s: str) -> str:
    out = []
    for ch in s or "":
        n = ord(ch)
        if n == 0x3000:
            n = 32
        elif 0xFF01 <= n <= 0xFF5E:
            n -= 0xFEE0
        out.append(chr(n))
    return "".join(out)


WS_RE = re.compile(r"\s+")

def norm(s: str) -> str:
    return WS_RE.sub(" ", full2half(s)).strip()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class EventSession:
    """One sellable session inside an event page."""
    label: str = ""          # offer name, e.g. "台中場 8/10(一)"
    venue_hint: str = ""     # venue part of label, e.g. "台中場"
    start: datetime | None = None
    end: datetime | None = None


@dataclass
class EventDetail:
    url: str = ""
    title: str = ""
    start: datetime | None = None
    end: datetime | None = None
    venue_name: str = ""
    venue_address: str = ""
    sessions: list[EventSession] = field(default_factory=list)
    raw_text: str = ""       # extra page text (og:description) for the LLM

    @property
    def has_time(self) -> bool:
        return self.start is not None and self.end is not None


# ---------------------------------------------------------------------------
# Time parsing — "2026/08/10(周一) 10:00(+0800)" / ISO 8601
# ---------------------------------------------------------------------------

SUFFIX_RE = re.compile(
    r"(\d{4})/(\d{1,2})/(\d{1,2})"      # date
    r"(?:\([^)]*\))?"                    # optional (周一) weekday
    r"\s*(\d{1,2}):(\d{2})"              # time
    r"(?:\([^)]*([+-]\d{4}|[+-]\d{2}:?\d{2})[^)]*\))?"  # optional (+0800)
)

def parse_suffix_ts(s: str) -> datetime | None:
    m = SUFFIX_RE.search(norm(s))
    if not m:
        return None
    y, mo, d, hh, mm = (int(m.group(i)) for i in (1, 2, 3, 4, 5))
    try:
        return datetime(y, mo, d, int(hh), int(mm), tzinfo=TZ8)
    except ValueError:
        return None


def parse_iso_ts(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ8)
    return dt


# Offer labels like "台中場 8/10(一)" / "台北場 8/17" — split venue / date hint.
OFFER_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})(?:\([^)]*\))?\s*$")

def split_offer_label(label: str) -> tuple[str, str]:
    label = norm(label)
    m = OFFER_DATE_RE.search(label)
    if not m:
        return label, ""
    return label[: m.start()].strip(" -·•"), m.group(0)


def apply_offer_date_hints(det: EventDetail) -> None:
    """Offer labels often carry the true per-session date (8/10, 8/17, 9/15)
    while the ticket-row spans repeat the sale window. Rebuild session times
    as event-year + offer M/D + row/event HH:MM."""
    if not det.sessions or det.start is None:
        return
    # Only rebuild when row times look like a repeated sale window (all equal)
    starts = {s.start for s in det.sessions if s.start is not None}
    if len(starts) > 1:
        return  # rows already carry distinct times; trust them
    base = det.sessions[0].start or det.start
    for sess in det.sessions:
        m = OFFER_DATE_RE.search(sess.label)
        if not m:
            continue
        try:
            rebuilt = datetime(det.start.year, int(m.group(1)), int(m.group(2)),
                               base.hour, base.minute, tzinfo=TZ8)
        except ValueError:
            continue
        sess.start = rebuilt
        sess.end = rebuilt


# ---------------------------------------------------------------------------
# KKTIX
# ---------------------------------------------------------------------------

JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.S | re.I,
)
SUFFIX_SPAN_RE = re.compile(
    r'<span[^>]*class="[^"]*timezoneSuffix[^"]*"[^>]*>(.*?)</span>', re.S | re.I
)
TAG_RE = re.compile(r"<[^>]+>")
OG_RE = re.compile(
    r'<meta[^>]*property="og:(?:description|title)"[^>]*content="([^"]*)"',
    re.I,
)


def _strip_tags(s: str) -> str:
    return norm(TAG_RE.sub(" ", s or ""))


def _find_event_block(obj) -> dict | None:
    """Depth-first search for the @type Event node."""
    if isinstance(obj, dict):
        t = obj.get("@type", "")
        types = t if isinstance(t, list) else [t]
        if "Event" in types:
            return obj
        for v in obj.values():
            hit = _find_event_block(v)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_event_block(v)
            if hit is not None:
                return hit
    return None


def parse_kktix_detail(html: str, url: str = "") -> EventDetail:
    det = EventDetail(url=url)

    # 1. JSON-LD Event block (most reliable: ISO times + venue + offers)
    for m in JSONLD_RE.finditer(html or ""):
        try:
            data = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        ev = _find_event_block(data)
        if not ev:
            continue
        det.title = norm(str(ev.get("name", "") or det.title))
        det.start = parse_iso_ts(str(ev.get("startDate", ""))) or det.start
        det.end = parse_iso_ts(str(ev.get("endDate", ""))) or det.end
        loc = ev.get("location") or {}
        if isinstance(loc, dict):
            det.venue_name = norm(str(loc.get("name", "")))
            det.venue_address = norm(str(loc.get("address", "")))
        for offer in ev.get("offers") or []:
            if not isinstance(offer, dict):
                continue
            label = norm(str(offer.get("name", "")))
            if not label:
                continue
            venue_hint, _date_hint = split_offer_label(label)
            sess = EventSession(label=label, venue_hint=venue_hint)
            vt = offer.get("validThrough", "")
            # validThrough is the sale window, not the show time — keep only
            # as a last-resort end marker, never as session start.
            if vt and det.end is None:
                det.end = parse_iso_ts(str(vt))
            det.sessions.append(sess)
        break  # first Event block wins

    # 2. span.timezoneSuffix — exact per-occurrence show times.
    #    Order on page: activity range (start ~ end) then one per ticket row.
    suffix_times = []
    for m in SUFFIX_SPAN_RE.finditer(html or ""):
        ts = parse_suffix_ts(_strip_tags(m.group(1)))
        if ts:
            suffix_times.append(ts)
    if suffix_times:
        if det.start is None:
            det.start = suffix_times[0]
        # ticket-row times (index 2+) map onto sessions in order
        row_times = suffix_times[2:] if len(suffix_times) > 2 else []
        for sess, ts in zip(det.sessions, row_times):
            sess.start = ts
            sess.end = ts  # single show; LLM/pipeline expands if needed
        if det.end is None and len(suffix_times) > 1:
            det.end = suffix_times[1]

    # 3. og:description as extra LLM context (often holds venue + transit info)
    og_bits = [_strip_tags(x) for x in OG_RE.findall(html or "")]
    det.raw_text = norm(" ".join(b for b in og_bits if b))[:1000]

    # Sessions without own time inherit the event window
    for sess in det.sessions:
        if sess.start is None:
            sess.start, sess.end = det.start, det.end
    apply_offer_date_hints(det)
    return det


def fetch_kktix_detail(url: str, timeout: int = 30) -> EventDetail:
    """Fetch + parse a KKTIX event page. Retries on Cloudflare 403s."""
    last_err = "unknown"
    for attempt in range(3):
        try:
            r = httpx.get(url, headers=HEADERS, timeout=timeout,
                          follow_redirects=True)
            if r.status_code == 200:
                return parse_kktix_detail(r.text, url)
            last_err = f"HTTP {r.status_code}"
        except Exception as e:  # network blip
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"KKTIX detail unavailable ({url}): {last_err}")


# ---------------------------------------------------------------------------
# Feed-text meta — KKTIX Atom <content> embeds a structured line:
#   時間：2026/09/08 08:00(+0800) ~ 2026/09/16 08:00(+0800)
#   地點：元智一館 R1401A教室 / 桃園市中壢區遠東路135號
# This beats the detail page: JSON-LD startDate is often the SALE window,
# while the feed 地點 carries the full street address.
# ---------------------------------------------------------------------------

FEED_TIME_RE = re.compile(r"時間\s*[：:]\s*(.+?)\s*~\s*(\S+?)(?:\s|$)")
FEED_VENUE_RE = re.compile(r"地點\s*[：:]\s*(.+?)(?:\s{2,}|\n|$)")
TITLE_DT_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})")


def parse_feed_meta(feed_text: str) -> dict:
    """Deterministic time + venue/address straight from the Atom content."""
    t = norm(feed_text or "")
    out: dict = {}
    m = FEED_TIME_RE.search(t)
    if m:
        out["start"] = parse_suffix_ts(m.group(1))
        out["end"] = parse_suffix_ts(m.group(2))
    m = FEED_VENUE_RE.search(t)
    if m:
        parts = [p.strip() for p in m.group(1).split("/") if p.strip()]
        if parts:
            out["venue"] = parts[0]
        if len(parts) > 1:
            out["address"] = " / ".join(parts[1:])
    return out


def parse_title_datetime(title: str, ref: datetime | None) -> datetime | None:
    """Explicit 'M/D HH:MM' in the title (e.g. '9/16 12:15') = show time.
    tickets_hunter never needed this (it buys, doesn't index), but for a
    map the title hint beats the sale window when they disagree."""
    m = TITLE_DT_RE.search(norm(title or ""))
    if not m:
        return None
    year = ref.year if ref is not None else datetime.now(TZ8).year
    try:
        dt = datetime(year, int(m.group(1)), int(m.group(2)),
                      int(m.group(3)), int(m.group(4)), tzinfo=TZ8)
    except ValueError:
        return None
    if dt < datetime.now(TZ8) - timedelta(days=1):
        try:
            dt = dt.replace(year=year + 1)
        except ValueError:
            return None
    return dt


def refine_with_feed_meta(det: EventDetail, title: str, feed_text: str) -> EventDetail:
    """Merge feed-line facts into the detail parse. Priority:
    title show-time hint > feed 時間 > JSON-LD/suffix window;
    JSON-LD venue > feed 地點+address > offer venue_hint."""
    meta = parse_feed_meta(feed_text)
    if meta.get("start") and det.start is None:
        det.start = meta["start"]
    if meta.get("end") and det.end is None:
        det.end = meta["end"]
    if not det.venue_name and meta.get("venue"):
        det.venue_name = meta["venue"]
    if not det.venue_address and meta.get("address"):
        det.venue_address = meta["address"]

    # Title show-time override: applies when the event has at most one
    # distinct session date (single-show events whose detail window is
    # actually the registration period, e.g. 09/08~09/16 sale, show 9/16).
    hint = parse_title_datetime(title, det.start)
    if hint is not None:
        session_dates = {s.start.date() for s in det.sessions if s.start} or \
                        ({det.start.date()} if det.start else set())
        if len(session_dates) <= 1 and (hint.date() not in session_dates):
            det.start = hint
            det.end = hint  # fan-out expands to +3h estimated
            for s in det.sessions:
                s.start = hint
                s.end = hint
    return det


# ---------------------------------------------------------------------------
# Registry — other ticketing sites plug in here.
# TixCraft/TicketPlus/iBon/FamiTicket/KHAM are JS-rendered: plain httpx gets
# an empty shell, so their fetchers need tickets_hunter's zendriver browser
# backend (not available in CI/plain-Python). Parser stubs stay behind the
# same interface so the pipeline code doesn't change when they land.
# ---------------------------------------------------------------------------

def _not_impl(site: str, url: str):  # noqa: ARG001
    raise NotImplementedError(
        f"{site} pages are JS-rendered; needs zendriver browser backend "
        "(see tickets_hunter src/nodriver_common.py). Plain httpx cannot parse them."
    )


DETAIL_PARSERS = {
    # site key -> {"fetch": fn(url)->EventDetail, "rendered_js": bool}
    "kktix": {"fetch": fetch_kktix_detail, "rendered_js": False},
    "tixcraft": {"fetch": lambda url: _not_impl("tixcraft", url), "rendered_js": True},
    "ticketplus": {"fetch": lambda url: _not_impl("ticketplus", url), "rendered_js": True},
    "ibon": {"fetch": lambda url: _not_impl("ibon", url), "rendered_js": True},
    "famiticket": {"fetch": lambda url: _not_impl("famiticket", url), "rendered_js": True},
    "kham": {"fetch": lambda url: _not_impl("kham", url), "rendered_js": True},
}
