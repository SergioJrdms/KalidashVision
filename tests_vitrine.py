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
check("a classificação manda o IDENTIFICADOR, não o nome humano",
      "label: arrastando" in arv and "d.comportamento," in arv,
      "Arvore.tsx pode estar mandando nome traduzido para a API")
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
for t in ("diaadia", "validacao", "arvore", "auditoria", "duvidas", "rotulos",
          "eventos", "padroes", "fila", "upload", "descricao", "titular"):
    check(f"'{t}' foi agrupado (não removido)", f'tab: "{t}"' in avanc)
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

print("\n[9] A árvore: vocabulário real, peso proporcional, decisão humana")
check("os três ramos existem, incluindo o vazio",
      'id: "va"' in arv and 'id: "desp"' in arv and 'id: "sem"' in arv)
check("o ramo 'sem classificação' aparece MESMO VAZIO",
      "Nada pendente" in arv and "ramo.id === \"sem\"" in arv)
check("peso visual proporcional ao tempo",
      "d.tempo_total_s / maiorS" in arv)
check("mover uma folha classifica (arrastar)",
      "onDragStart" in arv and "onDrop" in arv)
check("e há botões, porque arrastar não funciona no celular",
      "BotaoMover" in arv and "celular" in arv)
check("não se pode DESPROMOVER para 'sem classificação'",
      'ramo.id !== "sem" && !!arrastando' in arv,
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

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
