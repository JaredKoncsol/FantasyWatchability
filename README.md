# Fantasy Watchability

A Claude skill that answers one question every NFL week: **which games should I watch?**

It pulls your starting lineups from every fantasy football league you play in (ESPN, Sleeper and Yahoo), lines them up against the NFL schedule, projections and injury reports, and ranks every game by a **Watchability** score. The result is a dashboard page showing, for each game, who you have playing, which team to root for in each league, and anything in your lineups that needs fixing before kickoff.

> **AI-assisted project.** This skill was designed and built with the help of Claude (Anthropic's AI assistant). The scoring rules, priorities and requirements are my own design; much of the code and documentation was written with AI assistance and then reviewed and run on real league data. Treat it as a hobby tool, not as betting or fantasy advice.

## What the dashboard shows

- **Watchability ranking** for every game of the week (0–100%, with 🔥 / 👀 / 📺 / 💤 bands)
- **Per-league rooting** for each game: All In, Slightly, or ⚔️ Conflicted when you have players on both sides
- **Overall rooting** across all your leagues, weighted by how much each league matters to you
- **Fix now alerts:** starters on bye, Out/IR/Doubtful, empty lineup slots, players that couldn't be matched
- **Keep an eye on:** Questionable starters
- **Recap mode** for a finished week: final scores, won/lost matchups, and your biggest over- and under-performers

## How it works

```
Flaim connector ──► leagues.json ─┐
(your leagues, matchups, starters)│
                                  ├──► watch.py run ──► dashboard (HTML + Markdown)
Browser collectors ──► nfl.json ──┘
(schedule, records, weather, byes,
 projections, injuries)
```

Claude's only job is gathering data. Every calculation (scoring, rooting, ordering, alerts and integrity checks) lives in `scripts/watch.py`, so a run is repeatable and the numbers come out the same every time. If the inputs don't make sense (a player on both sides of a matchup, a week mismatch, an unresolvable defense), the script stops with `DASHBOARD BLOCKED` instead of rendering something wrong.

### Watchability in one line

`Watchability = 0.80 × Fantasy Exposure + 0.20 × Game Interest`

- **Fantasy Exposure** comes from *your* active starters in the game: how many, across how many leagues, how important those leagues are, and how big a projected role each player has.
- **Game Interest** comes from the two teams' records.

The full plain-language explanation is in [`fantasy-watch/references/scoring.md`](fantasy-watch/references/scoring.md).

## Requirements

- **Claude** with skills support (the Claude app)
- **[Flaim](https://flaim.app)**, a read-only connector to your fantasy leagues (`https://api.flaim.app/mcp`). It can't change your lineups.
- **A browser Claude can drive**: the Claude desktop app's built-in browser, or Claude in Chrome, with `fantasy.espn.com` and `sleeper.com` allowed. The collectors call ESPN's and Sleeper's public endpoints from those pages.
- **Python 3** (standard library only)

## Getting started

1. **Connect Flaim.** In claude.ai go to Settings → Connectors → *Add custom connector*, name it `Flaim`, URL `https://api.flaim.app/mcp`, and sign in.
2. **Link your leagues** at [flaim.app/leagues](https://flaim.app/leagues): ESPN through the Flaim Chrome extension (signed in to ESPN in the same Chrome profile), Yahoo by signing in, Sleeper by username.
3. **Install the skill.** Zip the `fantasy-watch` folder and upload it as a skill in Claude.
4. **Run setup.** Say `/fantasy-watch setup`. Claude checks the connector, lists your football leagues, and asks which leagues matter most, your timezone, and whether any ESPN league isn't full PPR. It saves your answers to `scripts/settings.json` and hands you a personalized zip; re-upload that so your settings stick.
5. **Use it.** `/fantasy-watch` for this week, or just ask "which games should I watch?"

### Arguments

| Command | What it does |
|---|---|
| `/fantasy-watch` | Dashboard for the current NFL week |
| `/fantasy-watch week 9` | Dashboard for a specific week |
| `/fantasy-watch recap` | Recap of last week: results, over- and under-performers |
| `/fantasy-watch setup` | First-time setup, or redo it |
| `/fantasy-watch set timezone America/Denver` | Change one setting (also `set importance`, `set scoring`) |
| `/fantasy-watch show settings` | Show your effective settings |
| `/fantasy-watch schedule` | Set up automatic Thursday, Sunday and Tuesday-recap runs |

## Settings

Settings are layered, and each layer overrides the one before it:

1. `scripts/config.json`: shipped defaults (weights, curves, emojis, injury rules). Generic, nothing personal.
2. `scripts/settings.json`: **your** settings, written by setup. Git-ignored. See `scripts/settings.example.json`.
3. A file named in the `FW_SETTINGS` environment variable, or `--settings FILE` on the command line.

Dicts merge key by key; lists replace whole. The setup command writes the common ones for you:

```bash
python3 fantasy-watch/scripts/watch.py setup \
  --timezone America/Chicago \
  --league "Main League=100" --league "Dynasty=90" --league "Work League=70" \
  --default-importance 40 \
  --scoring "Work League=half" \
  --rename "Wrok Leauge=Work League"

python3 fantasy-watch/scripts/watch.py setup        # no options: show effective settings
```

| Option | Meaning |
|---|---|
| `--timezone` | IANA timezone for kickoff times; the label (ET, CT, PT…) is derived unless you set `--tz-label` |
| `--league NAME=0-100` | How much a league matters, matched by case-insensitive substring. Repeat in priority order; replaces the list. `--clear-leagues` empties it |
| `--default-importance` | Importance for leagues you didn't list (default 40) |
| `--scoring NAME=ppr\|half\|std` | Force a league's scoring. Needed for non-PPR ESPN leagues, since Flaim doesn't report ESPN reception scoring |
| `--default-scoring` | Scoring when nothing is reported (default `ppr`) |
| `--rename RAW=SHOWN` | Display-name fix for a league |

Deeper tuning (Watchability weights, curves, emoji bands, which injury designations count as inactive, Fix now or Keep an eye on) goes in `settings.json` using the same keys as `config.json`.

## Repository layout

```
fantasy-watch/
├── SKILL.md                     # instructions Claude follows, including setup and arguments
├── references/
│   └── scoring.md               # how every number is calculated
└── scripts/
    ├── watch.py                 # the engine: setup, js-espn, js and run
    ├── config.json              # shipped defaults
    ├── settings.example.json    # example personal settings
    ├── collect_espn.js          # browser lookup: ESPN player ID → current NFL team
    ├── collect_nfl.js           # browser collector: schedule, projections, injuries
    └── template.html            # the dashboard page
```

## Running the engine by hand

Once the input files exist:

```bash
python3 fantasy-watch/scripts/watch.py js-espn leagues.json > espn.js     # ESPN ID lookup script
python3 fantasy-watch/scripts/watch.py js leagues.json --espn espn.json > collect.js
python3 fantasy-watch/scripts/watch.py run leagues.json nfl.json --out out
```

`run` writes `out/dashboard.md`, `out/dashboard.json`, `out/audit.txt` and `out/fantasy-watch-week-N.html`. If the inputs don't add up, it stops with `DASHBOARD BLOCKED` instead of rendering something wrong. The weekly data files and `out/` are git-ignored because they contain league and team names.

## Disclaimer

Not affiliated with or endorsed by the NFL, ESPN, Sleeper, Yahoo, Flaim or Anthropic. It reads public data endpoints that can change without notice. Projections come from Sleeper and are only projections.
