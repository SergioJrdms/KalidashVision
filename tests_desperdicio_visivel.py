"""Fase 104 — O DESPERDÍCIO VOLTA A SER VISÍVEL, e o horário abre.

Dois relatos sobre a mesma faixa ("A jornada típica — todos os dias"):

  A. *"o vermelho virou linhas extremamente finas... Não quero que o verde
     consuma elas também."* O desperdício estava lá, na conta, mas não no olho.

  B. *"ao clicar, nada acontece, e ao clicar deveria abrir os eventos que
     compõem aquela hora/segmento."*

A CAUSA DE (A) ERA A ORDEM, NÃO O DADO. A suavização espelhava as fatias
dentro do bloco para que as pontas iguais se encontrassem — mas com `desp` no
MEIO (`va, desp, vazio` / `vazio, desp, va`), verde encostava em verde e cinza
em cinza na virada, e o vermelho era a ÚNICA categoria que nunca encontrava a
sua. Ficava isolado entre dois blocos gordos, com 2 px, e o verde desenhado
depois passava por cima. A menor fatia era também a mais castigada — justo a
que o gestor precisa ver.

Agora `desp` é o EIXO do espelho, o vermelho funde com o vermelho do bloco
seguinte, tem piso de largura desenhada e fica na camada de cima.

⚠️ O QUE ESTA SUÍTE PROTEGE ACIMA DE TUDO: nenhum número muda. Reordenar
dentro do bloco é legítimo porque a ordem dentro do bloco não carrega horário;
inflar a SOMA de uma categoria não seria. O piso é de DESENHO — a fatia
transborda por cima da vizinha em vez de empurrá-la, então nenhum horário se
desloca.

(B) era um gate: os alvos de clique só eram desenhados com `!agregado`, e
`eventos_do_bin` exigia um dia. No agregado o bloco não pertence a UM dia —
`dia = None` agora soma o mesmo horário sobre todos, e cada trecho volta com
o dia a que pertence.

Rodar:  python tests_desperdicio_visivel.py
"""
import json, os, subprocess, sys, types

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

pl._PERMANENCIA = False
ok = fail = 0
FRONT = os.path.join(RAIZ, "frontend", "src")


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


D2 = open(os.path.join(FRONT, "pages", "Dashboard2.tsx"), encoding="utf-8").read()

# Roda a função DE VERDADE (esbuild + node): a regra, não as palavras.
_ini = D2.index("const BIN_MIN = 15;")
_fim = D2.index("function JornadaDoDia")
_TS = os.path.join(RAIZ, ".desp_teste.ts")
open(_TS, "w", encoding="utf-8").write(
    D2[_ini:_fim] + "\nexport { suavizarJornada };\n")
_ESBUILD = os.path.join(RAIZ, "frontend", "node_modules", ".bin", "esbuild")


