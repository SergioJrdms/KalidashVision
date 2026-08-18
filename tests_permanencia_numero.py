# ============================================================
# Fase 101 — O NÚMERO VEM DA PERMANÊNCIA. A DESCRIÇÃO VIRA EVIDÊNCIA.
#
# Decisão dos sócios, 12/08. Fernando: "a única coisa que a gente conseguiu
# medir é tempo de permanência". Iago: "se ele está apontando pra máquina, ele
# está trabalhando. Não vamos inventar mais nada, até pelo prazo".
#
# O QUE ESTA SUÍTE PROTEGE:
# [1] O número não lê rótulo, descrição, categoria nem `trabalho`. Por
#     CONSTRUÇÃO, não por filtro — é a diferença entre "não deve influenciar" e
#     "não tem como influenciar".
# [2] Os estados somam 100% do tempo observado. Sem "indefinido", sem sobra.
# [3] Zero chamada de API.
# [4] ⛔ Nenhuma duração absoluta em superfície do cliente. Varredura de
#     arquivo, que é a única prova que não apodrece.
# [5] A orientação colapsa enquanto não houver dado — e o motivo fica na tela.
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


def ev(ini, fim, papel="operador", label="operar_torno", **kw):
    d = {"tempo_inicio_s": ini, "tempo_fim_s": fim, "papel_pessoa": papel,
         "comportamento_label": label, "principal": True}
    d.update(kw)
    return d


print("\n[1] Os estados somam 100% do tempo observado")
evs = [ev(0, 60), ev(60, 120), ev(120, 180, papel="posto_vazio", label="posto_vazio")]
p = pl.permanencia_do_dia(evs, None)
check("no posto + fora = 100%", abs(p["no_posto_pct"] + p["fora_pct"] - 100.0) < 0.05, p)
check("dois terços no posto", p["no_posto_pct"] == 66.7, p["no_posto_pct"])
check("um terço fora", p["fora_pct"] == 33.3, p["fora_pct"])
check("sem estado 'indefinido' na saída",
      not any("indefinid" in k or "nao_class" in k for k in p), list(p))

# Sem tempo nenhum não pode explodir nem inventar 100%.
vazio = pl.permanencia_do_dia([], None)
check("dia sem evento devolve zero e se declara sem dado",
      vazio["sem_dado"] and vazio["no_posto_pct"] == 0.0, vazio)
check("evento de duração zero não entra no denominador",
      pl.permanencia_do_dia([ev(10, 10)], None)["sem_dado"] is True)

print("\n[2] ⭐ RÓTULO ERRADO NÃO MOVE O NÚMERO — por construção")
base = [ev(0, 60), ev(60, 120, papel="posto_vazio", label="posto_vazio")]
p0 = pl.permanencia_do_dia(base, None)
# O MESMO minuto, com todo rótulo possível trocado por lixo.
for lixo in ("acao_indefinida", "nao_nomeado", "monitorar_maquina_parada",
             "operar_torno_ciclo", "conversando_colega", "rotulo_que_nao_existe"):
    p1 = pl.permanencia_do_dia(
        [ev(0, 60, label=lixo), ev(60, 120, papel="posto_vazio", label="posto_vazio")], None)
    check(f"rótulo '{lixo}' no minuto presente não muda nada",
          p1["no_posto_pct"] == p0["no_posto_pct"], (p1, p0))

# Track sintético e zero amostra: mesma imunidade.
p2 = pl.permanencia_do_dia(
    [ev(0, 60, pessoa_track_id=-2, n_amostras=0, confianca=None),
     ev(60, 120, papel="posto_vazio", label="posto_vazio",
        pessoa_track_id=-1, n_amostras=0)], None)
check("track sintético (-2) e zero amostra analisada NÃO mudam o número",
      p2["no_posto_pct"] == p0["no_posto_pct"], (p2, p0))

# Categoria Lean e julgamento do VLM: idem.
p3 = pl.permanencia_do_dia(
    [ev(0, 60, categoria_lean="desperdicio", trabalho=False, descricao_bruta="lixo"),
     ev(60, 120, papel="posto_vazio", label="posto_vazio",
        categoria_lean="valor_agregado", trabalho=True)], None)
check("categoria Lean e `trabalho` do VLM NÃO mudam o número",
      p3["no_posto_pct"] == p0["no_posto_pct"], (p3, p0))

# A prova estrutural: a função não MENCIONA os campos da esteira.
_corpo = fonte.split("def permanencia_do_dia")[1].split("\ndef ")[0]
for campo in ("categoria_lean", "descricao_bruta", "comportamento_label",
              '"trabalho"', "n_amostras", "confianca", "cat_por_label"):
    check(f"o corpo da função não lê {campo}", campo not in _corpo)

