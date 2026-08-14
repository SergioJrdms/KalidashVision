# ============================================================
# Fase 100 — `acao_indefinida` explodiu: 38,7% do dia 14/08.
#
# A hipótese que chegou (o banimento da Fase 99 teria derrubado as raízes do
# vocabulário e o cluster teria "perdido as referências") é REFUTADA aqui, com
# o dado: as raízes seguiram sendo atribuídas normalmente no mesmo dia.
#
# A causa é outra e é anterior: `acao_indefinida` VIROU VOCABULÁRIO CANÔNICO.
# 65 eventos foram confirmados por um humano na fila ("sim, é indefinida
# mesmo"), e é exatamente disso que `carregar_memoria_do_negocio` monta a lista
# de "LABELS CANÔNICOS JÁ VALIDADOS" do prompt do cluster — logo acima da
# "REGRA DURA: se uma descrição corresponde semanticamente a um destes labels,
# REUSE". Com a descrição de catálogo que a linha tinha ("Operador parado
# próximo ao torno, sem manipulação, monitoramento ativo ou conversa
# identificável"), qualquer "parado junto ao torno, sem manipulação visível"
# casa. O modelo obedeceu.
# ============================================================
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

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()


print("\n[1] A abstenção não é vocabulário")
VOCAB = [
    {"label": "operar_torno", "descricao": "Operador trabalhando no torno", "n_confirmacoes": 922},
    {"label": "monitorar_maquina", "descricao": "Operador observando a máquina", "n_confirmacoes": 688},
    {"label": "conversando_colega", "descricao": "Conversando colega", "n_confirmacoes": 73},
    {"label": "acao_indefinida",
     "descricao": "Operador parado próximo ao torno, sem manipulação, "
                  "monitoramento ativo ou conversa identificável.",
     "n_confirmacoes": 2},
]
_v = pl.vocabulario_sem_estado(VOCAB)
check("`acao_indefinida` NÃO entra no vocabulário sugerido",
      "acao_indefinida" not in [v["label"] for v in _v], _v)
check("e o carimbo novo também não", "nao_nomeado" not in [v["label"] for v in _v])

# ⚠️ REQUISITO 4 do dono: conferir que o banimento da Fase 99 não derrubou as
# raízes legítimas junto. Refutação direta da hipótese.
check("as TRÊS raízes legítimas sobrevivem intactas",
      [v["label"] for v in _v] == ["operar_torno", "monitorar_maquina", "conversando_colega"],
      [v["label"] for v in _v])
for _r in ("monitorar_maquina", "operar_torno", "conversando_colega"):
    check(f"{_r} não é lido como afirmação de estado",
          not pl.rotulo_afirma_estado(_r) and pl.limpar_sufixo_estado(_r) == _r)
check("nem entra na lista de banidos da Fase 99",
      not (pl.ROTULOS_BANIDOS_DO_VOCABULARIO & {"monitorar_maquina", "operar_torno",
                                                "conversando_colega"}))

print("\n[2] O prompt parou de oferecer o balde")
check("a regra que MANDAVA criar acao_indefinida saiu",
      '"ação não identificada" vira o label "acao_indefinida"' not in fonte)
check("e virou proibição explícita", "NÃO EXISTE label de desistência" in fonte)
check("com a saída correta: omitir, não inventar grupo genérico",
      "OMITA-A da resposta" in fonte)
check("o prompt explica que 'parado ao lado da máquina' TEM nome",
      "descreve as MÃOS, não a ausência de atividade" in fonte)
_bloco = pl.construir_bloco_memoria_cluster({"vocabulario": VOCAB})
check("o bloco do prompt não cita a abstenção", "acao_indefinida" not in _bloco, _bloco)
check("mas cita as raízes, com a REGRA DURA intacta",
      "monitorar_maquina" in _bloco and "REGRA DURA" in _bloco)

print("\n[3] A guarda: desistência não vira rótulo, mesmo se o modelo insistir")
for _d in ("acao_indefinida", "atividade_indefinida", "acao_nao_identificada",
           "comportamento_generico", "indefinido", "outros", "nao_classificado",
           "sem_atividade", "acao_inconclusiva", "acao_indeterminada"):
    check(f"'{_d}' é reconhecido como desistência", pl._e_desistencia(_d))
