#!/usr/bin/env python3
"""
Refresh prem.html with live FPL data + prem_news.json with RSS headlines.
Reads prem.html, updates LFC_DATA (fixtures, table, scorers, squads, meta), writes back.
Fetches BBC Sport, Guardian, Sky Sports RSS and writes prem_news.json.
Run on schedule via GitHub Actions or manually.
"""
import json, urllib.request, re, sys, os, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

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
            status = 'C'
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

    # --- Build cups ---
    old_data['cups'] = build_cups(id2code)

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

# ============================================================
# CUPS: build cup fixture data for baking into LFC_DATA.cups
# ============================================================
# Cup fixture format: [koISO, homeName, homeBadge, awayName, awayBadge, hs, as, status, round]
# PL badge URL pattern
def pl_badge(code):
    return f'https://resources.premierleague.com/premierleague/badges/50/t{code}.png'

# Map of PL team names -> FPL code (for cup fixtures where we reference teams by name)
PL_NAME2CODE = {
    'Arsenal': 3, 'Aston Villa': 7, 'Bournemouth': 91, 'Brentford': 94,
    'Brighton': 36, 'Chelsea': 8, 'Coventry City': 9, 'Crystal Palace': 31,
    'Everton': 11, 'Fulham': 54, 'Hull City': 88, 'Ipswich Town': 40,
    'Leeds United': 2, 'Liverpool': 14, 'Man City': 43, 'Man Utd': 1,
    'Newcastle': 4, 'Nott\'m Forest': 17, 'Sunderland': 56, 'Tottenham': 6,
}

# PL code -> short display name (for cups)
PL_CODE2NAME = {v: k for k, v in PL_NAME2CODE.items()}

def cup_team(name, code=None):
    """Return (displayName, badgeURL) for a cup team."""
    if code and code in PL_CODE2NAME:
        return PL_CODE2NAME[code], pl_badge(code)
    if name in PL_NAME2CODE:
        c = PL_NAME2CODE[name]
        return name, pl_badge(c)
    return name, ''  # non-PL team, no badge

def cup_fixture(home, away, ko='', hs=None, as_=None, status='U', rnd=''):
    """Build a cup fixture row."""
    hn, hb = cup_team(home) if isinstance(home, str) else cup_team(PL_CODE2NAME.get(home,''), home)
    an, ab = cup_team(away) if isinstance(away, str) else cup_team(PL_CODE2NAME.get(away,''), away)
    return [ko, hn, hb, an, ab, hs, as_, status, rnd]

