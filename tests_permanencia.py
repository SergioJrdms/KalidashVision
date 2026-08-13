"""Fase 97 — a produtividade vem do que foi OBSERVADO, não do nome do rótulo.

Decisão dos sócios (12/08). O produto é TEMPO DE PERMANÊNCIA NO POSTO.

O DIAGNÓSTICO: as descrições do VLM estão boas. O que estava quebrado era o
que vinha depois — descrição → rótulo (cluster) → categoria Lean →
produtividade. Duas traduções, cada uma perdendo informação e somando erro. O
caso que fechou a decisão: "parado junto ao torno, máquina parada" virava
`acao_indefinida` e saía PRODUTIVO. A descrição certa, o rótulo lixo, a
categoria contradizendo a descrição.

O QUE ESTA SUÍTE PROTEGE, em ordem de importância:

[1-4] NENHUM RÓTULO DECIDE. É a garantia estrutural da fase: se um rótulo
      novo puder mexer no número, a "queda por contabilidade" volta e todo o
      resto perde o sentido.

[5]   `trabalho=null` NUNCA vira produtivo por omissão. Omissão que rende
      ponto é exatamente o viés que esta fase existe para derrubar.

[6]   Os três estados somam 100% do tempo observado.

[7]   Correção humana é inviolável.

[8]   Zero chamada de API nova: `trabalho` vai no JSON que já existe.

Rodar:  python tests_permanencia.py
"""
import sys, types, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
          "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object
sys.modules["numpy"].ndarray = object
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

from backend import pipeline as pl  # noqa: E402

# O nível 2 nasce ABSTENDO-SE até a orientação ser verificada com dado (a
# verificação exige `orientacao` persistida, que só existe a partir desta
# fase). Esta suíte testa a REGRA, então liga a chave — e o bloco [13] trava
# o padrão desligado.
pl._ORIENTACAO_VERIFICADA = True

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# `frente_maquina='oposta'` = quem está DE COSTAS para a câmera está de frente
# para o torno. É a configuração que a verificação com dado precisa confirmar.
OPOSTA, CAMERA = "oposta", "camera"


def ev(label="operar_torno", papel="operador", orient=None, trabalho=None,
       corrigido=None, validado=False, correto=None, ini=0, fim=60):
    return {"comportamento_label": label, "label_corrigido": corrigido,
            "papel_pessoa": papel, "orientacao": orient, "trabalho": trabalho,
            "validado_humano": validado, "validacao_correto": correto,
            "principal": True, "tempo_inicio_s": ini, "tempo_fim_s": fim}


print("\n[1] Nível 1 — fora do posto é IMPRODUTIVO, e não interessa o que faz lá")
c, n, m, est = pl.decidir_permanencia(
    ev(label="posto_vazio", papel="posto_vazio"), OPOSTA)
check("desperdício", c == "desperdicio", c)
check("estado 'fora_do_posto'", est == pl.EST_FORA, est)
check("o motivo cita zona e rastreamento", "zona" in m and "rastreamento" in m, m)
c, _n, _m, est = pl.decidir_permanencia(ev(papel="visitante", orient="costas"), OPOSTA)
check("visitante conta como fora (o tempo não é do titular)",
      c == "desperdicio" and est == pl.EST_FORA, (c, est))

print("\n[2] Nível 2 — no posto e voltado para o torno é PRODUTIVO")
c, n, m, est = pl.decidir_permanencia(ev(orient="costas"), OPOSTA)
check("de costas para a câmera + 'oposta' → voltado para o torno",
      c == "valor_agregado" and est == pl.EST_NO_TORNO, (c, est))
check("nível 'orientacao'", n == "orientacao", n)
check("o motivo diz que foi a POSE", "pose" in m, m)
c, _n, _m, est = pl.decidir_permanencia(ev(orient="frente"), OPOSTA)
check("de frente para a câmera + 'oposta' → de costas para o torno",
      est == pl.EST_OUTRO_LADO, est)
# E com a configuração invertida, tudo troca de lado — é por isso que ela
# precisa ser verificada com dado ANTES de valer.
_c, _n, _m, est = pl.decidir_permanencia(ev(orient="frente"), CAMERA)
check("com 'camera', de frente para a câmera = voltado para o torno",
      est == pl.EST_NO_TORNO, est)

