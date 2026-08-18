"""Fase 106 — O TETO DO POSTO no lugar da "Qualidade da leitura".

"Qualidade da leitura" (cobertura da identificação, cobertura da decisão, %
inconclusivo) era INSTRUMENTAÇÃO: falava do medidor, não da fábrica. O dono não
compra o medidor. Decisão de produto: sai da tela do cliente.

No lugar entra a resposta para a pergunta que o gestor faz sozinho diante de
qualquer número de produtividade — "e daí, quanto DÁ para melhorar?". A resposta
não é benchmark de consultoria: é o melhor dia que ESTE posto já entregou, com
ESTE operador e ESTA máquina. Ninguém contesta o próprio recorde.

Não repete o resto da tela: os KPIs dão a MÉDIA do período, a "Evolução diária"
dá a TRAJETÓRIA, e aqui é a DISTRIBUIÇÃO — piso, típico, teto — com a folga
entre típico e teto em destaque.

⚠️ TRÊS HONESTIDADES QUE ESTA SUÍTE TRAVA:
  1. "Típico" é MEDIANA, não média. Com 7 pontos, um dia de manutenção puxaria
     a média e inventaria uma oportunidade que não existe.
  2. A escala é sempre 0–100%. Ampliar a faixa medida faria 78→89 parecer
     abismo — o truque de gráfico mais comum e o mais desonesto.
  3. Menos de 3 dias válidos NÃO desenha recorde. "Melhor dia" com n=2 é
     sorteio, e sorteio virando meta é a mesma família de erro que já nos
     custou caro: ausência de medida virando medida.

Rodar:  python tests_teto_do_posto.py
"""
import json, os, subprocess, sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(RAIZ, "frontend", "src")
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


D = open(os.path.join(FRONT, "pages", "Dashboard.tsx"), encoding="utf-8").read()
render = "\n".join(l for l in D.splitlines()
                   if not l.strip().startswith(("//", "*", "/*")))

# Roda `faixaDa`/`mediana` DE VERDADE: a regra, não as palavras.
_ini = D.index("function mediana(")
_fim = D.index("function TetoDoPosto(")
_TS = os.path.join(RAIZ, ".teto_teste.ts")
open(_TS, "w", encoding="utf-8").write(
    "type PontoSerie = { dia: string; presenca_pct: number | null; "
    "produtividade_pct: number | null };\n"
    + D[_ini:_fim] + "\nexport { faixaDa, mediana };\n")
_ESBUILD = os.path.join(RAIZ, "frontend", "node_modules", ".bin", "esbuild")


def js(expr: str):
    r = subprocess.run([_ESBUILD, _TS, "--format=cjs", "--loader:.ts=ts"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:])
    out = subprocess.run(
        ["node", "-e", r.stdout + f"\nconsole.log(JSON.stringify({expr}))"],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-500:])
    return json.loads(out.stdout)


def serie(*presencas):
    return [{"dia": f"2026-08-{10 + i:02d}", "presenca_pct": v,
             "produtividade_pct": None} for i, v in enumerate(presencas)]


# ══════════════════ [1] A instrumentação saiu da tela ══════════════════
print("\n[1] 'Qualidade da leitura' saiu — ela falava do medidor")

check("⭐ o card sumiu da tela", "Qualidade da leitura" not in render)
check("a cobertura da identificação não é mais exibida",
      "cobertura_identificacao_pct" not in render)
check("nem a cobertura da decisão", "cobertura_produtividade_pct" not in render)
check("⭐ nem o % de leituras inconclusivas", "inconclusivo_pct" not in render)
check("o componente órfão foi removido junto (nada de código morto)",
      "QualidadeItem" not in D)
check("o motivo da retirada está escrito",
      "fala do MEDIDOR, não da\n// FÁBRICA" in D)


# ══════════════════ [2] O que entrou ═══════════════════════════════════
print("\n[2] O teto: o recorde do próprio posto vira a meta")

check("o card existe", "function TetoDoPosto(" in D and "<TetoDoPosto" in D)
check("com título de fábrica, não de instrumento", "O teto deste posto" in D)
check("e a frase que desarma a objeção de meta",
      "É a meta que ninguém contesta." in render)
check("⭐ a faixa afirma UMA coisa, em forma de frase",
      "no dia comum" in render and "no seu melhor dia" in render)
