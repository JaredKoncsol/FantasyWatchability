// Fantasy Watch — ESPN identity lookup (exact ESPN player ID -> current NFL team).
// Run in the Claude app browser pane on a https://fantasy.espn.com tab (javascript_tool).
// `watch.py js-espn` fills in the ESPN_IDS and SEASON placeholders. Save the returned string verbatim as espn.json.
const ESPN_IDS = __ESPN_IDS__, SEASON = __SEASON__;
const H = { 'X-Fantasy-Filter': JSON.stringify({ players: { limit: 20000 } }) };
const base = 'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/' + SEASON;
const teams = await fetch(base + '?view=proTeamSchedules_wl', { credentials: 'include' }).then(r => r.json());
const abbr = {}; for (const t of (teams.settings && teams.settings.proTeams) || []) abbr[t.id] = t.abbrev === 'WAS' ? 'WSH' : t.abbrev;
const all = await fetch(base + '/players?view=players_wl', { credentials: 'include', headers: H }).then(r => r.json());
const want = new Set(ESPN_IDS.map(Number)); const out = {};
for (const p of all) if (want.has(p.id)) out[p.id] = [p.fullName, abbr[p.proTeamId] || ''];
JSON.stringify({ catalog_size: all.length, players: out, missing: ESPN_IDS.filter(i => !out[i]) })
