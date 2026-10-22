// Fantasy Watch — NFL data collector.
// Run in the Claude app browser pane on a https://sleeper.com tab (javascript_tool).
// `watch.py js` fills in the REQ placeholder with the week, season and the provider player IDs to resolve.
// Returns one compact JSON object; save it verbatim as nfl.json.
const REQ = __REQ__;
const WK = REQ.week, SEASON = REQ.season;
const POOL = { QB: 32, RB: 64, WR: 96, TE: 32, K: 32, DEF: 32 };
const fix = t => (t === 'WAS' ? 'WSH' : t);

const sb = await fetch(`https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week=${WK}&dates=${SEASON}`).then(r => r.json());
const games = sb.events.map(e => {
  const c = e.competitions[0];
  const side = h => c.competitors.find(x => x.homeAway === h);
  const a = side('away'), h = side('home');
  const rec = t => (t.records && t.records[0] && t.records[0].summary) || '0-0';
  const v = c.venue || {};
  const w = e.weather || c.weather || null;
  const st = e.status.type.state; // pre | in | post
  return [e.id, e.date,
    a.team.abbreviation, a.team.displayName, rec(a),
    h.team.abbreviation, h.team.displayName, rec(h),
    v.fullName || '', [v.address && v.address.city, v.address && v.address.state].filter(Boolean).join(', '),
    !!v.indoor, w ? (w.displayValue || '') + (w.temperature != null ? ` ${w.temperature}°F` : '') : '',
    st, st === 'pre' ? null : +a.score, st === 'pre' ? null : +h.score, e.status.type.shortDetail || ''];
});
const byes = ((sb.week && sb.week.teamsOnBye) || []).map(t => t.abbreviation);

const pos = Object.keys(POOL).map(p => 'position[]=' + p).join('&');
const proj = await fetch(`https://api.sleeper.app/projections/nfl/${SEASON}/${WK}?season_type=regular&${pos}`).then(r => r.json());
// P[id] = [ppr, half_ppr, standard]; pools (for Player Impact) use REQ.scoring only.
const FMT = ['pts_ppr', 'pts_half_ppr', 'pts_std'];
const P = {}; const pools = {};
for (const x of proj) {
  const v = x.stats && x.stats[REQ.scoring];
  if (v == null || !x.team) continue;
  const p = x.player && x.player.position; if (!POOL[p]) continue;
  P[x.player_id] = FMT.map(f => x.stats[f] ?? v); (pools[p] = pools[p] || []).push(v);
}
for (const p in pools) pools[p] = pools[p].sort((a, b) => b - a).slice(0, POOL[p]);

const cat = await fetch('https://api.sleeper.app/v1/players/nfl').then(r => r.json());
const want = { espn: new Set(REQ.espn.map(String)), yahoo: new Set(REQ.yahoo.map(String)), sleeper: new Set(REQ.sleeper.map(String)) };
const players = {};
const row = (id, p) => [id, p.full_name || `${p.first_name} ${p.last_name}`, p.position, fix(p.team || ''), p.number ?? null, p.injury_status || null, P[id] ?? null];
for (const [id, p] of Object.entries(cat)) {
  if (want.sleeper.has(id)) players['sleeper:' + id] = row(id, p);
  if (p.espn_id != null && want.espn.has(String(p.espn_id))) players['espn:' + p.espn_id] = row(id, p);
  if (p.yahoo_id != null && want.yahoo.has(String(p.yahoo_id))) players['yahoo:' + p.yahoo_id] = row(id, p);
}
// Fallback: Sleeper's catalog lacks ESPN/Yahoo IDs for many newer players. ESPN players are matched on
// exact normalized name within the NFL team ESPN reports for that exact ID (espn.json); Yahoo players on
// exact normalized name among active players, narrowed by position if needed.
// Accepted only when exactly one player matches; anything else stays in `missing`.
const norm = s => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/[.'’`-]/g, '').replace(/\b(jr|sr|ii|iii|iv|v)\b/g, '').replace(/\s+/g, ' ').trim();
const byName = {};
for (const [id, p] of Object.entries(cat)) {
  if (!p.team || !p.position) continue;
  const k = norm(p.full_name || `${p.first_name} ${p.last_name}`);
  (byName[k] = byName[k] || []).push([id, p]);
}
const missing = [], viaName = [], viaTeam = [];
for (const [k, s] of Object.entries(want)) for (const i of s) {
  const key = k + ':' + i;
  if (players[key]) continue;
  const [nm, pos] = (REQ.names[key] || '|').split('|');
  let c = byName[norm(nm)] || [];
  // ESPN players: ESPN's own catalog gave the exact current team for this ID; require it.
  const et = (REQ.espn_teams || {})[i];
  if (k === 'espn' && et) c = c.filter(([, p]) => fix(p.team) === et);
  if (c.length > 1 && pos) c = c.filter(([, p]) => p.position === pos);
  if (c.length === 1) { players[key] = row(c[0][0], c[0][1]); (k === 'espn' && et ? viaTeam : viaName).push(key); }
  else missing.push(`${key} ${nm}${c.length > 1 ? ' (ambiguous)' : ''}`);
}
const defs = {}; for (const [id, v] of Object.entries(P)) if (/^[A-Z]{2,3}$/.test(id)) defs[fix(id)] = v;

JSON.stringify({ week: WK, season: SEASON, scoring: REQ.scoring, games, byes: byes.map(fix), players, defs, pools, via_name: viaName, via_espn_team: viaTeam, missing })
