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

from backend import main as mn  # noqa: E402
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

print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
