# ============================================================
# A NARRATIVA DO MINUTO — descrever primeiro, rotular depois.
#
# O DIAGNÓSTICO, encontrado no código: o VLM já descreve TODOS os quadros — o
# prompt pede uma entrada por imagem e ele devolve 12 num minuto. Mas
# `_abrir_evento` guarda `descricao_bruta` da PRIMEIRA observação do bloco
# dominante e NUNCA a atualiza conforme o bloco cresce (a acumulação mexe em
# tempo_fim, frame_fim, n_amostras, orientação, bbox e maos_maquina — não na
# descrição). As outras onze são descartadas.
#
# Efeito: o card de 180s→240s mostrava a frase de UM instante como se fosse o
# minuto inteiro. Não é "sempre o último frame" — é o primeiro do bloco
# vencedor —, mas o resultado é o mesmo.
#
# O QUE ESTA SUÍTE PROTEGE:
# [1] A narrativa ACOMPANHA, não substitui — as descrições por instante seguem
#     cortando o minuto quando a ação muda.
# [2] Nenhuma chamada de API nova.
# [3] ⭐ Ela não move o número.
# [4] Coluna ausente não derruba vídeo da campanha.
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
main = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()

NARR = ("O operador está de pé à esquerda do torno, de costas para a câmera. "
        "Nas duas primeiras imagens as mãos estão sobre a máquina; na terceira "
        "ele se afasta meio passo e vira o corpo para a bancada.")

print("\n[1] O prompt pede a narrativa da SEQUÊNCIA, não de um instante")
p = pl.PROMPT_VLM_SEQUENCIA
check("o campo `resumo` é pedido", '"resumo"' in p)
# ⭐ A narrativa é o PRODUTO, não um extra. Se o prompt a tratar como
# acessório, ela volta a sair em uma linha.
check("⭐ é declarada como o campo MAIS IMPORTANTE da resposta",
      "O CAMPO MAIS IMPORTANTE DESTA RESPOSTA" in p)
check("e as frases por imagem são declaradas como ÍNDICE, não como a observação",
      "índices curtos" in p and "observação de verdade" in p)
check("percorrendo as imagens EM ORDEM", "Percorra as imagens EM ORDEM" in p)
# O checklist é o que separa "descrição fiel" de "frase genérica".
for item in ("ONDE a pessoa está", "O QUE AS MÃOS FAZEM", "A POSTURA",
             "O QUE MUDOU", "OUTRAS PESSOAS", "OBJETOS"):
    check(f"o checklist cobre {item!r}", item in p)
check("e manda dizer quando NÃO vê as mãos, em vez de adivinhar",
      "não vê as mãos" in p and "não adivinhe" in p)
# ⭐ O pedido do dono, literal: "sem tentar definir ou concluir em 1 ação".
check("⭐ é PROIBIDO concluir numa ação só",
      "NÃO RESUMA e NÃO CONCLUA" in p and 'Não escolha "a" ação do trecho' in p)
check("e não precisa ser compacto — o outro pedido dele",
      "SEJA ESPECÍFICO, não econômico" in p
      and "Três a cinco frases é o normal" in p)
check("⭐ escrever pouco é declarado como o PIOR erro",
      "escrever pouco quando havia o que ver é o pior erro" in p)
check("e o exemplo mostra o nível de detalhe, não só o formato",
      "nível de detalhe esperado" in p and "carro transversal" in p)
check("permanecer igual É observação, não falta de informação",
      "Permanecer parado é um fato observado" in p
      and "tão boa quanto qualquer outra" in p)
check("sem rótulo, categoria nem estado da máquina na narrativa",
      "Nada de rótulo, categoria, produtividade, julgamento, estado da máquina" in p)
check("com exemplo do formato esperado", "recua meio passo" in p)

print("\n[2] Zero chamada nova — o campo entra no MESMO JSON")
check("`resumo` é irmão de `trechos` no mesmo objeto",
      '{{"resumo":' in p and '"trechos": [' in p)
check("e o parser guarda o objeto INTEIRO, não só os trechos",
      "bruto = json.loads(resposta) or {}" in fonte
      and "trechos = bruto.get(\"trechos\", [])" in fonte)
_f = fonte.split("def _resumo_da_sequencia")[1].split("\ndef ")[0]
for chamada in ("groq_vision_call", "groq_text_call", "anthropic", "requests."):
    check(f"a extração não chama {chamada}", chamada not in _f)

print("\n[3] A flag e o filtro de ruído")
# ⭐ LIGADA POR PADRÃO. Ficou atrás de flag desligada por uma versão e o
# resultado foi previsível: nada mudou na tela, porque a narrativa não existia.
check("⭐ ligada por padrão", pl._NARRATIVA is True)
check("a narrativa rica sai inteira", pl._resumo_da_sequencia({"resumo": NARR}) == NARR)
# ⭐ O filtro de ruído: uma linha não é narrativa, é a descrição antiga com
# outro nome. Melhor não exibir nada do que exibir um resumo que não resume.
check("⭐ frase de uma linha é REJEITADA",
      pl._resumo_da_sequencia(
          {"resumo": "operador parado junto ao torno, observando a máquina"}) is None)
