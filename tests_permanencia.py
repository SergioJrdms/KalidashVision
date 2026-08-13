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
      'if body.acao == "corrigir" and body.label_corrigido:' in main
      and "_reavaliar_evento" not in main.split("def validar_lote")[1])
check("não pede ao Storage o que não existe (Fase 93)",
      "_nomes_no_prefixo(sb, bucket, _prefixo_frames(caminho))" in main)
check("e é NÃO-FATAL: a correção já está gravada",
      "não-fatal: %s" in main)
sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sql", "schema.sql"), encoding="utf-8").read()
check("a coluna existe", "add column if not exists reavaliacao" in sql)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
