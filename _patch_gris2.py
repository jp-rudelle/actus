import json, shutil, pathlib
H = pathlib.Path(__file__).parent
RA, AG, SH = ('RealAdvisor', '10/2026'), ('agence.immo (modélisé)', '2026'), ('Square Habitat (modélisé)', '2026')
rows = [
 # RealAdvisor 10/2026 : (commune, m, a)
 ('Montarnaud',None,3059),('Montaud',3747,3303),('Mudaison',None,3686),('Murles',3733,3786),('Murviel-lès-Montpellier',None,3178),
 ('Puéchabon',None,1989),('Rouet',3640,2408),('Saint-Bauzille-de-Montmel',None,3052),('Entre-Vignes',None,2781),
 ('Sainte-Croix-de-Quintillargues',3701,3042),('Saint-Drézéry',None,3067),('Saint-Geniès-des-Mourgues',None,3787),
 ('Saint-Hilaire-de-Beauvoir',3596,2997),('Saint-Jean-de-Cornies',3755,2953),('Saint-Jean-de-Cuculles',4212,3880),
 ('Saint-Just',None,3254),('Saint-Martin-de-Londres',None,2683)]
src = {r[0]: RA for r in rows}
ag = [('Argelliers',None,3610),('Assas',None,3885),('Aumelas',None,3213),('Beaulieu',None,1722),('La Boissière',None,4174),
 ('Buzignargues',2606,3275),('Campagne',1795,1784),('Causse-de-la-Selle',2231,3038),('Cazevieille',3185,4705),
 ('Combaillaux',None,3617),('Fontanès',None,2328),('Galargues',2682,2928),('Garrigues',2884,2988),('Guzargues',3425,3890),
 ('Mas-de-Londres',2842,3427),('Les Matelles',None,3898)]
rows += ag
for r in ag: src[r[0]] = AG
rows.append(('Lansargues', None, 3294)); src['Lansargues'] = ('netvendeur.com (prix des annonces)', '02/2026')
sh = [('Saint-Nazaire-de-Pézan',3672,2096),('Saint-Paul-et-Valmalle',None,3016),('Saint-Sériès',None,3039),
 ('Saint-Vincent-de-Barbeyrargues',None,3674),('Saturargues',None,3010),('Saussan',None,3682),('Saussines',None,3376),
 ('Le Triadou',4174,2702),('Vacquières',None,3469),('Vailhauquès',None,3546),('Valergues',None,3662),('Valflaunès',3870,3661),
 ('Vic-la-Gardiole',None,2929),('Villetelle',None,2925),('Villeveyrac',None,4733),('Viols-en-Laval',4011,3374),('Viols-le-Fort',None,3344)]
rows += sh
for r in sh: src[r[0]] = SH
src['Saint-Vincent-de-Barbeyrargues'] = ('immovrai (ventes DVF)', '06/2026')
src['Le Triadou'] = ('immovrai (ventes DVF)', '06/2026')
src['Villeveyrac'] = ('immovrai (ventes DVF)', '06/2026')
out = {}
for n, m, a in rows:
    d = {'src': src[n][0], 'date': src[n][1]}
    if m: d['m'] = m
    if a: d['a'] = a
    out[n] = d
(H / 'complements_carte.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print('complements', len(out))

# ---- template
p = H / 'page_template.html'
shutil.copy(p, H / 'page_template.html.bak_gris')
t = p.read_bytes().decode('utf-8')
def rep(a, b):
    global t
    assert t.count(a) == 1, a
    t = t.replace(a, b)
rep("var MODE='p',QUART=null;", "var MODE='p',QUART=null,MAPK='m';")
rep("var k=st.t,idx=", "var k=MAPK,idx=")
rep("chips($(P+'types'),[['m','Maisons'],['a','Appartements']],st.t,function(v){st.t=v;st.p=0;render();});", "chips($(P+'types'),[['m','Maisons'],['a','Appartements']],MAPK,function(v){MAPK=v;drawMap();});")
rep("(d[3]||12)+' mois)'+(d[2]!==null", "(d[3]||12)+' mois)'+(d[2]!==null")
rep("return d[1]?name(c)+' : '+eur(d[1],10)", "if(d[4])return name(c)+' : environ '+eur(d[1],10)+'/m² (estimation indicative, '+((c.e&&c.e[k])||'source tierce')+' ; moins de 10 ventes enregistrées)';\n    return d[1]?name(c)+' : '+eur(d[1],10)")
rep("if(idx===1&&c[k][1]&&c[k][3]>12){", "if(idx===1&&c[k][4]){var h2=document.createElementNS(ns,'path');h2.setAttribute('d',c.d);h2.setAttribute('fill-rule','evenodd');h2.setAttribute('fill','url(#dot'+P+')');h2.style.pointerEvents='none';svg.appendChild(h2);}\n    if(idx===1&&c[k][1]&&c[k][3]>12){")
rep("pt.appendChild(ln);df.appendChild(pt);svg.appendChild(df);", "pt.appendChild(ln);df.appendChild(pt);\n  var pd=document.createElementNS(ns,'pattern');pd.setAttribute('id','dot'+P);pd.setAttribute('width','8');pd.setAttribute('height','8');pd.setAttribute('patternUnits','userSpaceOnUse');var cc=document.createElementNS(ns,'circle');cc.setAttribute('cx','4');cc.setAttribute('cy','4');cc.setAttribute('r','1.6');cc.setAttribute('fill','rgba(0,0,0,.30)');pd.appendChild(cc);df.appendChild(pd);svg.appendChild(df);")
rep("gris : moins de 10 ventes en 36 mois)", "pointillé : moins de 10 ventes enregistrées, prix estimé à partir de sites immobiliers tiers et donc indicatif)")
p.write_bytes(t.encode('utf-8'))
print('template ok')
