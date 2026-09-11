"""Testes do patch da Fase 113 — roda contra o código PATCHADO de verdade."""
import json
import os
import sys
import types

# Mesmo enxerto das outras suites: o pipeline importa cv2/torch/supabase no
# topo, e nada disso e necessario para testar decisao. Sem isto a suite so
# roda em maquina com o ambiente completo instalado.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _m in ["cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
           "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))
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

os.environ.setdefault("KV_PONTE_SEGMENTO", "on")
os.environ.setdefault("KV_PONTE_ROLANTE", "on")

from backend import ponte_rolante as pr          # noqa: E402
from backend import pipeline as pl               # noqa: E402
from backend import productivity as pd_          # noqa: E402

falhas = []


def ok(cond, nome, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + nome + (f"  {extra}" if extra else ""))
    if not cond:
        falhas.append(nome)


def jan(n, alta=0):
    return ([{"operando_ponte_rolante": True, "confianca": "alta"}] * alta
            + [{"operando_ponte_rolante": True, "confianca": "media"}] * (n - alta))


print("\n1) PORTÃO DE CERTEZA")
v = pr.segmento_certeza_ponte(jan(5, 2), [{"duracao_s": 200.0}], 300.0)
ok(v["certo"], "5 janelas, 2 altas, cobertura 67% -> certo", v["motivo"])

v = pr.segmento_certeza_ponte(jan(5, 0), [{"duracao_s": 200.0}], 300.0)
ok(not v["certo"] and "alta" in v["motivo"], "sem janela de confianca alta -> nao")

v = pr.segmento_certeza_ponte(jan(2, 2), [{"duracao_s": 200.0}], 300.0)
ok(not v["certo"] and "janelas positivas" in v["motivo"], "2 janelas < 3 -> nao")

v = pr.segmento_certeza_ponte(jan(5, 2), [{"duracao_s": 8.0}], 300.0)
ok(not v["certo"] and "cobertura" in v["motivo"],
   "8s de ponte em 5 min -> nao (a trava que importa)", v["motivo"])

v = pr.segmento_certeza_ponte(jan(5, 2), [{"duracao_s": 200.0}], 0.0)
ok(not v["certo"] and "duracao" in v["motivo"], "duracao desconhecida -> nao")

v = pr.segmento_certeza_ponte([], [], 300.0)
ok(not v["certo"], "nada positivo -> nao")

v = pr.segmento_certeza_ponte(
    [{"operando_ponte_rolante": False, "confianca": "alta"}] * 9, [], 300.0)
ok(not v["certo"] and v["n_janelas"] == 0, "janela negativa nao conta")

os.environ["KV_PONTE_SEG_MIN_COBERTURA"] = "0"
v = pr.segmento_certeza_ponte(jan(3, 1), [{"duracao_s": 8.0}], 300.0)
ok(v["certo"], "cobertura 0 (escolha do Sergio) -> qualquer ponte certa marca")
os.environ["KV_PONTE_SEG_MIN_COBERTURA"] = "0.5"

print("\n2) decidir_permanencia — o nível novo")
base = {"papel_pessoa": "operador", "zona_contexto": "posto",
        "estado_presenca": "dentro", "trabalho": None}
marcado = {**base, "categoria_lean_origem": pr.PONTE_SEGMENTO_ORIGEM,
           "categoria_lean": "valor_agregado"}
cat, niv, mot, _est = pl.decidir_permanencia(marcado, "esquerda")
ok(cat == "valor_agregado" and niv == pl.NIVEL_PONTE_SEGMENTO,
   "evento marcado -> valor_agregado no nivel ponte_rolante", f"{cat}/{niv}")

fora = {**marcado, "estado_presenca": "fora", "papel_pessoa": "operador_fora"}
cat, niv, _m, _e = pl.decidir_permanencia(fora, "esquerda")
ok(cat == "valor_agregado" and niv == pl.NIVEL_PONTE_SEGMENTO,
   "atravessa 'fora do posto' — que e o ponto da fase")

vazio = {**marcado, "papel_pessoa": "posto_vazio", "estado_presenca": "vazio",
         "origem_validacao": "posto_vazio", "validado_humano": True}
cat, niv, _m, _e = pl.decidir_permanencia(vazio, "esquerda")
ok(cat == "valor_agregado" and niv == pl.NIVEL_PONTE_SEGMENTO,
   "atravessa 'posto vazio' auto-validado por mecanismo")

humano = {**marcado, "validado_humano": True, "label_corrigido": "conversando",
          "_cat_humana": "desperdicio", "origem_validacao": "humano"}
