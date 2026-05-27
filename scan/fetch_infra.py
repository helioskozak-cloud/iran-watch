"""
fetch_infra.py — pulls infrastructure data for the situation monitor:
- Brent crude oil price (via yfinance)
- WTI crude price
- USD/IRR rate (parallel market via news where possible)
- A hand-maintained treaty / ceasefire tracker
- Recent tanker/shipping incidents extracted from headlines

Writes docs/data/infra.json
"""
import json
import re
import hashlib
import socket
import warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser

socket.setdefaulttimeout(15)
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA = ROOT / "docs" / "data"
OUT = DATA / "infra.json"
SITUATION = DATA / "situation.json"


# ── Treaty / ceasefire tracker (hand-maintained) ─────────────────────────────
# Status: HOLDING (currently respected), VIOLATED (broken but still nominally in force),
# EXPIRED (period ended without renewal), COLLAPSED (withdrawn/scrapped), UNCERTAIN
TREATIES = [
    {
        "name": "JCPOA — Iran Nuclear Deal",
        "parties": ["Iran", "P5+1"],
        "signed": "2015-07-14",
        "status": "COLLAPSED",
        "last_event": "2018-05-08",
        "notes": "US withdrew unilaterally May 2018. Iran began exceeding limits in 2019. Indirect talks have stalled."
    },
    {
        "name": "Israel–Hezbollah Ceasefire (Nov 2024)",
        "parties": ["Israel", "Hezbollah", "Lebanon (mediator: US/France)"],
        "signed": "2024-11-27",
        "status": "UNCERTAIN",
        "last_event": "2026-05-26",
        "notes": "60-day initial period with phased IDF withdrawal. Both sides report intermittent violations; broader picture stays largely in force."
    },
    {
        "name": "Iran–Saudi Diplomatic Restoration",
        "parties": ["Iran", "Saudi Arabia (mediator: China)"],
        "signed": "2023-03-10",
        "status": "HOLDING",
        "last_event": "2026-04-01",
        "notes": "Beijing-brokered rapprochement; embassies reopened. Tensions persist over proxy conflicts."
    },
    {
        "name": "Israel–UAE Abraham Accords",
        "parties": ["Israel", "UAE", "Bahrain", "Morocco", "Sudan"],
        "signed": "2020-09-15",
        "status": "HOLDING",
        "last_event": "2026-03-01",
        "notes": "Normalization holds but Gaza war has cooled engagement; Saudi accession deferred indefinitely."
    },
    {
        "name": "Yemen Truce (UN-mediated)",
        "parties": ["Houthi (Ansar Allah)", "Saudi-led coalition"],
        "signed": "2022-04-02",
        "status": "EXPIRED",
        "last_event": "2022-10-02",
        "notes": "Formal 6-month truce lapsed Oct 2022. De facto pause held until Red Sea Houthi campaign restarted late 2023."
    },
    {
        "name": "Houthi Red Sea Ceasefire (US-Oman)",
        "parties": ["Houthi (Ansar Allah)", "United States"],
        "signed": "2025-05-06",
        "status": "VIOLATED",
        "last_event": "2026-04-10",
        "notes": "US-Houthi de-escalation excluded Israel-bound vessels; Houthi missile strikes on Israel continued."
    },
    {
        "name": "Iran–Iraq Border Security Agreement",
        "parties": ["Iran", "Iraq"],
        "signed": "2023-03-19",
        "status": "HOLDING",
        "last_event": "2026-02-15",
        "notes": "Bilateral agreement on Kurdish armed groups along Iran-Iraq border; cooperation deepens."
    },
    {
        "name": "Egypt–Israel Peace Treaty",
        "parties": ["Egypt", "Israel"],
        "signed": "1979-03-26",
        "status": "HOLDING",
        "last_event": "2026-05-01",
        "notes": "Longest-standing Arab-Israeli treaty; under significant strain from Gaza war but holding."
    },
    {
        "name": "Jordan–Israel Peace Treaty",
        "parties": ["Jordan", "Israel"],
        "signed": "1994-10-26",
        "status": "HOLDING",
        "last_event": "2026-04-15",
        "notes": "Diplomatic ties strained over Gaza; ambassador recalls have occurred but treaty intact."
    },
]


