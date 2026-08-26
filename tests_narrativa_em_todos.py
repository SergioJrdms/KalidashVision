"""Fase 107 — A DESCRIÇÃO COMPLETA EM TODOS OS CARDS.

O relato: *"pq a descrição não aparece em todos? Esse segmento foi processado
após o deploy e tá sem a descrição, só alguns vieram com descrição e todos
precisam dela."*

⭐ A CAUSA: o campo `resumo` só era PEDIDO no prompt V9.

`PROMPT_VLM_SEQUENCIA_V8` — que é o caminho PADRÃO, porque
`KV_PRODUTIVIDADE_OPERADOR_V9` é fail-closed e nasce desligada — nunca pediu o
campo. Com ele ativo, `bruto.get("resumo")` era None SEMPRE e a descrição
completa simplesmente não existia.

Por isso ela aparecia em uns cards e não em outros: quem decidia não era a
qualidade da cena, era QUAL PROMPT tinha rodado naquele minuto. Duas
funcionalidades sem relação nenhuma — quem é o operador e o que se viu no posto
— presas na mesma chave por acidente.

Duas causas menores, somadas:
  · O gabarito JSON do V9 dizia "Duas ou três frases" enquanto a instrução
    acima pedia "Três a cinco". O gabarito é a última coisa que o modelo lê e
    ancora o tamanho — e abaixo de 120 caracteres o texto era descartado.
  · O filtro de 120 é tudo-ou-nada: quando o modelo devolvia pouco, o card
    ficava MUDO. Agora existe a narrativa MONTADA a partir das frases por
    instante, que o modelo já escreveu.

⚠️ A narrativa montada NÃO INVENTA NADA: são as mesmas frases do modelo,
enfileiradas na ordem do tempo, com repetições colapsadas. É a segunda opção
justamente porque sai menos fluida.

Rodar:  python tests_narrativa_em_todos.py
"""
import os, sys, types

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
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