def js(expr: str):
    r = subprocess.run([_ESBUILD, _TS, "--format=cjs", "--loader:.ts=ts"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    out = subprocess.run(
        ["node", "-e", r.stdout + f"\nconsole.log(JSON.stringify({expr}))"],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-400:])
    return json.loads(out.stdout)


def soma(fs):
    s = {}
    for f in fs:
        s[f["cat"]] = round(s.get(f["cat"], 0) + (f["fim_m"] - f["ini_m"]), 6)
    return s


# Quatro blocos de 15 min do agregado: cada um com muito verde, pouco vermelho
# e um pedaço de cinza — a forma real do dado que virava confete.
def bloco(b, va, desp, vazio):
    m = b * 15.0
    fs = []
    for cat, larg in (("va", va), ("desp", desp), ("vazio", vazio)):
        if larg > 0:
            fs.append({"ini_m": m, "fim_m": m + larg, "cat": cat})
            m += larg
    return fs


CRU = bloco(32, 12, 1, 2) + bloco(33, 11, 1, 3) + bloco(34, 13, 1, 1) + bloco(35, 12, 1, 2)
SUAVE = js(f"suavizarJornada({json.dumps(CRU)}, true)")


# ══════════════ [1] Nenhum número muda — a trava principal ══════════════
print("\n[1] Nenhum número muda (é conserto de desenho)")
check("a soma de cada categoria é IDÊNTICA", soma(SUAVE) == soma(CRU),
      (soma(SUAVE), soma(CRU)))
check("o desperdício soma exatamente os mesmos 4 min",
      soma(SUAVE)["desp"] == 4.0, soma(SUAVE))
check("a faixa começa e termina no mesmo instante",
      (SUAVE[0]["ini_m"], SUAVE[-1]["fim_m"])
      == (CRU[0]["ini_m"], CRU[-1]["fim_m"]))
check("nenhuma fatia sai da janela dos blocos de origem",
      all(480.0 <= f["ini_m"] <= f["fim_m"] <= 540.0 for f in SUAVE))


# ══════════════ [2] O vermelho engorda — por FUSÃO, não por invenção ════
print("\n[2] O vermelho para de ser fio de cabelo")

vermelhos = [f for f in SUAVE if f["cat"] == "desp"]
cruas_vermelhas = [f for f in CRU if f["cat"] == "desp"]
check("antes eram 4 fatias vermelhas de 1 min", len(cruas_vermelhas) == 4)
check("⭐ agora são MENOS marcas vermelhas (elas se encontraram)",
      len(vermelhos) < len(cruas_vermelhas), len(vermelhos))
check("⭐ e cada marca é MAIS LARGA que a fatia crua",
      max(f["fim_m"] - f["ini_m"] for f in vermelhos)
      > max(f["fim_m"] - f["ini_m"] for f in cruas_vermelhas))
check("a maior marca vermelha dobrou (1 min → 2 min)",
      max(f["fim_m"] - f["ini_m"] for f in vermelhos) == 2.0, vermelhos)
check("`desp` é o EIXO do espelho na tabela de ordem",
      "const ORDEM_CAT: Record<string, number> = { va: 0, vazio: 1, desp: 2 };" in D2)
check("com o motivo escrito no código",
      "O VERMELHO VIRAVA FIO DE CABELO" in D2
      and "NUNCA encostava" in D2)


# ══════════════ [3] O verde não come mais o vermelho ════════════════════
print("\n[3] O verde não desenha por cima do vermelho")

check("existe uma tabela de camada por categoria",
      "const CAMADA_CAT: Record<string, number> = { va: 1, vazio: 2, desp: 3 };" in D2)
check("⭐ o vermelho fica na camada MAIS ALTA (o pedido explícito)",
      D2.index("desp: 3 }") > 0 and "zIndex: CAMADA_CAT[f.cat]" in D2)
check("existe PISO de largura desenhada", "const MIN_FATIA_PX = 3;" in D2
      and "minWidth: MIN_FATIA_PX" in D2)
check("o piso é de DESENHO: transborda por cima, não empurra a vizinha",
      "transborda alguns pixels por cima da vizinha" in D2
      and "vez de empurrá-la, então nenhum horário se desloca" in D2)
check("a posição continua vindo do horário real (left = ini_m)",
      "left: `${((f.ini_m - ini) / span) * 100}%`" in D2)
check("a transparência saiu (8% em 3 px virava rosa sobre o trilho)",
      "opacity: 1," in D2 and "opacity: 0.92" not in D2)
check("a faixa ficou mais alta (fatia fina precisa de altura para ser vista)",
      "height: 56" in D2 and "position: \"relative\", height: 46" not in D2)


# ══════════════ [4] O clique agora existe no agregado ═══════════════════
print("\n[4] Clicar na jornada típica abre o horário")

render = D2[D2.index("function JornadaDoDia"):]
check("⭐ os alvos de clique não são mais escondidos por `!agregado`",
      "{!agregado && [...binsCobertos]" not in render
      and "{[...binsCobertos].sort" in render)
check("⭐ o painel de detalhe também abre no agregado",
      "{binSel != null && (" in render and "{!agregado && binSel != null" not in render)
check("no agregado o dia vai NULO (o bloco não pertence a um dia)",
      "dia={agregado ? null : d.dia}" in render)
check("o alvo de clique fica ACIMA das fatias coloridas",
      "zIndex: 5," in render)
check("os alvos continuam vindo das faixas CRUAS, não das suavizadas",
      "for (const f of faixasCruas) {" in render)
check("a legenda convida ao clique nos dois modos",
      "clique num bloco de 15 min para abrir" in render
      and "{!agregado && (\n          <span style={{ color: \"var(--faint)\" }}>· clique" not in render)

API = open(os.path.join(FRONT, "lib", "api.ts"), encoding="utf-8").read()
check("o cliente aceita `dia = null` e OMITE o parâmetro",
      "bin: (processoId: string, dia: string | null, minuto: number)" in API
      and '(dia ? `&dia=${dia}` : "")' in API)
TIPOS = open(os.path.join(FRONT, "lib", "types.ts"), encoding="utf-8").read()
check("o tipo do bloco admite dia nulo e conta os dias",
      "dia: string | null;" in TIPOS and "agregado: boolean;" in TIPOS
      and "n_dias: number;" in TIPOS)


# ══════════════ [5] O servidor: o mesmo horário sobre todos os dias ═════
print("\n[5] `eventos_do_bin` no modo agregado")


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
        r = [dict(l) for l in self.sb.dados.get(self.tabela, [])
             if all(l.get(c) == v for c, v in self.eqs.items())
             and all(l.get(c) in vs for c, vs in self.ins.items())]
        if self.ordem:
            r.sort(key=lambda x: str(x.get(self.ordem)))
        self.sb.leituras.append(self.tabela)
        if self.faixa is not None:
            a, b = self.faixa
            r = r[a: b + 1]
        else:
            r = r[: pl.TETO_POSTGREST]
        return types.SimpleNamespace(data=r)


class FakeSB:
    def __init__(self, dados):
        self.dados, self.escritas, self.leituras = dados, [], []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome)
            def update(self, p): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
            def insert(self, p): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
            def upsert(self, p, **k): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
            def delete(self): sb.escritas.append((nome, "delete")); return FakeQ(sb, nome)
        return T()


