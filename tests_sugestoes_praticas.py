# ============================================================
# SUGESTÕES DO POSTO — por regra, simples e com o "como fazer".
#
# As antigas vinham de `PROMPT_ANALISE`, um consultor Lean de LLM: genéricas
# ("implantar 5S", "reduzir setup via SMED"), com nome complicado, sem dizer
# COMO fazer, custando token e podendo inventar um problema que o dado não
# mostrava.
#
# O QUE ESTA SUÍTE PROTEGE:
# [1] Nenhuma sugestão nasce sem um NÚMERO que a dispare.
# [2] Toda sugestão traz o passo a passo — sem passo é opinião.
# [3] Linguagem de chão de fábrica: nada de jargão Lean.
# [4] Zero chamada de API, função pura.
# [5] Não move número nenhum.
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
sug = pl.sugestoes_do_posto

print("\n[1] ⭐ SEM NÚMERO, SEM SUGESTÃO")
# É a garantia central: se não houver gatilho medido, a tela fica em silêncio.
# Uma sugestão que aparece sempre é decoração, e o gestor aprende a ignorá-la.
check("posto saudável não gera sugestão nenhuma",
      sug(produtividade={"posto_vazio_pct": 4.0, "presenca_pct": 96.0,
                         "cobertura_produtividade_pct": 88.0},
          pendentes=3) == [], sug(produtividade={"posto_vazio_pct": 4.0}))
check("payload vazio não quebra e não inventa", sug() == [])
check("payload todo None não quebra",
      sug(permanencia=None, produtividade=None, por_hora=None,
          diagnostico_descricao=None) == [])

print("\n[2] Cada sugestão carrega o número que a disparou")
r = sug(produtividade={"posto_vazio_pct": 20.0, "presenca_pct": 79.7})
check("posto vazio dispara em 20%", len(r) == 1, r)
check("⭐ e o TÍTULO traz o número, não uma categoria",
      "20%" in r[0]["titulo"], r[0]["titulo"])
check("com nome de chão de fábrica",
      r[0]["titulo"] == "O posto ficou vazio em 20% do turno", r[0]["titulo"])
check("abaixo do gatilho, silêncio",
      sug(produtividade={"posto_vazio_pct": 14.0}) == [])
check("mais grave pesa mais e vem antes",
      sug(produtividade={"posto_vazio_pct": 30.0, "captura_atrasada": True})[0]["chave"]
      == "captura_parada")

print("\n[3] ⭐ TODA sugestão diz COMO fazer")
TODOS = sug(
    produtividade={"posto_vazio_pct": 22.0, "presenca_pct": 78.0,
                   "cobertura_produtividade_pct": 0.0, "captura_atrasada": True},
    permanencia={"fora_pct": 22.0},
    pendentes=111,
    por_hora=[{"hora": 6, "desp_pct": 10.0}, {"hora": 7, "desp_pct": 12.0},
              {"hora": 9, "desp_pct": 45.0}, {"hora": 10, "desp_pct": 11.0}],
    diagnostico_descricao={"pct_sem_observacao": 42.0},
    max_itens=99,
)
check("o cenário completo gera várias", len(TODOS) >= 5, len(TODOS))
for s_ in TODOS:
    check(f"'{s_['chave']}' tem passos", len(s_["passos"]) >= 2, s_)
    check(f"'{s_['chave']}' tem o porquê em uma frase",
          len(s_["porque"]) > 30 and s_["porque"].endswith("."), s_["porque"])
    check(f"'{s_['chave']}' tem título curto — cabe numa linha",
          len(s_["titulo"]) <= 60, s_["titulo"])

print("\n[4] ⭐ NADA DE JARGÃO — o dono reclamou de 'nomes complexos'")
_texto = " ".join(
    s_["titulo"] + " " + s_["porque"] + " " + " ".join(s_["passos"]) for s_ in TODOS
).lower()
for jargao in ("valor agregado", "categoria lean", "5s", "smed", "kaizen",
               "takt", "oee", "setup", "gargalo sistêmico", "otimizar",
               "taxa de ocupação", "recurso", "kpi", "não classificado",
               "concordância", "acurácia", "inferência", "pipeline"):
    check(f"não usa '{jargao}'", jargao not in _texto)
# ⛔ E a regra da permanência vale aqui também: percentual, nunca duração.
import re as _re
# A regra da permanência vale aqui: percentual sempre, duração nunca. "09h" é
# HORA DO RELÓGIO — localizador, não duração —, então fica de fora do corte.
check("⛔ nenhuma duração absoluta nas sugestões",
      not _re.search(r"\d+\s*(min\b|minutos?\b|horas?\b)", _texto), _texto[:200])

print("\n[5] Os gatilhos, um a um")
check("captura parada avisa que a tela mostra o PASSADO",
      "não o que está acontecendo agora"
      in sug(produtividade={"captura_atrasada": True})[0]["porque"],
      sug(produtividade={"captura_atrasada": True})[0]["porque"])