def build_cups(id2code):
    """Build cups dict with current known fixtures."""
    cups = {}

    # --- Champions League 2026-27: league-phase draw 27 Aug 2026 ---
    # Matchday dates: MD1 Sep 8-10, MD2 Oct 13-14, MD3 Oct 20-21, MD4 Nov 3-4,
    #                 MD5 Nov 24-25, MD6 Dec 8-9, MD7 Jan 19-20, MD8 Jan 27
    # Exact kickoff times TBC - using midpoint dates. Client-side fetch from
    # football-data.org API will override with precise times once available.
    md_dates = {
        1: '2026-09-09T20:00:00Z', 2: '2026-10-13T20:00:00Z',
        3: '2026-10-20T20:00:00Z', 4: '2026-11-03T20:00:00Z',
        5: '2026-11-24T20:00:00Z', 6: '2026-12-08T20:00:00Z',
        7: '2027-01-19T20:00:00Z', 8: '2027-01-27T20:00:00Z',
    }
    # English clubs: (code, home_opponents, away_opponents)
    # Each club plays 4 home + 4 away = 8 matches, alternating H/A across matchdays
    cl_english = [
        (3, ['Real Madrid', 'Borussia Dortmund', 'Lille', 'Sabah FK'],
            ['Bayern Munich', 'Real Betis', 'Napoli', 'Slavia Prague']),
        (14, ['Atletico Madrid', 'Porto', 'Villarreal', 'Lens'],
             ['Inter', 'Club Brugge', 'Fenerbahce', 'LASK']),
        (43, ['PSG', 'Sporting CP', 'Napoli', 'AEK Athens'],
             ['Barcelona', 'Porto', 'RB Leipzig', 'Lens']),
        (1,  ['Bayern Munich', 'Roma', 'RB Leipzig', 'Sabah FK'],
             ['Atletico Madrid', 'Sporting CP', 'Villarreal', 'Como']),
        (7,  ['PSG', 'Borussia Dortmund', 'Fenerbahce', 'Viking'],
             ['Barcelona', 'Club Brugge', 'Galatasaray', 'Slavia Prague']),
    ]
    ucl_byteam = {}
    for code, home_opps, away_opps in cl_english:
        fixtures = []
        for i in range(4):
            md_h = i * 2 + 1  # odd matchdays home
            md_a = i * 2 + 2  # even matchdays away
            team_name = PL_CODE2NAME.get(code, str(code))
            # Home fixture
            fixtures.append([
                md_dates[md_h], team_name, pl_badge(code),
                home_opps[i], '',
                None, None, 'U', f'Matchday {md_h}'
            ])
            # Away fixture
            fixtures.append([
                md_dates[md_a], away_opps[i], '',
                team_name, pl_badge(code),
                None, None, 'U', f'Matchday {md_a}'
            ])
        fixtures.sort(key=lambda r: r[0])
        ucl_byteam[str(code)] = fixtures

    total_ucl = sum(len(v) for v in ucl_byteam.values())
    print(f'  Cups: UCL = {len(cl_english)} English clubs, {total_ucl} fixtures')

    cups['ucl'] = {
        'name': 'Champions League',
        'note': 'This club is not in the Champions League this season.',
        'byTeam': ucl_byteam
    }
    # --- Europa League 2026-27: league-phase draw 28 Aug 2026 ---
    uel_dates = {
        1: '2026-09-17T20:00:00Z', 2: '2026-10-15T20:00:00Z',
        3: '2026-10-22T20:00:00Z', 4: '2026-11-05T20:00:00Z',
        5: '2026-11-26T20:00:00Z', 6: '2026-12-10T20:00:00Z',
        7: '2027-01-21T20:00:00Z', 8: '2027-01-28T20:00:00Z',
    }
    # English clubs: (code, home_opponents[MD1,3,5,7], away_opponents[MD2,4,6,8])
    uel_english = [
        (91, ['AC Milan', 'Viktoria Plzen', 'Sturm Graz', 'Hapoel Beer Sheva'],
             ['Real Sociedad', 'Sparta Prague', 'Celta Vigo', 'Lillestrom']),
        (31, ['Real Sociedad', 'Sparta Prague', 'Lech Poznan', 'Hoffenheim'],
             ['Lyon', 'Red Bull Salzburg', 'Jagiellonia', 'Besiktas']),
        (56, ['AZ Alkmaar', 'Dinamo Zagreb', 'Jagiellonia', 'Levski Sofia'],
             ['AC Milan', 'Anderlecht', 'Lech Poznan', 'Torreense']),
    ]
    uel_byteam = {}
    for code, home_opps, away_opps in uel_english:
        fixtures = []
        for i in range(4):
            md_h = i * 2 + 1
            md_a = i * 2 + 2
            team_name = PL_CODE2NAME.get(code, str(code))
            fixtures.append([
                uel_dates[md_h], team_name, pl_badge(code),
                home_opps[i], '',
                None, None, 'U', f'Matchday {md_h}'
            ])
            fixtures.append([
                uel_dates[md_a], away_opps[i], '',
                team_name, pl_badge(code),
                None, None, 'U', f'Matchday {md_a}'
            ])
        fixtures.sort(key=lambda r: r[0])
        uel_byteam[str(code)] = fixtures

    total_uel = sum(len(v) for v in uel_byteam.values())
    print(f'  Cups: UEL = {len(uel_english)} English clubs, {total_uel} fixtures')

    cups['uel'] = {
        'name': 'Europa League',
        'note': 'This club is not in the Europa League this season.',
        'byTeam': uel_byteam
    }
    cups['uecl'] = {
        'name': 'Conference League',
        'note': 'Conference League coverage requires a paid API tier - fixtures will be added manually after each draw.',
        'byTeam': {}
    }

    # --- EFL Cup 2026-27: R3 draw 26 Aug 2026 ---
    # Games played w/c 7 Sep and 14 Sep 2026. Exact dates TBC.
    # Update this list after each round's draw/results.
    efl_r3 = [
        # (home, away, date, home_score, away_score, status)
        ('Crystal Palace', 'Middlesbrough', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Man Utd', 'Brighton', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Man City', 'Norwich City', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Sunderland', 'Hull City', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Ipswich Town', 'Arsenal', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Coventry City', 'Aston Villa', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Bournemouth', 'Lincoln City', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Liverpool', 'Tottenham', '2026-09-10T20:00:00Z', None, None, 'U'),
        ('Chelsea', 'Leeds United', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Millwall', 'Newcastle', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Everton', 'Wolverhampton', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Reading', 'Brentford', '2026-09-10T19:45:00Z', None, None, 'U'),
        ('Fulham', 'West Ham United', '2026-09-10T19:45:00Z', None, None, 'U'),
    ]

    efl_byteam = {}
    for home, away, ko, hs, as_, st in efl_r3:
        row = cup_fixture(home, away, ko, hs, as_, st, 'Round 3')
        # File under PL teams only
        hc = PL_NAME2CODE.get(home)
        ac = PL_NAME2CODE.get(away)
        if hc:
            efl_byteam.setdefault(str(hc), []).append(row)
        if ac:
            efl_byteam.setdefault(str(ac), []).append(row)

    cups['efl'] = {
        'name': 'EFL Cup',
        'note': 'No EFL Cup fixtures for this club yet.',
        'byTeam': efl_byteam
    }

    # --- FA Cup: PL clubs enter R3 in January 2027 ---
    cups['fac'] = {
        'name': 'FA Cup',
        'note': 'Premier League clubs enter the FA Cup at Round 3 in early January 2027.',
        'byTeam': {}
    }

    total = sum(len(v) for v in efl_byteam.values())
    print(f'  Cups: EFL Cup R3 = {len(efl_r3)} ties, {total} team entries')
    return cups


# ============================================================
# NEWS: fetch RSS feeds and write prem_news.json
# ============================================================
RSS_FEEDS = [
    ('https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml', 'BBC Sport', 'league'),
    ('https://feeds.bbci.co.uk/sport/football/gossip/rss.xml', 'BBC Sport', 'gossip'),
    ('https://www.theguardian.com/football/premierleague/rss', 'The Guardian', 'league'),
    ('https://www.skysports.com/rss/11660', 'Sky Sports', 'league'),
]

# Keywords -> FPL team code. Order matters: longer/more specific first.
TEAM_KW = {
    'Manchester United': 1, 'Man United': 1, 'Man Utd': 1, 'MUFC': 1,
    'Leeds United': 2, 'Leeds': 2,
    'Arsenal': 3, 'Gunners': 3,
    'Newcastle United': 4, 'Newcastle': 4, 'Magpies': 4,
    'Tottenham': 6, 'Spurs': 6,
    'Aston Villa': 7, 'Villa': 7,
    'Chelsea': 8,
    'Coventry City': 9, 'Coventry': 9,
    'Everton': 11, 'Toffees': 11,
    'Liverpool': 14, 'Reds': 14,
    "Nott'm Forest": 17, 'Nottingham Forest': 17, "Nott'ham Forest": 17, 'Forest': 17,
    'Crystal Palace': 31, 'Palace': 31,
    'Brighton': 36, 'Seagulls': 36,
    'Ipswich Town': 40, 'Ipswich': 40,
    'Man City': 43, 'Manchester City': 43, 'MCFC': 43,
    'Fulham': 54, 'Cottagers': 54,
    'Sunderland': 56,
    'Hull City': 88, 'Hull': 88,
    'Bournemouth': 91, 'AFC Bournemouth': 91, 'Cherries': 91,
    'Brentford': 94, 'Bees': 94,
}
# Sort by length desc so "Manchester United" matches before "Manchester"
_KW_SORTED = sorted(TEAM_KW.keys(), key=len, reverse=True)

def fetch_xml(url):
    """Fetch RSS XML, return parsed root or None."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = urllib.request.urlopen(req, timeout=20).read()
        return ET.fromstring(data)
    except Exception as e:
        print(f'  RSS fetch failed: {url} - {e}')
        return None

def parse_rss_date(s):
    """Parse RFC-2822 date to ISO string, or return empty."""
    if not s:
        return ''
    try:
        dt = parsedate_to_datetime(s)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return s

def item_image(item):
    """Extract image URL from RSS item (media:thumbnail, enclosure, or media:content)."""
    ns = {'media': 'http://search.yahoo.com/mrss/'}
    # media:thumbnail
    thumb = item.find('media:thumbnail', ns)
    if thumb is not None:
        return thumb.get('url', '')
    # enclosure
    enc = item.find('enclosure')
    if enc is not None and 'image' in (enc.get('type', '') or ''):
        return enc.get('url', '')
    if enc is not None and enc.get('url', '').split('?')[0].split('.')[-1] in ('jpg','jpeg','png','webp','gif'):
        return enc.get('url', '')
    # media:content
    mc = item.find('media:content', ns)
    if mc is not None:
        return mc.get('url', '')
    return ''

def match_teams(text):
    """Return set of FPL team codes mentioned in text."""
    codes = set()
    t = text or ''
    for kw in _KW_SORTED:
        if kw.lower() in t.lower():
            codes.add(TEAM_KW[kw])
    return codes

def build_news(output_path):
    """Fetch all RSS feeds and write prem_news.json."""
    print('Refreshing news...')
    team_items = {}   # code -> list of [title, link, iso, source, desc, img]
    league_items = []
    gossip_items = []

    for url, source, category in RSS_FEEDS:
        root = fetch_xml(url)
        if root is None:
            continue
        items = root.findall('.//item')
        print(f'  {source} ({category}): {len(items)} items')
        for it in items:
            title = (it.findtext('title') or '').strip()
            link = (it.findtext('link') or '').strip()
            pub = parse_rss_date(it.findtext('pubDate'))
            desc = (it.findtext('description') or '').strip()
            # Strip HTML from description
            desc = re.sub(r'<[^>]+>', '', desc).strip()
            if len(desc) > 200:
                desc = desc[:197] + '...'
            img = item_image(it)
            row = [title, link, pub, source, desc]
            if img:
                row.append(img)

            if category == 'gossip':
                gossip_items.append(row)
            else:
                league_items.append(row)

            # Also file under matching teams
            codes = match_teams(title + ' ' + desc)
            for c in codes:
                team_items.setdefault(str(c), []).append(row)

    # Sort each list by date descending
    def sort_key(r):
        return r[2] if len(r) > 2 and r[2] else ''
    league_items.sort(key=sort_key, reverse=True)
    gossip_items.sort(key=sort_key, reverse=True)
    for c in team_items:
        team_items[c].sort(key=sort_key, reverse=True)
        team_items[c] = team_items[c][:15]  # cap per team

    league_items = league_items[:25]
    gossip_items = gossip_items[:20]

    news = {
        'meta': {
            'built': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'sources': ['BBC Sport', 'Sky Sports', 'The Guardian']
        },
        'team': team_items,
        'league': league_items,
        'gossip': gossip_items
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(news, f, separators=(',', ':'), ensure_ascii=False)

    total_team = sum(len(v) for v in team_items.values())
    print(f'  News written: {len(league_items)} league, {len(gossip_items)} gossip, {total_team} team-tagged across {len(team_items)} clubs')


if __name__ == '__main__':
    main()
    # Also refresh news sidecar
    news_path = os.path.join(os.path.dirname(sys.argv[1] if len(sys.argv) > 1 else 'prem.html'), 'prem_news.json')
    build_news(news_path)
