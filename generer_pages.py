#!/usr/bin/env python3
"""
generer_pages.py : génère une page « actualités + taux de crédit » par commune (site statique).

Usage :
    python generer_pages.py                 # génère le dossier docs/
    python generer_pages.py --push          # génère puis git add/commit/push (GitHub Pages)
    python generer_pages.py --offline x.xml # test sans réseau (même flux RSS pour toutes les communes)

Aucune dépendance : bibliothèque standard Python 3.8+.
Les réglages (taux, communes, signature) sont dans config.json.
"""
import argparse
import base64
import html
import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:  # console Windows : évite les erreurs d'encodage sur les accents et les symboles
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs"  # GitHub Pages : Settings > Pages > branche main, dossier /docs
MAX_AGE_DAYS = 60

# ── Filtres : mêmes listes que actus-lib.js (si tu en modifies une, modifie l'autre) ──
BLOCK = [
    'vs($|[^a-z])', 'handball', 'rugby', 'football', 'basket', 'volley', 'championnat', 'compositions', 'score',
    'billetterie', 'meteo', 'petition', 'prison', 'detenu', 'pendu', 'verbalisation', 'effondr', 'infiltration',
    'intemperie', 'pillage', 'preservatif', 'seisme', 'covid', 'vaccin', 'taux immobilier', 'zone tendue',
    'fermee', 'huis clos', 'ehpad', 'enfants places', 'abus', 'harcelement', 'phobie',
    'accident', 'mort($|[^a-z])', 'morte', 'deces', 'decede', 'tue($|[^a-z])', 'tues($|[^a-z])', 'tuerie',
    'meurtr', 'homicid', 'assassin', 'agress', 'viol($|[^a-z])', 'violee', 'violence', 'cambriol', 'incendi',
    'noyad', 'noye', 'suicid', 'disparu', 'disparition', 'proces($|[^a-z])', 'condamn', 'mis en examen',
    'garde a vue', 'interpell', 'trafic', 'drogue', 'stupefiant', 'fusillade', 'coups? de feu', 'poignard',
    'arme blanche', 'blesse', 'victime', 'enquete', 'fraude', 'escroquer', 'arnaque', 'explosion',
    'polemique', 'scandale', 'plainte', 'tribunal', 'justice', 'inondation', 'crue($|[^a-z])',
    'vigilance (rouge|orange)', 'pollution', 'greve', 'manifestation', 'blocage', 'faits? divers',
    'drame', 'tragi', 'horreur', 'terroris', 'attentat', 'bagarre', 'rixe', 'emeute', 'squat', 'expuls',
    'menace', 'cadavre', 'corps sans vie', 'hopital en crise', 'urgences? satur',
    'mobilisation', 'interruption', 'prefet', 'episode mediterraneen', 'episode cevenol', 'alerte',
    'ferment', 'fermeture', 'municipales', 'election', 'maire elu', 'resultats', 'syndicat', 'penurie',
    'carburant', 'conflit', 'guerre', 'tempete', 'canicule', 'incident', 'perturb', 'retard', 'panne',
    'couvre', 'interdiction', 'interdit', 'manifest', 'rassemblement', 'plus aucun', 'aucun tram', 'aucun bus',
    'coupe', 'urgence', 'percute', 'a tout emporte', 'colere', 'securite', 'police', 'gendarm', 'blocus',
    'saccage', 'degradation', 'vandal', 'vol($|[^a-z])', 'braquage', 'overdose', 'noyer', 'chute', 'ecroule',
]
BOOST = [
    'tram', 'bus($|[^a-z])', 'transport', 'gare', 'piste cyclable', 'velo', 'travaux', 'amenag', 'projet',
    'ecole', 'college', 'lycee', 'creche', 'marche', 'commerce', 'ouverture', 'ouvre', 'inaugur',
    'festival', 'fete', 'exposition', 'concert', 'mediatheque', 'piscine', 'parc($|[^a-z])', 'jardin',
    'mairie', 'metropole', 'logement', 'immobilier', 'urbanisme', 'quartier', 'vacances', 'spectacle',
    'noel', 'salon', 'stade', 'arena', 'aeroport', 'zone', 'culture', 'sport',
]
# exclusions.txt : une expression par ligne (sans accents ni majuscules) ; tout titre qui la contient est écarté
try:
    EXCLUSIONS = [norm_l.strip() for norm_l in open(HERE / 'exclusions.txt', encoding='utf-8').read().lower().splitlines()
                  if norm_l.strip() and not norm_l.startswith('#')]