# ── Oil & FX prices via yfinance ─────────────────────────────────────────────
def fetch_prices() -> dict:
    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance not installed — skipping prices", flush=True)
        return {}

    # Tickers: Brent, WTI, USD, Gold, S&P 500 (for risk context), USO (oil ETF)
    symbols = {
        "Brent Crude":    "BZ=F",
        "WTI Crude":      "CL=F",
        "Gold":           "GC=F",
        "USD Index":      "DX-Y.NYB",
        "S&P 500":        "^GSPC",
        "USO (Oil ETF)":  "USO",
        "VIX":            "^VIX",
        "Nat Gas":        "NG=F",
    }
    result = {}
    for label, sym in symbols.items():
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="5d", interval="1d")
            if hist.empty or len(hist) < 2:
                continue
            curr = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            change_abs = curr - prev
            change_pct = (curr / prev - 1) * 100
            # 5-day spark
            spark = [float(x) for x in hist["Close"].dropna().tolist()][-5:]
            result[sym] = {
                "label":      label,
                "symbol":     sym,
                "price":      round(curr, 2),
                "change":     round(change_abs, 2),
                "change_pct": round(change_pct, 2),
                "spark":      spark,
            }
            print(f"  {label}: ${curr:.2f} ({change_pct:+.2f}%)", flush=True)
        except Exception as e:
            print(f"  {label}: fetch failed — {e}", flush=True)
    return result


# ── Shipping incident extraction from situation.json ─────────────────────────
SHIPPING_KEYWORDS = re.compile(
    r"\b(tanker|vessel|ship|cargo|freighter|bulk carrier|container ship|hijack|"
    r"seized|attacked|drone (?:strike|attack).+(?:ship|vessel|tanker)|"
    r"Red Sea|Bab[\s-]el[\s-]Mandeb|Hormuz|Suez|Strait of Hormuz|Persian Gulf|"
    r"Gulf of Oman|Gulf of Aden|Arabian Sea|Bandar Abbas|Jask)\b", re.I
)


def extract_shipping_incidents() -> list[dict]:
    """Pull headlines mentioning shipping incidents from the situation feed."""
    if not SITUATION.exists():
        return []
    try:
        doc = json.loads(SITUATION.read_text(encoding="utf-8"))
    except Exception:
        return []
    headlines = doc.get("headlines", [])

    # Filter to last 7 days + shipping keywords
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    incidents = []
    for h in headlines:
        pub = h.get("published", "")
        if pub < cutoff:
            continue
        text = h.get("title", "") + " " + (h.get("summary", "") or "")
        if SHIPPING_KEYWORDS.search(text):
            incidents.append({
                "title":     h["title"],
                "link":      h["link"],
                "source":    h.get("source", ""),
                "published": h.get("published", ""),
                "severity":  h.get("severity", "info"),
            })
    incidents.sort(key=lambda x: x.get("published", ""), reverse=True)
    return incidents[:15]


# ── Choke-point status (heuristic from incidents) ───────────────────────────
CHOKEPOINTS = {
    "Strait of Hormuz": {
        "keywords": re.compile(r"\b(hormuz|persian gulf|bandar abbas|jask|gulf of oman)\b", re.I),
        "description": "20% of global oil + LNG transit. Iran-controlled north shore.",
    },
    "Bab el-Mandeb / Red Sea": {
        "keywords": re.compile(r"\b(bab[\s-]el[\s-]mandeb|red sea|aden|houthi|yemen|djibouti)\b", re.I),
        "description": "Suez canal southern approach. Houthi attack zone since late 2023.",
    },
    "Suez Canal": {
        "keywords": re.compile(r"\b(suez|egyptian canal|port said|ismailia)\b", re.I),
        "description": "12% of global trade. Re-routings via Cape add ~10 days transit.",
    },
    "Eastern Mediterranean": {
        "keywords": re.compile(r"\b(haifa|eastern mediterranean|levant basin|cyprus|lebanon coast)\b", re.I),
        "description": "Israeli ports + Levantine gas fields. Hezbollah cross-border range.",
    },
}


def assess_chokepoints(incidents: list[dict]) -> list[dict]:
    results = []
    for name, meta in CHOKEPOINTS.items():
        hits = [i for i in incidents if meta["keywords"].search(i["title"] + " " + i.get("source", ""))]
        sev_counts = {"critical": 0, "high": 0, "medium": 0}
        for h in hits:
            s = h.get("severity", "info")
            if s in sev_counts:
                sev_counts[s] += 1
        if sev_counts["critical"] >= 1:
            status = "ELEVATED"
        elif sev_counts["high"] >= 2 or sev_counts["medium"] >= 4:
            status = "WATCH"
        elif hits:
            status = "ACTIVE"
        else:
            status = "QUIET"
        results.append({
            "name":        name,
            "description": meta["description"],
            "status":      status,
            "incident_count": len(hits),
            "recent":      hits[:3],
        })
    return results


