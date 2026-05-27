# iran-watch

OSINT-style situation monitor focused on Iran and the broader Middle East. Pulls headlines from ~45 public RSS sources — regional news, Iranian state and opposition media, Western defense analysts, US government press feeds, Reddit, YouTube channels, and X (Twitter) via Nitter bridges where reachable. Filters everything against a keyword set anchored on Iran / Israel / proxies / nuclear / sanctions / regional security, classifies each headline by severity (CRITICAL / HIGH / MEDIUM / LOW / INFO), and tags topics.

Built as a free, OSINT-first situational-awareness page. Headlines are not verified — this is a feed, not intel.

## Architecture

```
iran-watch/
├── docs/
│   ├── index.html                # tactical-feed UI (dark/green CRT vibe)
│   └── data/situation.json       # written by CI cron
├── scan/
│   └── fetch_situation.py        # RSS scraper + keyword filter + severity classifier
└── .github/workflows/refresh.yml # 15-min cron during day, hourly overnight
```

## Source categories

| Category       | Examples                                                        |
|----------------|-----------------------------------------------------------------|
| Regional News  | Times of Israel, Jerusalem Post, Al Jazeera, BBC ME, Al-Monitor |
| Iran State     | IRNA, Tasnim, Tehran Times, Press TV                            |
| Iran Voices    | Iran International, Radio Farda                                 |
| OSINT          | Long War Journal, ISW, War on the Rocks, Breaking Defense       |
| Official       | DoD, State Department, CENTCOM, OFAC                            |
| Reddit         | r/IranProtests, r/worldnews, r/MiddleEastNews, r/syriancivilwar |
| X (Bridges)    | @sentdefender, @AuroraIntel, @OSINTtechnical via Nitter         |
| YouTube        | Caspian Report, Perun                                           |

Nitter instances are unreliable since Twitter's API restrictions — those rows may go silent for stretches. The rest are robust.

## Severity rules

Patterns matched against title + summary, in order:

- **CRITICAL** — strike / airstrike / killed / nuclear test / invade / war breaks / assassination / exchange of fire
- **HIGH** — attack / drone strike / launch / deploy / mobilize / skirmish / clash / hostage / raid / incursion / cross-border
- **MEDIUM** — sanction / threat / warns / escalate / alert / condemn / enrich / uranium / IRGC commander / drill / maneuver
- **LOW** — meeting / talks / negotiate / diplomacy / summit / brief / press / ceasefire holds / aid
- **INFO** — anything else

The 24-hour rolling severity mix drives the alert level in the banner (NOMINAL / WATCH / ELEVATED / CRITICAL).

## Local dev

```bat
pip install -r requirements.txt
python scan/fetch_situation.py
```

Then open `docs/index.html` in a browser.

## Not intelligence

Open-source headlines only. Sources have biases. Reddit is not journalism. Iranian state media and Western outlets disagree about facts. This page is for awareness, not analysis. Verify before you believe.
