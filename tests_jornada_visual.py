# ============================================================
# A JORNADA "TODOS OS DIAS" — conserto de DESENHO, não de dado.
#
# No dia único o gráfico ficava legível; no agregado virava confete. A causa
# não era o dado, era a renderização: no agregado cada bloco de 15 min vira até
# três fatias (produtivo + desperdício + vazio, na proporção somada dos dias),
# e com ~40 blocos isso dá ~120 retângulos de 3 px.
#
# Três coisas somavam:
#  1. a ordem das fatias DENTRO do bloco variava — e ela não carrega horário
#     nenhum (dentro do bloco largura é proporção, não instante);
#  2. fatias vizinhas de mesma cor eram desenhadas como retângulos separados;
#  3. cada fatia tinha canto arredondado, e num retângulo de 3 px o raio come
#     a fatia e abre um vão claro entre vizinhas.
#
# ⚠️ O QUE ESTA SUÍTE PROTEGE ACIMA DE TUDO: nenhum número muda. A soma de cada
# categoria e os BURACOS (horário sem filmagem) têm de sair idênticos — senão
# "melhorar o visual" virou falsear o gráfico.
# ============================================================
import json, os, subprocess, sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(RAIZ, "frontend", "src")
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


D2 = open(os.path.join(FRONT, "pages", "Dashboard2.tsx"), encoding="utf-8").read()

# ── Roda a função DE VERDADE: recorta o trecho puro do TSX e executa no node.
# Testar a regra, e não o texto do arquivo, é o que pega uma refatoração que
# mude o comportamento sem mudar as palavras.
_ini = D2.index("const BIN_MIN = 15;")
_fim = D2.index("function JornadaDoDia")
_TS = os.path.join(RAIZ, ".jornada_teste.ts")
open(_TS, "w", encoding="utf-8").write(
    D2[_ini:_fim] + "\nexport { suavizarJornada };\n")
_ESBUILD = os.path.join(RAIZ, "frontend", "node_modules", ".bin", "esbuild")


