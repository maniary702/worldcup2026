#!/usr/bin/env python3
"""
Refresh the Premier League page's baked data + the news sidecar.
Canonical copy lives in lfc_deploy; deploy_lfc.ps1 copies it into the repo so a
"git reset --hard origin/main" can never revert it mid-deploy.

Owns:  prem.html -> var LFC_DATA={...}   and   prem_news.json

Two rules this file exists to enforce:
 1. Never blank squad[i][6] (the API-Football headshot id baked by build_lfc.py).
 2. Never reorder the table row. The page reads
        [pos, code, P, W, D, L, GD, Pts, form]
    An earlier version wrote GF and GA into the GD and Pts slots, which is why
    every club showed 0 points on the live site.

Live scores, the live table, line-ups, match stats and odds come from the public
ESPN feed in the browser. This bake is the offline floor, not the live path.
"""
import json, urllib.request, re, sys, os, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

FPL_BOOT = 'https://fantasy.premierleague.com/api/bootstrap-static/'
FPL_FX   = 'https://fantasy.premierleague.com/api/fixtures/'
UA       = {'User-Agent': 'Mozilla/5.0 (compatible; prem-page-refresh/2.0)'}


def fetch_json(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read())


def status_of(fx):
    if fx.get('finished') or fx.get('finished_provisional'):
        return 'C'
    if fx.get('started'):
        mins = fx.get('minutes') or 0
        if mins and mins <= 45:
            return '1H'
        if mins and mins > 45:
            return '2H'
        return 'LIVE'
    return 'U'


def build_table(teams, fixtures_raw, id2code):
    rows = {t['code']: {'code': t['code'], 'p': 0, 'w': 0, 'd': 0, 'l': 0,
                        'gf': 0, 'ga': 0, 'pts': 0, 'form': []} for t in teams}
    done = [f for f in fixtures_raw
            if (f.get('finished') or f.get('finished_provisional')) and f.get('team_h_score') is not None]
    done.sort(key=lambda f: f.get('kickoff_time') or '')
    for fx in done:
        hc, ac = id2code[fx['team_h']], id2code[fx['team_a']]
        hg, ag = int(fx['team_h_score']), int(fx['team_a_score'])
        for side, sc, oc in ((hc, hg, ag), (ac, ag, hg)):
            r = rows[side]
            r['p'] += 1
            r['gf'] += sc
            r['ga'] += oc
            if sc > oc:
                r['w'] += 1
                r['pts'] += 3
                r['form'].append('W')
            elif sc == oc:
                r['d'] += 1
                r['pts'] += 1
                r['form'].append('D')
            else:
                r['l'] += 1
                r['form'].append('L')
    ranked = sorted(rows.values(), key=lambda r: (-r['pts'], -(r['gf'] - r['ga']), -r['gf']))
    return [[i + 1, r['code'], r['p'], r['w'], r['d'], r['l'],
             r['gf'] - r['ga'], r['pts'], ''.join(r['form'][-5:])]
            for i, r in enumerate(ranked)]


def carry_af_ids(old_squads):
    keep = {}
    for squad in (old_squads or {}).values():
        for p in squad:
            if len(p) > 6 and p[6] is not None:
                keep[p[0]] = p[6]
    return keep


def load_cups(script_dir):
    """Load cup fixtures from cups.json so every refresh re-bakes them.
    The PL-page-deploy workflow pushes a local prem.html that has empty cups;
    this ensures the next auto-refresh restores cup data from the canonical file."""
    cups_path = os.path.join(script_dir, 'cups.json')
    if not os.path.exists(cups_path):
        print('  cups.json not found, skipping cups')
        return None
    with open(cups_path, 'r', encoding='utf-8') as f:
        cups = json.load(f)
    total = sum(len(g) for comp in cups.values()
                for g in comp.get('byTeam', {}).values())
    print('  cups.json loaded: %d competitions, %d fixtures' % (len(cups), total))
    return cups


