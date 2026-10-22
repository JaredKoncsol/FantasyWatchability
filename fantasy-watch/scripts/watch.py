#!/usr/bin/env python3
"""Fantasy Watch — deterministic weekly watchability dashboard.

  watch.py setup [options]              -> writes/updates your personal settings.json (no options: show current)
  watch.py js-espn leagues.json         -> prints the ESPN ID lookup JS
  watch.py js   leagues.json            -> prints the browser collector JS (REQ filled in)
  watch.py run  leagues.json nfl.json   -> writes dashboard.md, dashboard.json, audit.txt to --out

Settings are layered: config.json (defaults, shipped) < settings.json next to this script (your setup)
< the file in $FW_SETTINGS < --settings FILE. Dicts merge key by key; lists replace whole.

All scoring, rooting, sorting and formatting happens here so it is identical every run.
"""
import argparse, json, math, os, re, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DEFAULT_SETTINGS = HERE / "settings.json"
SCORING_CHOICES = ("ppr", "half", "std")


def deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_cfg(extra=None):
    cfg = json.loads((HERE / "config.json").read_text())
    for p in (DEFAULT_SETTINGS, os.environ.get("FW_SETTINGS"), extra):
        if p and Path(p).is_file():
            cfg = deep_merge(cfg, json.loads(Path(p).read_text()))
    return cfg


CFG = load_cfg()
TEAMS = CFG["teams"]
FIX = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


class LedgerError(Exception):
    pass


def rnd(x):  # half-up rounding (Python's round() is banker's)
    return int(math.floor(x + 0.5))


def team_abbr(t):
    t = (t or "").upper()
    return FIX.get(t, t)


# ---------------------------------------------------------------- leagues.json
def load_leagues(path):
    L = json.loads(Path(path).read_text())
    for k in ("week", "season", "leagues"):
        if k not in L:
            raise LedgerError(f"leagues.json missing '{k}'")
    if not L["leagues"]:
        raise LedgerError("leagues.json has no leagues")
    names = set()
    for lg in L["leagues"]:
        if lg.get("name"):
            lg["name"] = CFG.get("league_display", {}).get(lg["name"].strip(), lg["name"].strip())
        nm = lg.get("name")
        if not nm:
            raise LedgerError("a league is missing 'name'")
        if nm in names:
            raise LedgerError(f"league listed twice: {nm}")
        names.add(nm)
        plat = lg.get("platform")
        if plat not in ("espn", "sleeper", "yahoo"):
            raise LedgerError(f"{nm}: platform must be espn|sleeper|yahoo, got {plat!r}")
        for side in ("user", "opponent"):
            if not isinstance(lg.get(side), list):
                raise LedgerError(f"{nm}: '{side}' starter list missing (capture both sides' starters)")
            ids = [str(p.get("id")) for p in lg[side]]
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                raise LedgerError(f"{nm}: duplicate starter id(s) on {side} side: {sorted(dup)}")
            for p in lg[side]:
                if p.get("id") in (None, "") or not p.get("name"):
                    raise LedgerError(f"{nm}: {side} starter needs id and name: {p}")
        both = {str(p["id"]) for p in lg["user"]} & {str(p["id"]) for p in lg["opponent"]}
        if both:
            raise LedgerError(f"{nm}: player id(s) on BOTH sides (ownership mix-up): {sorted(both)}")
    return L


def is_def(p):
    return str(p.get("pos", "")).upper().replace("/", "") in ("DEF", "DST", "D")


def def_team(p):
    """Resolve a defense to its NFL abbreviation from its name/id. Errors if ambiguous."""
    cand = str(p.get("team") or "").upper()
    if cand and team_abbr(cand) in TEAMS:
        return team_abbr(cand)
    s = f"{p.get('name','')} {p.get('id','')}"
    if team_abbr(str(p.get("id", ""))) in TEAMS:
        return team_abbr(str(p["id"]))
    hits = {a for a, (full, nick, _) in TEAMS.items() if re.search(rf"\b{re.escape(nick)}\b", s, re.I)}
    if len(hits) != 1:
        hits = {a for a, (full, nick, _) in TEAMS.items() if full.lower() in s.lower()}
    if len(hits) != 1:
        city = {a for a, (full, nick, _) in TEAMS.items() if s.lower().startswith(full.rsplit(" ", 1)[0].lower())}
        hits = city if len(city) == 1 else hits
    if len(hits) != 1:
        raise LedgerError(f"can't resolve defense {p.get('name')!r} to one NFL team (add \"team\": \"ABBR\")")
    return hits.pop()


POS_MAP = {"D/ST": "DEF", "DST": "DEF", "PK": "K"}