print("\n[3] ⚠️ NENHUM RÓTULO DECIDE — a garantia estrutural da fase")
# O mesmo evento, com rótulos completamente diferentes, tem de dar o MESMO
# resultado. Se isto quebrar, a "queda por contabilidade" volta.
base = dict(orient="costas")
cats = {pl.decidir_permanencia(ev(label=l, **base), OPOSTA)[0]
        for l in ("operar_torno", "conversando_colega", "acao_indefinida",
                  "rotulo_que_nunca_existiu", "xpto_123")}
check("rótulos diferentes, MESMA categoria", cats == {"valor_agregado"}, cats)
# O caso real que motivou a fase.
c, _n, _m, _e = pl.decidir_permanencia(
    ev(label="acao_indefinida", orient="frente", trabalho=False), OPOSTA)
check("'parado junto ao torno' com rótulo lixo NÃO sai produtivo",
      c == "desperdicio", c)
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
trecho = fonte[fonte.index("def decidir_permanencia"):
               fonte.index("def _montar_placar")]
check("a função não consulta categoria de rótulo nenhuma",
      "cat_por_label" not in trecho and "categoria_lean" not in trecho)

print("\n[4] Rótulo novo não move número nenhum")
antes = pl.decidir_permanencia(ev(label="operar_torno", orient="costas"), OPOSTA)[0]
depois = pl.decidir_permanencia(ev(label="rotulo_nascido_agora", orient="costas"), OPOSTA)[0]
check("nasceu rótulo novo, número igual", antes == depois == "valor_agregado")
check("e a classificação Lean automática está DESLIGADA",
      pl._LEAN_AUTO is False)
check("o mecanismo não foi apagado, só desligado",
      "def classificar_comportamentos_lean" in fonte and "KV_LEAN_AUTO" in fonte)

print("\n[5] Nível 3 — o VLM julga, e `null` NUNCA vira produtivo")
c, n, _m, est = pl.decidir_permanencia(ev(orient="frente", trabalho=True), OPOSTA)
check("trabalho=true → produtivo", c == "valor_agregado" and n == "julgamento")
check("no estado 'outro lado'", est == pl.EST_OUTRO_LADO, est)
c, n, _m, _e = pl.decidir_permanencia(ev(orient="frente", trabalho=False), OPOSTA)
check("trabalho=false → improdutivo", c == "desperdicio" and n == "julgamento")
c, n, m, _e = pl.decidir_permanencia(ev(orient="frente", trabalho=None), OPOSTA)
check("⚠️ trabalho=null NÃO vira produtivo", c != "valor_agregado", c)
check("vira DÚVIDA e vai para a fila", n == "duvida" and "fila" in m, (n, m))
# Sem pose também não afirma nada.
_c, _n, _m, est = pl.decidir_permanencia(ev(orient=None, trabalho=None), OPOSTA)
check("sem pose cai em 'outro lado', não em 'voltado para o torno'",
      est == pl.EST_OUTRO_LADO, est)
_c, _n, _m, est = pl.decidir_permanencia(ev(orient="costas"), None)
check("sem `frente_maquina` configurado, o nível 2 não afirma",
      est == pl.EST_OUTRO_LADO, est)

print("\n[6] Os três estados somam 100% do tempo observado")
casos = [ev(papel="posto_vazio", label="posto_vazio"),
         ev(orient="costas"), ev(orient="frente", trabalho=True),
         ev(orient="frente", trabalho=None), ev(orient=None),
         ev(papel="visitante")]
estados = [pl.estado_permanencia(e, OPOSTA)[0] for e in casos]
check("todo evento cai em exatamente um estado",
      all(x in pl.ESTADOS_PERMANENCIA for x in estados), estados)
check("os três estados existem no vocabulário",
      set(pl.ESTADOS_PERMANENCIA) == {pl.EST_NO_TORNO, pl.EST_OUTRO_LADO, pl.EST_FORA})
check("nenhum caso ficou sem estado", None not in estados)

print("\n[7] Correção humana é INVIOLÁVEL")
c, n, m, _e = pl.decidir_permanencia(
    ev(orient="costas", corrigido="conversando_colega", validado=True), OPOSTA)
check("corrigido por humano → nível humano, mesmo com pose dizendo torno",
      n == pl.NIVEL_HUMANO, n)
