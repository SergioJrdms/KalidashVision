"""Fase 109 — A CAUDA DE "<1%" NA ÁRVORE: quase tudo era artefato.

O relato: *"não podemos deixar esse <1% para essas ações... Tem como alterar
algo para fazer esse número crescer? Ou apresentar um número melhor?"*

⭐ SIM, E SEM INFLAR NADA — porque a maior parte daquele "<1%" não era "essa
atividade é rara". Eram três problemas somados:

  1. A ÁRVORE LISTAVA RÓTULO CRU, NÃO FAMÍLIA. `nomeHumano` já colapsa a
     família ao ESCREVER o nome (`operar_torno_ciclo` → "Operando o torno"),
     mas as LINHAS continuavam separadas. Duas linhas com o MESMO nome, cada
     uma com metade do tempo — e as duas metades pequenas o bastante para
     virarem "<1%".

  2. O DENOMINADOR ERA O TURNO, NÃO O RAMO. A tela existe para responder "o
     que compõe ESTE lado" — está escrito no cabeçalho dela — e mostrava a
     fatia do turno inteiro. 2% do trabalho produtivo aparecia como 1%.

  3. "<1%" APAGAVA A INFORMAÇÃO. Nasceu certo (dizer "0%" para algo que
     aconteceu é errado), mas 0,4% e 0,04% liam igual, e sete linhas iguais
     viram um muro que esconde as duas folhas que importam.

⚠️ O QUE ESTA SUÍTE PROTEGE ACIMA DE TUDO: nenhum número foi inflado. Somar
variantes da mesma família é a MESMA conta que o backend já faz para tendência,
a fatia do turno continua visível ao lado da fatia do ramo, e a cauda é somada
e expansível — nunca escondida. Folha com zero continua fora da árvore.

Rodar:  python tests_arvore_familia.py
"""
import os, re, sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
ARV = open(os.path.join(RAIZ, "frontend", "src", "pages", "Arvore.tsx"),
           encoding="utf-8").read()
RENDER = "\n".join(l for l in ARV.splitlines()
                   if not l.strip().startswith(("//", "*", "/*")))
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# ═══════════ [1] Linhas agrupadas por FAMÍLIA ═════════════════════════
print("\n[1] ⭐ Uma linha por família, não por rótulo cru")

check("a árvore usa a raiz da família para agrupar",
      "familiaLabel(d.comportamento)" in RENDER
      and 'import { nomeHumano, familiaLabel }' in RENDER)
check("⭐ a chave do agrupamento inclui o RAMO",
      'const chave = `${r}|${fam}`;' in RENDER)
check("com o motivo: duas variantes podem ter classificação diferente",
      "podem ter classificação diferente" in ARV)
check("o tempo das variantes é SOMADO", "atual.tempo_total_s += d.tempo_total_s" in RENDER)
check("e as ocorrências também", "atual.ocorrencias += d.ocorrencias" in RENDER)
check("a folha guarda os rótulos crus que somou",
      "atual.labels.push(d.comportamento)" in RENDER)
check("a linha é desenhada pela família",
      "key={d.familia}" in RENDER and "nomeHumano(d.familia)" in RENDER)
check("⭐ decisão HUMANA de qualquer variante manda na família inteira",
      'if (d.categoria_lean_origem === "humano")' in RENDER)
check("com o motivo: ela vale mais que a automática",
      "vale mais que a automática" in ARV)
check("a descrição mais informativa vence a eco-do-rótulo",
      "atual.descricao.length) atual.descricao" in RENDER)


# ═══════════ [2] Classificar continua honesto ═════════════════════════
print("\n[2] Mover a folha aplica a decisão a TODAS as variantes")

check("⭐ a mutação percorre os rótulos CRUS da família",
      "d.labels.forEach(" in RENDER
      and "classificar.mutate({ label: l, cat })" in RENDER)
check("nenhuma chamada de API recebe nome humano",
      not re.search(r"mutate\(\{ label: nomeHumano", RENDER))
check("o botão avisa quando a decisão vale para várias",
      "d.labels.length > 1" in RENDER and "variantes) para" in RENDER)
check("com o motivo: deixar as outras do lado errado seria pior",
      "outras do lado errado, e ele nem saberia que existem" in ARV)


