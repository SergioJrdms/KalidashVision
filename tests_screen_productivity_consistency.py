"""Contrato: todas as telas projetam a mesma decisão ternária por evidência."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# O teste do read-model não carrega visão/modelos pesados.
for nome in (
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
):
    sys.modules.setdefault(nome, types.ModuleType(nome))
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
from backend import productivity as prod  # noqa: E402


ok = fail = 0


def check(nome: str, cond: bool, extra="") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def evento(i: int, ini: float, fim: float, trabalho: bool | None) -> dict:
    return {
        "id": f"e{i}", "video_id": "v1", "empresa": "U", "processo": "T",
        "pessoa_track_id": 1, "comportamento_label": "acompanhar_maquina",
        "label_corrigido": None, "descricao_bruta": f"evidencia {i}",
        "tempo_inicio_s": ini, "tempo_fim_s": fim, "validacao_correto": None,
        "principal": False, "papel_pessoa": "operador", "confianca": 0.9,
        "em_duvida": trabalho is None, "validado_humano": False,
        "origem_validacao": "vlm", "n_amostras": 5,
        "categoria_lean": None, "categoria_lean_origem": None,
        "maos_maquina": None, "orientacao": None, "trabalho": trabalho,
        "bbox_stats": {}, "produtividade_predita": None,
        "produtividade_regra": None, "versao_instrumento": 12,
    }


EVENTOS = [
    evento(1, 0, 300, True),
    evento(2, 300, 600, False),
    evento(3, 600, 900, None),
]


print("[1] Um mesmo rotulo conserva a decisao de cada evidencia")
fatias = prod.fatias_produtividade(EVENTOS)
check("ha uma fatia de cada decisao", [f["decisao"] for f in fatias] == [
    "produtivo", "improdutivo", "sem_decisao",
], fatias)
dist = prod.distribuicao_produtividade(EVENTOS)
check("a arvore recebe tres galhos para o mesmo rotulo", {
    (d["comportamento"], d["decisao"]) for d in dist
} == {
    ("acompanhar_maquina", "produtivo"),
    ("acompanhar_maquina", "improdutivo"),
    ("acompanhar_maquina", "sem_decisao"),
}, dist)
check("as tres fatias fecham 100%", abs(sum(d["pct_tempo"] for d in dist) - 100.0) < 0.01, dist)


class FakeQ:
    def __init__(self, sb, tabela):
        self.sb, self.tabela = sb, tabela
        self.eqs, self.ins, self.faixa = {}, {}, None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def in_(self, c, vs): self.ins[c] = list(vs); return self
    def range(self, a, b): self.faixa = (a, b); return self

    def execute(self):
        linhas = [dict(x) for x in self.sb.dados.get(self.tabela, [])]
        linhas = [x for x in linhas
                  if all(x.get(k) == v for k, v in self.eqs.items())
                  and all(x.get(k) in vs for k, vs in self.ins.items())]
        if self.faixa:
            linhas = linhas[self.faixa[0]:self.faixa[1] + 1]
        return types.SimpleNamespace(data=linhas)


class FakeSB:
    def __init__(self):
        self.dados = {
            "eventos": EVENTOS,
            "videos": [{
                "id": "v1", "empresa": "U", "processo": "T",
                "nome": "seg_20260914_080000.mp4", "cam_id": "cam1",
                "duracao_s": 900, "gravado_em": "2026-09-14T08:00:00",
                "processado_em": "2026-09-14T08:20:00",
            }],
            "zonas_camera": [],
        }

    def table(self, tabela):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, tabela)
        return T()


print("\n[2] O detalhe do dia usa exatamente a mesma particao")
bin_ = pl.eventos_do_bin(FakeSB(), "U", "T", "2026-09-14", 8 * 60 + 7)
check("detalhe tem P, I e sem decisao", set(bin_["por_categoria"]) == {"va", "desp", "sem"}, bin_)
check("cada fatia ocupa um terco", all(
    abs(bin_["por_categoria"][cat]["pct"] - 33.3) < 0.2
    for cat in ("va", "desp", "sem")
), bin_["por_categoria"])
check("o mesmo rotulo nao perde a decisao do trecho", {
    (a["rotulo"], a["cat"]) for a in bin_["acoes"]
} == {
    ("acompanhar_maquina", "va"),
    ("acompanhar_maquina", "desp"),
    ("acompanhar_maquina", "sem"),
}, bin_["acoes"])


print("\n[3] Os quatro consumidores apontam para o read-model canonico")
main = (ROOT / "backend/main.py").read_text(encoding="utf-8")
dashboard = (ROOT / "frontend/src/pages/Dashboard.tsx").read_text(encoding="utf-8")
arvore = (ROOT / "frontend/src/pages/Arvore.tsx").read_text(encoding="utf-8")
adapt = (ROOT / "frontend/src/lib/adapt.ts").read_text(encoding="utf-8")
check("dashboard embutido usa distribuicao canonica", "q.data.distribuicao_produtividade" in dashboard)
check("arvore independente usa distribuicao canonica", "q.data.distribuicao_produtividade" in arvore)
check("eventos usa decisao do evento", "decisaoShort(e.produtividade_decisao)" in adapt)
check("dia a dia usa as fatias canonicas", "produtividade.fatias_produtividade" in main)

print("\n[4] Decisao humana vence automacao sem inventar presenca")
humano = dict(EVENTOS[0], categoria_lean="desperdicio",
              categoria_lean_origem="humano_rotulo", maos_maquina=True)
check("correcao humana vence maos e trabalho", prod.classificar_observacao(humano)[0] == prod.EST_IMPRODUTIVO)
check("operador continua presente", prod.estado_compone_indicador(prod.classificar_observacao(humano)[0], "presenca_operador"))
fora = dict(humano, papel_pessoa="operador_fora", categoria_lean="valor_agregado")
check("atividade fora pode ser produtiva", prod.classificar_observacao(fora)[0] == prod.EST_OPERADOR_FORA_PRODUTIVO)
check("trabalho fora nao inventa presenca", not prod.estado_compone_indicador(prod.classificar_observacao(fora)[0], "presenca_operador"))
vazio = dict(EVENTOS[2], papel_pessoa="posto_vazio")
estado = prod.classificar_observacao(vazio)[0]
check("ausencia confirmada e improdutiva na vitrine", prod.indicador_produtividade_do_estado(estado) == "improdutivo")
check("ausencia continua ausencia", prod.estado_compone_indicador(estado, "posto_sem_operador") and not prod.estado_compone_indicador(estado, "presenca_operador"))
check("ausencia contraditoria continua duvida", prod.indicador_produtividade_do_estado(prod.classificar_observacao(dict(vazio, maos_maquina=True))[0]) == "sem_decisao")
check("nome acompanhar sozinho nao inventa trabalho", prod.indicador_produtividade_do_estado(prod.classificar_observacao(EVENTOS[2])[0]) == "sem_decisao")
check("origem automatica nao vira decisao humana", prod.classificar_observacao(dict(humano, categoria_lean_origem="ia"))[0] == prod.EST_PRODUTIVO)

print("\n[5] Propagacao protege humano individual e nao engole falha")
class WriteQ:
    def __init__(self, sb): self.sb = sb; self.filters = {}; self.payload = {}; self.rule = ""; self.null = None
    def update(self, payload): self.payload = payload; return self
    def select(self, *a): return self
    def eq(self, c, v): self.filters[c] = v; return self
    def is_(self, c, v): self.null = c; return self
    def or_(self, rule): self.rule = rule; return self
    def execute(self):
        if self.sb.fail: raise RuntimeError("write failed")
        touched = []
        for e in self.sb.rows:
            if any(e.get(k) != v for k, v in self.filters.items()): continue
            if self.null and e.get(self.null) is not None: continue
            origin = e.get("categoria_lean_origem")
            eligible = origin != "humano" if "neq.humano" in self.rule else (e.get("categoria_lean") is None or origin == "herdado")
            if eligible: e.update(self.payload); touched.append(dict(e))
        return types.SimpleNamespace(data=touched)
class WriteSB:
    def __init__(self, rows, fail=False): self.rows, self.fail = rows, fail
    def table(self, name): return WriteQ(self)
rows = [dict(EVENTOS[0], id=str(i), categoria_lean="valor_agregado", categoria_lean_origem=o)
        for i, o in enumerate(["ia", "aprendido", "herdado", "humano_rotulo", "humano"])]
sb = WriteSB(rows)
check("humano corrige IA e decisoes anteriores", pl.propagar_categoria_para_eventos(sb, "U", "T", "acompanhar_maquina", "desperdicio", origem="humano_rotulo") == 4)
check("humano individual permanece protegido", rows[-1]["categoria_lean"] == "valor_agregado")
check("automacao nao apaga humano rotulo", pl.propagar_categoria_para_eventos(sb, "U", "T", "acompanhar_maquina", "valor_agregado") == 0)
try:
    pl.propagar_categoria_para_eventos(WriteSB([], fail=True), "U", "T", "acompanhar_maquina", "desperdicio", origem="humano_rotulo")
    raised = False
except RuntimeError:
    raised = True
check("falha de escrita nao retorna sucesso", raised)

print(f"\n{ok} ok - {fail} falha(s)")
raise SystemExit(1 if fail else 0)