check("e a mensagem é em português de gente", "você decidiu" in m, m)
# ⚠️ CONFIRMAR NÃO É APROVAR. "o rótulo está certo" diz que o RÓTULO está
# certo — não que o trecho é produtivo. A primeira versão devolvia
# `valor_agregado` aqui, e o comparativo com o dia real acusou na hora: 41%
# viravam 81%. A categoria vem do rótulo CONFIRMADO.
e_conf = ev(orient="frente", trabalho=False, validado=True, correto=True)
e_conf["_cat_humana"] = "desperdicio"
c, n, _m, _e = pl.decidir_permanencia(e_conf, OPOSTA)
check("confirmação humana vence o julgamento do VLM", n == pl.NIVEL_HUMANO, n)
check("e confirmar 'conversando_colega' MANTÉM improdutivo",
      c == "desperdicio", c)
e_conf2 = dict(e_conf, _cat_humana="valor_agregado")
check("confirmar um rótulo produtivo mantém produtivo",
      pl.decidir_permanencia(e_conf2, OPOSTA)[0] == "valor_agregado")
# E validação MECÂNICA (posto_vazio/auditoria) não é decisão humana.
e_mec = ev(papel="posto_vazio", label="posto_vazio", validado=True, correto=True)
e_mec["origem_validacao"] = "posto_vazio"
c, n, _m, _e = pl.decidir_permanencia(e_mec, OPOSTA)
check("⚠️ validado por MECANISMO não sequestra a precedência humana",
      n == pl.NIVEL_PRESENCA, n)
c, n, _m, _e = pl.decidir_permanencia(ev(orient="costas", validado=True), OPOSTA)
check("validado_humano sem correção nem confirmação NÃO sequestra a decisão",
      n == "orientacao", n)

print("\n[8] Zero chamada de API nova — `trabalho` vai no JSON que já existe")
check("o prompt da sequência pede o campo", '"trabalho"' in fonte)
check("com as três respostas possíveis explicadas",
      "null  = não dá para dizer" in fonte)
check("e proíbe o chute", "nunca chute true" in fonte)
check("o parser aceita só booleano de verdade (string não vira True)",
      'isinstance(t.get("trabalho"), bool)' in fonte)
seq = fonte[fonte.index("def _analisar_sequencia_vlm"):
            fonte.index("def _analisar_sequencia_cam2")]
check("nenhuma chamada NOVA na sequência", seq.count("groq_vision_call") == 1,
      seq.count("groq_vision_call"))

print("\n[9] O julgamento do minuto: maioria, e empate vira dúvida")
def crus(*ts):
    return [({"trabalho": t}, 1.0) for t in ts]
check("maioria true → true", pl._trabalho_do_minuto(crus(True, True, False)) is True)
check("maioria false → false", pl._trabalho_do_minuto(crus(False, False, True)) is False)
check("empate → None (na dúvida, dúvida)",
      pl._trabalho_do_minuto(crus(True, False)) is None)
check("só nulls → None", pl._trabalho_do_minuto(crus(None, None)) is None)
check("sem crus → None", pl._trabalho_do_minuto([]) is None)

print("\n[10] A orientação do minuto é o REGIME, não o instante")
def crusO(*os_):
    return [({"orientacao": o}, 1.0) for o in os_]
check("moda simples", pl._orientacao_do_minuto(crusO("costas", "costas", "frente"))
      == "costas")
check("sem pose nenhuma → None (nunca 'frente' por omissão)",
      pl._orientacao_do_minuto(crusO(None, None)) is None)
check("e a orientação passou a ser PERSISTIDA",
      '"orientacao": e.get("orientacao"),' in fonte)
check("com o registro de que era calculada e jogada fora",
      "JOGADA" in fonte and "terceira vez" in fonte)

print("\n[11] O que foi desligado continua existindo, atrás de flag")
for flag, nome in (("_PERMANENCIA", "KV_PERMANENCIA"),
                   ("_LEAN_AUTO", "KV_LEAN_AUTO"),
                   ("_PARTICAO_CENA", "KV_PARTICAO_CENA"),
                   ("_MOV_INJETAR", "KV_MOVIMENTO_INJETAR")):
    check(f"{nome} existe como chave", nome in fonte and hasattr(pl, flag))
check("a permanência nasce LIGADA (é o padrão novo)", pl._PERMANENCIA is True)
check("a partição de cena continua desligada", pl._PARTICAO_CENA is False)
check("o sensor de movimento continua MEDINDO", pl._MOV_ENABLE is True)
check("mas não decide nada (injeção off)", pl._MOV_INJETAR is False)
check("versão do instrumento bumpada", pl.VERSAO_INSTRUMENTO >= 7,
      pl.VERSAO_INSTRUMENTO)

