"""Fase 81 — o teto de linhas do PostgREST.

O SINTOMA: o dono selecionou o dia 29, que ele gravou inteiro, e a plataforma
mostrou algumas faixinhas soltas. Pior: o dia 28, que na véspera aparecia
completo, tinha ganhado um buraco entre 8:48 e 9:00. Nada havia sido apagado.

A CAUSA: `.limit(50000)` e `.limit(100000)` não pedem 50 mil nem 100 mil linhas.
O PostgREST corta toda resposta no seu `max-rows` (1000 no Supabase) e devolve
as primeiras 1000 SEM erro, SEM aviso, SEM header — uma resposta truncada é
byte a byte indistinguível de uma completa. Passados 1000 vídeos na campanha,
`montar_analise_diaria` só enxergava os 1000 primeiros; os eventos dos demais
caíam no `if dt0 is None: continue` e sumiam do dia. Como o corte é por ordem
física da tabela, ele SE MOVE a cada gravação nova — por isso um dia cheio
ontem vira um dia esburacado hoje.

Este arquivo prova as duas metades:
  1. o dublê aqui SE COMPORTA COMO O SERVIDOR (nunca devolve mais que o teto),
     e mesmo assim `varrer()` entrega a tabela inteira;
  2. o fonte não voltou a confiar em `.limit()` acima do teto.

Rodar:  python tests_teto_postgrest.py
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

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# ── O dublê que MENTE do mesmo jeito que o servidor ──────────────────────
class QTeto:
    """Aplica o teto do PostgREST: devolve no máximo TETO linhas, sem sinalizar
    que cortou. `.limit(n)` NÃO levanta o teto — é exatamente o mal-entendido
    que causou o bug."""
    TETO = 1000

    def __init__(self, sb, tabela):
        self.sb, self.tabela = sb, tabela
        self.eqs = {}
        self.rng = None
        self.lim = None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def limit(self, n): self.lim = n; return self
    def range(self, i, f): self.rng = (i, f); return self

    def execute(self):
        self.sb.chamadas += 1
        linhas = [l for l in self.sb.dados.get(self.tabela, [])
                  if all(l.get(c) == v for c, v in self.eqs.items())]
        if self.rng is not None:
            linhas = linhas[self.rng[0]: self.rng[1] + 1]
        elif self.lim is not None:
            linhas = linhas[: self.lim]
        return types.SimpleNamespace(data=[dict(l) for l in linhas[: self.TETO]])


class SBTeto:
    def __init__(self, dados): self.dados = dados; self.chamadas = 0
    def table(self, nome): return QTeto(self, nome)


N = 4321
LINHAS = [{"id": f"{i:06d}", "empresa": "U", "processo": "T", "n": i} for i in range(N)]
SB = SBTeto({"eventos": LINHAS})

print("[1] O dublê reproduz o corte silencioso")
cru = SB.table("eventos").select("*").eq("empresa", "U").limit(100000).execute().data
check("`.limit(100000)` devolve só o teto", len(cru) == 1000, len(cru))
check("e nada na resposta diz que faltou linha",
      isinstance(cru, list) and all(isinstance(l, dict) for l in cru))

print("\n[2] varrer() atravessa o teto")
todas = pl.varrer(SB, "eventos", "id, n", empresa="U", processo="T")
check("devolve a tabela INTEIRA", len(todas) == N, len(todas))
check("sem repetir linha", len({l["id"] for l in todas}) == N)
check("sem pular linha", {l["n"] for l in todas} == set(range(N)))
check("na ordem da chave de paginação",
      [l["id"] for l in todas] == sorted(l["id"] for l in todas))
check("gastando ceil(N/teto)+1 requisições, não uma por linha",
      SB.chamadas <= (N // 1000) + 3, SB.chamadas)

print("\n[3] Os filtros continuam valendo (varredura não é vazamento)")
SB2 = SBTeto({"eventos": LINHAS + [{"id": "zzz", "empresa": "OUTRA",
                                    "processo": "T", "n": -1}]})
so_minhas = pl.varrer(SB2, "eventos", "id", empresa="U", processo="T")
check("outra empresa não entra", all(l["id"] != "zzz" for l in so_minhas))
check("empresa=None varre tudo (uso interno do orquestrador)",
      len(pl.varrer(SB2, "eventos", "id")) == N + 1)

print("\n[4] O caso real: o dia inteiro volta a aparecer")
# Reprodução mínima do que quebrou: mais de 1000 vídeos, e os eventos do dia
# que interessa pendurados nos ÚLTIMOS — justamente os que o corte descartava.
VIDS = [{"id": f"v{i:05d}", "empresa": "U", "processo": "T",
         "nome": f"cam1_seg_202607{28 + i % 2:02d}_{6 + i % 8:02d}0000_roi.mp4",
         "duracao_s": 300, "gravado_em": None, "processado_em": None}
        for i in range(1400)]
SB3 = SBTeto({"videos": VIDS})
lidos = pl.varrer(SB3, "videos", "id, nome", empresa="U", processo="T")
check("todos os vídeos do processo são lidos", len(lidos) == 1400, len(lidos))
dias = {pl._inicio_video_dt(v).date().isoformat() for v in lidos
        if pl._inicio_video_dt(v)}
check("e os dois dias de gravação aparecem", dias == {"2026-07-28", "2026-07-29"}, dias)
truncado = SB3.table("videos").select("*").eq("empresa", "U").limit(50000).execute().data
check("com o padrão antigo, 400 vídeos sumiriam", len(truncado) == 1000, len(truncado))

print("\n[5] O fonte não pode voltar a confiar em .limit()")
import re  # noqa: E402
from pathlib import Path  # noqa: E402

GRANDES = ("eventos", "videos", "segmentos", "comportamentos",
           "padroes_processo", "perguntas_processo", "heartbeats_edge")
ARQS = ["backend/pipeline.py", "backend/main.py", "backend/orquestrador_lote.py"]
suspeitos = []
for arq in ARQS:
    linhas = Path(arq).read_text().splitlines()
    for i, l in enumerate(linhas):
        m = re.search(r'\.table\("([a-z_]+)"\)', l)
        if not m or m.group(1) not in GRANDES:
            continue
        bloco = []
        for j in range(i, min(i + 18, len(linhas))):
            bloco.append(linhas[j].strip())
            if ".execute()" in linhas[j]:
                break
        txt = " ".join(bloco)
        if any(k in txt for k in (".insert(", ".update(", ".upsert(", ".delete(",
                                  ".range(", ".single()", ".maybe_single()", "count=")):
            continue
        lim = re.search(r"\.limit\((\d+)\)", txt)
        if lim and int(lim.group(1)) > pl.TETO_POSTGREST:
            suspeitos.append(f"{arq}:{i + 1} limit({lim.group(1)}) em {m.group(1)}")

check("nenhuma leitura pede acima do teto achando que recebe",
      not suspeitos, "\n      " + "\n      ".join(suspeitos))
check("o teto está declarado em um lugar só", pl.TETO_POSTGREST == 1000)

print("\n[6] Os pontos que o dono viu quebrados passam por varrer()")
pipe = Path("backend/pipeline.py").read_text()
mn = Path("backend/main.py").read_text()
i_dia = pipe.index("def montar_analise_diaria(")
bloco_dia = pipe[i_dia: i_dia + 3000]
check("Dia a dia: vídeos paginados",
      'varrer(sb, "videos"' in bloco_dia)
check("Dia a dia: eventos paginados",
      'varrer(\n        sb, "eventos"' in bloco_dia or 'varrer(sb, "eventos"' in bloco_dia)
i_dash = mn.index("def dashboard(")
check("Dashboard: eventos e vídeos paginados",
      'varrer(\n        sb, "eventos"' in mn[i_dash: i_dash + 2500]
      and 'varrer(\n        sb, "videos"' in mn[i_dash: i_dash + 2500])
i_serie = pipe.index("def montar_serie_temporal(")
bloco_serie = pipe[i_serie: i_serie + 2000]
check("Série temporal: pega os vídeos MAIS RECENTES, não os mais antigos",
      'desc=True' in bloco_serie and "reversed(videos)" in bloco_serie)

print(f"\n{'=' * 56}\n  {ok} ok · {fail} falha(s)\n{'=' * 56}")
sys.exit(1 if fail else 0)