# ── White House + Trump statement tracker ───────────────────────────────────
USER_AGENT = "iran-watch/0.3 (https://github.com/helioskozak-cloud/iran-watch)"

WH_FEEDS = [
    # Official whitehouse.gov RSS — the working endpoints under the new admin
    ("WH News",            "wh_news",         "https://www.whitehouse.gov/news/feed/"),
    ("WH Presidential Actions", "wh_actions", "https://www.whitehouse.gov/presidential-actions/feed/"),
    # Indirect Trump-statement coverage from major political wires
    ("Politico WH",        "politico_wh",     "https://rss.politico.com/whitehouse.xml"),
    ("Politico Politics",  "politico_pol",    "https://rss.politico.com/politics-news.xml"),
    ("AP Politics",        "ap_pol",          "https://feeds.apnews.com/apnews/politics"),
    ("Reuters Politics",   "reuters_pol",     "https://www.reutersagency.com/feed/?best-topics=political-general&post_type=best"),
    # Bluesky: Trump-quote-heavy WH reporters; resolved at fetch time
    # (these still go through fetch_situation.py — keep this list focused on RSS)
]


def _hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def _clean_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&\w+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Iran/Israel/MidEast keyword set for relevance flagging on WH/Trump posts
WH_RELEVANCE = re.compile(
    r"\b(iran|iranian|tehran|irgc|israel|israeli|idf|gaza|hamas|hezbollah|"
    r"houthi|yemen|red sea|lebanon|hormuz|nuclear|enrichment|sanction|tariff|"
    r"oil|opec|middle east|levant|persian|netanyahu|khamenei|pezeshkian|"
    r"strike|missile|drone|ceasefire|hostage|terrorism|terrorist)\b", re.I
)


def fetch_wh_statements() -> list[dict]:
    items = []
    for name, category, url in WH_FEEDS:
        try:
            parsed = feedparser.parse(url, agent=USER_AGENT)
        except Exception as e:
            print(f"  {name}: fetch failed — {e}", flush=True)
            continue
        if parsed.bozo and not parsed.entries:
            print(f"  {name}: parse error / no entries", flush=True)
            continue
        kept = 0
        for entry in parsed.entries[:30]:
            title = _clean_html(entry.get("title", ""))
            link = entry.get("link", "")
            if not title or not link:
                continue
            summary = _clean_html(entry.get("summary", ""))[:600]
            pub = None
            for key in ("published_parsed", "updated_parsed"):
                t = entry.get(key)
                if t:
                    try:
                        pub = datetime(*t[:6], tzinfo=timezone.utc).isoformat()
                        break
                    except Exception:
                        pass
            pub = pub or datetime.now(timezone.utc).isoformat()
            combined = f"{title} {summary}"
            is_relevant = bool(WH_RELEVANCE.search(combined))
            items.append({
                "id":         _hash(link),
                "title":      title,
                "link":       link,
                "summary":    summary if summary != title else "",
                "source":     name,
                "category":   category,
                "published":  pub,
                "iran_topic": is_relevant,
            })
            kept += 1
        print(f"  {name}: {kept} statements", flush=True)
    # Sort by publish time desc, cap at 40
    items.sort(key=lambda x: x.get("published", ""), reverse=True)
    return items[:40]


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    print("iran-watch infrastructure fetcher", flush=True)

    print("\n[PRICES]", flush=True)
    prices = fetch_prices()

    print("\n[SHIPPING]", flush=True)
    incidents = extract_shipping_incidents()
    print(f"  {len(incidents)} shipping-related headlines in last 7 days", flush=True)

    chokepoints = assess_chokepoints(incidents)
    for c in chokepoints:
        print(f"  {c['name']}: {c['status']} ({c['incident_count']} hits)", flush=True)

    print("\n[WH / TRUMP]", flush=True)
    wh_statements = fetch_wh_statements()
    iran_relevant_count = sum(1 for s in wh_statements if s.get("iran_topic"))
    print(f"  Loaded {len(wh_statements)} statements ({iran_relevant_count} region-relevant)", flush=True)

    payload = {
        "generated":     datetime.now(timezone.utc).isoformat(),
        "prices":        prices,
        "treaties":      TREATIES,
        "incidents":     incidents,
        "chokepoints":   chokepoints,
        "wh_statements": wh_statements,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