def js(expr: str):
    r = subprocess.run([_ESBUILD, _TS, "--format=cjs", "--loader:.ts=ts"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    out = subprocess.run(
        ["node", "-e", r.stdout + f"\nconsole.log(JSON.stringify({expr}))"],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-400:])
    return json.loads(out.stdout)


# Agregado realista: blocos de 15 min com três fatias em ordem embaralhada,
# um BURACO de 30 min (almoço sem filmagem) e blocos limpos depois.
CRUAS = []
_t = 360.0
for b in range(8):
    ordem = ["desp", "va", "vazio"] if b % 2 else ["vazio", "va", "desp"]
    larg = [1.5, 12.0, 1.5] if b % 2 else [1.0, 13.0, 1.0]
    for cat, w in zip(ordem, larg):
        CRUAS.append({"ini_m": _t, "fim_m": _t + w, "cat": cat})
        _t += w
_t += 30.0                      # ⬅ o buraco
for b in range(4):
    CRUAS.append({"ini_m": _t, "fim_m": _t + 15.0, "cat": "va"})
    _t += 15.0

CJ = json.dumps(CRUAS)
SUAVE = js(f"suavizarJornada({CJ}, true)")


def soma(faixas):
    d = {}
    for f in faixas:
        d[f["cat"]] = round(d.get(f["cat"], 0) + (f["fim_m"] - f["ini_m"]), 4)
    return d


def buracos(faixas):
    return sum(1 for i in range(1, len(faixas))
               if abs(faixas[i - 1]["fim_m"] - faixas[i]["ini_m"]) > 0.01)


print("\n[1] ⭐ NENHUM NÚMERO MUDA — é a garantia da fase")
check("⭐ a soma de cada categoria é IDÊNTICA", soma(SUAVE) == soma(CRUAS),
      (soma(CRUAS), soma(SUAVE)))
check("o intervalo total é o mesmo",
      SUAVE[0]["ini_m"] == CRUAS[0]["ini_m"]
      and SUAVE[-1]["fim_m"] == CRUAS[-1]["fim_m"],
      (SUAVE[0]["ini_m"], SUAVE[-1]["fim_m"]))
check("⭐ o BURACO sobrevive — fatia só funde quando ENCOSTA no tempo",
      buracos(SUAVE) == buracos(CRUAS) == 1, (buracos(CRUAS), buracos(SUAVE)))
check("nenhuma faixa fica invertida (fim antes do início)",
      all(f["fim_m"] > f["ini_m"] for f in SUAVE))
check("as faixas saem em ordem cronológica",
      all(SUAVE[i - 1]["fim_m"] <= SUAVE[i]["ini_m"] + 0.01
          for i in range(1, len(SUAVE))))

print("\n[2] E o desenho fica legível")
check("⭐ o número de retângulos cai bastante",
      len(SUAVE) <= len(CRUAS) * 0.7, (len(CRUAS), len(SUAVE)))
_verdes = [f["fim_m"] - f["ini_m"] for f in SUAVE if f["cat"] == "va"]
check("o produtivo vira faixa longa em vez de confete",
      max(_verdes) >= 45, max(_verdes))
# O espelhamento é o que faz as pontas iguais se encontrarem. Sem ele, o verde
# fica sempre no começo do bloco e nunca alcança o vizinho.
check("o espelhamento em blocos alternados está no código",
      "const sinal = b % 2 === 0 ? 1 : -1;" in D2)
check("com o motivo escrito", "Ordem\n//     fixa sem espelhar não bastava" in D2
      or "fixa sem espelhar não bastava" in D2)

print("\n[3] O dia ÚNICO não é reordenado — ele já estava bom")
UM_DIA = [{"ini_m": 360.0, "fim_m": 375.0, "cat": "va"},
          {"ini_m": 375.0, "fim_m": 390.0, "cat": "va"},
          {"ini_m": 390.0, "fim_m": 405.0, "cat": "desp"}]
S1 = js(f"suavizarJornada({json.dumps(UM_DIA)}, false)")
check("no dia único a ordem é preservada",
      [f["cat"] for f in S1] == ["va", "desp"], S1)
check("mas as vizinhas iguais ainda fundem (também ajuda no dia único)",
      len(S1) == 2 and S1[0]["fim_m"] == 390.0, S1)
check("e a soma continua idêntica", soma(S1) == soma(UM_DIA))

print("\n[4] Casos de borda")
check("lista vazia não quebra", js("suavizarJornada([], true)") == [])
_uma = [{"ini_m": 360.0, "fim_m": 375.0, "cat": "va"}]
check("uma fatia só passa intacta", js(f"suavizarJornada({json.dumps(_uma)}, true)") == _uma)
_alt = [{"ini_m": 360.0, "fim_m": 361.0, "cat": "va"},
        {"ini_m": 361.0, "fim_m": 362.0, "cat": "desp"},
        {"ini_m": 362.0, "fim_m": 363.0, "cat": "va"}]
check("categorias alternadas dentro do bloco não somem",
      soma(js(f"suavizarJornada({json.dumps(_alt)}, true)")) == soma(_alt))

print("\n[5] O canto arredondado saiu de cada fatia")
check("só as PONTAS da banda são arredondadas",
      'i === 0 ? "6px 0 0 6px"' in D2 and 'i === faixas.length - 1 ? "0 6px 6px 0"' in D2)
check("com o motivo: num retângulo de 3 px o raio come a fatia",
      "o raio come a fatia" in D2)

print("\n[6] O clique continua abrindo o BLOCO real, não o reordenado")
# Se os alvos de clique viessem das faixas suavizadas, clicar abriria os
# eventos de outro horário — o reordenamento é só visual e não pode vazar
# para a navegação.
check("⭐ os alvos de clique usam as faixas CRUAS",
      "for (const f of faixasCruas) {" in D2)
check("e o desenho usa as suavizadas",
      "const faixas = suavizarJornada(faixasCruas, !!agregado);" in D2)

os.remove(_TS)
print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