# ═══════════ [3] O denominador certo ══════════════════════════════════
print("\n[3] A fatia DESTE LADO vem primeiro — é a pergunta da tela")

check("⭐ existe a fatia do ramo, além da do turno",
      "pct_ramo: number" in ARV and "x.pct_ramo = soma > 0" in RENDER)
check("a folha mostra a do RAMO em destaque", "{pctFolha(d.pct_ramo)}" in RENDER)
check("⭐ e a do TURNO continua ao lado, menor",
      "{pctFolha(d.pct_tempo)} do turno" in RENDER)
check("com o motivo: sem ela '45% do produtivo' pareceria 45% do dia",
      "pareceria 45% do dia" in ARV)
check("a raiz do ramo continua medindo contra o turno",
      "const pctDoRamo = (r: Ramo) =>" in RENDER and "/ totalS" not in RENDER.split("pctDoRamo")[1][:200]
      or "totalS > 0 ? (100 * s) / totalS : 0" in RENDER)


# ═══════════ [4] "<1%" saiu, "0%" continua proibido ═══════════════════
print("\n[4] O número pequeno passa a dizer QUANTO é")

check("⭐ '<1%' não existe mais", 'return "<1%"' not in ARV)
check("abaixo de 1 vai uma casa decimal",
      'if (pct >= 1) return `${pct.toFixed(1).replace(".", ",")}%`;' in RENDER)
check("abaixo de 0,1 vão duas (0,4% e 0,04% são coisas diferentes)",
      'return `${pct.toFixed(2).replace(".", ",")}%`;' in RENDER)
check("vírgula decimal, que é como se lê em português",
      '.replace(".", ",")' in RENDER)
check("⭐ folha com zero continua FORA da árvore",
      "if (!(d.tempo_total_s > 0)) continue;" in RENDER)
check("e o motivo antigo continua escrito",
      "nunca foi observada" in ARV)
check("o novo motivo também", "0,4% e 0,04% são coisas MUITO diferentes" in ARV)


# ═══════════ [5] A cauda: junta, soma, mas não esconde ════════════════
print("\n[5] As miudezas viram um número usável")

check("existe o agrupador da cauda", "function Cauda(" in ARV)
check("o corte é pela fatia do RAMO", "const CAUDA_PCT_RAMO = 3;" in RENDER
      and "d.pct_ramo >= CAUDA_PCT_RAMO" in RENDER)
check("⭐ uma folha pequena sozinha NÃO vira 'outras 1 ação'",
      "cauda.length > 1 ? grandes : todos" in RENDER)
check("a cauda mostra a SOMA das duas fatias",
      "somaRamo" in RENDER and "somaTurno" in RENDER
      and "do turno · juntas" in RENDER)
check("⭐ e abre a um clique — nada é escondido",
      "aria-expanded={aberta}" in RENDER and "{aberta && (" in RENDER)
check("cada folha dentro dela continua classificável",
      "onMover={(cat) => onMover(d.labels, cat)}" in RENDER)
check("com o motivo: esconder trocaria leitura por honestidade",
      "trocar um problema de\n// leitura por um problema de honestidade" in ARV)
check("a espinha da árvore não sobra depois da cauda",
      "ultima={i === itens.length - 1 && naCauda.length === 0}" in RENDER)


# ═══════════ [6] Nada foi inflado ═════════════════════════════════════
print("\n[6] ⭐ Nenhum número cresceu por invenção")

check("nenhum piso, mínimo ou multiplicador foi introduzido",
      not re.search(r"Math\.max\(\s*[0-9.]+\s*,\s*(pct|d\.pct)", ARV))
check("a soma da família é a mesma conta que o backend já faz",
      "def familia_label(" in open(os.path.join(RAIZ, "backend", "pipeline.py"),
                                   encoding="utf-8").read()
      and "mesma conta\n// que o backend já faz" in ARV)
check("o total do turno continua vindo de TODAS as folhas com tempo",
      "total += d.tempo_total_s;" in RENDER)
check("⛔ nenhuma duração absoluta entrou na tela",
      "min<" not in RENDER and "fmtDur" not in RENDER)
check("o motivo do desenho está escrito, não só aqui",
      "POR QUE A CAUDA ERA TODA" in ARV and "NADA AQUI INFLA NÚMERO" in ARV)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