print("\n[12] O que FICA: descrições, titular, rótulo para agrupar")
check("as descrições continuam sendo geradas", "descricao_bruta" in fonte)
check("a identificação do titular continua", "def identificar_titular_do_dia" in fonte)
check("o cluster continua existindo (agrupa a tela)",
      "def etapa_clusterizar" in fonte)
check("mas o rótulo não entra na decisão",
      "NENHUM RÓTULO ENTRA NA DECISÃO" in fonte)

print("\n[13] ⚠️ A orientação NÃO afirma enquanto não for verificada com dado")
pl._ORIENTACAO_VERIFICADA = False
try:
    _c, _n, _m, est = pl.decidir_permanencia(ev(orient="costas"), OPOSTA)
    check("sem verificação, o nível 2 se abstém",
          est == pl.EST_OUTRO_LADO, est)
    c, n, _m, _e = pl.decidir_permanencia(
        ev(orient="costas", trabalho=None), OPOSTA)
    check("e o minuto cai em dúvida, não em produtivo",
          c != "valor_agregado" and n == "duvida", (c, n))
finally:
    pl._ORIENTACAO_VERIFICADA = True
import os as _os
_amb = "KV_ORIENTACAO_VERIFICADA"
check(f"{_amb} existe como chave", _amb in fonte)
check("e o porquê está escrito (a métrica inteira depende dela)",
      "produtivo e improdutivo trocam de lugar" in fonte)
check("nasce desligada",
      _os.environ.get(_amb, "off") in ("off", "0", "false", "False"))




# ══════════════════════════════════════════════════════════════════════════
# Fase 98 — REAVALIAÇÃO (diagnóstico) e `acao_indefinida` como ESTADO
# ══════════════════════════════════════════════════════════════════════════
print("\n[14] `acao_indefinida` deixa de ser rótulo e vira ESTADO")
check("evento com o rótulo é marcado como sem descrição utilizável",
      pl.sem_descricao_utilizavel({"comportamento_label": "acao_indefinida"}) is True)
check("qualquer outro rótulo não",
      pl.sem_descricao_utilizavel({"comportamento_label": "operar_torno"}) is False)
check("⚠️ correção humana TIRA o evento do estado (alguém disse o que era)",
      pl.sem_descricao_utilizavel({"comportamento_label": "acao_indefinida",
                                   "label_corrigido": "medir_peca"}) is False)
check("não entra na árvore nem no Pareto",
      pl.evento_conta_no_vocabulario(
          {"comportamento_label": "acao_indefinida"}) is False)
check("e é filtrado na distribuição de comportamentos",
      "if l == LABEL_INDEFINIDA:" in fonte and "FORA da árvore e do Pareto" in fonte)
check("nem no top de ações do dia",
      "evento_conta_no_vocabulario(e)" in fonte)

print("\n[15] Vai para a fila — com tipo PRÓPRIO, não misturado")
dv, motivo, tp = pl.evento_em_duvida(
    {"comportamento_label": "acao_indefinida", "n_amostras": 12,
     "confianca": 1.0, "n_rotulos_no_minuto": 1}, 0.7)
check("entra na fila", dv is True)
check("tipo 'sem_descricao' (não 'sem_evidencia')", tp == "sem_descricao", tp)
check("o motivo diz que precisa de olho humano", "olho humano" in motivo, motivo)
# ⚠️ A abstenção CONTINUA existindo — proibi-la faria o modelo chutar.
check("a capacidade de admitir que não soube NÃO foi removida",
      "LABEL_INDEFINIDA" in fonte and "chute confiante é pior" in fonte)

print("\n[16] Reavaliação: DIAGNÓSTICO, e só para o evento corrigido")
check("nasce desligada", pl._REAVALIAR is False)
check("com a flag off não gasta chamada nenhuma",
      pl.reavaliar_correcao(None, {"descricao_bruta": "x"}, ["img"], "y") is None)
_r = pl._REAVALIAR
pl._REAVALIAR = True
try:
    check("sem frame aquecido devolve o motivo, não inventa diagnóstico",
          (pl.reavaliar_correcao(None, {"descricao_bruta": "x"}, [], "y") or {})
          .get("erro", "").startswith("sem frames"))