for _r in ("operar_torno", "monitorar_maquina", "conversando_colega",
           "posto_vazio", "medir_peca", "limpando_cavaco", "ajustar_maquina",
           "lendo_desenho_tecnico", "deslocar_pelo_posto"):
    check(f"'{_r}' NÃO é confundido com desistência", not pl._e_desistencia(_r))

print("\n[4] Descrição utilizável que não foi nomeada vai para a FILA, não para o balde")
chamou = []


def _cluster_que_desiste(cli, prompt, **k):
    chamou.append(prompt)
    import json as _j
    linhas = [l[2:] for l in prompt.splitlines() if l.startswith("- ")]
    # O modelo devolve o balde para tudo — o pior caso, que é o que aconteceu.
    return _j.dumps({"comportamentos": [
        {"label": "acao_indefinida", "descricao": "Ação não identificada",
         "descricoes_originais": linhas}]})


_txt = pl.groq_text_call
pl.groq_text_call = _cluster_que_desiste
try:
    obs = [{"descricao": "parado junto ao torno, sem tocar em nada",
            "track_id": 1, "tempo_s": 0, "frame_idx": 0}]
    _, catalogo, label_de, _ = pl.etapa_clusterizar(
        None, obs, "torno", {}, 3, lambda *a, **k: None, aprendizado_auto=False)
    check("o balde do modelo NÃO vira rótulo — devolve None (ausência)",
          label_de("parado junto ao torno, sem tocar em nada") is None)
    check("e não aparece no catálogo como se fosse atividade",
          not any(pl._e_desistencia(l) for l in catalogo), list(catalogo))
finally:
    pl.groq_text_call = _txt

def _obs(desc):
    return {"descricao": desc, "track_id": 1, "tempo_s": 0, "frame_idx": 0,
            "zona": "posto_operador", "origem_gate": "analisado"}


evs = pl.etapa_segmentar_eventos([_obs("parado junto ao torno, sem tocar em nada")],
                                 lambda *a, **k: None, 3.0)
check("o evento CONTINUA existindo (o minuto foi observado de verdade)", len(evs) == 1)
check("mas nasce com o carimbo de ausência, não com nome de atividade",
      evs[0]["comportamento_label"] == pl.LABEL_NAO_NOMEADO == "nao_nomeado",
      evs[0]["comportamento_label"])
check("e a descrição observada é PRESERVADA — é o que o gestor vai ler",
      evs[0]["descricao_bruta"] == "parado junto ao torno, sem tocar em nada")
evs2 = pl.etapa_segmentar_eventos([_obs("operando o torno")],
                                  lambda *a, **k: "operar_torno", 3.0)
check("quando o cluster nomeia, nada muda",
      evs2[0]["comportamento_label"] == "operar_torno")

print("\n[5] Os DOIS carimbos são ausência de rótulo, em todo lugar")
for _c in ("acao_indefinida", "nao_nomeado"):
    check(f"{_c} é ausência", pl.rotulo_e_ausencia(_c))
    check(f"{_c} fica FORA da árvore e do Pareto",
          not pl.evento_conta_no_vocabulario({"comportamento_label": _c}))
    check(f"{_c} conta como sem descrição utilizável",
          pl.sem_descricao_utilizavel({"comportamento_label": _c}))
check("rótulo de verdade continua contando",
      pl.evento_conta_no_vocabulario({"comportamento_label": "monitorar_maquina"}))
check("e a correção humana TIRA o evento do estado de ausência",
      not pl.sem_descricao_utilizavel(
          {"comportamento_label": "nao_nomeado", "label_corrigido": "monitorar_maquina"}))
check("o Pareto filtra por ausência, não por um nome fixo",
      "if rotulo_e_ausencia(l):" in fonte)

print("\n[6] Confirmar a abstenção na fila não a promove a canônica")
check("as confirmações de ausência são removidas antes do vocabulário",
      "if rotulo_e_ausencia(_l):" in fonte and "del confirmados[_l]" in fonte)
check("com o registro de quantas foram", "confirmação(ões) de ausência" in fonte)