ok = fail = 0
fonte = open(os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read()


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# ═══════════ [1] O DEFEITO: o V8 nunca pediu a narrativa ═══════════════
print("\n[1] ⭐ O prompt PADRÃO não pedia o campo — era esse o buraco")

V8, V9 = pl.PROMPT_VLM_SEQUENCIA_V8, pl.PROMPT_VLM_SEQUENCIA
check("⭐ o V8 (caminho PADRÃO) agora pede `resumo`", '"resumo"' in V8)
check("e o gabarito JSON dele tem o campo", '{{"resumo":' in V8)
check("o V9 continua pedindo", '{{"resumo":' in V9)

# Fase 110: são TRÊS agora — o terceiro é o prompt que descreve a atividade
# FORA do posto. O contrato é o mesmo: nenhum deles tem cópia própria do bloco.
check("⭐ o bloco de instruções é UM só, compartilhado por todos os prompts",
      "_BLOCO_RESUMO" in fonte
      and fonte.count("_BLOCO_RESUMO + ") == 3)
check("⭐ e por isso eles não podem mais divergir",
      "O CAMPO MAIS IMPORTANTE DESTA RESPOSTA" in V8
      and "O CAMPO MAIS IMPORTANTE DESTA RESPOSTA" in V9)
for item in ("ONDE a pessoa está", "O QUE AS MÃOS FAZEM", "O QUE MUDOU",
             "QUEM LÊ ISTO É UM DONO DE FÁBRICA", "NÚMERO DE IMAGEM"):
    check(f"o V8 herdou {item!r}", item in V8)
check("o motivo do defeito está escrito no código",
      "A NARRATIVA NÃO PERTENCE A UMA FLAG DE IDENTIFICAÇÃO" in fonte
      and "presas na mesma chave por acidente" in fonte)


# ═══════════ [2] O gabarito contradizia a instrução ════════════════════
print("\n[2] O gabarito JSON ancorava o modelo em texto curto")

check("⭐ 'Duas ou três frases' saiu dos dois",
      "Duas ou três frases" not in V8 and "Duas ou três frases" not in V9)
check("o gabarito pede TRÊS A CINCO, como a instrução",
      "TRÊS A CINCO FRASES" in V8 and "TRÊS A CINCO FRASES" in V9)
check("e declara que é o campo mais longo",
      V8.count("campo mais longo desta resposta") == 1)
check("a instrução acima continua coerente com ele",
      "Três a cinco frases é o normal" in V8)
check("o teto de tokens tem folga para o texto longo",
      "max_tokens=220 * max(1, n_cam1) + 650," in fonte)
check("com o motivo: JSON truncado perde o minuto inteiro",
      "perde o minuto inteiro, não só o resumo" in fonte)


# ═══════════ [3] A narrativa montada — a rede ══════════════════════════
print("\n[3] Resumo curto não deixa mais o card mudo")


def bruto(*acoes_por_instante, resumo=None):
    t = [{"i": i, "acoes": a} for i, a in enumerate(acoes_por_instante)]
    return {"resumo": resumo, "trechos": t}


b = bruto({"P1": "mãos no torno, ajustando a peça", "P2": "conversando ao lado"},
          {"P1": "mãos no torno, ajustando a peça", "P2": "conversando ao lado"},
          {"P1": "medindo a peça na bancada"})
m = pl._narrativa_dos_instantes(b)
check("monta a narrativa a partir dos instantes", isinstance(m, str) and len(m) > 60, m)
check("⭐ usa TEMPO, nunca número de imagem",
      "No começo do trecho" in m and "Até o fim do trecho" in m
      and "imagem" not in m.lower(), m)
check("⭐ 'P1' não sobra — vira 'o operador'", "P1" not in m and "operador" in m, m)
check("'P2' vira 'outra pessoa'", "P2" not in m and "utra pessoa" in m, m)
check("⭐ instantes IGUAIS seguidos viram um só (não cinco frases iguais)",
      m.count("ajustando a peça") == 1, m)
check("e o que se manteve é declarado como fato observado",
      "e segue assim" in m, m)
check("a ordem do tempo é respeitada",
      m.index("ajustando a peça") < m.index("medindo a peça"), m)
check("as frases são as MESMAS do modelo (nada foi reescrito)",
      "medindo a peça na bancada" in m, m)

check("instante fora de ordem é reordenado, não perdido",
      "ajustando" in (pl._narrativa_dos_instantes(
          {"trechos": [{"i": 1, "acoes": {"P1": "medindo a peça na bancada com o paquímetro"}},
                       {"i": 0, "acoes": {"P1": "mãos no torno, ajustando a peça devagar"}}]}) or ""))
_ord = pl._narrativa_dos_instantes(
    {"trechos": [{"i": 1, "acoes": {"P1": "medindo a peça na bancada com o paquímetro"}},
                 {"i": 0, "acoes": {"P1": "mãos no torno, ajustando a peça devagar"}}]})
check("e na ordem certa", _ord.index("ajustando") < _ord.index("medindo"), _ord)

# ⚠️ Sem matéria-prima, NÃO INVENTA.
check("⭐ sem trechos, devolve None — não fabrica texto",
      pl._narrativa_dos_instantes({"trechos": []}) is None)
check("⭐ trechos sem `acoes` também", pl._narrativa_dos_instantes(
    {"trechos": [{"i": 0}, {"i": 1, "acoes": {}}]}) is None)
check("ação vazia não vira frase", pl._narrativa_dos_instantes(
    {"trechos": [{"i": 0, "acoes": {"P1": "   "}}]}) is None)
check("bruto vazio não quebra", pl._narrativa_dos_instantes({}) is None)
check("um instante de duas palavras não vira narrativa",
      pl._narrativa_dos_instantes({"trechos": [{"i": 0, "acoes": {"P1": "parado"}}]}) is None)


# ═══════════ [4] Quem vence: o modelo, se resumir de verdade ═══════════
print("\n[4] A escolha entre a do modelo e a montada")

BOM = ("O operador está de pé à direita do torno, com o corpo voltado para a "
       "máquina. No começo do trecho ele mantém as duas mãos sobre o "
       "equipamento. Mais adiante ele se afasta meio passo e vira o tronco "
       "para a bancada, onde permanece até o fim.")
check("resumo bom do modelo VENCE a montagem",
      pl._resumo_da_sequencia(bruto({"P1": "mãos no torno"}, resumo=BOM)) == BOM)
r = pl._resumo_da_sequencia(bruto(
    {"P1": "mãos no torno, ajustando a peça"},
    {"P1": "medindo a peça na bancada com o paquímetro"},
    resumo="Ele opera o torno."))
check("⭐ resumo CURTO cai para a montagem em vez de sumir",
      isinstance(r, str) and "Ele opera o torno." not in r, r)
check("e a montagem carrega as duas ações, não uma",
      "ajustando a peça" in r and "medindo a peça" in r, r)
check("resumo AUSENTE também cai para a montagem",
      isinstance(pl._resumo_da_sequencia(bruto(
          {"P1": "mãos no torno, ajustando a peça devagar"},
          {"P1": "medindo a peça na bancada"})), str))
check("resumo não-texto não quebra",
      isinstance(pl._resumo_da_sequencia(
          {"resumo": 42, "trechos": [{"i": 0, "acoes": {"P1": "mãos no torno ajustando"}},
                                     {"i": 1, "acoes": {"P1": "medindo na bancada"}}]}), str))
check("⭐ sem nada, continua None — o banco não recebe string vazia",
      pl._resumo_da_sequencia({"resumo": "curto", "trechos": []}) is None)

_flag = pl._NARRATIVA
pl._NARRATIVA = False
try:
    check("⭐ KV_NARRATIVA=off desliga tudo, inclusive a montagem",
          pl._resumo_da_sequencia(bruto({"P1": "mãos no torno, ajustando a peça"},
                                        resumo=BOM)) is None)
finally:
    pl._NARRATIVA = _flag


# ═══════════ [5] A narrativa não depende da flag do operador ═══════════
print("\n[5] ⭐ Narrativa e identificação do operador ficam separadas")

_f = fonte.split("def _resumo_da_sequencia")[1].split("\ndef ")[0]
check("⭐ a escolha da narrativa não consulta a flag V9",
      "PRODUTIVIDADE_OPERADOR_V9" not in _f)
_g = fonte.split("def _narrativa_dos_instantes")[1].split("\ndef ")[0]
check("nem a montagem", "PRODUTIVIDADE_OPERADOR_V9" not in _g)
check("os dois prompts alimentam o MESMO extrator",
      fonte.count('"resumo": _resumo_da_sequencia(bruto),') == 2)
check("e a narrativa só olha a chave que leva o nome dela",
      "if not _NARRATIVA:" in _f)
check("a montagem é registrada no log (dá para medir quanto se usa)",
      "montada a partir de" in fonte)

# ═══════════ [6] A narrativa atravessa TODOS os tipos de observação ════
print("\n[6] ⭐ Posto vazio, inconclusivo e cam2 também recebem a narrativa")

# O segundo relato, com o print do banco: a maioria das linhas com `narrativa`
# NULA. O código repetia em três comentários que "a narrativa é do MINUTO, não
# do instante — por isso ela viaja igual em todas as observações do grupo", mas
# ela era LIDA dentro do ramo `tipo == "cam1"` e anexada só ali. Posto vazio,
# inconclusivo e cam2 (resgate/ponte) saíam sem ela — e são justamente as mais
# numerosas quando o posto esvazia.
_emissao = fonte.split("# ── 4) Emite as observações")[1].split("\ndef ")[0]
_vazio_helper = fonte.split("def _emitir_posto_vazio")[1].split("    ancoras:")[0]
check("⭐ a narrativa do grupo é calculada UMA vez, antes do laço",
      "narrativa_grupo = next(" in fonte
      and fonte.index("narrativa_grupo = next(")
      < fonte.index("# ── 4) Emite as observações"))
# Fase 110: cinco tipos de observação (entrou `fora_posto`). A regra não muda —
# TODA observação emitida carrega a narrativa do minuto.
check("⭐ TODAS as observações emitidas carregam a chave",
      _emissao.count("observacoes.append({") == 4
      and _emissao.count('"narrativa"') == 4
      and "observacoes.append(observacao)" in _vazio_helper
      and '"narrativa": narrativa' in _vazio_helper,
      (_emissao.count("observacoes.append({"), _emissao.count('"narrativa"')))
check("o posto vazio recebe — é o que o torna auditável",
      "O QUE TORNA O \"POSTO VAZIO\" AUDITÁVEL" in fonte)
check("o inconclusivo também", "O minuto foi observado mesmo quando ESTE instante não foi" in fonte)
check("e o instante INTERPOLADO cai no grupo em vez de perder a narrativa",
      '_bloco.get("resumo") or narrativa_grupo' in fonte)
check("⭐ minuto inteiro vazio continua SEM narrativa (nada foi observado)",
      "não houve nada para observar" in fonte
      and '"não pedimos"' in fonte)
check("⭐ orientacao/maos/trabalho seguem NULOS nessas observações, de propósito",
      "Preencher seria fabricar medida onde só há" in fonte)
check("com o motivo: não há pessoa, ou não há pose retida",
      "Sem pose retida na cam2 — orientação é ausência, não zero." in fonte)
check("o defeito está nomeado no código",
      "a narrativa não atravessando a fronteira" in fonte)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
