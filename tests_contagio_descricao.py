"""Fase 67 — correção NUNCA é chaveada por `descricao_bruta`.

O incidente: o operador faltou, algumas pessoas passaram pela zona do posto e o
VLM ALUCINOU — descreveu "operando o torno, manipulando a máquina". O gestor
corrigiu o rótulo daquele evento para `posto_vazio`, que era o certo ALI. O
sistema guardou "esta frase significa posto_vazio" — e essa é a descrição mais
frequente do dataset. A partir daí, eventos legítimos de operação viraram
`posto_vazio` e `conversando_colega`.

Dois erros distintos que o sistema tratava igual:
  (a) descrição certa, rótulo errado → corrigir o rótulo faz sentido;
  (b) descrição ERRADA (alucinação)  → corrigir o rótulo cria mapeamento falso.

Enquanto a validação não separar (a) de (b), nenhuma correção generaliza.

Rodar:  python tests_contagio_descricao.py
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

import json as _json  # noqa: E402
from backend import pipeline as pl  # noqa: E402

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
        self.eqs, self.ins = {}, {}

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def in_(self, c, vs): self.ins[c] = list(vs); return self
    def is_(self, c, _v): return self
    def or_(self, e): return self

    def _casa(self, l):
        for c, v in self.eqs.items():
            if l.get(c) != v:
                return False
        for c, vs in self.ins.items():
            if l.get(c) not in vs:
                return False
        return True

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        casam = [l for l in linhas if self._casa(l)]
        if self.modo == "update":
            for l in casam:
                l.update(self.payload)
                self.sb.escritas.append((self.tabela, l.get("id")))
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


# A frase que o VLM alucinou e que é a MAIS COMUM do dataset.
FRASE = "operando o torno, manipulando a máquina"
MEM_ENVENENADA = {
    "correcoes_aprendidas": {FRASE: "posto_vazio"},
    "correcoes_confirmacoes": {FRASE: 9},     # muito acima de qualquer limiar
    "vocabulario": [{"label": "operar_torno", "descricao": "opera o torno",
                     "n_confirmacoes": 40}],
    "descartados": {"conversando_colega": 3},
}

_prompts = []


def _fake_llm(_cli, prompt, **k):
    _prompts.append(prompt)
    return _json.dumps({"comportamentos": [{
        "label": "operar_torno", "descricao": "Opera o torno",
        "descricoes_originais": [FRASE],
    }]})


pl.groq_text_call = _fake_llm


def clusterizar(mem, aprendizado_auto):
    _prompts.clear()
    return pl.etapa_clusterizar(None, [{"descricao": FRASE}], "torneamento",
                                mem, 2, lambda *a, **k: None,
                                aprendizado_auto=aprendizado_auto)


print("\n[1] A correção não remapeia mais — em NENHUM modo")
for modo in (False, True):
    _m, _c, label_de, origem_de = clusterizar(MEM_ENVENENADA, modo)
    check(f"aprendizado_auto={modo}: a frase NÃO vira posto_vazio",
          label_de(FRASE) == "operar_torno", label_de(FRASE))
    # Com a chave ligada, `vocabulario_canonico` é legítimo (o rótulo tem 40
    # confirmações humanas). O que não pode aparecer NUNCA é correcao_aprendida.
    check(f"aprendizado_auto={modo}: origem não é correcao_aprendida",
          origem_de(FRASE) != "correcao_aprendida", origem_de(FRASE))
    check(f"aprendizado_auto={modo}: a descrição foi ao cluster normal",
          len(_prompts) == 1, len(_prompts))

print("\n[2] O caminho que a chave da Fase 62 não cobria: o PROMPT")
_m, _c, _l, _o = clusterizar(MEM_ENVENENADA, False)
prompt = _prompts[0]
# A frase APARECE no prompt — ela é a descrição a clusterizar. O que não pode
# aparecer é o MAPEAMENTO dela para o rótulo corrigido.
check("a frase entra como entrada a clusterizar", FRASE in prompt)
check("o prompt NÃO contém o mapa descrição→rótulo",
      "posto_vazio" not in prompt, prompt[-400:])
check("o prompt NÃO contém a instrução 'CORREÇÕES APRENDIDAS'",
      "CORREÇÕES APRENDIDAS" not in prompt)
check("o prompt NÃO manda usar rótulo em 'descrições parecidas'",
      "descrições parecidas" not in prompt)
check("com a chave DESLIGADA, 'descartados' também não vai ao prompt",
      "FALSO POSITIVO" not in prompt)
check("o VOCABULÁRIO continua indo (mantém os nomes consistentes)",
      "LABELS CANÔNICOS" in prompt)

_m, _c, _l, _o = clusterizar(MEM_ENVENENADA, True)
check("com a chave LIGADA, 'descartados' volta ao prompt",
      "FALSO POSITIVO" in _prompts[0])
check("mas as correções seguem fora, mesmo com a chave ligada",
      "CORREÇÕES APRENDIDAS" not in _prompts[0])

print("\n[3] O bloco de memória, isolado")
b = pl.construir_bloco_memoria_cluster(MEM_ENVENENADA)
check("nunca emite o mapa de correções", FRASE not in b, b)
b_off = pl.construir_bloco_memoria_cluster({**MEM_ENVENENADA, "_generalizar": False})
check("_generalizar=False corta os descartados", "FALSO POSITIVO" not in b_off)
check("e mantém o vocabulário", "LABELS CANÔNICOS" in b_off)


def ev(id_, desc, label, *, corrigido=None, origem=None, validado=False, seg=60):
    return {"id": id_, "empresa": "U", "processo": "T",
            "comportamento_label": label, "label_corrigido": corrigido,
            "descricao_bruta": desc, "tempo_inicio_s": 0, "tempo_fim_s": seg,
            "origem_validacao": origem, "validado_humano": validado,
            "validacao_correto": True if validado else None, "principal": True}


print("\n[4] Limpeza — acha o contágio pela assinatura exata")
def cenario():
    return {"eventos": [
        # A correção legítima do gestor (o dia em que o operador faltou).
        ev("h1", FRASE, "operar_torno", corrigido="posto_vazio",
           origem="humano", validado=True),
        # O contágio: mesma frase, terminaram no rótulo corrigido, sem humano.
        ev("c1", FRASE, "posto_vazio"),
        ev("c2", FRASE, "posto_vazio", validado=True, origem="vocabulario_canonico"),
        # Mesma frase, MAS outro rótulo → não é contágio.
        ev("n1", FRASE, "operar_torno"),
        # Outra frase → intocado.
        ev("n2", "monitorando o ciclo", "monitorar_maquina"),
        # Auditoria não conta.
        {**ev("a1", FRASE, "posto_vazio"), "principal": False},
    ]}


sb = FakeSB(cenario())
rel = pl.diagnosticar_contagio_por_descricao(sb, "U", "T", dry_run=True)
check("achou 1 correção humana de origem", rel["correcoes_humanas"] == 1, rel)
check("achou os 2 contaminados", rel["contaminados"] == 2, rel)
check("só 1 deles ainda passa por verdade (o outro já está na fila)",
      rel["passando_por_verdade"] == 1, rel)
check("dry_run não escreve", not sb.escritas and rel["revertidos"] == 0)
check("relatório mostra a frase e o rótulo herdado",
      rel["por_descricao"][0]["rotulo"] == "posto_vazio", rel["por_descricao"])
check("relatório diz quantas vezes o humano corrigiu aquela frase",
      rel["por_descricao"][0]["corrigido_pelo_humano_n"] == 1, rel["por_descricao"])
check("avisa que o rótulo original não é recuperável", "não é recuperável" in rel["aviso"])

sb = FakeSB(cenario())
rel = pl.diagnosticar_contagio_por_descricao(sb, "U", "T", dry_run=False)
por_id = {e["id"]: e for e in sb.dados["eventos"]}
check("reverteu só o que passava por verdade", rel["revertidos"] == 1, rel)
check("contaminados voltaram para a fila",
      all(por_id[i]["validado_humano"] is False
          and por_id[i]["validacao_correto"] is None for i in ("c1", "c2")),
      [por_id["c1"], por_id["c2"]])
check("a correção HUMANA ficou intocada",
      por_id["h1"]["validado_humano"] is True
      and por_id["h1"]["label_corrigido"] == "posto_vazio", por_id["h1"])
check("mesma frase com outro rótulo não foi tocada",
      por_id["n1"]["validado_humano"] is False and por_id["n1"]["label_corrigido"] is None)
check("outra frase intocada", por_id["n2"]["comportamento_label"] == "monitorar_maquina")
check("auditoria intocada", por_id["a1"]["validado_humano"] is False)

n = len(sb.escritas)
rel2 = pl.diagnosticar_contagio_por_descricao(sb, "U", "T", dry_run=False)
check("idempotente — nada mais passa por verdade na 2ª passada",
      rel2["passando_por_verdade"] == 0, rel2)
check("idempotente — 2ª passada não escreve", len(sb.escritas) == n)
check("mas o relatório continua mostrando o estrago (só reprocesso desfaz)",
      rel2["contaminados"] == 2, rel2)

sb = FakeSB({"eventos": [ev("x", "frase", "lbl")]})
rel = pl.diagnosticar_contagio_por_descricao(sb, "U", "T", dry_run=True)
check("sem correção humana, não há o que limpar", rel["contaminados"] == 0, rel)

sb = FakeSB({"eventos": [
    ev("h", FRASE, "operar_torno", corrigido="posto_vazio", origem="humano", validado=True),
    {**ev("out", FRASE, "posto_vazio"), "empresa": "OUTRA"},
]})
pl.diagnosticar_contagio_por_descricao(sb, "U", "T", dry_run=False)
check("escopo multi-tenant respeitado",
      sb.dados["eventos"][1]["validado_humano"] is False
      and sb.dados["eventos"][1].get("validacao_correto") is None,
      sb.dados["eventos"][1])
check("nenhuma escrita fora da empresa", not sb.escritas, sb.escritas)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
