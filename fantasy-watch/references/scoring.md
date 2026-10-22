# How Fantasy Watch scores a game

All numbers live in `scripts/config.json` (defaults), overridden by your `scripts/settings.json`. This is the plain-language version.

## Watchability (0–100%)
`Watchability = 0.80 × Fantasy Exposure + 0.20 × Game Interest`, rounded half-up.

**Game Interest** = 50 × away win% + 50 × home win% (ties count half a win; 0 games played = 0). It never changes with live or final scores.

Starters listed Out, IR, Suspended, PUP or NA are left out of everything below (they still appear on the page, struck through, and in the alerts).

**Fantasy Exposure** uses only *your* active starters in that game (opponents never raise it):
- 20% **Starter count**: curve on total starter instances across leagues (1→15, 2→30, 3→45, 4→58, 5→68 … 13+→100). The same player in three leagues counts three times.
- 20% **Multi-league**: curve on how many leagues you have someone in (1→20, 2→35, 3→48 … 10+→100).
- 20% **League importance**: average importance (0–100) of the leagues involved, from your settings (`watch.py setup --league "Name=90"`); leagues you didn't rank get the default, 40. Averaged per league, so one league with four players isn't counted four times.
- 40% **Player impact**: each starter's projected points this week (Sleeper projections, PPR by default; `scoring` setting) ranked within their position's pool (top 32 QB, 64 RB, 96 WR, 32 TE, 32 K, 32 DEF). Percentile → impact: 90+ = 10, 75+ = 8, 50+ = 6, 25+ = 4, else 2. Sort your starters by impact, multiply by 100% / 70% / 50% / 35% / 25%…, sum, divide by 30, cap at 100. No projection → a fixed position baseline goes through the same steps, and the dashboard says so.

Emoji bands: 90+ 🔥🔥🔥, 80+ 🔥🔥, 70+ 🔥, 60+ 👀, 50+ 📺, below 50 💤.

## League rooting (per league row)
Each starter carries its impact (2–10). For teams A and B: pressure for A = your starters on A + opponent's starters on B (and the mirror for B).
- You only have players on one team → you root for that team, always. **All In** if that side's pressure is at least 2× the other's, otherwise **Slightly**.
- You have players on both → **⚔️ Conflicted**, unless one side's pressure is 2× the other's (then **Slightly** that side).
- You have nobody, opponent has players on one team → **All In** on the other team.
- Opponent on both, you on neither → 2× rule, else Conflicted.

## Overall rooting (per game)
Same pressures summed across all leagues, each starter weighted by impact × league importance. Shows the stronger team and `50 + 100 × |share − 0.5|` (so an even split is 50%, fully one-sided is 100%). No exposure at all shows "— (no stake)".

## Projected points
Sum of each side's active starters' projections in that game, each league in its own format (PPR, half or standard). A starter with no projection counts as 0 and the game notes how many.

## Actual results
Live and final games show the score; each league row shows the points your and your opponent's players scored in that game; the Matchups table shows each league's total (projected before kickoff, then live, then Won/Lost). Over/under-performers compare each of your active starters' actual points with their projection in that league's scoring, for finished games only; a player started in several leagues is averaged across them.

## Ordering
Viewing priority: Watchability high→low, ties by kickoff then game ID. Game sections: kickoff order, grouped by time slot. Leagues within a game: your league priority order, then alphabetical.

## Integrity checks (script stops instead of rendering)
Week in leagues.json ≠ week in nfl.json; a league missing either side's starters; duplicate player on one side; same player on both sides of a matchup; a defense that can't be resolved to one team; any starter lost or duplicated between capture and game grouping; a league rooting that points away from your only exposed team; an overall rooting that doesn't match the larger pressure.

Not blockers (shown as alerts instead). **Fix now:** starters on bye, Out/IR/Suspended/PUP/Doubtful, empty starting slots, players that couldn't be matched. **Keep an eye on:** Questionable.