finally:
    pl._REAVALIAR = _r
trecho_r = fonte[fonte.index("def reavaliar_correcao"):
                 fonte.index("def custo_reavaliacao_usd")]
check("o escopo fica escrito no PRÓPRIO dado",
      "não propaga, não vira vocabulário" in trecho_r)
check("e o porquê está registrado (a Fase 67)",
      "Fase 67" in fonte and "espalhou" in fonte)
check("o prompt pede DIAGNÓSTICO, não reclassificação",
      "DIAGNOSTICAR o próprio erro" in fonte and "não se defender" in fonte)
check("distingue as duas causas que têm consertos opostos",
      "descricao_errada" in fonte and "rotulo_traiu_descricao" in fonte)
check("revisa também `trabalho` (é ele que move o número na Fase 97)",
      '"trabalho": (bool(r["trabalho"])' in trecho_r)
check("e `null` continua `null` na revisão", "else None" in trecho_r)
check("a chave existe", "KV_REAVALIAR_CORRECAO" in fonte)

print("\n[17] Custo por correção — controlado e consultável")
c3 = pl.custo_reavaliacao_usd(3)
c1 = pl.custo_reavaliacao_usd(1)
check(f"3 imagens: US$ {c3:.5f}", 0.002 < c3 < 0.006, c3)
check(f"1 imagem:  US$ {c1:.5f}", c1 < c3, (c1, c3))
check("100 correções ficam abaixo de US$ 0,50", c3 * 100 < 0.5, c3 * 100)
main = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "backend", "main.py"), encoding="utf-8").read()
check("há endpoint para consultar o custo antes de ligar",
      "/reavaliacao/custo" in main)
check("só dispara em correção INDIVIDUAL, nunca em lote",
      'if body.acao == "corrigir" and _lc:' in main
      and "_reavaliar_evento" not in main.split("def validar_lote")[1])
check("não pede ao Storage o que não existe (Fase 93)",
      "_nomes_no_prefixo(sb, bucket, _prefixo_frames(caminho))" in main)
check("e é NÃO-FATAL: a correção já está gravada",
      "não-fatal: %s" in main)
sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sql", "schema.sql"), encoding="utf-8").read()
check("a coluna existe", "add column if not exists reavaliacao" in sql)




# ══════════════════════════════════════════════════════════════════════════
# Fase 99 — O SUFIXO DE ESTADO NÃO PODE NASCER. POR CONSTRUÇÃO.
#
# A Fase 88 desligou a partição e o sufixo DUPLO morreu em 07/08. Mas cinco
# rótulos continuaram nascendo — 896 eventos desde 12/08 — porque tinham
# entrado no VOCABULÁRIO CANÔNICO enquanto a partição existia, e o prompt do
# cluster manda REUSAR label canônico. O rótulo afirmava "máquina parada" e
# nada mediu isso: a Fase 89 provou que o estado que o VLM afirmava trocava
# como moeda entre minutos consecutivos.
# ══════════════════════════════════════════════════════════════════════════
print("\n[18] A guarda estrutural: sufixo de estado não vira rótulo")
for antes, depois in [
    ("monitorar_maquina_parada", "monitorar_maquina"),
    ("operar_torno_parada", "operar_torno"),
    ("operar_torno_ciclo", "operar_torno"),
    ("conversando_colega_parada", "conversando_colega"),
    ("monitorar_maquina_ciclo", "monitorar_maquina"),
    # os de sufixo duplo do histórico
    ("monitorar_maquina_parada_parada", "monitorar_maquina"),
    ("operar_torno_ciclo_ciclo", "operar_torno"),
    ("conversando_colega_parada_imovel", "conversando_colega"),
    # variações de grafia que o modelo pode inventar
    ("medir_peca_parado", "medir_peca"),
    ("ajustar_maquina_imovel", "ajustar_maquina"),
]:
    check(f"{antes} → {depois}", pl.limpar_sufixo_estado(antes) == depois,
          pl.limpar_sufixo_estado(antes))
check("rótulo sem sufixo passa intacto",
      pl.limpar_sufixo_estado("operar_torno") == "operar_torno")
check("⚠️ nunca esvazia o rótulo", pl.limpar_sufixo_estado("_parada") == "_parada")
check("vazio e None não quebram",
      pl.limpar_sufixo_estado("") == "" and pl.limpar_sufixo_estado(None) == "")