except FileNotFoundError:
    EXCLUSIONS = []
BLOCK_RE = re.compile('(^|[^a-z])(?:' + '|'.join(BLOCK) + ')')
BOOST_RE = re.compile('(^|[^a-z])(?:' + '|'.join(BOOST) + ')')
STOP = {'jusqu', 'vers', 'dans', 'pour', 'avec', 'sont', 'plus', 'leur', 'leurs', 'cette', 'entre',
        'apres', 'sans', 'sous', 'comme', 'tout', 'tous', 'toute'}


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '') if not unicodedata.combining(c))


def norm(s):
    return strip_accents(s).lower()


def slugify(ville):
    """Doit rester identique à slugifyVille() dans content.js."""
    return re.sub(r'(^-+|-+$)', '', re.sub(r'[^a-z0-9]+', '-', norm(ville)))


def clean_ville(v):
    v = re.sub(r'[ ​‌‍﻿]', ' ', v or '')
    v = re.sub(r'\bcedex\b.*$', '', v, flags=re.I)
    v = re.sub(r'^ste[\s.-]+', 'Sainte-', v, flags=re.I)
    v = re.sub(r'^st[\s.-]+', 'Saint-', v, flags=re.I)
    return re.sub(r'\s+', ' ', v).strip()


# ── Récupération et parsing ──
def decode_gnews(link):
    """Anciens liens Google Actualités : l'URL de l'éditeur est dans l'identifiant (base64).
    Les nouveaux formats ne le permettent pas : on garde alors le lien Google tel quel."""
    try:
        m = re.search(r'/articles/([A-Za-z0-9_-]+)', link)
        if not m:
            return link
        ident = m.group(1)
        raw = base64.urlsafe_b64decode(ident + '=' * (-len(ident) % 4))
        u = re.search(rb'https?://[\x21-\x7e]+', raw)
        if u:
            url = u.group(0).decode('ascii', 'ignore')
            if '.' in url:
                return url
    except Exception:
        pass
    return link


def parse_rss(xml_text):
    items = []
    root = ET.fromstring(xml_text)
    for it in root.iter('item'):
        title = (it.findtext('title') or '').strip()
        link = (it.findtext('link') or '').strip()
        src_el = it.find('source')
        source = (src_el.text or '').strip() if src_el is not None else ''
        try:
            ts = parsedate_to_datetime(it.findtext('pubDate') or '').timestamp()
        except Exception:
            ts = 0
        if source and title.endswith(' - ' + source):
            title = title[:-(len(source) + 3)].strip()
        elif not source:
            k = title.rfind(' - ')
            if k > 20:
                source, title = title[k + 3:].strip(), title[:k].strip()
        if title and re.match(r'https?://', link):
            items.append({'title': title, 'source': source, 'link': decode_gnews(link), 'ts': ts})
    return items


