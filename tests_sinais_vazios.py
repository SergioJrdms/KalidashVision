# ============================================================
# POR QUE ESTA COLUNA ESTÁ VAZIA?
#
# `decidido_por`, `maos_maquina`, `orientacao`, `trabalho`, `reavaliacao` e
# `narrativa` apareceram TODAS nulas no banco ao mesmo tempo. Três perguntas
# diferentes se parecem com um `NULL`, e olhar a tabela não distingue nenhuma:
#
#   · a chave está ligada?
#   · o deploy com o código novo subiu?
#   · a coluna existe no banco?
#
# E há uma quarta causa que nem é defeito: 3 de cada 4 linhas de `eventos` são
# CRUS de auditoria (`principal = false`), que não passam pela consolidação do
# minuto e por isso não recebem essas colunas POR DESENHO. Olhar a tabela sem
# filtrar faz tudo parecer quebrado.
#
# Esta suíte protege o diagnóstico que responde as quatro sem adivinhação.
# ============================================================
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


fonte = open(os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read()
main = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()
d = pl.estado_dos_sinais()
por_col = {s["coluna"]: s for s in d["sinais"]}

print("\n[1] Toda coluna que costuma nascer vazia está coberta")
for col in ("narrativa", "trabalho", "maos_maquina", "orientacao",
            "decidido_por", "reavaliacao"):
    check(f"`{col}` tem diagnóstico", col in por_col, list(por_col))
    s_ = por_col.get(col) or {}
    check(f"`{col}` diz o que é", len(s_.get("o_que_e") or "") > 15)
    check(f"`{col}` diz onde é escrita", bool(s_.get("escrita_em")))
    check(f"`{col}` explica o que fazer se vier vazia",
          len(s_.get("se_vazia") or "") > 40)

print("\n[2] ⭐ Diz se a chave está ligada NESTE processo")
# É a única forma de saber se a variável que você mexeu no Render chegou.
check("`narrativa` está ligada por padrão", por_col["narrativa"]["ligado"] is True)
check("e sua chave é nomeada", por_col["narrativa"]["chave"] == "KV_NARRATIVA")
check("⭐ `trabalho` está DESLIGADO — e é por isso que a coluna é nula",
      por_col["trabalho"]["ligado"] is False)
check("com a chave certa nomeada",
      por_col["trabalho"]["chave"] == "KV_PRODUTIVIDADE_OPERADOR_V9")
check("`decidido_por` não tem chave — não depende de nada",
      por_col["decidido_por"]["chave"] is None
      and por_col["decidido_por"]["ligado"] is True)
check("a lista de desligados sai pronta",
      set(d["desligados"]) == {c for c, s_ in por_col.items() if not s_["ligado"]},
      d["desligados"])
check("e reflete o ambiente de verdade, não uma constante",
      por_col["trabalho"]["ligado"] is pl.PRODUTIVIDADE_OPERADOR_V9
      and por_col["narrativa"]["ligado"] is pl._NARRATIVA)

print("\n[3] ⭐ A ARMADILHA DOS CRUS está no aviso")
# 3 de cada 4 linhas são crus. Sem este aviso, a tabela parece quebrada mesmo
# quando está certa.
check("⭐ o aviso existe e cita `principal`",
      "principal = false" in d["aviso"] and "principal is true" in d["aviso"])
check("e explica que é POR DESENHO, não falha",
      "por desenho" in d["aviso"].lower())
check("`decidido_por` repete o alerta no seu próprio verbete",
      "crus" in por_col["decidido_por"]["se_vazia"].lower())

print("\n[4] Cada verbete aponta o conserto certo, não um genérico")
check("`narrativa`: histórico não é reprocessado",
      "DEPOIS do deploy" in por_col["narrativa"]["se_vazia"]
      and "histórico não" in por_col["narrativa"]["se_vazia"])
check("e cita o filtro de 120 caracteres, que é a outra causa possível",
      "120" in por_col["narrativa"]["se_vazia"])
check("`trabalho`: a chave FORÇA nulo mesmo com o modelo respondendo",
      "forçado a NULL" in por_col["trabalho"]["se_vazia"])
check("`maos_maquina`: sem zona 'maquina' o sensor não tem onde medir",
      "zona com papel 'maquina'" in por_col["maos_maquina"]["se_vazia"])
# ⚠️ A confusão mais fácil de cometer: achar que KV_ORIENTACAO_VERIFICADA
# controla a ESCRITA da orientação. Ela controla só se a orientação DECIDE.
check("⭐ `orientacao`: separa ESCREVER de DECIDIR",
      "independe de KV_ORIENTACAO_VERIFICADA" in por_col["orientacao"]["se_vazia"])
check("`reavaliacao`: vazio é o normal, só existe em correção manual",
      "é o normal" in por_col["reavaliacao"]["se_vazia"])

print("\n[5] Zero API, zero banco — dá para chamar sempre")
_corpo = fonte.split("def estado_dos_sinais")[1].split("\ndef ")[0]
for chamada in ("sb.table", "varrer(", "groq_", "anthropic", "requests."):
    check(f"não chama {chamada}", chamada not in _corpo)

print("\n[6] Chega ao operador do sistema por dois caminhos")
check("endpoint dedicado", '@app.get("/diagnostico/sinais")' in main)
check("e ele responde sobre o processo que está ATENDENDO",
      "está ATENDENDO agora" in main)
# O log de subida é o que faz o defeito ser descoberto ANTES de alguém abrir o
# banco e estranhar.
check("⭐ a subida ANUNCIA o que está desligado",
      "[sinais] DESLIGADOS nesta subida" in main)
check("e diz onde ver o detalhe", "/diagnostico/sinais" in main)
check("o aviso é não-fatal — diagnóstico não derruba a API",
      "diagnóstico de subida indisponível" in main)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
