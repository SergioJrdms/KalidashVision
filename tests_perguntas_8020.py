"""Fase 105 — A CONVERSA RÁPIDA EM 80/20: poucas perguntas, e só as que decidem.

O relato: *"já são 200 mensagens e ninguém responde isso... quero enviar
somente perguntas impactantes de vdd, que resolvam dúvidas reais. Poucas
perguntas, não pode ultrapassar de 10 por dia ou semana."*

A CAUSA NÃO ERA A IA PERGUNTAR MAL — era ONDE ela era chamada.
`gerar_perguntas_processo` roda a cada VÍDEO PROCESSADO, com teto de 4. O
runner da borda sobe dezenas de segmentos por dia: 4 × dezenas = centenas por
semana. O único freio era o dedupe por Jaccard, que barra o texto quase igual
e deixa passar qualquer reformulação. Ninguém nunca olhou a fila antes de
escrever mais uma.

Duas travas, nesta ordem:

  1. ORÇAMENTO, determinístico e ANTES do token. O teto que mais importa é o de
     perguntas ABERTAS: com a fila cheia, a cota é ZERO e nada nasce até o
     gestor responder. Autorregula sem cron e sem limpeza.

  2. IMPACTO MEDIDO. Uma pergunta vale o quanto de TEMPO OBSERVADO ela decide.
     "Limpar cavaco acontece durante o ciclo?" sobre um rótulo de 0,3% do dia
     não muda número nenhum.

⚠️ O PONTO MAIS IMPORTANTE DESTA SUÍTE: pergunta SEM comportamento relacionado
vale ZERO, não "impacto desconhecido". É a quinta vez que o projeto tropeça no
mesmo padrão — bbox (0,0,0,0), MAD=0, share=1.00, acao_indefinida — AUSÊNCIA DE
MEDIDA VIRANDO MEDIDA. Aqui ela é barrada por desenho, e este arquivo existe
para que continue barrada.

Rodar:  python tests_perguntas_8020.py
"""
import os, sys, types
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
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

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


class FakeQ:
    def __init__(self, sb, tabela):
        self.sb, self.tabela = sb, tabela
        self.eqs, self.ins, self.faixa, self.ordem = {}, {}, None, None

    def select(self, *a, **k): return self
    def order(self, c=None, **k): self.ordem = c; return self
    def limit(self, n, **k): self.faixa = (0, n - 1); return self
    def range(self, a, b): self.faixa = (a, b); return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def in_(self, c, vs): self.ins[c] = list(vs); return self

    def execute(self):
        if self.sb.quebrar and self.tabela in self.sb.quebrar:
            raise RuntimeError("PostgREST fora do ar")
        r = [dict(l) for l in self.sb.dados.get(self.tabela, [])
             if all(l.get(c) == v for c, v in self.eqs.items())
             and all(l.get(c) in vs for c, vs in self.ins.items())]
        if self.ordem:
            r.sort(key=lambda x: str(x.get(self.ordem)))
        if self.faixa is not None:
            a, b = self.faixa
            r = r[a: b + 1]
        else:
            r = r[: pl.TETO_POSTGREST]
        return types.SimpleNamespace(data=r)


class FakeSB:
    def __init__(self, dados, quebrar=None):
        self.dados, self.escritas, self.quebrar = dados, [], quebrar or set()

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome)
            def update(self, p):
                sb.escritas.append(("update", nome, p)); return FakeQ(sb, nome)
            def insert(self, p):
                sb.escritas.append(("insert", nome, p)); return FakeQ(sb, nome)
            def upsert(self, p, **k):
                sb.escritas.append(("upsert", nome, p)); return FakeQ(sb, nome)
            def delete(self):
                sb.escritas.append(("delete", nome, None)); return FakeQ(sb, nome)
        return T()


AGORA = datetime.now(timezone.utc)


def perg(i, *, status="pendente", horas=1.0, rel=None, impacto=None,
         texto=None):
    return {"id": f"p{i:03d}", "empresa": "U", "processo": "T",
            "pergunta": texto or f"pergunta número {i} sobre a operação?",
            "motivo": "m", "comportamentos_relacionados": rel if rel is not None else [],
            "respostas_rapidas": None, "status": status, "resposta": None,
            "respondida_em": None, "impacto_pct": impacto,
            "criada_em": (AGORA - timedelta(hours=horas)).isoformat()}


