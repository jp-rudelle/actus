#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generer_marche.py : une page « marché immobilier + taux de crédit » par commune (site statique GitHub Pages).

Source des ventes : DVF (Demandes de valeurs foncières, Etalab / data.gouv.fr), données publiques.
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
import statistics
import subprocess
import sys
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = Path(__file__).resolve().parent
OUT = HERE / 'docs'
DVF_DIR = HERE / 'dvf'
DVF_URL = 'https://files.data.gouv.fr/geo-dvf/latest/csv/%d/departements/34.csv.gz'
MIN_VENTES = 8      # en dessous : on élargit à 24 mois
MIN_AFFICHAGE = 5   # en dessous (même à 24 mois) : pas de chiffres
MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
        'septembre', 'octobre', 'novembre', 'décembre']


# ── Utilitaires ──
def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '') if unicodedata.category(c) != 'Mn')


def norm(s):
    return strip_accents(s).lower()


def slugify(ville):
    """Doit rester identique à slugifyVille() dans content.js."""
    return re.sub(r'(^-+|-+$)', '', re.sub(r'[^a-z0-9]+', '-', norm(ville)))


def esc(s):
    return html.escape(s or '', quote=True)


def euros(x, arrondi=1000):
    x = int(round(x / arrondi) * arrondi)
    return '{:,}'.format(x).replace(',', '\u202f') + '\u00a0€'


def mois_annee(d):
    return '%s %d' % (MOIS[d.month - 1], d.year)


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


def load_ventes(files):
    """Ventes de maisons « simples » : une seule maison dans la mutation, pas d'appartement, terrain < 5000 m²."""
    muts = {}
    for p in files:
        with gzip.open(p, 'rt', encoding='utf-8', newline='') as f:
            for r in csv.DictReader(f):
                if r['nature_mutation'] == 'Vente':
                    muts.setdefault(r['id_mutation'], []).append(r)
    ventes = []
    for rows in muts.values():
        if any(r['type_local'] == 'Appartement' for r in rows):
            continue
        maisons = [r for r in rows if r['type_local'] == 'Maison']
        if len(maisons) != 1:
            continue
        m = maisons[0]
        try:
            prix = float(m['valeur_fonciere'])
            surf = float(m['surface_reelle_bati'])
            d = date.fromisoformat(m['date_mutation'])
        except (ValueError, TypeError):
            continue
        seen, terrain = set(), 0.0
        for r in rows:
            key = (r['id_parcelle'], r['code_nature_culture'], r['surface_terrain'])
            if key in seen:
                continue
            seen.add(key)
            try:
                terrain += float(r['surface_terrain'] or 0)
            except ValueError:
                pass
        if not (30 <= surf <= 500) or terrain > 5000 or prix < 30000:
            continue
        pm2 = prix / surf
        if not (800 <= pm2 <= 9000):
            continue
        ventes.append({'slug': slugify(m['nom_commune']), 'date': d, 'prix': prix, 'surf': surf,
                       'terrain': terrain, 'pm2': pm2})
    return ventes


def stats(vs):
    pm2 = [v['pm2'] for v in vs]
    q = statistics.quantiles(pm2, n=4, method='inclusive') if len(pm2) >= 2 else [pm2[0]] * 3
    return {'n': len(vs), 'prix': statistics.median(v['prix'] for v in vs),
            'pm2': statistics.median(pm2), 'p25': q[0], 'p75': q[2],
            'surf': statistics.median(v['surf'] for v in vs)}


