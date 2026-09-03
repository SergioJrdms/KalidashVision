"""Fase 111D — autoridade local da identidade lógica sobre a Fase 110.

Teste comportamental sem vídeo/modelo reais: imagem, VLM e configuração são
substituídos apenas nas fronteiras; a reatribuição e o downstream executados
são os de produção.
"""
from copy import deepcopy
import importlib
import os
from pathlib import Path
import sys
import types


RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
for nome in [
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(nome, types.ModuleType(nome))
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
from backend import productivity as prod  # noqa: E402


ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def pessoa(tid, papel=None, *, bbox=(10, 10, 110, 310), rotulo=None):
    return {
        "track_id": int(tid),
        "bbox": tuple(bbox),
        "centro": ((bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2),
        "kpts": None,
        "papel": papel,
        "rotulo": rotulo or f"P{tid}",
        "zona": "posto",
        "zona_desc": "posto do torno",
        "maos_maquina": False,
        "orientacao": None,
    }


def amostra(tempo, pessoas=(), *, presente=None):
    return pl.Amostra(
        frame_idx=int(float(tempo) * 10),
        tempo_s=float(tempo),
        img_b64="IMG-CAM1" if pessoas else "",
        pessoas=list(pessoas),
        dim=(640, 480),
        operador_presente=presente,
    )


def marcar_estado_c6(am, tid=9):
    pessoa_fora = {
        "track_id": int(tid),
        "bbox": (10, 10, 110, 310),
        "kpts": None,
        "rotulo": "P1",
        "_fora_motivo": "operador",
        "_fora_amostras_zona": 7,
    }
    am.fora_posto = [pessoa_fora]
    am.img_b64_fora = "IMG-C6"
    am.fora_candidatos = [pessoa_fora]
    am.img_b64_fora_candidato = "JPEG-C6"
    am.operador_fora_estado = True
    am.operador_fora_proveniencia = "continuidade_track"
    am.operador_fora_track_id = int(tid)
    am.operador_fora_episodio = 3
    am.operador_fora_migracao = (4, int(tid))
    return am


def c6_limpo(am):
    return (
        am.operador_fora_estado is False
        and am.operador_fora_proveniencia is None
        and am.operador_fora_track_id is None
        and am.operador_fora_episodio is None
        and am.operador_fora_migracao is None
        and am.fora_candidatos == []
        and am.img_b64_fora_candidato is None
    )


def detalhe(tid, estado, *, bbox=(10, 10, 110, 310)):
    return {
        "track_id": int(tid), "bbox": tuple(bbox), "kpts": None,
        "estado": estado,
    }


def observacao(tempo, tracks, *, cam="cam1", detalhes=None,
               medido=True, frame=None):
    if frame is None and any(estado == "fora" for estado in tracks.values()):
        frame = "JPEG-SLOT"
    return {
        "cam_id": cam,
        "tempo_s": float(tempo),
        "medido": medido,
        "tracks": dict(tracks),
        "pessoas": detalhes or {},
        "dim": (640, 480),
        "frame_b64": frame,
    }


def resultado_confirmado(tracks=(4, 9), *, cam="cam1", n_posto=23):
    identidade = {
        "identidade_logica": "R1",
        "cam_id": cam,
        "track_ids": list(tracks),
        "pessoa_track_id": min(tracks),
        "n_amostras_posto": n_posto,
        "tempo_posto_s": float(n_posto * 5),
    }
    return {
        "cam_id": cam,
        "identidades": [identidade],
        "decisao": {
            "status": "confirmado", "motivo": "dominante_claro",
            "identidade_logica": "R1", "track_ids": list(tracks),
            "track_id": min(tracks),
        },
        "timeline": {"status": "disponivel", "cam_id": cam},
    }


def resultado_indefinido(*, cam="cam1", motivo="dominancia_ambigua"):
    return {
        "cam_id": cam,
        "identidades": [],
        "decisao": {"status": "indefinido", "motivo": motivo,
                    "identidade_logica": None, "track_ids": []},
        "timeline": {"status": "nao_gerada", "cam_id": cam},
    }


def aplicar(amostras, resultados, observacoes):
    return pl.aplicar_identidade_logica_segmento(
        amostras, resultados, {"observacoes": observacoes}, "cam1"
    )


def analisar(amostras, *, seq=None, fora=None):
    antigos = {
        "_analisar_sequencia_vlm": pl._analisar_sequencia_vlm,
        "_analisar_sequencia_fora": pl._analisar_sequencia_fora,
        "_FORA_MODO": pl._FORA_MODO,
        "_POSTO_VAZIO_ENABLE": pl._POSTO_VAZIO_ENABLE,
        "PRODUTIVIDADE_OPERADOR_V9": pl.PRODUTIVIDADE_OPERADOR_V9,
        "PRODUTIVIDADE_OPERADOR_ESTRUTURADA": (
            pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA
        ),
    }
    try:
        pl._FORA_MODO = "on"
        pl._POSTO_VAZIO_ENABLE = True
        pl.PRODUTIVIDADE_OPERADOR_V9 = True
        pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA = True
        if seq is not None:
            pl._analisar_sequencia_vlm = seq
        if fora is not None:
            pl._analisar_sequencia_fora = fora
        return pl.etapa_analise_vlm(
            object(), amostras, "torneamento", {}, lambda *_a, **_k: None,
            zona_posto="posto", intervalo_s=5.0,
        )
    finally:
        for nome, valor in antigos.items():
            setattr(pl, nome, valor)


def seq_adversarial(track_errado=12, trabalho=True):
    def _fake(_cli, grupo, *_a, **_k):
        return {
            i: {
                "acoes": {
                    p["track_id"]: f"pessoa {p['track_id']} em atividade"
                    for p in am.pessoas
                },
                "operador_estado": "identificado",
                "operador_track_id": track_errado,
                "trabalho": trabalho,
                "produtividade_motivo": "maos_no_torno",
                "interlocutor_evidencia": None,
                "maquina": None, "imovel": None,
            }
            for i, am in enumerate(grupo)
        }
    return _fake


autoridade_original = pl.AUTORIDADE_111D_CONFIGURADA
modo_original = pl._OPERADOR_SEGMENTO_MODO
imagem_original = pl._imagem_fora_identidade
decode_original = pl._frame_b64_para_bgr


class FrameIdentidadeFake:
    shape = (240, 320, 3)


try:
    print("[1-5] Modos e três chaves")
    reid = pl._TRACKER_REID
    fixa = pl._TRACKER_FIXA
    check("C1 off nunca satisfaz a autoridade",
          not pl.operador_segmento_autoridade_configurada(
              "off", "reid", "on", reid))
    check("C2 sombra continua somente diagnóstica",
          not pl.operador_segmento_autoridade_configurada(
              "sombra", "reid", "on", reid))
    check("C3 on sem Re-ID cai no legado",
          not pl.operador_segmento_autoridade_configurada(
              "on", "fixa", "on", fixa))
    check("C4 on sem Fase 110 cai no legado",
          not pl.operador_segmento_autoridade_configurada(
              "on", "reid", "off", reid))
    check("C5 somente as três chaves completas ativam 111D",
          pl.operador_segmento_autoridade_configurada(
              "on", "reid", "on", reid)
          and pl.operador_segmento_autoridade_configurada(
              "on", "fixa_reid", "on", reid))
    check("identity_only exige e aceita as mesmas três chaves",
          pl.operador_segmento_autoridade_configurada(
              "identity_only", "reid", "on", reid)
          and not pl.operador_segmento_autoridade_configurada(
              "identity_only", "reid", "off", reid))
    check("valor inválido permanece fail-closed",
          not pl.operador_segmento_autoridade_configurada(
              "talvez", "reid", "on", reid))

    legado = amostra(0, [pessoa(12, "operador")], presente=True)
    legado_antes = deepcopy(legado)
    pl.AUTORIDADE_111D_CONFIGURADA = False
    r_off = aplicar(
        [legado], [resultado_confirmado((9,))],
        [observacao(0, {9: "dentro"})],
    )
    check("off/sombra/incompleto não mutam sequer a Amostra",
          legado == legado_antes and r_off["status"] == "fallback_legado",
          (legado, r_off))

    pl.AUTORIDADE_111D_CONFIGURADA = True
    pl._imagem_fora_identidade = lambda *_a, **_k: "IMG-FORA"
    pl._frame_b64_para_bgr = lambda *_a, **_k: FrameIdentidadeFake()

    print("\n[5] Operador dentro e VLM adversarial")
    a5 = amostra(0, [
        pessoa(9, "visitante", rotulo="P1"),
        pessoa(12, "operador", bbox=(200, 10, 300, 310), rotulo="P2"),
    ], presente=True)
    r5 = aplicar(
        [a5], [resultado_confirmado((4, 9))],
        [observacao(0, {9: "dentro", 12: "dentro"})],
    )
    papeis5 = {p["track_id"]: p["papel"] for p in a5.pessoas}
    check("C5 R1 dentro substitui a escolha causal antiga",
          r5["status"] == "aplicado" and papeis5 == {9: "operador", 12: "visitante"}
          and a5.operador_presente is True and a5.identidade_autoritativa,
          (r5, papeis5))
    obs5 = analisar([a5], seq=seq_adversarial(12, True))
    por_tid5 = {o["track_id"]: o for o in obs5}
    check("VLM não promove o não vencedor nem transfere seu trabalho",
          por_tid5[9]["papel"] == "operador"
          and por_tid5[9].get("trabalho") is None
          and por_tid5[12]["papel"] == "visitante"
          and por_tid5[12].get("trabalho") is None,
          obs5)
    obs5_correta = analisar([a5], seq=seq_adversarial(9, True))
    por_tid5_correta = {o["track_id"]: o for o in obs5_correta}
    check("VLM que respeita a identidade fixa mede o trabalho de R1",
          por_tid5_correta[9]["papel"] == "operador"
          and por_tid5_correta[9].get("trabalho") is True
          and por_tid5_correta[12]["papel"] == "visitante"
          and por_tid5_correta[12].get("trabalho") is None,
          obs5_correta)

    print("\n[6-7] Janela completa vence a causalidade do track físico")
    a6_fora = amostra(0, [], presente=False)
    a6_dentro = amostra(5, [pessoa(9, "operador")], presente=True)
    r6 = aplicar(
        [a6_fora, a6_dentro], [resultado_confirmado((4, 9), n_posto=17)],
        [
            observacao(0, {4: "fora"}, detalhes={4: detalhe(4, "fora")}),
            observacao(5, {9: "dentro"}),
        ],
    )
    check("C6 fora antes de entrar é reatribuído retroativamente",
          r6["reatribuicoes_fora"] == 1
          and a6_fora.identidade_estado == "fora"
          and a6_fora.fora_posto[0]["track_id"] == 4
          and a6_dentro.pessoas[0]["papel"] == "operador",
          (r6, a6_fora, a6_dentro))
    check("fora_amostras_zona usa a evidência agregada de R1",
          a6_fora.fora_posto[0]["_fora_amostras_zona"] == 17,
          a6_fora.fora_posto)

    a7_dentro = amostra(0, [pessoa(4, "operador")], presente=True)
    a7_fora = amostra(5, [], presente=False)
    r7 = aplicar(
        [a7_dentro, a7_fora], [resultado_confirmado((4, 9), n_posto=19)],
        [
            observacao(0, {4: "dentro"}),
            observacao(5, {9: "fora"}, detalhes={9: detalhe(9, "fora")}),
        ],
    )
    check("C7 troca de ID permite track novo fora sem presença física própria",
          r7["status"] == "aplicado"
          and a7_fora.fora_posto[0]["track_id"] == 9
          and a7_fora.fora_posto[0]["_fora_amostras_zona"] == 19,
          (r7, a7_fora.fora_posto))

    print("\n[8-9] Outro ocupante não toma o posto")
    a8 = amostra(0, [pessoa(12, "operador")], presente=True)
    r8 = aplicar(
        [a8], [resultado_confirmado((4, 9))],
        [observacao(
            0, {9: "fora", 12: "dentro"},
            detalhes={9: detalhe(9, "fora"), 12: detalhe(12, "dentro")},
        )],
    )
    check("C8 operador fora tem precedência sobre outro dentro",
          r8["reatribuicoes_fora"] == 1
          and a8.fora_posto[0]["track_id"] == 9
          and a8.pessoas[0]["papel"] == "visitante"
          and a8.operador_presente is False,
          (r8, a8))
    obs8 = analisar(
        [a8], fora=lambda _cli, grupo, *_a, **_k: {
            i: {"acao": "conversando fora", "resumo": "fora do posto"}
            for i, _am in enumerate(grupo)
        },
    )
    por_tid8 = {o["track_id"]: o for o in obs8}
    check("C8/C14 downstream preserva operador_fora e visitante",
          len(obs8) == 2
          and por_tid8[9]["papel"] == pl.PAPEL_OPERADOR_FORA
          and por_tid8[12]["papel"] == "visitante"
          and por_tid8[12].get("trabalho") is None
          and not any(o.get("papel") == "posto_vazio" for o in obs8)
          and not any(o.get("track_id") == 12 and o.get("papel") == "operador"
                      for o in obs8),
          obs8)

    a9 = amostra(0, [pessoa(12, "operador")], presente=True)
    r9 = aplicar(
        [a9], [resultado_confirmado((4, 9))],
        [observacao(0, {12: "dentro"}, detalhes={12: detalhe(12, "dentro")})],
    )
    obs9 = analisar([a9], seq=seq_adversarial(12, True))
    check("C9 operador ausente mantém o ocupante como visitante",
          r9["reatribuicoes_ausente"] == 1
          and a9.pessoas[0]["papel"] == "visitante"
          and len(obs9) == 1 and obs9[0]["papel"] == "visitante"
          and obs9[0].get("trabalho") is None,
          (r9, obs9))
    check("visitante representa operator_absent no contrato existente",
          prod.classificar_observacao({"papel_pessoa": "visitante"})[0]
          == prod.EST_OPERADOR_AUSENTE)

    print("\n[10-11] Fallback integral e por slot")
    a10 = amostra(0, [pessoa(12, "operador")], presente=True)
    antes10 = deepcopy(a10)
    r10 = aplicar(
        [a10], [resultado_indefinido()],
        [observacao(0, {12: "dentro"})],
    )
    check("C10 identidade indefinida preserva o legado",
          r10["status"] == "fallback_legado" and a10 == antes10,
          (r10, a10))
    sem_timeline = resultado_confirmado((9,))
    sem_timeline["timeline"] = {"status": "indisponivel"}
    a10b = amostra(0, [pessoa(12, "operador")], presente=True)
    antes10b = deepcopy(a10b)
    r10b = aplicar([a10b], [sem_timeline], [observacao(0, {9: "dentro"})])
    check("timeline indisponível também é fallback sem mutação",
          r10b["motivo"] == "timeline_indisponivel" and a10b == antes10b,
          (r10b, a10b))

    a10c1 = amostra(0, [pessoa(4, "visitante")], presente=False)
    a10c2 = amostra(5, [], presente=False)
    antes10c = deepcopy([a10c1, a10c2])
    pl._frame_b64_para_bgr = lambda *_a, **_k: None
    r10c = aplicar(
        [a10c1, a10c2], [resultado_confirmado((4, 9))],
        [
            observacao(0, {4: "dentro"}),
            observacao(5, {9: "fora"}, detalhes={9: detalhe(9, "fora")}),
        ],
    )
    check("erro interno prepara tudo antes e não deixa mutação parcial",
          r10c["motivo"] == "erro_preparacao"
          and [a10c1, a10c2] == antes10c,
          (r10c, a10c1, a10c2))
    pl._frame_b64_para_bgr = lambda *_a, **_k: FrameIdentidadeFake()

    a11_conflito = amostra(0, [pessoa(4, "operador"), pessoa(9, "visitante")], presente=True)
    a11_valida = amostra(5, [pessoa(9, "visitante")], presente=False)
    conflito_antes = deepcopy(a11_conflito)
    r11 = aplicar(
        [a11_conflito, a11_valida], [resultado_confirmado((4, 9))],
        [
            observacao(0, {4: "dentro", 9: "fora"}),
            observacao(5, {9: "dentro"}),
        ],
    )
    check("C11 conflito faz fallback só no slot ambíguo",
          r11["slots_fallback"] == 1
          and a11_conflito == conflito_antes
          and a11_valida.pessoas[0]["papel"] == "operador",
          (r11, a11_conflito, a11_valida))

    print("\n[12-14] Não vencedor, vazio e fora visível")
    a12 = [
        amostra(t, [pessoa(12, "operador")], presente=True)
        for t in (0, 5, 10)
    ]
    r12 = aplicar(
        a12, [resultado_confirmado((4, 9))],
        [observacao(t, {12: "dentro"}) for t in (0, 5, 10)],
    )
    check("C12 não vencedor nunca é promovido em vários slots",
          r12["reatribuicoes_ausente"] == 3
          and all(am.pessoas[0]["papel"] == "visitante" for am in a12),
          (r12, a12))

    a13 = amostra(0, [], presente=True)
    a13.operador_ponte = True
    r13 = aplicar(
        [a13], [resultado_confirmado((4, 9))],
        [observacao(0, {})],
    )
    obs13 = analisar([a13])
    check("C13 R1 invisível e ninguém relevante preserva posto_vazio",
          r13["reatribuicoes_ausente"] == 1
          and a13.operador_ponte is False
          and len(obs13) == 1 and obs13[0]["papel"] == "posto_vazio",
          (r13, obs13))
    check("C14 operador visível fora nunca vira vazio quando VLM responde",
          obs8[0]["papel"] == pl.PAPEL_OPERADOR_FORA
          and obs8[0]["track_id"] == 9,
          obs8)

    print("\n[15] Imagem somente em memória; zero segunda inferência")
    chamadas15 = {"decode": 0, "anotar": 0, "encode": 0,
                  "video": 0, "yolo": 0, "vlm": 0}
    class FrameFake:
        shape = (240, 320, 3)
    antigos15 = {
        "_frame_b64_para_bgr": pl._frame_b64_para_bgr,
        "anotar_frame_com_ids": pl.anotar_frame_com_ids,
        "frame_para_base64": pl.frame_para_base64,
        "_analisar_sequencia_vlm": pl._analisar_sequencia_vlm,
        "_analisar_sequencia_fora": pl._analisar_sequencia_fora,
    }
    video_capture_antigo = getattr(pl.cv2, "VideoCapture", None)
    yolo_antigo = getattr(pl, "YOLO", None)
    try:
        def decode15(valor):
            chamadas15["decode"] += 1
            check("JPEG temporário correto chega ao decode em memória",
                  valor == "JPEG-SLOT", valor)
            return FrameFake()
        def anotar15(frame, pessoas):
            chamadas15["anotar"] += 1
            check("imagem é anotada uma vez e somente com o vencedor",
                  len(pessoas) == 1 and pessoas[0]["rotulo"] == "OP", pessoas)
            return frame
        def encode15(*_a, **_k):
            chamadas15["encode"] += 1
            return "IMG-FORA-MEMORIA"
        def proibido15(tipo):
            def _f(*_a, **_k):
                chamadas15[tipo] += 1
                raise AssertionError(f"chamada proibida: {tipo}")
            return _f
        pl._frame_b64_para_bgr = decode15
        pl.anotar_frame_com_ids = anotar15
        pl.frame_para_base64 = encode15
        pl.cv2.VideoCapture = proibido15("video")
        pl.YOLO = proibido15("yolo")
        pl._analisar_sequencia_vlm = proibido15("vlm")
        pl._analisar_sequencia_fora = proibido15("vlm")
        pl._imagem_fora_identidade = imagem_original
        a15 = amostra(0, [], presente=False)
        slot15 = observacao(
            0, {9: "fora", 12: "dentro"},
            detalhes={9: detalhe(9, "fora"), 12: detalhe(12, "dentro")},
        )
        r15 = aplicar([a15], [resultado_confirmado((9,))], [slot15])
        imagens_no_slot = [
            valor for valor in (
                slot15.get("frame_b64"), a15.img_b64, a15.img_b64_fora,
            ) if valor
        ]
        check("C15 retém uma única imagem comprimida por slot",
              imagens_no_slot == ["IMG-FORA-MEMORIA"], imagens_no_slot)
        check("C15 reconstrói em memória sem vídeo, YOLO, Re-ID ou VLM",
              r15["reatribuicoes_fora"] == 1
              and a15.img_b64_fora == "IMG-FORA-MEMORIA"
              and chamadas15 == {"decode": 2, "anotar": 1, "encode": 1,
                                 "video": 0, "yolo": 0, "vlm": 0},
              (r15, chamadas15))
    finally:
        for nome, valor in antigos15.items():
            setattr(pl, nome, valor)
        if video_capture_antigo is None:
            try:
                delattr(pl.cv2, "VideoCapture")
            except AttributeError:
                pass
        else:
            pl.cv2.VideoCapture = video_capture_antigo
        if yolo_antigo is None:
            try:
                delattr(pl, "YOLO")
            except AttributeError:
                pass
        else:
            pl.YOLO = yolo_antigo
        pl._imagem_fora_identidade = lambda *_a, **_k: "IMG-FORA"

    print("\n[16-17] Falha do VLM e contrato Lean/humano")
    a16 = amostra(0, [], presente=False)
    aplicar(
        [a16], [resultado_confirmado((9,), n_posto=14)],
        [observacao(0, {9: "fora"}, detalhes={9: detalhe(9, "fora")})],
    )
    obs16 = analisar([a16], fora=lambda *_a, **_k: {})
    check("C16 falha VLM preserva fallback canônico e auditoria",
          len(obs16) == 1 and obs16[0]["papel"] == "posto_vazio"
          and obs16[0].get("fora_do_posto") == "falha_vlm"
          and obs16[0].get("fora_amostras_zona") == 14,
          obs16)
    check("C17 operador_fora automático não recebe produtividade",
          obs8[0].get("trabalho") is None
          and all(prod.classificar_observacao({
              "papel_pessoa": "operador_fora",
              "categoria_lean": "valor_agregado",
              "categoria_lean_origem": origem,
          })[0] == prod.EST_OPERADOR_FORA
                  for origem in (None, "ia", "herdado", "aprendido")))
    check("C17 somente humano_rotulo mantém a decisão humana existente",
          prod.classificar_observacao({
              "papel_pessoa": "operador_fora",
              "categoria_lean": "valor_agregado",
              "categoria_lean_origem": "humano_rotulo",
          })[0] == prod.EST_OPERADOR_FORA_PRODUTIVO)

    print("\n[C6→111D] Reconciliação antes do VLM")
    c6_dentro = marcar_estado_c6(
        amostra(21, [pessoa(9, "visitante")], presente=False), 9,
    )
    r_c6_dentro = aplicar(
        [c6_dentro], [resultado_confirmado((9,))],
        [observacao(21, {9: "dentro"})],
    )
    obs_c6_dentro = analisar([c6_dentro], seq=seq_adversarial(9, False))
    check("C6 fora → 111D dentro limpa C6 e chega ao VLM como operador",
          r_c6_dentro["reatribuicoes_dentro"] == 1
          and c6_limpo(c6_dentro)
          and c6_dentro.fora_posto == []
          and len(obs_c6_dentro) == 1
          and obs_c6_dentro[0]["papel"] == "operador",
          (r_c6_dentro, c6_dentro, obs_c6_dentro))

    c6_ausente = marcar_estado_c6(amostra(22, [], presente=False), 9)
    r_c6_ausente = aplicar(
        [c6_ausente], [resultado_confirmado((9,))],
        [observacao(22, {})],
    )
    obs_c6_ausente = analisar([c6_ausente])
    check("C6 fora → 111D ausente limpa C6 e não vaza operador_fora",
          r_c6_ausente["reatribuicoes_ausente"] == 1
          and c6_limpo(c6_ausente)
          and c6_ausente.fora_posto == []
          and len(obs_c6_ausente) == 1
          and obs_c6_ausente[0]["papel"] == "posto_vazio",
          (r_c6_ausente, c6_ausente, obs_c6_ausente))

    c6_fora = marcar_estado_c6(amostra(23, [], presente=False), 4)
    r_c6_fora = aplicar(
        [c6_fora], [resultado_confirmado((9,))],
        [observacao(
            23, {9: "fora"}, detalhes={9: detalhe(9, "fora")},
        )],
    )
    obs_c6_fora = analisar([c6_fora], fora=lambda *_a, **_k: {})
    check("C6 fora → 111D fora reconcilia track/proveniência e permanece fora",
          r_c6_fora["reatribuicoes_fora"] == 1
          and c6_fora.operador_fora_estado is True
          and c6_fora.operador_fora_track_id == 9
          and c6_fora.operador_fora_proveniencia == "identidade_autoritativa_111d"
          and c6_fora.operador_fora_episodio is None
          and c6_fora.operador_fora_migracao is None
          and c6_fora.fora_candidatos == []
          and c6_fora.fora_posto[0]["track_id"] == 9
          and len(obs_c6_fora) == 1
          and obs_c6_fora[0]["papel"] == pl.PAPEL_OPERADOR_FORA,
          (r_c6_fora, c6_fora, obs_c6_fora))

    c6_fallback = marcar_estado_c6(amostra(24, [], presente=False), 9)
    c6_fallback_antes = deepcopy(c6_fallback)
    r_c6_fallback = aplicar(
        [c6_fallback], [resultado_indefinido()],
        [observacao(24, {9: "fora"}, detalhes={9: detalhe(9, "fora")})],
    )
    obs_c6_fallback = analisar([c6_fallback], fora=lambda *_a, **_k: {})
    check("C6 fora → fallback 111D preserva integralmente o resultado C6",
          r_c6_fallback["status"] == "fallback_legado"
          and c6_fallback == c6_fallback_antes
          and len(obs_c6_fallback) == 1
          and obs_c6_fallback[0]["papel"] == pl.PAPEL_OPERADOR_FORA,
          (r_c6_fallback, c6_fallback, obs_c6_fallback))

    print("\n[19] Cam2 é somente evidência secundária")
    a19 = amostra(0, [
        pessoa(4, "visitante"),
        pessoa(12, "operador", bbox=(200, 10, 300, 310)),
    ], presente=True)
    antes19 = deepcopy(a19)
    r19 = aplicar(
        [a19], [resultado_confirmado((4,), cam="cam2"), resultado_indefinido(cam="cam1")],
        [
            observacao(0, {4: "dentro"}, cam="cam2"),
            observacao(0, {12: "dentro"}, cam="cam1"),
        ],
    )
    check("C19 confirmação cam2 com mesmo track numérico não ganha autoridade",
          r19["status"] == "fallback_legado" and a19 == antes19,
          (r19, a19))
    a19b = amostra(0, [pessoa(4, "operador"), pessoa(9, "visitante")], presente=True)
    r19b = aplicar(
        [a19b], [resultado_confirmado((4,), cam="cam2"),
                 resultado_confirmado((9,), cam="cam1")],
        [
            observacao(0, {4: "dentro"}, cam="cam2"),
            observacao(0, {9: "dentro"}, cam="cam1"),
        ],
    )
    check("cam1 primária vence independentemente da ordem dos resultados",
          r19b["status"] == "aplicado"
          and {p["track_id"]: p["papel"] for p in a19b.pessoas}
          == {4: "visitante", 9: "operador"},
          (r19b, a19b))

    print("\n[identity_only] Identidade sem autoridade física")
    pl._OPERADOR_SEGMENTO_MODO = "identity_only"

    io_cam2 = amostra(30, [], presente=True)
    io_cam2.op_cam2 = True
    io_cam2.n_posto_cam2 = 2
    io_cam2.operador_ponte = True
    r_io_cam2 = aplicar(
        [io_cam2], [resultado_confirmado((9,))], [observacao(30, {})],
    )
    obs_io_cam2 = analisar([io_cam2])
    check("identity_only preserva presença física positiva da CAM2",
          r_io_cam2["motivo"] == "identidade_confirmada_sem_autoridade_fisica"
          and io_cam2.identidade_estado == "ausente"
          and io_cam2.operador_presente is True
          and io_cam2.op_cam2 is True
          and io_cam2.n_posto_cam2 == 2
          and io_cam2.operador_ponte is True
          and not any(o.get("papel") == "posto_vazio" for o in obs_io_cam2),
          (r_io_cam2, io_cam2, obs_io_cam2))

    io_c6 = marcar_estado_c6(amostra(31, [], presente=False), 9)
    c6_antes = deepcopy(io_c6)
    r_io_c6 = aplicar(
        [io_c6], [resultado_confirmado((9,))], [observacao(31, {})],
    )
    obs_io_c6 = analisar([io_c6], fora=lambda *_a, **_k: {})
    check("identity_only não limpa nem reescreve C6",
          r_io_c6["reatribuicoes_ausente"] == 1
          and io_c6.operador_fora_estado == c6_antes.operador_fora_estado
          and io_c6.operador_fora_proveniencia == c6_antes.operador_fora_proveniencia
          and io_c6.operador_fora_track_id == c6_antes.operador_fora_track_id
          and io_c6.operador_fora_episodio == c6_antes.operador_fora_episodio
          and io_c6.operador_fora_migracao == c6_antes.operador_fora_migracao
          and io_c6.fora_candidatos == c6_antes.fora_candidatos
          and io_c6.fora_posto == c6_antes.fora_posto
          and len(obs_io_c6) == 1
          and obs_io_c6[0]["papel"] == pl.PAPEL_OPERADOR_FORA,
          (r_io_c6, io_c6, obs_io_c6))

    io_c5 = amostra(32, [], presente=True)
    io_c5.operador_resgate_cam1_640 = True
    io_c5.operador_resgate_cam1_640_confidence = 0.61
    io_c5.operador_resgate_cam1_640_bbox = (20, 20, 120, 320)
    io_c5.operador_resgate_cam1_640_ancora = (70, 320)
    aplicar([io_c5], [resultado_confirmado((9,))], [observacao(32, {})])
    obs_io_c5 = analisar([io_c5])
    check("identity_only preserva integralmente o C5 positivo",
          io_c5.operador_presente is True
          and io_c5.operador_resgate_cam1_640 is True
          and io_c5.operador_resgate_cam1_640_confidence == 0.61
          and io_c5.operador_resgate_cam1_640_bbox == (20, 20, 120, 320)
          and io_c5.operador_resgate_cam1_640_ancora == (70, 320)
          and len(obs_io_c5) == 1
          and obs_io_c5[0].get("origem_gate") == "resgate_cam1_640",
          (io_c5, obs_io_c5))

    io_safety = amostra(33, [], presente=None)
    io_safety.presenca_safety_gate = True
    io_safety.presenca_safety_motivo = "veto_posto_vazio_por_consenso_multicamera_640"
    aplicar(
        [io_safety], [resultado_confirmado((9,))], [observacao(33, {})],
    )
    obs_io_safety = analisar([io_safety])
    check("identity_only preserva safety e mantém o slot inconclusivo",
          io_safety.operador_presente is None
          and io_safety.presenca_safety_gate is True
          and io_safety.presenca_safety_motivo
          == "veto_posto_vazio_por_consenso_multicamera_640"
          and io_safety.identidade_estado == "ausente"
          and len(obs_io_safety) == 1
          and obs_io_safety[0]["papel"] is None
          and obs_io_safety[0].get("origem_gate")
          == "confirmacao_presenca_indisponivel",
          (io_safety, obs_io_safety))

    io_dentro = amostra(
        34,
        [pessoa(9, "visitante"),
         pessoa(12, "operador", bbox=(200, 10, 300, 310))],
        presente=False,
    )
    r_io_dentro = aplicar(
        [io_dentro], [resultado_confirmado((9,))],
        [observacao(34, {9: "dentro", 12: "dentro"})],
    )
    check("identity_only mantém titular e visitantes sem promover presença",
          r_io_dentro["reatribuicoes_dentro"] == 1
          and io_dentro.identidade_autoritativa is True
          and io_dentro.identidade_estado == "dentro"
          and io_dentro.identidade_track_id == 9
          and {p["track_id"]: p["papel"] for p in io_dentro.pessoas}
          == {9: "operador", 12: "visitante"}
          and io_dentro.operador_presente is False,
          (r_io_dentro, io_dentro))

    io_fora_identitario = amostra(
        34.5, [pessoa(12, "operador")], presente=True,
    )
    io_fora_identitario.op_cam2 = True
    aplicar(
        [io_fora_identitario], [resultado_confirmado((9,))],
        [observacao(
            34.5, {9: "fora", 12: "dentro"},
            detalhes={9: detalhe(9, "fora"), 12: detalhe(12, "dentro")},
        )],
    )
    check("identity_only registra titular fora sem fabricar OPERADOR_FORA",
          io_fora_identitario.identidade_estado == "fora"
          and io_fora_identitario.identidade_track_id == 9
          and io_fora_identitario.pessoas[0]["papel"] == "visitante"
          and io_fora_identitario.operador_presente is True
          and io_fora_identitario.op_cam2 is True
          and io_fora_identitario.fora_posto == []
          and io_fora_identitario.operador_fora_estado is False,
          io_fora_identitario)

    io_visitante = amostra(35, [pessoa(12, "operador")], presente=False)
    aplicar(
        [io_visitante], [resultado_confirmado((9,))],
        [observacao(35, {12: "dentro"})],
    )
    obs_io_visitante = analisar(
        [io_visitante], seq=seq_adversarial(12, True),
    )
    check("identity_only mantém não vencedor como visitante sem criar vazio",
          io_visitante.identidade_estado == "ausente"
          and io_visitante.pessoas[0]["papel"] == "visitante"
          and io_visitante.operador_presente is False
          and len(obs_io_visitante) == 1
          and obs_io_visitante[0]["papel"] == "visitante"
          and not any(o.get("papel") == "posto_vazio"
                      for o in obs_io_visitante),
          (io_visitante, obs_io_visitante))

    pl._OPERADOR_SEGMENTO_MODO = "on"
    legado_on = marcar_estado_c6(amostra(36, [], presente=True), 9)
    legado_on.operador_ponte = True
    aplicar(
        [legado_on], [resultado_confirmado((9,))], [observacao(36, {})],
    )
    check("on preserva autoridade física legada para o A/B",
          legado_on.identidade_estado == "ausente"
          and legado_on.operador_presente is False
          and legado_on.operador_ponte is False
          and c6_limpo(legado_on),
          legado_on)

    for modo_sem_autoridade in ("sombra", "off"):
        pl._OPERADOR_SEGMENTO_MODO = modo_sem_autoridade
        pl.AUTORIDADE_111D_CONFIGURADA = False
        desligada = amostra(37, [pessoa(12, "operador")], presente=True)
        desligada_antes = deepcopy(desligada)
        r_desligada = aplicar(
            [desligada], [resultado_confirmado((9,))],
            [observacao(37, {})],
        )
        check(f"{modo_sem_autoridade} permanece sem autoridade e sem mutação",
              desligada == desligada_antes
              and r_desligada["status"] == "fallback_legado",
              (r_desligada, desligada))
    pl.AUTORIDADE_111D_CONFIGURADA = True

finally:
    pl.AUTORIDADE_111D_CONFIGURADA = autoridade_original
    pl._OPERADOR_SEGMENTO_MODO = modo_original
    pl._imagem_fora_identidade = imagem_original
    pl._frame_b64_para_bgr = decode_original


print("\n[18] Versão do instrumento por configuração de processo")
nomes_env = (
    "KV_OPERADOR_SEGMENTO", "KV_TRACKER", "KV_FORA_DO_POSTO",
    "KV_PRODUTIVIDADE_OPERADOR_V9",
)
env_original = {n: os.environ.get(n) for n in nomes_env}
try:
    os.environ["KV_PRODUTIVIDADE_OPERADOR_V9"] = "on"
    os.environ["KV_TRACKER"] = "reid"
    os.environ["KV_FORA_DO_POSTO"] = "on"
    os.environ["KV_OPERADOR_SEGMENTO"] = "off"
    versao_anterior = importlib.reload(pl).VERSAO_INSTRUMENTO

    os.environ["KV_OPERADOR_SEGMENTO"] = "on"
    versao_on = importlib.reload(pl).VERSAO_INSTRUMENTO

    os.environ["KV_OPERADOR_SEGMENTO"] = "identity_only"
    modulo_identity_only = importlib.reload(pl)
    versao_identity_only = modulo_identity_only.VERSAO_INSTRUMENTO
    estruturada_identity_only = (
        modulo_identity_only.PRODUTIVIDADE_OPERADOR_ESTRUTURADA
    )

    os.environ["KV_OPERADOR_SEGMENTO"] = "on"
    os.environ["KV_PRODUTIVIDADE_OPERADOR_V9"] = "off"
    modulo_on_sem_v9 = importlib.reload(pl)
    versao_on_sem_v9 = modulo_on_sem_v9.VERSAO_INSTRUMENTO
    estruturada_on_sem_v9 = modulo_on_sem_v9.PRODUTIVIDADE_OPERADOR_ESTRUTURADA
    os.environ["KV_PRODUTIVIDADE_OPERADOR_V9"] = "on"

    os.environ["KV_OPERADOR_SEGMENTO"] = "sombra"
    versao_sombra = importlib.reload(pl).VERSAO_INSTRUMENTO

    os.environ["KV_OPERADOR_SEGMENTO"] = "on"
    os.environ["KV_TRACKER"] = "fixa"
    versao_sem_reid = importlib.reload(pl).VERSAO_INSTRUMENTO

    os.environ["KV_TRACKER"] = "reid"
    os.environ["KV_FORA_DO_POSTO"] = "off"
    versao_sem_110 = importlib.reload(pl).VERSAO_INSTRUMENTO

    check("C18 tríade autoritativa carimba V11",
          versao_on == 11, versao_on)
    check("C18 identity_only mantém a construção identitária V11",
          versao_identity_only == 11 and estruturada_identity_only is True,
          (versao_identity_only, estruturada_identity_only))
    check("C18 tríade não depende de quarta chave V9",
          versao_on_sem_v9 == 11 and estruturada_on_sem_v9 is True,
          (versao_on_sem_v9, estruturada_on_sem_v9))
    check("C18 off/sombra/config incompleta preservam a versão anterior",
          versao_anterior == 10
          and versao_sombra == versao_anterior
          and versao_sem_reid == versao_anterior
          and versao_sem_110 == versao_anterior,
          (versao_anterior, versao_sombra, versao_sem_reid, versao_sem_110))
finally:
    for nome, valor in env_original.items():
        if valor is None:
            os.environ.pop(nome, None)
        else:
            os.environ[nome] = valor
    importlib.reload(pl)


print("\n[20] Orquestração e zero persistência lógica")
fonte = Path("backend/pipeline.py").read_text(encoding="utf-8")
i_proc = fonte.index("def processar_video(")
i_registrar = fonte.index("resultados_identidade = _registrar_identidades_segmento_sombra(", i_proc)
i_confirmar = fonte.index("stats_op = etapa_confirmar_operador(", i_registrar)
i_aplicar = fonte.index("resumo_111d = aplicar_identidade_logica_segmento(", i_confirmar)
i_vlm = fonte.index("observacoes = etapa_analise_vlm(", i_aplicar)
check("ordem oficial é 111C → legado → 111D → VLM",
      i_registrar < i_confirmar < i_aplicar < i_vlm,
      (i_registrar, i_confirmar, i_aplicar, i_vlm))
trecho_deteccao = fonte[
    fonte.index("def etapa_detectar_e_amostrar("):
    fonte.index("def _analisar_amostra_vlm(")
]
check("coletor on guarda detalhes e no máximo um JPEG quando há pessoa fora",
      'obs_identidade["pessoas"] = detalhes_identidade' in trecho_deteccao
      and 'precisa_frame_identidade = any(' in trecho_deteccao
      and 'obs_identidade["frame_b64"] = frame_para_base64(' in trecho_deteccao
      and 'identidade_shadow["frames_falhos"]' in trecho_deteccao
      and 'img_b64 = obs_identidade["frame_b64"]' in trecho_deteccao
      and 'obs["frame_b64"] = None' in fonte)
check("prompt autoritativo fixa R1 em vez de pedir nova eleição",
      'a identidade já foi fixada pela janela completa' in fonte
      and 'devolva exatamente esse mesmo rótulo em "operador"' in fonte)
trecho_persistir = fonte[
    fonte.index("def etapa_persistir("):
    fonte.index("\ndef ", fonte.index("def etapa_persistir(") + 5)
]
check("R1 e identidade_logica não entram na persistência",
      '"identidade_logica"' not in trecho_persistir
      and '"track_ids"' not in trecho_persistir)


print(f"\n{'=' * 68}\n  {ok} ok · {fail} falha(s)\n{'=' * 68}")
sys.exit(1 if fail else 0)
