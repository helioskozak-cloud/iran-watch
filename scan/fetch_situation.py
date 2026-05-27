"""
fetch_situation.py — pulls headlines from RSS sources AND Bluesky posts,
filters to Iran/MidEast-relevant items, classifies severity with conflict
context, attaches embed media (images/external links) where available, and
writes docs/data/situation.json.
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
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

import feedparser

socket.setdefaulttimeout(15)
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA = ROOT / "docs" / "data"
OUT = DATA / "situation.json"

MAX_KEEP = 800
USER_AGENT = "iran-watch/0.2 (https://github.com/helioskozak-cloud/iran-watch)"

# ── RSS sources ──────────────────────────────────────────────────────────────
FEEDS = [
    # Regional news
    ("Times of Israel",    "regional_news", "https://www.timesofisrael.com/feed/"),
    ("Times of Israel ME", "regional_news", "https://www.timesofisrael.com/topic/middle-east/feed/"),
    ("Jerusalem Post",     "regional_news", "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"),
    ("Al Jazeera",         "regional_news", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Al Arabiya",         "regional_news", "https://english.alarabiya.net/.mrss/en.xml"),
    ("Al-Monitor",         "regional_news", "https://www.al-monitor.com/rss.xml"),
    ("Middle East Eye",    "regional_news", "https://www.middleeasteye.net/rss/all"),
    ("BBC Middle East",    "regional_news", "http://feeds.bbci.co.uk/news/world/middle_east/rss.xml"),
    # Iran outlets
    ("Iran International", "iran_voice",    "https://www.iranintl.com/en/rss.xml"),
    ("Tasnim News",        "iran_state",    "https://www.tasnimnews.com/en/rss/feed/0/7/2/featured-news"),
    ("Tehran Times",       "iran_state",    "https://www.tehrantimes.com/rss"),
    ("Press TV",           "iran_state",    "https://www.presstv.ir/rss.xml"),
    ("IRNA",               "iran_state",    "https://en.irna.ir/rss"),
    # OSINT / defense
    ("Long War Journal",   "osint",         "https://www.longwarjournal.org/feed"),
    ("War on the Rocks",   "osint",         "https://warontherocks.com/feed/"),
    ("ISW",                "osint",         "https://www.understandingwar.org/rss.xml"),
    ("Defense One",        "osint",         "https://www.defenseone.com/rss/all/"),
    ("Breaking Defense",   "osint",         "https://breakingdefense.com/feed/"),
    ("The Drive",          "osint",         "https://www.twz.com/feed"),
    ("USNI News",          "osint",         "https://news.usni.org/feed"),
    # Official
    ("DoD News",           "official",      "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10"),
    ("State Dept",         "official",      "https://www.state.gov/rss-feeds/department-press-briefing-rss-feed/feed/"),
    ("CENTCOM",            "official",      "https://www.centcom.mil/Desktop-Modules/ArticleCS/RSS.ashx?ContentType=1&Site=313"),
    ("Treasury OFAC",      "official",      "https://ofac.treasury.gov/rss/recent-actions.xml"),
    # Reddit
    ("r/worldnews",        "reddit",        "https://www.reddit.com/r/worldnews/hot/.rss?limit=30"),
    ("r/IranProtests",     "reddit",        "https://www.reddit.com/r/IranProtests/new/.rss?limit=30"),
    ("r/Iran",             "reddit",        "https://www.reddit.com/r/Iran/new/.rss?limit=30"),
    ("r/Israel",           "reddit",        "https://www.reddit.com/r/Israel/hot/.rss?limit=30"),
    ("r/MiddleEastNews",   "reddit",        "https://www.reddit.com/r/MiddleEastNews/new/.rss?limit=30"),
    ("r/geopolitics",      "reddit",        "https://www.reddit.com/r/geopolitics/hot/.rss?limit=30"),
    ("r/syriancivilwar",   "reddit",        "https://www.reddit.com/r/syriancivilwar/new/.rss?limit=30"),
    ("r/lebanon",          "reddit",        "https://www.reddit.com/r/lebanon/new/.rss?limit=30"),
    ("r/yemen",            "reddit",        "https://www.reddit.com/r/yemen/new/.rss?limit=30"),
    ("r/CombatFootage",    "reddit",        "https://www.reddit.com/r/CombatFootage/new/.rss?limit=30"),
    ("r/CredibleDefense",  "reddit",        "https://www.reddit.com/r/CredibleDefense/hot/.rss?limit=30"),
    # YouTube
    ("YT Caspian Report",  "youtube",       "https://www.youtube.com/feeds/videos.xml?user=CaspianReport"),
]

# ── Bluesky handles ──────────────────────────────────────────────────────────
# Pulled via the public AT Protocol API. Best-effort — handles that 404 are
# silently skipped. Mix of mainstream news, OSINT investigators, and analysts.
BLUESKY_HANDLES = [
    "bellingcat.com",                  # Bellingcat OSINT (custom domain handle)
    "nytimes.com",                     # NYT
    "reuters.com",                     # Reuters
    "bbcnews.bsky.social",             # BBC
    "apnews.com",                      # AP
    "ft.com",                          # Financial Times
    "aljazeeraenglish.bsky.social",
    "timesofisrael.bsky.social",
    "haaretzcom.bsky.social",          # Haaretz
    "iranintl.bsky.social",            # Iran International
    "longwarjournal.bsky.social",
    "isw.bsky.social",                 # Institute for the Study of War
    "warontherocks.bsky.social",
    "csis.org",                        # CSIS
    "rusi.bsky.social",                # RUSI
    "wapo.bsky.social",                # Washington Post
    "maxiboot.bsky.social",            # Max Boot
    "antho.bsky.social",
    "rcallimachi.bsky.social",         # Rukmini Callimachi (NYT, terrorism)
    "shashj.bsky.social",              # Shashank Joshi (Economist defence editor)
    "narges-bajoghli.bsky.social",     # Iran analyst
    "afshonostovar.bsky.social",       # Iran/IRGC expert
    "raniaab.bsky.social",             # Rania Abouzeid (ME journalism)
    "borzou.bsky.social",              # Borzou Daragahi (foreign correspondent)
    "kim-ghattas.bsky.social",         # Kim Ghattas (ME analyst)
    "alimaisam.bsky.social",
    "obretix.bsky.social",             # OSINT imagery
    "amaarpaq.bsky.social",
    "joshrogin.bsky.social",
]


# ── Filtering ────────────────────────────────────────────────────────────────
RELEVANCE_TERMS = [
    "iran","iranian","tehran","isfahan","bushehr","natanz","fordow","hormuz",
    "khamenei","pezeshkian","raisi","khomeini","irgc","quds","basij","soleimani",
    "nuclear","enrichment","uranium","iaea","jcpoa",
    "israel","israeli","idf","mossad","knesset","netanyahu","gallant","gantz",
    "tel aviv","jerusalem","haifa","eilat","negev","golan",
    "gaza","hamas","sinwar","haniyeh",
    "hezbollah","nasrallah","lebanon","lebanese","beirut",
    "houthi","ansarallah","yemen","sanaa","red sea","bab el-mandeb","bab al-mandab","bab-el-mandeb",
    "syria","syrian","assad","damascus","deir ez-zor","deir ezzor",
    "iraq","iraqi","baghdad","anbar","pmf","kataib","saraya","harakat",
    "centcom","fifth fleet","carrier strike","uss",
    "sanction","ofac",
    "ceasefire","hostage","negotiation","diplomacy","summit",
    "brent","wti","opec","tanker","drone","missile","ballistic","cruise missile",
]
RELEVANCE_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in RELEVANCE_TERMS) + r")\b", re.IGNORECASE)

# Conflict-context words required for CRITICAL classification
CONFLICT_CONTEXT = [
    "iran","iranian","irgc","israel","israeli","idf","gaza","hamas","hezbollah",
    "houthi","yemen","lebanon","syria","syrian","iraq","tehran","beirut","damascus",
    "drone","missile","airstrike","ballistic","cruise missile","fighter jet",
    "f-16","f-35","f-15","f-18","f-22","mig","sukhoi","su-",
    "tanker","warship","destroyer","cruiser","frigate","submarine","aircraft carrier",
    "centcom","quds","irgc","mossad","cia","mi6","soldier","troops","commander",
    "military","forces","army","navy","air force","marine","fighter","militant",
    "jihadist","militia","proxy","strike","retaliat","escalat","nuclear","enrichment",
    "uranium","sanction","ofac","embassy","consulate","ambassador",
    "fired","launched","struck","intercept","assassin","kidnap","abduct","raid",
    "incursion","breach","casualt","killed","dead","wounded","injured","hostage",
]
CONFLICT_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in CONFLICT_CONTEXT) + r")\b", re.IGNORECASE)


def is_relevant(text: str) -> bool:
    return bool(RELEVANCE_RE.search(text or ""))


# Critical requires BOTH a severe verb AND conflict context
CRITICAL_VERBS = re.compile(
    r"\b(airstrike|struck|missile (?:launch|strike|hit|fired|barrage)|"
    r"rocket (?:launch|strike|hit|fired|barrage)|"
    r"killed|deaths?|fatalit|casualt|"
    r"nuclear (?:test|detonat)|invad(?:e|ed|ing|es)|"
    r"war breaks|all-out war|"
    r"assassin(?:ation|ated)?|"
    r"exchange of fire|direct attack|major attack|"
    r"shot down|downed (?:drone|aircraft|jet)|"
    r"destroyed (?:base|facility|infrastructure|warehouse)|"
    r"intercepted (?:missiles?|drones?|launches?)|"
    r"hijacked|seized (?:tanker|vessel|ship))\b", re.I
)

HIGH_PATTERNS = re.compile(
    r"\b(attack|drone (?:strike|attack|swarm)|launch|deploy|mobiliz|skirmish|clash|"
    r"tanker (?:seized|attack|incident)|hostage|abduct|captured|"
    r"exchange (?:fire|gunfire)|incursion|raid|ground operation|"
    r"air defen[cs]e activated|under fire|cross-border|"
    r"engaged in combat|targeted (?:strike|operation)|precision strike|"
    r"shells?|shelled|bombard|explosion|blast)\b", re.I
)

MEDIUM_PATTERNS = re.compile(
    r"\b(sanction|threat|warns?|escalat|alert|condemn|reject|expel|recall ambassador|"
    r"reinforc|surge|nuclear program|enrich|uranium|IRGC commander|"
    r"drill|exercise|maneuver|joint exercise|naval exercise|"
    r"summon|protest|demonstration|crackdown|arrest)\b", re.I
)

LOW_PATTERNS = re.compile(
    r"\b(meeting|talks?|negotiat|diplomat|summit|statement|brief|press conference|"
    r"ceasefire (?:holds?|extended)|prisoner exchange|aid|humanitarian|delegation)\b", re.I
)


def severity(text: str) -> str:
    """Tightened classifier — CRITICAL requires conflict context co-occurrence."""
    t = text or ""
    has_critical_verb = bool(CRITICAL_VERBS.search(t))
    has_conflict_context = bool(CONFLICT_RE.search(t))
    if has_critical_verb and has_conflict_context:
        return "critical"
    if has_critical_verb or HIGH_PATTERNS.search(t):
        # Severe verb without context → downgrade to high; full HIGH match → high
        return "high"
    if MEDIUM_PATTERNS.search(t):
        return "medium"
    if LOW_PATTERNS.search(t):
        return "low"
    return "info"


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
    return [label for label, pat in TOPIC_RULES if pat.search(text or "")][:4]


def _hash_url(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def _parse_published(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return None


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "").strip()
    return re.sub(r"\s+", " ", s)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _extract_media_from_rss(entry) -> dict:
    """Pull image/thumbnail from common RSS extension fields."""
    media = {"images": [], "thumbnail": None}

    # media:content
    for m in entry.get("media_content", []) or []:
        url = m.get("url")
        if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
            media["images"].append(url)

    # media:thumbnail
    for m in entry.get("media_thumbnail", []) or []:
        url = m.get("url")
        if url:
            media["thumbnail"] = url
            break

    # enclosure links
    for link in entry.get("links", []) or []:
        if (link.get("rel") == "enclosure" and "image" in (link.get("type") or "")):
            url = link.get("href")
            if url:
                media["images"].append(url)

    # Reddit-style: scrape from summary
    summary = entry.get("summary", "") or ""
    img_match = re.search(r'<img[^>]+src="([^"]+\.(?:jpg|jpeg|png|gif|webp))[^"]*"', summary, re.I)
    if img_match and not media["images"]:
        media["images"].append(img_match.group(1))

    # YouTube thumbnail via channel feeds
    if entry.get("yt_videoid"):
        media["thumbnail"] = f"https://i.ytimg.com/vi/{entry['yt_videoid']}/hqdefault.jpg"

    # Dedupe + limit
    seen = set()
    uniq = []
    for u in media["images"]:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    media["images"] = uniq[:4]
    return media


def fetch_rss(name: str, category: str, url: str) -> list[dict]:
    items = []
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"  {name}: fetch failed — {e}", flush=True)
        return items

    if parsed.bozo and not parsed.entries:
        print(f"  {name}: parse error / no entries", flush=True)
        return items

    raw = 0
    for entry in parsed.entries[:40]:
        title = _clean(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue
        raw += 1
        summary = _clean(entry.get("summary", ""))[:600]
        combined = f"{title} {summary}"
        if category in ("regional_news", "reddit", "osint", "official", "youtube") and not is_relevant(combined):
            continue
        published = _parse_published(entry) or datetime.now(timezone.utc).isoformat()
        media = _extract_media_from_rss(entry)
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
            "media":     media,
            "kind":      "rss",
        })
    print(f"  {name}: {len(items)}/{raw} kept", flush=True)
    return items


# ── Bluesky via AT Protocol public API ──────────────────────────────────────
BSKY_API = "https://public.api.bsky.app/xrpc"


def _http_get_json(url: str) -> dict | None:
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, socket.timeout, json.JSONDecodeError, ValueError):
        return None


def fetch_bluesky(handle: str) -> list[dict]:
    """Fetch a Bluesky author feed using the AT Protocol public XRPC."""
    items = []
    url = f"{BSKY_API}/app.bsky.feed.getAuthorFeed?actor={handle}&limit=30"
    data = _http_get_json(url)
    if not data or "feed" not in data:
        print(f"  bsky:{handle}: unavailable", flush=True)
        return items

    raw = 0
    for entry in data["feed"]:
        post = entry.get("post", {}) or {}
        record = post.get("record", {}) or {}
        text = _clean(record.get("text", ""))
        if not text:
            continue
        raw += 1
        if not is_relevant(text):
            continue

        author = post.get("author", {}) or {}
        author_handle = author.get("handle", handle)
        author_name = author.get("displayName") or author_handle
        # Construct a deep link
        post_uri = post.get("uri", "")
        rkey = post_uri.split("/")[-1] if post_uri else ""
        link = f"https://bsky.app/profile/{author_handle}/post/{rkey}" if rkey else f"https://bsky.app/profile/{author_handle}"

        # Embed media
        media = {"images": [], "thumbnail": None, "external": None, "video": None, "quote": None}
        embed = post.get("embed", {}) or {}
        emb_type = embed.get("$type") or ""

        # Images
        if "images" in embed:
            for img in embed.get("images", []):
                u = img.get("fullsize") or img.get("thumb")
                if u:
                    media["images"].append(u)
        # External link cards
        if "external" in embed and embed["external"]:
            ext = embed["external"]
            media["external"] = {
                "uri":  ext.get("uri"),
                "title": ext.get("title"),
                "description": _clean(ext.get("description", ""))[:240],
                "thumb": ext.get("thumb"),
            }
        # Video
        if "video" in emb_type.lower() or "playlist" in embed:
            media["video"] = embed.get("playlist") or embed.get("thumbnail")
            if embed.get("thumbnail"):
                media["thumbnail"] = embed["thumbnail"]

        # Engagement signals → boost severity for high-traction posts about conflict
        likes = post.get("likeCount", 0)
        replies = post.get("replyCount", 0)
        reposts = post.get("repostCount", 0)

        published = record.get("createdAt") or datetime.now(timezone.utc).isoformat()
        published = published.replace("Z", "+00:00")

        items.append({
            "id":        _hash_url(post_uri or link),
            "title":     text[:280],
            "link":      link,
            "summary":   "" if len(text) <= 280 else text[280:600],
            "source":    f"@{author_handle.split('.')[0]}",
            "author":    author_name,
            "category":  "bluesky",
            "domain":    "bsky.app",
            "published": published,
            "severity":  severity(text),
            "topics":    topics(text),
            "media":     media,
            "engage":    {"likes": likes, "replies": replies, "reposts": reposts},
            "kind":      "bsky",
        })

    print(f"  bsky:{handle}: {len(items)}/{raw} kept", flush=True)
    return items


def load_existing() -> list[dict]:
    if not OUT.exists():
        return []
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            return json.load(f).get("headlines", [])
    except Exception as e:
        print(f"WARN: existing load failed — {e}", flush=True)
        return []


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"iran-watch fetcher v0.2 · {len(FEEDS)} RSS sources + {len(BLUESKY_HANDLES)} bsky handles", flush=True)

    fresh = []
    print("\n[RSS]", flush=True)
    for name, category, url in FEEDS:
        fresh.extend(fetch_rss(name, category, url))
        time.sleep(0.2)

    print("\n[BLUESKY]", flush=True)
    for handle in BLUESKY_HANDLES:
        fresh.extend(fetch_bluesky(handle))
        time.sleep(0.25)

    existing = load_existing()
    seen_ids = {h["id"] for h in existing}
    new_items = [h for h in fresh if h["id"] not in seen_ids]
    print(f"\nFetched {len(fresh)} headlines ({len(new_items)} new since last run)", flush=True)

    # Backfill: existing entries without 'media' field
    for h in existing:
        h.setdefault("media", {"images": [], "thumbnail": None})
        h.setdefault("kind", "rss")
        # Re-classify existing entries with the new severity logic
        h["severity"] = severity(h.get("title", "") + " " + (h.get("summary", "") or ""))

    by_id = {h["id"]: h for h in existing}
    for h in fresh:
        by_id[h["id"]] = h
    combined = sorted(by_id.values(), key=lambda h: h.get("published", ""), reverse=True)
    combined = combined[:MAX_KEEP]

    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for h in combined:
        sev_counts[h.get("severity", "info")] = sev_counts.get(h.get("severity", "info"), 0) + 1
    print(f"Severity mix: {sev_counts}", flush=True)

    media_count = sum(1 for h in combined if (h.get("media") or {}).get("images"))
    print(f"With image media: {media_count}", flush=True)

    payload = {
        "generated":  datetime.now(timezone.utc).isoformat(),
        "feed_count": len(FEEDS) + len(BLUESKY_HANDLES),
        "headlines":  combined,
        "severity":   sev_counts,
    }
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(combined)} headlines)", flush=True)


if __name__ == "__main__":
    main()