def sb_com(perguntas, eventos=None, quebrar=None):
    return FakeSB({"perguntas_processo": list(perguntas),
                   "eventos": list(eventos or [])}, quebrar=quebrar)


# ══════════════════ [1] O ORÇAMENTO — a trava que faltava ══════════════
print("\n[1] O orçamento: a fila cheia CALA o sistema")

check("os tetos são os pedidos: 10 por semana", pl.PERG_MAX_SEMANA == 10)
check("e poucas abertas de cada vez", pl.PERG_MAX_ABERTAS == 3)
check("com teto diário também", pl.PERG_MAX_DIA == 3)

vazio = pl.orcamento_de_perguntas(sb_com([]), "U", "T")
check("fila vazia libera cota", vazio["vagas"] == pl.PERG_MAX_ABERTAS, vazio)

# ⭐ O caso que gerou o relato: 200 abertas.
duzentas = pl.orcamento_de_perguntas(
    sb_com([perg(i, horas=24 * 30) for i in range(200)]), "U", "T")
check("⭐ com 200 abertas a cota é ZERO", duzentas["vagas"] == 0, duzentas)
check("e o motivo diz o número, não 'limite atingido'",
      "200" in duzentas["motivo"] and "sem resposta" in duzentas["motivo"],
      duzentas["motivo"])

# Responder libera de novo — é isso que faz a trava se autorregular.
respondidas = [perg(i, status="respondida", horas=24 * 30) for i in range(200)]
check("responder tudo devolve a cota",
      pl.orcamento_de_perguntas(sb_com(respondidas), "U", "T")["vagas"] > 0)
check("dispensar também",
      pl.orcamento_de_perguntas(
          sb_com([perg(i, status="dispensada", horas=24 * 30) for i in range(200)]),
          "U", "T")["vagas"] > 0)

# Teto por dia e por semana, com a fila aberta pequena.
hoje = [perg(i, status="respondida", horas=2) for i in range(pl.PERG_MAX_DIA)]
o = pl.orcamento_de_perguntas(sb_com(hoje), "U", "T")
check("o teto DIÁRIO segura mesmo com a fila respondida", o["vagas"] == 0, o)
check("e o motivo aponta o dia", "por dia" in o["motivo"], o["motivo"])

semana = [perg(i, status="respondida", horas=24 * 3) for i in range(pl.PERG_MAX_SEMANA)]
o = pl.orcamento_de_perguntas(sb_com(semana), "U", "T")
check("o teto SEMANAL segura o acúmulo de dias", o["vagas"] == 0, o)
check("e o motivo aponta a semana", "por semana" in o["motivo"], o["motivo"])
check("pergunta de 8 dias atrás não conta mais na semana",
      pl.orcamento_de_perguntas(
          sb_com([perg(i, status="respondida", horas=24 * 8)
                  for i in range(pl.PERG_MAX_SEMANA)]), "U", "T")["vagas"] > 0)

# ⭐ Fail-closed.
falho = pl.orcamento_de_perguntas(
    sb_com([], quebrar={"perguntas_processo"}), "U", "T")
check("⭐ se não dá para CONTAR, a cota é zero (não liberdade)",
      falho["vagas"] == 0, falho)
check("e ele diz que não conseguiu ler", "não foi possível" in falho["motivo"])


# ══════════════════ [2] Nenhum token gasto sem vaga ════════════════════
print("\n[2] O gate roda ANTES do Groq")


class GroqEspiao:
    def __init__(self): self.chamadas = 0


_chamou = {"n": 0}
_orig = pl.groq_text_call
pl.groq_text_call = lambda *a, **k: (_chamou.__setitem__("n", _chamou["n"] + 1)
                                     or '{"perguntas": []}')
try:
    r = pl.gerar_perguntas_processo(
        sb_com([perg(i) for i in range(50)]), GroqEspiao(), "U", "T")
    check("⭐ com a fila cheia, ZERO chamada ao Groq", _chamou["n"] == 0)
    check("e devolve lista vazia sem erro", r == [])
finally:
    pl.groq_text_call = _orig


# ══════════════════ [3] O impacto: medido, nunca suposto ═══════════════
print("\n[3] O 80/20 — quanto do tempo a pergunta decide")

PCT = {"operar_torno": 42.0, "monitorar_maquina": 18.0,
       "limpando_cavaco": 0.3, "organizar_bancada": 1.1}

check("⭐ SEM comportamento relacionado o impacto é 0, não 'desconhecido'",
      pl.impacto_da_pergunta([], PCT) == 0.0)