check("e há um detector explícito",
      pl.rotulo_afirma_estado("operar_torno_parada") is True
      and pl.rotulo_afirma_estado("operar_torno") is False)

print("\n[19] Os cinco saem do VOCABULÁRIO sugerido ao modelo")
vocab = [{"label": "operar_torno", "descricao": "a"},
         {"label": "monitorar_maquina_parada", "descricao": "b"},
         {"label": "operar_torno_ciclo", "descricao": "c"},
         {"label": "medir_peca", "descricao": "d"},
         {"label": "conversando_colega_parada", "descricao": "e"}]
limpo = [v["label"] for v in pl.vocabulario_sem_estado(vocab)]
check("só sobram os sem estado", limpo == ["operar_torno", "medir_peca"], limpo)
check("os cinco estão na lista de banidos",
      {"monitorar_maquina_parada", "operar_torno_parada", "operar_torno_ciclo",
       "conversando_colega_parada", "monitorar_maquina_ciclo"}
      <= pl.ROTULOS_BANIDOS_DO_VOCABULARIO)
bloco = pl.construir_bloco_memoria_cluster({"vocabulario": vocab})
check("o PROMPT do cluster não sugere nenhum deles",
      "monitorar_maquina_parada" not in bloco and "operar_torno_ciclo" not in bloco,
      bloco[:200])
check("mas continua sugerindo os bons", "operar_torno" in bloco)
bloco_vlm = pl.construir_bloco_vocabulario({"vocabulario": vocab})
check("e o prompt do VLM também não",
      "monitorar_maquina_parada" not in bloco_vlm)

print("\n[20] O CACHE do cluster limpa o histórico ao ler")
# Sem isto acontecia o pior dos dois mundos: o rótulo com sufixo não casava na
# checagem, a descrição ia ao LLM de novo, e o LLM a devolvia com sufixo —
# o cache PAGAVA uma chamada para reintroduzir o resíduo.
check("a leitura do cache limpa o sufixo",
      "limpar_sufixo_estado((l.get(\"comportamento_label\")" in fonte)
check("e o uso do cache também",
      'em_cache = limpar_sufixo_estado(_cache.get(d_lower))' in fonte)
# O comentário quebra em duas linhas no fonte — casa pelo pedaço que sobrevive.
check("com o porquê escrito (pagava chamada para reintroduzir)",
      "ele PAGAVA uma" in fonte and "chamada para reintroduzi-lo" in fonte,
      "o comentário do cache sumiu")

print("\n[21] O prompt para de PEDIR e de ACEITAR estado da máquina")
check('o campo "maquina" saiu do JSON pedido',
      '"maquina": "ciclo" se a máquina' not in fonte)
check("e do exemplo de resposta", '"maquina": "ciclo", "imovel"' not in fonte)
check("da sequência E do prompt da cam2",
      fonte.count("NÃO AFIRME O ESTADO DA MÁQUINA") >= 2)
check("os EXEMPLOS não ensinam mais a afirmar estado",
      '"parado junto ao torno, máquina em ciclo"' not in fonte
      and '"parado ao lado do torno, máquina parada"' not in fonte)
check("a REGRA que mandava descrever a máquina virou proibição",
      "Diga também o que a MÁQUINA está fazendo" not in fonte
      and "NÃO AFIRME O ESTADO DA MÁQUINA" in fonte)
check("e o porquê está no prompt (foi medido, trocava como moeda)",
      "trocava como cara ou coroa" in fonte)
check("`imovel` continua sendo pedido (esse a pose mede)",
      '"imovel": true se a pessoa está na MESMA posição' in fonte)

print("\n[22] A correção humana também não reintroduz o sufixo")
main2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "main.py"), encoding="utf-8").read()
check("o endpoint de correção limpa o rótulo escolhido",
      'novo = limpar_sufixo_estado((label_corrigido or "").strip())' in main2)
check("com o porquê (a tela oferece o histórico)",
      "reintroduziria `monitorar_maquina_parada` de boa-fé" in main2)

