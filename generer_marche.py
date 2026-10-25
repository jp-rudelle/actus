#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generer_marche.py : une page « prix de vente + taux de crédit » par commune (GitHub Pages, dossier docs/).

Les pages sont interactives : le lien du SMS porte le type de bien, le nombre de pièces et la surface
(?t=a&p=3&s=65) et la page trace la courbe des ventes comparables (DVF, Etalab / data.gouv.fr).

Usage :
    python generer_marche.py                 # génère docs/
    python generer_marche.py --push          # génère puis git add/commit/push
    python generer_marche.py --commune Lunel # une seule commune
    python generer_marche.py --refresh       # force le re-téléchargement DVF
Réglages (taux, communes, signature) : config.json. Aucune dépendance : Python 3.8+.
"""
import argparse
import csv
import gzip
import html
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = Path(__file__).resolve().parent
OUT = HERE / 'docs'
DVF_DIR = HERE / 'dvf'
DVF_URL = 'https://files.data.gouv.fr/geo-dvf/latest/csv/%d/departements/34.csv.gz'
MONTHS_KEPT = 36


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '') if unicodedata.category(c) != 'Mn')


def norm(s):
    return strip_accents(s).lower()


def slugify(ville):
    """Doit rester identique à slugifyVille() dans content.js."""
    return re.sub(r'(^-+|-+$)', '', re.sub(r'[^a-z0-9]+', '-', norm(ville)))


def esc(s):
    return html.escape(s or '', quote=True)


# ── DVF ──
def dvf_files(refresh):
    DVF_DIR.mkdir(exist_ok=True)
    files = []
    for year in range(date.today().year, date.today().year - 4, -1):
        p = DVF_DIR / ('34_%d.csv.gz' % year)
        if refresh or not p.exists() or time.time() - p.stat().st_mtime > 30 * 86400:
            try:
                req = urllib.request.Request(DVF_URL % year, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=180) as r:
                    p.write_bytes(r.read())
                print('DVF %d téléchargé' % year, flush=True)
            except Exception as e:
                if not p.exists():
                    continue
                print('DVF %d : téléchargement impossible (%s), copie locale utilisée' % (year, e))
        files.append(p)
    if not files:
        sys.exit('Aucun fichier DVF disponible.')
    return files


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def load_ventes(files):
    """Ventes « simples » dans l'ancien : une seule maison OU un seul appartement par mutation."""
    muts = {}
    for p in files:
        with gzip.open(p, 'rt', encoding='utf-8', newline='') as f:
            for r in csv.DictReader(f):
                if r['nature_mutation'] == 'Vente':
                    muts.setdefault(r['id_mutation'], []).append(r)
    ventes = []
    for rows in muts.values():
        mais = [r for r in rows if r['type_local'] == 'Maison']
        apps = [r for r in rows if r['type_local'] == 'Appartement']
        if (mais and apps) or len(mais) + len(apps) != 1:
            continue
        m = (mais or apps)[0]
        kind = 'm' if mais else 'a'
        prix, surf = num(m['valeur_fonciere']), num(m['surface_reelle_bati'])
        try:
            d = date.fromisoformat(m['date_mutation'])
        except ValueError:
            continue
        seen, terrain = set(), 0.0
        for r in rows:
            key = (r['id_parcelle'], r['code_nature_culture'], r['surface_terrain'])
            if key not in seen:
                seen.add(key)
                terrain += num(r['surface_terrain'])
        if kind == 'm':
            if not (30 <= surf <= 500) or terrain > 5000 or prix < 30000 or not (800 <= prix / max(surf, 1) <= 9000):
                continue
        else:
            if not (9 <= surf <= 250) or prix < 20000 or not (1000 <= prix / max(surf, 1) <= 12000):
                continue
        ventes.append({'slug': slugify(m['nom_commune']), 'kind': kind, 'date': d, 'surf': surf,
                       'prix': prix, 'pieces': int(num(m['nombre_pieces_principales']))})
    return ventes


# ── Rendu ──
def taux_html(cfg, now_dt):
    t = cfg['taux']
    age = (now_dt - datetime.fromisoformat(t['date']).replace(tzinfo=timezone.utc)).days
    if age > t.get('max_age_jours', 40):
        return ''
    d = datetime.fromisoformat(t['date']).strftime('%d/%m/%Y')
    u = t.get('url') or ''
    src = ('<a href="%s">%s</a>' % (esc(u), esc(t['source']))) if re.match(r'https?://', u) else esc(t['source'])
    return ('<h2>Crédit immobilier</h2><div class="card"><h3>%s</h3><p>%s</p><p class="meta">Chiffres relevés le %s · Source : %s</p></div>'
            % (esc(t['titre']), esc(t['texte']), d, src))


