import sys, types, os
sys.path.insert(0, "/home/user/KalidashVision")
for m in ["cv2","numpy","requests","ultralytics","supabase","groq","anthropic","openai","dotenv","httpx","PIL","PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv=lambda *a,**k:None
sys.modules["ultralytics"].YOLO=object; sys.modules["supabase"].create_client=lambda *a,**k:None
sys.modules["supabase"].Client=object; sys.modules["groq"].Groq=object
sys.modules["anthropic"].Anthropic=object; sys.modules["openai"].OpenAI=object
sys.modules["numpy"].ndarray=object; sys.modules["numpy"].array=lambda s,dtype=None:[list(r) for r in s]
sys.modules["cv2"].pointPolygonTest=lambda *a,**k:-1.0
os.environ.setdefault("SUPABASE_URL","https://x.supabase.co"); os.environ.setdefault("SUPABASE_KEY","k")
from backend import pipeline as pl
ok=fail=0
def ck(n,c,e=""):
    global ok,fail
    c=bool(c); print(("  ok   " if c else "  FAIL ")+n+("" if c else f"  {e}")); ok+=c; fail+=(not c)

print("\n[1] Limiar 0.65 — o corte medido nos dados")
ck("0.53 (moeda ao ar) → dúvida", pl.evento_em_duvida({"confianca":0.53,"n_amostras":9},0.65)[0])
ck("0.60 → dúvida", pl.evento_em_duvida({"confianca":0.60,"n_amostras":9},0.65)[0])
ck("0.65 NÃO é dúvida (limiar é exclusivo)", not pl.evento_em_duvida({"confianca":0.65,"n_amostras":9},0.65)[0])
ck("0.70 → não", not pl.evento_em_duvida({"confianca":0.70,"n_amostras":9},0.65)[0])
ck("1.00 (concordante) → não", not pl.evento_em_duvida({"confianca":1.0,"n_amostras":9},0.65)[0])
d,m,tp = pl.evento_em_duvida({"confianca":0.5,"n_rotulos_no_minuto":3,"n_amostras":9},0.65)
ck("motivo cita concordância e nº de rótulos", "50%" in m and "3 rótulos" in m, m)

print("\n[2] Camada ativa também põe em dúvida (independe da confiança)")
d,m,tp = pl.evento_em_duvida({"confianca":0.95,"em_duvida":True,"duvida_motivo":"só uma pessoa","n_amostras":9},0.65)
ck("confiança alta + camada → dúvida", d, (d,m))
ck("motivo da camada preservado", "só uma pessoa" in m, m)
d,m,tp = pl.evento_em_duvida({"confianca":0.4,"em_duvida":True,"duvida_motivo":"cena X","n_amostras":9},0.65)
ck("as duas origens somam no motivo", "cena X" in m and "amostras" in m, m)

print("\n[3] Já validado sai da fila")
ck("validado_humano → não é dúvida",
   not pl.evento_em_duvida({"confianca":0.3,"em_duvida":True,"validado_humano":True,"n_amostras":9},0.65)[0])

print("\n[4] Limiar CONFIGURÁVEL muda o resultado sem reprocessar")
ck("com 0.55, o 0.60 sai da dúvida", not pl.evento_em_duvida({"confianca":0.60,"n_amostras":9},0.55)[0])
ck("com 0.80, o 0.70 entra", pl.evento_em_duvida({"confianca":0.70,"n_amostras":9},0.80)[0])

print("\n[5] B4 — fila ordenada por MINUTOS e filtro por rótulo")
class Q:
    def __init__(s,d): s.d=d
    def select(s,*a,**k): return s
    def eq(s,*a,**k): return s
    def is_(s,*a,**k): return s
    def limit(s,*a,**k): return s
    def order(s,*a,**k): return s
    def range(s,*a,**k): return s
    @property
    def not_(s): return s
    def execute(s): return types.SimpleNamespace(data=s.d)
class SB:
    def __init__(s,ev,ctx=None): s.ev=ev; s.ctx=ctx or []
    def table(s,n): return Q(s.ev if n=="eventos" else s.ctx)
def ev(i,label,conf,dur,principal=True,camadas=None):
    return {"id":i,"video_id":"v","comportamento_label":label,"label_corrigido":None,
            "descricao_bruta":label,"tempo_inicio_s":0,"tempo_fim_s":dur,"confianca":conf,
            "n_rotulos_no_minuto":2,"rotulos_competindo":[label,"outro"],
            "em_duvida":bool(camadas),"duvida_motivo":"camada X" if camadas else None,
            "camadas_disparadas":camadas,"validado_humano":False,"cam_id":"cam1",
            "pessoa_track_id":1,"papel_pessoa":"operador","principal":principal,
            "n_amostras":9}
eventos=[ev("a","monitorar_maquina",0.5,60), ev("b","monitorar_maquina",0.6,300),
         ev("c","operar_torno",0.5,120), ev("d","operar_torno",0.9,600),
         ev("e","conversar",0.4,30,principal=False)]
r = pl.montar_fila_duvidas(SB(eventos),"U","P")
ck("limiar exposto", r["limiar"]==0.65, r["limiar"])
ck("evento de confiança alta fica fora", all(i["id"]!="d" for i in r["itens"]), r["itens"])
ck("auditoria (principal=False) fica fora", all(i["id"]!="e" for i in r["itens"]))
ck("ORDENADO por minutos (maior primeiro)",
   [i["id"] for i in r["itens"]]==["b","c","a"], [i["id"] for i in r["itens"]])
ck("minutos calculados", r["itens"][0]["minutos"]==5.0, r["itens"][0])
ck("motivo presente em todo item", all(i["motivo"] for i in r["itens"]))
ck("total de minutos em dúvida", r["minutos_totais"]==8.0, r["minutos_totais"])

print("\n[6] por_rotulo revela o 'depósito da dúvida'")
pr = {x["rotulo"]:x for x in r["por_rotulo"]}
ck("monitorar_maquina agregado", pr["monitorar_maquina"]["minutos"]==6.0, pr)
ck("ordenado por minutos", r["por_rotulo"][0]["rotulo"]=="monitorar_maquina", r["por_rotulo"])
f = pl.montar_fila_duvidas(SB(eventos),"U","P",rotulo="monitorar_maquina")
ck("filtro por rótulo funciona", all(i["rotulo"]=="monitorar_maquina" for i in f["itens"]), f["itens"])
ck("filtro NÃO some com o agregado (dá pra comparar)", len(f["por_rotulo"])==2, f["por_rotulo"])
ck("filtrado_por reportado", f["filtrado_por"]=="monitorar_maquina")

print("\n[7] Camadas ativas aparecem no item")
r2 = pl.montar_fila_duvidas(SB([ev("z","x",0.99,60,camadas=[{"nome":"c1","modo":"ativa"}])]),"U","P")
ck("evento de camada entra mesmo com confiança alta", len(r2["itens"])==1, r2["itens"])
ck("nome da camada no item", r2["itens"][0]["camadas"]==["c1"], r2["itens"][0])
r3 = pl.montar_fila_duvidas(SB([ev("y","x",0.99,60,camadas=[{"nome":"s1","modo":"sombra"}])]),"U","P")
ck("camada em SOMBRA não põe na fila", len(r3["itens"])==0, r3["itens"])

print("\n[8] Limiar por processo (coluna) tem precedência sobre o env")
ck("coluna do processo vence", pl.limiar_duvida(SB([],[{"duvida_limiar":0.8}]),"U","P")==0.8)
ck("sem coluna → env/default", pl.limiar_duvida(SB([],[{"duvida_limiar":None}]),"U","P")==0.65)
print("\n[9] Fase 59 — AUSÊNCIA DE EVIDÊNCIA é caso à parte")
d,m,tp = pl.evento_em_duvida({"confianca":0.65,"n_amostras":1},0.65)
ck("1 amostra → dúvida do tipo sem_evidencia", d and tp=="sem_evidencia", (d,tp))
ck("motivo diz que falta evidência, não que discordaram",
   "evidência" in m and "discord" not in m, m)
d,m,tp = pl.evento_em_duvida({"confianca":1.0,"n_amostras":1},0.65)
ck("1 amostra com share 1.0 NÃO passa por confiante", d and tp=="sem_evidencia", (d,tp))
d,m,tp = pl.evento_em_duvida({"confianca":None,"n_amostras":1},0.65)
ck("concordância indefinida (None) é tratada", d and tp=="sem_evidencia", (d,tp))
d,m,tp = pl.evento_em_duvida({"confianca":0.53,"n_amostras":14},0.65)
ck("14 amostras discordando → tipo discordancia", d and tp=="discordancia", (d,tp))
ck("os dois casos NÃO se misturam no motivo", "evidência" not in m, m)
e1 = ev("s1","x",0.65,8); e1["n_amostras"]=1
e2 = ev("s2","x",0.53,60); e2["n_amostras"]=14
r9 = pl.montar_fila_duvidas(SB([e1,e2]),"U","P")
tipos = {t["tipo"]: t for t in r9["por_tipo"]}
ck("fila separa por tipo", set(tipos)=={"sem_evidencia","discordancia"}, r9["por_tipo"])
ck("cada item carrega seu tipo", all(i["tipo"] for i in r9["itens"]), r9["itens"])
f9 = pl.montar_fila_duvidas(SB([e1,e2]),"U","P",tipo_filtro="sem_evidencia")
ck("filtro por tipo funciona",
   len(f9["itens"])==1 and f9["itens"][0]["id"]=="s1", f9["itens"])
ck("agregado por tipo NÃO some ao filtrar", len(f9["por_tipo"])==2, f9["por_tipo"])

print("\n[10] Fase 59 — leitura AUTO-CURATIVA (o incidente do 500)")
class SBSemColuna:
    """Banco a uma migração de distância: recusa colunas específicas, uma por vez,
    exatamente como o PostgREST fez com `eventos.cam_id`."""
    def __init__(s, ev, ausentes): s.ev=ev; s.ausentes=set(ausentes); s.tentativas=[]
    def table(s,n):
        if n!="eventos": return Q([])
        outer=s
        class T:
            def select(self, cols):
                outer.tentativas.append(cols)
                for c in outer.ausentes:
                    if c in cols.split(", "):
                        raise Exception({"message": f"column eventos.{c} does not exist",
                                         "code":"42703"})
                return Q(outer.ev)
            def in_(self,*a,**k): return Q([])
        return T()
e_base = {"id":"p1","video_id":"v","comportamento_label":"x","label_corrigido":None,
          "descricao_bruta":"x","tempo_inicio_s":0,"tempo_fim_s":120,"confianca":0.5,
          "n_amostras":9,"validado_humano":False,"pessoa_track_id":1,
          "papel_pessoa":"operador","principal":True}
# UMA coluna ausente (o caso real: cam_id)
sb1 = SBSemColuna([e_base], ["rotulos_competindo"])
r10 = pl.montar_fila_duvidas(sb1,"U","P")
ck("1 coluna ausente → sem erro", "erro" not in r10, r10)
ck("a fila FUNCIONA degradada", len(r10["itens"])==1, r10["itens"])
ck("removeu só a coluna recusada",
   "rotulos_competindo" not in sb1.tentativas[-1] and "confianca" in sb1.tentativas[-1],
   sb1.tentativas[-1])
# VÁRIAS ausentes: precisa curar uma a uma, não desistir na primeira
sb2 = SBSemColuna([e_base], ["em_duvida","duvida_motivo","camadas_disparadas",
                             "n_rotulos_no_minuto","rotulos_competindo"])
r11 = pl.montar_fila_duvidas(sb2,"U","P")
ck("5 colunas ausentes → ainda funciona", "erro" not in r11 and len(r11["itens"])==1, r11)
ck("removeu TODAS as recusadas",
   not any(c in sb2.tentativas[-1] for c in
           ["em_duvida","duvida_motivo","camadas_disparadas","n_rotulos_no_minuto","rotulos_competindo"]),
   sb2.tentativas[-1])
ck("detecta discordância mesmo sem enriquecimento",
   r11["itens"][0]["tipo"]=="discordancia", r11["itens"][0])
ck("cam_id não é pedido a eventos (mora em videos)",
   "cam_id" not in sb2.tentativas[0], sb2.tentativas[0])
# erro que NÃO é de coluna → não entra em loop, devolve payload completo
class SBMorto:
    def table(s,n): raise Exception("banco fora")
r12 = pl.montar_fila_duvidas(SBMorto(),"U","P")
ck("erro não-de-coluna → payload completo, sem KeyError na tela",
   {"por_tipo","por_rotulo","itens","limiar","filtrado_por"} <= set(r12), list(r12))

print(f"\n== {ok} ok, {fail} fail ==")
sys.exit(1 if fail else 0)