def fetch_rss(q, offline=None):
    if offline:
        return parse_rss(Path(offline).read_text(encoding='utf-8'))
    url = 'https://news.google.com/rss/search?q=' + urllib.parse.quote(q) + '&hl=fr&gl=FR&ceid=FR:fr'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for essai in range(3):
        time.sleep(1.2)  # ~100 requêtes pour 40 communes : on reste poli pour éviter le blocage de Google
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return parse_rss(r.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and essai < 2:
                time.sleep(15 * (essai + 1))  # limitation temporaire : on attend puis on réessaie
                continue
            raise


# ── Sélection ──
def tokens(title):
    return [w[:4] for w in re.split(r'[^a-z0-9]+', norm(title)) if len(w) > 3 and w not in STOP]


def similar(a, b):
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.4


def score(it, ville_n, now):
    t = norm(it['title'])
    s = min(len(list(BOOST_RE.finditer(t))), 3) * 2
    if ville_n and ville_n in t:
        s += 3
    age = (now - it['ts']) / 86400 if it['ts'] else 30
    s += 2 if age <= 7 else 1 if age <= 14 else 0
    return s


SRC_BLOCK = ['federation francaise', 'score', 'rugby amateur', 'sofascore', 'volcano', 'mypetition',
             'orange actualites', 'jds.fr', 'meteo france', 'syndicat', 'tribunal du net', 'liberation',
             'rugbyrama', 'basketeurope', 'bebasket', 'radio 8', 'dimo', 'seloger', 'orpi', 'economiematin',
             'ac franchise', 'wansquare', 'maison', 'ligue contre', 'le parisien', 'le progres']


def _n2(x):
    return norm(x).replace("\u2019", "'").replace('-', ' ').replace("'", ' ')


def pertinent(title, ville_n):
    """Strict : la commune doit figurer dans le titre (sinon hors-sujet : homonymes, sport national...)."""
    t = _n2(title)
    if any(e in t for e in EXCLUSIONS):
        return False
    v = _n2(ville_n)
    return bool(v) and v in t


def select(items, ville, mx, now):
    ville_n = norm(ville)
    kept = []
    for it in items:
        age = (now - it['ts']) / 86400 if it['ts'] else 30
        if age <= MAX_AGE_DAYS and not BLOCK_RE.search(norm(it['title'])) and pertinent(it['title'], ville_n) and not any(b in _n2(it.get('source', '')) for b in SRC_BLOCK):
            kept.append(dict(it, score=score(it, ville_n, now)))
    kept.sort(key=lambda x: (-x['score'], -x['ts']))
    out = []
    for it in kept:
        if len(out) >= mx:
            break
        if not any(similar(o['title'], it['title']) for o in out):
            out.append(it)
    return out


def actus_pour(ville, mx, now, offline=None):
    selected, pool, errors = [], [], []
    for days in (21, 90):
        try:
            pool += fetch_rss('"%s" when:%dd' % (ville, days), offline)
        except Exception as e:  # réseau, XML...
            errors.append(str(e))
            continue
        selected = select(pool, ville, mx, now)
        if len(selected) >= 3:
            break
    if len(selected) < 3 and norm(ville) != 'montpellier':
        try:
            for it in select(fetch_rss('Montpellier Métropole when:21d', offline), 'Montpellier', mx, now):
                if len(selected) < mx and not any(similar(o['title'], it['title']) for o in selected):
                    selected.append(dict(it, scope='metropole'))
        except Exception as e:
            errors.append(str(e))
    return selected, errors


# ── Rendu HTML ──
def date_fr(ts):
    mois = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.']
    d = datetime.fromtimestamp(ts, timezone.utc)
    return '%d %s' % (d.day, mois[d.month - 1])


def esc(s):
    return html.escape(s or '', quote=True)


def safe_url(u):
    return u if re.match(r'https?://', u or '') else ''


CSS = """
:root{--v:#2E7D32;--t:#1f2a24;--m:#667;--b:#e4e8e5;--bg:#f6f8f6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
main{max-width:640px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:24px;line-height:1.25;margin:8px 0 4px}h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--v);margin:28px 0 10px}
.sub{color:var(--m);margin:0 0 8px;font-size:14px}
.card{background:#fff;border:1px solid var(--b);border-radius:12px;padding:14px 16px;margin:0 0 10px}
.card h3{font-size:16px;margin:0 0 4px;line-height:1.35}.card h3 a{color:var(--t);text-decoration:none}.card h3 a:hover{text-decoration:underline}
.card p{margin:4px 0;font-size:14.5px}.meta{color:var(--m);font-size:12.5px}.warn{color:#b26a00;font-weight:600}
.agent{margin-top:30px;padding-top:16px;border-top:1px solid var(--b);font-size:14.5px}
.agent a.tel{display:inline-block;margin-top:6px;background:var(--v);color:#fff;text-decoration:none;padding:10px 18px;border-radius:24px;font-weight:600}
.legal{color:var(--m);font-size:12px;margin-top:16px}
@media (prefers-color-scheme:dark){:root{--t:#e8eee9;--m:#9aa8a0;--b:#2c3a32;--bg:#121a15}.card{background:#1a251e}}
"""


def render_page(ville, actus, cfg, now_dt):
    t, a = cfg['taux'], cfg['agent']
    age = (now_dt - datetime.fromisoformat(t['date']).replace(tzinfo=timezone.utc)).days
    parts = ['<!doctype html><html lang="fr"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1">'
             '<meta name="robots" content="noindex,nofollow">'
             '<title>Actualité de %s</title><style>%s</style></head><body><main>' % (esc(ville), CSS)]
    parts.append('<h1>Actualité de %s</h1><p class="sub">Résumé de l\'actualité locale et immobilière · relevé le %s</p>'
                 % (esc(ville), now_dt.strftime('%d/%m/%Y')))

    if age <= t.get('max_age_jours', 40):
        d = datetime.fromisoformat(t['date']).strftime('%d/%m/%Y')
        src = ('<a href="%s">%s</a>' % (esc(safe_url(t['url'])), esc(t['source']))) if safe_url(t.get('url')) else esc(t['source'])
        parts.append('<h2>Crédit immobilier</h2><div class="card"><h3>%s</h3><p>%s</p><p class="meta">Chiffres relevés le %s · Source : %s</p></div>'
                     % (esc(t['titre']), esc(t['texte']), d, src))
    for c in cfg.get('chiffres', []):
        parts.append('<div class="card"><h3>%s</h3><p>%s</p><p class="meta">%s</p></div>'
                     % (esc(c.get('titre')), esc(c.get('texte')), esc(c.get('source'))))

    parts.append('<h2>Dans votre commune : %s</h2>' % esc(ville))
    if actus:
        for it in actus:
            link = safe_url(it['link'])
            title = '<a href="%s" rel="noopener">%s</a>' % (esc(link), esc(it['title'])) if link else esc(it['title'])
            meta = ' · '.join(x for x in [it.get('source'), date_fr(it['ts']) if it['ts'] else '',
                                          'Métropole de Montpellier' if it.get('scope') == 'metropole' else ''] if x)
            parts.append('<div class="card"><h3>%s</h3><p class="meta">%s</p></div>' % (title, esc(meta)))
    else:
        parts.append('<div class="card"><p>Pas d\'actualité marquante relevée cette semaine.</p></div>')

    tel = re.sub(r'\s', '', a['telephone'])
    parts.append('<div class="agent"><strong>%s</strong><br>%s<br>%s<br><a class="tel" href="tel:%s">📞 %s</a>'
                 % (esc(a['nom']), esc(a['titre']), esc(a['secteur']), esc(tel), esc(a['telephone'])))
    if a.get('mention'):
        parts.append('<p class="legal">%s</p>' % esc(a['mention']))
    parts.append('<p class="legal">Cette page résume des informations publiques, sources citées ; elle ne constitue ni une '
                 'estimation ni une promesse de prix.</p></div></main></body></html>')
    return ''.join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--push', action='store_true', help='git add/commit/push après génération')
    ap.add_argument('--offline', help='fichier RSS local (test sans réseau)')
    ap.add_argument('--commune', help='ne régénérer qu\'une commune')
    args = ap.parse_args()

    cfg = json.loads((HERE / 'config.json').read_text(encoding='utf-8'))
    now_dt = datetime.now(timezone.utc)
    now = now_dt.timestamp()
    communes = [clean_ville(c) for c in cfg['communes']]
    if args.commune:
        communes = [c for c in communes if slugify(c) == slugify(args.commune)]

    OUT.mkdir(exist_ok=True)
    (OUT / '.nojekyll').write_text('', encoding='utf-8')
    done, report = [], []
    for v in communes:
        actus, errors = actus_pour(v, cfg.get('max_actus', 5), now, args.offline)
        d = OUT / slugify(v)
        d.mkdir(exist_ok=True)
        (d / 'index.html').write_text(render_page(v, actus, cfg, now_dt), encoding='utf-8')
        done.append(slugify(v))
        report.append('%-28s %d actu(s)%s' % (v, len(actus), ('  ⚠ ' + errors[0]) if errors and not actus else ''))
        print(report[-1], flush=True)

    (OUT / 'index.html').write_text('<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
                                    '<title>Actualités</title>', encoding='utf-8')
    print('\nPages générées dans :', OUT)
    print('\nÀ coller dans content.js :')
    print('const ACTUS_COMMUNES = ' + json.dumps(sorted(set(slugify(clean_ville(c)) for c in cfg['communes'])), ensure_ascii=False) + ';')

    t_age = (now_dt - datetime.fromisoformat(cfg['taux']['date']).replace(tzinfo=timezone.utc)).days
    if t_age > cfg['taux'].get('max_age_jours', 40):
        print('\n⚠ Le taux de config.json date de %d jours : il est masqué des pages. Mets-le à jour.' % t_age)
    if not cfg['agent'].get('mention'):
        print('⚠ config.json > agent > mention est vide : ajoute ton EI / RSAC si tu veux l\'afficher en bas de page.')

    if args.push:
        for cmd in (['git', 'add', '-A'], ['git', 'commit', '-m', 'Actus %s' % now_dt.strftime('%Y-%m-%d')], ['git', 'push']):
            r = subprocess.run(cmd, cwd=HERE)
            if r.returncode != 0 and cmd[1] != 'commit':  # commit vide = rien de neuf, pas une erreur
                sys.exit('Échec : ' + ' '.join(cmd))


if __name__ == '__main__':
    main()