def requested_ids(L):
    req = {"espn": set(), "yahoo": set(), "sleeper": set()}
    names = {}
    for lg in L["leagues"]:
        for side in ("user", "opponent"):
            for p in lg[side]:
                if not is_def(p):
                    req[lg["platform"]].add(str(p["id"]))
                    pos = str(p.get("pos", "")).upper()
                    names[f"{lg['platform']}:{p['id']}"] = f"{p['name']}|{POS_MAP.get(pos, pos)}"
    return {**{k: sorted(v) for k, v in req.items()}, "names": names}


def cmd_js_espn(a):
    L = load_leagues(a.leagues)
    ids = requested_ids(L)["espn"]
    print((HERE / "collect_espn.js").read_text().replace("__ESPN_IDS__", json.dumps([int(i) for i in ids]))
          .replace("__SEASON__", str(int(L["season"]))))


def cmd_js(a):
    L = load_leagues(a.leagues)
    req = {"week": L["week"], "season": L["season"], "scoring": CFG["scoring"], **requested_ids(L)}
    if a.espn:
        E = json.loads(Path(a.espn).read_text())
        req["espn_teams"] = {str(k): v[1] for k, v in E["players"].items() if v[1] and v[1] != "FA"}
    js = (HERE / "collect_nfl.js").read_text().replace("__REQ__", json.dumps(req, separators=(",", ":")))
    print(js)


# ---------------------------------------------------------------- scoring helpers
def curve(c, n):
    return c[min(n, len(c) - 1)]


def league_importance(name):
    for r in CFG["league_priority"]:
        if r["match"].lower() in name.lower():
            return r["importance"]
    return CFG["default_league_importance"]


def league_rank(name):
    for i, r in enumerate(CFG["league_priority"]):
        if r["match"].lower() in name.lower():
            return (i, name)
    return (len(CFG["league_priority"]), name.lower())


SCORING_INDEX = {"ppr": 0, "half": 1, "std": 2}


def league_scoring(lg):
    sc = lg.get("scoring") or CFG["default_league_scoring"]
    for k, v in (CFG.get("league_scoring") or {}).items():  # explicit per-league override from setup wins
        if k.lower() in lg["name"].lower():
            sc = v
            break
    sc = str(sc).lower()
    if sc not in SCORING_INDEX:
        raise LedgerError(f"{lg['name']}: scoring must be ppr|half|std, got {sc!r}")
    return sc


def impact_from_percentile(pct):
    for floor_, val in CFG["impact_buckets"]:
        if pct >= floor_:
            return val
    return CFG["impact_buckets"][-1][1]


def percentile(value, pool):
    if not pool:
        return 0.0
    return 100.0 * sum(1 for v in pool if v < value) / len(pool)


def parse_rec(s):
    parts = [int(x) for x in re.findall(r"\d+", s or "")]
    w, l, t = (parts + [0, 0, 0])[:3]
    return w, l, t


def win_pct(s):
    w, l, t = parse_rec(s)
    g = w + l + t
    return (w + 0.5 * t) / g if g else 0.0


def fmt_rec(s):
    w, l, t = parse_rec(s)
    return f"{w}-{l}" + (f"-{t}" if t else "")


def emoji(abbr):
    return TEAMS[abbr][2]


def watch_emoji(pct):
    for floor_, e in CFG["watch_bands"]:
        if pct >= floor_:
            return e
    return CFG["watch_bands"][-1][1]


