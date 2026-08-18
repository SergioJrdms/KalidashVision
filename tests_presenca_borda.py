"""Fase 108 — A BORDA APRENDE A TAREFA MAIS BÁSICA: tem gente no posto, e QUANDO.

O relato: *"Estamos acertando o monitorar máquina, operar máquina, conversando
com colega, etc. Mas errando MUITO o posto_vazio."*

⭐ O DIAGNÓSTICO QUE FALTAVA. Quando o backend diz "posto vazio", dois casos
completamente diferentes chegavam com a MESMA cara:

  (a) não havia ninguém — o backend acertou;
  (b) havia alguém no quadro, fora do polígono desenhado — o backend errou, e
      o problema é a ZONA, não o posto.

Só a borda pode separá-los, porque só ela tem os frames brutos. A nuvem recebe
o recorte e a zona; se a zona estiver errada, ela não tem como saber.

Agora a borda mede presença em TRÊS níveis, da MESMA inferência do YOLO que já
rodava (custo adicional: zero):

  quadro      — alguém em qualquer lugar da área enviada
  zona_larga  — qualquer parte do corpo dentro do polígono
  zona        — a ÂNCORA (topo do tronco) dentro do polígono = a regra da nuvem

E manda junto a LINHA DO TEMPO: em que segundos havia alguém e em quais não.

⚠️ A BORDA NÃO DECIDE NADA. Ela mede e envia; quem classifica é o backend. Uma
borda que carimbasse `posto_vazio` sozinha só mudaria o lugar do erro.

⚠️ E O DESCARTE FICOU MAIS CONSERVADOR: antes bastava ninguém NA ZONA para o
segmento ser apagado no Pi — um polígono mal desenhado apagava o turno inteiro
antes de qualquer olho humano ver o vídeo. Agora só se apaga o que está vazio
NO QUADRO.

Rodar:  python tests_presenca_borda.py
"""
import json, os, sys, types

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(RAIZ, "edge"))
for m in ["requests", "dotenv"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None

import numpy as np  # noqa: E402
import cv2  # noqa: E402
import edge_runner as er  # noqa: E402

ok = fail = 0
FONTE = open(os.path.join(RAIZ, "edge", "edge_runner.py"), encoding="utf-8").read()


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# Posto = quadrado à esquerda do alvo 1000×1000.
POLY = np.array([[100, 100], [400, 100], [400, 600], [100, 600]], dtype=np.int32)
ALVO = (1000, 1000, 3)


def pessoa(ombro_x, ombro_y, punho=None, bbox=None):
    """bbox + 17 keypoints normalizados. `punho` estica UM braço."""
    k = [[0.0, 0.0] for _ in range(17)]
    k[5] = [(ombro_x - 15) / 1000, ombro_y / 1000]
    k[6] = [(ombro_x + 15) / 1000, ombro_y / 1000]
    if punho:
        k[10] = [punho[0] / 1000, punho[1] / 1000]
    b = bbox or (ombro_x - 60, ombro_y - 80, ombro_x + 60, ombro_y + 300)
    return np.array(b, dtype=float), np.array(k, dtype=float)


def avalia(*pessoas):
    boxes = np.array([p[0] for p in pessoas])
    kpts = np.array([p[1] for p in pessoas])
    larga = er._toca_poligono(boxes, kpts, [POLY], ALVO)
    estrita = er._ancora_na_zona(boxes, kpts, [POLY], ALVO)
    return bool(larga.any()), bool(estrita.any())


# ══════════ [1] O caso que motivou tudo: gente fora da zona ═══════════
print("\n[1] ⭐ 'Posto vazio' quando havia gente — o caso do relato")

fora = pessoa(700, 300)
larga, estrita = avalia(fora)
check("a pessoa ao lado do posto não entra pela regra estrita", not estrita)
check("nem pela larga", not larga)
check("⭐ mas ela EXISTE no quadro — e é isso que a borda passa a dizer",
      "frac_quadro" in FONTE and '"vazio": f_quadro == 0.0' in FONTE)
check("⭐ e o caso ganha nome próprio: `fora_da_zona`",
      '"fora_da_zona": bool(f_quadro > 0.0 and f_zona == 0.0)' in FONTE)
check("com o log gritando que NÃO é posto vazio",
      "provável zona mal desenhada, não posto" in FONTE)

dentro = pessoa(250, 300)
check("o operador dentro do posto conta pelas duas regras",
      avalia(dentro) == (True, True))


# ══════════ [2] A régua estrita é a MESMA da nuvem ════════════════════
print("\n[2] A âncora: onde a pessoa ESTÁ, não até onde ela ALCANÇA")

braco = pessoa(700, 300, punho=(380, 300))
larga, estrita = avalia(braco)
check("⭐ braço esticado para dentro NÃO coloca a pessoa no posto", not estrita)
check("mas a régua larga acusa o toque", larga)
check("⭐ e a diferença entre as duas tem nome: `na_borda`",
      '"na_borda": bool(f_larga > 0.0 and f_zona < f_larga)' in FONTE)

ocluso = pessoa(250, 300)          # nenhum keypoint inferior válido
check("operador ocluso pela máquina (só ombros) continua reconhecido",
      avalia(ocluso)[1])
um_ombro = pessoa(250, 300)
um_ombro[1][5] = [0.0, 0.0]
check("com UM ombro só, idem",
      bool(er._ancora_na_zona(np.array([um_ombro[0]]), np.array([um_ombro[1]]),
                              [POLY], ALVO).any()))
so_nariz = pessoa(250, 300)
so_nariz[1][5] = [0.0, 0.0]; so_nariz[1][6] = [0.0, 0.0]
so_nariz[1][0] = [250 / 1000, 250 / 1000]
check("só com o nariz, idem",
      bool(er._ancora_na_zona(np.array([so_nariz[0]]), np.array([so_nariz[1]]),
                              [POLY], ALVO).any()))
sem_pose = (np.array([200, 150, 320, 560], dtype=float),
            np.array([[0.0, 0.0]] * 17))
check("SEM POSE, cai no topo-do-tronco do bbox e ainda funciona",
      bool(er._ancora_na_zona(np.array([sem_pose[0]]), np.array([sem_pose[1]]),
                              [POLY], ALVO).any()))
check("a régua estrita declara que é a mesma da nuvem",
      "a MESMA que a nuvem usa para decidir" in FONTE)


# ══════════ [3] A linha do tempo — 'quando' há e 'quando' não há ══════
print("\n[3] A linha do tempo: em que segundos havia alguém")

m = [True, True, False, False, False, True]
j = er._janelas_de(m, 6.0, 60)
check("⭐ intervalos COM pessoa, em segundos", j == [[0.0, 12.0], [30.0, 36.0]], j)
check("⭐ a maior ausência é o número que separa 'saiu' de 'sumiu'",
      er._maior_ausencia(m, 6.0) == 18.0, er._maior_ausencia(m, 6.0))
check("tudo presente vira uma janela só",
      er._janelas_de([True] * 4, 6.0, 60) == [[0.0, 24.0]])
check("e ausência zero", er._maior_ausencia([True] * 4, 6.0) == 0.0)
check("tudo ausente não gera janela nenhuma",
      er._janelas_de([False] * 4, 6.0, 60) == [])
check("e a ausência é o segmento inteiro",
      er._maior_ausencia([False] * 4, 6.0) == 24.0)
check("lista vazia não quebra",
      er._janelas_de([], 6.0, 60) == [] and er._maior_ausencia([], 6.0) == 0.0)
check("⭐ o teto de janelas trunca (payload não pode explodir)",
      len(er._janelas_de([True, False] * 200, 6.0, 60)) == 60)
check("e o truncamento é declarado, não silencioso",
      '"janelas_truncadas"' in FONTE)


# ══════════ [4] A borda MEDE, não decide ══════════════════════════════
print("\n[4] ⭐ A borda não classifica — ela entrega contexto")

check("nenhum rótulo do backend é escrito no edge",
      "posto_vazio" not in FONTE.replace("`posto_vazio`", ""))
check("o desenho diz isso em voz alta",
      "NÃO decide nada aqui" in FONTE
      and "quem classifica é o backend" in FONTE)
check("⭐ o descarte passou a exigir vazio NO QUADRO, não vazio na zona",
      'not perfil.get("vazio", False)' in FONTE
      and "ninguém no quadro" in FONTE)
check("com o motivo: zona errada apagava o turno na origem",
      "apagava o turno inteiro na origem" in FONTE)
check("o perfil é NÃO-FATAL: perder o contexto não custa o segmento",
      "Perder o contexto" in FONTE
      and "não pode custar o segmento" in FONTE)
check("e pode ser desligado se o Pi apertar",
      'PRESENCA_PERFIL   = _env("PRESENCA_PERFIL", "true")' in FONTE)
check("⭐ zero inferência a mais — sai do mesmo YOLO que já rodava",
      FONTE.count("yolo.predict(alvo, classes=[0], conf=PORTEIRO_CONF_MIN") == 2
      and "Três níveis, UMA inferência" in FONTE)


# ══════════ [5] O transporte: mandar hoje, ler depois ═════════════════
print("\n[5] O sinal viaja sem quebrar o backend de hoje")

check("vai como JSON no form-data do upload clássico",
      'data["presenca"] = json.dumps(presenca' in FONTE)
check("e no corpo da inbox do upload direto", 'reg["presenca"] = presenca' in FONTE)
check("⭐ com o motivo: campo desconhecido é IGNORADO pelos dois",
      "FastAPI descarta form-data que" in FONTE
      and "extra='ignore' é o padrão do Pydantic v2" in FONTE)
check("os dois caminhos de processamento mandam o perfil",
      FONTE.count("presenca=perfil") >= 2
      and 'presenca=c.get("presenca")' in FONTE)

# Sidecar: o backend ainda não lê, então a medição não pode se perder.
check("existe sidecar ao lado do segmento", "def _gravar_sidecar" in FONTE)
check("⭐ o nome do sidecar sobrevive às renomeações do fluxo",
      er._nome_sidecar("/x/seg_20260818_132000_cam1.mp4").name
      == er._nome_sidecar("/x/seg_20260818_132000_cam1.sel.mp4").name
      == er._nome_sidecar("/x/seg_20260818_132000_cam1_skip.mp4").name
      == "seg_20260818_132000_cam1.presenca.json",
      er._nome_sidecar("/x/seg_20260818_132000_cam1.sel.mp4").name)
check("⭐ e a retenção varre os sidecars (o vídeo some, eles ficariam)",
      'SEG_DIR.rglob("seg_*.presenca.json")' in FONTE)
check("gravar o sidecar nunca derruba o upload",
      "um disco cheio não\n    pode impedir o upload" in FONTE)


# ══════════ [6] A linha do tempo não pode desandar ════════════════════
print("\n[6] Uma marca por amostra — senão os segundos mentem")

check("⭐ amostra sem recorte válido também vira marca",
      "m_quadro.append(False); m_larga.append(False); m_zona.append(False)" in FONTE)
check("com o motivo escrito", "cada marca tem\n                    # de corresponder a um passo" in FONTE)
check("o passo é o do pontuador — não há um segundo passo a configurar",
      "PRESENCA_PASSO_S" not in FONTE and "passo_s = SELECAO_AMOSTRA_S" in FONTE)
check("sem zona desenhada, os três níveis coincidem (e isso é honesto)",
      "não há onde a pessoa \"não estar\"" in FONTE)
check("o score histórico da seleção top-K NÃO mudou",
      "valido = larga          # o score histórico não muda" in FONTE)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
