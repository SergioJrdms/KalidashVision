#!/usr/bin/env python3
"""Probe read-only do detector bruto no FP #1.

IMPORTANTE: isto NÃO é replay do pipeline. O script faz somente sete seeks por
câmera e chama ``predict`` com confiança diagnóstica de 0.05 para revelar
candidatos que os cortes atuais de produção não entregariam ao restante do
pipeline. O resultado não altera thresholds nem participa de decisão alguma.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


RAIZ_REPO = Path(__file__).resolve().parents[1]
ZONAS_PADRAO = Path(__file__).with_name("fp1_zonas_camera_20260824.json")
SLOTS_S = (120.0, 128.0, 136.0, 144.0, 152.0, 160.0, 168.0)
PROBE_CONF = 0.05
PROBE_IMGSZ = 416
PROBE_CLASSES = [0]
MODELO_ESPERADO = "yolo11n-pose.pt"
THRESHOLDS_ESPERADOS = {"cam1": 0.30, "cam2": 0.35}
VIDEOS_ESPERADOS_SHA256 = {
    "cam1": "12afc1d3f8fdb4ce7d47a76f991c5c01855b94bc2ceaa8771f4d5330aa1adc36",
    "cam2": "d68aeb8d0bee56ba1bfedd808d3789580ef9d176720f4b291718e17a9943cf26",
}
DIAGNOSTICO_TIPO = "probe_detector_bruto_lowconf_nao_replay_pipeline"

NOMES_KEYPOINTS_COCO = (
    "nariz", "olho_esq", "olho_dir", "orelha_esq", "orelha_dir",
    "ombro_esq", "ombro_dir", "cotovelo_esq", "cotovelo_dir",
    "punho_esq", "punho_dir", "quadril_esq", "quadril_dir",
    "joelho_esq", "joelho_dir", "tornozelo_esq", "tornozelo_dir",
)

CAMPOS_CSV = (
    "diagnostico_tipo",
    "camera",
    "tempo_s",
    "frame_index_lido",
    "frame_pos_msec_apos_leitura",
    "deteccao_encontrada",
    "n_deteccoes_lowconf",
    "detection_index",
    "probe_conf",
    "imgsz",
    "classes",
    "confidence",
    "bbox_xyxy",
    "area_ratio",
    "keypoints_n_validos",
    "keypoints_xyn_validos",
    "ancora_xy",
    "ancora_dentro_posto",
    "threshold_conf_producao",
    "passa_conf_producao",
    "operador_area_min_ratio",
    "passa_area_min_cam1",
    "passa_conf_e_area_cam1",
    "modelo",
    "modelo_sha256",
)


def _json_compacto(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


def _celula_bool(valor: bool | None) -> str:
    if valor is None:
        return ""
    return "true" if valor else "false"


def _sha256(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arq:
        for bloco in iter(lambda: arq.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def _validar_videos(cam1: Path, cam2: Path) -> None:
    for camera, caminho in (("cam1", cam1), ("cam2", cam2)):
        if not caminho.is_file():
            raise SystemExit(f"{camera} não encontrada: {caminho}")
        recebido = _sha256(caminho)
        if recebido != VIDEOS_ESPERADOS_SHA256[camera]:
            raise SystemExit(
                f"{camera} não é o MP4 original do FP #1: SHA-256 {recebido}"
            )


def _ativo(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor or "").strip().lower() in {"1", "true", "on", "yes", "sim"}


def _validar_pts_rel(valor: Any, origem: str) -> list[list[float]]:
    if not isinstance(valor, list) or len(valor) < 3:
        raise ValueError(f"pts_rel inválido em {origem}")
    pontos = []
    for indice, ponto in enumerate(valor):
        if not isinstance(ponto, (list, tuple)) or len(ponto) != 2:
            raise ValueError(f"ponto {indice} inválido em {origem}")
        x, y = float(ponto[0]), float(ponto[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"ponto {indice} fora de [0,1] em {origem}")
        pontos.append([x, y])
    return pontos


def carregar_postos(caminho: Path) -> dict[str, dict[str, dict]]:
    caminho = caminho.resolve()
    if not caminho.is_file():
        raise FileNotFoundError(f"arquivo de zonas não encontrado: {caminho}")
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    linhas = bruto.get("zonas_camera") if isinstance(bruto, dict) else None
    if not isinstance(linhas, list):
        raise ValueError("arquivo deve conter a lista zonas_camera")

    postos: dict[str, dict[str, dict]] = {"cam1": {}, "cam2": {}}
    for indice, linha in enumerate(linhas, start=1):
        if not isinstance(linha, dict) or not _ativo(linha.get("ativo", True)):
            continue
        if str(linha.get("papel") or "") != "posto_operador":
            continue
        camera = str(linha.get("cam_id") or "")
        if camera not in postos:
            continue
        nome = str(linha.get("nome") or "").strip()
        if not nome:
            raise ValueError(f"zona {indice} sem nome")
        postos[camera][nome] = {
            **linha,
            "pts_rel": _validar_pts_rel(
                linha.get("pts_rel"), f"{camera}/{nome}"
            ),
        }

    faltantes = [camera for camera, zonas in postos.items() if not zonas]
    if faltantes:
        raise ValueError(
            "falta zona ativa posto_operador em " + ", ".join(faltantes)
        )
    return postos


def _validar_frame_ref(zonas: dict, largura: int, altura: int, camera: str) -> None:
    for nome, info in zonas.items():
        ref_w, ref_h = info.get("frame_ref_w"), info.get("frame_ref_h")
        if ref_w in (None, "") or ref_h in (None, ""):
            continue
        if int(ref_w) != largura or int(ref_h) != altura:
            raise ValueError(
                f"zona {camera}/{nome} foi desenhada em {ref_w}x{ref_h}, "
                f"mas o vídeo tem {largura}x{altura}"
            )


def _bloquear_integracoes(pipeline, worker) -> None:
    def proibido(*_args, **_kwargs):
        raise RuntimeError("integração externa proibida no probe low-confidence")

    for modulo, nomes in (
        (
            pipeline,
            (
                "make_supabase_client",
                "make_groq_client",
                "etapa_analise_vlm",
                "etapa_persistir",
                "processar_video",
            ),
        ),
        (
            worker,
            (
                "make_supabase_client",
                "make_groq_client",
                "_baixar_video",
                "_buscar_zonas_por_cam",
                "executar_job",
            ),
        ),
    ):
        for nome in nomes:
            if hasattr(modulo, nome):
                setattr(modulo, nome, proibido)


def _como_lista(valor: Any) -> list:
    if valor is None:
        return []
    if hasattr(valor, "detach"):
        valor = valor.detach()
    if hasattr(valor, "cpu"):
        valor = valor.cpu()
    if hasattr(valor, "tolist"):
        return valor.tolist()
    return list(valor)


def _keypoints_validos(
    kpts: list[list[float]] | None,
    confiancas: list[float] | None,
) -> list[dict]:
    validos = []
    for indice, ponto in enumerate(kpts or []):
        if len(ponto) < 2:
            continue
        x, y = float(ponto[0]), float(ponto[1])
        if x <= 0 or y <= 0:
            continue
        item = {
            "index": indice,
            "nome": (
                NOMES_KEYPOINTS_COCO[indice]
                if indice < len(NOMES_KEYPOINTS_COCO) else f"kpt_{indice}"
            ),
            "x": round(x, 6),
            "y": round(y, 6),
        }
        if confiancas is not None and indice < len(confiancas):
            item["confidence"] = round(float(confiancas[indice]), 6)
        validos.append(item)
    return validos


def _analisar_resultado(
    resultado: Any,
    pipeline: Any,
    postos_px: dict,
    camera: str,
    largura: int,
    altura: int,
) -> list[dict]:
    boxes = getattr(resultado, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []

    bboxes = _como_lista(boxes.xyxy)
    confiancas = _como_lista(boxes.conf)
    keypoints = getattr(resultado, "keypoints", None)
    kpts_todos = _como_lista(getattr(keypoints, "xyn", None))
    kpts_conf_todos = _como_lista(getattr(keypoints, "conf", None))
    threshold_conf = (
        float(pipeline._OPERADOR_CONF)
        if camera == "cam1" else float(pipeline._CAM2_CONF)
    )
    area_min_cam1 = float(pipeline._OPERADOR_AREA_MIN_RATIO)

    deteccoes = []
    for indice, bbox_bruta in enumerate(bboxes):
        bbox = [float(v) for v in bbox_bruta[:4]]
        if len(bbox) != 4:
            raise RuntimeError(f"bbox inválida no índice {indice}: {bbox_bruta}")
        confidence = float(confiancas[indice])
        largura_bbox = max(0.0, bbox[2] - bbox[0])
        altura_bbox = max(0.0, bbox[3] - bbox[1])
        area_ratio = (
            largura_bbox * altura_bbox / float(largura * altura)
            if largura > 0 and altura > 0 else 0.0
        )

        kpts = kpts_todos[indice] if indice < len(kpts_todos) else None
        kpts_conf = (
            kpts_conf_todos[indice]
            if indice < len(kpts_conf_todos) else None
        )
        pessoa = {"bbox": tuple(int(v) for v in bbox)}
        if kpts is not None:
            pessoa["kpts"] = kpts
        ancora_x, ancora_y = pipeline._ponto_ancora(pessoa, largura, altura)
        dentro_posto = any(
            pipeline._ponto_em_roi(ancora_x, ancora_y, info["polygon"])
            for info in postos_px.values()
        )
        passa_conf = confidence >= threshold_conf
        passa_area = area_ratio >= area_min_cam1 if camera == "cam1" else None

        deteccoes.append({
            "detection_index": indice,
            "confidence": confidence,
            "bbox": bbox,
            "area_ratio": area_ratio,
            "keypoints": _keypoints_validos(kpts, kpts_conf),
            "ancora": [float(ancora_x), float(ancora_y)],
            "dentro_posto": bool(dentro_posto),
            "threshold_conf": threshold_conf,
            "passa_conf": passa_conf,
            "area_min_cam1": area_min_cam1 if camera == "cam1" else None,
            "passa_area_cam1": passa_area,
            "passa_conf_e_area_cam1": (
                passa_conf and bool(passa_area) if camera == "cam1" else None
            ),
        })
    return deteccoes


def _linha_base(
    camera: str,
    tempo_s: float,
    frame_index: int,
    frame_pos_msec: float,
    n_deteccoes: int,
    pipeline: Any,
    modelo_sha256: str,
) -> dict:
    threshold = (
        float(pipeline._OPERADOR_CONF)
        if camera == "cam1" else float(pipeline._CAM2_CONF)
    )
    return {
        "diagnostico_tipo": DIAGNOSTICO_TIPO,
        "camera": camera,
        "tempo_s": f"{tempo_s:.3f}",
        "frame_index_lido": frame_index,
        "frame_pos_msec_apos_leitura": f"{frame_pos_msec:.3f}",
        "deteccao_encontrada": "false",
        "n_deteccoes_lowconf": n_deteccoes,
        "detection_index": "",
        "probe_conf": f"{PROBE_CONF:.3f}",
        "imgsz": PROBE_IMGSZ,
        "classes": _json_compacto(PROBE_CLASSES),
        "confidence": "",
        "bbox_xyxy": "",
        "area_ratio": "",
        "keypoints_n_validos": "",
        "keypoints_xyn_validos": "",
        "ancora_xy": "",
        "ancora_dentro_posto": "",
        "threshold_conf_producao": f"{threshold:.6f}",
        "passa_conf_producao": "",
        "operador_area_min_ratio": (
            f"{float(pipeline._OPERADOR_AREA_MIN_RATIO):.9f}"
            if camera == "cam1" else ""
        ),
        "passa_area_min_cam1": "",
        "passa_conf_e_area_cam1": "",
        "modelo": MODELO_ESPERADO,
        "modelo_sha256": modelo_sha256,
    }


def _linha_deteccao(base: dict, deteccao: dict) -> dict:
    linha = dict(base)
    linha.update({
        "deteccao_encontrada": "true",
        "detection_index": deteccao["detection_index"],
        "confidence": f"{deteccao['confidence']:.9f}",
        "bbox_xyxy": _json_compacto(
            [round(v, 3) for v in deteccao["bbox"]]
        ),
        "area_ratio": f"{deteccao['area_ratio']:.12f}",
        "keypoints_n_validos": len(deteccao["keypoints"]),
        "keypoints_xyn_validos": _json_compacto(deteccao["keypoints"]),
        "ancora_xy": _json_compacto(
            [round(v, 3) for v in deteccao["ancora"]]
        ),
        "ancora_dentro_posto": _celula_bool(deteccao["dentro_posto"]),
        "passa_conf_producao": _celula_bool(deteccao["passa_conf"]),
        "passa_area_min_cam1": _celula_bool(
            deteccao["passa_area_cam1"]
        ),
        "passa_conf_e_area_cam1": _celula_bool(
            deteccao["passa_conf_e_area_cam1"]
        ),
    })
    return linha


def _anotar_frame(cv2: Any, frame: Any, postos_px: dict, deteccoes: list[dict], camera: str, tempo_s: float) -> Any:
    anotado = frame.copy()
    for info in postos_px.values():
        cv2.polylines(anotado, [info["polygon"]], True, (255, 0, 255), 2)
    cv2.putText(
        anotado,
        f"{camera} t={tempo_s:.0f}s RAW predict conf={PROBE_CONF:.2f} (NAO REPLAY)",
        (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 2,
    )
    if not deteccoes:
        cv2.putText(
            anotado, "nenhuma pessoa em conf >= 0.05", (10, 46),
            cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 0, 255), 2,
        )
    for det in deteccoes:
        x1, y1, x2, y2 = (int(v) for v in det["bbox"])
        cor = (0, 220, 0) if det["dentro_posto"] else (0, 165, 255)
        cv2.rectangle(anotado, (x1, y1), (x2, y2), cor, 2)
        ax, ay = (int(round(v)) for v in det["ancora"])
        cv2.circle(anotado, (ax, ay), 5, cor, -1)
        corte = "passa_conf" if det["passa_conf"] else "abaixo_conf"
        cv2.putText(
            anotado,
            f"#{det['detection_index']} c={det['confidence']:.3f} {corte}",
            (max(0, x1), max(38, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.44, cor, 1,
        )
    return anotado


def _processar_camera(
    cv2: Any,
    pipeline: Any,
    yolo: Any,
    camera: str,
    video_path: Path,
    postos: dict,
    images_dir: Path | None,
    modelo_sha256: str,
) -> tuple[list[dict], int]:
    info_video = pipeline.inspecionar_video(str(video_path))
    largura = int(info_video["largura"])
    altura = int(info_video["altura"])
    _validar_frame_ref(postos, largura, altura, camera)
    postos_px = pipeline._build_rois(postos, largura, altura)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"não foi possível abrir {camera}: {video_path}")
    linhas: list[dict] = []
    imagens = 0
    try:
        for tempo_s in SLOTS_S:
            cap.set(cv2.CAP_PROP_POS_MSEC, tempo_s * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(
                    f"falha ao ler {camera} no slot {tempo_s:.0f}s"
                )
            h_frame, w_frame = frame.shape[:2]
            if int(w_frame) != largura or int(h_frame) != altura:
                raise RuntimeError(
                    f"dimensão inesperada em {camera}/{tempo_s:.0f}s: "
                    f"{w_frame}x{h_frame}, esperado {largura}x{altura}"
                )

            resultados = yolo.predict(
                frame,
                classes=PROBE_CLASSES,
                conf=PROBE_CONF,
                imgsz=PROBE_IMGSZ,
                verbose=False,
                save=False,
            )
            resultado = resultados[0] if resultados else None
            deteccoes = (
                _analisar_resultado(
                    resultado, pipeline, postos_px, camera, largura, altura
                )
                if resultado is not None else []
            )
            frame_index = int(round(float(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1))
            frame_pos_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            base = _linha_base(
                camera,
                tempo_s,
                frame_index,
                frame_pos_msec,
                len(deteccoes),
                pipeline,
                modelo_sha256,
            )
            if deteccoes:
                linhas.extend(_linha_deteccao(base, det) for det in deteccoes)
            else:
                linhas.append(base)

            if images_dir is not None:
                anotado = _anotar_frame(
                    cv2, frame, postos_px, deteccoes, camera, tempo_s
                )
                destino = images_dir / f"{camera}_{int(tempo_s):03d}s.jpg"
                if not cv2.imwrite(str(destino), anotado):
                    raise RuntimeError(f"falha ao salvar imagem: {destino}")
                imagens += 1
    finally:
        cap.release()
    return linhas, imagens


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe ortogonal low-confidence do detector bruto; NÃO é replay "
            "do pipeline e não altera thresholds."
        )
    )
    parser.add_argument("--cam1", type=Path, required=True)
    parser.add_argument("--cam2", type=Path, required=True)
    parser.add_argument("--zones-file", type=Path, default=ZONAS_PADRAO)
    parser.add_argument("--output", type=Path, required=True, help="CSV local")
    parser.add_argument(
        "--images-dir",
        type=Path,
        default=None,
        help="diretório local opcional para 14 JPGs anotados",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    args.cam1 = args.cam1.resolve()
    args.cam2 = args.cam2.resolve()
    args.zones_file = args.zones_file.resolve()
    args.output = args.output.resolve()
    args.images_dir = args.images_dir.resolve() if args.images_dir else None

    _validar_videos(args.cam1, args.cam2)
    try:
        postos = carregar_postos(args.zones_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"zonas inválidas: {exc}") from exc

    sys.path.insert(0, str(RAIZ_REPO))
    from backend import pipeline, worker
    import cv2

    _bloquear_integracoes(pipeline, worker)
    if pipeline.YOLO_MODEL != MODELO_ESPERADO:
        raise SystemExit(
            f"modelo do pipeline divergiu: {pipeline.YOLO_MODEL!r}"
        )
    thresholds_efetivos = {
        "cam1": float(pipeline._OPERADOR_CONF),
        "cam2": float(pipeline._CAM2_CONF),
    }
    divergentes = {
        camera: valor
        for camera, valor in thresholds_efetivos.items()
        if abs(valor - THRESHOLDS_ESPERADOS[camera]) > 1e-12
    }
    if divergentes:
        raise SystemExit(
            "thresholds efetivos divergem da pergunta diagnóstica "
            f"(esperado {THRESHOLDS_ESPERADOS}, recebido {thresholds_efetivos})"
        )

    yolo = worker._get_yolo()
    modelo_path = Path(worker._modelo_path(pipeline.YOLO_MODEL)).resolve()
    if not modelo_path.is_file():
        raise SystemExit(f"modelo carregado sem arquivo auditável: {modelo_path}")
    modelo_sha256 = _sha256(modelo_path)

    if args.images_dir is not None:
        args.images_dir.mkdir(parents=True, exist_ok=True)
    linhas = []
    imagens = 0
    for camera, caminho in (("cam1", args.cam1), ("cam2", args.cam2)):
        linhas_camera, imagens_camera = _processar_camera(
            cv2,
            pipeline,
            yolo,
            camera,
            caminho,
            postos[camera],
            args.images_dir,
            modelo_sha256,
        )
        linhas.extend(linhas_camera)
        imagens += imagens_camera

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as arq:
        escritor = csv.DictWriter(arq, fieldnames=CAMPOS_CSV)
        escritor.writeheader()
        escritor.writerows(linhas)

    print("ATENÇÃO: este resultado NÃO é replay nem decisão do pipeline.")
    print(
        f"Probe bruto: predict conf={PROBE_CONF:.2f}, imgsz={PROBE_IMGSZ}, "
        f"classes={PROBE_CLASSES}; thresholds de produção não foram alterados."
    )
    print(f"CSV: {args.output} ({len(linhas)} linhas)")
    if args.images_dir is not None:
        print(f"Imagens: {args.images_dir} ({imagens} JPGs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