print("\n[7] A CATRACA do cache — por que foi rampa e não degrau")
# EFEITO PRINCIPAL (285 dos 330 eventos de 14/08): frase cujo histórico só tem
# o balde ficava TRAVADA nele. O cache servia `acao_indefinida` de graça, sem
# chamar modelo, para sempre — sem nem a chance de o cluster revisar.
TRAVADA = "parado junto ao torno, sem tocar em nada"
# EFEITO SECUNDÁRIO (os outros 45): frase com label DE VERDADE e o balde no
# histórico virava ambígua e perdia a proteção do cache. Dado real de 14/08.
DESC = "operador parado junto ao torno, sem manipulação visível"


class SBCache:
    def __init__(self, linhas):
        self._l = linhas

    def table(self, _):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *a, **k):
        return self

    def execute(self):
        class R:
            data = self._l
        return R()


linhas = ([{"id": str(i), "descricao_bruta": DESC,
            "comportamento_label": "monitorar_maquina"} for i in range(28)]
          + [{"id": f"x{i}", "descricao_bruta": DESC,
              "comportamento_label": "acao_indefinida"} for i in range(21)])
_cc = pl._CACHE_CLUSTER
pl._CACHE_CLUSTER = True
try:
    # ── EFEITO PRINCIPAL: a trava. Frase que só teve o balde no histórico.
    travada = pl.cache_desc_label(SBCache(
        [{"id": str(i), "descricao_bruta": TRAVADA,
          "comportamento_label": "acao_indefinida"} for i in range(159)]), "U", "T")
    check("o cache NÃO serve mais o balde de graça, para sempre",
          TRAVADA.lower() not in travada, travada)
    check("a frase volta a poder ser NOMEADA pelo cluster (cache vazio = vai ao modelo)",
          travada == {})

    # ── EFEITO SECUNDÁRIO: a ambiguidade fabricada pelo balde.
    cache = pl.cache_desc_label(SBCache(linhas), "U", "T")
    check("a descrição VOLTA a ter resposta determinística no cache",
          cache.get(DESC.lower()) == "monitorar_maquina", cache)
    check("o descarte por ambiguidade continua valendo para rótulos DE VERDADE",
          pl.cache_desc_label(SBCache([
              {"id": "1", "descricao_bruta": DESC, "comportamento_label": "monitorar_maquina"},
              {"id": "2", "descricao_bruta": DESC, "comportamento_label": "operar_torno"},
          ]), "U", "T") == {})
finally:
    pl._CACHE_CLUSTER = _cc
check("com o porquê escrito no código", "A CATRACA" in fonte)

print("\n[8] Ausência NUNCA agrega valor")
# Em 14/08 a linha `acao_indefinida` estava em valor_agregado/humano e, por
# herança, ~320 eventos do dia contaram como PRODUTIVOS.
check("a correção de categoria cobre os dois carimbos",
      "if rotulo_e_ausencia(c.get(\"label\")):" in fonte)
check("e propaga usando o label da própria linha, não um nome fixo",
      "sb, empresa, processo, c[\"label\"], CATEGORIA_SEM_EVIDENCIA" in fonte)
check("a categoria de ausência é não-produtivo, com origem que manda para a fila",
      pl.CATEGORIA_SEM_EVIDENCIA == "desperdicio" and pl.ORIGEM_SEM_EVIDENCIA == "fallback")

print("\n[9] Sem nome ⇒ fila, sempre — com a descrição visível")
check("o não-nomeado é forçado para a fila independentemente de camadas",
      "SEM NOME ⇒ FILA, sempre" in fonte)
check("e o motivo carrega a descrição observada, para o gestor nomear",
      "a descrição observada foi:" in fonte and "Nomeie você." in fonte)

print("\n[10] A tela não mostra ausência como atividade")
rot = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "frontend", "src", "design", "rotulos.ts"),
           encoding="utf-8").read()
check("o front conhece os dois carimbos", 'SEM_ROTULO = new Set(["acao_indefinida", "nao_nomeado"])' in rot)
check("e expõe o teste", "export function semRotulo" in rot)
check("nenhum dos dois é oferecido como escolha de correção",
      "afirmaEstado(cru) || semRotulo(cru)" in rot)
