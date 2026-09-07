"""Fase 112-A — dominância relaxada com fusão de fragmentos.

O que este teste tranca:

    O tracker perde o operador quando ele some atrás do torno e devolve um
    `track_id` novo. A eleição vê vários pedaços curtos, nenhum alcança o piso
    de 60 s, e a identidade nunca se estabelece — 76,2% dos segmentos travam
    por `evidencia_insuficiente` contra 8,4% de ambiguidade entre pessoas.

    A fase costura os pedaços consecutivos. O RISCO da costura é juntar duas
    pessoas num "ocupante" só quando as duas estão no polígono ao mesmo tempo,
    e foi exatamente isso que apareceu num quadro do `20260807_110001`. Por
    isso a guarda de ambiguidade tem teste próprio aqui.

Este teste NÃO lê o texto do arquivo: monta descritores sintéticos e chama
`fundir_fragmentos_posto` e `eleger_operador_segmento` de verdade, nas duas
rotas. Um teste de string passaria com a linha certa e a semântica errada.

Rodar:  python tests_112a_dominancia.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
          "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object

from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond):
    global ok, fail
    print(f"  {'ok  ' if cond else 'FAIL'} {nome}")
    ok += bool(cond)
    fail += not cond


if not hasattr(pl, "fundir_fragmentos_posto"):
    print("  FAIL o patch da Fase 112-A nao foi aplicado "
          "(falta `fundir_fragmentos_posto` no pipeline)")
    sys.exit(1)


INTERVALO = 5.0


def desc(tid, instantes, visivel=None):
    """Descritor mínimo no formato que `fechar_descritores` emite."""
    n = len(instantes)
    return {
        "pessoa_track_id": tid,
        "n_amostras_posto": n,
        "tempo_posto_s": round(n * INTERVALO, 1),
        "tempo_visivel_s": visivel if visivel is not None else round(n * INTERVALO, 1),
        "instantes_posto": sorted(float(t) for t in instantes),
    }


class Regime:
    """Liga/desliga a fase no módulo e restaura no fim.

    Mexer no ambiente não serve: as constantes são lidas na importação.
    """

    def __init__(self, ligada, tempo=15.0, obs=3, gap=15.0):
        self.alvo = {
            "_DOMINANCIA_RELAXADA": ligada,
            "_DOMINANCIA_MIN_TEMPO_POSTO_S": tempo,
            "_DOMINANCIA_MIN_OBS_POSTO": obs,
            "_DOMINANCIA_GAP_FRAG_S": gap,
        }

    def __enter__(self):
        self.antes = {k: getattr(pl, k) for k in self.alvo}
        for k, v in self.alvo.items():
            setattr(pl, k, v)
        return pl

    def __exit__(self, *a):
        for k, v in self.antes.items():
            setattr(pl, k, v)
        return False


# ══════════════════════════════════════════════════════════════════════════
print("\n[1] Chave DESLIGADA — nada muda")
# ══════════════════════════════════════════════════════════════════════════
with Regime(False):
    curtos = [desc(1, [0, 5, 10]), desc(2, [15, 20, 25]), desc(3, [30, 35])]
    r = pl.eleger_operador_segmento(curtos)
    check("tres fragmentos curtos continuam sem eleger",
          r["status"] == "indefinido" and r["motivo"] == "evidencia_insuficiente")

    longo = [desc(1, [5 * i for i in range(14)])]        # 14 x 5 s = 70 s
    r = pl.eleger_operador_segmento(longo)
    check("um track de 70 s continua confirmando (portoes de hoje)",
          r["status"] == "confirmado" and r["track_id"] == 1)

    r = pl.eleger_operador_segmento([desc(1, [0, 5, 10]), desc(2, [15, 20, 25])])
    check("fragmentos vizinhos NAO sao costurados com a chave desligada",
          r["status"] == "indefinido")

# ══════════════════════════════════════════════════════════════════════════
print("\n[2] A costura — fragmentos consecutivos do mesmo ocupante")
# ══════════════════════════════════════════════════════════════════════════
with Regime(True):
    d = [desc(1, [0, 5, 10]), desc(2, [15, 20, 25])]     # buraco de 5 s
    f = pl.fundir_fragmentos_posto(d)
    check("dois tracks vizinhos viram UM ocupante", len(f) == 1)
    check("o ocupante fundido soma as observacoes dos dois",
          len(f) == 1 and f[0]["n_amostras_posto"] == 6)
    check("o tempo fundido e a soma (30 s)",
          len(f) == 1 and abs(f[0]["tempo_posto_s"] - 30.0) < 1e-6)
    check("a fusao registra de quais tracks veio",
          len(f) == 1 and f[0]["fundido_de"] == [1, 2])

    r = pl.eleger_operador_segmento(d)
    check("30 s fundidos passam o piso de 15 s e elegem",
          r["status"] == "confirmado")

    longe = [desc(1, [0, 5, 10]), desc(2, [50, 55, 60])]  # buraco de 40 s
    f = pl.fundir_fragmentos_posto(longe)
    check("buraco maior que o gap NAO costura", len(f) == 2)
    check("cada fragmento distante mantem seu tamanho",
          len(f) == 2 and all(x["n_amostras_posto"] == 3 for x in f))

# ══════════════════════════════════════════════════════════════════════════
print("\n[3] A guarda — duas pessoas no posto ao mesmo tempo")
# ══════════════════════════════════════════════════════════════════════════
with Regime(True):
    # t=15 tem DOIS tracks no posto e nenhum deles e o track 1, que vinha
    # ocupando. E o caso do 20260807_110001: dois no poligono no mesmo quadro.
    amb = [desc(1, [0, 5, 10]), desc(2, [15]), desc(3, [15, 20, 25])]
    f = pl.fundir_fragmentos_posto(amb)
    instantes = [x["instantes_posto"] for x in f]
    check("o fragmento anterior a ambiguidade sobrevive",
          [0.0, 5.0, 10.0] in instantes)
    check("o instante ambiguo NAO e creditado a ninguem",
          all(15.0 not in i for i in instantes))
    check("a costura NAO atravessa a ambiguidade",
          not any(0.0 in i and 20.0 in i for i in instantes))

    # Aqui um dos tracks do instante ambiguo E o ocupante em curso: continua.
    segue = [desc(1, [0, 5, 10, 15, 20]), desc(9, [15])]
    f = pl.fundir_fragmentos_posto(segue)
    check("continuidade de track desempata a ambiguidade", len(f) == 1)
    check("o instante ambiguo desempatado entra no fragmento",
          len(f) == 1 and 15.0 in f[0]["instantes_posto"])
    check("o ocupante fundido herda o track dominante",
          len(f) == 1 and f[0]["pessoa_track_id"] == 1)

# ══════════════════════════════════════════════════════════════════════════
print("\n[4] Robustez — a funcao nao inventa e nao suja")
# ══════════════════════════════════════════════════════════════════════════
with Regime(True):
    antigos = [{"pessoa_track_id": 1, "n_amostras_posto": 4,
                "tempo_posto_s": 20.0, "tempo_visivel_s": 40.0}]
    check("descritor sem `instantes_posto` devolve a lista original",
          pl.fundir_fragmentos_posto(antigos) == antigos)

    check("lista vazia nao quebra", pl.fundir_fragmentos_posto([]) == [])
    check("None nao quebra", pl.fundir_fragmentos_posto(None) == [])

    entrada = [desc(1, [0, 5, 10]), desc(2, [15, 20])]
    copia = [dict(x) for x in entrada]
    pl.fundir_fragmentos_posto(entrada)
    check("a fusao nao altera a lista de entrada", entrada == copia)

    sujo = [desc(1, [0, 5, 10]), {"pessoa_track_id": "x", "instantes_posto": [7]},
            {"pessoa_track_id": 4, "instantes_posto": ["nao-numero"]}]
    try:
        pl.fundir_fragmentos_posto(sujo)
        check("descritor malformado nao levanta excecao", True)
    except Exception as e:
        check(f"descritor malformado nao levanta excecao ({type(e).__name__})", False)

# ══════════════════════════════════════════════════════════════════════════
print("\n[5] A licao da 111E — a identidade nunca afirma ausencia")
# ══════════════════════════════════════════════════════════════════════════
with Regime(True):
    # Nada alcanca o piso nem com a fusao: dois pedacos de 10 s, longe um do
    # outro. O veredito tem que ser "nao sei", nunca "o posto esta vazio".
    r = pl.eleger_operador_segmento([desc(1, [0, 5]), desc(2, [120, 125])])
    check("sem evidencia o status e indefinido", r["status"] == "indefinido")
    check("sem evidencia o track fica nulo", r["track_id"] is None)
    motivo = str(r.get("motivo", "")).lower()
    check("o motivo nao fala em vazio", "vazio" not in motivo)
    check("o motivo nao fala em ausencia", "ausen" not in motivo)
    check("o veredito nao carrega estado de slot",
          "operador_presente" not in r)

    # E com evidencia de sobra, tambem nao inventa presenca de slot.
    r = pl.eleger_operador_segmento([desc(1, [5 * i for i in range(10)])])
    check("com evidencia confirma identidade e so isso",
          r["status"] == "confirmado" and "operador_presente" not in r)

# ══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 64)
print(f"  {ok} ok · {fail} falha(s)")
print("=" * 64)
sys.exit(1 if fail else 0)