# Três dias, mesmo horário: 08:00 começa o vídeo, e cada dia tem 10 min de
# torno + 5 min de espera no bloco 08:00–08:15 (bin 32).
DIAS = ["2026-08-04", "2026-08-05", "2026-08-06"]
VIDEOS, EVS = [], []
for k, d in enumerate(DIAS):
    vid = f"v{k}"
    VIDEOS.append({"id": vid, "empresa": "U", "processo": "T",
                   "nome": f"seg_{d.replace('-', '')}_080000.mp4",
                   "cam_id": "cam1", "duracao_s": 3600,
                   "processado_em": f"{d}T09:30:00"})
    for j, (lbl, ini, dur) in enumerate(
            [("operar_torno", 0, 600), ("aguardar_ciclo", 600, 300)]):
        EVS.append({"id": f"e{k}{j}", "video_id": vid, "empresa": "U",
                    "processo": "T", "comportamento_label": lbl,
                    "label_corrigido": None, "descricao_bruta": f"{lbl} no dia {d}",
                    "tempo_inicio_s": ini, "tempo_fim_s": ini + dur,
                    "principal": True, "papel_pessoa": "operador",
                    "origem_validacao": "vlm", "validado_humano": False,
                    "validacao_correto": None, "confianca": 0.8, "n_amostras": 6,
                    "em_duvida": False, "pessoa_track_id": 3,
                    "versao_instrumento": 3})
COMPS = [{"label": "operar_torno", "empresa": "U", "processo": "T",
          "categoria_lean": "valor_agregado"},
         {"label": "aguardar_ciclo", "empresa": "U", "processo": "T",
          "categoria_lean": "desperdicio"}]


def novo_sb():
    return FakeSB({"eventos": list(EVS), "videos": list(VIDEOS),
                   "comportamentos": list(COMPS)})


SB = novo_sb()
agg = pl.eventos_do_bin(SB, "U", "T", None, 8 * 60 + 7)
check("abriu sem erro e no bloco certo",
      (agg.get("de"), agg.get("ate")) == ("08:00", "08:15"), agg.get("erro"))
check("⭐ marca que é agregado", agg["agregado"] is True)
check("⭐ soma os TRÊS dias (2 eventos × 3 dias)", agg["n_eventos"] == 6,
      agg["n_eventos"])
check("e diz quantos dias entraram", agg["n_dias"] == 3 and agg["dias"] == DIAS,
      (agg["n_dias"], agg["dias"]))
check("⭐ cada trecho carrega o DIA dele (09:12 acontece todo dia)",
      all(it.get("dia") in DIAS for it in agg["itens"]))
check("os trechos vêm ordenados por dia e depois por hora",
      [(it["dia"], it["hora"]) for it in agg["itens"]]
      == sorted((it["dia"], it["hora"]) for it in agg["itens"]))
check("as proporções são as do conjunto (2/3 produtivo, 1/3 desperdício)",
      round(agg["por_categoria"]["va"]["pct"]) == 67
      and round(agg["por_categoria"]["desp"]["pct"]) == 33,
      agg["por_categoria"])
check("a nota avisa que NÃO é um dia real",
      "não é um dia real" in agg["nota"] and "3 dia(s)" in agg["nota"],
      agg["nota"])
check("abrir continua não sendo validar (zero escrita)", not SB.escritas)

um = pl.eventos_do_bin(novo_sb(), "U", "T", "2026-08-05", 8 * 60 + 7)
check("o modo DIA ÚNICO não mudou: só os eventos daquele dia",
      um["n_eventos"] == 2 and {it["dia"] for it in um["itens"]} == {"2026-08-05"},
      um["n_eventos"])
check("e ele não se declara agregado", um["agregado"] is False)
check("a soma dos dias bate com o agregado",
      sum(pl.eventos_do_bin(novo_sb(), "U", "T", d, 8 * 60 + 7)["n_eventos"]
          for d in DIAS) == agg["n_eventos"])
check("data inválida continua recusada",
      "erro" in pl.eventos_do_bin(novo_sb(), "U", "T", "06/08/2026", 480))

vazio = pl.eventos_do_bin(FakeSB({"eventos": [], "videos": [],
                                  "comportamentos": []}), "U", "T", None, 480)
check("processo sem vídeo devolve vazio explicado, não erro",
      vazio["n_eventos"] == 0 and "Nenhum vídeo processado" in vazio["nota"])

MAIN = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()
check("o endpoint aceita `dia` opcional",
      "dia: str | None = Query(" in MAIN and "OMITIDO = modo " in MAIN)

os.remove(_TS)
print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
