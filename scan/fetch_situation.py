"""
fetch_situation.py — pulls headlines from a wide range of news, OSINT,
social-media-bridge, and government RSS sources, filters to anything
Iran/Middle-East-relevant, classifies by severity, and writes
docs/data/situation.json.

Designed to give an at-a-glance "what's happening in the region right now"
view modeled on a tactical operations center / OSINT terminal.

Sources include:
- Reddit r/* subreddits relevant to the region
- Mainstream Middle East news (Times of Israel, Al Jazeera, BBC ME, Iran International)
- OSINT / defense analyst blogs (Long War Journal, War on the Rocks, ISW)
- Iranian state and opposition media (where RSS available)
- US government press feeds (Pentagon, State Department, Treasury sanctions)
- YouTube channel feeds for analyst commentary
- Mastodon / Bluesky bridges where useful
"""
import json
import re
import sys
import time
import socket
import hashlib
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser

socket.setdefaulttimeout(15)
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA = ROOT / "docs" / "data"
OUT = DATA / "situation.json"

MAX_KEEP = 600
USER_AGENT = "iran-watch/0.1 (https://github.com/helioskozak-cloud/iran-watch)"

# ── Sources ──────────────────────────────────────────────────────────────────
# (name, category, url)
# Categories drive color coding in the UI.
FEEDS = [
    # ── Mainstream regional / Middle East focus ─────────────────────────────
    ("Times of Israel",    "regional_news",   "https://www.timesofisrael.com/feed/"),
    ("Times of Israel ME", "regional_news",   "https://www.timesofisrael.com/topic/middle-east/feed/"),
    ("Jerusalem Post",     "regional_news",   "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"),
    ("Jerusalem Post ME",  "regional_news",   "https://www.jpost.com/rss/rssfeedsmiddleeastnews.aspx"),
    ("Al Jazeera",         "regional_news",   "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Al Arabiya",         "regional_news",   "https://english.alarabiya.net/.mrss/en.xml"),
    ("Al-Monitor",         "regional_news",   "https://www.al-monitor.com/rss.xml"),
    ("Middle East Eye",    "regional_news",   "https://www.middleeasteye.net/rss/all"),
    ("BBC Middle East",    "regional_news",   "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml"),
    ("Reuters World",      "regional_news",   "https://www.reutersagency.com/feed/?best-topics=global-news&post_type=best"),

    # ── Iran-specific outlets ───────────────────────────────────────────────
    ("Iran International", "iran_voice",      "https://www.iranintl.com/en/rss.xml"),
    ("Radio Farda",        "iran_voice",      "https://en.radiofarda.com/api/zsq-rb-mtjyt"),  # may 404 — kept as attempt
    ("Tasnim News",        "iran_state",      "https://www.tasnimnews.com/en/rss/feed/0/7/2/featured-news"),
    ("Tehran Times",       "iran_state",      "https://www.tehrantimes.com/rss"),
    ("Press TV",           "iran_state",      "https://www.presstv.ir/rss.xml"),
    ("IRNA",               "iran_state",      "https://en.irna.ir/rss"),

    # ── OSINT / defense analyst ─────────────────────────────────────────────
    ("Long War Journal",   "osint",           "https://www.longwarjournal.org/feed"),
    ("War on the Rocks",   "osint",           "https://warontherocks.com/feed/"),
    ("ISW",                "osint",           "https://www.understandingwar.org/rss.xml"),
    ("Defense One",        "osint",           "https://www.defenseone.com/rss/all/"),
    ("Breaking Defense",   "osint",           "https://breakingdefense.com/feed/"),
    ("The Drive",          "osint",           "https://www.twz.com/feed"),
    ("USNI News",          "osint",           "https://news.usni.org/feed"),

    # ── US government ───────────────────────────────────────────────────────
    ("DoD News",           "official",        "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10"),
    ("State Dept",         "official",        "https://www.state.gov/rss-feeds/department-press-briefing-rss-feed/feed/"),
    ("CENTCOM",            "official",        "https://www.centcom.mil/Desktop-Modules/ArticleCS/RSS.ashx?ContentType=1&Site=313"),
    ("Treasury OFAC",      "official",        "https://ofac.treasury.gov/rss/recent-actions.xml"),

    # ── Reddit (social proxy) ───────────────────────────────────────────────
    ("r/worldnews",        "reddit",          "https://www.reddit.com/r/worldnews/hot/.rss?limit=30"),
    ("r/IranProtests",     "reddit",          "https://www.reddit.com/r/IranProtests/new/.rss?limit=30"),
    ("r/Iran",             "reddit",          "https://www.reddit.com/r/Iran/new/.rss?limit=30"),
    ("r/Israel",           "reddit",          "https://www.reddit.com/r/Israel/hot/.rss?limit=30"),
    ("r/MiddleEastNews",   "reddit",          "https://www.reddit.com/r/MiddleEastNews/new/.rss?limit=30"),
    ("r/geopolitics",      "reddit",          "https://www.reddit.com/r/geopolitics/hot/.rss?limit=30"),
    ("r/AnythingGoesNews", "reddit",          "https://www.reddit.com/r/AnythingGoesNews/hot/.rss?limit=30"),
    ("r/syriancivilwar",   "reddit",          "https://www.reddit.com/r/syriancivilwar/new/.rss?limit=30"),
    ("r/lebanon",          "reddit",          "https://www.reddit.com/r/lebanon/new/.rss?limit=30"),
    ("r/yemen",            "reddit",          "https://www.reddit.com/r/yemen/new/.rss?limit=30"),

    # ── Nitter bridges for key OSINT X accounts (likely flaky) ─────────────
    # If these die we replace with alternatives.
    ("X @sentdefender",    "x_bridge",        "https://nitter.privacydev.net/sentdefender/rss"),
    ("X @AuroraIntel",     "x_bridge",        "https://nitter.privacydev.net/AuroraIntel/rss"),
    ("X @OSINTtechnical",  "x_bridge",        "https://nitter.privacydev.net/OSINTtechnical/rss"),
    ("X @TheStudyofWar",   "x_bridge",        "https://nitter.privacydev.net/TheStudyofWar/rss"),
    ("X @IsraelinUSA",     "x_bridge",        "https://nitter.privacydev.net/IsraelinUSA/rss"),

    # ── YouTube channels (analysts) ─────────────────────────────────────────
    ("YT Caspian Report",  "youtube",         "https://www.youtube.com/feeds/videos.xml?user=CaspianReport"),
    ("YT Perun",           "youtube",         "https://www.youtube.com/feeds/videos.xml?channel_id=UCgkAQc7gXjj9c0vV56sJ3jw"),
]

