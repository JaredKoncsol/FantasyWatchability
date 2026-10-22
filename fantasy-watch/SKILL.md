---
name: fantasy-watch
description: Builds a personalized weekly NFL "which games should I watch" dashboard across all of the user's fantasy football leagues (ESPN, Sleeper, Yahoo via the Flaim connector), ranking every game by Watchability with per-league rooting and lineup alerts, plus first-time setup. Use whenever the user asks for their watch dashboard, watchability rankings, which games to watch or root for this week, a fantasy viewing guide, a weekly recap, to set up or reconfigure fantasy watch, or says /fantasy-watch, even if they don't name the skill.
---

# Fantasy Watch

Claude's job is only to **gather data**. All scoring, rooting, ordering, emoji lookup, formatting and integrity checks live in `scripts/watch.py`, so they come out the same every run. Never compute Watchability, rooting or rankings by hand, and never edit the rendered output to fix something. If the output is wrong, the input file or the script is wrong; fix that and rerun.

Files in this skill:
- `scripts/watch.py`: the engine (`setup`, `js-espn`, `js` and `run` subcommands; `python3 watch.py -h` lists every option)
- `scripts/config.json`: shipped defaults for every tunable number. Don't put personal values here.
- `scripts/settings.json`: the user's personal settings, written by `watch.py setup`. Absent until setup has run.
- `scripts/settings.example.json`: what a filled-in settings file looks like
- `scripts/collect_espn.js`, `scripts/collect_nfl.js`: browser collectors (filled in by `watch.py js-espn` / `watch.py js`)
- `scripts/template.html`: the dashboard page
- `references/scoring.md`: how the math works, for when the user asks why a game ranked where it did

## Arguments

The user may pass arguments after `/fantasy-watch` or say the same thing in words:

| Argument | Meaning |
|---|---|
| *(none)* | Dashboard for the current NFL week |
| `week N` | Dashboard for week N |
| `recap` / `recap N` | Recap of a finished week (default: last week) |
| `setup` | First-time setup, or redo it |
| `set <thing>` | Change one setting, e.g. `set timezone America/Chicago`, `set importance "Dynasty League"=90`, `set scoring "Work League"=half` |
| `show settings` | Print the effective settings (`watch.py setup` with no options) |
| `schedule` | Set up recurring runs (see Scheduling) |

## Requirements
- **Flaim connector** (`https://api.flaim.app/mcp`): the source for leagues, matchups and starters. Read-only; it cannot change lineups.
- **A browser Claude can drive**: the Claude desktop app's built-in browser, or Claude in Chrome. The workspace shell usually can't reach sports APIs; the browser can. `fantasy.espn.com` and `sleeper.com` must be allowed sites. Before the first browser step, read that browser's skill if one is listed.
- **Python 3** (standard library only).

## Setup (`setup`, or first run with no settings.json)

On a normal run with no `scripts/settings.json`, build the dashboard with defaults and add one line at the end offering `/fantasy-watch setup`. Don't block the run on it.

During setup, ask only what's below, batching the questions with AskUserQuestion when it's available:

