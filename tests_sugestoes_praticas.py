# ============================================================
# SUGESTÕES DO POSTO — sobre o PROCESSO e o OPERADOR.
#
# Duas versões erradas antes desta:
#
#  1. `PROMPT_ANALISE`, um consultor Lean de LLM: genéricas ("implantar 5S",
#     "reduzir setup via SMED"), nome complicado, sem dizer como fazer.
#  2. Regras minhas que falavam do SPECTRA: "veja se o computador da borda
#     está ligado", "abra a Fila", "desenhe a zona da máquina". O cliente não
#     tem acesso a hardware, a vídeo bruto nem às entranhas do produto —
#     aquilo era lista de tarefas NOSSA na tela DELE.
#
# O QUE ESTA SUÍTE PROTEGE:
# [1] Nenhuma sugestão nasce sem um NÚMERO que a dispare.
# [2] ⭐ Todo passo é executável no CHÃO DE FÁBRICA — nada de tela, cabo ou
#     configuração. É a guarda que impede a regressão nº 2.
# [3] Nenhuma acusa o operador. O número mede o POSTO, não a pessoa.
# [4] Toda sugestão traz o passo a passo — sem passo é opinião.
# [5] Zero API, função pura, não move número.
# ============================================================
import sys, types, os, re

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

# Cenário completo, com os números reais de 14/08 no mix de atividades.
TODOS = sug(
    produtividade={"posto_vazio_pct": 20.0, "presenca_pct": 79.7},
    permanencia={"fora_pct": 20.0},
    por_hora=[{"hora": 6, "desp_pct": 14.0}, {"hora": 7, "desp_pct": 15.0},
              {"hora": 8, "desp_pct": 13.0}, {"hora": 11, "desp_pct": 48.0}],
    atividades=[{"comportamento": "operar_torno", "pct_tempo": 33.0},
                {"comportamento": "monitorar_maquina", "pct_tempo": 25.0},
                {"comportamento": "conversando_colega", "pct_tempo": 11.0},
                {"comportamento": "deslocar_pelo_posto", "pct_tempo": 9.0}],
    serie=[{"presenca_pct": 90.0}, {"presenca_pct": 88.0}, {"presenca_pct": 70.0}],
    max_itens=99,
)

print("\n[1] ⭐ SEM NÚMERO, SEM SUGESTÃO")
check("posto saudável não gera sugestão nenhuma",
      sug(produtividade={"posto_vazio_pct": 4.0, "presenca_pct": 96.0},
          atividades=[{"comportamento": "operar_torno", "pct_tempo": 95.0}]) == [])
check("payload vazio não quebra e não inventa", sug() == [])
check("tudo None não quebra",
      sug(permanencia=None, produtividade=None, por_hora=None,
          atividades=None, serie=None) == [])
check("o cenário completo gera várias", len(TODOS) >= 5, len(TODOS))

print("\n[2] ⭐ TODO PASSO É EXECUTÁVEL NO CHÃO DE FÁBRICA")
# A guarda que impede a regressão: se alguém escrever de novo "abra a Fila" ou
# "veja se a câmera está ligada", esta suíte reprova.
_passos = " ".join(" ".join(s["passos"]) for s in TODOS).lower()
_tudo = " ".join(s["titulo"] + " " + s["porque"] + " " + " ".join(s["passos"])
                 for s in TODOS).lower()
PRODUTO = [
    "borda", "hardware", "câmera", "camera", "cabo", "internet", "energizad",
    "servidor", "sistema", "plataforma", "spectra", "prism", "dashboard",
    "abra a fila", "abra o dia a dia", "abra configurações", "validação",
    "processando", "zona da máquina", "reprocess", "vídeo", "video",
    "configure", "configuração", "banco de dados", "flag", "kv_",
]
_achados = [t for t in PRODUTO if t in _tudo]
check("⭐ nenhuma sugestão fala do produto, de tela ou de equipamento",
      not _achados, _achados)
# E o contrário: os passos precisam falar de gente e de coisa física.
CHAO = ["operador", "torno", "material", "ferramenta", "turno", "posto",
        "bancada", "abastec", "pausa", "desenho"]
check("e todas falam de gente, máquina ou material",
      sum(1 for t in CHAO if t in _passos) >= 6,
      [t for t in CHAO if t in _passos])

print("\n[3] ⭐ NENHUMA ACUSA O OPERADOR")
# O número mede o POSTO, não a pessoa. Gestor que usa isto para punir perde a
# cooperação de quem mais sabe onde o processo trava.
CULPA = ["preguiç", "vadia", "enrola", "desatento", "displicen", "cobrar dele",
         "advert", "punir", "chamar a atenção", "improdutivo do operador",
         "falta de comprometimento", "corpo mole"]
check("⭐ nenhuma palavra de culpa", not [c for c in CULPA if c in _tudo],
      [c for c in CULPA if c in _tudo])
check("e o primeiro passo costuma ser PERGUNTAR a ele",
      sum(1 for s in TODOS
          if any(p.lower().startswith(("pergunte", "veja com", "liste com",
                                       "descubra", "fique dez"))
                 for p in s["passos"][:1])) >= 3)
# O caso mais sensível: acompanhar a máquina NÃO pode ser lido como ociosidade.
_ciclo = [s for s in TODOS if s["chave"] == "tempo_de_ciclo"]
check("'acompanhar a máquina' é enquadrado como CICLO, não como parada",
      _ciclo and "não é parada" in _ciclo[0]["porque"], _ciclo)
# E conversa é enquadrada como interrupção de processo, não como indisciplina.
_conv = [s for s in TODOS if s["chave"] == "interrupcoes"]
check("conversa é enquadrada como recado na hora errada, não como indisciplina",
      _conv and "não é sobre proibir conversa" in _conv[0]["porque"].lower(), _conv)

