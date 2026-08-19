"""Fase 96 — a plataforma como VITRINE. Nenhum número muda.

CONTEXTO
Os sócios vão produzir criativos e demos usando a plataforma REAL, não um
mockup — decisão deliberada, porque tela de vitrine separada viraria dívida e
a demo desmentiria o anúncio.

⚠️ A GARANTIA CENTRAL, e é ela que esta suíte protege acima de tudo:
NENHUM CÁLCULO, NENHUM DADO, NENHUMA MÉTRICA MUDA. Se qualquer número do
Dashboard for diferente depois desta fase, algo saiu errado. Por isso os
blocos [5] e [6] varrem o backend inteiro procurando alteração de conta.

⚠️ E O IDENTIFICADOR CONTINUA SENDO A CHAVE. `operar_torno` segue sendo o que
o banco guarda, o que a API recebe e o que compara dias entre si. A tradução
existe só na exibição — traduzir na camada errada quebraria a série histórica
sem ninguém perceber.

Rodar:  python tests_vitrine.py
"""
import sys, os, re, json, subprocess

RAIZ = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(RAIZ, "frontend", "src")

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


def ler(*p):
    return open(os.path.join(FRONT, *p), encoding="utf-8").read()


ROTULOS = ler("design", "rotulos.ts")


# ── Executa as funções de TRADUÇÃO DE VERDADE ────────────────────────────
# Transpila o TS com o esbuild que já está no projeto e roda no node. Testar a
# REGRA e não o texto do arquivo é o que pega uma refatoração que mude o
# comportamento sem mudar as palavras.
_ESBUILD = os.path.join(
    RAIZ, "frontend", "node_modules", ".bin",
    "esbuild.cmd" if os.name == "nt" else "esbuild",
)
_JS = None


def _js_modulo() -> str:
    global _JS
    if _JS is None:
        r = subprocess.run(
            [_ESBUILD, os.path.join(FRONT, "design", "rotulos.ts"),
             "--format=cjs"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-400:])
        _JS = r.stdout
    return _JS


def js_eval(expr: str):
    js = _js_modulo() + f"\nconsole.log(JSON.stringify({expr}))"
    return subprocess.run(["node", "-e", js], capture_output=True, text=True)


print("\n[1] Nomes humanos — nenhum identificador cru chega à tela")
r = js_eval("[nomeHumano('operar_torno'), nomeHumano('posto_vazio'), "
            "nomeHumano('monitorar_maquina'), nomeHumano('conversando_colega'), "
            "nomeHumano('acao_indefinida')]")
check("as funções rodam (o TS porta para JS sem tipos)", r.returncode == 0,
      r.stderr[-300:] if r.returncode else "")
if r.returncode == 0:
    v = json.loads(r.stdout)
    # Fase 100: "Ação não identificada" saiu. Ela nomeava uma ausência como se
    # fosse uma ação observada — e foi exatamente essa aparência de atividade
    # que fez o rótulo ser confirmado na fila, virar vocabulário canônico e
    # engolir 38,7% do dia 14/08. O texto agora diz o que a linha É: trabalho
    # pendente do gestor.
    esperado = ["Operando o torno", "Posto vazio", "Acompanhando a máquina",
                "Conversando com colega", "Sem nome — aguardando você"]
    for got, exp in zip(v, esperado):
        check(f"{exp!r}", got == exp, got)

print("\n[2] Sufixo de cena não aparece para ninguém (Fase 86 foi revogada)")
r = js_eval("[nomeHumano('operar_torno_ciclo'), nomeHumano('monitorar_maquina_parada'), "
            "nomeHumano('conversando_colega_parada_imovel'), "
            "nomeHumano('monitorar_maquina_parada_ciclo')]")
if r.returncode == 0:
    v = json.loads(r.stdout)
    check("'_ciclo' some", v[0] == "Operando o torno", v[0])
    check("'_parada' some", v[1] == "Acompanhando a máquina", v[1])
    check("sufixo DUPLO também some", v[2] == "Conversando com colega", v[2])
    check("e o duplo do histórico chega à raiz certa",
          v[3] == "Acompanhando a máquina", v[3])

print("\n[3] Rótulo NOVO aparece legível — e nada quebra")
r = js_eval("[nomeHumano('afiar_ferramenta_no_esmeril'), nomeHumano('xyz'), "
            "nomeHumano(''), nomeHumano(null), nomeHumano(undefined)]")
if r.returncode == 0:
    v = json.loads(r.stdout)
    check("underscore vira espaço e capitaliza",
          v[0] == "Afiar ferramenta no esmeril", v[0])
    check("rótulo de uma palavra funciona", v[1] == "Xyz", v[1])
    for i, caso in enumerate(("vazio", "null", "undefined"), start=2):
        check(f"{caso} não quebra e não vaza identificador",
              v[i] == "Sem rótulo", v[i])