# ── Keyword filter ───────────────────────────────────────────────────────────
# A headline must mention ≥1 of these to be kept (anchors the feed to the topic).
RELEVANCE_TERMS = [
    # Iran proper
    "iran", "iranian", "tehran", "isfahan", "bushehr", "natanz", "fordow", "hormuz",
    "khamenei", "pezeshkian", "raisi", "khomeini",
    "irgc", "quds force", "basij", "soleimani", "artesh", "shia", "shiite",
    # Nuclear
    "nuclear", "enrichment", "uranium", "iaea", "jcpoa",
    # Regional theatres / proxies
    "israel", "israeli", "idf", "mossad", "knesset", "netanyahu", "gallant", "gantz",
    "tel aviv", "jerusalem", "haifa", "eilat", "negev", "golan",
    "gaza", "hamas", "sinwar", "haniyeh",
    "hezbollah", "nasrallah", "lebanon", "lebanese", "beirut",
    "houthi", "ansarallah", "yemen", "sanaa", "red sea", "bab el-mandeb", "bab al-mandab",
    "syria", "syrian", "assad", "damascus", "deir ez-zor", "deir ezzor",
    "iraq", "iraqi", "baghdad", "anbar", "popular mobilization", "pmf",
    "kataib", "saraya", "harakat",
    # US / coalition relevance
    "centcom", "fifth fleet", "carrier strike", "uss",
    "sanction", "ofac",
    # Diplomacy / events
    "ceasefire", "hostage", "negotiation", "diplomacy", "summit",
    # Energy / market spillovers
    "brent", "wti", "opec", "tanker", "drone", "missile",
]
RELEVANCE_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in RELEVANCE_TERMS) + r")\b", re.IGNORECASE)


def is_relevant(text: str) -> bool:
    return bool(RELEVANCE_RE.search(text or ""))


# ── Severity classifier ──────────────────────────────────────────────────────
SEVERITY_RULES = [
    ("critical", re.compile(r"\b(struck?|strikes?|airstrike|missile (?:launch|strike|hit)|killed?|dead|casualt|nuclear (?:test|detonat)|invad|war breaks?|assassin|exchanges? of fire|all-out war)\b", re.I)),
    ("high",     re.compile(r"\b(attack|drone (?:strike|attack)|launch|deploy|mobiliz|skirmish|clash|tanker (?:seized|attack)|hostage|abduct|captured|exchange (?:fire|gunfire)|incursion|raid|ground operation|air defen[cs]e activated|under fire|cross-border)\b", re.I)),
    ("medium",   re.compile(r"\b(sanction|threat|warns?|escalat|alert|condemn|reject|expel|recall ambassador|reinforc|surge|nuclear program|enrich|uranium|IRGC commander|drill|exercise|maneuver)\b", re.I)),
    ("low",      re.compile(r"\b(meeting|talks?|negotiat|diplomat|summit|statement|brief|press conference|ceasefire (?:holds?|extended)|prisoner exchange|aid)\b", re.I)),
]


def severity(text: str) -> str:
    for level, pat in SEVERITY_RULES:
        if pat.search(text or ""):
            return level
    return "info"


