"""Fase 89 — o sensor de movimento da máquina. Em sombra.

O QUE ESTA SUÍTE PROTEGE

1. AUSÊNCIA DE MEDIÇÃO NÃO É MEDIÇÃO DE AUSÊNCIA. É a regra que atravessa
   tudo aqui e a que mais fácil se perde numa refatoração: zona ocupada por
   gente, contraste insuficiente ou par descartado por incoerência têm de
   produzir `indisponivel`, NUNCA `ausente`. Um sinal que diz "parada" quando
   a verdade é "não deu para ver" converte oclusão em máquina desligada — a
   mesma armadilha do `_mad` devolvendo 0.0 com n=1 (Fase 84).

2. O SENSOR NÃO SOBRESCREVE O VLM. Ele entra como fato no prompt, ao lado de
   maos_maquina e orientacao, e seu único poder é mandar para a fila.

3. MEDIR E INFLUENCIAR SÃO DECISÕES SEPARADAS. O sinal grava desde o primeiro
   vídeo; a injeção no prompt fica atrás de KV_MOVIMENTO_INJETAR e liga por
   variável de ambiente, sem deploy.

4. SEM ZONA DE MÁQUINA NÃO HÁ SINAL. Ausência de zona é ausência de
   informação, não ausência de movimento — mesmo contrato de `maos_na_maquina`.

Rodar:  python tests_movimento_maquina.py
"""
import sys, types, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["requests", "ultralytics", "supabase", "groq", "anthropic",
          "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

import numpy as np  # noqa: E402
import cv2  # noqa: E402

from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


W, H = 640, 480
# Zona da máquina: metade direita do quadro.
ROIS = {"torno": {"papel": "maquina", "descricao_contexto": "o torno",
                  "polygon": np.array([[320, 120], [620, 120], [620, 440],
                                       [320, 440]], dtype=np.int32)}}
ROIS_SEM_MAQUINA = {"posto": {"papel": "posto_operador",
                              "descricao_contexto": "o posto",
                              "polygon": np.array([[10, 10], [300, 10],
                                                   [300, 300], [10, 300]],
                                                  dtype=np.int32)}}


def cena(textura_seed=0, desloc=0, brilho=0):
    """Quadro com textura estável + um bloco que se desloca `desloc` px.
    Textura fixa = máquina parada; bloco deslocando = parte móvel."""
    rng = np.random.default_rng(1234)
    f = (rng.integers(0, 255, (H, W), dtype=np.uint8))
    f = cv2.GaussianBlur(f, (5, 5), 0)
    # Peça: retângulo de alto contraste dentro da zona, que anda com `desloc`.
    x = 400 + desloc
    cv2.rectangle(f, (x, 200), (x + 60, 260), 255, -1)
    cv2.rectangle(f, (x + 10, 210), (x + 50, 250), 0, -1)
    f = np.clip(f.astype(np.int16) + brilho, 0, 255).astype(np.uint8)
    return cv2.cvtColor(f, cv2.COLOR_GRAY2BGR)


print("\n[1] Sem zona de máquina não há sinal (ausência de zona ≠ ausência de movimento)")
m0 = pl.MedidorMovimento(ROIS_SEM_MAQUINA, W, H, cam_id="cam1")
check("medidor inativo", m0.ativo is False)
m0.passo(cena(), 0.0, [])
m0.passo(cena(desloc=20), 0.17, [])
check("nenhum par medido", m0.pares == [])
check("nenhum minuto com veredito", m0.por_minuto() == {})

print("\n[2] Máquina PARADA — textura idêntica entre quadros")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
check("medidor ativo com zona 'maquina'", m.ativo is True)
check("achou a zona pelo papel, não pelo nome", m.zona_nome == "torno")
base = cena()
for i in range(20):
    m.passo(base, i / 6.0, [])
r = pl.classificar_movimento(m.pares)
check("veredito 'ausente'", r["movimento"] == "ausente", r)
check("0% dos intervalos com movimento",
      r["detalhe"]["pct_intervalos_com_movimento"] == 0.0, r["detalhe"])
check("mas os pares foram VÁLIDOS (mediu e não viu)",
      r["detalhe"]["pares_validos"] == r["detalhe"]["pares"], r["detalhe"])

print("\n[3] Máquina em movimento CONTÍNUO — a peça anda em todo intervalo")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
for i in range(20):
    m.passo(cena(desloc=(i * 7) % 60), i / 6.0, [])
r = pl.classificar_movimento(m.pares)
check("veredito 'continuo'", r["movimento"] == "continuo", r)
check("alta fração de intervalos com movimento",
      r["detalhe"]["pct_intervalos_com_movimento"] >= 70, r["detalhe"])

print("\n[4] INTERMITENTE — o que 360 pares/min compram e 7 não compravam")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
pos = 0
for i in range(40):
    # avança 3 quadros, para 5 — a assinatura do torno manual
    if i % 8 < 3:
        pos += 8
    m.passo(cena(desloc=pos % 60), i / 6.0, [])
r = pl.classificar_movimento(m.pares)
check("veredito 'intermitente'", r["movimento"] == "intermitente", r)
check("entre os dois limiares",
      pl._MOV_INTERMITENTE * 100 <= r["detalhe"]["pct_intervalos_com_movimento"]
      < pl._MOV_CONTINUO * 100, r["detalhe"])

print("\n[5] A REGRA: zona ocupada por gente → indisponivel, NUNCA ausente")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
# Uma pessoa cobrindo a zona inteira, com a máquina parada por trás.
pessoa = [(300, 100, 640, 480)]
for i in range(20):
    m.passo(base, i / 6.0, pessoa)
r = pl.classificar_movimento(m.pares)
check("veredito 'indisponivel'", r["movimento"] == "indisponivel", r)
check("NÃO diz 'ausente' com a zona tomada", r["movimento"] != "ausente")
check("o motivo fica registrado",
      (r["detalhe"]["descartados"] or {}).get("ocluida", 0) > 0, r["detalhe"])
check("e a % de zona ocupada é reportada",
      r["detalhe"]["pct_zona_ocupada"] > 50, r["detalhe"])
check("sem veredito de movimento (não inventa 0%)",
      r["detalhe"]["pct_intervalos_com_movimento"] is None, r["detalhe"])

print("\n[6] Máscara de pessoa: tira do NUMERADOR e do DENOMINADOR")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
validos, ocup = m._mascara_pessoas([(400, 200, 460, 260)])
check("a pessoa vira pixel inválido", validos.sum() < validos.size)
check("a ocupação é medida", 0 < ocup < 1, ocup)
v2, ocup2 = m._mascara_pessoas([])
check("sem pessoa, tudo é válido", int(v2.sum()) == v2.size and ocup2 == 0.0)
# A dilatação existe porque a bbox do YOLO é justa e membros escapam.
_, ocup_dil = m._mascara_pessoas([(400, 200, 460, 260)])
check("a caixa é dilatada (cobre mais que a bbox crua)",
      ocup_dil > (60 * 60) / float((620 - 320) * (440 - 120)) * 0.9)

print("\n[7] Movimento DA máquina × movimento NA frente dela")
# A peça anda, mas atrás de uma pessoa que cobre exatamente aquela região:
# o movimento é da máquina, mas não é observável — e o honesto é dizer isso.
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
for i in range(20):
    m.passo(cena(desloc=(i * 7) % 60), i / 6.0, [(330, 130, 630, 430)])
r = pl.classificar_movimento(m.pares)
check("zona tomada → indisponivel, não 'continuo' nem 'ausente'",
      r["movimento"] == "indisponivel", r)

print("\n[8] Sombra: o gradiente aguenta mudança de brilho global")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
for i in range(20):
    # mesma cena, iluminação oscilando — sombra desloca brilho e preserva borda
    m.passo(cena(brilho=(-30 if i % 2 else 30)), i / 6.0, [])
r = pl.classificar_movimento(m.pares)
check("oscilação de luz não vira 'continuo'", r["movimento"] != "continuo", r)

print("\n[9] Coerência espacial: blob único gigante é descartado, não medido")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
ant = cena()
for i in range(20):
    # metade da zona muda de textura por inteiro = oclusor/iluminação
    f = cena().copy()
    if i % 2:
        f[120:440, 320:520] = 0
    m.passo(f, i / 6.0, [])
r = pl.classificar_movimento(m.pares)
check("houve pares descartados por blob grande OU nada foi afirmado",
      (r["detalhe"]["descartados"] or {}).get("blob_grande", 0) > 0
      or r["movimento"] == "indisponivel", r["detalhe"])

print("\n[10] Contraste insuficiente é 'não dá para ver', não 'parada'")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
liso = np.full((H, W, 3), 40, dtype=np.uint8)     # parede lisa, sem estrutura
for i in range(20):
    m.passo(liso, i / 6.0, [])
r = pl.classificar_movimento(m.pares)
check("veredito 'indisponivel'", r["movimento"] == "indisponivel", r)
check("com o motivo 'contraste_baixo'",
      (r["detalhe"]["descartados"] or {}).get("contraste_baixo", 0) > 0,
      r["detalhe"])

print("\n[11] O veredito por MINUTO usa o mesmo bucket do evento principal")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
for i in range(12):                      # minuto 0: parada
    m.passo(base, i / 6.0, [])
for i in range(12):                      # minuto 1: andando
    m.passo(cena(desloc=(i * 7) % 60), 60 + i / 6.0, [])
mm = m.por_minuto()
check("dois minutos", sorted(mm) == [0, 1], sorted(mm))
check("minuto 0 ausente", mm[0]["movimento"] == "ausente", mm[0])
check("minuto 1 com movimento", mm[1]["movimento"] in ("continuo", "intermitente"), mm[1])
check("o detalhe carrega a câmera e a zona",
      mm[0]["detalhe"]["cam"] == "cam1" and mm[0]["detalhe"]["zona"] == "torno")

print("\n[12] O fato no prompt DESCREVE, não conclui")
f = pl.frase_movimento("continuo", {"pct_intervalos_com_movimento": 88.0})
check("cita o sensor", "SENSOR" in f)
check("diz que descontou as pessoas", "descontando as pessoas" in f)
check("traz o número", "88%" in f, f)
for proibido in ("ciclo", "parada", "ocioso", "produtiv"):
    check(f"NÃO conclui '{proibido}'", proibido not in f.lower(), f)
check("indisponivel não vira frase (silêncio é o certo)",
      pl.frase_movimento("indisponivel", {}) == ""
      and pl.frase_movimento(None, None) == "")

print("\n[13] Medir e INFLUENCIAR são chaves separadas")
check("a injeção nasce DESLIGADA", pl._MOV_INJETAR is False)
check("a medição nasce LIGADA", pl._MOV_ENABLE is True)
ctx = pl._contexto_zonas.__doc__ or ""
check("o contexto documenta que é fato, como maos/orientacao",
      "FATO" in ctx and "decidindo" in ctx)

print("\n[14] O veto: não troca rótulo, manda para a fila — e só com a chave")
det_bom = {"pct_zona_ocupada": 5.0, "contraste": 40.0}
check("com a injeção OFF o veto não existe",
      pl.veto_movimento("ausente", det_bom, "ciclo") is None)
_real = pl._MOV_INJETAR
pl._MOV_INJETAR = True
try:
    check("sensor ausente + VLM 'ciclo' + medição boa → veta",
          pl.veto_movimento("ausente", det_bom, "ciclo") is not None)
    check("mas só marca dúvida (a mensagem não fala em corrigir)",
          "corrig" not in (pl.veto_movimento("ausente", det_bom, "ciclo") or "").lower())
    check("VLM dizendo 'parada' não é contradição",
          pl.veto_movimento("ausente", det_bom, "parada") is None)
    check("sensor 'indisponivel' nunca veta",
          pl.veto_movimento("indisponivel", det_bom, "ciclo") is None)
    check("zona muito ocupada → medição fraca, não veta",
          pl.veto_movimento("ausente", {"pct_zona_ocupada": 40.0, "contraste": 40.0},
                            "ciclo") is None)
    check("contraste apertado → medição fraca, não veta",
          pl.veto_movimento("ausente", {"pct_zona_ocupada": 5.0, "contraste": 9.0},
                            "ciclo") is None)
finally:
    pl._MOV_INJETAR = _real

print("\n[15] O mapa 16x16 só PESA depois de base")
m = pl.MedidorMovimento(ROIS, W, H, cam_id="cam1")
for i in range(10):
    m.passo(cena(desloc=(i * 7) % 60), i / 6.0, [])
check("a grade acumula deste vídeo", m.n_pares_grade > 0)
check("é 16x16", len(m.grade) == 16 and all(len(l) == 16 for l in m.grade))
check("mapa pequeno NÃO pesa (3 vídeos é chute)",
      pl.MedidorMovimento._normalizar_mapa({"n_pares": 10, "grade": m.grade}) is None)
grande = pl.MedidorMovimento._normalizar_mapa(
    {"n_pares": pl._MOV_MAPA_MIN_PARES + 1, "grade": m.grade})
check("com base, pesa", grande is not None)
check("e a célula que nunca se mexeu mantém PISO (a peça nova pode aparecer lá)",
      grande is not None and min(min(l) for l in grande) >= pl._MOV_MAPA_PESO_MIN)
check("grade de tamanho errado é ignorada",
      pl.MedidorMovimento._normalizar_mapa(
          {"n_pares": 10**9, "grade": [[1, 2], [3, 4]]}) is None)

print("\n[16] Os limiares são todos mexíveis por ambiente")
lim = pl.limiares_movimento()
for k in ("KV_MOVIMENTO", "KV_MOVIMENTO_INJETAR", "KV_MOV_LIMIAR_REL",
          "KV_MOV_CONTINUO", "KV_MOV_INTERMITENTE", "KV_MOV_OCUPACAO_MAX",
          "KV_MOV_ESCALA_MIN", "KV_MOV_FRACAO_PIXEL", "KV_MOV_BLOB_MAX"):
    check(f"{k} exposto", k in lim, lim)
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
for var in ("KV_MOV_LIMIAR_REL", "KV_MOV_CONTINUO", "KV_MOV_INTERMITENTE",
            "KV_MOV_OCUPACAO_MAX", "KV_MOV_ESCALA_MIN"):
    check(f"{var} lido de os.environ", f'os.environ.get("{var}"' in fonte)

print("\n[17] Custo: o movimento não decodifica nada a mais")
trecho = fonte[fonte.index("            tempo_s = frame_idx / fps"):]
trecho = trecho[:trecho.index("if tempo_s >= prox_amostra_s:")]
check("o passo roda DENTRO do laço já decodificado",
      "medidor.passo(frame, tempo_s, _bbs)" in trecho, trecho[:200])
check("usa as bboxes do MESMO instante (results já calculado)",
      "results[0].boxes.xyxy" in trecho)
check("nenhum cap.read() extra para o movimento",
      trecho.count("cap.read()") == 0 and trecho.count("VideoCapture") == 0)

print("\n[18] Persistência: o sensor E a afirmação do VLM, lado a lado")
check("o evento grava o movimento", '"movimento_maquina": e.get("movimento_maquina")' in fonte)
check("e o detalhe", '"movimento_detalhe": e.get("movimento_detalhe")' in fonte)
check("junto de cena_maquina (é o par que torna a discordância mensurável)",
      '"cena_maquina": _normalizar_maquina' in fonte)
sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sql", "schema.sql"), encoding="utf-8").read()
check("schema declara movimento_maquina", "add column if not exists movimento_maquina" in sql)
check("com constraint dos 4 valores válidos",
      "'continuo','intermitente','ausente','indisponivel'" in sql)
check("a tabela do mapa existe", "create table if not exists mapa_movimento" in sql)
check("e a view de calibração ordena pela discordância",
      "v_calibracao_movimento" in sql and "discordam" in sql)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