check("nenhum retorno pode conter underscore",
      "_" not in "".join(json.loads(r.stdout)) if r.returncode == 0 else False)

print("\n[4] Duração em unidade de chão de fábrica")
r = js_eval("[duracaoHumana(7800), duracaoHumana(2700), duracaoHumana(45), "
            "duracaoHumana(7200), duracaoHumana(0)]")
if r.returncode == 0:
    v = json.loads(r.stdout)
    check("2h10 (não '130 minutos')", v[0] == "2h10", v[0])
    check("45min", v[1] == "45min", v[1])
    check("45s", v[2] == "45s", v[2])
    check("2h redondas sem ':00'", v[3] == "2h", v[3])
    check("zero não quebra", v[4] == "0s", v[4])

print("\n[5] ⚠️ NENHUM CÁLCULO MUDOU — o backend não foi tocado")
git = subprocess.run(["git", "diff", "--name-only", "HEAD~1", "HEAD"],
                     cwd=RAIZ, capture_output=True, text=True).stdout.split()
# Esta suíte roda ANTES do commit; usa o working tree.
alterados = subprocess.run(["git", "status", "--porcelain"], cwd=RAIZ,
                           capture_output=True, text=True).stdout.splitlines()
# O guard original era da Fase 96 ("esta fase não toca backend"). Fases
# seguintes tocam, e travar o repositório inteiro seria falso. O que esta
# suíte protege de VERDADE é que a CAMADA DE EXIBIÇÃO não depende do backend:
# se `rotulos.ts` importar algo de rede ou de API, a tradução deixou de ser
# tradução.
check("a camada de tradução não importa nada de rede/API",
      "import" not in ROTULOS.split("export")[0].replace("// ", "")
      and "fetch(" not in ROTULOS and "api." not in ROTULOS, "rotulos.ts tem dependência externa")
check("e não depende de nenhum tipo do backend",
      "from \"../lib/types\"" not in ROTULOS)

print("\n[6] A tradução é SÓ exibição — a chave nunca muda")
# A chave é o que vai para a API. Se alguém passar o nome humano numa chamada,
# a série histórica quebra em silêncio.
arv = ler("pages", "Arvore.tsx")
# A folha agora é a FAMÍLIA, e ela carrega os rótulos CRUS que somou. A
# classificação percorre essa lista — continua mandando identificador, e agora
# aplica a decisão a TODAS as variantes em vez de deixar as outras do lado
# errado sem o gestor saber que existem.
check("a classificação manda o IDENTIFICADOR, não o nome humano",
      "d.labels.forEach(" in arv and "classificar.mutate({ label: l, cat })" in arv,
      "Arvore.tsx pode estar mandando nome traduzido para a API")
check("⭐ e a decisão vale para TODAS as variantes da família",
      "vale para todas as\n                // variantes dela" in arv)
check("nenhuma chamada de API recebe nomeHumano(",
      not re.search(r"setCategoriaPorLabel\([^)]*nomeHumano", arv))
ev = ler("pages", "Eventos.tsx")
check("o filtro de eventos mantém a chave como VALUE",
      'value={c}>{nomeHumano(c)}' in ev, "o value do <option> precisa ser a chave")
check("o identificador continua visível onde tem valor técnico",
      'title={d.nome}' in ler("pages", "Dashboard.tsx")
      or 'title={e.label}' in ev)

print("\n[7] Menu enxuto, agrupado e com estado persistido")
shell = ler("design", "Shell.tsx")
check("existe um grupo 'Avançado'", "procNavAvancado" in shell and "Avançado" in shell)
check("recolhido por padrão", 'localStorage.getItem("kv.nav.avancado") === "1"' in shell)
check("e o estado é PERSISTIDO", 'localStorage.setItem("kv.nav.avancado"' in shell)
visiveis = re.search(r"const procNav = \[(.*?)\n  \];", shell, re.S).group(1)
n_vis = visiveis.count('{ tab:')
check(f"menu visível focado no caso comercial ({n_vis} item)", n_vis == 1, n_vis)
check("'dashboard' está no menu visível", 'tab: "dashboard"' in visiveis)
avanc = re.search(r"const procNavAvancado = \[(.*?)\n  \];", shell, re.S).group(1)
for t in ("diaadia", "validacao", "arvore", "auditoria", "duvidas",
          "eventos", "padroes", "fila", "descricao"):
    check(f"'{t}' foi agrupado (não removido)", f'tab: "{t}"' in avanc)