print("\n[3] Zero chamada de API")
for chamada in ("groq_text_call", "anthropic", "vlm_call", "requests.",
                "sb.table", "varrer("):
    check(f"nenhuma chamada a {chamada}", chamada not in _corpo)
check("é função PURA: só recebe eventos e a config da zona",
      "def permanencia_do_dia(eventos: list, frente_maquina: str | None = None)" in fonte)

print("\n[4] A orientação colapsa enquanto não houver dado")
check("por padrão a verificação está OFF", pl._ORIENTACAO_VERIFICADA is False)
check("com ela off, os dois estados vêm colapsados (None), não zerados",
      p["no_posto_torno_pct"] is None and p["no_posto_outro_lado_pct"] is None, p)
check("e a tela é instruída a não detalhar", p["detalhado"] is False)
check("a frase mostra só a permanência",
      pl.frase_permanencia(p) == "O operador esteve no posto em 67% do turno.",
      pl.frase_permanencia(p))

_ov = pl._ORIENTACAO_VERIFICADA
pl._ORIENTACAO_VERIFICADA = True
try:
    pd = pl.permanencia_do_dia(
        [ev(0, 60, orientacao="costas"), ev(60, 120, orientacao="frente"),
         ev(120, 180, papel="posto_vazio", label="posto_vazio")], "oposta")
    check("ligada, os TRÊS estados aparecem e somam 100%",
          abs(pd["no_posto_torno_pct"] + pd["no_posto_outro_lado_pct"]
              + pd["fora_pct"] - 100.0) < 0.2, pd)
    check("com `frente_maquina=oposta`, quem está de COSTAS está voltado ao torno",
          pd["no_posto_torno_pct"] == 33.3, pd)
    check("e a frase ganha o detalhe", "voltado para o torno" in pl.frase_permanencia(pd))
finally:
    pl._ORIENTACAO_VERIFICADA = _ov

print("\n[5] A orientação atravessa TODAS as fronteiras até o banco")
# O bug que travou o gate: `_abrir_evento` copiava `maos_maquina` e NÃO copiava
# `orientacao`, então `_orientacao_do_minuto` nunca via nada e a coluna nascia
# nula — ZERO linhas em 3.447 eventos.
def _obs(t, orient=None):
    return {"descricao": "x", "track_id": 1, "tempo_s": t, "frame_idx": int(t * 10),
            "zona": "posto_operador", "origem_gate": "analisado", "orientacao": orient}


e1 = pl.etapa_segmentar_eventos(
    [_obs(0, "costas"), _obs(3, "costas"), _obs(6, "frente")],
    lambda *a, **k: "operar_torno", 3.0)
check("o evento cru CARREGA a orientação (antes era descartada aqui)",
      e1[0].get("orientacao") == "costas", e1[0].get("orientacao"))
check("e é a MODA das amostras, não a primeira nem a última",
      e1[0]["orientacao"] == "costas")
e2 = pl.etapa_segmentar_eventos([_obs(0), _obs(3)], lambda *a, **k: "operar_torno", 3.0)
check("sem pose, orientação é None — e None nunca vira 'de frente'",
      e2[0].get("orientacao") is None)
check("o campo temporário não vaza para o banco", "_orient" not in e1[0])
check("o minuto pondera as modas dos crus", "_orientacao_do_minuto" in fonte)

print("\n[6] ⛔ NENHUMA DURAÇÃO ABSOLUTA EM SUPERFÍCIE DO CLIENTE")
# A prova que não apodrece: varre os ARQUIVOS. Um `fmtDur` novo amanhã quebra
# esta suíte, e é esse o ponto.
#
# Superfícies do cliente, na definição do pedido: "dashboard, evolução por dia,
# ritmo por hora, jornada típica, Pareto, árvore, relatórios e exportações".
SUPERFICIES_CLIENTE = [
    "pages/Dashboard.tsx", "pages/Dashboard2.tsx", "pages/Arvore.tsx",
    "pages/Rotulos.tsx", "pages/Processos.tsx", "pages/Padroes.tsx",
]
# FERRAMENTA INTERNA — fora da regra por decisão explícita do dono: "a tela de
# validação pode exibir o instante do trecho (120→180s) como LOCALIZADOR, para
# eu achar o momento no vídeo. Isso é ferramenta interna, não métrica."
FERRAMENTAS = ["pages/Validacao.tsx", "pages/Fila.tsx", "pages/Duvidas.tsx",
               "pages/Eventos.tsx", "pages/ConfiguracoesSaude.tsx",
               "pages/Extras.tsx", "pages/Titular.tsx"]

