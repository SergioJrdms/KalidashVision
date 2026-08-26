import sys, types, os
from datetime import datetime, timezone
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
from backend import productivity as prod
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

print("\n[9b] Fase 110 — indeciso chega à mesma fila sem alterar o placar")
indeciso = ev("fora","posto_vazio",0.99,120)
indeciso["fora_do_posto"] = "indeciso"
passante = ev("pass","posto_vazio",0.99,120); passante["fora_do_posto"] = "passante"
confirmado = ev("op","posto_vazio",0.99,120); confirmado["fora_do_posto"] = "operador"
falha = ev("falha","posto_vazio",0.99,120); falha["fora_do_posto"] = "falha_vlm"
teto = ev("teto","posto_vazio",0.99,120); teto["fora_do_posto"] = "teto_chamadas"
d, motivo, tp = pl.evento_em_duvida(indeciso, 0.65)
ck("fora_do_posto=indeciso vira dúvida própria",
   d and tp == "operador_fora_indeciso", (d, motivo, tp))
ck("motivo explica a identidade não confirmada",
   "não foi possível confirmar" in motivo.lower() and "operador" in motivo.lower(), motivo)
ck("passante não vira dúvida de indeciso", not pl.evento_em_duvida(passante, 0.65)[0])
ck("operador confirmado não vira dúvida de indeciso", not pl.evento_em_duvida(confirmado, 0.65)[0])
ck("falha_vlm não vira indeciso", not pl.evento_em_duvida(falha, 0.65)[0])
ck("teto_chamadas não vira indeciso", not pl.evento_em_duvida(teto, 0.65)[0])
f_ind = pl.montar_fila_duvidas(SB([indeciso, passante, confirmado, falha, teto]), "U", "P",
                                tipo_filtro="operador_fora_indeciso")
ck("filtro por tipo devolve somente o indeciso",
   [i["id"] for i in f_ind["itens"]] == ["fora"], f_ind["itens"])
base_kpi = {
    "video_id": "v", "papel_pessoa": "posto_vazio", "tempo_inicio_s": 0,
    "tempo_fim_s": 60, "principal": True,
    "_capturado_em": datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
    "_dia": "2026-08-17", "_cam_id": "cam1",
}
kpi_sem = prod.agregar_produtividade([base_kpi], agora=datetime(2026, 8, 17, 12, 3, tzinfo=timezone.utc))
kpi_ind = prod.agregar_produtividade([{**base_kpi, "fora_do_posto": "indeciso"}], agora=datetime(2026, 8, 17, 12, 3, tzinfo=timezone.utc))
metricas = ("produtividade_pct", "improdutividade_pct", "presenca_pct", "posto_vazio_pct")
ck("colocar indeciso na fila não altera KPI",
   all(kpi_sem[k] == kpi_ind[k] for k in metricas),
   {k: (kpi_sem[k], kpi_ind[k]) for k in metricas})

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



# ═════════════════════════════════════════════════════════════════════════
# A FILA SEGUE O RELÓGIO DA FÁBRICA (pedido do dono).
#
# Dois defeitos, encontrados em sequência:
#
# (1) ORDEM. O gestor recebia 06h, 14h, 09h, 07h e aquilo parecia sorteio. Não
#     era: a ordenação usava `tempo_inicio_s`, o tempo DENTRO do vídeo — e todo
#     vídeo começa em 0s. Com 46 vídeos no dia, saíam primeiro todos os trechos
#     de 0s de todos os vídeos, depois todos os de 10s.
#
# (2) HORA. Corrigida a ordem, a etiqueta mostrava 04h para um trecho das 07h.
#     Duas causas somadas: a referência era `videos.gravado_em` (derivado, que
#     cai para o instante de PROCESSAMENTO quando o nome não tem relógio), e a
#     formatação acontecia no NAVEGADOR — que relia o carimbo de hora de parede
#     como UTC e subtraía três horas. Agora a âncora é o carimbo do SEGMENTO e
#     quem formata é o servidor, que conhece o fuso do processo.
#
# ⚠️ SÓ ORDEM E LEITURA. O conjunto da fila, o gate de relevância, o que é
# aprendido e como se valida continuam idênticos — este bloco protege isso.
# ═════════════════════════════════════════════════════════════════════════
print("\n[F] A fila em ordem cronológica, no relógio da fábrica")
_main = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "main.py"), encoding="utf-8").read()

ck("a ordenação final usa o instante de RELÓGIO, não o tempo do arquivo",
   "def _instante(e: dict) -> tuple:" in _main and "itens.sort(key=_instante)" in _main)
ck("com o diagnóstico escrito: a chave estava errada, não aleatória",
   "todo vídeo começa em 0s" in _main)