1. **Flaim.** Look for the Flaim tools (ToolSearch "flaim" if they're deferred). If they're missing, explain how to connect, then stop until the user is back:
   - claude.ai → Settings → Connectors → **Add custom connector**, name `Flaim`, URL `https://api.flaim.app/mcp`, then sign in to Flaim when prompted.
   - Link leagues at https://flaim.app/leagues: **ESPN** through the Flaim Chrome extension while signed in to ESPN in that Chrome profile; **Yahoo** by signing in to Yahoo on that page; **Sleeper** by entering the Sleeper username (no password).
   - Then enable the connector in this chat and ask again. Never ask the user for a password, cookie or token.
2. **Leagues.** Call `get_user_session` and list every current **football** league by name and platform. If a league is missing, suggest `refresh_leagues` or re-linking at flaim.app/leagues.
3. **Importance.** Ask which leagues matter most. Turn the answer into 0–100 importances in priority order (a sensible ladder: 100, 90, 80, 70, 60…; everything unranked gets the default, 40). Importance weights Watchability and overall rooting, and orders leagues on the page.
4. **Timezone** for kickoff times (IANA name, e.g. `America/Chicago`). Default `America/New_York`.
5. **ESPN scoring.** Flaim doesn't report ESPN reception scoring, so ESPN leagues are treated as full PPR. Ask whether any ESPN league is half-PPR or standard. Sleeper and Yahoo are read from league settings automatically.
6. **Display names** (optional): offer to fix typos or shorten long league names.

Then write the settings in one command, from the working copy (Step 0 below):

```bash
python3 fw/watch.py setup --timezone America/Chicago \
  --league "Main League=100" --league "Dynasty League=90" --league "Work League=70" \
  --default-importance 40 \
  --scoring "Work League=half" \
  --rename "Wrok Leauge=Work League"
```

`--league` is matched as a case-insensitive substring of the league name, and repeating it replaces the whole priority list in the order given. `--scoring` and `--rename` add to what's there. Check the printed effective settings with the user.

**Make it stick.** The installed skill folder is read-only, so setup only lasts for this conversation until the user re-uploads the skill. Build a personalized copy and send it:

```bash
mkdir -p fw-pkg && cp -r <skill dir> fw-pkg/fantasy-watch && cp fw/settings.json fw-pkg/fantasy-watch/scripts/ \
  && (cd fw-pkg && zip -qr ../fantasy-watch.zip fantasy-watch)
```

Send `fantasy-watch.zip` and tell the user to replace the installed fantasy-watch skill with it. If they keep the skill in a public repo, `settings.json` holds their league names and is git-ignored there. Then offer to run this week's dashboard.

`set <thing>` works the same way with only the relevant `watch.py setup` options, followed by the same repackaging offer.

## Step 0: Set up the working copy
`cp -r <skill dir>/scripts ./fw`. If the user has a `settings.json` from an earlier setup in this conversation, it's in `./fw` already. Determine the week: the one the user named, otherwise the current NFL week (the collector's ESPN/Sleeper state, or Flaim's matchup default). Before Tuesday of a new week, ask which week if it's ambiguous.

## Step 1: Leagues → `leagues.json` (Flaim)
1. Call `get_user_session`. Take **every current football league** in it. Never use a remembered league list or count.
2. Call `get_league_info` once per league (Flaim requires it before league data). For each league, identify the user's team ID from the session and find their Week N matchup and opponent. **Side is decided only by exact provider team/roster ID**, never by home/away, order, or team names.
3. Capture **only players in starting slots** for both sides, per platform:
   - **ESPN**: `get_matchups` with the user's team_id, the week, and `detail: "players"`. A starter has a real starting `lineupSlot` (not Bench/IR). One row per playerId.
   - **Sleeper**: matchup sides are keyed by `rosterId`. Match the session `teamId` to `rosterId` (compare as strings); exactly one side must match or stop. `get_matchups` already returns each side's `starters[]`; use it (fall back to `get_roster` with the week only if it's missing). Entries with `"empty": true` are open slots.
   - **Yahoo**: `get_matchups` gives the opponent; `get_roster` for both teams with the week; starters are players whose `selectedPosition` is not `BN`/`IR`/reserve.
   - Empty starting slots: compare each side's starters with the league's starting-slot counts from `get_league_info`. Leave empty slots out of the player list and count them in `"empty_slots": {"user": n, "opponent": n}`. The user's empty slots become a Fix-now alert.
   - **Scoring format** per league, from `get_league_info`: points per reception 1 → `"ppr"`, 0.5 → `"half"`, 0 → `"std"` (Sleeper `scoringSettings.rec`, Yahoo `statModifiers` "Receptions"). ESPN's Flaim settings don't include it; write `"ppr"` and let a `--scoring` setting override it.
4. Write `leagues.json` (build it with a short script rather than by hand when there are many leagues):
```json
{"week": 7, "season": 2026, "leagues": [
  {"name": "Main League", "platform": "espn", "scoring": "ppr",
   "user_team": "…", "opponent_team": "…", "empty_slots": {"user": 0, "opponent": 0},
   "user":     [{"id": "4241389", "name": "CeeDee Lamb", "pos": "WR", "slot": "WR"},
                {"id": "-16016", "name": "Vikings D/ST", "pos": "D/ST"}],
   "opponent": [ … ]}
]}
```
   Once any game has kicked off, also record each starter's actual `"points"` (ESPN `players[].points`, Sleeper `starters[].points`, Yahoo roster `points`) and each team's total as `"score": {"user": x, "opponent": y}` (ESPN `currentPoints`, Sleeper/Yahoo `points`). Check that each side's player points sum to its score; a mismatch means a starter was missed or mis-sided.
   `id` is the provider's own player ID, exactly as Flaim returned it. Use the league name exactly as Flaim gives it; display fixes come from settings. For defenses, the name is enough (the script resolves "Vikings D/ST", "Minnesota", "MIN"); add `"team": "MIN"` only if it reports an ambiguous defense.

