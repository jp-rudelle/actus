s = open('_patch_gris.py', encoding='utf-8').read()
a = s.index('# ---- generateur'); b = s.index('# ---- template')
s = s[:a] + s[b:]
s = s.replace("""rep("[['m','Maisons'],['a','Appartements']],st.t,function(v){st.t=v;st.p=0;render();});", "[['m','Maisons'],['a','Appartements']],MAPK,""",
              """rep("chips($(P+'types'),[['m','Maisons'],['a','Appartements']],st.t,function(v){st.t=v;st.p=0;render();});", "chips($(P+'types'),[['m','Maisons'],['a','Appartements']],MAPK,""")
open('_patch_gris2.py', 'w', encoding='utf-8').write(s)