_h = sug(por_hora=[{"hora": 6, "desp_pct": 10.0}, {"hora": 7, "desp_pct": 12.0},
                   {"hora": 9, "desp_pct": 45.0}, {"hora": 10, "desp_pct": 11.0}])
check("hora fora da curva é detectada e nomeada",
      _h and "09h" in _h[0]["titulo"], _h)
check("hora dispersa NÃO dispara — rotina não é achado",
      sug(por_hora=[{"hora": 6, "desp_pct": 20.0}, {"hora": 7, "desp_pct": 22.0},
                    {"hora": 8, "desp_pct": 19.0}]) == [])
check("poucas horas não geram conclusão",
      sug(por_hora=[{"hora": 6, "desp_pct": 5.0}, {"hora": 7, "desp_pct": 90.0}]) == [])

# A cobertura zerada é enquadrada como CONFIGURAÇÃO, não como falha da IA —
# porque é isso que ela é, e porque "a IA não conseguiu" não tem ação.
_c = sug(produtividade={"cobertura_produtividade_pct": 0.0, "presenca_pct": 79.7})
check("cobertura zerada vira tarefa de configuração", _c and _c[0]["chave"] == "sem_lado_maquina")
check("e não culpa a IA nem manda mexer em variável de ambiente",
      "torno" in _c[0]["titulo"].lower()
      and "KV_" not in " ".join(_c[0]["passos"]), _c[0])
check("sem presença nenhuma, não pede configuração (não há o que configurar)",
      sug(produtividade={"cobertura_produtividade_pct": 0.0, "presenca_pct": 0}) == [])

check("fila grande dispara", sug(pendentes=111)[0]["chave"] == "fila")
check("fila pequena não incomoda", sug(pendentes=12) == [])
check("descrição sem observação só entra quando é grande",
      sug(diagnostico_descricao={"pct_sem_observacao": 42.0})[0]["chave"] == "sem_observacao"
      and sug(diagnostico_descricao={"pct_sem_observacao": 12.0}) == [])
check("e ela avisa que a PRESENÇA não é afetada",
      "presença NÃO é afetado"
      in " ".join(sug(diagnostico_descricao={"pct_sem_observacao": 42.0})[0]["passos"]))

print("\n[6] Poucas por vez — lista de dez é lista de zero")
check("no máximo três por padrão", len(sug(
    produtividade={"posto_vazio_pct": 22.0, "presenca_pct": 78.0,
                   "cobertura_produtividade_pct": 0.0, "captura_atrasada": True},
    pendentes=111, diagnostico_descricao={"pct_sem_observacao": 42.0})) == 3)
check("e vêm ordenadas por peso, não por categoria",
      [x["chave"] for x in sug(
          produtividade={"posto_vazio_pct": 22.0, "presenca_pct": 78.0,
                         "cobertura_produtividade_pct": 0.0,
                         "captura_atrasada": True},
          pendentes=111)][:2] == ["captura_parada", "posto_vazio"])
check("o campo de peso não vaza para a tela",
      all("_peso" not in x for x in TODOS))

print("\n[7] Zero API, função pura, e não move número")
_corpo = fonte.split("def sugestoes_do_posto")[1].split("\ndef ")[0]
for chamada in ("groq_", "anthropic", "sb.table", "varrer(", "requests.", "os.environ"):
    check(f"não chama {chamada}", chamada not in _corpo)
# Ela LÊ os números; nada dela volta para o cálculo.
_perm = fonte.split("def permanencia_do_dia")[1].split("\ndef ")[0]
check("⭐ a permanência não conhece sugestão nenhuma", "sugesto" not in _perm)
prod = open(os.path.join(RAIZ, "backend", "productivity.py"), encoding="utf-8").read()
check("e o contrato de produtividade também não", "sugesto" not in prod)

print("\n[8] Chega à tela com o passo a passo visível")
dash = open(os.path.join(RAIZ, "frontend", "src", "pages", "Dashboard.tsx"),
            encoding="utf-8").read()
check("o painel existe", "function OQueFazerAgora" in dash)
# Fica DEPOIS dos KPIs e da qualidade da leitura, e ANTES da evolução diária:
# o gestor lê o número, entende a confiança dele, e então recebe o que fazer.
check("e vem depois da qualidade da leitura, antes da evolução diária",
      dash.index("Qualidade da leitura") < dash.index("<OQueFazerAgora itens")
      < dash.index("<SerieComercial pontos"))
check("os passos são renderizados numerados", "<ol className" in dash and "s.passos.map" in dash)
check("com o porquê ao lado do título", "{s.porque}" in dash and "{s.titulo}" in dash)
check("sem itens, o painel some em vez de mostrar caixa vazia",
      "if (!itens.length) return null;" in dash)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
