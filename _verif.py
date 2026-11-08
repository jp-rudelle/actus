import json
for f in ('docs/carte.json', 'docs/quartiers.json'):
    j = json.load(open(f, encoding='utf-8'))
    print(f, len(j['c']))
    for k in ('m', 'a'):
        grey = [c['n'] for c in j['c'] if not c[k][1]]
        est = [c['n'] for c in j['c'] if len(c[k]) > 4]
        print(' ', k, 'gris:', len(grey), grey, '| estimés:', len(est))