check("⭐ com rótulo INVENTADO também é 0 (não dá para provar que decide nada)",
      pl.impacto_da_pergunta(["rotulo_que_nao_existe"], PCT) == 0.0)
check("um rótulo pesado vale o tempo dele",
      pl.impacto_da_pergunta(["monitorar_maquina"], PCT) == 18.0)
check("dois rótulos somam", pl.impacto_da_pergunta(
    ["operar_torno", "monitorar_maquina"], PCT) == 60.0)
check("o mesmo rótulo repetido NÃO conta duas vezes",
      pl.impacto_da_pergunta(["operar_torno", "Operar_Torno"], PCT) == 42.0)
check("nunca passa de 100%", pl.impacto_da_pergunta(
    ["operar_torno", "monitorar_maquina", "limpando_cavaco",
     "organizar_bancada"], PCT) <= 100.0)

check("⭐ a pergunta do relato (limpar cavaco + organizar bancada) fica ABAIXO do corte",
      pl.impacto_da_pergunta(["limpando_cavaco", "organizar_bancada"], PCT)
      < pl.PERG_IMPACTO_MIN,
      pl.impacto_da_pergunta(["limpando_cavaco", "organizar_bancada"], PCT))
check("e a pergunta que separa trabalho de espera fica ACIMA",
      pl.impacto_da_pergunta(["monitorar_maquina"], PCT) >= pl.PERG_IMPACTO_MIN)

# Família: o cluster cria variantes e a pergunta cita a raiz.
FAM = {"monitorar_maquina_ciclo": 9.0, "monitorar_maquina_parada": 7.0}
check("a raiz soma as variantes da família",
      pl.impacto_da_pergunta(["monitorar_maquina"], FAM) == 16.0)
check("e a variante encontra a raiz quando é a raiz que está medida",
      pl.impacto_da_pergunta(["operar_torno_fino"], PCT) == 42.0)

check("o corte é configurável e tem padrão", pl.PERG_IMPACTO_MIN > 0)
check("a distribuição vira mapa de %",
      pl.pct_por_comportamento({"distribuicao_comportamentos": [
          {"comportamento": "operar_torno", "pct_do_tempo_observado": 42.0}]})
      == {"operar_torno": 42.0})
check("contexto ausente não quebra", pl.pct_por_comportamento(None) == {})


# ══════════════════ [4] O filtro na geração ════════════════════════════
print("\n[4] A geração descarta o fraco e guarda o forte")

RESP = ('{"perguntas": ['
        '{"pergunta": "Limpar cavaco acontece durante o ciclo automatico?",'
        ' "motivo": "m", "comportamentos_relacionados": ["limpando_cavaco"],'
        ' "respostas_rapidas": ["Durante o ciclo", "So depois", "Depende"]},'
        '{"pergunta": "Parado de frente pro torno enquanto ele corta e trabalho ou espera?",'
        ' "motivo": "m", "comportamentos_relacionados": ["monitorar_maquina"],'
        ' "respostas_rapidas": ["E trabalho dele", "E espera", "Depende da peca"]},'
        '{"pergunta": "Como voces chamam a etapa final do acabamento da peca?",'
        ' "motivo": "m", "comportamentos_relacionados": [],'
        ' "respostas_rapidas": ["Acabamento", "Rebarba", "Polimento"]}'
        ']}')
CTX = {"distribuicao_comportamentos": [
    {"comportamento": k, "pct_do_tempo_observado": v,
     "descricao": k, "ocorrencias_totais": 10} for k, v in PCT.items()]}

pl.groq_text_call = lambda *a, **k: RESP
try:
    SB = sb_com([])
    r = pl.gerar_perguntas_processo(SB, GroqEspiao(), "U", "T",
                                    contexto_agregado=CTX)
    inseridas = [w for w in SB.escritas if w[0] == "insert"]
    linhas = inseridas[0][2] if inseridas else []
    textos = " | ".join(l["pergunta"] for l in linhas)
    check("⭐ a pergunta de 0,3% do dia NÃO foi gravada",
          "cavaco" not in textos.lower(), textos)
    check("⭐ a pergunta sem rótulo relacionado NÃO foi gravada",
          "chamam" not in textos.lower(), textos)
    check("⭐ a que decide 18% do dia FOI gravada",
          "espera" in textos.lower(), textos)
    check("e o impacto ficou gravado junto",
          linhas and linhas[0].get("impacto_pct") == 18.0, linhas)
    check("nasce pendente", all(l["status"] == "pendente" for l in linhas))
