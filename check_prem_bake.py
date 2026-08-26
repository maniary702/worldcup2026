#!/usr/bin/env python3
"""Refuse to publish a broken bake. Run after refresh_prem.py, before committing."""
import json, re, sys

html = open('prem.html', encoding='utf-8').read()
m = re.search(r'var LFC_DATA=(\{.*?\});', html, re.DOTALL)
if not m:
    sys.exit('FAIL: LFC_DATA missing from prem.html')
d = json.loads(m.group(1))

if len(d.get('teams', {})) != 20:
    sys.exit('FAIL: expected 20 teams, got %d' % len(d.get('teams', {})))
if len(d.get('fixtures', [])) < 300:
    sys.exit('FAIL: only %d fixtures' % len(d.get('fixtures', [])))

table = d.get('table', [])
if len(table) != 20:
    sys.exit('FAIL: table has %d rows, expected 20' % len(table))

# row = [pos, code, P, W, D, L, GD, Pts, form]
# A club can never take more than 3 points per game, and points must equal 3W+D.
for r in table:
    pos, code, p, w, dr, l, gd, pts, form = r[:9]
    if w + dr + l != p:
        sys.exit('FAIL: W/D/L do not add up to played: %r' % (r,))
    if pts != w * 3 + dr:
        sys.exit('FAIL: points do not match W/D - column order is wrong: %r' % (r,))
    if pts > p * 3:
        sys.exit('FAIL: impossible points total: %r' % (r,))

blanked = sum(1 for sq in d.get('squads', {}).values() for pl in sq if len(pl) < 7)
if blanked:
    sys.exit('FAIL: %d squad rows lost the headshot-id column' % blanked)

news = json.load(open('prem_news.json', encoding='utf-8'))
if not news.get('league'):
    sys.exit('FAIL: news feed came back empty')

print('OK: %d teams, %d fixtures, %d table rows, %d league headlines, top = %d pts'
      % (len(d['teams']), len(d['fixtures']), len(table), len(news['league']), table[0][7]))