# ---------------------------------------------------------------- build ledger
def build(L, N):
    if int(N["week"]) != int(L["week"]):
        raise LedgerError(f"week mismatch: leagues.json week {L['week']} vs nfl.json week {N['week']}")
    tz = ZoneInfo(CFG["timezone"])
    games = []
    team_game = {}
    for g in N["games"]:
        (gid, iso, aa, an, ar, ha, hn, hr, venue, city, indoor, wx, state, ascore, hscore) = g[:15]
        status = g[15] if len(g) > 15 else ("Final" if state == "post" else "")
        aa, ha = team_abbr(aa), team_abbr(ha)
        for t in (aa, ha):
            if t not in TEAMS:
                raise LedgerError(f"unknown NFL team abbreviation in schedule: {t}")
            if t in team_game:
                raise LedgerError(f"{t} appears in two games this week")
            team_game[t] = gid
        ko = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)
        games.append(dict(id=str(gid), kickoff=ko, away=aa, home=ha, away_name=an, home_name=hn,
                          away_rec=fmt_rec(ar), home_rec=fmt_rec(hr), venue=venue, city=city,
                          weather=("Indoors" if indoor else (wx.strip() or "Weather unavailable")),
                          state=state, status=status, away_score=ascore, home_score=hscore))
    byes = {team_abbr(t) for t in N.get("byes", [])}
    game_state = {g["id"]: g["state"] for g in games}
    pools = N["pools"]

    rows, alerts, unresolved = [], [], []
    for lg in L["leagues"]:
        for side in ("user", "opponent"):
            for p in lg[side]:
                pts = p.get("points")
                r = dict(league=lg["name"], side=side, pid=str(p["id"]), slot=p.get("slot", ""),
                         provider_name=p["name"], points=None if pts is None else float(pts))
                if is_def(p):
                    t = def_team(p)
                    r.update(name=f"{TEAMS[t][1]} D/ST", pos="DEF", team=t, number=None, injury=None,
                             proj=N["defs"].get(t))
                    r["sleeper_id"] = t
                else:
                    hit = N["players"].get(f"{lg['platform']}:{p['id']}")
                    if not hit:
                        unresolved.append(r)
                        continue
                    sid, name, pos, team, num, inj, proj = hit
                    if pos not in CFG["projection_fallback"]:  # e.g. two-way players listed as DB
                        pos = POS_MAP.get(str(p.get("pos", "")).upper(), str(p.get("pos", "")).upper())
                    r.update(sleeper_id=sid, name=name, pos=pos, team=team_abbr(team),
                             number=num, injury=inj, proj=proj)
                    if r["team"] and r["team"] not in TEAMS:
                        raise LedgerError(f"{name}: unknown NFL team {team}")
                # projections: [ppr, half, std] (older collectors sent one number)
                pv = r.get("proj")
                fmts = pv if isinstance(pv, list) else [pv, pv, pv]
                r["proj"] = fmts[0]
                r["proj_league"] = fmts[SCORING_INDEX[league_scoring(lg)]]
                r["game"] = team_game.get(r["team"])
                # Injury status is "as of now", so it only matters for games that haven't kicked off.
                not_started = r["game"] is None or game_state[r["game"]] == "pre"
                r["inactive"] = not_started and r.get("injury") in CFG["inactive_statuses"]
                if side == "user":
                    if not r["team"]:
                        alerts.append((lg["name"], r, "not on an NFL roster", "fix"))
                    elif r["team"] in byes:
                        alerts.append((lg["name"], r, "on BYE", "fix"))  # still worth knowing in a recap
                    elif r["game"] is None:
                        alerts.append((lg["name"], r, "team has no game this week", "fix"))
                    if not not_started:
                        pass
                    elif r.get("injury") in CFG["fix_now_statuses"]:
                        alerts.append((lg["name"], r, r["injury"], "fix"))
                    elif r.get("injury") in CFG["keep_an_eye_statuses"]:
                        alerts.append((lg["name"], r, r["injury"], "watch"))
                rows.append(r)
        empty = int((lg.get("empty_slots") or {}).get("user", 0) or 0)
        if empty:
            alerts.append((lg["name"], None, f"{empty} empty starting slot{'s' if empty > 1 else ''}", "fix"))

    # Player Impact (position percentile -> 2..10)
    fallback_used = False
    for r in rows:
        pool = pools.get(r["pos"], [])
        if r.get("proj") is None:
            r["fallback"] = True
            fallback_used = fallback_used or (r["side"] == "user" and r["game"] is not None and not r["inactive"])
            base = CFG["projection_fallback"].get(r["pos"], 2)
            r["impact"] = impact_from_percentile(percentile(base, pool))
        else:
            r["fallback"] = False
            r["impact"] = impact_from_percentile(percentile(r["proj"], pool))
    return games, rows, alerts, unresolved, fallback_used


# ---------------------------------------------------------------- per-game math
def league_rooting(rows, A, B):
    w = lambda side, t: sum(r["impact"] for r in rows if r["side"] == side and r["team"] == t)
    UA, UB, OA, OB = w("user", A), w("user", B), w("opponent", A), w("opponent", B)
    PA, PB = UA + OB, UB + OA
    k = CFG["decisive_ratio"]
    if UA and not UB:
        return ("All In" if PB == 0 or PA >= k * PB else "Slightly", A), (UA, UB, OA, OB)
    if UB and not UA:
        return ("All In" if PA == 0 or PB >= k * PA else "Slightly", B), (UA, UB, OA, OB)
    if UA and UB:
        if PA >= k * PB:
            return ("Slightly", A), (UA, UB, OA, OB)
        if PB >= k * PA:
            return ("Slightly", B), (UA, UB, OA, OB)
        return ("Conflicted", None), (UA, UB, OA, OB)
    if OA and not OB:
        return ("All In", B), (UA, UB, OA, OB)
    if OB and not OA:
        return ("All In", A), (UA, UB, OA, OB)
    if OA and OB:
        if PA >= k * PB:
            return ("Slightly", A), (UA, UB, OA, OB)
        if PB >= k * PA:
            return ("Slightly", B), (UA, UB, OA, OB)
        return ("Conflicted", None), (UA, UB, OA, OB)
    return ("None", None), (0, 0, 0, 0)