def analyse(vs_commune, end):
    cur12 = [v for v in vs_commune if end - timedelta(days=365) < v['date'] <= end]
    prev12 = [v for v in vs_commune if end - timedelta(days=730) < v['date'] <= end - timedelta(days=365)]
    if len(cur12) >= MIN_VENTES:
        win, debut = cur12, end - timedelta(days=364)
    else:
        win = [v for v in vs_commune if end - timedelta(days=730) < v['date'] <= end]
        debut = end - timedelta(days=729)
    if len(win) < MIN_AFFICHAGE:
        return None
    s = stats(win)
    s['debut'], s['fin'] = debut, end
    s['evol'] = None
    if len(cur12) >= 12 and len(prev12) >= 12:
        s['evol'] = statistics.median(v['pm2'] for v in cur12) / statistics.median(v['pm2'] for v in prev12) - 1
    s['recentes'] = sorted(win, key=lambda v: v['date'], reverse=True)[:3]
    return s


# ── Rendu ──
CSS = """
:root{--v:#2E7D32;--t:#1f2a24;--m:#667;--b:#e4e8e5;--bg:#f6f8f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
main{max-width:640px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:24px;line-height:1.25;margin:8px 0 4px}h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--v);margin:28px 0 10px}
.sub{color:var(--m);margin:0 0 8px;font-size:14px}
.card{background:#fff;border:1px solid var(--b);border-radius:12px;padding:14px 16px;margin:0 0 10px}
.card h3{font-size:16px;margin:0 0 4px;line-height:1.35}
.card p{margin:4px 0;font-size:14.5px}.meta{color:var(--m);font-size:12.5px}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 10px}
.kpi{flex:1 1 150px;background:#fff;border:1px solid var(--b);border-radius:12px;padding:12px 14px}
.kpi b{display:block;font-size:22px;line-height:1.2}.kpi span{color:var(--m);font-size:12.5px}
ul.v{list-style:none;padding:0;margin:6px 0}ul.v li{padding:6px 0;border-top:1px solid var(--b);font-size:14.5px}
.agent{margin-top:30px;padding-top:16px;border-top:1px solid var(--b);font-size:14.5px}
.agent a.tel{display:inline-block;margin-top:6px;background:var(--v);color:#fff;text-decoration:none;padding:10px 18px;border-radius:24px;font-weight:600}
.legal{color:var(--m);font-size:12px;margin-top:16px}
@media (prefers-color-scheme:dark){:root{--t:#e8eee9;--m:#9aa8a0;--b:#2c3a32;--bg:#121a15}.card,.kpi{background:#1a251e}}
"""