def main():
    html_path = sys.argv[1] if len(sys.argv) > 1 else 'prem.html'
    print('Refreshing %s ...' % html_path)

    boot = fetch_json(FPL_BOOT)
    fixtures_raw = fetch_json(FPL_FX)
    teams, players = boot['teams'], boot['elements']
    id2code = {t['id']: t['code'] for t in teams}
    id2short = {t['id']: t['short_name'] for t in teams}

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'var LFC_DATA=(\{.*?\});', html, re.DOTALL)
    if not m:
        print('ERROR: LFC_DATA not found in %s' % html_path)
        sys.exit(1)
    data = json.loads(m.group(1))

    af_ids = carry_af_ids(data.get('squads'))
    print('  carried %d API-Football headshot ids' % len(af_ids))

    fixtures = []
    for fx in fixtures_raw:
        st = status_of(fx)
        hs, as_ = fx.get('team_h_score'), fx.get('team_a_score')
        if st == 'U':
            hs = as_ = None
        fixtures.append([fx.get('event') or 0, fx.get('kickoff_time') or '',
                         id2code[fx['team_h']], id2code[fx['team_a']],
                         None if hs is None else int(hs),
                         None if as_ is None else int(as_), st])
    fixtures.sort(key=lambda r: (r[0] or 99, r[1] or ''))

    table = build_table(teams, fixtures_raw, id2code)

    scorers = [[p['code'], id2code[p['team']], p['web_name'], p['goals_scored'], p['assists']]
               for p in sorted([p for p in players if p['goals_scored'] > 0],
                               key=lambda p: (-p['goals_scored'], -p['assists']))[:30]]
    assisters = [[p['code'], id2code[p['team']], p['web_name'], p['assists'], p['goals_scored']]
                 for p in sorted([p for p in players if p['assists'] > 0],
                                 key=lambda p: (-p['assists'], -p['goals_scored']))[:30]]

    squads = {}
    for t in teams:
        rows = []
        for p in sorted([p for p in players if p['team'] == t['id']],
                        key=lambda x: (x['element_type'], x.get('squad_number') or 99)):
            rows.append([p['code'], p['web_name'], p['element_type'],
                         p.get('goals_scored') or 0, p.get('assists') or 0,
                         p.get('squad_number') or None,
                         af_ids.get(p['code'])])
        squads[str(t['code'])] = rows

    played = sum(1 for fx in fixtures_raw if fx.get('finished') or fx.get('finished_provisional'))
    data.setdefault('meta', {})
    data['meta']['built'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    data['meta']['plPlayed'] = played
    data['meta']['plTotal'] = len(fixtures)
    data['fixtures'] = fixtures
    data['table'] = table
    data['scorers'] = scorers
    data['assisters'] = assisters
    data['squads'] = squads

    # Always re-inject cups from cups.json so PL-page-deploy wipes get restored
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cups = load_cups(script_dir)
    if cups is not None:
        data['cups'] = cups

    blob = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
    new_html = re.sub(r'var LFC_DATA=\{.*?\};', lambda _: 'var LFC_DATA=' + blob + ';', html, flags=re.DOTALL)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)

    print('  played=%d fixtures=%d table=%d scorers=%d assisters=%d squads=%d'
          % (played, len(fixtures), len(table), len(scorers), len(assisters), len(squads)))
    if table:
        top = table[0]
        short = next((id2short[t['id']] for t in teams if t['code'] == top[1]), '?')
        print('  table top: %s %d pts (GD %+d)  schema [pos,code,P,W,D,L,GD,Pts,form]'
              % (short, top[7], top[6]))
    for fx in [f for f in fixtures_raw if f.get('started') and not f.get('finished')]:
        print('  LIVE: %s %s-%s %s' % (id2short[fx['team_h']], fx['team_h_score'],
                                       fx['team_a_score'], id2short[fx['team_a']]))