def score_game(g, rows):
    A, B = g["away"], g["home"]
    shown = [r for r in rows if r["game"] == g["id"]]          # everyone in this game (charts)
    gr = [r for r in shown if not r["inactive"]]               # only active starters are scored
    user = [r for r in gr if r["side"] == "user"]
    w = CFG["weights"]
    gi = 50 * win_pct(g["away_rec"]) + 50 * win_pct(g["home_rec"])
    if user:
        sc = curve(CFG["starter_count_curve"], len(user))
        leagues = sorted({r["league"] for r in user})
        ml = curve(CFG["multi_league_curve"], len(leagues))
        li = sum(league_importance(l) for l in leagues) / len(leagues)
        imps = sorted((r["impact"] for r in user), reverse=True)
        mult = CFG["diminishing_returns"]
        adj = sum(v * mult[min(i, len(mult) - 1)] for i, v in enumerate(imps))
        pi = min(adj / CFG["impact_reference_max"], 1) * 100
        fe = w["starter_count"] * sc + w["multi_league"] * ml + w["league_importance"] * li + w["player_impact"] * pi
    else:
        sc = ml = li = pi = fe = 0.0
    watch = max(0, min(100, rnd(w["fantasy_exposure"] * fe + w["game_interest"] * gi)))

    # Overall rooting: impact x league importance, opponent exposure pushes the other way
    pa = pb = 0.0
    for r in gr:
        v = r["impact"] * league_importance(r["league"])
        mine = r["side"] == "user"
        if (r["team"] == A) == mine:
            pa += v
        else:
            pb += v
    if pa == pb == 0:
        overall = None
    elif pa == pb:
        overall = (None, 50)
    else:
        share = max(pa, pb) / (pa + pb)
        overall = (A if pa > pb else B, max(50, min(100, rnd(50 + 100 * abs(share - 0.5)))))

    # League rows
    charts = []
    for lg in sorted({r["league"] for r in shown}, key=league_rank):
        lr = [r for r in shown if r["league"] == lg]
        label, buckets = league_rooting([r for r in lr if not r["inactive"]], A, B)
        charts.append(dict(league=lg, user=[r for r in lr if r["side"] == "user"],
                           opp=[r for r in lr if r["side"] == "opponent"], rooting=label, buckets=buckets))

    # Projected points in each league's own scoring; missing projections count as 0 and are counted.
    proj = None
    if user:
        opp = [r for r in gr if r["side"] == "opponent"]
        tot = lambda rs: sum(r["proj_league"] or 0 for r in rs)
        proj = (tot(user), tot(opp), sum(1 for r in gr if r.get("proj_league") is None))

    g.update(rows=shown, watch=watch, overall=overall, charts=charts, proj=proj,
             components=dict(starters=len(user), starter_count=sc, multi_league=ml, league_importance=li,
                             player_impact=pi, fantasy_exposure=fe, game_interest=gi, pa=pa, pb=pb))
    return g


# ---------------------------------------------------------------- results (actual points)
def week_state(games):
    states = {g["state"] for g in games}
    if states == {"post"}:
        return "final"
    return "pre" if states <= {"pre"} else "live"


def results(L, games, rows):
    gstate = {g["id"]: g["state"] for g in games}
    ws = week_state(games)
    started = lambda r: r["game"] is not None and gstate.get(r["game"]) != "pre"
    # per-league matchup totals
    matchups = []
    for lg in sorted(L["leagues"], key=lambda l: league_rank(l["name"])):
        lr = [r for r in rows if r["league"] == lg["name"]]
        tot = {}
        for side in ("user", "opponent"):
            sr = [r for r in lr if r["side"] == side]
            given = (lg.get("score") or {}).get(side)
            pts = float(given) if given is not None else (
                sum(r["points"] or 0 for r in sr) if ws != "pre" else None)
            proj = sum(r["proj_league"] or 0 for r in sr if not r["inactive"] and r["game"] is not None)
            left = sum(1 for r in sr if r["game"] is not None and gstate.get(r["game"]) != "post" and not r["inactive"])
            tot[side] = dict(points=pts, proj=proj, left=left)
        u, o = tot["user"]["points"], tot["opponent"]["points"]
        if u is None or o is None:
            status = "Upcoming"
        elif ws == "final":
            status = "Won" if u > o else "Lost" if u < o else "Tied"
        else:
            status = "Leading" if u > o else "Trailing" if u < o else "Tied"
        matchups.append(dict(league=lg["name"], opponent=lg.get("opponent_team", ""), user=tot["user"],
                             opp=tot["opponent"], status=status))
    # per game/league row points (only once that game has started)
    for g in games:
        for c in g["charts"]:
            if g["state"] == "pre":
                c["pts"] = None
            else:
                c["pts"] = (sum(r["points"] or 0 for r in c["user"]), sum(r["points"] or 0 for r in c["opp"]))
    # over/under performers: your active starters in finished games, actual vs projection (league scoring)
    grp = {}
    for r in rows:
        if (r["side"] != "user" or r["inactive"] or r["points"] is None or r.get("proj_league") is None
                or r["game"] is None or gstate.get(r["game"]) != "post"):
            continue
        k = r.get("sleeper_id") or r["name"]
        e = grp.setdefault(k, dict(r=r, act=[], proj=[], leagues=[]))
        e["act"].append(r["points"]); e["proj"].append(r["proj_league"]); e["leagues"].append(r["league"])
    perf = []
    for e in grp.values():
        a, p = sum(e["act"]) / len(e["act"]), sum(e["proj"]) / len(e["proj"])
        perf.append(dict(player=fmt_player(e["r"], tag=False), qb=e["r"]["pos"] == "QB", actual=round(a, 1),
                         proj=round(p, 1), diff=round(a - p, 1), leagues=sorted(e["leagues"], key=league_rank)))
    n = CFG.get("performers_count", 8)
    over = sorted([x for x in perf if x["diff"] > 0], key=lambda x: -x["diff"])[:n]
    under = sorted([x for x in perf if x["diff"] < 0], key=lambda x: x["diff"])[:n]
    return ws, matchups, over, under