# ── (2) A ÂNCORA É O SEGMENTO ──────────────────────────────────────────
ck("⭐ o relógio vem do SEGMENTO, que é o que a borda carimbou",
   'sb.table("segmentos")' in _main and '_seg_por_video[_k] = _s' in _main)
ck("e o vídeo fica só como reserva",
   '_base = (_s or {}).get("gravado_em") or i.get("gravado_em")' in _main)
ck("com o porquê: `videos.gravado_em` cai para o instante de PROCESSAMENTO",
   "PROCESSAMENTO" in _main)
ck("do vídeo usa-se o segmento MAIS ANTIGO — é a âncora de tempo_inicio_s",
   "O MAIS ANTIGO do vídeo" in _main)
ck("a origem do relógio viaja no payload, para o defeito ser diagnosticável",
   '"hora_de"' in _main)

# ── (2b) QUEM FORMATA É O SERVIDOR ─────────────────────────────────────
ck("⭐ o servidor entrega a hora PRONTA, no fuso do processo",
   '_tz, _ = fuso_do_processo(sb, user.empresa, nome)' in _main
   and 'i["instante_fabrica"] = _d.strftime("%H:%M")' in _main)
ck("carimbo sem fuso é hora de PAREDE da fábrica, não UTC",
   "_d.replace(tzinfo=_tz) if _d.tzinfo is None else _d.astimezone(_tz)" in _main)
_val = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "frontend", "src", "pages", "Validacao.tsx"),
            encoding="utf-8").read()
_adapt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "frontend", "src", "lib", "adapt.ts"), encoding="utf-8").read()
ck("⭐ a tela NÃO recalcula instante nenhum — era daí que vinha o 04h",
   "new Date" not in _val.replace("// ⚠️ NENHUM `new Date` AQUI", ""))
ck("e o adaptador só repassa o texto do servidor",
   "e.instante_fabrica ?? null" in _adapt and "new Date(new Date(e.gravado_em)" not in _adapt)

# ── (1) A ORDEM, como contrato ─────────────────────────────────────────
def _instante(e):
    g = e.get("instante_iso") or e.get("gravado_em")
    return (0 if g else 1, str(g or ""), float(e.get("tempo_inicio_s") or 0))

_fila = [
    {"id": "b", "instante_iso": "2026-08-14T09:00:00-03:00", "tempo_inicio_s": 0},
    {"id": "a", "instante_iso": "2026-08-14T06:09:00-03:00", "tempo_inicio_s": 540},
    {"id": "d", "instante_iso": "2026-08-14T14:00:00-03:00", "tempo_inicio_s": 0},
    {"id": "c", "instante_iso": "2026-08-14T09:05:00-03:00", "tempo_inicio_s": 300},
]
_fila.sort(key=_instante)
ck("06h vem antes de 09h, mesmo com deslocamento maior dentro do vídeo",
   [x["id"] for x in _fila] == ["a", "b", "c", "d"], [x["id"] for x in _fila])

# Sem relógio o evento não tem lugar na linha do tempo. Vai para o FIM: no
# começo, desalinharia justamente a primeira hora, que é onde o gestor ancora
# a leitura do turno.
_com_orfao = _fila + [{"id": "orfao", "instante_iso": None, "tempo_inicio_s": 0}]
_com_orfao.sort(key=_instante)
ck("evento sem relógio vai para o FIM, não para o começo",
   [x["id"] for x in _com_orfao][-1] == "orfao", [x["id"] for x in _com_orfao])

ck("ordenar e exibir usam a MESMA âncora — senão a fila parece fora de ordem",
   'g = e.get("instante_iso") or e.get("gravado_em")' in _main)
ck("a ordenação roda DEPOIS do gate — ordenar não muda o que entra",
   _main.index("itens = relevantes") < _main.index("itens.sort(key=_instante)"))

# ── LEITURA ────────────────────────────────────────────────────────────
ck("a tela marca a troca de faixa horária", "function MarcoHora" in _val)
ck("e mostra o instante do turno no card em foco", "{evento.hora}" in _val)
ck("com a faixa na barra do lote, para o foco único também mostrar a ordem",
   "por volta das {faixa}" in _val)

# ── ⚠️ O QUE NÃO PODE TER MUDADO ───────────────────────────────────────
ck("o gate de relevância continua igual",
   "evento_relevante_para_validacao(i, ocorr, maturidade)" in _main)
ck("e a rotação do PULAR continua (é ação do gestor, não desordem)",
   "Empurra o item da frente pro fim do lote" in _val)
ck("a leitura da hora é NÃO-FATAL: falhou, usa o vídeo e segue",
   "hora do segmento não lida" in _main)

print(f"\n== {ok} ok, {fail} fail ==")