check("e o texto na tela diz o que é: item de trabalho, não ação observada",
      'acao_indefinida: "Sem nome — aguardando você"' in rot
      and 'nao_nomeado: "Sem nome — aguardando você"' in rot)
check("'Ação não identificada' sumiu — fingia ser uma atividade",
      '"Ação não identificada"' not in rot)

# ═════════════════════════════════════════════════════════════════════════
# [11] MEDIÇÃO COM O DADO REAL DE 14/08 — as 56 descrições distintas que
# viraram `acao_indefinida` (330 eventos), copiadas do banco.
#
# ⚠️ HONESTIDADE SOBRE O QUE ISTO MEDE. Quem nomeia de verdade é o cluster
# (uma LLM). Isto aqui é um PROXY determinístico: classifica cada descrição
# pelo que ela diz, com as mesmas raízes que já existem no vocabulário. Serve
# para responder "quantas destas descrições CONTÊM ação nomeável", que é a
# pergunta que importa — se contêm, o cluster sem balde tem o que nomear; se
# não contêm, vão para a fila e é o gestor quem nomeia.
# ═════════════════════════════════════════════════════════════════════════
print("\n[11] Medição com as 56 descrições reais de 14/08")

REAL_14_08 = [
    ("parado junto ao torno, sem tocar em nada", 159),
    ("parado junto ao torno, sem manipulação visível", 18),
    ("operador parado junto ao torno, sem manipulação visível.", 17),
    ("operador parado junto ao torno, sem manipulação visível", 15),
    ("parado junto ao torno, braços abaixados", 13),
    ("parado junto ao torno, sem manipulação ativa", 9),
    ("parado na área de interação, sem tocar em nada", 7),
    ("operador parado junto ao torno, sem mudança de posição.", 4),
    ("posto de trabalho vazio, operador não visível na área.", 4),
    ("operador titular parado junto ao torno, sem manipulação visível", 3),
    ("operador ausente do posto de trabalho visível.", 3),
    ("parado junto ao torno, posição alterada, braços em movimento.", 3),
    ("parado junto ao torno, sem mudança de posição", 3),
    ("parado próximo ao torno, sem manipulação visível", 3),
    ("operando o torno, mãos na máquina.", 3),
    ("operador na mesma posição, sem mudança visível", 2),
    ("parado junto ao torno, mãos na bancada", 2),
    ("operador parado à esquerda do torno, sem manipulação", 2),
    ("parado próximo ao torno, sem manipulação identificável", 2),
    ("operador parado junto ao torno, mãos próximas à máquina", 2),
    ("parado junto ao torno, braços abaixados, observando o posto.", 2),
    ("operador deslocado, posição alterada junto ao torno.", 2),
    ("operador presente junto ao torno, mãos na máquina.", 2),
    ("operador presente junto ao torno, parado sem manipulação visível.", 2),
    ("operador presente junto ao torno, posição junto à máquina.", 2),
    ("sem mudança de posição, parado junto ao torno", 2),
    ("operador presente no posto, parado junto ao torno, sem manipulação visível.", 2),
    ("parado junto ao torno, posição similar à imagem anterior", 2),
    ("parado junto ao torno, sem manipulação visível.", 2),
    ("operador parado junto ao torno, braços abaixados, sem manipulação.", 2),
    ("operador retornou ao torno, parado sem manipulação visível.", 1),
    ("deslocado para a esquerda do torno, longe da máquina", 1),
    ("deslocado para a esquerda, parado junto ao torno", 1),
    ("deslocou-se para a direita do posto, posição alterada", 1),
    ("operador à esquerda operando/manipulando máquina, mãos na estrutura; "
     "operador à direita parado próximo ao equipamento", 1),
    ("operador ausente do posto de trabalho.", 1),
    ("operador de perfil ao torno, mãos próximas à máquina, posição de trabalho.", 1),
    ("operador deslocado para a direita, posição alterada", 1),
    ("operador em pé junto ao torno, sem manipulação visível", 1),
    ("operador na mesma posição, mãos na máquina, operando.", 1),
    ("operador parado junto ao torno, posição próxima à anterior", 1),
    ("operador parado junto ao torno, posição sem mudança", 1),
    ("operador parado junto ao torno, posição similar à anterior, sem manipulação.", 1),
    ("operador parado junto ao torno, sem mudança de posição", 1),
    ("operador próximo ao torno, parado, sem manipulação visível", 1),
    ("deslocado para a direita do posto, parado sem manipulação", 1),
    ("parado afastado do torno, sem manipulação", 1),
    ("parado ao fundo, sem atividade aparente", 1),
    ("parado junto ao torno, mãos não visíveis em manipulação ativa", 1),
    ("parado junto ao torno, observando desenho técnico na parede", 1),
    ("parado junto ao torno, sem manipulação", 1),
    ("parado na mesma posição, sem tocar na máquina", 1),
    ("presente na área, parado próximo ao posto", 1),
    ("presente no posto, próximo a p1", 1),
    ("sem mudança de posição na sequência", 1),
    ("sem mudança de posição, mãos na máquina.", 1),
]