print("\n[22b] Fase 99 · O RESTO DO RESÍDUO — o que sobrou da partição")
# (a) O catálogo. Descrevia o estado em prosa; como as duas cenas colapsam no
# mesmo label, a última partição a rodar sobrescrevia o texto e um rótulo que
# cobre ciclo E parada era descrito ao gestor como parada.
check("a descrição do catálogo não anexa mais estado — nem com a flag ligada",
      pl._descricao_com_cena("operador junto ao torno", "parada", True)
      == pl._descricao_com_cena("operador junto ao torno", "ciclo", False)
      == "operador junto ao torno")
_part = pl._PARTICAO_CENA
pl._PARTICAO_CENA = True
try:
    check("e nem quando KV_PARTICAO_CENA volta a valer",
          pl._descricao_com_cena("x", "parada", True) == "x")
finally:
    pl._PARTICAO_CENA = _part

# (b) Parar de PEDIR era metade; a outra metade é parar de ACEITAR. Sem isto o
# modelo podia voluntariar `"maquina": "ciclo"` contra a proibição e o valor ia
# direto para a coluna `cena_maquina` — a mesma afirmação não medida, fora do
# nome.
check("o parser NUNCA aceita estado de máquina do VLM",
      pl._maquina_do_vlm({"maquina": "ciclo"}) is None
      and pl._maquina_do_vlm({"maquina": "parada"}) is None
      and pl._maquina_do_vlm({}) is None)
check("mas a imobilidade da PESSOA continua sendo lida — ela é observável",
      '"imovel": bool(t.get("imovel"))' in fonte)
check("e o descarte é LOGADO, para a violação da proibição aparecer",
      "o modelo afirmou estado da máquina" in fonte)
check("os dois pontos de parse usam a guarda, não o normalizador cru",
      fonte.count('"maquina": _maquina_do_vlm(t)') == 2
      and '"maquina": _normalizar_maquina(t.get("maquina"))' not in fonte)

# (c) O cache do cluster exigia que a cena batesse com o sufixo do label
# guardado. Com a leitura limpando o sufixo, ele nunca mais batia: com a
# partição ligada, o sistema PAGAVA uma chamada por frase já conhecida.
check("o cache não condiciona mais o reuso à cena",
      "em_cache = limpar_sufixo_estado" in fonte
      and "if em_cache:" in fonte
      and "em_cache == familia_label(em_cache) + sufixo_cena" not in fonte)

# (d) A camada de contradição do sensor exige o VLM afirmando `ciclo`. Ele
# parou de afirmar, então ela não dispara mais — e isso está escrito.
check("a camada sensor×ciclo está documentada como não-disparável",
      "ESTA CAMADA NÃO DISPARA MAIS" in fonte)
check("e o veto de fato não dispara sem afirmação de ciclo",
      pl.veto_movimento("ausente", {"pct_zona_ocupada": 1, "contraste": 99},
                        pl._maquina_do_vlm({"maquina": "ciclo"}), "parado") is None)

# (e) A reavaliação diagnosticava sobre o texto CRU da correção — um rótulo que
# não existe no banco, porque o update grava a versão limpa.
check("a reavaliação recebe o MESMO rótulo que foi gravado",
      "_lc = limpar_sufixo_estado(body.label_corrigido" in main2
      and "_reavaliar_evento(sb, user.empresa, evento_id, _lc)" in main2)

# (f) A tela oferecia o histórico como opção de correção. O backend limpava em
# silêncio: o gestor escolhia uma coisa, o banco gravava outra.
rot = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "frontend", "src", "design", "rotulos.ts"),
           encoding="utf-8").read()
check("o front expõe o teste de afirmação de estado", "export function afirmaEstado" in rot)
check("e a lista de ESCOLHA filtra por ele", "export function rotulosAtribuiveis" in rot)
for _tela in ("Validacao", "Eventos"):
    _t = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "frontend", "src", "pages", f"{_tela}.tsx"),
              encoding="utf-8").read()
    check(f"{_tela} não oferece rótulo com estado como correção",
          "rotulosAtribuiveis(" in _t)
check("mas a LEITURA do histórico continua intacta (nomeHumano/familiaLabel)",
      "export function familiaLabel" in rot and "export function nomeHumano" in rot)

print("\n[23] Histórico NÃO é renomeado")
check("nenhum UPDATE em massa de comportamento_label",
      "comportamento_label" not in main2.split("def _montar_update_validacao")[0]
      or "update({\"comportamento_label\"" not in main2)
check("e a família continua unindo o histórico na leitura",
      pl.familia_label("monitorar_maquina_parada") == "monitorar_maquina")

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