print("\n[4] Cada sugestão carrega o número e diz COMO")
for s in TODOS:
    check(f"'{s['chave']}' traz o número no título",
          re.search(r"\d", s["titulo"]) is not None, s["titulo"])
    check(f"'{s['chave']}' tem ao menos 3 passos", len(s["passos"]) >= 3, s)
    check(f"'{s['chave']}' explica o porquê", len(s["porque"]) > 40, s["porque"])
    check(f"'{s['chave']}' cabe numa linha", len(s["titulo"]) <= 62, s["titulo"])

print("\n[5] Sem jargão e sem duração")
for jargao in ("valor agregado", "categoria lean", "5s", "smed", "kaizen",
               "takt", "oee", "kpi", "gargalo sistêmico", "otimizar",
               "taxa de ocupação", "acurácia", "inferência", "pipeline"):
    check(f"não usa '{jargao}'", jargao not in _tudo)
# "10h" é hora do relógio — localizador, não duração.
check("⛔ nenhuma duração absoluta",
      not re.search(r"\d+\s*(min\b|minutos?\b|horas?\b)", _tudo), _tudo[:160])

print("\n[6] Os gatilhos, um a um")
check("sair do posto dispara em 12%",
      sug(produtividade={"posto_vazio_pct": 12.0})[0]["chave"] == "posto_vazio")
check("e abaixo disso, silêncio", sug(produtividade={"posto_vazio_pct": 9.0}) == [])
_h = sug(por_hora=[{"hora": 6, "desp_pct": 10.0}, {"hora": 7, "desp_pct": 12.0},
                   {"hora": 11, "desp_pct": 45.0}, {"hora": 12, "desp_pct": 11.0}])
check("hora fora da curva é nomeada", _h and "11h" in _h[0]["titulo"], _h)
check("hora dispersa NÃO dispara — rotina não é achado",
      sug(por_hora=[{"hora": 6, "desp_pct": 20.0}, {"hora": 7, "desp_pct": 22.0},
                    {"hora": 8, "desp_pct": 19.0}]) == [])
check("ciclo automático dispara em 20% do mix",
      sug(atividades=[{"comportamento": "monitorar_maquina", "pct_tempo": 20.0}])[0]["chave"]
      == "tempo_de_ciclo")
check("andar pelo posto dispara em 8%",
      sug(atividades=[{"comportamento": "deslocar_pelo_posto", "pct_tempo": 8.0}])[0]["chave"]
      == "arranjo_do_posto")
check("conversa só entra quando é grande",
      sug(atividades=[{"comportamento": "conversando_colega", "pct_tempo": 11.0}])[0]["chave"]
      == "interrupcoes"
      and sug(atividades=[{"comportamento": "conversando_colega", "pct_tempo": 6.0}]) == [])
check("queda compara o posto COM ELE MESMO, não com meta inventada",
      sug(serie=[{"presenca_pct": 90.0}, {"presenca_pct": 88.0},
                 {"presenca_pct": 70.0}])[0]["chave"] == "queda")
check("série curta não conclui tendência",
      sug(serie=[{"presenca_pct": 90.0}, {"presenca_pct": 60.0}]) == [])
check("rótulo desconhecido não é somado a fatia nenhuma",
      sug(atividades=[{"comportamento": "afiar_ferramenta", "pct_tempo": 90.0}]) == [])

print("\n[7] Poucas por vez, ordenadas por peso")
check("no máximo três por padrão",
      len(sug(produtividade={"posto_vazio_pct": 30.0},
              atividades=[{"comportamento": "monitorar_maquina", "pct_tempo": 25.0},
                          {"comportamento": "deslocar_pelo_posto", "pct_tempo": 9.0},
                          {"comportamento": "conversando_colega", "pct_tempo": 12.0}])) == 3)
check("a maior perda vem primeiro", TODOS[0]["chave"] in ("posto_vazio", "hora_ruim"),
      TODOS[0]["chave"])
check("o campo de peso não vaza para a tela", all("_peso" not in x for x in TODOS))

print("\n[8] Zero API, função pura, e não move número")
_corpo = fonte.split("def sugestoes_do_posto")[1].split("\ndef ")[0]
for chamada in ("groq_", "anthropic", "sb.table", "varrer(", "requests.", "os.environ"):
    check(f"não chama {chamada}", chamada not in _corpo)
_perm = fonte.split("def permanencia_do_dia")[1].split("\ndef ")[0]
check("⭐ a permanência não conhece sugestão nenhuma", "sugesto" not in _perm)
prod = open(os.path.join(RAIZ, "backend", "productivity.py"), encoding="utf-8").read()
check("e o contrato de produtividade também não", "sugesto" not in prod)
check("a regra de ouro está escrita no código",
      "executável no CHÃO" in fonte and "lista de tarefas NOSSA na tela DELE" in fonte)

print("\n[9] Chega à tela com o passo a passo visível")
dash = open(os.path.join(RAIZ, "frontend", "src", "pages", "Dashboard.tsx"),
            encoding="utf-8").read()
check("o painel existe", "function OQueFazerAgora" in dash)
check("vem depois da qualidade da leitura, antes da evolução diária",
      dash.index("Qualidade da leitura") < dash.index("<OQueFazerAgora itens")
      < dash.index("<SerieComercial pontos"))
check("passos numerados", "<ol className" in dash and "s.passos.map" in dash)
check("sem itens, o painel some", "if (!itens.length) return null;" in dash)
main = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()
check("o mix de atividades é passado — é dele que sai o processo",
      "atividades=dist_enriquecida," in main)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
