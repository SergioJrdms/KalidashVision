"""Fases 61 e 62 — a máquina não escreve verdade humana.

O incidente: 4 correções manuais para `conversando_colega` geraram 6 eventos
novos com origem_validacao='correcao_aprendida', validado_humano=true e
validacao_correto=true — todos errados. Três falhas somadas:
  • `correcao_aprendida` entrava em auto_validado SEM limiar nenhum, enquanto
    `vocabulario_canonico` sempre exigiu n_confirmacoes >= limiar;
  • os eventos auto-validados voltavam para a memória como "confirmação
    humana", realimentando o mesmo contador que libera a auto-validação;
  • a contagem real mostrou que METADE da "verdade humana" era máquina
    assinando: auditoria 907 · vocabulario_canonico 122 · humano 120 ·
    posto_vazio 71.

Cobertura:
  1) NENHUMA origem inferida grava validado_humano/validacao_correto
  2) exceções mecânicas (posto_vazio, auditoria) seguem fora da fila
  3) correção não generaliza por descrição (limiar da F61 revogado na F67)
  4) memória não conta inferência de máquina como confirmação humana
  5) reversão: escopo, proteções, dry_run e idempotência
  6) a CHAVE por processo: desligada, nada generaliza; religada, tudo volta

Rodar:  python tests_verdade_humana.py
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


# ── Fake Supabase com eq / in_ / insert / update ──────────────────────────
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

    def _casa(self, linha):
        for c, v in self.eqs.items():
            if linha.get(c) != v:
                return False
        for c, vs in self.ins.items():
            if linha.get(c) not in vs:
                return False
        for c in self.isnull:
            if linha.get(c) is not None:
                return False
        if self.ors == "categoria_lean.is.null,categoria_lean_origem.eq.herdado":
            if not (linha.get("categoria_lean") is None
                    or linha.get("categoria_lean_origem") == "herdado"):
                return False
        return True

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        if self.modo == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            novas = []
            for p in payload:
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


def evento(desc, label, i=0):
    """Evento no formato que etapa_persistir espera."""
    return {
        "pessoa_track_id": 1, "comportamento_label": label, "descricao_bruta": desc,
        "tempo_inicio_s": i * 60, "tempo_fim_s": i * 60 + 60,
        "frame_inicio": i * 30, "frame_fim": i * 30 + 30,
        "bbox_inicio": [0, 0, 10, 10], "zona_contexto": None,
        "n_amostras": 3, "confianca": 0.9, "principal": True,
    }


INFO = {"duracao_s": 300.0, "fps": 10.0, "largura": 640, "altura": 480}


def persistir(sb, eventos, origem_de):
    return pl.etapa_persistir(
        sb, "U", "Torneamento", "/tmp/v.mp4", INFO, eventos, [1],
        {"conversando_colega": "conversa", "operar_torno": "torno"}, origem_de,
    )


print("\n[1] correcao_aprendida PROPÕE, não valida")
sb = FakeSB()
evs = [evento("operador de pé olhando a peça", "conversando_colega", i) for i in range(3)]
_, n_auto, _ = persistir(sb, evs, lambda d, *_a: "correcao_aprendida")
gravados = [e for e in sb.dados["eventos"] if e.get("principal") is True]
check("nenhum evento nasceu validado",
      all(e["validado_humano"] is False for e in gravados), gravados)
check("nenhum evento nasceu com validacao_correto",
      all("validacao_correto" not in e for e in gravados), gravados)
check("nenhum evento nasceu com validado_em",
      all("validado_em" not in e for e in gravados), gravados)
check("n_auto_validados == 0", n_auto == 0, n_auto)
check("origem_validacao PRESERVADA como proposta",
      all(e["origem_validacao"] == "correcao_aprendida" for e in gravados), gravados)
check("os 3 caem na fila (pendente = validado_humano false)",
      sum(1 for e in gravados if not e["validado_humano"]) == 3)

print("\n[2] pendente comum também nasce na fila (controle)")
sb = FakeSB()
evs = [evento("operando o torno", "operar_torno", i) for i in range(2)]
_, n_auto, _ = persistir(sb, evs, lambda d, *_a: "pendente")
gravados = [e for e in sb.dados["eventos"] if e.get("principal") is True]
check("pendente não é validado", all(e["validado_humano"] is False for e in gravados))
check("n_auto_validados == 0", n_auto == 0, n_auto)

print("\n[3] posto_vazio e auditoria intocados")
sb = FakeSB()
ev_vazio = evento("posto de trabalho vazio", "posto_vazio", 0)
ev_vazio["papel_pessoa"] = "posto_vazio"
_, _, _ = pl.etapa_persistir(
    sb, "U", "Torneamento", "/tmp/v.mp4", INFO, [ev_vazio], [1],
    {"posto_vazio": "vazio"}, lambda d, *_a: "pendente",
    eventos_auditoria=[evento("cru qualquer", "operar_torno", 5)],
)
por_origem = {e["origem_validacao"]: e for e in sb.dados["eventos"]}
check("posto_vazio segue fora da fila",
      por_origem["posto_vazio"]["validado_humano"] is True, por_origem.get("posto_vazio"))
check("auditoria segue fora da fila",
      por_origem["auditoria"]["validado_humano"] is True, por_origem.get("auditoria"))

print("\n[4] Correção não vira regra global (F61 limiar → F67 revogação)")
DESC = "operador de pé olhando a peça"
mem_1 = {"correcoes_aprendidas": {DESC: "conversando_colega"},
         "correcoes_confirmacoes": {DESC: 1}, "vocabulario": []}
mem_2 = {"correcoes_aprendidas": {DESC: "conversando_colega"},
         "correcoes_confirmacoes": {DESC: 2}, "vocabulario": []}
obs = [{"descricao": DESC}]



# Abaixo do limiar a descrição volta para o caminho normal de clusterização —
# ou seja, VAI para a LLM. O stub prova isso e devolve o rótulo que o cluster
# daria, que não é o da correção.
import json as _json  # noqa: E402

_chamadas_llm = []


def _fake_llm(_cli, prompt, **k):
    _chamadas_llm.append(prompt)
    return _json.dumps({"comportamentos": [{
        "label": "olhar_peca", "descricao": "Operador observa a peça",
        "descricoes_originais": [DESC],
    }]})


pl.groq_text_call = _fake_llm


def clusterizar(mem, limiar=2):
    _chamadas_llm.clear()
    return pl.etapa_clusterizar(None, obs, "torneamento", mem, limiar,
                                lambda *a, **k: None)


# ⚠️ A Fase 61 introduziu um LIMIAR para a correção generalizar. A Fase 67
# REVOGA isso: correção não generaliza por `descricao_bruta` em modo nenhum,
# porque o pressuposto "mesma descrição ⇒ mesma ação" é falso quando o VLM
# alucina a descrição. O limiar continua valendo para `vocabulario_canonico`;
# aqui as asserções passam a travar a REVOGAÇÃO.
mapa, _cat, label_de, origem_de = clusterizar(mem_1)
check("1 correção NÃO remapeia o rótulo",
      label_de(DESC) != "conversando_colega", label_de(DESC))
check("1 correção NÃO marca origem correcao_aprendida",
      origem_de(DESC) == "pendente", origem_de(DESC))
check("a descrição vai ao cluster normal",
      len(_chamadas_llm) == 1 and label_de(DESC) == "olhar_peca", label_de(DESC))

mapa, _cat, label_de, origem_de = clusterizar(mem_2)
check("Fase 67: nem 2 correções remapeiam",
      label_de(DESC) == "olhar_peca", label_de(DESC))
check("Fase 67: a LLM é chamada mesmo com correção na memória",
      len(_chamadas_llm) == 1, _chamadas_llm)
check("Fase 67: origem nunca é correcao_aprendida",
      origem_de(DESC) != "correcao_aprendida", origem_de(DESC))

mapa, _cat, label_de, origem_de = clusterizar(mem_1, limiar=1)
check("Fase 67: limiar 1 também não ressuscita o remap por descrição",
      label_de(DESC) == "olhar_peca", label_de(DESC))

print("\n[5] Memória — a máquina não confirma a si mesma")
def linha(label, origem, correto=True, corrigido=None, desc="d"):
    return {"comportamento_label": label, "label_corrigido": corrigido,
            "descricao_bruta": desc, "validacao_correto": correto,
            "principal": True, "origem_validacao": origem,
            "empresa": "U", "processo": "Torneamento", "validado_humano": True}


sb = FakeSB({"eventos": [
    linha("operar_torno", "humano"),
    linha("operar_torno", "vocabulario_canonico"),   # eco da máquina
    linha("operar_torno", "correcao_aprendida"),     # eco da máquina
    linha("operar_torno", "posto_vazio"),            # determinístico
    linha("operar_torno", None),                     # humano legado (origem nula)
], "comportamentos": [
    {"label": "operar_torno", "descricao": "torno", "empresa": "U",
     "processo": "Torneamento"},
]})
mem = pl.carregar_memoria_do_negocio(sb, "U", "Torneamento")
vocab = {v["label"]: v["n_confirmacoes"] for v in mem["vocabulario"]}
check("n_confirmacoes conta só humano + legado (2, não 5)",
      vocab.get("operar_torno") == 2, vocab)
check("total_eventos_validados também exclui os ecos",
      mem["total_eventos_validados"] == 2, mem["total_eventos_validados"])

sb = FakeSB({"eventos": [
    linha("andar", "humano", corrigido="conversando_colega", desc="frase x"),
    linha("andar", "humano", corrigido="conversando_colega", desc="frase x"),
    linha("andar", "humano", corrigido="conversando_colega", desc="frase y"),
], "comportamentos": []})
mem = pl.carregar_memoria_do_negocio(sb, "U", "Torneamento")
check("correcoes_confirmacoes conta por descrição",
      mem["correcoes_confirmacoes"] == {"frase x": 2, "frase y": 1},
      mem["correcoes_confirmacoes"])

print("\n[6] Reversão com escopo estreito — proteções, dry_run, idempotência")
def ev_banco(id_, origem, vh=True, vc=True):
    return {"id": id_, "empresa": "U", "processo": "Torneamento",
            "comportamento_label": "conversando_colega", "label_corrigido": None,
            "tempo_inicio_s": 0, "tempo_fim_s": 60,
            "origem_validacao": origem, "validado_humano": vh,
            "validacao_correto": vc, "validado_em": "2026-07-01T00:00:00"}


def cenario():
    return {"eventos": [
        ev_banco("a1", "correcao_aprendida"),
        ev_banco("a2", "correcao_aprendida"),
        ev_banco("h1", "humano"),
        ev_banco("au1", "auditoria"),
        ev_banco("pv1", "posto_vazio"),
        ev_banco("vc1", "vocabulario_canonico"),
    ]}


sb = FakeSB(cenario())
SO_CORRECAO = ("correcao_aprendida",)
rel = pl.reverter_auto_validacao_maquina(sb, "U", "Torneamento",
                                         origens=SO_CORRECAO, dry_run=True)
check("dry_run encontra os 2", rel["encontrados"] == 2, rel)
check("dry_run NÃO escreve", not sb.escritas and rel["revertidos"] == 0, sb.escritas)
check("dry_run conta os minutos devolvidos",
      rel["minutos_devolvidos_a_fila"] == 2.0, rel)

sb = FakeSB(cenario())
rel = pl.reverter_auto_validacao_maquina(sb, "U", "Torneamento",
                                         origens=SO_CORRECAO, dry_run=False)
por_id = {e["id"]: e for e in sb.dados["eventos"]}
check("revertidos == 2", rel["revertidos"] == 2, rel)
check("a1/a2 voltaram à fila",
      all(por_id[i]["validado_humano"] is False
          and por_id[i]["validacao_correto"] is None
          and por_id[i]["validado_em"] is None for i in ("a1", "a2")),
      [por_id["a1"], por_id["a2"]])
check("origem_validacao PRESERVADA (vira 'proposto por')",
      por_id["a1"]["origem_validacao"] == "correcao_aprendida", por_id["a1"])
check("humano INTOCADO",
      por_id["h1"]["validado_humano"] is True and por_id["h1"]["validacao_correto"] is True,
      por_id["h1"])
check("auditoria INTOCADA", por_id["au1"]["validado_humano"] is True, por_id["au1"])
check("posto_vazio INTOCADO", por_id["pv1"]["validado_humano"] is True, por_id["pv1"])
check("origem fora da lista pedida não é tocada",
      por_id["vc1"]["validado_humano"] is True, por_id["vc1"])

n_escritas = len(sb.escritas)
rel2 = pl.reverter_auto_validacao_maquina(sb, "U", "Torneamento",
                                          origens=SO_CORRECAO, dry_run=False)
check("idempotente — 2ª passada não acha nada", rel2["encontrados"] == 0, rel2)
check("idempotente — 2ª passada não escreve", len(sb.escritas) == n_escritas)

sb = FakeSB(cenario())
rel = pl.reverter_auto_validacao_maquina(sb, "U", "Torneamento",
                                         origens=("humano",), dry_run=False)
check("origem protegida é recusada na função", "erro" in rel, rel)
check("recusa não escreveu nada", not sb.escritas, sb.escritas)

sb = FakeSB({"eventos": [ev_banco("x1", "correcao_aprendida"),
                         dict(ev_banco("x2", "correcao_aprendida"),
                              empresa="OUTRA")]})
pl.reverter_auto_validacao_maquina(sb, "U", "Torneamento", dry_run=False)
por_id = {e["id"]: e for e in sb.dados["eventos"]}
check("escopo multi-tenant respeitado",
      por_id["x1"]["validado_humano"] is False
      and por_id["x2"]["validado_humano"] is True, por_id)


# ════════════════════════════════════════════════════════════════════════
# Fase 62 — a chave da generalização automática
# ════════════════════════════════════════════════════════════════════════
print("\n[7] vocabulario_canonico também deixa de assinar verdade humana")
sb = FakeSB()
evs = [evento("operando o torno", "operar_torno", i) for i in range(2)]
_, n_auto, _ = persistir(sb, evs, lambda d, *_a: "vocabulario_canonico")
gravados = [e for e in sb.dados["eventos"] if e.get("principal") is True]
check("nenhum evento nasce validado",
      all(e["validado_humano"] is False for e in gravados), gravados)
check("n_auto_validados == 0 (nenhuma origem auto-valida)", n_auto == 0, n_auto)
check("origem preservada como proposta",
      all(e["origem_validacao"] == "vocabulario_canonico" for e in gravados))

sb = FakeSB()
ev_v = evento("posto de trabalho vazio", "posto_vazio", 0)
ev_v["papel_pessoa"] = "posto_vazio"
pl.etapa_persistir(sb, "U", "Torneamento", "/tmp/v.mp4", INFO, [ev_v], [1],
                   {"posto_vazio": "vazio"}, lambda d, *_a: "pendente",
                   eventos_auditoria=[evento("cru", "operar_torno", 5)])
por_o = {e["origem_validacao"]: e for e in sb.dados["eventos"]}
check("posto_vazio segue fora da fila (exceção mecânica, não verdade)",
      por_o["posto_vazio"]["validado_humano"] is True)
check("auditoria segue fora da fila", por_o["auditoria"]["validado_humano"] is True)

print("\n[8] Limpeza inclui os 122 de vocabulario_canonico por default")
sb = FakeSB(cenario())
rel = pl.reverter_auto_validacao_maquina(sb, "U", "Torneamento", dry_run=False)
por_id = {e["id"]: e for e in sb.dados["eventos"]}
check("default agora pega as DUAS origens de máquina", rel["encontrados"] == 3, rel)
check("vocabulario_canonico voltou à fila",
      por_id["vc1"]["validado_humano"] is False
      and por_id["vc1"]["validacao_correto"] is None, por_id["vc1"])
check("humano segue intocado", por_id["h1"]["validado_humano"] is True)
check("auditoria segue intocada", por_id["au1"]["validado_humano"] is True)
check("posto_vazio segue intocado", por_id["pv1"]["validado_humano"] is True)

print("\n[9] A chave: leitura do flag por processo")
sb = FakeSB({"contexto_processo": [
    {"empresa": "U", "processo": "Torneamento", "aprendizado_automatico": False},
    {"empresa": "U", "processo": "Fresagem", "aprendizado_automatico": True},
    {"empresa": "U", "processo": "Solda", "aprendizado_automatico": None},
]})
check("processo desligado → False",
      pl.aprendizado_automatico(sb, "U", "Torneamento") is False)
check("processo ligado → True",
      pl.aprendizado_automatico(sb, "U", "Fresagem") is True)
check("NULL herda o default do ambiente",
      pl.aprendizado_automatico(sb, "U", "Solda") is pl.APRENDIZADO_AUTO_PADRAO)
check("processo inexistente herda o default",
      pl.aprendizado_automatico(sb, "U", "NaoExiste") is pl.APRENDIZADO_AUTO_PADRAO)
check("default de ambiente é DESLIGADO durante a campanha",
      pl.APRENDIZADO_AUTO_PADRAO is False, pl.APRENDIZADO_AUTO_PADRAO)


class SBQuebrado:
    def table(self, _n):
        raise RuntimeError("banco fora")


check("falha de leitura cai no default (modo seguro)",
      pl.aprendizado_automatico(SBQuebrado(), "U", "X") is pl.APRENDIZADO_AUTO_PADRAO)

print("\n[10] Chave desligada — nada generaliza")
mem_forte = {"correcoes_aprendidas": {DESC: "conversando_colega"},
             "correcoes_confirmacoes": {DESC: 9},
             "vocabulario": [{"label": "olhar_peca", "descricao": "d",
                              "n_confirmacoes": 50}]}
_chamadas_llm.clear()
_, _c, label_de, origem_de = pl.etapa_clusterizar(
    None, obs, "torneamento", mem_forte, 2, lambda *a, **k: None,
    aprendizado_auto=False)
check("correção com 9 confirmações NÃO remapeia",
      label_de(DESC) != "conversando_colega", label_de(DESC))
check("descrição volta ao cluster normal", len(_chamadas_llm) == 1)
check("origem é sempre pendente", origem_de(DESC) == "pendente", origem_de(DESC))

_chamadas_llm.clear()
_, _c, label_de, origem_de = pl.etapa_clusterizar(
    None, obs, "torneamento", mem_forte, 2, lambda *a, **k: None,
    aprendizado_auto=True)
# A chave da Fase 62 religa o vocabulário e os descartados. NÃO ressuscita o
# remapeamento por descrição — esse morreu na 67, em qualquer modo.
check("religando, a correção por descrição SEGUE morta",
      label_de(DESC) == "olhar_peca"
      and origem_de(DESC) != "correcao_aprendida", (label_de(DESC), origem_de(DESC)))

print("\n[11] Chave desligada — precedente Lean de outro processo não entra")
_orig_mem_cat = pl.carregar_memoria_categoria
pl.carregar_memoria_categoria = lambda *a, **k: {"mapa_humano": {"andar": "desperdicio"},
                                                 "exemplos_por_cat": {}, "n_decisoes": 1}
_llm_lean = []
pl.groq_text_call = lambda *a, **k: (_llm_lean.append(1) or
                                     _json.dumps({"classificacoes": []}))

sb = FakeSB({
    "comportamentos": [{"id": "c1", "empresa": "U", "processo": "Torneamento",
                        "label": "andar", "descricao": "andar",
                        "categoria_lean": None, "categoria_lean_origem": None}],
    "contexto_processo": [{"empresa": "U", "processo": "Torneamento",
                           "aprendizado_automatico": False}],
    "eventos": [],
})
pl.classificar_comportamentos_lean(sb, None, "U", "Torneamento")
# Fase 63: não existe mais categoria nula — o que prova que o precedente não
# foi aplicado é a ORIGEM. 'fallback' = o sistema assumiu por convenção;
# 'aprendido' seria a decisão de outro processo tendo vazado para cá.
check("precedente humano de outro processo NÃO foi aplicado",
      sb.dados["comportamentos"][0]["categoria_lean_origem"] == "fallback",
      sb.dados["comportamentos"][0])
check("mas o nível 2 (IA) continua rodando", len(_llm_lean) == 1, _llm_lean)

sb = FakeSB({
    "comportamentos": [{"id": "c1", "empresa": "U", "processo": "Fresagem",
                        "label": "andar", "descricao": "andar",
                        "categoria_lean": None, "categoria_lean_origem": None}],
    "contexto_processo": [{"empresa": "U", "processo": "Fresagem",
                           "aprendizado_automatico": True}],
    "eventos": [],
})
pl.classificar_comportamentos_lean(sb, None, "U", "Fresagem")
check("com a chave ligada, o precedente volta a valer",
      sb.dados["comportamentos"][0]["categoria_lean"] == "desperdicio"
      and sb.dados["comportamentos"][0]["categoria_lean_origem"] == "aprendido",
      sb.dados["comportamentos"][0])
pl.carregar_memoria_categoria = _orig_mem_cat

print(f"\n{'='*56}\n  TOTAL {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