def fmt_pts(v):
    return "—" if v is None else f"{v:.1f}"


# ---------------------------------------------------------------- rendering
def fmt_player(r, tag=True, pts=False):
    num = f" {r['number']}" if r.get("number") not in (None, "") else ""
    s = f"{r['name']}{num} ({r['team']})"
    s = f"**{s}**" if r["pos"] == "QB" else s
    s += f" _{r['injury']}_" if tag and r.get("inactive") else ""
    return s + (f" · {r['points']:.1f}" if pts and r.get("points") is not None else "")


def fmt_rooting(label):
    kind, team = label
    if kind == "Conflicted":
        return "⚔️ Conflicted"
    if kind == "None":
        return "—"
    return f"{kind} — {team} {emoji(team)}"


def fmt_overall(o):
    if o is None:
        return "— (no stake)"
    team, pct = o
    if team is None:
        return "Even 50%"
    return f"{emoji(team)} {team} {pct}%"


def tz_label():
    if CFG.get("tz_label"):
        return CFG["tz_label"]
    ab = datetime.now(ZoneInfo(CFG["timezone"])).tzname() or ""
    return ab[0] + ab[2] if len(ab) == 3 and ab[1] in "DS" and ab[2] == "T" else ab  # EDT/EST -> ET, PDT -> PT


def matchup_text(g):
    return f"{emoji(g['away'])} {g['away_name']} vs {emoji(g['home'])} {g['home_name']}"