cat, niv, _m, _e = pl.decidir_permanencia(humano, "esquerda")
ok(niv == pl.NIVEL_HUMANO and cat == "desperdicio",
   "correcao humana continua ganhando da ponte", f"{cat}/{niv}")

sem_marca = dict(base)
cat, niv, _m, _e = pl.decidir_permanencia(sem_marca, "esquerda")
ok(niv != pl.NIVEL_PONTE_SEGMENTO,
   "evento sem a marca nao entra no nivel novo", f"{niv}")

print("\n3) o kill switch (flag desligada = numero de antes)")
import importlib                                    # noqa: E402
os.environ["KV_PONTE_SEGMENTO"] = "off"
importlib.reload(sys.modules["backend.pipeline"])
import backend.pipeline as pl_off                   # noqa: E402
cat, niv, _m, _e = pl_off.decidir_permanencia(dict(marcado), "esquerda")
ok(niv != pl_off.NIVEL_PONTE_SEGMENTO,
   "com KV_PONTE_SEGMENTO=off a marca e ignorada", f"{niv}")
ok(not pr.ponte_segmento_habilitada(), "e a marcacao nao roda")
os.environ["KV_PONTE_SEGMENTO"] = "on"
importlib.reload(sys.modules["backend.pipeline"])

print("\n4) marcar_segmento_como_ponte — com banco falso")


class FakeQuery:
    def __init__(s, sb, tabela, op, payload=None):
        s.sb, s.tabela, s.op, s.payload, s.filtros = sb, tabela, op, payload, {}

    def select(s, campos):
        s.op, s.campos = "select", campos
        return s

    def update(s, payload):
        s.op, s.payload = "update", payload
        return s

    def insert(s, payload):
        s.op, s.payload = "insert", payload
        return s

    def eq(s, k, v):
        s.filtros[k] = v
        return s

    def limit(s, n):
        return s

    def in_(s, k, vals):
        s.filtros[k] = list(vals)
        return s

    def execute(s):
        return s.sb._executar(s)


class Resp:
    def __init__(s, data):
        s.data = data


class FakeSB:
    def __init__(s, eventos, catalogo=None, aceita_label_original=True):
        s.eventos, s.catalogo = eventos, (catalogo or [])
        s.aceita = aceita_label_original
        s.updates, s.inserts = [], []

    def table(s, nome):
        return FakeQuery(s, nome, None)

    def _executar(s, q):
        if q.tabela == "eventos" and q.op == "select":
            return Resp([dict(e) for e in s.eventos])
        if q.tabela == "eventos" and q.op == "update":
            if "label_original" in q.payload and not s.aceita:
                raise RuntimeError(
                    "Could not find the 'label_original' column of 'eventos'")
            s.updates.append((q.payload, q.filtros.get("id")))
            return Resp([])
        if q.tabela == "comportamentos" and q.op == "select":
            return Resp(list(s.catalogo))
        if q.tabela == "comportamentos" and q.op == "insert":
            s.inserts.append(q.payload)
            return Resp([])
        return Resp([])


EVENTOS = [
    {"id": "cru1", "comportamento_label": "posto_vazio", "principal": False,
     "origem_validacao": "posto_vazio", "validado_humano": True,
     "label_corrigido": None, "validacao_correto": None},
    {"id": "cru2", "comportamento_label": "manipular_peca", "principal": False,
     "origem_validacao": None, "validado_humano": False,
     "label_corrigido": None, "validacao_correto": None},
    {"id": "a", "comportamento_label": "posto_vazio", "principal": True,
     "origem_validacao": "posto_vazio", "validado_humano": True,
     "label_corrigido": None, "validacao_correto": None},
    {"id": "b", "comportamento_label": "acao_indefinida", "principal": True,
     "origem_validacao": None, "validado_humano": False,
     "label_corrigido": None, "validacao_correto": None},
    {"id": "c", "comportamento_label": "conversando_colega", "principal": True,
     "origem_validacao": None, "validado_humano": True,
     "label_corrigido": "conversando_colega", "validacao_correto": True},
    {"id": "d", "comportamento_label": pr.PONTE_ROLANTE_LABEL, "principal": False,
     "origem_validacao": None, "validado_humano": False,
     "label_corrigido": None, "validacao_correto": None},
]
sb = FakeSB(EVENTOS)
r = pr.marcar_segmento_como_ponte(sb, "v1", "U", "TC", {"certo": True})
ok(r["principais_marcados"] == 2, "troca o rotulo dos 2 principais elegiveis",
   json.dumps({k: r[k] for k in ("principais_marcados", "crus_marcados",
                                 "preservados_humanos", "ja_marcados")}))
ok(r["crus_marcados"] == 3,
   "e MARCA os crus — sao eles que o instrumento de produtividade mede")