check("resposta de uma palavra também", pl._resumo_da_sequencia({"resumo": "operando"}) is None)
check("campo ausente devolve None", pl._resumo_da_sequencia({}) is None)
check("tipo errado não quebra", pl._resumo_da_sequencia({"resumo": 42}) is None)
check("objeto nulo não quebra", pl._resumo_da_sequencia(None) is None)
_n = pl._NARRATIVA
pl._NARRATIVA = False
try:
    check("e dá para desligar, se precisar", pl._resumo_da_sequencia({"resumo": NARR}) is None)
finally:
    pl._NARRATIVA = _n

print("\n[4] ⭐ ACOMPANHA, não substitui")
# Se a narrativa substituísse as descrições por instante, o minuto viraria um
# bloco só e as transições REAIS dentro dele desapareceriam.
def _obs(t, desc, narr=NARR):
    return {"descricao": desc, "track_id": 1, "tempo_s": t, "frame_idx": int(t * 10),
            "zona": "posto_operador", "origem_gate": "analisado", "narrativa": narr}


evs = pl.etapa_segmentar_eventos(
    [_obs(0, "mãos no torno"), _obs(3, "mãos no torno"),
     _obs(6, "saindo do posto"), _obs(9, "saindo do posto")],
    lambda d, *a, **k: "operar_torno" if "torno" in d else "deslocar_pelo_posto", 3.0)
check("⭐ o minuto continua sendo CORTADO onde a ação muda", len(evs) == 2, len(evs))
check("cada trecho mantém a descrição do seu instante",
      evs[0]["descricao_bruta"] == "mãos no torno"
      and evs[1]["descricao_bruta"] == "saindo do posto")
check("e os dois carregam a MESMA narrativa — ela é do minuto, não do trecho",
      evs[0]["narrativa"] == evs[1]["narrativa"] == NARR)
check("sem narrativa, o evento nasce com None e nada quebra",
      pl.etapa_segmentar_eventos([_obs(0, "x", narr=None)],
                                 lambda *a, **k: "operar_torno", 3.0)[0]["narrativa"] is None)
check("a narrativa viaja igual em TODAS as observações do grupo",
      '"narrativa": cena_narrativa,' in fonte)
check("o motivo de acompanhar e não substituir está escrito",
      "ACOMPANHA, NÃO SUBSTITUI" in fonte and "apagaria transições que são REAIS" in fonte)

print("\n[5] ⭐ A narrativa NÃO move o número")
DIA = [
    {"tempo_inicio_s": 0, "tempo_fim_s": 60, "papel_pessoa": "operador",
     "comportamento_label": "operar_torno", "principal": True},
    {"tempo_inicio_s": 60, "tempo_fim_s": 120, "papel_pessoa": "posto_vazio",
     "comportamento_label": "posto_vazio", "principal": True},
]
antes = pl.permanencia_do_dia(DIA, None)
depois = pl.permanencia_do_dia(
    [dict(e, narrativa=NARR, descricao_bruta="qualquer coisa") for e in DIA], None)
check("⭐ permanência IDÊNTICA com e sem narrativa", antes == depois, (antes, depois))
check("e não é empate trivial — o número existe", antes["no_posto_pct"] == 50.0)
_perm = fonte.split("def permanencia_do_dia")[1].split("\ndef ")[0]
check("a função da permanência nem menciona narrativa", "narrativa" not in _perm)

print("\n[5b] O teto de tokens acompanha a narrativa")
# Narrativa cortada no meio é pior que curta: some justamente o fim da
# sequência, que é onde mora a mudança.
check("o teto cresceu e tem folga fixa para o resumo",
      "max_tokens=220 * max(1, n_cam1) + 400," in fonte)
check("com o motivo escrito", "narrativa cortada no meio é pior que uma curta" in fonte)

print("\n[6] Coluna ausente não derruba vídeo da campanha")
check("a gravação regrava o lote SEM o campo se a coluna não existir",
      '_l.pop("narrativa", None)' in fonte and "resp = sb.table(\"eventos\").insert(lote).execute()" in fonte)
check("mas só quando o erro É sobre a narrativa — o resto sobe",
      'if "narrativa" not in str(erro):' in fonte and "raise" in fonte)
check("com o porquê: o vídeo carrega presença e pose, que são o produto",
      "NÃO PODE DERRUBAR UM VÍDEO DA CAMPANHA" in fonte)
check("a LEITURA da fila também tolera a coluna ausente",
      'if "narrativa" not in str(_e):' in main
      and "seguindo sem ela (rode o schema.sql" in main)
check("e a coluna está no schema, para o dia em que for rodado",
      "alter table eventos add column if not exists narrativa text;" in
      open(os.path.join(RAIZ, "sql", "schema.sql"), encoding="utf-8").read())

print("\n[7] A tela mostra as DUAS, e a diferença entre elas")
val = open(os.path.join(RAIZ, "frontend", "src", "pages", "Validacao.tsx"),
           encoding="utf-8").read()
check("a narrativa aparece primeiro e em destaque", "{evento.narrativa}" in val)
check("a frase curta continua visível, como resumo",
      "resumido como" in val and "“{evento.descricao}”" in val)
check("sem narrativa, o card volta ao que era — nada quebra",
      "evento.narrativa ? (" in val)
check("o porquê está no código, para ninguém 'limpar' a duplicação",
      "é ela que vira rótulo" in val)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