# Formatadores de DURAÇÃO e unidades de tempo decorrido. Hora-do-dia ("14h" no
# eixo do ritmo) é LOCALIZADOR, não duração — e por isso não entra aqui.
PROIBIDO = [
    (r"\bfmtDur\s*\(", "fmtDur()"),
    (r"\bfmtSeg\s*\(", "fmtSeg()"),
    (r"\bduracaoHumana\s*\(", "duracaoHumana()"),
    (r"\}\s*min\b", "'} min'"),
    (r"minutos_observados", "minutos_observados"),
    (r"minutos_sem_categoria", "minutos_sem_categoria"),
    (r"tempoObservadoMin", "tempoObservadoMin"),
    (r"\bpor_turno_s\b", "por_turno_s"),
    (r"\bpor_mes_s\b", "por_mes_s"),
    (r"turno_h\}", "turno_h"),
]
achados = []
for rel in SUPERFICIES_CLIENTE:
    caminho = os.path.join(RAIZ, "frontend", "src", rel)
    if not os.path.exists(caminho):
        continue
    txt = open(caminho, encoding="utf-8").read()
    for i, linha in enumerate(txt.splitlines(), 1):
        cru = linha.strip()
        # Comentário não é superfície: é onde EXPLICAMOS por que a duração saiu.
        if cru.startswith("//") or cru.startswith("*") or cru.startswith("/*"):
            continue
        for padrao, nome in PROIBIDO:
            if re.search(padrao, linha):
                achados.append(f"{rel}:{i} → {nome} | {cru[:70]}")

check("nenhuma duração absoluta nas superfícies do cliente",
      not achados, "\n         " + "\n         ".join(achados[:12]))
check("e a varredura de fato olhou os arquivos (não passou vazia)",
      all(os.path.exists(os.path.join(RAIZ, "frontend", "src", r))
          for r in SUPERFICIES_CLIENTE[:4]))

# O localizador da validação continua vivo — a exceção é deliberada e testada,
# para ninguém "limpar" a ferramenta achando que é vitrine.
_val = open(os.path.join(RAIZ, "frontend", "src", "pages", "Validacao.tsx"),
            encoding="utf-8").read()
check("a EXCEÇÃO existe: a validação mantém o localizador do trecho",
      "fmtSeg" in _val or "toFixed(1)}s" in _val)
check("e ela está na lista de ferramentas, não de vitrines",
      "pages/Validacao.tsx" in FERRAMENTAS)

print("\n[7] O motivo estatístico está escrito, não é preferência estética")
check("a regra do percentual explica a amostragem de ~50%",
      "amostra" in fonte and "METADE da verdade" in fonte)
check("na função da frase, que é o que o cliente lê",
      "METADE da verdade" in fonte.split("def frase_permanencia")[1][:900])

print("\n[8] A tela padrão mostra somente o contrato comercial")
dash = open(os.path.join(RAIZ, "frontend", "src", "pages", "Dashboard.tsx"),
            encoding="utf-8").read()
corpo_padrao = dash.split("export default function Dashboard", 1)[1].split(
    "type IconeNome", 1
)[0]
check("o topo responde produtividade, presença e posto vazio",
      all(x in corpo_padrao for x in (
          'titulo="Produtividade"', 'titulo="Operador no posto"',
          'titulo="Posto vazio"')))
# Fase 106 — DECISÃO DE PRODUTO: a cobertura saiu da tela do cliente. Ela
# falava do MEDIDOR (quanto da leitura foi excluída), não da fábrica, e o dono
# não compra o medidor. O que a suíte ainda protege é o que a cobertura
# protegia: incerteza NÃO PODE virar improdutividade. Isso continua valendo no
# backend — o número já nasce calculado só sobre leitura válida — e na tela
# pelo selo de calibração, que barra o uso comercial de leitura fraca.
check("a incerteza continua fora do número (o cálculo é só sobre leitura válida)",
      "somente sobre leituras válidas do posto" in corpo_padrao)
check("e a tela do cliente não expõe mais a instrumentação",
      "Qualidade da leitura" not in corpo_padrao
      and "inconclusivo_pct" not in corpo_padrao)
check("baixa cobertura bloqueia o uso em criativo",
      "Leitura em calibração" in corpo_padrao)
check("o dashboard padrão não renderiza o placar/Pareto Lean antigo",
      "<PlacarHero" not in corpo_padrao and "<ParetoPanel" not in corpo_padrao)
adapt = open(os.path.join(RAIZ, "frontend", "src", "lib", "adapt.ts"),
             encoding="utf-8").read()
check("o contrato vem do backend sem ser recalculado no frontend",
      "produtividade: ProdutividadePosto" in adapt and "produtividade," in adapt)

print("\n[9] Nada foi apagado nem desligado — só saiu da conta")
check("o cluster continua existindo", "def etapa_clusterizar" in fonte)
check("a categoria Lean continua existindo", "def classificar_comportamentos_lean" in fonte)
check("a árvore de decisão continua existindo", "def arvore_decidir" in fonte)
check("`decidir_permanencia` (a esteira da Fase 97) continua de pé",
      "def decidir_permanencia" in fonte)
