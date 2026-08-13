"""Fase 63 — "não classificado" deixa de existir.

Todo tempo observado é produtivo ou não-produtivo. Onde falta evidência vale a
convenção Lean (o ônus da prova é de quem afirma que agrega valor) e o trecho
vai para a FILA DE DÚVIDAS — a incerteza passa a ser declarada em vez de virar
uma fatia cinza que ninguém reclama.

Cobertura:
  1) categoria_efetiva nunca devolve None; nulo/lixo → não-produtivo
  2) categoria_tem_evidencia separa DECIDIDO de ASSUMIDO
  3) compor_tempo_observado: duas fatias fecham 100%; resíduo vai p/ desp
  4) acao_indefinida deixa de ficar sem categoria
  5) fechamento: o que o LLM não classificou vira 'fallback'
  6) dúvida de categoria entra na fila, com o motivo certo
  7) o 2º ângulo vem sincronizado (offset real de relógio)

Rodar:  python tests_binario_lean.py
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

# Fase 97: a classificação Lean automática saiu do caminho da produtividade e
# ficou DESLIGADA por padrão. Esta suíte testa o MECANISMO, que continua
# existindo — então ela o liga explicitamente. Sem isto ela passaria a testar
# "a flag está off", que é outra coisa.
pl._LEAN_AUTO = True

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


_SEQ = [0]


class FakeQ:
    def __init__(self, sb, tabela, modo, payload=None):
        self.sb, self.tabela, self.modo, self.payload = sb, tabela, modo, payload
        self.eqs, self.ins, self.isnull, self.ors = {}, {}, [], None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def in_(self, c, vs): self.ins[c] = list(vs); return self
    def is_(self, c, _v): self.isnull.append(c); return self
    def or_(self, e): self.ors = e; return self

    def _casa(self, l):
        for c, v in self.eqs.items():
            if l.get(c) != v:
                return False
        for c, vs in self.ins.items():
            if l.get(c) not in vs:
                return False
        for c in self.isnull:
            if l.get(c) is not None:
                return False
        if self.ors == "categoria_lean.is.null,categoria_lean_origem.eq.herdado":
            if not (l.get("categoria_lean") is None
                    or l.get("categoria_lean_origem") == "herdado"):
                return False
        return True

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        if self.modo == "insert":
            pl_ = self.payload if isinstance(self.payload, list) else [self.payload]
            novas = []
            for p in pl_:
                _SEQ[0] += 1
                n = {"id": f"id-{_SEQ[0]}", **p}
                linhas.append(n); novas.append(dict(n))
            return types.SimpleNamespace(data=novas)
        casam = [l for l in linhas if self._casa(l)]
        if self.modo == "update":
            for l in casam:
                l.update(self.payload)
                self.sb.escritas.append((self.tabela, l.get("id"), dict(self.payload)))
        return types.SimpleNamespace(data=[dict(l) for l in casam])


class FakeSB:
    def __init__(self, dados=None):
        self.dados = dados or {}
        self.escritas = []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome, "select")
            def update(self, p): return FakeQ(sb, nome, "update", p)
            def insert(self, p): return FakeQ(sb, nome, "insert", p)
        return T()


print("\n[1] categoria_efetiva — nunca devolve None")
check("valor_agregado passa", pl.categoria_efetiva("valor_agregado") == "valor_agregado")
check("desperdicio passa", pl.categoria_efetiva("desperdicio") == "desperdicio")
check("None → não-produtivo", pl.categoria_efetiva(None) == "desperdicio")
check("string vazia → não-produtivo", pl.categoria_efetiva("") == "desperdicio")
check("'apoio' legado → não-produtivo", pl.categoria_efetiva("apoio") == "desperdicio")
check("'nao_classificado' legado → não-produtivo",
      pl.categoria_efetiva("nao_classificado") == "desperdicio")
check("nunca é None em nenhum caso",
      all(pl.categoria_efetiva(v) in pl.CATEGORIAS_LEAN_VALIDAS
          for v in (None, "", "apoio", "lixo", "valor_agregado", "desperdicio")))

print("\n[2] categoria_tem_evidencia — decidido × assumido")
check("decisão humana tem evidência",
      pl.categoria_tem_evidencia("valor_agregado", "humano") is True)
check("palpite da IA tem evidência", pl.categoria_tem_evidencia("desperdicio", "ia") is True)
check("fallback NÃO tem evidência",
      pl.categoria_tem_evidencia("desperdicio", "fallback") is False)
check("categoria nula NÃO tem evidência",
      pl.categoria_tem_evidencia(None, "ia") is False)

print("\n[3] compor_tempo_observado — duas fatias, 100%")
r = pl.compor_tempo_observado(60, 40, 0, 100)
check("va+desp = 100", abs(r["va_pct"] + r["desp_pct"] - 100) < 0.05, r)
check("sem chave none_pct", "none_pct" not in r, r)
r = pl.compor_tempo_observado(30, 20, 0, 100)   # 50s de resíduo
check("resíduo vai para NÃO-produtivo (nunca infla o produtivo)",
      r["va_pct"] == 30.0 and r["desp_pct"] == 70.0, r)
r = pl.compor_tempo_observado(50, 30, 20, 100)
check("vazio é DETALHE do não-produtivo, não 3ª fatia",
      abs(r["va_pct"] + r["desp_pct"] - 100) < 0.05 and r["vazio_pct"] == 20.0, r)
r = pl.compor_tempo_observado(0, 0, 0, 0)
check("total zero não quebra", r["va_pct"] == 0.0 and r["observado_s"] == 0.0, r)
r = pl.compor_tempo_observado(200, 0, 0, 100)
check("va maior que o total é capado", r["va_pct"] == 100.0 and r["desp_pct"] == 0.0, r)

print("\n[4] acao_indefinida deixa de ficar sem categoria")
sb = FakeSB({
    "comportamentos": [{"id": "c1", "empresa": "U", "processo": "T",
                        "label": "acao_indefinida", "descricao": "x",
                        "categoria_lean": None, "categoria_lean_origem": None}],
    "eventos": [{"id": "e1", "empresa": "U", "processo": "T",
                 "comportamento_label": "acao_indefinida", "label_corrigido": None,
                 "tempo_inicio_s": 0, "tempo_fim_s": 60, "principal": True,
                 "categoria_lean": None, "categoria_lean_origem": None,
                 "validacao_correto": None}],
    "contexto_processo": [{"empresa": "U", "processo": "T",
                           "aprendizado_automatico": False}],
})
pl.classificar_comportamentos_lean(sb, None, "U", "T")
c1 = sb.dados["comportamentos"][0]
check("recebeu não-produtivo", c1["categoria_lean"] == "desperdicio", c1)
check("marcado como assumido ('fallback')",
      c1["categoria_lean_origem"] == "fallback", c1)
check("desceu para os eventos",
      sb.dados["eventos"][0]["categoria_lean"] == "desperdicio", sb.dados["eventos"][0])

print("\n[5] Fechamento — LLM caiu, ninguém fica sem categoria")
def _llm_morto(*a, **k):
    raise RuntimeError("provedor fora")


pl.groq_text_call = _llm_morto
pl.carregar_memoria_categoria = lambda *a, **k: {"mapa_humano": {}, "exemplos_por_cat": {}}
sb = FakeSB({
    "comportamentos": [
        {"id": "c1", "empresa": "U", "processo": "T", "label": "mexer_bancada",
         "descricao": "d", "categoria_lean": None, "categoria_lean_origem": None},
        {"id": "c2", "empresa": "U", "processo": "T", "label": "operar_torno",
         "descricao": "d", "categoria_lean": "valor_agregado",
         "categoria_lean_origem": "humano"},
    ],
    "eventos": [],
    "contexto_processo": [{"empresa": "U", "processo": "T",
                           "aprendizado_automatico": False}],
})
n = pl.classificar_comportamentos_lean(sb, None, "U", "T")
por_id = {c["id"]: c for c in sb.dados["comportamentos"]}
check("o que o LLM não classificou virou 'fallback'",
      por_id["c1"]["categoria_lean"] == "desperdicio"
      and por_id["c1"]["categoria_lean_origem"] == "fallback", por_id["c1"])
check("decisão HUMANA continua intocada",
      por_id["c2"]["categoria_lean"] == "valor_agregado"
      and por_id["c2"]["categoria_lean_origem"] == "humano", por_id["c2"])
check("contabiliza o assumido no retorno", n == 1, n)
check("nenhum comportamento ficou sem categoria",
      all(c["categoria_lean"] for c in sb.dados["comportamentos"]))

print("\n[6] Dúvida de categoria entra na fila")
sb = FakeSB({"comportamentos": [
    {"label": "conferir_peca", "categoria_lean": "desperdicio",
     "categoria_lean_origem": "fallback", "empresa": "U", "processo": "T"},
    {"label": "operar_torno", "categoria_lean": "valor_agregado",
     "categoria_lean_origem": "ia", "empresa": "U", "processo": "T"},
    {"label": "andar", "categoria_lean": None,
     "categoria_lean_origem": None, "empresa": "U", "processo": "T"},
]})
assumidos = pl.labels_com_categoria_assumida(sb, "U", "T")
check("pega o 'fallback'", "conferir_peca" in assumidos, assumidos)
check("pega o nulo", "andar" in assumidos, assumidos)
check("NÃO pega o decidido pela IA", "operar_torno" not in assumidos, assumidos)


def ev(**kw):
    base = {"comportamento_label": "conferir_peca", "label_corrigido": None,
            "n_amostras": 5, "confianca": 0.95, "validado_humano": False}
    base.update(kw)
    return base


dv, motivo, tipo = pl.evento_em_duvida(ev(), 0.65, assumidos)
check("evento com categoria assumida entra em dúvida", dv is True)
check("tipo é categoria_assumida", tipo == "categoria_assumida", tipo)
check("motivo explica que é convenção, não evidência",
      "convenção" in motivo and "NÃO-produtivo" in motivo, motivo)

dv, _, tipo = pl.evento_em_duvida(ev(comportamento_label="operar_torno"), 0.65, assumidos)
check("rótulo com categoria decidida NÃO entra", dv is False, tipo)

# Sem evidência continua exclusivo e vem primeiro: são problemas diferentes.
dv, _, tipo = pl.evento_em_duvida(ev(n_amostras=1), 0.65, assumidos)
check("1 amostra continua 'sem_evidencia' (não vira categoria_assumida)",
      tipo == "sem_evidencia", tipo)

# Discordância é mais informativa que "ninguém decidiu" → vence.
dv, _, tipo = pl.evento_em_duvida(ev(confianca=0.4), 0.65, assumidos)
check("discordância tem precedência sobre categoria assumida",
      tipo == "discordancia", tipo)

dv, _, tipo = pl.evento_em_duvida(ev(validado_humano=True), 0.65, assumidos)
check("evento já julgado sai da fila", dv is False)

check("sem o conjunto, o comportamento antigo é preservado",
      pl.evento_em_duvida(ev(), 0.65)[0] is False)

print("\n[7] 2º ângulo sincronizado pelo relógio real")
off = pl.offset_video_segmento(
    {"gravado_em": "2026-07-21T13:00:30+00:00"},
    {"gravado_em": "2026-07-21T13:00:00+00:00"})
check("offset por gravado_em (cam1 30s depois)", off == 30.0, off)
off = pl.offset_video_segmento(
    {"nome": "seg_20260721_130000.mp4"}, {"nome": "seg_20260721_125945.mp4"})
check("fallback pelo token do nome", off == 15.0, off)
check("sem dado confiável → 0.0",
      pl.offset_video_segmento({}, {}) == 0.0)
check("nunca mistura fonte (um lado só) → 0.0",
      pl.offset_video_segmento({"gravado_em": "2026-07-21T13:00:00+00:00"}, {}) == 0.0)


# ════════════════════════════════════════════════════════════════════════
# [8] SMOKE do dashboard — o teste que faltava.
#
# A Fase 63 mexeu em toda a cadeia de agregação e nenhum teste chamava essas
# funções de verdade: um NameError em `_montar_perguntas_gestor` derrubou o
# dashboard inteiro em produção e a suíte passou verde. Testar o valor de
# retorno de helpers puros não cobre "a página abre".
# ════════════════════════════════════════════════════════════════════════
print("\n[8] Smoke — o dashboard monta de ponta a ponta")

CATS_LBL = {"operar_torno": "valor_agregado", "andar": "desperdicio",
            "conferir_peca": "desperdicio", "posto_vazio": "desperdicio"}


def ev_dash(i, label, ini_s, zona="torno", papel=None):
    return {
        "id": f"e{i}", "video_id": "v1", "empresa": "U", "processo": "T",
        "comportamento_label": label, "label_corrigido": None,
        "tempo_inicio_s": ini_s, "tempo_fim_s": ini_s + 60,
        "zona_contexto": zona, "papel_pessoa": papel, "principal": True,
        "n_amostras": 4, "confianca": 0.9, "validacao_correto": None,
        "validado_humano": False, "pessoa_track_id": 1,
    }


eventos_d = [
    ev_dash(1, "operar_torno", 0), ev_dash(2, "operar_torno", 60),
    ev_dash(3, "andar", 120, zona="corredor"),
    ev_dash(4, "conferir_peca", 180), ev_dash(5, "conferir_peca", 240),
    ev_dash(6, "conferir_peca", 300),
    ev_dash(7, "posto_vazio", 360, papel="posto_vazio"),
]
videos_d = [{"id": "v1", "empresa": "U", "processo": "T",
             "processado_em": "2026-07-21T13:00:00+00:00",
             "nome": "seg_20260721_130000.mp4", "duracao_s": 420}]
dist_d = [
    {"comportamento": "operar_torno", "tempo_total_s": 120, "pct_tempo": 20.0,
     "categoria_lean": "valor_agregado", "categoria_lean_origem": "ia"},
    {"comportamento": "andar", "tempo_total_s": 60, "pct_tempo": 10.0,
     "categoria_lean": "desperdicio", "categoria_lean_origem": "ia"},
    {"comportamento": "conferir_peca", "tempo_total_s": 180, "pct_tempo": 30.0,
     "categoria_lean": "desperdicio", "categoria_lean_origem": "fallback"},
    {"comportamento": "posto_vazio", "tempo_total_s": 60, "pct_tempo": 10.0,
     "categoria_lean": "desperdicio", "categoria_lean_origem": "ia"},
]
composicao_d = {
    "valor_agregado_pct": 28.6, "desperdicio_pct": 71.4,
    "tempo_total_s": 420.0, "sem_evidencia_pct": 42.9, "sem_evidencia_s": 180.0,
    "posto_vazio_pct": 14.3, "posto_vazio_s": 60.0,
    "por_categoria_s": {"valor_agregado": 120.0, "desperdicio": 300.0},
}

try:
    iq = pl.montar_insights_quantitativos(dist_d, composicao_d, eventos_d,
                                          videos_d, CATS_LBL)
    erro_iq = None
except Exception as e:  # noqa: BLE001
    iq, erro_iq = None, e

check("montar_insights_quantitativos não explode", erro_iq is None, erro_iq)
if iq:
    check("devolve frases", isinstance(iq.get("frases"), list) and len(iq["frases"]) > 0)
    check("por_categoria tem só as duas categorias",
          set(iq["por_categoria"]) == {"valor_agregado", "desperdicio"}, iq["por_categoria"])
    check("as duas fatias fecham ~100%",
          abs(sum(v["pct"] for v in iq["por_categoria"].values()) - 100) < 0.5,
          iq["por_categoria"])
    check("tempo_por_acao marca o que foi assumido",
          any(a.get("sem_evidencia") for a in iq["tempo_por_acao"]), iq["tempo_por_acao"])
    check("perguntas montam sem erro", isinstance(iq.get("perguntas"), list))
    check("a pergunta do tempo ASSUMIDO aparece (20% ≥ 10%)",
          any("assumir" in p["texto"] or "sem evidência" in p["texto"]
              for p in iq["perguntas"]),
          [p["texto"][:60] for p in iq["perguntas"]])
    check("nenhuma frase fala em 'não classificado'",
          not any("não classificado" in f["texto"].lower() for f in iq["frases"]),
          [f["texto"] for f in iq["frases"]])
    check("placar monta", "placar" in iq)

# `analise_diaria` é o outro caminho de agregação que a Fase 63 tocou.
sb = FakeSB({
    "eventos": eventos_d,
    "videos": videos_d,
    "comportamentos": [{"label": k, "categoria_lean": v, "empresa": "U",
                        "processo": "T", "categoria_lean_origem": "ia"}
                       for k, v in CATS_LBL.items()],
    "contexto_processo": [{"empresa": "U", "processo": "T", "duvida_limiar": 0.65}],
})
try:
    dias = pl.montar_analise_diaria(sb, "U", "T", dias=30)
    erro_dias = None
except Exception as e:  # noqa: BLE001
    dias, erro_dias = None, e
check("montar_analise_diaria não explode", erro_dias is None, erro_dias)
if dias:
    com_trab = [d for d in dias["dias"] if d["tempo_obs_s"] > 0]
    check("há dia com trabalho (senão as checagens abaixo passam no vácuo)",
          len(com_trab) > 0, len(dias["dias"]))
    check("dia com trabalho: va+desp fecham 100%",
          all(abs(d["va_pct"] + d["desp_pct"] - 100) < 0.5 for d in com_trab),
          [(d["dia"], d["va_pct"], d["desp_pct"]) for d in com_trab])
    check("nenhum dia devolve none_pct",
          all("none_pct" not in d for d in dias["dias"]))
    check("vazio_pct nunca passa do desp_pct (é pedaço dele)",
          all(d["vazio_pct"] <= d["desp_pct"] + 0.05 for d in com_trab),
          [(d["dia"], d["vazio_pct"], d["desp_pct"]) for d in com_trab])
    check("linha_tempo só usa categorias vivas",
          all(f["cat"] in ("va", "desp", "vazio")
              for d in dias["dias"] for f in d["linha_tempo"]))


# ════════════════════════════════════════════════════════════════════════
# [9] Fase 66 — A CURVA DA DÚVIDA É HISTÓRICA.
#
# Antes, validar um trecho o APAGAVA do histórico: a curva media "o que ainda
# está em dúvida hoje" em vez de "o que o sistema não soube naquele dia". O
# passado se reescrevia a cada validação e o gráfico que existe para provar
# aprendizado era zerado justamente pelo ato de aprender.
# ════════════════════════════════════════════════════════════════════════
print("\n[9] A curva da dúvida não se reescreve quando alguém valida")


def ev_dv(id_, ini_s, *, conf=0.4, n_am=5, validado=False, origem=None,
          label="conferir_peca", papel=None):
    return {
        "id": id_, "video_id": "v1", "empresa": "U", "processo": "T",
        "comportamento_label": label, "label_corrigido": None,
        "tempo_inicio_s": ini_s, "tempo_fim_s": ini_s + 60,
        "zona_contexto": "torno", "papel_pessoa": papel, "principal": True,
        "n_amostras": n_am, "confianca": conf, "validacao_correto": None,
        "validado_humano": validado, "origem_validacao": origem,
        "pessoa_track_id": 1, "em_duvida": None, "duvida_motivo": None,
        "n_rotulos_no_minuto": 3, "camadas_disparadas": None,
    }


VIDS = [{"id": "v1", "empresa": "U", "processo": "T",
         "processado_em": "2026-07-21T13:00:00+00:00",
         "nome": "seg_20260721_130000.mp4", "duracao_s": 600}]
COMPS = [{"label": "conferir_peca", "categoria_lean": "desperdicio",
          "categoria_lean_origem": "ia", "empresa": "U", "processo": "T"}]


def dias_de(eventos):
    sb = FakeSB({
        "eventos": eventos, "videos": VIDS, "comportamentos": COMPS,
        "contexto_processo": [{"empresa": "U", "processo": "T", "duvida_limiar": 0.65}],
    })
    d = pl.montar_analise_diaria(sb, "U", "T", dias=30)
    return next(x for x in d["dias"] if x["tempo_obs_s"] > 0)


# 4 minutos: 2 em dúvida (concordância 0.4 < 0.65), 2 sem dúvida.
abertos = [ev_dv("a", 0), ev_dv("b", 60), ev_dv("c", 120, conf=0.95),
           ev_dv("d", 180, conf=0.95)]
dia = dias_de(abertos)
check("com tudo em aberto, 50% do dia está em dúvida",
      abs(dia["duvida_pct"] - 50.0) < 0.6, dia["duvida_pct"])
check("nada julgado ainda", dia["duvida_resolvida_pct"] == 0.0, dia)

# O gestor valida os DOIS trechos duvidosos. O dia NÃO pode virar 0%.
validados = [ev_dv("a", 0, validado=True, origem="humano"),
             ev_dv("b", 60, validado=True, origem="humano"),
             ev_dv("c", 120, conf=0.95), ev_dv("d", 180, conf=0.95)]
dia2 = dias_de(validados)
check("validar NÃO apaga a dúvida do histórico (era o bug)",
      abs(dia2["duvida_pct"] - 50.0) < 0.6, dia2["duvida_pct"])
check("a curva do dia fica IDÊNTICA antes e depois de validar",
      dia2["duvida_pct"] == dia["duvida_pct"], (dia["duvida_pct"], dia2["duvida_pct"]))
check("e o julgado aparece separado, para a tela mostrar o trabalho feito",
      abs(dia2["duvida_resolvida_pct"] - 50.0) < 0.6, dia2["duvida_resolvida_pct"])
check("resolvida nunca passa da levantada",
      dia2["duvida_resolvida_pct"] <= dia2["duvida_pct"] + 0.05, dia2)

print("\n[10] A fila continua sendo só o que FALTA julgar")
check("na fila, evento julgado sai",
      pl.evento_em_duvida(ev_dv("a", 0, validado=True), 0.65)[0] is False)
check("no histórico, o mesmo evento continua contando",
      pl.evento_em_duvida(ev_dv("a", 0, validado=True), 0.65,
                          incluir_resolvidas=True)[0] is True)
check("o motivo é preservado no histórico",
      "discordaram" in pl.evento_em_duvida(ev_dv("a", 0, validado=True), 0.65,
                                           incluir_resolvidas=True)[1])

print("\n[11] Determinístico nunca foi dúvida — nem no histórico")
for orig in ("posto_vazio", "auditoria"):
    check(f"{orig}: fora da fila",
          pl.evento_em_duvida(ev_dv("x", 0, n_am=1, validado=True, origem=orig),
                              0.65)[0] is False)
    check(f"{orig}: fora do histórico (não vira 'dúvida resolvida')",
          pl.evento_em_duvida(ev_dv("x", 0, n_am=1, validado=True, origem=orig),
                              0.65, incluir_resolvidas=True)[0] is False)

vazios = [ev_dv("v1", 0, n_am=1, validado=True, origem="posto_vazio",
                label="posto_vazio", papel="posto_vazio"),
          ev_dv("c", 60, conf=0.95)]
dia3 = dias_de(vazios)
check("posto vazio não infla a curva do dia",
      dia3["duvida_pct"] == 0.0 and dia3["sem_evidencia_pct"] == 0.0, dia3)

print("\n[12] 'sem evidência' voltou a ser medido no dia")
# A consulta do dia não trazia `n_amostras`: o ramo nunca disparava e o KPI
# mostrava 0 para sempre, independentemente dos dados.
poucas = [ev_dv("p", 0, n_am=1), ev_dv("q", 60, n_am=1),
          ev_dv("r", 120, conf=0.95), ev_dv("s", 180, conf=0.95)]
dia4 = dias_de(poucas)
check("1 amostra vira 'sem evidência', não 'dúvida'",
      dia4["sem_evidencia_pct"] > 0 and dia4["duvida_pct"] == 0.0, dia4)
check("50% do dia sem evidência", abs(dia4["sem_evidencia_pct"] - 50.0) < 0.6,
      dia4["sem_evidencia_pct"])

print(f"\n{'='*56}\n  TOTAL {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
