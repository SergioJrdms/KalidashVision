"""Portão negativo: proveniência obrigatória e leitura retrocompatível."""
import os
import sys
import types


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
os.environ["KV_PRODUTIVIDADE_OPERADOR_V9"] = "on"

from backend import main as mn  # noqa: E402
from backend import pipeline as pl  # noqa: E402
from backend import productivity as prod  # noqa: E402


ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


print("[1] Classificação negativa exige a causa")
base = {"papel_pessoa": "operador", "trabalho": False}
check(
    "false isolado se abstém",
    prod.classificar_observacao(base)[0]
    == prod.EST_PRODUTIVIDADE_INCONCLUSIVA,
)
for motivo in ("uso_celular", "sem_atividade"):
    check(
        f"{motivo} autoriza improdutividade",
        prod.classificar_observacao({
            **base, "produtividade_motivo": motivo,
        })[0] == prod.EST_IMPRODUTIVO,
    )
for motivo in (None, "conversa", "conversa_ou_celular", "costas_ou_lado"):
    check(
        f"{motivo!r} sem evidência complementar se abstém",
        prod.classificar_observacao({
            **base, "produtividade_motivo": motivo,
        })[0] == prod.EST_PRODUTIVIDADE_INCONCLUSIVA,
    )


print("\n[2] Dashboard pede o motivo e degrada com segurança")
original = mn.varrer
chamadas = []


def coluna_ausente(sb, tabela, colunas, **kwargs):
    chamadas.append((tabela, colunas, kwargs))
    if "produtividade_motivo" in colunas:
        raise RuntimeError("column eventos.produtividade_motivo does not exist")
    return [{"id": "legado"}]


try:
    mn.varrer = coluna_ausente
    linhas = mn._varrer_eventos_com_motivo(
        object(), "id, trabalho", empresa="E", processo="P",
    )
finally:
    mn.varrer = original

check("primeira leitura projeta produtividade_motivo",
      "produtividade_motivo" in chamadas[0][1], chamadas)
check("fallback remove somente a coluna opcional",
      chamadas[1][1] == "id, trabalho" and linhas == [{"id": "legado"}],
      (chamadas, linhas))
check("escopo empresa/processo é preservado nas duas leituras",
      all(c[2] == {"empresa": "E", "processo": "P"} for c in chamadas),
      chamadas)


def erro_real(*args, **kwargs):
    raise RuntimeError("rede indisponível")


propagou = False
try:
    mn.varrer = erro_real
    mn._varrer_eventos_com_motivo(
        object(), "id, trabalho", empresa="E", processo="P",
    )
except RuntimeError as exc:
    propagou = str(exc) == "rede indisponível"
finally:
    mn.varrer = original
check("erro não relacionado nunca é engolido", propagou)


print("\n[3] Round-trip observação → banco → reload preserva a licença negativa")


class FakeQuery:
    def __init__(self, sb, tabela, acao, payload=None):
        self.sb, self.tabela, self.acao, self.payload = sb, tabela, acao, payload
        self.eqs = {}

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def range(self, *_a, **_k): return self
    def eq(self, campo, valor): self.eqs[campo] = valor; return self

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        if self.acao == "insert":
            lote = self.payload if isinstance(self.payload, list) else [self.payload]
            novas = []
            for payload in lote:
                nova = {"id": f"{self.tabela}-{self.sb.seq}", **payload}
                self.sb.seq += 1
                linhas.append(nova)
                novas.append(dict(nova))
            return types.SimpleNamespace(data=novas)
        if self.acao == "update":
            for linha in linhas:
                if all(linha.get(k) == v for k, v in self.eqs.items()):
                    linha.update(self.payload)
            return types.SimpleNamespace(data=[])
        filtradas = [linha for linha in linhas
                     if all(linha.get(k) == v for k, v in self.eqs.items())]
        return types.SimpleNamespace(data=[dict(x) for x in filtradas])


class FakeTable:
    def __init__(self, sb, nome): self.sb, self.nome = sb, nome
    def select(self, *_a, **_k): return FakeQuery(self.sb, self.nome, "select")
    def insert(self, payload): return FakeQuery(self.sb, self.nome, "insert", payload)
    def update(self, payload): return FakeQuery(self.sb, self.nome, "update", payload)


class FakeSB:
    def __init__(self):
        self.dados = {"videos": [], "comportamentos": [], "eventos": []}
        self.seq = 1

    def table(self, nome): return FakeTable(self, nome)


def evento(principal):
    return {
        "pessoa_track_id": 7,
        "comportamento_label": "uso_celular",
        "descricao_bruta": "operador usando celular",
        "tempo_inicio_s": 0.0,
        "tempo_fim_s": 30.0,
        "frame_inicio": 0,
        "frame_fim": 30,
        "bbox_inicio": [0, 0, 10, 10],
        "bbox_cam": "cam1",
        "bbox_stats": None,
        "zona_contexto": "posto",
        "papel_pessoa": "operador",
        "maos_maquina": None,
        "orientacao": None,
        "maquina": False,
        "imovel": True,
        "trabalho": False,
        "produtividade_motivo": "uso_celular",
        "n_amostras": 3,
        "n_observacoes": 3,
        "observacoes_origem": {"analisado": 3},
        "origens": {"analisado": 3},
        "confianca": 1.0,
        "principal": principal,
    }


sb = FakeSB()
pl.etapa_persistir(
    sb, "U", "T", "video.mp4",
    {"duracao_s": 30.0, "fps": 1.0, "largura": 640, "altura": 480},
    [evento(True)], [7], {"uso_celular": "uso de celular"},
    lambda *_a, **_k: "vlm", eventos_auditoria=[evento(False)],
)
principal_salvo = next(e for e in sb.dados["eventos"] if e["principal"] is True)
cru_salvo = next(e for e in sb.dados["eventos"] if e["principal"] is False)
check("principal mantém o motivo", principal_salvo["produtividade_motivo"] == "uso_celular",
      principal_salvo)
check("evento cru mantém o motivo", cru_salvo["produtividade_motivo"] == "uso_celular",
      cru_salvo)
check("reload do cru continua improdutivo",
      prod.classificar_observacao(cru_salvo)[0] == prod.EST_IMPRODUTIVO,
      prod.classificar_observacao(cru_salvo))

print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