def render_md(week, games, alerts, unresolved, fallback_used, audit, res):
    tzl = tz_label()
    out = [f"# Fantasy Watch — Week {week}", ""]
    if fallback_used:
        out += ["_Projection data unavailable for one or more players; position fallback values used for Player Impact._", ""]
    fix = [a for a in alerts if a[3] == "fix"]
    eye = [a for a in alerts if a[3] == "watch"]
    line = lambda lg, r, why: f"- **{lg}** — " + (f"{fmt_player(r, tag=False)}: {why}" if r else why)
    mine = [r for r in unresolved if r["side"] == "user"]
    theirs = [r for r in unresolved if r["side"] != "user"]
    if fix or mine:
        out += ["## 🚨 Fix now", ""] + [line(lg, r, w) for lg, r, w, _ in fix]
        for r in mine:
            out.append(f"- **{r['league']}** — {r['provider_name']}: couldn't match player ID {r['pid']}; left out of scores")
        out.append("")
    if eye:
        out += ["## 👀 Keep an eye on", ""] + [line(lg, r, w) for lg, r, w, _ in eye] + [""]

    ws, matchups, over, under = res
    out += ["## 🏈 Matchups", "", "| League | You | Opponent | Status |", "|---|---|---|---|"]
    for m in matchups:
        side = lambda t: (f"{fmt_pts(t['points'])} (proj {t['proj']:.1f})" if t["points"] is not None else f"proj {t['proj']:.1f}")
        out.append(f"| {m['league']} | {side(m['user'])} | {side(m['opp'])} | {m['status']} |")
    out.append("")
    if over or under:
        row = lambda x: f"- {x['player']}: {x['actual']:.1f} vs {x['proj']:.1f} proj (**{x['diff']:+.1f}**)" + (
            f" · {len(x['leagues'])} leagues" if len(x["leagues"]) > 1 else f" · {x['leagues'][0]}")
        if over:
            out += ["## 📈 Overperformers", ""] + [row(x) for x in over] + [""]
        if under:
            out += ["## 📉 Disappointments", ""] + [row(x) for x in under] + [""]

    out += ["## 📺 Viewing Priority", "", "| Rank | Watchability | Matchup | Date | Kickoff |", "|---|---|---|---|---|"]
    order = sorted(games, key=lambda g: (-g["watch"], g["kickoff"], g["id"]))
    for i, g in enumerate(order, 1):
        m = matchup_text(g)
        if g["state"] == "post":
            m = f"~~{m}~~ {g['away']} {g['away_score']}–{g['home_score']} {g['home']}"
        elif g["state"] == "in":
            m = f"{m} — 🔴 {g['status']}, {g['away']} {g['away_score']}–{g['home_score']} {g['home']}"
        out.append(f"| {i} | {watch_emoji(g['watch'])} {g['watch']}% | {m} | {g['kickoff']:%a %-m/%-d} | {g['kickoff']:%-I:%M %p} {tzl} |")
    out.append("")

    for g in sorted(games, key=lambda g: (g["kickoff"], g["id"])):
        out += [f"## {emoji(g['away'])} {g['away_name']} ({g['away_rec']}) vs {emoji(g['home'])} {g['home_name']} ({g['home_rec']})",
                f"🏟️ {g['venue']} • 📍 {g['city']}  ",
                f"🌤️ {g['weather']} • {g['kickoff']:%a %-I:%M %p} {tzl}", "", "---", "",
                f"**Watchability:** {watch_emoji(g['watch'])} {g['watch']}%  ",
                f"**Overall Rooting:** {fmt_overall(g['overall'])}" + ("  " if g["proj"] else "")]
        if g["proj"]:
            miss = g["proj"][2]
            note = f" _({miss} player{'s' if miss != 1 else ''} with no projection counted as 0)_" if miss else ""
            out.append(f"**Projected Points:** Your Team {g['proj'][0]:.1f} — Opponent {g['proj'][1]:.1f}{note}")
        out += ["", "| League | Your Players | Opponent Players | Rooting |", "|---|---|---|---|"]
        if not g["charts"]:
            out.append("| No direct fantasy exposure | — | — | — |")
        for c in g["charts"]:
            live = g["state"] != "pre"
            u = "<br>".join(fmt_player(r, pts=live) for r in sorted(c["user"], key=lambda r: (r["inactive"], -r["impact"]))) or "—"
            o = "<br>".join(fmt_player(r, pts=live) for r in sorted(c["opp"], key=lambda r: (r["inactive"], -r["impact"]))) or "—"
            lname = c["league"] + (f"<br>{c['pts'][0]:.1f}–{c['pts'][1]:.1f}" if c.get("pts") else "")
            out.append(f"| {lname} | {u} | {o} | {fmt_rooting(c['rooting'])} |")
        out.append("")

    out += ["---", ""]
    if theirs:
        out += ["_Opponent players that couldn't be matched (left out of scores): " +
                ", ".join(f"{r['provider_name']} ({r['league']})" for r in theirs) + "_", ""]
    out += [f"_Audit: {audit}_", ""]
    return "\n".join(out)


def self_check(L, games, rows, unresolved):
    """Hard invariants. Any failure raises — nothing partial gets rendered."""
    src = sum(len(lg[s]) for lg in L["leagues"] for s in ("user", "opponent"))
    if len(rows) + len(unresolved) != src:
        raise LedgerError(f"starter count drift: source {src} vs ledger {len(rows)} + unresolved {len(unresolved)}")
    placed = [r for g in games for r in g["rows"]]
    keys = [(r["league"], r["side"], r["pid"]) for r in placed]
    if len(keys) != len(set(keys)):
        raise LedgerError("a starter landed in more than one game")
    orphans = [r for r in rows if r["game"] is None]
    if len(placed) + len(orphans) != len(rows):
        raise LedgerError("game grouping lost or duplicated starters")
    for g in games:
        for r in g["rows"]:
            if r["team"] not in (g["away"], g["home"]):
                raise LedgerError(f"{r['name']} ({r['team']}) grouped into {g['away']}@{g['home']}")
        for c in g["charts"]:
            kind, team = c["rooting"]
            UA, UB, _, _ = c["buckets"]
            if UA and not UB and team != g["away"]:
                raise LedgerError(f"{c['league']} {g['away']}@{g['home']}: rooting points against your only side")
            if UB and not UA and team != g["home"]:
                raise LedgerError(f"{c['league']} {g['away']}@{g['home']}: rooting points against your only side")
        if g["overall"] and g["overall"][0]:
            want = g["away"] if g["components"]["pa"] > g["components"]["pb"] else g["home"]
            if g["overall"][0] != want:
                raise LedgerError(f"overall rooting direction wrong for {g['away']}@{g['home']}")
    per = ", ".join(f"{lg['name']} {len(lg['user'])}+{len(lg['opponent'])}" for lg in L["leagues"])
    return src, len(placed), len(orphans), len(unresolved), per