check("⭐ o 'pior dia' saiu — não gerava ação e assustava sem motivo",
      "pior dia" not in render)
check("⭐ 'pontos de folga' saiu — ponto percentual é vocabulário de analista",
      "de folga" not in render and "pontos</b>" not in render)
check("o listrado ganhou nome ali do lado",
      "o listrado é o que dá para ganhar" in render)
check("aponta a DATA do melhor dia (dá para ir perguntar o que houve)",
      "diaMelhor" in D and "o que foi diferente em" in render)
check("o parágrafo de três linhas virou uma frase",
      "Foi ele quem fez esse número." in render
      and "Repetir o melhor dia em vez do dia típico" not in D)
check("os dias medidos ficam, pequenos: é o que sustenta o recorde",
      "dias medidos" in render)
check("cobre presença E produtividade sem repetir componente",
      render.count("<FaixaTeto") == 2)
check("o motivo da simplificação está escrito",
      "ERA COMPLEXO DEMAIS" in D and "ponto percentual é jargão" not in D
      and "não de chão de fábrica" in D)


# ══════════════════ [3] Típico é MEDIANA, não média ════════════════════
print("\n[3] 'Típico' é mediana — um dia ruim não inventa oportunidade")

check("mediana de ímpar", js("mediana([61,78,89])") == 78)
check("mediana de par é o meio-termo dos centrais",
      js("mediana([60,70,80,90])") == 75)
check("a ordem de entrada não muda nada", js("mediana([89,61,78])") == 78)

# 6 dias bons + 1 dia de manutenção. A média cairia ~8 pontos; a mediana não.
f = js(f"faixaDa({json.dumps(serie(78,79,80,77,78,81,12))}, 'presenca_pct')")
check("⭐ um dia de manutenção NÃO desloca o típico",
      f["tipico"] == 78, f)
check("mas ele continua visível como pior dia", f["pior"] == 12, f)
media = (78 + 79 + 80 + 77 + 78 + 81 + 12) / 7
check("⭐ e a média (que inflaria a folga em ~9 pontos) não é usada",
      abs(f["tipico"] - media) > 8, (f["tipico"], media))


# ══════════════════ [4] A escala não mente ═════════════════════════════
print("\n[4] Escala fixa 0–100 — sem zoom que fabrica abismo")

check("⭐ a barra é posicionada em % absoluto do 0 ao 100",
      'width: `${lim(f.tipico)}%`' in D
      and 'left: `${lim(f.tipico)}%`' in D)
check("com trava nas pontas", "Math.max(0, Math.min(100, v))" in D)
check("⭐ nenhum eixo é calculado a partir do min/max medido",
      "f.pior}%`" not in D and "- f.pior" not in D)
check("o motivo está escrito", "o truque de gráfico mais comum" in D)


# ══════════════════ [5] Poucos dias NÃO viram recorde ══════════════════
print("\n[5] Com 2 dias não há recorde — sorteio não é meta")

check("⭐ 2 dias válidos não desenham faixa",
      js(f"faixaDa({json.dumps(serie(78, 89))}, 'presenca_pct')") is None)
check("3 já desenham",
      js(f"faixaDa({json.dumps(serie(78,89,61))}, 'presenca_pct')") is not None)
check("dias sem leitura não contam para o mínimo de 3",
      js(f"faixaDa({json.dumps(serie(78, None, None, 89))}, 'presenca_pct')") is None)
check("e não entram no cálculo quando há dias suficientes",
      js(f"faixaDa({json.dumps(serie(78, None, 89, 61))}, 'presenca_pct')")["n"] == 3)
check("série vazia não quebra", js("faixaDa([], 'presenca_pct')") is None)
check("a tela explica a espera em vez de mostrar caixa vazia",
      "o melhor deles é sorteio — não meta" in D)
check("posto já no teto não inventa folga",
      js(f"faixaDa({json.dumps(serie(80,80,80))}, 'presenca_pct')")["melhor"] == 80
      and "— e é o seu melhor" in render)
check("e nesse caso nem desenha o listrado nem a legenda dele",
      "{temFolga && (" in D and "temFolga = f.melhor - f.tipico >= 1" in D)

os.remove(_TS)
print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