finally:
    pl.groq_text_call = _orig

# Ordenação por impacto quando há mais candidatas que vagas.
MUITAS = ('{"perguntas": ['
          '{"pergunta": "pergunta media sobre o monitoramento da maquina?",'
          ' "motivo": "m", "comportamentos_relacionados": ["monitorar_maquina"],'
          ' "respostas_rapidas": ["a", "bb", "ccc"]},'
          '{"pergunta": "outra bem diferente sobre operar o torno pesado?",'
          ' "motivo": "m", "comportamentos_relacionados": ["operar_torno"],'
          ' "respostas_rapidas": ["a", "bb", "ccc"]}'
          ']}')
pl.groq_text_call = lambda *a, **k: MUITAS
try:
    # Uma vaga só: duas respondidas hoje deixam PERG_MAX_DIA - 2 = 1.
    SB = sb_com([perg(1, status="respondida", horas=1),
                 perg(2, status="respondida", horas=1)])
    pl.gerar_perguntas_processo(SB, GroqEspiao(), "U", "T",
                               contexto_agregado=CTX)
    linhas = [w for w in SB.escritas if w[0] == "insert"][0][2]
    check("⭐ com 1 vaga, guarda a de MAIOR impacto (42%, não a primeira da lista)",
          len(linhas) == 1 and linhas[0]["impacto_pct"] == 42.0, linhas)
finally:
    pl.groq_text_call = _orig


# ══════════════════ [5] O prompt carrega a regra ═══════════════════════
print("\n[5] O prompt: 80/20 escrito, e o teto como TETO")

P = pl.PROMPT_PERGUNTAS
check("a regra do 80/20 manda em todas as outras", "80/20" in P)
check("pergunta só o que muda um NÚMERO ou uma CLASSIFICAÇÃO",
      "muda um NÚMERO ou uma CLASSIFICAÇÃO" in P)
check("os três testes estão escritos (tempo, decisão, só o cliente sabe)",
      "1. TEMPO:" in P and "2. DECISÃO:" in P and "3. SÓ O CLIENTE SABE:" in P)
check("⭐ o exemplo do relato está no prompt como o que NÃO perguntar",
      "Limpar cavaco" in P and "✗" in P)
check("exige rótulo relacionado, sob pena de descarte",
      "DESCARTADA automaticamente" in P)
check("o teto é TETO, não meta", "é um TETO, não uma meta" in P)
check("lista vazia continua sendo resposta legítima", "lista VAZIA" in P)