## Step 2: NFL data → `espn.json`, then `nfl.json` (browser)
Browser output can be large. If the tool saves it to a file, read it from there; if it comes back inline, write the returned string to the file exactly as returned. Never retype, trim or edit the data.

**2a. ESPN exact IDs** (skip if there are no ESPN leagues)
1. `python3 fw/watch.py js-espn leagues.json > espn.js`
2. Open or reuse a `https://fantasy.espn.com/football/` tab and run `espn.js` with the browser's JavaScript tool. It reads ESPN's public player catalog and returns each ESPN starter's current NFL team by exact ID.
3. Save the returned string as `espn.json`. Its `missing` should be empty.

**2b. Everything else**
1. `python3 fw/watch.py js leagues.json --espn espn.json > collect.js` (drop `--espn` if there are no ESPN leagues)
2. Open or reuse a `https://sleeper.com` tab (the API hosts can't be opened directly but fetch fine from that tab). Run `collect.js`.
3. Save the returned string as `nfl.json`. It holds the schedule, records, venues, weather, byes, player teams/numbers/injuries, projections in PPR/half/standard, and the position pools.
4. How players are matched: Sleeper IDs directly; ESPN/Yahoo via Sleeper's cross-IDs when present; otherwise ESPN players by exact name **within the team ESPN reported for that exact ID** (`via_espn_team`), and Yahoo players by exact normalized name, accepted only when exactly one active player matches (`via_name`). Both counts appear in the audit line. Anything in `missing` shows as an alert. Don't guess teams.

## Step 3: Run
`python3 fw/watch.py run leagues.json nfl.json --out out`

- `DASHBOARD BLOCKED: …` means a real data problem (week mismatch, a player on both sides, a missing side, an unresolvable defense, …). Fix `leagues.json` from Flaim data and rerun. Never work around a block by hand.
- Success prints the audit line and writes `out/dashboard.md`, `out/dashboard.json`, `out/audit.txt`, and `out/fantasy-watch-week-N.html`.

## Recap mode (a finished week)
Same steps with the finished week's number passed explicitly to every Flaim call (`week: N`) and in `leagues.json`, plus the points/score fields above. The page then shows final scores, a Won/Lost Matchups table, and over/under-performers (the user's active starters in finished games, actual points vs Sleeper's projection in that league's format; a player in several leagues is averaged). Injury statuses are current, so the engine ignores them for games that have already been played.

## Step 4: Deliver
Publish `out/fantasy-watch-week-N.html` as an artifact (icon `football`) when the artifact tool is available; otherwise send the HTML file. If an earlier week's dashboard exists in this conversation, a new week gets a new artifact; a rerun of the same week republishes the same file path.

For a recap, the reply is the week's record (each league's result and score), then the top 5 overperformers and top 5 disappointments. Otherwise keep it short: the "Fix now" alerts first (bye, Out/IR/Suspended/Doubtful, empty slots, unmatched players), then the top 3 games with their Watchability. "Keep an eye on" (Questionable) items are on the page; mention them only if one is a QB. Nothing about methodology unless asked. If the user wants the dashboard in chat instead of a page, paste `out/dashboard.md`.

Out/IR/Suspended/PUP starters are shown struck through on the page but don't count toward Watchability, rooting or projected points (`inactive_statuses`). Projected points use each league's own scoring; a player with no projection counts as 0 and is noted.

## Scheduling (`schedule`)
Offer three scheduled tasks, created with the session's scheduling tools, in the user's timezone: Thursday ~3:30 PM (before Thursday night), Sunday ~11:45 AM (before the early games), and a Tuesday morning recap. Each task's prompt names this skill and says which mode to run. The browser steps need the Claude desktop app open on the user's computer (or Chrome running with Claude in Chrome), so mark tasks as needing the user's computer when the tool supports it. A scheduled run has nobody to answer questions, so it takes the week from its prompt and doesn't ask.

## When the user changes a rule
League importance, timezone, scoring overrides and display names go through `watch.py setup` (above). Deeper changes (weights, curves, emojis, which injury statuses count as inactive / Fix now / Keep an eye on, projection format for Player Impact) go in `settings.json` as JSON overriding the same keys in `config.json`; dicts merge key by key and lists replace whole. Rerun, and offer the repackaged zip, since changes only last for this conversation otherwise.
