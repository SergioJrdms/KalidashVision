"""Fase 75 — coluna NOT NULL vai SEMPRE explícita no INSERT em lote.

O incidente: processar um vídeo quebrou com

    null value in column "em_duvida" of relation "eventos"
    violates not-null constraint  (23502)

`em_duvida` é NOT NULL DEFAULT false, mas só era escrito quando alguma camada
disparava. Num INSERT em lote o PostgREST UNIFICA as colunas de todas as linhas
do chunk: basta UMA linha trazer a chave para que as demais sejam enviadas com
NULL EXPLÍCITO — e o DEFAULT do Postgres não se aplica a NULL explícito.

Ficou latente desde a Fase 57 porque nenhuma camada estava ATIVA: nenhuma linha
trazia a chave, o lote era homogêneo e o DEFAULT valia. As camadas de
contradição (Fases 68/69) tornaram o lote heterogêneo e o bug apareceu no
primeiro vídeo processado.

Este teste lê o SCHEMA e exige que TODA coluna NOT NULL de `eventos` apareça
explicitamente no dict — para que a próxima coluna NOT NULL não repita isto.

Rodar:  python tests_not_null_lote.py
"""
import sys, types, os, re

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

from pathlib import Path  # noqa: E402
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
        self.eqs = {}

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        if self.modo == "insert":
            lote = self.payload if isinstance(self.payload, list) else [self.payload]
            if self.tabela == "eventos":
                self.sb.lotes.append(lote)
            novas = []
            for pay in lote:
                _SEQ[0] += 1
                n = {"id": f"id-{_SEQ[0]}", **pay}
                linhas.append(n); novas.append(dict(n))
            return types.SimpleNamespace(data=novas)
        casam = [l for l in linhas if all(l.get(c) == v for c, v in self.eqs.items())]
        return types.SimpleNamespace(data=[dict(l) for l in casam])


class FakeSB:
    def __init__(self, dados=None):
        self.dados = dados or {}
        self.lotes = []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome, "select")
            def insert(self, p): return FakeQ(sb, nome, "insert", p)
            def update(self, p): return FakeQ(sb, nome, "update", p)
        return T()


def ev(i, *, camadas=None, duvida=False):
    e = {
        "pessoa_track_id": 1, "comportamento_label": "operar_torno",
        "descricao_bruta": "operando o torno", "tempo_inicio_s": i * 60,
        "tempo_fim_s": i * 60 + 60, "frame_inicio": i * 30, "frame_fim": i * 30 + 30,
        "bbox_inicio": [0, 0, 10, 10], "zona_contexto": "Posto do Torneiro",
        "n_amostras": 10, "confianca": 0.6, "principal": True,
        "papel_pessoa": "operador",
    }
    if camadas:
        e["camadas_disparadas"] = camadas
        e["em_duvida"] = duvida
        e["duvida_motivo"] = "contradição"
    return e


INFO = {"duracao_s": 300.0, "fps": 10.0, "largura": 640, "altura": 480}


def persistir(eventos, auditoria=None):
    sb = FakeSB()
    pl.etapa_persistir(sb, "U", "T", "/tmp/v.mp4", INFO, eventos, [1],
                       {"operar_torno": "torno"}, lambda d: "pendente",
                       caminho_storage="u/t/seg.mp4", eventos_auditoria=auditoria)
    return sb


print("\n[1] O LOTE HETEROGÊNEO — a reprodução exata do incidente")
sb = persistir([
    ev(0, camadas=[{"nome": "contradicao", "modo": "ativa"}], duvida=True),
    ev(1),
    ev(2),
])
lote = sb.lotes[0]
check("as 3 linhas foram no mesmo lote", len(lote) == 3, len(lote))
check("TODAS trazem em_duvida (nenhuma vira NULL na unificação)",
      all("em_duvida" in l for l in lote),
      [sorted(set(l) ^ set(lote[0])) for l in lote])
check("nenhuma tem em_duvida None",
      all(l["em_duvida"] is not None for l in lote), [l["em_duvida"] for l in lote])
check("a linha da camada preserva em_duvida=True", lote[0]["em_duvida"] is True)
check("as demais ficam False",
      lote[1]["em_duvida"] is False and lote[2]["em_duvida"] is False)
check("só a linha da camada carrega camadas_disparadas (coluna NULLABLE)",
      "camadas_disparadas" in lote[0] and "camadas_disparadas" not in lote[1])

print("\n[2] Auditoria vai no MESMO lote e também precisa da chave")
sb = persistir([ev(0, camadas=[{"nome": "c", "modo": "ativa"}], duvida=True)],
               auditoria=[ev(5)])
lote = sb.lotes[0]
check("principais + auditoria no mesmo lote", len(lote) == 2, len(lote))
check("a linha de auditoria também traz em_duvida",
      all("em_duvida" in l for l in lote), lote)
check("auditoria nasce sem dúvida",
      lote[-1]["origem_validacao"] == "auditoria" and lote[-1]["em_duvida"] is False)

print("\n[3] Lote homogêneo continua correto (não era só sorte antes)")
sb = persistir([ev(0), ev(1)])
lote = sb.lotes[0]
check("sem camada nenhuma, em_duvida ainda vem explícito",
      all(l["em_duvida"] is False for l in lote), lote)

print("\n[4] A REGRA, lida do schema — pega a PRÓXIMA coluna NOT NULL")
schema = Path("sql/schema.sql").read_text()
nn_alter = set(re.findall(
    r"alter\s+table\s+eventos\s+add\s+column\s+if\s+not\s+exists\s+(\w+)[^;]*?not\s+null",
    schema, re.I))
m = re.search(r"create table if not exists eventos\s*\((.*?)\n\);", schema, re.S | re.I)
nn_create = set()
if m:
    for linha in m.group(1).splitlines():
        mm = re.match(r"\s*(\w+)\s+[\w\s()]*?not\s+null", linha, re.I)
        if mm and "primary key" not in linha.lower():
            nn_create.add(mm.group(1))
not_nulls = (nn_alter | nn_create) - {"id"}
check("o schema declara colunas NOT NULL em eventos", len(not_nulls) >= 6, not_nulls)
check("em_duvida está entre elas (senão o teste não prova nada)",
      "em_duvida" in not_nulls, not_nulls)

for nome_lote, lote_alvo in (("principal", persistir([ev(0), ev(1)]).lotes[0][0]),
                             ("auditoria",
                              persistir([ev(0)], auditoria=[ev(5)]).lotes[0][-1])):
    faltando = sorted(c for c in not_nulls if c not in lote_alvo)
    check(f"[{nome_lote}] TODA coluna NOT NULL vai explícita",
          not faltando,
          f"faltando: {faltando} — num lote heterogêneo virariam NULL e o "
          f"vídeo inteiro falharia com 23502")

print("\n[5] A regra está escrita onde quem editar o dict vai ler")
src = Path("backend/pipeline.py").read_text()
check("o comentário explica a unificação do PostgREST", "UNIFICA as colunas" in src)
check("e enuncia a regra geral", "coluna NOT NULL vai SEMPRE explícita" in src)
check("e diz por que ficou latente até agora", "nenhuma camada estava ATIVA" in src)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
