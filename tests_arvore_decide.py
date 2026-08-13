"""Fase 95 — a árvore DECIDE produtivo/improdutivo. Atrás de flag.

O PROBLEMA QUE ELA RESOLVE
A produtividade vinha do NOME que o VLM dá à ação, e quase todo nome que ele
dá é produtivo. O teto de 75-80% não era resultado: era o único resultado
possível. Um instrumento que só pode dizer "sim" não está medindo.

A INVERSÃO
Sinal determinístico decide; o rótulo vira último recurso.
  1 presenca · 2 movimento · 3 manual · 4 rotulo

PRECEDÊNCIA ABSOLUTA, e é o que esta suíte protege mais:
  correção HUMANA > sinal DETERMINÍSTICO > rótulo do VLM

⚠️ O nível 3 NÃO é "mãos na máquina" — é `modo_operacao == 'manual'`, que
exige a medição ter ficado cega POR CAUSA das mãos. Mão apoiada em máquina
visivelmente parada não é trabalho, e aceitá-la reintroduziria exatamente o
viés que a árvore existe para eliminar.

Rodar:  python tests_arvore_decide.py
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

# Fase 97: a produtividade passou a vir da PERMANÊNCIA. O sujeito desta suíte
# é outro (o bloco da jornada / a árvore da Fase 95), e os dois mecanismos
# coexistem atrás de flag — então ela roda no caminho que está testando.
pl._PERMANENCIA = False

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


def ev(label="operar_torno", papel="operador", mov=None, modo=None,
       corrigido=None, validado=False, correto=None, ini=0, fim=60):
    return {"comportamento_label": label, "label_corrigido": corrigido,
            "papel_pessoa": papel, "movimento_maquina": mov,
            "modo_operacao": modo, "validado_humano": validado,
            "validacao_correto": correto, "principal": True,
            "tempo_inicio_s": ini, "tempo_fim_s": fim}


print("\n[1] Nível 1 — presença. Ninguém no posto é IMPRODUTIVO, e ponto")
c, n, m, _ = pl.arvore_decidir(ev(label="posto_vazio", papel="posto_vazio"), None)
check("categoria desperdicio", c == "desperdicio", c)
check("nível 'presenca'", n == pl.NIVEL_PRESENCA, n)
check("o motivo cita zona e rastreamento", "zona" in m and "rastreamento" in m, m)
# E vence o rótulo: mesmo que o rótulo fosse produtivo.
c2, n2, _, _ = pl.arvore_decidir(ev(label="posto_vazio", papel="posto_vazio"),
                                 "valor_agregado")
check("presença vence rótulo produtivo", c2 == "desperdicio" and n2 == pl.NIVEL_PRESENCA)

print("\n[2] Nível 2 — o sensor vê a máquina, e isso vence o rótulo")
for mov in ("continuo", "intermitente"):
    c, n, m, _ = pl.arvore_decidir(ev(mov=mov), None)
    check(f"'{mov}' → produtivo", c == "valor_agregado" and n == pl.NIVEL_MOVIMENTO)
# O caso que o dono citou: rótulo diz conversa, sensor diz que a máquina roda.
c, n, m, _ = pl.arvore_decidir(ev(label="conversando_colega", mov="continuo"),
                               "desperdicio")
check("rótulo 'conversando_colega' + máquina rodando → PRODUTIVO",
      c == "valor_agregado" and n == pl.NIVEL_MOVIMENTO, (c, n))
check("o motivo diz o que o sensor viu", "sensor viu a máquina" in m, m)

print("\n[3] Nível 3 — operação MANUAL, e só ela")
c, n, _, _ = pl.arvore_decidir(ev(modo="manual"), None)
check("modo 'manual' → produtivo", c == "valor_agregado" and n == pl.NIVEL_MANUAL)
# ⚠️ O que a árvore NÃO aceita: mão apoiada em máquina visivelmente parada.
c, n, _, _ = pl.arvore_decidir(ev(modo="parado"), "valor_agregado")
check("modo 'parado' NÃO vira nível 3 (mão apoiada não é trabalho)",
      n == pl.NIVEL_ROTULO, n)
c, n, _, _ = pl.arvore_decidir(ev(modo="indeterminado"), "valor_agregado")
check("modo 'indeterminado' também não decide", n == pl.NIVEL_ROTULO, n)

print("\n[4] Nível 4 — o rótulo decide, mas fica MARCADO que foi ele")
c, n, m, cand = pl.arvore_decidir(ev(modo=None), "valor_agregado")
check("devolve a categoria do rótulo", c == "valor_agregado")
check("nível 'rotulo'", n == pl.NIVEL_ROTULO)
check("o motivo admite que nenhum sinal decidiu",
      "nenhum sinal determinístico" in m, m)
c, n, _, _ = pl.arvore_decidir(ev(modo=None), "desperdicio")
check("rótulo improdutivo também é respeitado", c == "desperdicio")
c, n, _, _ = pl.arvore_decidir(ev(modo=None), None)
check("rótulo SEM categoria continua caindo em desperdício (convenção da F63)",
      c == "desperdicio", c)

print("\n[5] O CANDIDATO a improdutivo — a árvore aponta, NÃO decide")
c, n, _, cand = pl.arvore_decidir(ev(modo="parado"), "valor_agregado")
check("parado + sem mãos + rótulo produtivo → candidato",
      cand == pl.NIVEL_CANDIDATO_IMPRODUTIVO, cand)
check("mas a CATEGORIA não muda (pode ser ler desenho, esperar material)",
      c == "valor_agregado", c)
_c, _n, _m, cand2 = pl.arvore_decidir(ev(modo="parado"), "desperdicio")
check("rótulo já improdutivo não vira candidato", cand2 is None, cand2)
_c, _n, _m, cand3 = pl.arvore_decidir(ev(modo="indeterminado"), "valor_agregado")
check("sem medição boa da parada, não aponta nada", cand3 is None, cand3)

print("\n[6] PRECEDÊNCIA: correção humana vence TUDO")
c, n, m, _ = pl.arvore_decidir(
    ev(label="operar_torno", corrigido="conversando_colega",
       validado=True, mov="continuo"), "desperdicio")
check("humano corrigiu → vale o rótulo corrigido, não o sensor",
      c == "desperdicio" and n == pl.NIVEL_HUMANO, (c, n))
c, n, _, _ = pl.arvore_decidir(
    ev(papel="posto_vazio", label="posto_vazio", corrigido="operar_torno",
       validado=True), "valor_agregado")
check("e vence até o nível 1 (presença)",
      c == "valor_agregado" and n == pl.NIVEL_HUMANO, (c, n))
c, n, _, _ = pl.arvore_decidir(ev(validado=True, correto=True, mov="continuo"),
                               "desperdicio")
check("confirmação humana também conta como decisão humana",
      n == pl.NIVEL_HUMANO, n)
c, n, _, _ = pl.arvore_decidir(ev(validado=True, mov="continuo"), "desperdicio")
check("validado_humano SEM correção nem confirmação não sequestra a árvore",
      n == pl.NIVEL_MOVIMENTO, n)

print("\n[7] A ordem é uma ORDEM — cada nível vence os de baixo")
check("presença vence movimento",
      pl.arvore_decidir(ev(papel="posto_vazio", label="posto_vazio",
                           mov="continuo"), None)[1] == pl.NIVEL_PRESENCA)
check("movimento vence manual",
      pl.arvore_decidir(ev(mov="continuo", modo="manual"), None)[1]
      == pl.NIVEL_MOVIMENTO)
check("manual vence rótulo",
      pl.arvore_decidir(ev(modo="manual"), "desperdicio")[1] == pl.NIVEL_MANUAL)

print("\n[8] A FLAG: desligada, nada muda")
check("nasce desligada", pl._ARVORE_DECIDE is False)
cat_map = {"conversando_colega": "desperdicio"}
e_teste = ev(label="conversando_colega", mov="continuo")
lbl, cat, dur, niv, cand = pl._cat_com_arvore(e_teste, cat_map)
check("com a flag OFF vale o rótulo (comportamento de hoje)",
      cat == "desperdicio" and niv is None, (cat, niv))
_real = pl._ARVORE_DECIDE
pl._ARVORE_DECIDE = True
try:
    lbl, cat, dur, niv, cand = pl._cat_com_arvore(e_teste, cat_map)
    check("com a flag ON o sensor vence", cat == "valor_agregado", cat)
    check("e o nível vem junto", niv == pl.NIVEL_MOVIMENTO, niv)
finally:
    pl._ARVORE_DECIDE = _real
check("`_cat_do_evento` é o ponto ÚNICO por onde toda métrica passa",
      "_cat_com_arvore(e, cat_por_label)" in open(
          os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "backend", "pipeline.py"), encoding="utf-8").read())

print("\n[9] O nível fica GRAVADO — 'por que este minuto é produtivo?'")
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
check("o evento grava decidido_por", '"decidido_por": e.get("decidido_por")' in fonte)
check("calculado na ingestão mesmo com a flag OFF",
      'principais[-1]["decidido_por"] = _niv' in fonte)
check("o candidato NÃO é gravado na ingestão (a categoria do rótulo não existe lá)",
      '"candidato_improdutivo": e.get' not in fonte)
check("e o porquê disso está escrito",
      "AINDA NÃO EXISTE" in fonte)
check("maos_maquina passa a ser persistido (sinal cru auditável)",
      '"maos_maquina": e.get("maos_maquina"),' in fonte)
sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sql", "schema.sql"), encoding="utf-8").read()
check("schema declara decidido_por", "add column if not exists decidido_por" in sql)
check("com os cinco níveis válidos",
      "'humano','presenca','movimento','manual','rotulo'" in sql)

print("\n[10] O comparativo antes/depois, sem reprocessar")
class FakeQ:
    def __init__(s, sb, t): s.sb, s.t, s.eqs = sb, t, {}
    def select(s, *a, **k): return s
    def order(s, *a, **k): return s
    def limit(s, *a, **k): return s
    def range(s, a, b): s.faixa = (a, b); return s
    def eq(s, c, v): s.eqs[c] = v; return s
    def in_(s, c, vs): return s
    def is_(s, c, v): return s
    def not_(s): return s
    def execute(s):
        r = [dict(x) for x in s.sb.dados.get(s.t, [])
             if all(x.get(k) == v for k, v in s.eqs.items())]
        return types.SimpleNamespace(data=r)


class FakeSB:
    def __init__(s, d): s.dados, s.escritas = d, []
    def table(s, n):
        sb = s
        class T:
            def select(self, *a, **k): return FakeQ(sb, n)
            def upsert(self, p, **k): sb.escritas.append((n, p)); return FakeQ(sb, n)
            def update(self, p): sb.escritas.append((n, p)); return FakeQ(sb, n)
        return T()


EVS = [
    # 10 min de máquina rodando, mas rotulada como conversa (o caso do dono)
    {**ev(label="conversando_colega", mov="continuo", ini=0, fim=600),
     "empresa": "U", "processo": "T", "video_id": "v1"},
    # 10 min de posto vazio
    {**ev(label="posto_vazio", papel="posto_vazio", ini=600, fim=1200),
     "empresa": "U", "processo": "T", "video_id": "v1"},
    # 10 min parado sem mãos, rotulado produtivo → candidato
    {**ev(label="monitorar_maquina", modo="parado", ini=1200, fim=1800),
     "empresa": "U", "processo": "T", "video_id": "v1"},
]
SB = FakeSB({
    "eventos": EVS,
    "comportamentos": [
        {"label": "conversando_colega", "categoria_lean": "desperdicio",
         "empresa": "U", "processo": "T"},
        {"label": "monitorar_maquina", "categoria_lean": "valor_agregado",
         "empresa": "U", "processo": "T"},
        {"label": "posto_vazio", "categoria_lean": "desperdicio",
         "empresa": "U", "processo": "T"},
    ],
    "videos": [],
})
r = pl.comparar_arvore(SB, "U", "T")
check("30 minutos observados", r["minutos_observados"] == 30.0, r["minutos_observados"])
check("hoje: 33% produtivo (só o monitorar)", r["produtivo_hoje_pct"] == 33.3,
      r["produtivo_hoje_pct"])
check("árvore: 66% (a conversa com máquina rodando vira produtiva)",
      r["produtivo_arvore_pct"] == 66.7, r["produtivo_arvore_pct"])
check("o delta é reportado", r["delta_pp"] == 33.4, r["delta_pp"])
check("decomposto POR NÍVEL", set(r["por_nivel"]) == {"movimento", "presenca", "rotulo"},
      sorted(r["por_nivel"]))
check("e diz o que mudou, de onde para onde",
      any("→" in k for k in r["mudancas"]), r["mudancas"])
check("os candidatos saem separados, com rótulo",
      r["candidatos_improdutivo"]["minutos"] == 10.0
      and r["candidatos_improdutivo"]["rotulos"][0]["rotulo"] == "monitorar_maquina",
      r["candidatos_improdutivo"])
check("candidato NÃO entrou na conta da árvore (segue produtivo até você decidir)",
      r["produtivo_arvore_pct"] > 33.3)
check("funciona com a flag DESLIGADA (é simulação, não execução)",
      r["flag_ligada"] is False)
check("a nota explica que não reprocessa nada", "não precisa" in r["nota"])
check("nenhuma escrita — é só leitura", not SB.escritas, SB.escritas)

print("\n[11] Custo: a árvore não chama nada")
trecho = fonte[fonte.index("def arvore_decidir"):fonte.index("def comparar_arvore")]
for proibido in ("groq_text_call", "groq_vision_call", "ai_provider", "vision_call"):
    check(f"nenhuma chamada a {proibido}", proibido not in trecho)
check("e é função PURA (sem sb)", "def arvore_decidir(e: dict, cat_do_rotulo" in fonte)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