check("a versão do instrumento subiu", pl.VERSAO_INSTRUMENTO >= 8)
check("com a data e o motivo registrados", "8 = (14/08) o NÚMERO PRINCIPAL" in fonte)

print("\n[9] ⭐ REGRA PRIMÁRIA: só conta quem está NO POSTO")
# O dono viu a fila chamando de OPERADOR alguém fora do posto. Auditando, o
# defeito era anterior e pior: a guarda "ausência de identidade não é presença"
# existia, mas atrás de `KV_PRODUTIVIDADE_OPERADOR_V9` — que é fail-closed e
# vem DESLIGADA. Com ela off, `papel_pessoa = None` voltava a cair no mesmo
# ramo do operador.
#
# O caso que isso deixava passar é o pior: sem zona de posto desenhada, a
# eleição inteira é pulada, o papel nasce nulo para todo mundo, e qualquer
# pessoa em qualquer canto do quadro virava "operador no posto" — a permanência
# ia a 100% justamente quando o sistema não sabia onde o posto fica.
check("⭐ a guarda NÃO depende mais de flag nenhuma",
      'if papel != "operador":' in fonte
      and 'if papel != "operador" and PRODUTIVIDADE_OPERADOR_V9' not in fonte)
check("com o motivo escrito", "REGRA PRIMÁRIA: SÓ CONTA QUEM ESTÁ NO POSTO" in fonte)

_sem_papel = pl.permanencia_do_dia([ev(0, 60, papel=None), ev(60, 120, papel=None)], None)
check("⭐ pessoa sem papel NÃO vira presença",
      _sem_papel["no_posto_pct"] == 0.0, _sem_papel)
check("e o tempo dela sai do DENOMINADOR — não vira 'fora' por tabela",
      _sem_papel["fora_pct"] == 0.0 and _sem_papel["inconclusivo_pct"] == 100.0,
      _sem_papel)
check("o dia se declara sem dado em vez de mostrar 0% como resultado",
      _sem_papel["sem_dado"] is True)

check("visitante continua fora da permanência do titular",
      pl.permanencia_do_dia(
          [ev(0, 60, papel="operador"), ev(60, 120, papel="visitante")], None
      )["no_posto_pct"] == 50.0)
_normal = pl.permanencia_do_dia(
    [ev(0, 60, papel="operador"),
     ev(60, 120, papel="posto_vazio", label="posto_vazio")], None)
check("e o caso normal não mudou — operador × posto vazio segue 50/50",
      _normal["no_posto_pct"] == 50.0 and _normal["fora_pct"] == 50.0
      and _normal["cobertura_pct"] == 100.0, _normal)
# ⚠️ "Não sei" não pode ser promovido a "sei que sim", mas também não pode ser
# rebaixado a "sei que não" — some do denominador e aparece como cobertura.
check("o inconclusivo aparece como COBERTURA, não como fatia",
      "cobertura_pct" in _normal and "inconclusivo_pct" in _normal)
_misto = pl.permanencia_do_dia(
    [ev(0, 60, papel="operador"), ev(60, 120, papel=None)], None)
check("com metade inconclusiva, a cobertura cai e o percentual não mente",
      _misto["no_posto_pct"] == 100.0 and _misto["cobertura_pct"] == 50.0, _misto)

print("\n[10] A descrição completa abre — sem depender de <details>")
val = open(os.path.join(RAIZ, "frontend", "src", "pages", "Validacao.tsx"),
           encoding="utf-8").read()
# `<details className="col">` põe `display: flex` no elemento, e mudar o
# display de um <details> quebra o colapso nativo: o botão aparecia, o clique
# registrava e o texto não abria.
# Só o que é RENDERIZADO: o comentário acima do componente CITA `<details>`
# para explicar por que ele saiu, e apagar essa memória é como o bug volta.
_val_render = "\n".join(l for l in val.splitlines() if not l.strip().startswith("//"))
check("⭐ não usa mais <details>", "<details" not in _val_render)
check("mas o motivo de ter saído fica registrado", "<details" in val)
check("e sim um estado controlado",
      "function DescricaoCompleta(" in val and "const [aberto, setAberto]" in val)
check("o texto só renderiza quando aberto", "{aberto && (" in val)
check("o rótulo diz o que vai acontecer nos dois estados",
      "Veja aqui a descrição completa" in val
      and "Ocultar a descrição completa" in val)
# Sem o key, o painel ficaria aberto ao trocar de card e o gestor leria a
# descrição do trecho anterior achando que era a do atual.
check("⭐ troca de card fecha o painel (key pelo id do evento)",
      "<DescricaoCompleta key={evento.id}" in val)
check("com o motivo registrado", "quebra o colapso nativo" in val)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
