"""Fase 55 — propagação da categoria Lean: comportamento → eventos.

Critérios de aceitação cobertos sem banco:
  1) dry_run não escreve   2) override 'humano' é inviolável
  3) acao_indefinida segue sem categoria   4) backfill idempotente
  6) evento nasce com categoria (herança na ingestão)
Rodar:  python tests_lean_propagacao.py
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
sys.modules["numpy"].array = lambda s, dtype=None: [list(r) for r in s]
sys.modules["cv2"].pointPolygonTest = lambda *a, **k: -1.0
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


# ── Fake Supabase que aplica os filtros de verdade ────────────────────────
class FakeQ:
    def __init__(self, sb, tabela, modo, payload=None):
        self.sb, self.tabela, self.modo, self.payload = sb, tabela, modo, payload
        self.eqs, self.isnull, self.ors = {}, [], None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self

    def eq(self, campo, valor): self.eqs[campo] = valor; return self

    def is_(self, campo, _valor): self.isnull.append(campo); return self

    def or_(self, expr): self.ors = expr; return self

    def _casa(self, linha):
        for c, v in self.eqs.items():
            if linha.get(c) != v:
                return False
        for c in self.isnull:
            if linha.get(c) is not None:
                return False
        # ⚠️ O dublê INTERPRETA o `or_`, não compara a string dele.
        # Antes ele casava por igualdade exata com a expressão esperada — então
        # qualquer mudança na string (mesmo correta) fazia o filtro sumir
        # silenciosamente e TODOS os eventos passarem. O teste acusava
        # regressão onde não havia, e — pior — deixaria de acusar onde houvesse,
        # porque um filtro ausente é permissivo. Agora ele parseia
        # `col.is.null` / `col.eq.valor`, como o PostgREST.
        if self.ors:
            passou = False
            for termo in self.ors.split(","):
                partes = termo.strip().split(".", 2)
                if len(partes) != 3:
                    continue
                col, op, val = partes
                if op == "is" and val == "null":
                    passou = passou or linha.get(col) is None
                elif op == "eq":
                    passou = passou or linha.get(col) == val
            if not passou:
                return False
        return True

    def execute(self):
        linhas = self.sb.dados.get(self.tabela, [])
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
        return T()


def ev(id_, label, cat=None, origem=None, corrigido=None, seg=60,
       processo="Torneamento", principal=True):
    return {"id": id_, "empresa": "U", "processo": processo,
            "comportamento_label": label, "label_corrigido": corrigido,
            "tempo_inicio_s": 0, "tempo_fim_s": seg,
            "categoria_lean": cat, "categoria_lean_origem": origem,
            "principal": principal, "validacao_correto": None}


def comp(label, cat, origem="ia", processo="Torneamento"):
    return {"label": label, "categoria_lean": cat, "categoria_lean_origem": origem,
            "empresa": "U", "processo": processo}


print("\n[1] Critério 2 — override 'humano' é INVIOLÁVEL")
dados = {"eventos": [
    ev("e1", "operar_torno"),                                  # elegível (NULL)
    ev("e2", "operar_torno", "desperdicio", "humano"),          # INVIOLÁVEL
    ev("e3", "operar_torno", "valor_agregado", "herdado"),      # já certo
    ev("e4", "operar_torno", "desperdicio", "aprendido"),       # aprendido: não toca
]}
sb = FakeSB(dados)
n = pl.propagar_categoria_para_eventos(sb, "U", "Torneamento", "operar_torno", "valor_agregado")
e2 = next(e for e in dados["eventos"] if e["id"] == "e2")
e4 = next(e for e in dados["eventos"] if e["id"] == "e4")
check("evento 'humano' NÃO foi alterado",
      e2["categoria_lean"] == "desperdicio" and e2["categoria_lean_origem"] == "humano", e2)
check("evento 'aprendido' NÃO foi alterado",
      e4["categoria_lean"] == "desperdicio" and e4["categoria_lean_origem"] == "aprendido", e4)
e1 = next(e for e in dados["eventos"] if e["id"] == "e1")
check("evento NULL recebeu a categoria",
      e1["categoria_lean"] == "valor_agregado" and e1["categoria_lean_origem"] == "herdado", e1)
check("nenhuma escrita tocou e2/e4",
      not any(i in ("e2", "e4") for _, i, _ in sb.escritas), sb.escritas)

print("\n[2] Escopo multi-tenant — nunca vaza entre processos")
dados = {"eventos": [
    ev("a", "operar_torno", processo="Torneamento"),
    ev("b", "operar_torno", processo="Fresagem"),
]}
sb = FakeSB(dados)
pl.propagar_categoria_para_eventos(sb, "U", "Torneamento", "operar_torno", "valor_agregado")
check("evento do processo alvo recebeu",
      dados["eventos"][0]["categoria_lean"] == "valor_agregado")
check("evento de OUTRO processo intacto",
      dados["eventos"][1]["categoria_lean"] is None, dados["eventos"][1])

print("\n[3] Label EFETIVO — pega o que o gestor renomeou")
dados = {"eventos": [
    ev("c1", "monitorar_maquina"),                                  # sem correção
    ev("c2", "operar_torno", corrigido="monitorar_maquina"),        # renomeado
    ev("c3", "monitorar_maquina", corrigido="outra_coisa"),         # renomeado p/ fora
]}
sb = FakeSB(dados)
pl.propagar_categoria_para_eventos(sb, "U", "Torneamento", "monitorar_maquina", "valor_agregado")
check("evento sem correção recebeu", dados["eventos"][0]["categoria_lean"] == "valor_agregado")
check("evento RENOMEADO para o rótulo recebeu",
      dados["eventos"][1]["categoria_lean"] == "valor_agregado", dados["eventos"][1])
check("evento renomeado para OUTRO rótulo NÃO recebeu",
      dados["eventos"][2]["categoria_lean"] is None, dados["eventos"][2])

print("\n[4] categoria=None nunca limpa evento já herdado")
dados = {"eventos": [ev("d1", "x", "valor_agregado", "herdado")]}
sb = FakeSB(dados)
n = pl.propagar_categoria_para_eventos(sb, "U", "Torneamento", "x", None)
check("None → 0 escritas", n == 0 and not sb.escritas, sb.escritas)
check("categoria preservada", dados["eventos"][0]["categoria_lean"] == "valor_agregado")

print("\n[5] Critério 1 — dry_run NÃO escreve")
dados = {
    "eventos": [ev("f1", "operar_torno", seg=120), ev("f2", "operar_torno", seg=60)],
    "comportamentos": [comp("operar_torno", "valor_agregado")],
}
sb = FakeSB(dados)
rel = pl.relatorio_propagacao_lean(sb, "U", "Torneamento", dry_run=True)
check("dry_run=True reportado", rel["dry_run"] is True)
check("relatório conta 2 eventos", rel["propagacao"]["eventos"] == 2, rel["propagacao"])
check("relatório conta 3 minutos", rel["propagacao"]["minutos"] == 3.0, rel["propagacao"])
check("NADA foi escrito", not sb.escritas, sb.escritas)
check("eventos seguem NULL", all(e["categoria_lean"] is None for e in dados["eventos"]))
check("escritos = 0 no dry-run", rel["propagacao"]["escritos"] == 0)

print("\n[6] Critério 4 — backfill idempotente")
sb = FakeSB(dados)
r1 = pl.relatorio_propagacao_lean(sb, "U", "Torneamento", dry_run=False)
check("1ª passada escreve 2", r1["propagacao"]["escritos"] == 2, r1["propagacao"])
check("eventos classificados",
      all(e["categoria_lean"] == "valor_agregado" for e in dados["eventos"]))
sb2 = FakeSB(dados)
r2 = pl.relatorio_propagacao_lean(sb2, "U", "Torneamento", dry_run=False)
check("2ª passada: 0 elegíveis", r2["propagacao"]["eventos"] == 0, r2["propagacao"])
check("2ª passada: 0 escritas", not sb2.escritas, sb2.escritas)

print("\n[7] Critério 3 — acao_indefinida continua SEM categoria")
dados = {
    "eventos": [ev("g1", "acao_indefinida", seg=300), ev("g2", "operar_torno", seg=60)],
    "comportamentos": [comp("acao_indefinida", None), comp("operar_torno", "valor_agregado")],
}
sb = FakeSB(dados)
rel = pl.relatorio_propagacao_lean(sb, "U", "Torneamento", dry_run=False)
g1 = next(e for e in dados["eventos"] if e["id"] == "g1")
check("acao_indefinida NÃO recebeu categoria", g1["categoria_lean"] is None, g1)
check("só operar_torno migrou", rel["propagacao"]["eventos"] == 1, rel["propagacao"])
check("acao_indefinida entra no cinza_real",
      any(r["label"] == "acao_indefinida" for r in rel["cinza_real"]["por_rotulo"]),
      rel["cinza_real"])
check("cinza_real contabiliza 5 min", rel["cinza_real"]["minutos"] == 5.0, rel["cinza_real"])

print("\n[8] Diagnóstico separa 'propagação' de 'cinza real'")
dados = {
    "eventos": [
        ev("h1", "operar_torno", seg=600),            # categoria existe → propaga
        ev("h2", "esperar_ciclo", seg=1200),          # comportamento SEM categoria
        ev("h3", "rotulo_orfao", seg=300),            # nem comportamento existe
        ev("h4", "operar_torno", "valor_agregado", "humano", seg=60),
    ],
    "comportamentos": [comp("operar_torno", "valor_agregado"), comp("esperar_ciclo", None)],
}
sb = FakeSB(dados)
rel = pl.relatorio_propagacao_lean(sb, "U", "Torneamento", dry_run=True)
check("propagação = só o que tem resposta no banco (10 min)",
      rel["propagacao"]["minutos"] == 10.0, rel["propagacao"])
check("cinza real = 25 min (esperar_ciclo + órfão)",
      rel["cinza_real"]["minutos"] == 25.0, rel["cinza_real"])
check("override humano contabilizado no 'fora'",
      rel["fora"]["override_humano"]["eventos"] == 1, rel["fora"])
check("distingue sem-categoria de rótulo-órfão",
      rel["fora"]["sem_categoria_no_comportamento"]["eventos"] == 1
      and rel["fora"]["rotulo_sem_comportamento"]["eventos"] == 1, rel["fora"])
check("cinza_real ordenado por minutos (maior primeiro)",
      rel["cinza_real"]["por_rotulo"][0]["label"] == "esperar_ciclo",
      rel["cinza_real"]["por_rotulo"])

print("\n[9] Critério 6 — herança na ingestão (código)")
src = open("backend/pipeline.py").read()
check("etapa_persistir monta o mapa de herança", "cat_ingestao" in src)
check("evento principal nasce com origem 'herdado'",
      'row["categoria_lean_origem"] = "herdado"' in src)
check("IA propaga após classificar",
      src.count("propagar_categoria_para_eventos(sb, empresa, processo") >= 2, )
main_src = open("backend/main.py").read()
check("endpoint humano usa a função compartilhada",
      # Fase 60: o corpo virou `_aplicar_categoria_lean(sb, empresa, ...)`, para
      # ser reusado pela rota POR RÓTULO. O `user.empresa` entra como argumento.
      "propagar_categoria_para_eventos(\n            sb, empresa, alvo[\"processo\"]" in main_src
      and "_aplicar_categoria_lean(sb, user.empresa, comportamento_id, alvo, cat)" in main_src)
check("endpoint de backfill existe", "manutencao/lean/propagar" in main_src)
check("dry_run é o DEFAULT do endpoint", 'dry_run: bool = Query(True' in main_src)

print(f"\n{'=' * 56}\n== {ok} ok, {fail} fail ==\n{'=' * 56}")
sys.exit(1 if fail else 0)
