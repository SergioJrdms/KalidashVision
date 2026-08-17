# ============================================================
# TODO NOME QUE `main.py` USA TEM DE EXISTIR.
#
# Esta suíte nasce de um erro em PRODUÇÃO que a bateria inteira deixou passar:
# `sugestoes_do_posto` foi usada no dashboard e não foi importada. `py_compile`
# passou (compilar não resolve nome), 36 suítes passaram (elas importam a
# função direto do `pipeline`, não pelo `main`), e o defeito só apareceu como
# 500 quando um humano abriu a tela.
#
# A lição é específica: NENHUM teste exercitava o `main` como módulo. Compilar
# prova sintaxe; só importar prova que os nomes resolvem.
#
# É barato e pega uma classe inteira de erro — qualquer símbolo usado e não
# importado, em qualquer endpoint, some daqui em diante.
# ============================================================
import sys, types, os, ast

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

RAIZ = os.path.dirname(os.path.abspath(__file__))
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


print("\n[1] O módulo da API importa de verdade")
# ⚠️ O import completo depende de fastapi/pydantic, que nem toda máquina de
# desenvolvimento tem. Quando faltam, o teste AVISA e segue — a guarda que de
# fato pega o defeito é a varredura de AST do bloco [2], que não depende de
# dependência nenhuma e ainda enxerga DENTRO dos corpos de função (que é onde
# o `sugestoes_do_posto` quebrou; import de módulo sozinho não pegaria).
try:
    from backend import main as api      # noqa: E402
    _erro = None
except ModuleNotFoundError as e:         # noqa: BLE001
    api = None
    _erro = e
    print(f"       (pulado: {e} — o bloco [2] cobre o essencial)")
except Exception as e:                   # noqa: BLE001
    api = None
    _erro = e
if _erro is None or not isinstance(_erro, ModuleNotFoundError):
    check("`backend.main` importa sem explodir", api is not None, _erro)

print("\n[2] ⭐ Todo nome usado resolve — a guarda que faltava")
# Varre a AST do `main.py` e coleta os nomes usados que não são locais, nem
# built-in, nem importados. Qualquer sobra é um NameError esperando um usuário.
fonte = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()
arvore = ast.parse(fonte)

importados: set[str] = set()
definidos: set[str] = set()
for no in ast.walk(arvore):
    if isinstance(no, ast.ImportFrom):
        for a in no.names:
            importados.add(a.asname or a.name.split(".")[0])
    elif isinstance(no, ast.Import):
        for a in no.names:
            importados.add(a.asname or a.name.split(".")[0])
    elif isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        definidos.add(no.name)
    elif isinstance(no, ast.Assign):
        for alvo in no.targets:
            if isinstance(alvo, ast.Name):
                definidos.add(alvo.id)
    elif isinstance(no, ast.AnnAssign) and isinstance(no.target, ast.Name):
        definidos.add(no.target.id)

# Locais de função (parâmetros, atribuições, laços, with, except, comprehensions)
locais: set[str] = set()
for no in ast.walk(arvore):
    if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = no.args
        for grupo in (a.posonlyargs, a.args, a.kwonlyargs):
            locais.update(x.arg for x in grupo)
        if a.vararg:
            locais.add(a.vararg.arg)
        if a.kwarg:
            locais.add(a.kwarg.arg)
    elif isinstance(no, ast.Name) and isinstance(no.ctx, (ast.Store, ast.Del)):
        locais.add(no.id)
    elif isinstance(no, ast.ExceptHandler) and no.name:
        locais.add(no.name)
    elif isinstance(no, ast.comprehension) and isinstance(no.target, ast.Name):
        locais.add(no.target.id)
    elif isinstance(no, (ast.Global, ast.Nonlocal)):
        locais.update(no.names)

builtins_ = set(dir(__builtins__)) | set(dir(__import__("builtins")))
conhecidos = importados | definidos | locais | builtins_ | {"__name__", "__file__"}

usados: dict[str, int] = {}
for no in ast.walk(arvore):
    if isinstance(no, ast.Name) and isinstance(no.ctx, ast.Load):
        if no.id not in conhecidos:
            usados.setdefault(no.id, no.lineno)

_sobras = sorted(f"{n} (linha {l})" for n, l in usados.items())
check("⭐ nenhum nome usado sem estar importado ou definido",
      not _sobras, "\n         " + "\n         ".join(_sobras[:12]))

print("\n[3] O caso concreto que quebrou")
# ⭐ ESTE É O TESTE QUE TERIA PEGO O 500. Ele não depende de importar nada.
check("⭐ `sugestoes_do_posto` está no bloco de import do main",
      "    sugestoes_do_posto," in fonte)
check("e é de fato usada no dashboard (senão o teste acima é vazio)",
      "sugestoes_praticas = sugestoes_do_posto(" in fonte)
if api is not None:
    check("e é alcançável pelo módulo", callable(getattr(api, "sugestoes_do_posto", None)))
    check("com a assinatura que o dashboard usa",
          api.sugestoes_do_posto(produtividade={"posto_vazio_pct": 20.0}) != [])

print("\n[4] E os outros símbolos que este trabalho trouxe")
if api is not None:
    for nome in ("permanencia_do_dia", "frase_permanencia", "frente_maquina_do_processo",
                 "origens_sem_observacao", "descricoes_que_afirmam_estado",
                 "sortear_amostra_cega", "taxa_de_acerto", "origem_da_descricao",
                 "descricao_foi_observada", "descricao_para_exibir",
                 "limpar_sufixo_estado", "sugestoes_do_posto"):
        check(f"`{nome}` resolve no main", getattr(api, nome, None) is not None)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