def cmd_run(a):
    L = load_leagues(a.leagues)
    N = json.loads(Path(a.nfl).read_text())
    games, rows, alerts, unresolved, fb = build(L, N)
    for g in games:
        score_game(g, rows)
    src, placed, orphans, unres, per = self_check(L, games, rows, unresolved)
    vn = len(N.get("via_name", []))
    vt = len(N.get("via_espn_team", []))
    audit = (f"{len(L['leagues'])} leagues, {src} starters captured; {placed} placed in {len(games)} games, "
             f"{orphans} on bye/no game, {unres} unmatched" + (f", {vt} ESPN players matched by exact ID + team" if vt else "") + (f", {vn} matched by exact name only" if vn else "") + f". Per league (you+opp): {per}.")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = results(L, games, rows)
    ws, matchups, over, under = res
    (out / "dashboard.md").write_text(render_md(L["week"], games, alerts, unresolved, fb, audit, res))
    model = dict(week=L["week"], season=L["season"], sample=bool(L.get("sample")), tz=tz_label(), fallback_used=fb, audit=audit,
                 week_state=ws, matchups=matchups, over=over, under=under,
                 alerts=[dict(league=lg, player=fmt_player(r, tag=False) if r else "", why=w, level=lv)
                         for lg, r, w, lv in alerts],
                 unresolved=[dict(league=r["league"], side=r["side"], name=r["provider_name"], id=r["pid"]) for r in unresolved],
                 games=[dict(id=g["id"], kickoff=g["kickoff"].isoformat(), day=f"{g['kickoff']:%a %-m/%-d}",
                             time=f"{g['kickoff']:%-I:%M %p}", away=g["away"], home=g["home"],
                             away_name=g["away_name"], home_name=g["home_name"], away_rec=g["away_rec"],
                             home_rec=g["home_rec"], away_emoji=emoji(g["away"]), home_emoji=emoji(g["home"]),
                             venue=g["venue"], city=g["city"], weather=g["weather"], state=g["state"], status=g["status"],
                             score=[g["away_score"], g["home_score"]], watch=g["watch"],
                             watch_emoji=watch_emoji(g["watch"]), overall=fmt_overall(g["overall"]),
                             proj=g["proj"], components={k: round(v, 2) for k, v in g["components"].items()},
                             charts=[dict(league=c["league"], rooting=fmt_rooting(c["rooting"]), pts=c.get("pts"),
                                          user=[dict(p=fmt_player(r, tag=False), qb=r["pos"] == "QB", out=r["injury"] if r["inactive"] else None,
                                                     pts=r["points"] if g["state"] != "pre" else None)
                                                for r in sorted(c["user"], key=lambda r: (r["inactive"], -r["impact"]))],
                                          opp=[dict(p=fmt_player(r, tag=False), qb=r["pos"] == "QB", out=r["injury"] if r["inactive"] else None,
                                                    pts=r["points"] if g["state"] != "pre" else None)
                                               for r in sorted(c["opp"], key=lambda r: (r["inactive"], -r["impact"]))])
                                     for c in g["charts"]])
                        for g in games])
    (out / "dashboard.json").write_text(json.dumps(model, ensure_ascii=False, indent=1))
    data = json.dumps(model, ensure_ascii=False).replace("</", "<\\/")
    html = (HERE / "template.html").read_text().replace("__WEEK__", str(L["week"])).replace("/*DATA*/null", data)
    (out / f"fantasy-watch-week-{L['week']}.html").write_text(html)
    lines = [audit, ""]
    for g in sorted(games, key=lambda g: -g["watch"]):
        c = g["components"]
        lines.append(f"{g['away']}@{g['home']}: watch {g['watch']} = 0.8*FE {c['fantasy_exposure']:.1f} + 0.2*GI {c['game_interest']:.1f} "
                     f"| SC {c['starter_count']} ML {c['multi_league']} LI {c['league_importance']:.0f} PI {c['player_impact']:.1f} "
                     f"| starters {c['starters']} | rooting A {c['pa']:.0f} vs B {c['pb']:.0f}")
    (out / "audit.txt").write_text("\n".join(lines) + "\n")
    print(audit)


