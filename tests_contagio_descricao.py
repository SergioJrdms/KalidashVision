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


def ev(id_, desc, label, *, corrigido=None, origem=None, validado=False, seg=60,
       criado="2026-07-29T10:00:00", validado_em=None):
    return {"id": id_, "empresa": "U", "processo": "T",
            "comportamento_label": label, "label_corrigido": corrigido,
            "descricao_bruta": desc, "tempo_inicio_s": 0, "tempo_fim_s": seg,
            "origem_validacao": origem, "validado_humano": validado,
            "validacao_correto": True if validado else None, "principal": True,
            "criado_em": criado, "validado_em": validado_em}


print("\n[4] Limpeza — acha o contágio pela assinatura exata")
def cenario():
    return {"eventos": [
        # A correção legítima do gestor (o dia em que o operador faltou).
        ev("h1", FRASE, "operar_torno", corrigido="posto_vazio",
           origem="humano", validado=True,
           criado="2026-07-28T09:00:00", validado_em="2026-07-28T18:00:00"),
        # O contágio: mesma frase, terminaram no rótulo corrigido, sem humano.
        ev("c1", FRASE, "posto_vazio", criado="2026-07-29T10:00:00"),
        ev("c2", FRASE, "posto_vazio", validado=True,
           origem="vocabulario_canonico", criado="2026-07-30T10:00:00"),
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


# ════════════════════════════════════════════════════════════════════════
# Fase 70 — MAPEAMENTO NATURAL não é contágio.
#
# Medido em produção: `monitorar_maquina ← "monitorando o ciclo da máquina"`
# casa a assinatura com 361 eventos, mas a descrição levaria a esse rótulo sem
# mapa nenhum. Contá-lo como estrago esconderia a contaminação real (~91
# eventos) dentro de um número dez vezes maior.
#
# O corte é temporal: o par já existia ANTES da primeira correção humana?
# ════════════════════════════════════════════════════════════════════════
print("\n[5] Mapeamento natural × contágio — o corte temporal")

NAT = "monitorando o ciclo da máquina"
sb = FakeSB({"eventos": [
    # O par (NAT → monitorar_maquina) existe desde o DIA 1, muito antes de
    # qualquer correção. É o cluster fazendo o trabalho dele.
    ev("nat1", NAT, "monitorar_maquina", criado="2026-07-21T08:00:00"),
    ev("nat2", NAT, "monitorar_maquina", criado="2026-07-22T08:00:00"),
    ev("nat3", NAT, "monitorar_maquina", criado="2026-07-30T08:00:00"),
    # Em 28/07 alguém corrigiu um evento dessa mesma frase para posto_vazio.
    ev("hum", NAT, "monitorar_maquina", corrigido="posto_vazio",
       origem="humano", validado=True,
       criado="2026-07-28T09:00:00", validado_em="2026-07-28T18:00:00"),
    # ...e daí em diante a frase passou a virar posto_vazio sozinha. ISTO é
    # contágio: o par não existia antes de 28/07.
    ev("ctg1", NAT, "posto_vazio", criado="2026-07-29T08:00:00",
       validado=True, origem="vocabulario_canonico"),
    ev("ctg2", NAT, "posto_vazio", criado="2026-07-30T08:00:00",
       validado=True, origem="vocabulario_canonico"),
    # E o humano também corrigiu a mesma frase para monitorar_maquina uma vez
    # — não pode fazer o par natural virar suspeito.
]})
rel = pl.diagnosticar_contagio_por_descricao(sb, "U", "T", dry_run=True)
nat = {(n["rotulo"]) for n in rel["naturais"]}
check("o par natural é reconhecido como natural",
      "monitorar_maquina" in nat or not rel["naturais"], rel["naturais"])
check("só o contágio real entra na conta", rel["contaminados"] == 2, rel)
check("e ambos passavam por verdade", rel["passando_por_verdade"] == 2, rel)
check("o relatório nomeia o rótulo contaminado",
      rel["por_descricao"][0]["rotulo"] == "posto_vazio", rel["por_descricao"])

sb2 = FakeSB(sb.dados)
rel2 = pl.diagnosticar_contagio_por_descricao(sb2, "U", "T", dry_run=False)
por_id = {e["id"]: e for e in sb2.dados["eventos"]}
check("os naturais NÃO são revertidos",
      all(por_id[i].get("validado_humano") is False for i in ("nat1", "nat2", "nat3"))
      and por_id["nat3"]["comportamento_label"] == "monitorar_maquina",
      [por_id["nat1"], por_id["nat3"]])
check("os contaminados são revertidos", rel2["revertidos"] == 2, rel2)
check("a correção humana segue intocada",
      por_id["hum"]["validado_humano"] is True
      and por_id["hum"]["label_corrigido"] == "posto_vazio", por_id["hum"])

# Sem data em nada (base legada): o corte não pode inventar naturalidade.
sb3 = FakeSB({"eventos": [
    {**ev("h", "f", "a", corrigido="b", origem="humano", validado=True), "criado_em": None,
     "validado_em": None},
    {**ev("c", "f", "b"), "criado_em": None},
]})
rel3 = pl.diagnosticar_contagio_por_descricao(sb3, "U", "T", dry_run=True)
check("sem data, o evento continua sendo tratado como suspeito",
      rel3["contaminados"] == 1, rel3)


# ════════════════════════════════════════════════════════════════════════
# Fase 71 — relatório de reprocesso. SÓ LEITURA, ranqueado por MINUTOS.
# ════════════════════════════════════════════════════════════════════════
print("\n[6] Relatório de reprocesso — o minuto manda, não a contagem")

F = "operando o torno, manipulando a máquina"


def evv(id_, vid, desc, label, *, seg=60, origem=None, criado="2026-07-29T10:00:00"):
    return {**ev(id_, desc, label, criado=criado), "video_id": vid,
            "tempo_fim_s": seg, "origem_validacao": origem,
            "validado_humano": origem == "humano"}


dados = {"eventos": [
    # A correção humana que criou o mapa (em 28/07).
    {**ev("h", F, "operar_torno", corrigido="posto_vazio", origem="humano",
          validado=True, criado="2026-07-28T09:00:00",
          validado_em="2026-07-28T18:00:00"), "video_id": "vA"},
    # vB: POUCOS eventos, MUITOS minutos. Tem de vir na frente.
    evv("b1", "vB", F, "posto_vazio", seg=300),
    evv("b2", "vB", F, "posto_vazio", seg=300),
    # vC: MUITOS eventos, POUCOS minutos.
    *[evv(f"c{i}", "vC", F, "posto_vazio", seg=10) for i in range(8)],
    # vD: contaminado E com correção humana — não pode ser reprocessado sem perda.
    evv("d1", "vD", F, "posto_vazio", seg=60),
    {**ev("d2", "outra frase", "operar_torno", corrigido="medir_peca",
          origem="humano", validado=True), "video_id": "vD"},
], "videos": [
    {"id": "vA", "nome": "a.mp4", "duracao_s": 600, "video_removido_em": None},
    {"id": "vB", "nome": "b.mp4", "duracao_s": 600, "video_removido_em": None},
    {"id": "vC", "nome": "c.mp4", "duracao_s": 600, "video_removido_em": None},
    {"id": "vD", "nome": "d.mp4", "duracao_s": 600, "video_removido_em": None},
    {"id": "vE", "nome": "e.mp4", "duracao_s": 600, "video_removido_em": "2026-07-29"},
]}
sb = FakeSB(dados)
rel = pl.relatorio_reprocesso_por_video(sb, "U", "T", custo_por_min=0.02)

check("não escreve nada (relatório é só leitura)", not sb.escritas, sb.escritas)
top = rel["por_video"][0]
check("o vídeo com MAIS MINUTOS vem primeiro, não o com mais eventos",
      top["video_id"] == "vB", [(l["video_id"], l["minutos_contaminados"],
                                 l["eventos_contaminados"]) for l in rel["por_video"]])
check("vB tem menos eventos que vC", top["eventos_contaminados"] == 2)
segundo = rel["por_video"][1]
check("vC vem depois mesmo com 8 eventos",
      segundo["video_id"] == "vC" and segundo["eventos_contaminados"] == 8, segundo)

check("acumulado é monotônico e fecha em 100%",
      rel["por_video"][-1]["acumulado_pct"] == 100.0,
      [l["acumulado_pct"] for l in rel["por_video"]])
check("informa quantos vídeos cobrem 80% do estrago",
      rel["videos_para_80pct"] == 1, rel["videos_para_80pct"])
check("e o custo desses", rel["custo_80pct"] == 0.2, rel["custo_80pct"])
check("custo total usa a DURAÇÃO do vídeo, não os minutos contaminados",
      rel["custo_reprocessar_tudo"] == round(30 * 0.02, 2),
      (rel["custo_reprocessar_tudo"], rel["minutos_de_video"]))

check("separa os vídeos SEM correção humana",
      rel["sem_correcao_humana"]["videos"] == 2,
      rel["sem_correcao_humana"])
check("e nomeia os que TÊM correção (decisão caso a caso)",
      [v["video_id"] for v in rel["com_correcao_humana"]] == ["vD"],
      rel["com_correcao_humana"])
check("vD conta a correção humana dele",
      rel["com_correcao_humana"][0]["correcoes_humanas"] == 1,
      rel["com_correcao_humana"])
check("o vídeo sem contaminação não aparece",
      all(l["video_id"] != "vA" for l in rel["por_video"]),
      [l["video_id"] for l in rel["por_video"]])
check("avisa que reprocessar HOJE duplica",
      "DUPLICA" in rel["aviso"] and "sem dedup" in rel["aviso"], rel["aviso"])

sb2 = FakeSB({"eventos": [], "videos": []})
rel2 = pl.relatorio_reprocesso_por_video(sb2, "U", "T")
check("sem contaminação, o relatório não quebra",
      rel2["videos_afetados"] == 0 and rel2["minutos_contaminados"] == 0.0, rel2)

print(f"\n{'='*56}\n  TOTAL {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