SRC = open(os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read()
check("o bloco de lacunas é ordenado e cortado por TEMPO",
      "ORDENADO E CORTADO POR TEMPO" in SRC
      and "Frequência não é peso" in SRC)
check("o motivo de tudo isso está escrito no código",
      "AUSÊNCIA DE\n#     MEDIDA VIRANDO MEDIDA" in SRC
      and "roda a cada VÍDEO PROCESSADO" in SRC)


# ══════════════════ [6] A fila de 200 que já existe ════════════════════
print("\n[6] O acervo: reordenar pelo impacto, arquivar sem apagar")


def evt(i, label, dur):
    return {"id": f"e{i}", "empresa": "U", "processo": "T",
            "comportamento_label": label, "label_corrigido": None,
            "tempo_inicio_s": 0, "tempo_fim_s": dur,
            "validacao_correto": None, "principal": True}


# 42 / 18 / 39 minutos de trabalho + 1 min de cavaco = 100 min.
# ⚠️ De propósito, `limpando_cavaco` tem MUITAS ocorrências e pouquíssimo
# tempo: é o formato exato da armadilha. Frequência não é peso, e o ranking
# tem de olhar o tempo.
EVS = ([evt(i, "operar_torno", 60) for i in range(42)]
       + [evt(100 + i, "monitorar_maquina", 60) for i in range(18)]
       + [evt(200 + i, "outros_trabalhos", 60) for i in range(39)]
       + [evt(300 + i, "limpando_cavaco", 3) for i in range(20)])

medido = pl.pct_por_label_do_processo(sb_com([], EVS), "U", "T")
check("o tempo por rótulo é lido do banco",
      round(medido["operar_torno"]) == 42 and round(medido["monitorar_maquina"]) == 18,
      medido)

ACERVO = [
    perg(1, rel=["limpando_cavaco"], horas=1,
         texto="Limpar cavaco acontece durante o ciclo?"),
    perg(2, rel=[], horas=2, texto="Como voces chamam essa etapa?"),
    perg(3, rel=["monitorar_maquina"], horas=99,
         texto="Parado de frente pro torno e trabalho ou espera?"),
    perg(4, rel=["operar_torno"], horas=98,
         texto="O desbaste e o acabamento sao a mesma operacao?"),
]
r = pl.priorizar_perguntas_abertas(sb_com(ACERVO, EVS), "U", "T")
check("as 4 abertas foram lidas", r["abertas"] == 4)
check("⭐ a de MAIOR impacto vem primeiro, mesmo sendo a mais VELHA",
      r["valem"][0]["id"] == "p004", [x["id"] for x in r["valem"]])
check("a segunda é a de 18%", r["valem"][1]["id"] == "p003")
check("⭐ 20 OCORRÊNCIAS de cavaco não salvam 1% de tempo — frequência não é peso",
      "p001" in [x["id"] for x in r["abaixo_do_corte"]],
      [(x["id"], x["impacto_pct"]) for x in r["abaixo_do_corte"]])
check("tudo que sobrou na fila passa do corte",
      all(x["impacto_pct"] >= pl.PERG_IMPACTO_MIN for x in r["valem"]))
check("⭐ a pergunta SEM rótulo cai abaixo do corte",
      "p002" in [x["id"] for x in r["abaixo_do_corte"]],
      [x["id"] for x in r["abaixo_do_corte"]])
check("o topo pode ser limitado",
      len(pl.priorizar_perguntas_abertas(sb_com(ACERVO, EVS), "U", "T",
                                         topo=1)["valem"]) == 1)
check("respondidas não entram na fila",
      pl.priorizar_perguntas_abertas(
          sb_com([perg(9, status="respondida", rel=["operar_torno"])], EVS),
          "U", "T")["abertas"] == 0)

SB = sb_com(ACERVO, EVS)
arq = pl.arquivar_perguntas_de_baixo_impacto(SB, "U", "T", manter=1)
check("arquivou o resto", arq["arquivadas"] == 3, arq)
check("e manteve a de maior impacto", arq["mantidas"] == 1
      and "desbaste" in arq["mantidas_texto"][0], arq)
check("⭐ NÃO APAGA: só muda status para `dispensada`",
      all(w[0] != "delete" for w in SB.escritas)
      and all(w[2] == {"status": "dispensada"}
              for w in SB.escritas if w[0] == "update"), SB.escritas)
check("é ação explícita — nada a chama sozinho no pipeline",
      SRC.count("arquivar_perguntas_de_baixo_impacto(") == 1)

MAIN = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()
check("existe rota para a fila priorizada",
      "/perguntas/prioritarias" in MAIN)
check("e para o arquivamento, por POST explícito",
      "/perguntas/arquivar-baixo-impacto" in MAIN
      and "@app.post" in MAIN.split("arquivar_perguntas_fracas")[0][-400:])
check("a contagem passa o orçamento adiante (explica o silêncio)",
      '"vagas": orc.get("vagas")' in MAIN and "teto_semana" in MAIN)


# ══════════════════ [7] A tela ═════════════════════════════════════════
print("\n[7] A tela: poucas, escolhidas, e o motivo à vista")

V = open(os.path.join(RAIZ, "frontend", "src", "pages", "Validacao.tsx"),
         encoding="utf-8").read()
render = "\n".join(l for l in V.splitlines()
                   if not l.strip().startswith(("//", "*", "/*", "{/*")))
check("⭐ a tela pede as PRIORITÁRIAS, não a lista crua",
      "api.perguntas.prioritarias(proc.id, 3)" in render
      and 'api.perguntas.listar(proc.id, "pendente")' not in render)
check("⭐ some o crachá de '200 abertas'",
      "aberta{abertas.length > 1" not in render)
check("o subtítulo diz o critério", "Só o que muda o número" in render)
check("⭐ a pergunta mostra QUANTO a resposta reclassifica",
      "reclassifica" in render and "q.impacto" in render)
check("as engavetadas são declaradas, não escondidas em silêncio",
      "ficaram de fora" in render and "engavetadas" in render)
ADAPT = open(os.path.join(RAIZ, "frontend", "src", "lib", "adapt.ts"),
             encoding="utf-8").read()
check("o adaptador carrega o impacto", "impacto_pct" in ADAPT)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