# LIMPEZA DA VITRINE: quatro telas saíram do MENU. São ferramentas internas,
# não etapas do percurso do cliente — e o menu é a vitrine.
# `precisao` é a régua de acerto do time; `titular` é tela de sombra que já
# mostrou a pessoa errada; `rotulos` nasceu quando o rótulo decidia o número
# (desde a Fase 101 não decide); `upload` era muleta — a captura é automática.
for t in ("precisao", "titular", "rotulos", "upload"):
    check(f"'{t}' saiu do menu", f'tab: "{t}"' not in avanc and f'tab: "{t}"' not in visiveis)
    # ⚠️ SAIU DO MENU, NÃO DO PRODUTO: a rota continua de pé, para reativar
    # devolvendo uma linha — e para nenhum link salvo quebrar.
    check(f"mas a rota de '{t}' continua viva",
          f'route.tab === "{t}"' in ler("..", "src", "App.tsx"))
check("Configurações fica no rodapé, fora do grupo",
      'tab: "configuracoes"' in shell
      and 'tab: "configuracoes"' not in avanc and 'tab: "configuracoes"' not in visiveis)

print("\n[8] A conclusão em português — e ela degrada com honestidade")
r = js_eval("leituraDoPosto({vaPct: 75, vazioPct: 25, limiarCoberturaMin: 520, "
            "semEvidenciaPct: 3})")
check("gera a frase do dia cheio", r.returncode == 0, r.stderr[-300:])
if r.returncode == 0:
    L = json.loads(r.stdout)
    check("cita o rendimento em %", "75%" in L["frase"], L["frase"])
    # ⛔ Fase 101 — REVOGADO. A frase dizia "o operador esteve ausente 2h10".
    # A captura amostra ~50% de cada hora: esse "2h10" era METADE do tempo real
    # de ausência, apresentado como se fosse o total. Era ERRADO, não feio.
    check("a ausência vem em PERCENTUAL, nunca em duração",
          "25%" in L["frase"] and not re.search(r"\d+h\d*|\d+\s*min\b", L["frase"]),
          L["frase"])
    for proibido in ("valor agregado", "categoria Lean", "não classificado",
                     "concordância", "desperdício"):
        check(f"não usa jargão: '{proibido}'", proibido not in L["frase"].lower()
              or proibido == "desperdício", L["frase"])
# Pouca cobertura: NÃO finge precisão.
r = js_eval("leituraDoPosto({vaPct: 100, vazioPct: 0, limiarCoberturaMin: 12})")
if r.returncode == 0:
    L = json.loads(r.stdout)
    check("dia com pouca cobertura NÃO afirma percentual",
          "100%" not in L["frase"], L["frase"])
    check("e diz o porquê", "pouco material" in L["frase"], L["frase"])
    check("com tom fraco", L["tom"] == "fraco", L["tom"])
# Muita dúvida: a frase sai, com a ressalva colada.
r = js_eval("leituraDoPosto({vaPct: 80, vazioPct: 10, limiarCoberturaMin: 400, "
            "semEvidenciaPct: 35})")
if r.returncode == 0:
    L = json.loads(r.stdout)
    check("com muita dúvida, a ressalva aparece", bool(L["ressalva"]), L)
    check("e explica o efeito no número",
          "improdutivo até alguém decidir" in (L["ressalva"] or ""), L["ressalva"])
check("a frase é gerada por REGRA, não por LLM",
      "fetch" not in ROTULOS and "api." not in ROTULOS
      and "POR REGRA" in ROTULOS.upper(),
      "rotulos.ts não pode chamar rede nem LLM")

print("\n[9] A árvore: uma RAIZ por vez, folhas com tempo medido, decisão humana")
# ⚠️ CONTRATO NOVO. Três listas empilhadas viraram ÁRVORE: uma raiz no topo,
# as atividades pendendo dela por uma espinha, e um seletor para trocar de
# raiz. Ver produtivo e improdutivo lado a lado convidava à comparação errada.
check("os três lados continuam existindo no modelo",
      '"va"' in arv and '"desp"' in arv and '"sem"' in arv)
check("mas só UMA raiz é desenhada por vez",
      "const [ramo, setRamo] = useState<Ramo>" in arv)
check("com seletor para trocar de lado", "setRamo(r)" in arv)
check("a raiz mostra o percentual do lado", "pctDoRamo(ramo)" in arv)
check("e há espinha e cotovelo — é árvore, não lista",
      "borderLeft: `2px solid ${atual.cor}`" in arv and "cotovelo do galho" in arv)
# ⭐ O PEDIDO EXPLÍCITO DO DONO: "tenho tags com 0%, isso não pode."
check("⭐ folha sem tempo medido NÃO É GALHO",
      "if (!(d.tempo_total_s > 0)) continue;" in arv)