def agent_html(cfg):
    a = cfg['agent']
    tel = re.sub(r'\s', '', a['telephone'])
    s = ('<strong>%s</strong><br>%s<br>%s<br><a class="tel" href="tel:%s">📞 %s</a>'
         % (esc(a['nom']), esc(a['titre']), esc(a['secteur']), esc(tel), esc(a['telephone'])))
    if a.get('mention'):
        s += '<p class="legal">%s</p>' % esc(a['mention'])
    return s


def render_page(ville, data, end_mi, cfg, now_dt, template):
    payload = json.dumps(data, separators=(',', ':')).replace('</', '<\\/')
    return (template.replace('__VILLEJS__', json.dumps(ville, ensure_ascii=False).replace('</', '<\\/'))
            .replace('__VILLE__', esc(ville)).replace('__DATA__', payload).replace('__END__', str(end_mi))
            .replace('__TAUX__', taux_html(cfg, now_dt)).replace('__AGENT__', agent_html(cfg))
            .replace('__DATE__', now_dt.strftime('%d/%m/%Y')))


def stats_for(data, end_mi):
    """Petit fichier stats.json par commune (utilisé par l'extension pour l'email)."""
    import statistics

    def seg(rows):
        cur = [r for r in rows if r[0] > end_mi - 12]
        prev = [r for r in rows if end_mi - 24 < r[0] <= end_mi - 12]
        if len(cur) < 5:
            return None
        pm = [r[2] / r[1] for r in cur]
        o = {'n': len(cur), 'pm2': round(statistics.median(pm)), 'prix': round(statistics.median(r[2] for r in cur))}
        if len(prev) >= 12 and len(cur) >= 12:
            o['evol'] = round((statistics.median(pm) / statistics.median(r[2] / r[1] for r in prev) - 1) * 100, 1)
        return o

    out = {'fin_mi': end_mi, 'm': seg(data['m']), 'a': seg(data['a'])}
    for p in range(1, 6):
        out['a%d' % p] = seg([r for r in data['a'] if (r[3] >= 5 if p == 5 else r[3] == p)])
    return out


def find_git():
    return shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--commune')
    ap.add_argument('--refresh', action='store_true')
    args = ap.parse_args()

    cfg = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))
    template = (HERE / 'page_template.html').read_text(encoding='utf-8')
    now_dt = datetime.now(timezone.utc)
    communes = [re.sub(r'\s+', ' ', c).strip() for c in cfg['communes']]
    if args.commune:
        communes = [c for c in communes if slugify(c) == slugify(args.commune)]

    ventes = load_ventes(dvf_files(args.refresh))
    end = max(v['date'] for v in ventes)
    end_mi = (end.year - 2000) * 12 + end.month - 1
    print('%d ventes retenues, dernière date DVF : %s' % (len(ventes), end), flush=True)
    par = {}
    for v in ventes:
        mi = (v['date'].year - 2000) * 12 + v['date'].month - 1
        if mi <= end_mi - MONTHS_KEPT:
            continue
        par.setdefault(v['slug'], {'m': [], 'a': []})[v['kind']].append(
            [mi, round(v['surf']), round(v['prix']), v['pieces']])

    OUT.mkdir(exist_ok=True)
    (OUT / '.nojekyll').write_text('', encoding='utf-8')
    for v in communes:
        data = par.get(slugify(v), {'m': [], 'a': []})
        d = OUT / slugify(v)
        d.mkdir(exist_ok=True)
        (d / 'index.html').write_text(render_page(v, data, end_mi, cfg, now_dt, template), encoding='utf-8')
        (d / 'stats.json').write_text(json.dumps(stats_for(data, end_mi), separators=(',', ':')), encoding='utf-8')
        print('%-28s maisons %4d · appartements %4d' % (v, len(data['m']), len(data['a'])), flush=True)
    (OUT / 'index.html').write_text('<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>Marché</title>', encoding='utf-8')

    print('\nÀ coller dans content.js :')
    print('const ACTUS_COMMUNES = ' + json.dumps(sorted(set(slugify(c) for c in cfg['communes'])), ensure_ascii=False) + ';')
    t_age = (now_dt - datetime.fromisoformat(cfg['taux']['date']).replace(tzinfo=timezone.utc)).days
    if t_age > cfg['taux'].get('max_age_jours', 40):
        print('\n⚠ Le taux de config.json date de %d jours : il est masqué des pages. Mets-le à jour.' % t_age)
    if not cfg['agent'].get('mention'):
        print('⚠ config.json > agent > mention est vide (EI / RSAC à ajouter si tu veux l\'afficher).')

    if args.push:
        g = find_git()
        for cmd in ([g, 'add', '-A'], [g, 'commit', '-m', 'Marché %s' % now_dt.strftime('%Y-%m-%d')], [g, 'push']):
            r = subprocess.run(cmd, cwd=HERE)
            if r.returncode != 0 and cmd[1] != 'commit':
                sys.exit('Échec : ' + ' '.join(cmd))


if __name__ == '__main__':
    main()