# ── Topic tagging (light) ───────────────────────────────────────────────────
TOPIC_RULES = [
    ("Iran",       re.compile(r"\b(iran|iranian|tehran|irgc|khamenei|natanz|fordow|bushehr|hormuz|pezeshkian)\b", re.I)),
    ("Israel",     re.compile(r"\b(israel|israeli|idf|knesset|netanyahu|tel aviv|jerusalem|mossad|gantz|gallant)\b", re.I)),
    ("Gaza",       re.compile(r"\b(gaza|hamas|rafah|khan younis|sinwar)\b", re.I)),
    ("Lebanon",    re.compile(r"\b(hezbollah|lebanon|lebanese|nasrallah|beirut)\b", re.I)),
    ("Yemen",      re.compile(r"\b(houthi|yemen|sanaa|red sea|bab[\s-]el[\s-]mandeb|ansarallah)\b", re.I)),
    ("Syria",      re.compile(r"\b(syria|syrian|damascus|assad|deir ez)\b", re.I)),
    ("Iraq",       re.compile(r"\b(iraq|iraqi|baghdad|kataib|pmf)\b", re.I)),
    ("Nuclear",    re.compile(r"\b(nuclear|enrich|uranium|iaea|jcpoa|natanz|fordow)\b", re.I)),
    ("US Mil",     re.compile(r"\b(centcom|fifth fleet|carrier strike|uss\s+\w+|us military|pentagon)\b", re.I)),
    ("Sanctions",  re.compile(r"\b(sanction|ofac|designat|asset freeze)\b", re.I)),
    ("Oil",        re.compile(r"\b(oil|brent|wti|opec|crude|barrel|refiner|tanker)\b", re.I)),
    ("Diplomacy",  re.compile(r"\b(diplomat|summit|talks?|negotiat|ceasefire|hostage|prisoner)\b", re.I)),
]


def topics(text: str) -> list[str]:
    found = []
    for label, pat in TOPIC_RULES:
        if pat.search(text or ""):
            found.append(label)
    return found[:4]


# ── Feed parsing ─────────────────────────────────────────────────────────────
def _hash_url(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def _parse_published(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return None


def _clean_title(title: str) -> str:
    title = re.sub(r"<[^>]+>", "", title or "").strip()
    title = re.sub(r"\s+", " ", title)
    return title


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def fetch_feed(name: str, category: str, url: str) -> list[dict]:
    items: list[dict] = []
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"  {name}: fetch failed — {e}", flush=True)
        return items

    if parsed.bozo and not parsed.entries:
        print(f"  {name}: parse error / no entries", flush=True)
        return items

    raw_count = 0
    for entry in parsed.entries[:40]:
        title = _clean_title(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue
        raw_count += 1
        summary = _clean_title(entry.get("summary", ""))[:600]
        combined = f"{title} {summary}"
        # Regional / OSINT / official feeds: only keep relevant items
        if category in ("regional_news", "reddit", "osint", "official", "youtube") and not is_relevant(combined):
            continue
        published = _parse_published(entry) or datetime.now(timezone.utc).isoformat()
        items.append({
            "id":        _hash_url(link),
            "title":     title,
            "link":      link,
            "summary":   summary if summary != title else "",
            "source":    name,
            "category":  category,
            "domain":    _domain(link),
            "published": published,
            "severity":  severity(combined),
            "topics":    topics(combined),
        })
    print(f"  {name}: {len(items)}/{raw_count} kept", flush=True)
    return items


def load_existing() -> list[dict]:
    if not OUT.exists():
        return []
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc.get("headlines", [])
    except Exception as e:
        print(f"WARN: existing load failed — {e}", flush=True)
        return []


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"iran-watch fetcher · {len(FEEDS)} sources", flush=True)

    fresh: list[dict] = []
    for name, category, url in FEEDS:
        fresh.extend(fetch_feed(name, category, url))
        time.sleep(0.25)

    existing = load_existing()
    seen_ids = {h["id"] for h in existing}
    new_items = [h for h in fresh if h["id"] not in seen_ids]
    print(f"\nFetched {len(fresh)} headlines ({len(new_items)} new since last run)", flush=True)

    by_id = {h["id"]: h for h in existing}
    for h in fresh:
        by_id[h["id"]] = h
    combined = sorted(by_id.values(), key=lambda h: h.get("published", ""), reverse=True)
    combined = combined[:MAX_KEEP]

    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for h in combined:
        sev_counts[h.get("severity", "info")] = sev_counts.get(h.get("severity", "info"), 0) + 1
    print(f"Severity mix: {sev_counts}", flush=True)

    payload = {
        "generated":  datetime.now(timezone.utc).isoformat(),
        "feed_count": len(FEEDS),
        "headlines":  combined,
        "severity":   sev_counts,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(combined)} headlines)", flush=True)


if __name__ == "__main__":
    main()