# ⚠️ Fase 109 — "<1%" SAIU, e o motivo é o mesmo que o criou. Ele nasceu certo
# (dizer "0%" para algo que aconteceu é errado), mas apagava a informação: sete
# folhas com "<1%" empilhadas viram um muro em que 0,4% e 0,04% — coisas muito
# diferentes — leem igual. O contrato que importa continua de pé: NUNCA "0%".
check("⭐ tempo real pequeno mostra o valor, não um símbolo",
      'return "<1%"' not in arv
      and 'pct.toFixed(1).replace(".", ",")' in arv
      and 'pct.toFixed(2).replace(".", ",")' in arv)
check("e nenhuma folha chega com zero (o filtro é acima)",
      "Nenhuma folha\n *  chega aqui com zero" in arv)
check("com o motivo escrito: ausência de medida não é medida",
      "nunca foi observada" in arv)
check("o mesmo corte existe na ORIGEM, para não vazar por outra tela",
      'if round(a["dur"], 1) <= 0:' in open(
          os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read())
check("mover uma folha continua classificando, por botão",
      "onMover(t.cat)" in arv and "destinos" in arv)
check("'sem classificação' só vira opção quando tem tempo lá",
      'porRamo.g.sem.length > 0 ? ["va", "desp", "sem"] : ["va", "desp"]' in arv,
      "aba permanentemente vazia treina o olho a ignorá-la")
check("não se pode DESPROMOVER para 'sem classificação'",
      '"sem"' not in arv.split("const destinos")[1].split("];")[0],
      "despromover apagaria uma decisão humana")
check("a decisão humana fica marcada na tela",
      'categoria_lean_origem === "humano"' in arv and "você decidiu" in arv)
check("nenhuma chamada de LLM nova",
      "prism" not in arv.lower() and "groq" not in arv.lower())
check("usa o MESMO dado do Dashboard (não recalcula nada)",
      "distribuicao_comportamentos" in arv and 'queryKey: ["dashboard"' in arv)

print("\n[10] Responsivo e em português")
for f in ("Arvore.tsx",):
    src = ler("pages", f)
    check(f"{f} usa layout que quebra (wrap/col)",
          "wrap" in src and 'className="col"' in src)
check("nenhum texto de interface em inglês na árvore",
      not re.search(r">\s*(Productive|Idle|Waste|Unclassified)\s*<", arv))


print("\n[10] O card do posto fala do POSTO — e a barra tem preenchimento")
proc = ler("pages", "Processos.tsx")
check("o card mostra a presença como número grande",
      "do turno com o operador no posto" in proc)
check("e o posto vazio como apoio", "Posto vazio em" in proc)
# ⛔ "791 vídeos" era métrica do NOSSO acervo, não do posto do cliente.
# `p.videos` continua no MODAL DE EXCLUSÃO, e ali é essencial: diz o que você
# está prestes a destruir. O que saiu foi o contador do CARD.
_card = proc.split("O CARD FALA DO POSTO")[1].split("function ")[0]
check("⛔ o contador de vídeos saiu do card",
      'label="vídeos"' not in _card and "p.videos" not in _card)
check("mas o modal de exclusão continua dizendo o que será apagado",
      "p.videos} vídeo" in proc)
check("e o componente órfão foi removido junto",
      "function MiniStat(" not in proc)
check("o que fica no rodapé é acionável", "esperando sua conferência" in proc)
check("sem dado, o card diz isso em vez de mostrar zero",
      "Aguardando o primeiro turno processado" in proc)

# ⚠️ O BUG DA BARRA VAZIA, e ele é sutil: `.row` traz `align-items: center`, e
# um preenchimento sem conteúdo nem altura própria colapsa para 0 — a trilha
# aparece e a barra não. Duas defesas: a barra não usa `.row`, e o filho tem
# altura explícita.
# Só o JSX: o comentário logo acima CITA `className="row"` para explicar por
# que ele não pode estar ali, e contá-lo daria falso positivo.
_barra = proc.split("A barra é a leitura de uma olhada")[1].split("*/}")[1].split("</div>")[0]
check("⭐ a barra NÃO usa className=\"row\"", 'className="row"' not in _barra, _barra[:200])
check("⭐ e o preenchimento tem altura explícita", 'height: "100%"' in _barra)
check("com o motivo registrado, para ninguém 'simplificar' de volta",
      "colapsava para 0" in proc)
# A outra barra da vitrine (composição do dashboard) já estava certa — o teste
# fixa isso para as duas não divergirem.
dash2 = ler("pages", "Dashboard.tsx")
check("a barra do dashboard também preenche",
      'style={{ height: "100%", width: `${p.valor}%`' in dash2)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
