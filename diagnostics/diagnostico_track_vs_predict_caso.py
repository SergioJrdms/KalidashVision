#!/usr/bin/env python3
"""Compara predict e track no frame cam2=78 s do FP2B, sem corrigir nada.

Este experimento é estritamente observacional. A/B/C/D são executados em
processos Python separados para que o singleton de ``worker._get_yolo()`` e o
tracker não vazem estado entre modos. Dentro de D, cam1 e cam2 usam de propósito
o mesmo objeto YOLO e as funções reais do pipeline.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


DIRETORIO_DIAGNOSTICS = Path(__file__).resolve().parent
RAIZ_REPO = DIRETORIO_DIAGNOSTICS.parent
if str(DIRETORIO_DIAGNOSTICS) not in sys.path:
    sys.path.insert(0, str(DIRETORIO_DIAGNOSTICS))

import diagnostico_fp1_lowconf as lowconf_base
import diagnostico_fp1_presenca as presenca_base


CAM1_ID = "cam1"
CAM2_ID = "cam2"
TARGET_CAM1_S = 88.0
TARGET_CAM2_S = 78.0
CAM1_CONTEXT_S = (64.0, 72.0, 80.0, 88.0)
CAM2_CONTEXT_S = (54.0, 62.0, 70.0, 78.0)
CAM2_OFFSET_ESPERADO_S = 10.0
TRACK_CONF = 0.35
IMGSZ = 416
CLASSES = [0]
OPERADOR_REFERENCIA_BBOX = (114.834, 30.540, 182.405, 230.846)
OPERADOR_REFERENCIA_ANCHOR = (159.602, 71.098)
OPERADOR_REFERENCIA_CONFIDENCE = 0.818198681
IOU_REFERENCIA_MIN = 0.50
MODELO_ESPERADO = "yolo11n-pose.pt"
MODOS = ("A", "B", "C", "D")
DESCRICOES_MODO = {
    "A": "predict bruto no frame cam2=78s",
    "B": "track limpo somente no frame cam2=78s",
    "C": "track limpo com contexto cam2=54,62,70,78s",
    "D": "cam1 real, reset real da ponte e contexto cam2=54,62,70,78s",
}
HASHES_FP2B = {
    "cam1": "1cbd3c52e2af6e1f6abe99fc445515104b378b64b76352be5d2235f44c4676e4",
    "cam2": "b82a2951898e3b226c5fd5cbf626cc688abe314bb4b90d08b1f0b3d0b5e022af",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EPS = 1e-9
TIPO_DIAGNOSTICO = "track_vs_predict_fp2b_observacional_sem_correcao"

CAMPOS_CSV = (
    "diagnostico_tipo",
    "modo",
    "modo_descricao",
    "api_final",
    "camera",
    "tempo_cam1_s",
    "tempo_cam2_s",
    "sequencia_cam2_s",
    "modo_valido",
    "motivo_invalidade",
    "n_pessoas_retornadas",
    "n_pessoas_dentro_posto",
    "n_candidatos_operador_referencia",
    "n_com_track_id",
    "resultado_tem_box",
    "detection_index",
    "confidence",
    "bbox_xyxy",
    "track_id",
    "track_confirmado",
    "ancora_xy",
    "ancora_dentro_posto",
    "iou_bbox_operador_referencia",
    "corresponde_operador_referencia",
    "frame_sha256",
    "frame_shape",
    "frame_pos_msec_apos_leitura",
    "tracker_reset_antes_cam2",
    "parametros_inferencia_final",
    "modelo",
    "tracker_config",
)


def _json_compacto(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


def _celula_bool(valor: bool | None) -> str:
    if valor is None:
        return ""
    return "true" if valor else "false"


def _normalizar_sha256(valor: str, nome: str) -> str:
    normalizado = str(valor or "").strip().lower()
    if not SHA256_RE.fullmatch(normalizado):
        raise SystemExit(f"{nome} precisa ser SHA-256 hexadecimal de 64 caracteres")
    return normalizado


def _validar_video_fp2b(
    caminho: Path,
    informado: str,
    camera: str,
) -> str:
    esperado = HASHES_FP2B[camera]
    normalizado = _normalizar_sha256(informado, f"--sha256-{camera}")
    if normalizado != esperado:
        raise SystemExit(
            f"hash informado de {camera} não é o hash original do FP2B: "
            f"esperado={esperado}, informado={normalizado}"
        )
    if not caminho.is_file():
        raise SystemExit(f"{camera} não encontrada: {caminho}")
    recebido = lowconf_base._sha256(caminho)
    if recebido != esperado:
        raise SystemExit(
            f"arquivo {camera} não é o original do FP2B: "
            f"esperado={esperado}, recebido={recebido}"
        )
    return recebido


def cam2_tempo_s(tempo_cam1_s: float, cam2_offset_s: float) -> float:
    return float(tempo_cam1_s) - float(cam2_offset_s)


def _validar_tempos(args: argparse.Namespace) -> None:
    for nome in ("cam2_offset_s", "runner_inicio_s", "runner_fim_s"):
        valor = float(getattr(args, nome))
        if not math.isfinite(valor):
            raise SystemExit(f"--{nome.replace('_', '-')} precisa ser finito")
        setattr(args, nome, valor)
    if args.runner_inicio_s < 0 or args.runner_fim_s < args.runner_inicio_s:
        raise SystemExit("intervalo do runner inválido")
    if not (
        args.runner_inicio_s - EPS
        <= TARGET_CAM1_S
        <= args.runner_fim_s + EPS
    ):
        raise SystemExit(
            f"o intervalo do runner precisa conter o alvo cam1={TARGET_CAM1_S:.0f}s"
        )
    if abs(args.cam2_offset_s - CAM2_OFFSET_ESPERADO_S) > EPS:
        raise SystemExit(
            "FP2B exige --cam2-offset-s 10; "
            f"recebido={args.cam2_offset_s}"
        )
    mapeado = cam2_tempo_s(TARGET_CAM1_S, args.cam2_offset_s)
    if abs(mapeado - TARGET_CAM2_S) > EPS:
        raise SystemExit(
            f"alinhamento inválido: cam1 {TARGET_CAM1_S} mapeou cam2 {mapeado}"
        )


def _manifest_path(output: Path) -> Path:
    return output.with_suffix(".manifest.json")


def _validar_destinos(output: Path) -> None:
    manifesto = _manifest_path(output)
    for caminho in (output, manifesto):
        if caminho.exists():
            raise SystemExit(f"recusei sobrescrever artefato existente: {caminho}")


def _frame_sha256(frame: Any) -> str:
    if not hasattr(frame, "tobytes"):
        raise RuntimeError("frame não oferece tobytes() para auditoria")
    return hashlib.sha256(frame.tobytes()).hexdigest()


def _shape_frame(frame: Any) -> list[int]:
    return [int(v) for v in getattr(frame, "shape", ())]


def _bbox_iou(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
    ax1, ay1, ax2, ay2 = (float(v) for v in a)
    bx1, by1, bx2, by2 = (float(v) for v in b)
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    uniao = area_a + area_b - inter
    return inter / uniao if uniao > 0 else 0.0


def _snapshot_tracker(yolo: Any) -> dict:
    """Leitura best-effort do estado interno; nunca altera o tracker."""
    try:
        predictor = getattr(yolo, "predictor", None)
        trackers = list(getattr(predictor, "trackers", None) or [])
    except Exception as exc:  # noqa: BLE001
        return {"erro": f"{type(exc).__name__}: {exc}"}
    saida = []
    for tracker in trackers:
        item = {
            "tipo": type(tracker).__name__,
            "frame_id": getattr(tracker, "frame_id", None),
        }
        for atributo in ("tracked_stracks", "lost_stracks", "removed_stracks"):
            try:
                item[f"n_{atributo}"] = len(getattr(tracker, atributo, None) or [])
            except Exception:  # noqa: BLE001
                item[f"n_{atributo}"] = None
        saida.append(item)
    return {"predictor_existe": predictor is not None, "trackers": saida}


def _primeiro_resultado(resultados: Any) -> Any | None:
    if resultados is None:
        return None
    try:
        return resultados[0] if len(resultados) else None
    except (TypeError, AttributeError):
        return None


def _resumo_boxes(resultado: Any | None) -> dict:
    boxes = getattr(resultado, "boxes", None) if resultado is not None else None
    if boxes is None:
        return {"n_boxes": 0, "n_com_track_id": 0, "track_ids": []}
    try:
        n_boxes = int(len(boxes))
    except TypeError:
        n_boxes = 0
    ids = lowconf_base._como_lista(getattr(boxes, "id", None))
    ids_validos = []
    for valor in ids:
        try:
            numero = float(valor)
            if math.isfinite(numero):
                ids_validos.append(int(numero))
        except (TypeError, ValueError):
            continue
    return {
        "n_boxes": n_boxes,
        "n_com_track_id": len(ids_validos),
        "track_ids": ids_validos,
    }


def _extrair_deteccoes(
    resultado: Any | None,
    pipeline: Any,
    postos_px: dict,
    largura: int,
    altura: int,
) -> list[dict]:
    boxes = getattr(resultado, "boxes", None) if resultado is not None else None
    if boxes is None or len(boxes) == 0:
        return []
    bboxes = lowconf_base._como_lista(boxes.xyxy)
    confiancas = lowconf_base._como_lista(getattr(boxes, "conf", None))
    ids = lowconf_base._como_lista(getattr(boxes, "id", None))
    keypoints = getattr(resultado, "keypoints", None)
    kpts_todos = lowconf_base._como_lista(getattr(keypoints, "xyn", None))

    deteccoes = []
    for indice, bbox_bruta in enumerate(bboxes):
        bbox = [float(v) for v in bbox_bruta[:4]]
        if len(bbox) != 4:
            raise RuntimeError(f"bbox inválida no índice {indice}: {bbox_bruta}")
        kpts = kpts_todos[indice] if indice < len(kpts_todos) else None
        pessoa = {"bbox": tuple(int(v) for v in bbox)}
        if kpts is not None:
            pessoa["kpts"] = kpts
        ancora_x, ancora_y = pipeline._ponto_ancora(pessoa, largura, altura)
        dentro_posto = any(
            pipeline._ponto_em_roi(ancora_x, ancora_y, info["polygon"])
            for info in postos_px.values()
        )
        track_id = None
        if indice < len(ids):
            try:
                valor_id = float(ids[indice])
                if math.isfinite(valor_id):
                    track_id = int(valor_id)
            except (TypeError, ValueError):
                track_id = None
        confidence = (
            float(confiancas[indice]) if indice < len(confiancas) else None
        )
        iou_referencia = _bbox_iou(bbox, OPERADOR_REFERENCIA_BBOX)
        deteccoes.append({
            "detection_index": indice,
            "confidence": confidence,
            "bbox_xyxy": bbox,
            "track_id": track_id,
            "ancora_xy": [float(ancora_x), float(ancora_y)],
            "ancora_dentro_posto": bool(dentro_posto),
            "iou_bbox_operador_referencia": iou_referencia,
            "corresponde_operador_referencia": bool(
                dentro_posto and iou_referencia >= IOU_REFERENCIA_MIN
            ),
        })
    return deteccoes


def _ler_sequencia_direta(
    *,
    cv2: Any,
    yolo: Any,
    video_path: Path,
    tempos_cam2: tuple[float, ...],
    modo: str,
    tracker_config: str,
) -> tuple[Any | None, list[dict], dict]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"não foi possível abrir cam2: {video_path}")
    eventos = []
    resultado_final = None
    frame_final = {}
    try:
        for tempo_s in tempos_cam2:
            alvo_ms = float(tempo_s) * 1000.0
            if not cap.set(cv2.CAP_PROP_POS_MSEC, alvo_ms):
                raise RuntimeError(f"backend recusou seek cam2={tempo_s:.3f}s")
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(f"falha ao ler cam2={tempo_s:.3f}s")
            snapshot_antes = _snapshot_tracker(yolo)
            if modo == "A":
                resultados = yolo.predict(
                    frame,
                    classes=CLASSES,
                    conf=TRACK_CONF,
                    imgsz=IMGSZ,
                    verbose=False,
                    save=False,
                )
                parametros = {
                    "classes": CLASSES,
                    "conf": TRACK_CONF,
                    "imgsz": IMGSZ,
                    "verbose": False,
                    "save": False,
                }
            else:
                resultados = yolo.track(
                    frame,
                    classes=CLASSES,
                    conf=TRACK_CONF,
                    imgsz=IMGSZ,
                    persist=True,
                    tracker=tracker_config,
                    verbose=False,
                )
                parametros = {
                    "classes": CLASSES,
                    "conf": TRACK_CONF,
                    "imgsz": IMGSZ,
                    "persist": True,
                    "tracker": tracker_config,
                    "verbose": False,
                }
            resultado = _primeiro_resultado(resultados)
            frame_meta = {
                "tempo_cam2_s": float(tempo_s),
                "frame_sha256": _frame_sha256(frame),
                "frame_shape": _shape_frame(frame),
                "frame_index_lido": int(
                    round(float(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
                ),
                "frame_pos_msec_apos_leitura": float(
                    cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0
                ),
            }
            eventos.append({
                **frame_meta,
                **_resumo_boxes(resultado),
                "parametros": parametros,
                "tracker_antes": snapshot_antes,
                "tracker_depois": _snapshot_tracker(yolo),
            })
            resultado_final = resultado
            frame_final = frame_meta
    finally:
        cap.release()
    return resultado_final, eventos, frame_final


def _fim_processamento_cam1(
    args: argparse.Namespace,
    pipeline: Any,
    info_cam1: dict,
) -> float:
    intervalo = float(pipeline.DEFAULT_INTERVALO_AMOSTRAGEM_S)
    gap_slots = int(pipeline._OPERADOR_GAP_SLOTS)
    duracao = float(info_cam1["duracao_s"])
    if not math.isfinite(intervalo) or intervalo <= 0:
        raise SystemExit(f"intervalo real inválido: {intervalo}")
    if gap_slots < 0:
        raise SystemExit(f"_OPERADOR_GAP_SLOTS inválido: {gap_slots}")
    if args.runner_fim_s > duracao + EPS:
        raise SystemExit(
            f"fim do runner ultrapassa cam1: {args.runner_fim_s} > {duracao}"
        )
    return min(duracao, args.runner_fim_s + gap_slots * intervalo)


def _selecionar_amostras_contexto(amostras: list) -> list:
    por_tempo = {round(float(am.tempo_s), 3): am for am in amostras}
    faltantes = [tempo for tempo in CAM1_CONTEXT_S if tempo not in por_tempo]
    if faltantes:
        raise SystemExit(
            "a cadência real não produziu os slots FP2B exatos: "
            f"faltantes={faltantes}; disponíveis={sorted(por_tempo)}"
        )
    return [por_tempo[tempo] for tempo in CAM1_CONTEXT_S]


def _executar_modo_d(
    *,
    args: argparse.Namespace,
    pipeline: Any,
    yolo: Any,
    zonas_por_cam: dict,
    info_cam1: dict,
) -> tuple[Any | None, list[dict], dict, dict, str | None]:
    fim_processamento = _fim_processamento_cam1(args, pipeline, info_cam1)
    identidade_shadow = {
        "observacoes": [],
        "descritores": [],
        "guardar_detalhes": True,
        "guardar_frames": False,
    }
    (amostras, _info, _ids, _desc, _mov, _grade) = (
        pipeline.etapa_detectar_e_amostrar(
            yolo,
            str(args.cam1),
            float(pipeline.DEFAULT_INTERVALO_AMOSTRAGEM_S),
            zonas_por_cam[CAM1_ID],
            presenca_base._progress,
            cam_id=CAM1_ID,
            mapa_movimento=None,
            identidade_shadow=identidade_shadow,
            fim_s=fim_processamento,
        )
    )
    amostras_contexto = _selecionar_amostras_contexto(amostras)
    if int(pipeline._CAM2_CONFIRM_STRIDE) != 1:
        raise SystemExit(
            "D exige _CAM2_CONFIRM_STRIDE=1 para medir os quatro slots exatos; "
            f"recebido={pipeline._CAM2_CONFIRM_STRIDE}"
        )

    track_original = yolo.track
    reset_original = pipeline.resetar_tracker
    chamadas_track: list[dict] = []
    resets: list[dict] = []

    def track_observado(frame, *posicionais, **nomeados):
        antes = _snapshot_tracker(yolo)
        resultado = track_original(frame, *posicionais, **nomeados)
        primeiro = _primeiro_resultado(resultado)
        chamadas_track.append({
            "frame_sha256": _frame_sha256(frame),
            "frame_shape": _shape_frame(frame),
            "parametros": dict(nomeados),
            "resultado": primeiro,
            "resumo": _resumo_boxes(primeiro),
            "tracker_antes": antes,
            "tracker_depois": _snapshot_tracker(yolo),
        })
        return resultado

    def reset_observado(yolo_recebido):
        if yolo_recebido is not yolo:
            raise RuntimeError("reset da ponte recebeu outro objeto YOLO")
        antes = _snapshot_tracker(yolo)
        retorno = reset_original(yolo_recebido)
        resets.append({
            "retorno": retorno,
            "tracker_antes": antes,
            "tracker_depois": _snapshot_tracker(yolo),
        })
        return retorno

    diagnostico_cam2: list[dict] = []
    setattr(yolo, "track", track_observado)
    pipeline.resetar_tracker = reset_observado
    try:
        pipeline._anexar_segundo_angulo(
            amostras_contexto,
            str(args.cam2),
            yolo=yolo,
            rois_sec=zonas_por_cam[CAM2_ID],
            offset_s=-float(args.cam2_offset_s),
            desc_acc={},
            identidade_shadow=identidade_shadow,
            cam_id=CAM2_ID,
            diagnostico_presenca=diagnostico_cam2,
        )
    finally:
        setattr(yolo, "track", track_original)
        pipeline.resetar_tracker = reset_original

    parametros_esperados = {
        "classes": CLASSES,
        "conf": TRACK_CONF,
        "imgsz": IMGSZ,
        "persist": True,
        "tracker": pipeline.TRACKER_CONFIG,
        "verbose": False,
    }
    if len(chamadas_track) != len(CAM2_CONTEXT_S):
        raise SystemExit(
            f"D não executou quatro tracks cam2: {len(chamadas_track)}"
        )
    resultado_final_bruto = chamadas_track[-1]["resultado"]
    for indice, chamada in enumerate(chamadas_track):
        if chamada["parametros"] != parametros_esperados:
            raise SystemExit(
                f"parâmetros de track divergiram em D/{indice}: "
                f"{chamada['parametros']}"
            )
        chamada["tempo_cam2_s"] = CAM2_CONTEXT_S[indice]
        chamada.pop("resultado", None)
    if len(resets) != 1:
        raise SystemExit(f"D observou {len(resets)} resets antes da cam2, esperado 1")
    if len(diagnostico_cam2) != 4 or any(
        item.get("medido") is not True for item in diagnostico_cam2
    ):
        raise SystemExit(
            "D não mediu validamente os quatro slots na ponte real: "
            f"{diagnostico_cam2}"
        )

    contexto_cam1 = {
        "runner_inicio_s": args.runner_inicio_s,
        "runner_fim_s": args.runner_fim_s,
        "intervalo_amostragem_s": float(pipeline.DEFAULT_INTERVALO_AMOSTRAGEM_S),
        "operador_gap_slots": int(pipeline._OPERADOR_GAP_SLOTS),
        "fim_processamento_s": fim_processamento,
        "n_amostras_cam1": len(amostras),
        "primeira_amostra_s": float(amostras[0].tempo_s) if amostras else None,
        "ultima_amostra_s": float(amostras[-1].tempo_s) if amostras else None,
        "slots_contexto_cam1_s": list(CAM1_CONTEXT_S),
    }
    reset_retorno = resets[0].get("retorno")
    motivo_invalidade = (
        f"reset real da ponte retornou {reset_retorno!r}"
        if reset_retorno not in {"reset", "recriar"} else None
    )
    frame_final = {
        "tempo_cam2_s": TARGET_CAM2_S,
        "frame_sha256": chamadas_track[-1]["frame_sha256"],
        "frame_shape": chamadas_track[-1]["frame_shape"],
        "frame_index_lido": None,
        "frame_pos_msec_apos_leitura": None,
    }
    return (
        resultado_final_bruto,
        chamadas_track,
        frame_final,
        {"cam1": contexto_cam1, "reset_ponte": resets[0], "diagnostico_cam2": diagnostico_cam2},
        motivo_invalidade,
    )


def _validar_configuracao(
    pipeline: Any,
    worker: Any,
    zonas_por_cam: dict,
    args: argparse.Namespace,
) -> tuple[dict, dict, Path, Path]:
    if not bool(getattr(pipeline, "_ZONA_ESTRITA", False)):
        raise SystemExit("pipeline importado sem KV_ZONA_ESTRITA=on")
    if getattr(pipeline, "_FORA_MODO", None) != "on":
        raise SystemExit("pipeline importado sem KV_FORA_DO_POSTO=on")
    if not bool(getattr(pipeline, "_OPERADOR_FILTRO_ENABLE", False)):
        raise SystemExit("pipeline importado sem filtro de operador ativo")
    if pipeline.YOLO_MODEL != MODELO_ESPERADO:
        raise SystemExit(f"modelo divergente: {pipeline.YOLO_MODEL!r}")
    cam2_conf = float(pipeline._CAM2_CONF)
    if not math.isfinite(cam2_conf) or abs(cam2_conf - TRACK_CONF) > EPS:
        raise SystemExit(f"_CAM2_CONF divergiu de 0.35: {cam2_conf}")
    tracker_path = Path(pipeline.TRACKER_CONFIG).resolve()
    if tracker_path.name != "botsort_camera_fixa_reid.yaml" or not tracker_path.is_file():
        raise SystemExit(f"TRACKER_CONFIG não é o YAML Re-ID real: {tracker_path}")
    if "with_reid: true" not in tracker_path.read_text(encoding="utf-8").lower():
        raise SystemExit(f"tracker não ativa Re-ID: {tracker_path}")
    modelo_path = Path(worker._modelo_path(pipeline.YOLO_MODEL)).resolve()

    info_cam1 = pipeline.inspecionar_video(str(args.cam1))
    info_cam2 = pipeline.inspecionar_video(str(args.cam2))
    for camera, info in ((CAM1_ID, info_cam1), (CAM2_ID, info_cam2)):
        presenca_base.validar_frame_ref(
            zonas_por_cam[camera],
            int(info["largura"]),
            int(info["altura"]),
            camera,
        )
    ultimo_cam2 = (int(info_cam2["total_frames"]) - 1) / float(info_cam2["fps"])
    if min(CAM2_CONTEXT_S) < 0 or max(CAM2_CONTEXT_S) > ultimo_cam2 + EPS:
        raise SystemExit(
            f"contexto cam2 fora do vídeo: {CAM2_CONTEXT_S}, último={ultimo_cam2}"
        )
    _fim_processamento_cam1(args, pipeline, info_cam1)
    return info_cam1, info_cam2, modelo_path, tracker_path


def _executar_modo(
    *,
    args: argparse.Namespace,
    pipeline: Any,
    worker: Any,
    cv2: Any,
    zonas_por_cam: dict,
    info_cam1: dict,
    info_cam2: dict,
    modelo_path: Path,
    tracker_path: Path,
) -> dict:
    yolo = worker._get_yolo()
    if not modelo_path.is_file():
        raise SystemExit(f"modelo carregado sem arquivo auditável: {modelo_path}")
    postos_cam2 = {
        nome: info
        for nome, info in zonas_por_cam[CAM2_ID].items()
        if info.get("papel") == "posto_operador"
    }
    postos_px = pipeline._build_rois(
        postos_cam2,
        int(info_cam2["largura"]),
        int(info_cam2["altura"]),
    )
    snapshot_inicial = _snapshot_tracker(yolo)

    if args.internal_mode == "D":
        resultado_final, eventos, frame_final, contexto_extra, motivo_invalidade = (
            _executar_modo_d(
                args=args,
                pipeline=pipeline,
                yolo=yolo,
                zonas_por_cam=zonas_por_cam,
                info_cam1=info_cam1,
            )
        )
        reset_ponte = contexto_extra["reset_ponte"].get("retorno")
    else:
        sequencia = (
            (TARGET_CAM2_S,)
            if args.internal_mode in ("A", "B") else CAM2_CONTEXT_S
        )
        resultado_final, eventos, frame_final = _ler_sequencia_direta(
            cv2=cv2,
            yolo=yolo,
            video_path=args.cam2,
            tempos_cam2=sequencia,
            modo=args.internal_mode,
            tracker_config=pipeline.TRACKER_CONFIG,
        )
        contexto_extra = {}
        motivo_invalidade = None
        reset_ponte = None

    deteccoes = _extrair_deteccoes(
        resultado_final,
        pipeline,
        postos_px,
        int(info_cam2["largura"]),
        int(info_cam2["altura"]),
    )
    resumo_final = _resumo_boxes(resultado_final)
    n_dentro = sum(1 for det in deteccoes if det["ancora_dentro_posto"])
    n_referencia = sum(
        1 for det in deteccoes if det["corresponde_operador_referencia"]
    )
    return {
        "diagnostico_tipo": TIPO_DIAGNOSTICO,
        "modo": args.internal_mode,
        "modo_descricao": DESCRICOES_MODO[args.internal_mode],
        "api_final": "predict" if args.internal_mode == "A" else "track",
        "tempo_cam1_s": TARGET_CAM1_S,
        "tempo_cam2_s": TARGET_CAM2_S,
        "sequencia_cam2_s": (
            [TARGET_CAM2_S]
            if args.internal_mode in ("A", "B") else list(CAM2_CONTEXT_S)
        ),
        "modo_valido": motivo_invalidade is None,
        "motivo_invalidade": motivo_invalidade,
        "n_pessoas_retornadas": resumo_final["n_boxes"],
        "n_pessoas_dentro_posto": n_dentro,
        "n_candidatos_operador_referencia": n_referencia,
        "n_com_track_id": resumo_final["n_com_track_id"],
        "deteccoes": deteccoes,
        "frame_final": frame_final,
        "eventos_inferencia": eventos,
        "tracker_reset_antes_cam2": reset_ponte,
        "tracker_inicial": snapshot_inicial,
        "tracker_final": _snapshot_tracker(yolo),
        "contexto_extra": contexto_extra,
        "processo": {
            "pid": os.getpid(),
            "python": sys.version,
            "plataforma": platform.platform(),
        },
        "configuracao": {
            "modelo": pipeline.YOLO_MODEL,
            "modelo_path": str(modelo_path),
            "modelo_sha256": lowconf_base._sha256(modelo_path),
            "tracker_config": str(tracker_path),
            "tracker_sha256": lowconf_base._sha256(tracker_path),
            "cam2_conf": float(pipeline._CAM2_CONF),
            "imgsz_cam2": IMGSZ,
            "classes": CLASSES,
            "cam2_confirm_stride": int(pipeline._CAM2_CONFIRM_STRIDE),
            "intervalo_amostragem_s": float(pipeline.DEFAULT_INTERVALO_AMOSTRAGEM_S),
            "operador_gap_slots": int(pipeline._OPERADOR_GAP_SLOTS),
        },
    }


def _main_interno(args: argparse.Namespace) -> int:
    _validar_video_fp2b(args.cam1, args.sha256_cam1, CAM1_ID)
    _validar_video_fp2b(args.cam2, args.sha256_cam2, CAM2_ID)
    presenca_base.validar_env_obrigatorio()
    try:
        zonas_por_cam, _origem = presenca_base.carregar_zonas(
            args.zones_file, CAM1_ID, CAM2_ID, None, None
        )
        presenca_base.validar_zonas(zonas_por_cam, CAM1_ID, CAM2_ID)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"zonas inválidas: {exc}") from exc

    if str(RAIZ_REPO) not in sys.path:
        sys.path.insert(0, str(RAIZ_REPO))
    from backend import pipeline, worker
    import cv2

    lowconf_base._bloquear_integracoes(pipeline, worker)
    info_cam1, info_cam2, modelo_path, tracker_path = _validar_configuracao(
        pipeline, worker, zonas_por_cam, args
    )
    resultado = _executar_modo(
        args=args,
        pipeline=pipeline,
        worker=worker,
        cv2=cv2,
        zonas_por_cam=zonas_por_cam,
        info_cam1=info_cam1,
        info_cam2=info_cam2,
        modelo_path=modelo_path,
        tracker_path=tracker_path,
    )
    args.internal_output.parent.mkdir(parents=True, exist_ok=True)
    args.internal_output.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


def _argumentos_filhos(args: argparse.Namespace) -> list[str]:
    return [
        "--cam1", str(args.cam1),
        "--cam2", str(args.cam2),
        "--cam2-offset-s", str(args.cam2_offset_s),
        "--runner-inicio-s", str(args.runner_inicio_s),
        "--runner-fim-s", str(args.runner_fim_s),
        "--sha256-cam1", args.sha256_cam1,
        "--sha256-cam2", args.sha256_cam2,
        "--zones-file", str(args.zones_file),
        "--output", str(args.output),
    ]


def _executar_subprocessos(args: argparse.Namespace) -> list[dict]:
    resultados = []
    with tempfile.TemporaryDirectory(prefix="kv-track-vs-predict-") as tmp:
        tmp_path = Path(tmp)
        for modo in MODOS:
            interno = tmp_path / f"modo-{modo}.json"
            comando = [
                sys.executable,
                str(Path(__file__).resolve()),
                *_argumentos_filhos(args),
                "--internal-mode", modo,
                "--internal-output", str(interno),
            ]
            proc = subprocess.run(
                comando,
                cwd=str(RAIZ_REPO),
                capture_output=True,
                text=True,
                shell=False,
                check=False,
            )
            if proc.returncode != 0 or not interno.is_file():
                raise SystemExit(
                    f"modo {modo} falhou (exit={proc.returncode})\n"
                    f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
                )
            resultado = json.loads(interno.read_text(encoding="utf-8"))
            resultado["subprocesso"] = {
                "comando": comando,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
            resultados.append(resultado)
    pids = [item["processo"]["pid"] for item in resultados]
    if len(set(pids)) != len(MODOS):
        raise SystemExit(f"modos não ficaram isolados em quatro PIDs: {pids}")
    hashes_frame = {item["frame_final"]["frame_sha256"] for item in resultados}
    if len(hashes_frame) != 1:
        raise SystemExit(
            "o frame final cam2=78s divergiu entre subprocessos: "
            f"{sorted(hashes_frame)}"
        )
    return resultados


def _interpretar(resultados: list[dict]) -> dict:
    por_modo = {item["modo"]: item for item in resultados}
    if set(por_modo) != set(MODOS):
        return {"codigo": "inconclusivo_modos_ausentes", "criterio": None}
    invalidos = [modo for modo, item in por_modo.items() if not item["modo_valido"]]
    criterio = {
        modo: bool(item["n_candidatos_operador_referencia"] > 0)
        for modo, item in por_modo.items()
    }
    if invalidos:
        codigo = "inconclusivo_modo_invalido"
    elif not criterio["A"]:
        codigo = "inconclusivo_predict_bruto_nao_reproduzido"
    elif not criterio["B"]:
        codigo = "1_track_perde_em_estado_limpo"
    elif not criterio["C"]:
        codigo = "2_track_perde_com_historico_cam2"
    elif not criterio["D"]:
        codigo = "3_track_perde_apos_ciclo_cam1_ponte"
    else:
        codigo = "4_sem_divergencia_reprodutivel"
    return {
        "codigo": codigo,
        "criterio": (
            "box com IoU >= 0.50 contra o bbox bruto informado e âncora dentro "
            "de posto_operador"
        ),
        "presenca_like_cam2_por_modo": criterio,
        "modos_invalidos": invalidos,
        "limite": (
            "classificação descritiva deste experimento; não altera nem substitui "
            "a decisão do pipeline"
        ),
    }


def _linhas_csv(resultados: list[dict]) -> list[dict]:
    linhas = []
    for item in resultados:
        base = {
            "diagnostico_tipo": item["diagnostico_tipo"],
            "modo": item["modo"],
            "modo_descricao": item["modo_descricao"],
            "api_final": item["api_final"],
            "camera": CAM2_ID,
            "tempo_cam1_s": f"{item['tempo_cam1_s']:.3f}",
            "tempo_cam2_s": f"{item['tempo_cam2_s']:.3f}",
            "sequencia_cam2_s": _json_compacto(item["sequencia_cam2_s"]),
            "modo_valido": _celula_bool(item["modo_valido"]),
            "motivo_invalidade": item.get("motivo_invalidade") or "",
            "n_pessoas_retornadas": item["n_pessoas_retornadas"],
            "n_pessoas_dentro_posto": item["n_pessoas_dentro_posto"],
            "n_candidatos_operador_referencia": item[
                "n_candidatos_operador_referencia"
            ],
            "n_com_track_id": item["n_com_track_id"],
            "resultado_tem_box": _celula_bool(item["n_pessoas_retornadas"] > 0),
            "detection_index": "",
            "confidence": "",
            "bbox_xyxy": "",
            "track_id": "",
            "track_confirmado": "",
            "ancora_xy": "",
            "ancora_dentro_posto": "",
            "iou_bbox_operador_referencia": "",
            "corresponde_operador_referencia": "",
            "frame_sha256": item["frame_final"]["frame_sha256"],
            "frame_shape": _json_compacto(item["frame_final"]["frame_shape"]),
            "frame_pos_msec_apos_leitura": item["frame_final"].get(
                "frame_pos_msec_apos_leitura", ""
            ),
            "tracker_reset_antes_cam2": item.get("tracker_reset_antes_cam2") or "",
            "parametros_inferencia_final": _json_compacto(
                item["eventos_inferencia"][-1]["parametros"]
            ),
            "modelo": item["configuracao"]["modelo"],
            "tracker_config": item["configuracao"]["tracker_config"],
        }
        if not item["deteccoes"]:
            linhas.append(base)
            continue
        for deteccao in item["deteccoes"]:
            linha = dict(base)
            linha.update({
                "detection_index": deteccao["detection_index"],
                "confidence": (
                    f"{deteccao['confidence']:.9f}"
                    if deteccao["confidence"] is not None else ""
                ),
                "bbox_xyxy": _json_compacto(
                    [round(v, 3) for v in deteccao["bbox_xyxy"]]
                ),
                "track_id": (
                    deteccao["track_id"]
                    if deteccao["track_id"] is not None else ""
                ),
                "track_confirmado": _celula_bool(
                    deteccao["track_id"] is not None
                ),
                "ancora_xy": _json_compacto(
                    [round(v, 3) for v in deteccao["ancora_xy"]]
                ),
                "ancora_dentro_posto": _celula_bool(
                    deteccao["ancora_dentro_posto"]
                ),
                "iou_bbox_operador_referencia": (
                    f"{deteccao['iou_bbox_operador_referencia']:.9f}"
                ),
                "corresponde_operador_referencia": _celula_bool(
                    deteccao["corresponde_operador_referencia"]
                ),
            })
            linhas.append(linha)
    return linhas


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Experimento read-only A/B/C/D entre predict e track no FP2B; "
            "não aplica correção nem decisão de posto_vazio."
        )
    )
    parser.add_argument("--cam1", type=Path, required=True)
    parser.add_argument("--cam2", type=Path, required=True)
    parser.add_argument("--cam2-offset-s", type=float, required=True)
    parser.add_argument("--runner-inicio-s", type=float, required=True)
    parser.add_argument("--runner-fim-s", type=float, required=True)
    parser.add_argument("--sha256-cam1", required=True)
    parser.add_argument("--sha256-cam2", required=True)
    parser.add_argument("--zones-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--internal-mode", choices=MODOS, help=argparse.SUPPRESS)
    parser.add_argument("--internal-output", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    args.cam1 = args.cam1.resolve()
    args.cam2 = args.cam2.resolve()
    args.zones_file = args.zones_file.resolve()
    args.output = args.output.resolve()
    args.internal_output = (
        args.internal_output.resolve() if args.internal_output else None
    )
    _validar_tempos(args)
    args.sha256_cam1 = _normalizar_sha256(args.sha256_cam1, "--sha256-cam1")
    args.sha256_cam2 = _normalizar_sha256(args.sha256_cam2, "--sha256-cam2")

    if args.internal_mode:
        if args.internal_output is None:
            raise SystemExit("--internal-output é obrigatório no modo interno")
        return _main_interno(args)
    if args.internal_output is not None:
        raise SystemExit("--internal-output só pode ser usado pelo subprocesso interno")

    _validar_destinos(args.output)
    _validar_video_fp2b(args.cam1, args.sha256_cam1, CAM1_ID)
    _validar_video_fp2b(args.cam2, args.sha256_cam2, CAM2_ID)
    resultados = _executar_subprocessos(args)
    linhas = _linhas_csv(resultados)
    interpretacao = _interpretar(resultados)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as arq:
        escritor = csv.DictWriter(arq, fieldnames=CAMPOS_CSV)
        escritor.writeheader()
        escritor.writerows(linhas)

    manifesto = {
        "diagnostico_tipo": TIPO_DIAGNOSTICO,
        "aviso": (
            "experimento observacional; não é correção, decisão de presença ou "
            "substituto do pipeline"
        ),
        "git": {
            "commit": presenca_base._executar_texto(
                ["git", "rev-parse", "HEAD"], RAIZ_REPO
            ),
            "branch": presenca_base._executar_texto(
                ["git", "branch", "--show-current"], RAIZ_REPO
            ),
            "status_porcelain": presenca_base._executar_texto(
                ["git", "status", "--porcelain"], RAIZ_REPO
            ),
        },
        "entradas": {
            "cam1": str(args.cam1),
            "cam2": str(args.cam2),
            "sha256_cam1": args.sha256_cam1,
            "sha256_cam2": args.sha256_cam2,
            "zones_file": str(args.zones_file),
            "zones_sha256": lowconf_base._sha256(args.zones_file),
            "cam2_offset_s": args.cam2_offset_s,
            "runner_inicio_s": args.runner_inicio_s,
            "runner_fim_s": args.runner_fim_s,
        },
        "alvo": {
            "tempo_cam1_s": TARGET_CAM1_S,
            "tempo_cam2_s": TARGET_CAM2_S,
            "formula": "t_cam2=t_cam1-cam2_offset_s",
            "contexto_cam1_s": list(CAM1_CONTEXT_S),
            "contexto_cam2_s": list(CAM2_CONTEXT_S),
            "operador_referencia": {
                "bbox_xyxy": list(OPERADOR_REFERENCIA_BBOX),
                "ancora_informada": list(OPERADOR_REFERENCIA_ANCHOR),
                "confidence_informada": OPERADOR_REFERENCIA_CONFIDENCE,
                "iou_min": IOU_REFERENCIA_MIN,
            },
        },
        "isolamento": {
            "estrategia": "um subprocesso Python novo por modo",
            "pids": [item["processo"]["pid"] for item in resultados],
        },
        "resultados": resultados,
        "interpretacao_limitada": interpretacao,
        "artefatos": {
            "csv": str(args.output),
            "csv_sha256": lowconf_base._sha256(args.output),
        },
    }
    manifesto_path = _manifest_path(args.output)
    manifesto_path.write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("ATENÇÃO: experimento observacional; nenhuma correção foi aplicada.")
    print(f"CSV: {args.output}")
    print(f"Manifesto: {manifesto_path}")
    print("Interpretação limitada: " + _json_compacto(interpretacao))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
