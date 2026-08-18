# ============================================================
# A MANCHETE DO POSTO — o topo responde "como foi o turno?".
#
# Ali havia "CAPTURA DESATUALIZADA · aguardando nova captura", em amarelo de
# alerta. Três problemas:
#
#  1. CONTAVA A HISTÓRIA ERRADA. O que atrasa não é a captura, é a FILA DE
#     PROCESSAMENTO: medido em 18/08, segmentos gravados às 06:20 foram
#     processados às 08:39 — com a câmera filmando o tempo todo.
#  2. ALARMAVA O NORMAL. Captura amostrada e processada em lote: a última
#     leitura ser de meia hora atrás é o funcionamento esperado. Alarme que
#     dispara no estado normal ensina a ignorar alarme.
#  3. GASTAVA O MELHOR ESPAÇO DA TELA COM UM ASSUNTO NOSSO. É o primeiro bloco
#     que um dono de fábrica olha, e ele não quer o relógio do nosso
#     processamento.
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
dash = open(os.path.join(RAIZ, "frontend", "src", "pages", "Dashboard.tsx"),
            encoding="utf-8").read()
lt = pl.leitura_do_turno

# Os números REAIS do posto: 72,9 · 75,2 · 78,8 e hoje 79,7.
SERIE = [{"presenca_pct": 72.9}, {"presenca_pct": 75.2},
         {"presenca_pct": 78.8}, {"presenca_pct": 79.7}]

print("\n[1] ⭐ O topo diz COMO FOI O TURNO")
r = lt(produtividade={"presenca_pct": 79.7}, serie=SERIE)
check("⭐ o título é o resultado, não o estado do sistema",
      r["titulo"] == "O operador esteve no posto em 80% do turno", r["titulo"])
check("e traz o número, não uma categoria", "80%" in r["titulo"])
check("não fala de captura, fila, sistema nem leitura",
      not any(t in (r["titulo"] + r["frase"]).lower()
              for t in ("captura", "fila", "sistema", "leitura", "processa")),
      r)

print("\n[2] ⭐ COMPARA O POSTO COM ELE MESMO")
# Não existe padrão de indústria para este posto. A régua justa é o histórico
# dele — e é a única que não exige inventar uma meta.
check("⭐ dia acima da média é reconhecido",
      r["tom"] == "bom" and "acima da média" in r["frase"], r)
check("com os dias anteriores como régua, sem meta inventada",
      "dias anteriores deste posto" in r["frase"]
      and "meta" not in r["frase"].lower())
check("e o delta bate com a conta", r["delta_pontos"] == 4.1, r["delta_pontos"])
check("o dia CORRENTE fica fora da própria régua",
      r["n_dias_comparados"] == 3, r["n_dias_comparados"])

fraco = lt(produtividade={"presenca_pct": 61.0},
           serie=[{"presenca_pct": 79.0}, {"presenca_pct": 80.0},
                  {"presenca_pct": 61.0}])
check("dia fraco vira atenção, não alarme",
      fraco["tom"] == "atencao" and "abaixo da média" in fraco["frase"], fraco)

print("\n[3] Oscilação normal NÃO vira notícia")
# ⚠️ 3 pontos é o piso. Abaixo disso é variação entre turnos, e apontá-la como
# novidade treina o gestor a duvidar do painel.
igual = lt(produtividade={"presenca_pct": 78.0},
           serie=[{"presenca_pct": 77.0}, {"presenca_pct": 79.0},
                  {"presenca_pct": 78.0}])
check("⭐ diferença pequena é 'em linha', não 'acima'",
      igual["tom"] == "neutro" and "Em linha" in igual["frase"], igual)
check("+2,9 ainda é 'em linha'",
      lt(produtividade={"presenca_pct": 82.9},
         serie=[{"presenca_pct": 80.0}, {"presenca_pct": 82.9}])["tom"] == "neutro")
check("+3,0 já é acima",
      lt(produtividade={"presenca_pct": 83.0},
         serie=[{"presenca_pct": 80.0}, {"presenca_pct": 83.0}])["tom"] == "bom")

print("\n[4] Quando não há o que dizer, ele diz isso")
primeiro = lt(produtividade={"presenca_pct": 80.0}, serie=[{"presenca_pct": 80.0}])
check("primeiro turno não inventa comparação",
      primeiro["delta_pontos"] is None and "régua dos próximos" in primeiro["frase"],
      primeiro)
vazio = lt(produtividade={"sem_dado": True})
check("sem dado se declara e não mostra zero como resultado",
      vazio["sem_dado"] and "0%" not in vazio["titulo"], vazio)
check("payload vazio não quebra", lt()["sem_dado"] is True)
check("série toda inválida não quebra",
      lt(produtividade={"presenca_pct": 80.0},
         serie=[{"presenca_pct": None}, {}])["delta_pontos"] is None)
check("cai para a permanência quando não há presenca_pct",
      lt(permanencia={"no_posto_pct": 66.0})["titulo"].endswith("66% do turno"))

print("\n[5] ⛔ Sem duração, como todo o resto da vitrine")
import re as _re
_txt = " ".join(r[k] for k in ("titulo", "frase"))
check("⛔ nenhuma duração absoluta",
      not _re.search(r"\d+\s*(min\b|minutos?\b|horas?\b)", _txt), _txt)

print("\n[6] Zero API, função pura")
_corpo = fonte.split("def leitura_do_turno")[1].split("\ndef ")[0]
for chamada in ("groq_", "anthropic", "sb.table", "varrer(", "requests.", "datetime.now"):
    check(f"não chama {chamada}", chamada not in _corpo)

print("\n[7] O alarme saiu da tela")
check("⭐ 'Captura desatualizada' não existe mais",
      "Captura desatualizada" not in dash)
# O texto só pode sumir do que é RENDERIZADO; num comentário ele documenta por
# que saiu, e apagar essa memória é como o alarme volta daqui a três meses.
import re as _re2
_render = _re2.sub(r"\{?/\*.*?\*/\}?", "", dash, flags=_re2.S)
_render = "\n".join(l for l in _render.splitlines()
                    if not l.strip().startswith("//"))
check("⭐ 'Aguardando nova captura' não é mais renderizado",
      "Aguardando nova captura" not in _render)
check("mas o motivo de ter saído fica registrado no código",
      "Aguardando nova captura" in dash)
check("o topo passa a ser 'O turno até agora'", "O turno até agora" in dash)
check("e mostra a manchete do backend", "const m = p.manchete;" in dash)
# O horário continua visível — informação, não alarme — e com o nome certo.
check("⭐ o horário vira 'Último trecho lido', não 'Capturado em'",
      "Último trecho lido:" in dash and "Capturado em ${" not in dash)
check("com o motivo escrito: a captura segue rodando adiante dele",
      "a captura segue rodando adiante dele" in dash)
check("o tom colore o card (bom/atenção/neutro)", "const TOM: Record<string," in dash)
check("e o motivo do assunto ter mudado está no código",
      "gastava o melhor espaço da tela" in dash.lower()
      or "melhor espaço da tela" in dash)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