# ============================================================
# NEWS  (BBC Sport, The Guardian, Sky Sports)
# ============================================================
RSS_FEEDS = [
    ('https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml', 'BBC Sport', 'league'),
    ('https://feeds.bbci.co.uk/sport/football/gossip/rss.xml', 'BBC Sport', 'gossip'),
    ('https://www.theguardian.com/football/premierleague/rss', 'The Guardian', 'league'),
    ('https://www.skysports.com/rss/11660', 'Sky Sports', 'league'),
]
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
_KW_SORTED = sorted(TEAM_KW.keys(), key=len, reverse=True)


def fetch_xml(url):
    try:
        return ET.fromstring(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read())
    except Exception as e:
        print('  RSS fetch failed: %s - %s' % (url, e))
        return None


def parse_rss_date(s):
    if not s:
        return ''
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return s


def item_image(item):
    ns = {'media': 'http://search.yahoo.com/mrss/'}
    thumb = item.find('media:thumbnail', ns)
    if thumb is not None and thumb.get('url'):
        return thumb.get('url')
    enc = item.find('enclosure')
    if enc is not None:
        if 'image' in (enc.get('type') or ''):
            return enc.get('url', '')
        if (enc.get('url', '').split('?')[0].split('.')[-1] or '').lower() in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            return enc.get('url', '')
    mc = item.find('media:content', ns)
    if mc is not None and mc.get('url'):
        return mc.get('url')
    return ''


def match_teams(text):
    codes, t = set(), (text or '').lower()
    for kw in _KW_SORTED:
        if kw.lower() in t:
            codes.add(TEAM_KW[kw])
    return codes


def build_news(output_path):
    print('Refreshing news ...')
    team_items, league_items, gossip_items = {}, [], []
    seen = set()
    for url, source, category in RSS_FEEDS:
        root = fetch_xml(url)
        if root is None:
            continue
        items = root.findall('.//item')
        print('  %s (%s): %d items' % (source, category, len(items)))
        for it in items:
            title = (it.findtext('title') or '').strip()
            link = (it.findtext('link') or '').strip()
            if not title or link in seen:
                continue
            seen.add(link)
            pub = parse_rss_date(it.findtext('pubDate'))
            desc = re.sub(r'<[^>]+>', '', (it.findtext('description') or '')).strip()
            if len(desc) > 200:
                desc = desc[:197] + '...'
            row = [title, link, pub, source, desc]
            img = item_image(it)
            if img:
                row.append(img)
            (gossip_items if category == 'gossip' else league_items).append(row)
            for c in match_teams(title + ' ' + desc):
                team_items.setdefault(str(c), []).append(row)

    key = lambda r: (r[2] if len(r) > 2 and r[2] else '')
    league_items.sort(key=key, reverse=True)
    gossip_items.sort(key=key, reverse=True)
    for c in team_items:
        team_items[c].sort(key=key, reverse=True)
        team_items[c] = team_items[c][:15]

    news = {'meta': {'built': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                     'sources': ['BBC Sport', 'Sky Sports', 'The Guardian']},
            'team': team_items,
            'league': league_items[:25],
            'gossip': gossip_items[:20]}
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(news, f, separators=(',', ':'), ensure_ascii=False)
    total = sum(len(v) for v in team_items.values())
    print('  news written: %d league, %d gossip, %d team-tagged across %d clubs'
          % (len(news['league']), len(news['gossip']), total, len(team_items)))


def stage_for_commit(path):
    """The CI workflow commits with a bare `git commit`, but only runs
    `git add prem.html`. Staging the news file here makes it ride along."""
    try:
        import subprocess
        subprocess.run(['git', 'add', path], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        pass


if __name__ == '__main__':
    main()
    page = sys.argv[1] if len(sys.argv) > 1 else 'prem.html'
    news_path = os.path.join(os.path.dirname(page) or '.', 'prem_news.json')
    build_news(news_path)
    stage_for_commit(news_path)
