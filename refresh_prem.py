#!/usr/bin/env python3
"""
Refresh prem.html with live FPL data.
Reads prem.html, updates LFC_DATA (fixtures, table, scorers, squads, meta), writes back.
Run on schedule via GitHub Actions or manually.
"""
import json, urllib.request, re, sys, os
from datetime import datetime, timezone

FPL_BOOT = 'https://fantasy.premierleague.com/api/bootstrap-static/'
FPL_FX   = 'https://fantasy.premierleague.com/api/fixtures/'

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else 'prem.html'
    print(f'Refreshing {html_path}...')

    # --- Fetch FPL data ---
    boot = fetch_json(FPL_BOOT)
    fixtures_raw = fetch_json(FPL_FX)

    teams = boot['teams']
    players = boot['elements']
    events = boot['events']
    pos_types = {et['id']: et['id'] for et in boot['element_types']}  # 1=GK,2=DEF,3=MID,4=FWD

    # FPL id -> PL code mapping
    id2code = {t['id']: t['code'] for t in teams}
    id2name = {t['id']: t['name'] for t in teams}
    id2short = {t['id']: t['short_name'] for t in teams}

    # --- Read existing HTML ---
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # Extract existing LFC_DATA
    m = re.search(r'var LFC_DATA=(\{.*?\});', html, re.DOTALL)
    if not m:
        print('ERROR: Could not find LFC_DATA in HTML')
        sys.exit(1)
    old_data = json.loads(m.group(1))

    # --- Build fixtures ---
    # Format: [gameweek, isoDate, homeCode, awayCode, homeScore|null, awayScore|null, status]
    new_fixtures = []
    for fx in fixtures_raw:
        gw = fx['event'] or 0
        ko = fx['kickoff_time'] or ''
        hc = id2code[fx['team_h']]
        ac = id2code[fx['team_a']]
        hs = fx['team_h_score']
        as_ = fx['team_a_score']

        if fx['finished'] or fx.get('finished_provisional'):
            status = 'FT'
        elif fx['started']:
            mins = fx.get('minutes', 0)
            if mins and mins <= 45:
                status = '1H'
            elif mins and mins > 45:
                status = '2H'
            else:
                status = 'LIVE'
        else:
            status = 'U'
            hs = None
            as_ = None

        new_fixtures.append([gw, ko, hc, ac, hs, as_, status])

    # --- Build table from fixtures ---
    # Compute standings from finished fixtures
    table_data = {}
    for t in teams:
        c = t['code']
        table_data[c] = {'code': c, 'p': 0, 'w': 0, 'd': 0, 'l': 0, 'gf': 0, 'ga': 0, 'pts': 0, 'form': []}

    for fx in fixtures_raw:
        if not fx['finished'] and not fx.get('finished_provisional'):
            continue
        if fx['team_h_score'] is None:
            continue
        hc = id2code[fx['team_h']]
        ac = id2code[fx['team_a']]
        hg = fx['team_h_score']
        ag = fx['team_a_score']

        for side, sc, oc in [(hc, hg, ag), (ac, ag, hg)]:
            table_data[side]['p'] += 1
            table_data[side]['gf'] += sc
            table_data[side]['ga'] += oc
            if sc > oc:
                table_data[side]['w'] += 1
                table_data[side]['pts'] += 3
                table_data[side]['form'].append('W')
            elif sc == oc:
                table_data[side]['d'] += 1
                table_data[side]['pts'] += 1
                table_data[side]['form'].append('D')
            else:
                table_data[side]['l'] += 1
                table_data[side]['form'].append('L')

    # Sort: points desc, then GD desc, then GF desc
    ranked = sorted(table_data.values(), key=lambda r: (-r['pts'], -(r['gf']-r['ga']), -r['gf']))
    new_table = []
    for i, r in enumerate(ranked):
        form_str = ''.join(r['form'][-5:])  # last 5
        new_table.append([i+1, r['code'], r['p'], r['w'], r['d'], r['l'], r['gf'], r['ga'], form_str])

    # --- Build scorers ---
    # Format: [playerCode, teamCode, name, goals, assists]
    scorers_list = sorted(
        [p for p in players if p['goals_scored'] > 0],
        key=lambda p: (-p['goals_scored'], -p['assists'])
    )[:30]
    new_scorers = []
    for p in scorers_list:
        new_scorers.append([p['code'], id2code[p['team']], p['web_name'], p['goals_scored'], p['assists']])

    # --- Build squads ---
    # Format: {teamCode: [[playerCode, name, posType, goals, assists, shirtNum, apiFootballId], ...]}
    new_squads = {}
    for t in teams:
        tc = t['code']
        team_players = [p for p in players if p['team'] == t['id']]
        squad = []
        for p in sorted(team_players, key=lambda x: (x['element_type'], x.get('squad_number') or 99)):
            squad.append([
                p['code'],
                p['web_name'],
                p['element_type'],  # 1=GK, 2=DEF, 3=MID, 4=FWD
                p['goals_scored'] if p['goals_scored'] > 0 else 0,
                p['assists'] if p['assists'] > 0 else 0,
                p.get('squad_number') or None,
                None  # api-football ID placeholder
            ])
        new_squads[str(tc)] = squad

    # --- Count played ---
    pl_played = sum(1 for fx in fixtures_raw if fx['finished'] or fx.get('finished_provisional'))

    # --- Update meta ---
    old_data['meta']['built'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    old_data['meta']['plPlayed'] = pl_played

    # --- Update data ---
    old_data['fixtures'] = new_fixtures
    old_data['table'] = new_table
    old_data['scorers'] = new_scorers
    old_data['squads'] = new_squads

    # --- Write back ---
    new_json = json.dumps(old_data, separators=(',', ':'), ensure_ascii=False)
    new_html = re.sub(r'var LFC_DATA=\{.*?\};', 'var LFC_DATA=' + new_json + ';', html, flags=re.DOTALL)

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print(f'Done. Played={pl_played}, Fixtures={len(new_fixtures)}, Scorers={len(new_scorers)}, Teams={len(new_squads)}')
    live = [fx for fx in fixtures_raw if fx['started'] and not fx['finished']]
    if live:
        for fx in live:
            print(f'  LIVE: {id2short[fx["team_h"]]} {fx["team_h_score"]}-{fx["team_a_score"]} {id2short[fx["team_a"]]}')

if __name__ == '__main__':
    main()