def nomearia(d: str) -> str | None:
    """Proxy determinístico: que raiz do vocabulário esta descrição sustenta.
    None = não sustenta nenhuma → vai para a FILA (que é o comportamento certo,
    não uma falha)."""
    t = d.lower()
    if "ausente" in t or ("vazio" in t and "posto" in t):
        return "posto_vazio"
    if "mãos na máquina" in t or "mãos na estrutura" in t or "operando" in t \
            or "manipulando" in t or "mãos próximas à máquina" in t:
        return "operar_torno"
    if "desenho técnico" in t:
        return "lendo_desenho_tecnico"
    if "deslocado" in t or "deslocou" in t or "afastado" in t or "longe da máquina" in t:
        return "deslocar_pelo_posto"
    # "parado junto/próximo ao torno" sem manipulação É acompanhar a máquina —
    # é literalmente a descrição canônica de `monitorar_maquina` no catálogo
    # ("Operador observando o funcionamento da máquina... sem manipulação
    # direta"). Foi este casamento que o balde roubou.
    if ("torno" in t or "máquina" in t or "bancada" in t or "interação" in t) \
            and ("parado" in t or "sem manipula" in t or "sem tocar" in t
                 or "braços abaixados" in t or "sem mudança" in t or "em pé" in t):
        return "monitorar_maquina"
    return None


_por_rotulo: dict[str, int] = {}
_fila = 0
for _d, _n in REAL_14_08:
    _r = nomearia(_d)
    if _r is None:
        _fila += _n
    else:
        _por_rotulo[_r] = _por_rotulo.get(_r, 0) + _n

_total = sum(n for _, n in REAL_14_08)
_nomeados = _total - _fila
# Snapshot do banco às 18h de 14/08, com o dia AINDA sendo processado (minutos
# depois já eram 330). O que importa é a proporção, não o instante do corte.
check("o corpus real bate com o snapshot do banco (320 eventos, 56 descrições)",
      _total == 320 and len(REAL_14_08) == 56, (_total, len(REAL_14_08)))
print(f"       → nomeados: {_nomeados}/{_total} ({100*_nomeados/_total:.1f}%)")
for _r, _n in sorted(_por_rotulo.items(), key=lambda kv: -kv[1]):
    print(f"          {_r}: {_n}")
print(f"       → para a FILA (o gestor nomeia): {_fila}")

check("a esmagadora maioria vira rótulo de verdade, não balde",
      _nomeados >= 310 and _nomeados / _total >= 0.97, (_nomeados, _total))
check("e o que sobra para a fila é pequeno e honesto", _fila <= 10, _fila)
check("nenhum evento recebe rótulo de desistência",
      not any(pl._e_desistencia(r) for r in _por_rotulo), list(_por_rotulo))
check("a raiz dominante é monitorar_maquina — o rótulo que já existia",
      max(_por_rotulo, key=_por_rotulo.get) == "monitorar_maquina", _por_rotulo)
check("todas as raízes usadas são vocabulário legítimo, nenhuma nova inventada",
      set(_por_rotulo) <= {"monitorar_maquina", "operar_torno", "posto_vazio",
                           "deslocar_pelo_posto", "lendo_desenho_tecnico"},
      set(_por_rotulo))

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