def render_page(ville, s, cfg, now_dt):
    t, a = cfg['taux'], cfg['agent']
    parts = ['<!doctype html><html lang="fr"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1">'
             '<meta name="robots" content="noindex,nofollow">'
             '<title>Marché immobilier à %s</title><style>%s</style></head><body><main>' % (esc(ville), CSS)]
    parts.append('<h1>Marché immobilier à %s</h1><p class="sub">Ventes réelles de maisons et taux de crédit du moment</p>' % esc(ville))

    # Ventes
    parts.append('<h2>Ventes de maisons à %s</h2>' % esc(ville))
    if s:
        periode = '%s à %s' % (mois_annee(s['debut']), mois_annee(s['fin']))
        parts.append('<div class="kpis">'
                     '<div class="kpi"><b>%d</b><span>maisons vendues<br>%s</span></div>'
                     '<div class="kpi"><b>%s</b><span>prix médian</span></div>'
                     '<div class="kpi"><b>%s/m²</b><span>médiane par m² habitable<br>(terrain compris)</span></div></div>'
                     % (s['n'], esc(periode), euros(s['prix']), euros(s['pm2'], 10)))
        txt = ('La moitié des ventes se situe entre %s et %s le m² habitable ; surface médiane : %d m².'
               % (euros(s['p25'], 10), euros(s['p75'], 10), round(s['surf'])))
        if s['evol'] is not None:
            e = s['evol'] * 100
            txt += (' Sur un an, le prix médian au m² est %s.' % (
                'stable (%+.0f %%)' % e if abs(e) < 2 else ('en hausse de %.0f %%' % e if e > 0 else 'en baisse de %.0f %%' % -e)))
        parts.append('<div class="card"><p>%s</p>' % esc(txt))
        parts.append('<p class="meta">Dernières ventes enregistrées (sans adresse) :</p><ul class="v">')
        for v in s['recentes']:
            terr = ' · terrain %d m²' % round(v['terrain']) if v['terrain'] > 0 else ''
            parts.append('<li>%s · %d m² habitables%s · <b>%s</b></li>'
                         % (esc(mois_annee(v['date'])), round(v['surf']), esc(terr), euros(v['prix'])))
        parts.append('</ul><p class="meta">Source : DVF (Etalab / data.gouv.fr), actes enregistrés par les services fiscaux. '
                     'Maisons vendues seules, terrains &lt; 5 000 m². Les données publiques sont publiées avec plusieurs mois de décalage.</p></div>')
    else:
        parts.append('<div class="card"><p>Trop peu de ventes de maisons enregistrées à %s pour donner un repère fiable. '
                     'Je m\'appuie alors sur les ventes comparables des communes voisines, que je vous détaille sur place.</p></div>' % esc(ville))

    # Taux
    age = (now_dt - datetime.fromisoformat(t['date']).replace(tzinfo=timezone.utc)).days
    if age <= t.get('max_age_jours', 40):
        d = datetime.fromisoformat(t['date']).strftime('%d/%m/%Y')
        u = t.get('url') or ''
        src = ('<a href="%s">%s</a>' % (esc(u), esc(t['source']))) if re.match(r'https?://', u) else esc(t['source'])
        parts.append('<h2>Crédit immobilier</h2><div class="card"><h3>%s</h3><p>%s</p><p class="meta">Chiffres relevés le %s · Source : %s</p></div>'
                     % (esc(t['titre']), esc(t['texte']), d, src))

    # Agent
    tel = re.sub(r'\s', '', a['telephone'])
    parts.append('<div class="agent"><p>Chaque bien est différent : surface, terrain, état, exposition, quartier. '
                 'Ces chiffres sont des repères statistiques, pas une estimation. '
                 'Je me déplace pour vous donner un avis de valeur précis.</p>'
                 '<strong>%s</strong><br>%s<br>%s<br><a class="tel" href="tel:%s">📞 %s</a>'
                 % (esc(a['nom']), esc(a['titre']), esc(a['secteur']), esc(tel), esc(a['telephone'])))
    if a.get('mention'):
        parts.append('<p class="legal">%s</p>' % esc(a['mention']))
    parts.append('<p class="legal">Page mise à jour le %s. Informations publiques, sources citées ; elle ne constitue ni une '
                 'estimation ni une promesse de prix.</p></div></main></body></html>' % now_dt.strftime('%d/%m/%Y'))
    return ''.join(parts)


def find_git():
    return shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true')
    ap.add_argument('--commune')
    ap.add_argument('--refresh', action='store_true')
    args = ap.parse_args()

    cfg = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))
    now_dt = datetime.now(timezone.utc)
    communes = [re.sub(r'\s+', ' ', c).strip() for c in cfg['communes']]
    if args.commune:
        communes = [c for c in communes if slugify(c) == slugify(args.commune)]

    ventes = load_ventes(dvf_files(args.refresh))
    end = max(v['date'] for v in ventes)
    print('%d ventes de maisons retenues, dernière date DVF : %s' % (len(ventes), end), flush=True)
    par = {}
    for v in ventes:
        par.setdefault(v['slug'], []).append(v)

    OUT.mkdir(exist_ok=True)
    (OUT / '.nojekyll').write_text('', encoding='utf-8')
    for v in communes:
        s = analyse(par.get(slugify(v), []), end)
        d = OUT / slugify(v)
        d.mkdir(exist_ok=True)
        (d / 'index.html').write_text(render_page(v, s, cfg, now_dt), encoding='utf-8')
        print('%-28s %s' % (v, ('%d ventes, %s/m²' % (s['n'], euros(s['pm2'], 10))) if s else 'PAS ASSEZ DE VENTES'), flush=True)
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