ok(r["preservados_humanos"] == 1, "preserva o evento decidido por gente")
ok(all(p.get("categoria_lean_origem") == pr.PONTE_SEGMENTO_ORIGEM
       for p, _ in sb.updates), "toda atualizacao carrega a origem")
ok(all("comportamento_label" not in p for p, ids in sb.updates
       if set(ids or []) <= {"cru1", "cru2", "d"}),
   "rotulo de evento cru NAO e tocado (ele e o registro de auditoria)")
ok({p["label_original"] for p, _ in sb.updates if "label_original" in p}
   == {"posto_vazio", "acao_indefinida"},
   "label_original guarda o rotulo antigo de cada grupo")
ok(r["erro"] is None, "sem erro")

sb2 = FakeSB(EVENTOS, aceita_label_original=False)
r2 = pr.marcar_segmento_como_ponte(sb2, "v1", "U", "TC", {"certo": True})
ok(r2["principais_marcados"] == 2 and r2["erro"] is None,
   "banco sem `label_original` -> marca sem ela, nao derruba")
ok(all("label_original" not in p for p, _ in sb2.updates),
   "e a coluna some do payload no retry")

print("\n5) O OUTRO INSTRUMENTO — classificar_observacao")


def _ev(i, f, **k):
    return {"id": f"e{i}", "video_id": "v", "tempo_inicio_s": i,
            "tempo_fim_s": f, "principal": False, "n_amostras": 1,
            "versao_instrumento": 9, "_cam_id": "cam1", **k}


_m = {"categoria_lean_origem": pr.PONTE_SEGMENTO_ORIGEM}
ok(pd_.classificar_observacao({**_m, "papel_pessoa": "posto_vazio"})[0]
   == "operador_fora_produtivo", "posto vazio marcado -> fora PRODUTIVO")
ok(pd_.classificar_observacao({**_m, "papel_pessoa": "operador_fora"})[0]
   == "operador_fora_produtivo", "operador fora marcado -> fora PRODUTIVO")
ok(pd_.classificar_observacao({**_m, "papel_pessoa": "operador"})[0]
   == "produtivo", "operador no posto marcado -> produtivo")
ok(pd_.classificar_observacao({**_m, "papel_pessoa": "visitante"})[0]
   == "operador_ausente", "visitante NAO vira ponte (nao e o titular)")
ok(pd_.classificar_observacao({"papel_pessoa": "posto_vazio"})[0]
   == "posto_vazio", "sem a marca, nada muda")

# ⭐ A propriedade que importa: a marca move produtividade e NAO move presenca.
_sem = [_ev(0, 60, papel_pessoa="posto_vazio"),
        _ev(60, 90, papel_pessoa="operador", trabalho=False,
            produtividade_motivo="sem_atividade")]
_com = [{**e, **_m} if e["papel_pessoa"] == "posto_vazio" else e for e in _sem]
_a, _b = pd_._metricas(_sem, {}), pd_._metricas(_com, {})
ok(_a["presenca_pct"] == _b["presenca_pct"]
   and _a["posto_vazio_pct"] == _b["posto_vazio_pct"],
   "⭐ presenca INTACTA (presenca_pct e posto_vazio_pct nao se mexem)",
   f"{_a['presenca_pct']}% / {_a['posto_vazio_pct']}%")
ok(_b["produtividade_pct"] > _a["produtividade_pct"],
   "⭐ e a produtividade sobe — que e o ponto da fase",
   f"{_a['produtividade_pct']}% -> {_b['produtividade_pct']}%")

print("\n6) garantir_catalogo_ponte")
sb3 = FakeSB([], catalogo=[])
ok(pr.garantir_catalogo_ponte(sb3, "U", "TC") == "criado" and sb3.inserts,
   "catalogo vazio -> cria com valor_agregado")
ok(sb3.inserts[0]["categoria_lean"] == "valor_agregado", "com a categoria certa")
sb4 = FakeSB([], catalogo=[{"id": "x", "categoria_lean": "desperdicio"}])
ok(pr.garantir_catalogo_ponte(sb4, "U", "TC") == "divergente" and not sb4.inserts,
   "catalogo divergente -> avisa e NAO sobrescreve")
sb5 = FakeSB([], catalogo=[{"id": "x", "categoria_lean": "valor_agregado"}])
ok(pr.garantir_catalogo_ponte(sb5, "U", "TC") == "ok", "catalogo ja correto -> ok")

print(f"\n{'TODOS OS TESTES PASSARAM' if not falhas else 'FALHAS: ' + str(falhas)}")
sys.exit(1 if falhas else 0)