# ---------------------------------------------------------------- setup
def _pairs(items, what):
    out = []
    for it in items or []:
        if "=" not in it:
            raise LedgerError(f"{what} must look like NAME=VALUE, got {it!r}")
        k, v = it.rsplit("=", 1)
        if not k.strip():
            raise LedgerError(f"{what}: empty name in {it!r}")
        out.append((k.strip(), v.strip()))
    return out


def cmd_setup(a):
    path = Path(a.settings or DEFAULT_SETTINGS)
    S = json.loads(path.read_text()) if path.is_file() else {}
    changed = False
    if a.timezone:
        try:
            ZoneInfo(a.timezone)
        except Exception:
            raise LedgerError(f"unknown timezone {a.timezone!r} (use an IANA name like America/Chicago)")
        S["timezone"] = a.timezone
        S.pop("tz_label", None) if not a.tz_label else None
        changed = True
    if a.tz_label is not None:
        S["tz_label"] = a.tz_label
        changed = True
    if a.league:
        pri = []
        for name, imp in _pairs(a.league, "--league"):
            try:
                imp = int(imp)
            except ValueError:
                raise LedgerError(f"--league importance must be a whole number 0-100, got {imp!r}")
            if not 0 <= imp <= 100:
                raise LedgerError(f"--league importance must be 0-100, got {imp}")
            pri.append({"match": name, "importance": imp})
        S["league_priority"] = pri
        changed = True
    if a.clear_leagues:
        S["league_priority"] = []
        changed = True
    if a.default_importance is not None:
        if not 0 <= a.default_importance <= 100:
            raise LedgerError("--default-importance must be 0-100")
        S["default_league_importance"] = a.default_importance
        changed = True
    if a.rename:
        S["league_display"] = {**S.get("league_display", {}), **dict(_pairs(a.rename, "--rename"))}
        changed = True
    if a.scoring:
        m = S.get("league_scoring", {})
        for name, sc in _pairs(a.scoring, "--scoring"):
            if sc.lower() not in SCORING_CHOICES:
                raise LedgerError(f"--scoring for {name}: must be ppr|half|std, got {sc!r}")
            m[name] = sc.lower()
        S["league_scoring"] = m
        changed = True
    if a.default_scoring:
        if a.default_scoring.lower() not in SCORING_CHOICES:
            raise LedgerError("--default-scoring must be ppr|half|std")
        S["default_league_scoring"] = a.default_scoring.lower()
        changed = True
    if changed:
        path.write_text(json.dumps(S, ensure_ascii=False, indent=2) + "\n")
        print(f"saved {path}")
    global CFG
    CFG = load_cfg(path)
    show = {k: CFG.get(k) for k in ("timezone", "default_league_scoring", "league_scoring", "league_priority",
                                    "default_league_importance", "league_display")}
    show["tz_label"] = tz_label()
    print(json.dumps(show, ensure_ascii=False, indent=2))


def main():
    global CFG, TEAMS
    ap = argparse.ArgumentParser(description="Fantasy Watch engine")
    ap.add_argument("--settings", help="extra settings JSON layered over config.json and settings.json")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("setup", help="write your personal settings.json (no options: show effective settings)")
    s.add_argument("--timezone", help="IANA timezone for kickoff times, e.g. America/Chicago")
    s.add_argument("--tz-label", help="label shown after times (default: derived, e.g. CT)")
    s.add_argument("--league", action="append", metavar="NAME=IMPORTANCE",
                   help="league importance 0-100, matched by name substring; repeat in priority order (replaces the list)")
    s.add_argument("--clear-leagues", action="store_true", help="remove all league importances")
    s.add_argument("--default-importance", type=int, help="importance for leagues not listed (default 40)")
    s.add_argument("--rename", action="append", metavar="RAW=SHOWN", help="display name fix for a league, e.g. typos")
    s.add_argument("--scoring", action="append", metavar="NAME=ppr|half|std",
                   help="force a league's scoring (needed for ESPN non-PPR leagues; Flaim doesn't report it)")
    s.add_argument("--default-scoring", help="scoring for leagues with nothing reported (ppr|half|std)")
    s.set_defaults(f=cmd_setup)
    e = sp.add_parser("js-espn"); e.add_argument("leagues"); e.set_defaults(f=cmd_js_espn)
    j = sp.add_parser("js"); j.add_argument("leagues"); j.add_argument("--espn"); j.set_defaults(f=cmd_js)
    r = sp.add_parser("run"); r.add_argument("leagues"); r.add_argument("nfl"); r.add_argument("--out", default="out")
    r.set_defaults(f=cmd_run)
    a = ap.parse_args()
    if a.settings and a.cmd != "setup":
        CFG = load_cfg(a.settings)
        TEAMS = CFG["teams"]
    try:
        a.f(a)
    except LedgerError as e:
        print(f"{'SETUP ERROR' if a.cmd == 'setup' else 'DASHBOARD BLOCKED'}: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
