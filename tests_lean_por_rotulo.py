"""Fase 60 — reclassificar Lean PELO RÓTULO (o bug dos 'não classificados').

O que este arquivo trava:
  1) rótulo SEM linha em `comportamentos` passa a ser classificável — a linha
     é criada na hora (era aqui que a tela morria: sem id, sem PUT);
  2) rótulo COM linha reusa a linha, sem duplicar;
  3) a decisão desce para os eventos daquele processo;
  4) escopo: processo errado / empresa errada não são tocados;
  5) categoria inválida continua sendo recusada (400).

Rodar:  python tests_lean_por_rotulo.py
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

from fastapi import HTTPException  # noqa: E402
from backend import main as mn  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# ── Fake Supabase: aplica eq/neq/or_ de verdade e suporta insert ──────────
_SEQ = [0]


class FakeQ:
    def __init__(self, sb, tabela, modo, payload=None):
        self.sb, self.tabela, self.modo, self.payload = sb, tabela, modo, payload
        self.eqs, self.neqs, self.isnull, self.ors = {}, {}, [], None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self

    def eq(self, campo, valor): self.eqs[campo] = valor; return self
    def neq(self, campo, valor): self.neqs[campo] = valor; return self
    def is_(self, campo, _v): self.isnull.append(campo); return self
    def or_(self, expr): self.ors = expr; return self

    def _casa(self, linha):
        for c, v in self.eqs.items():
            if linha.get(c) != v:
                return False
        for c, v in self.neqs.items():
            if linha.get(c) == v:
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
            _SEQ[0] += 1
            nova = {"id": f"novo-{_SEQ[0]}", **self.payload}
            linhas.append(nova)
            self.sb.escritas.append((self.tabela, nova["id"], dict(self.payload)))
            return types.SimpleNamespace(data=[dict(nova)])
        casam = [l for l in linhas if self._casa(l)]
        if self.modo == "update":
            for l in casam:
                l.update(self.payload)
                self.sb.escritas.append((self.tabela, l.get("id"), dict(self.payload)))
        return types.SimpleNamespace(data=[dict(l) for l in casam])


class FakeSB:
    def __init__(self, dados):
        self.dados = dados
        self.escritas = []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome, "select")
            def update(self, payload): return FakeQ(sb, nome, "update", payload)
            def insert(self, payload): return FakeQ(sb, nome, "insert", payload)
        return T()


class User:
    empresa = "U"
    email = "gestor@u.com"


def ev(id_, label, cat=None, origem=None, corrigido=None, seg=60,
       processo="Torneamento", empresa="U"):
    return {"id": id_, "empresa": empresa, "processo": processo,
            "comportamento_label": label, "label_corrigido": corrigido,
            "tempo_inicio_s": 0, "tempo_fim_s": seg,
            "categoria_lean": cat, "categoria_lean_origem": origem,
            "principal": True, "validacao_correto": None}


def montar(dados):
    """Instala o FakeSB e um _processo_nome fixo, devolvendo o fake."""
    sb = FakeSB(dados)
    mn.make_supabase_client = lambda *a, **k: sb
    mn._processo_nome = lambda _sb, _u, _pid: "Torneamento"
    return sb


print("\n[1] Rótulo SEM linha no catálogo — o caso que travava a tela")
dados = {
    "comportamentos": [],
    "eventos": [ev("e1", "ajustar_placa"), ev("e2", "ajustar_placa")],
}
sb = montar(dados)
r = mn.setar_categoria_lean_por_label(
    "proc-1",
    mn.CategoriaLeanPorLabelBody(label="ajustar_placa", categoria_lean="valor_agregado"),
    User(),
)
comps = dados["comportamentos"]
check("linha do catálogo foi criada", len(comps) == 1, comps)
check("criada no (empresa, processo) certos",
      comps and comps[0]["empresa"] == "U" and comps[0]["processo"] == "Torneamento", comps)
check("categoria gravada com origem 'humano'",
      comps and comps[0]["categoria_lean"] == "valor_agregado"
      and comps[0]["categoria_lean_origem"] == "humano", comps)
check("resposta devolve o id criado", bool(r.get("comportamento_id")), r)
check("os 2 eventos do rótulo desceram",
      all(e["categoria_lean"] == "valor_agregado" for e in dados["eventos"]),
      dados["eventos"])
check("eventos_atualizados == 2", r.get("eventos_atualizados") == 2, r)

print("\n[2] Rótulo COM linha — reusa, não duplica")
dados = {
    "comportamentos": [{"id": "c1", "empresa": "U", "processo": "Torneamento",
                        "label": "operar_torno", "categoria_lean": None,
                        "categoria_lean_origem": None}],
    "eventos": [ev("e1", "operar_torno")],
}
sb = montar(dados)
r = mn.setar_categoria_lean_por_label(
    "proc-1",
    mn.CategoriaLeanPorLabelBody(label="operar_torno", categoria_lean="desperdicio"),
    User(),
)
check("continua com 1 linha só", len(dados["comportamentos"]) == 1, dados["comportamentos"])
check("usou a linha existente", r.get("comportamento_id") == "c1", r)
check("categoria aplicada", dados["comportamentos"][0]["categoria_lean"] == "desperdicio")
check("nenhum insert em comportamentos",
      not any(t == "comportamentos" and str(i).startswith("novo-") for t, i, _ in sb.escritas),
      sb.escritas)

print("\n[3] Override 'humano' do evento continua inviolável")
dados = {
    "comportamentos": [],
    "eventos": [
        ev("e1", "medir_peca"),
        ev("e2", "medir_peca", "desperdicio", "humano"),
    ],
}
montar(dados)
mn.setar_categoria_lean_por_label(
    "proc-1",
    mn.CategoriaLeanPorLabelBody(label="medir_peca", categoria_lean="valor_agregado"),
    User(),
)
e2 = next(e for e in dados["eventos"] if e["id"] == "e2")
check("evento marcado por humano NÃO mudou",
      e2["categoria_lean"] == "desperdicio" and e2["categoria_lean_origem"] == "humano", e2)

print("\n[4] Escopo — outro processo e outra empresa não são tocados")
dados = {
    "comportamentos": [],
    "eventos": [
        ev("a", "andar", processo="Torneamento"),
        ev("b", "andar", processo="Fresagem"),
        ev("c", "andar", processo="Torneamento", empresa="OUTRA"),
    ],
}
montar(dados)
mn.setar_categoria_lean_por_label(
    "proc-1",
    mn.CategoriaLeanPorLabelBody(label="andar", categoria_lean="desperdicio"),
    User(),
)
por_id = {e["id"]: e for e in dados["eventos"]}
check("evento do processo alvo recebeu", por_id["a"]["categoria_lean"] == "desperdicio")
check("evento de OUTRO processo intacto", por_id["b"]["categoria_lean"] is None, por_id["b"])
check("evento de OUTRA empresa intacto", por_id["c"]["categoria_lean"] is None, por_id["c"])

print("\n[5] Rótulo renomeado à mão (label_corrigido) também é alcançado")
dados = {
    "comportamentos": [],
    "eventos": [ev("e1", "acao_indefinida", corrigido="conferir_desenho")],
}
montar(dados)
mn.setar_categoria_lean_por_label(
    "proc-1",
    mn.CategoriaLeanPorLabelBody(label="conferir_desenho", categoria_lean="valor_agregado"),
    User(),
)
check("evento com label_corrigido desceu",
      dados["eventos"][0]["categoria_lean"] == "valor_agregado", dados["eventos"][0])

print("\n[6] Guardas de entrada")
montar({"comportamentos": [], "eventos": []})
try:
    mn.setar_categoria_lean_por_label(
        "proc-1",
        mn.CategoriaLeanPorLabelBody(label="x", categoria_lean="apoio"),
        User(),
    )
    check("categoria inválida recusada", False, "não levantou")
except HTTPException as e:
    check("categoria inválida recusada com 400", e.status_code == 400, e.detail)

dados = {"comportamentos": [], "eventos": []}
montar(dados)
r = mn.setar_categoria_lean_por_label(
    "proc-1",
    mn.CategoriaLeanPorLabelBody(label="rotulo_novo", categoria_lean=None),
    User(),
)
check("null limpa a categoria (libera pra IA)",
      dados["comportamentos"][0]["categoria_lean"] is None
      and dados["comportamentos"][0]["categoria_lean_origem"] is None,
      dados["comportamentos"])
check("null não propaga para eventos", r.get("eventos_atualizados") == 0, r)

print(f"\n{'='*54}\n  {ok} ok · {fail} falha(s)\n{'='*54}")
sys.exit(1 if fail else 0)
