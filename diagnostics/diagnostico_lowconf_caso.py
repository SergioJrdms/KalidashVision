#!/usr/bin/env python3
"""Probe genérico, local e read-only do detector bruto em slots de um caso.

IMPORTANTE: isto NÃO é replay do pipeline e não toma decisão de presença. Cada
slot gera somente um seek independente por câmera e uma chamada ``predict`` em
confiança diagnóstica 0.05. Nenhum resultado retorna ao pipeline.

Convenção temporal pública:
    cam2_offset_s = início(cam2) - início(cam1)
    t_cam2 = t_cam1 - cam2_offset_s
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


DIRETORIO_DIAGNOSTICS = Path(__file__).resolve().parent
RAIZ_REPO = DIRETORIO_DIAGNOSTICS.parent
if str(DIRETORIO_DIAGNOSTICS) not in sys.path:
    sys.path.insert(0, str(DIRETORIO_DIAGNOSTICS))

import diagnostico_fp1_lowconf as base


PROBE_CONF = base.PROBE_CONF
PROBE_IMGSZ = base.PROBE_IMGSZ
PROBE_CLASSES = base.PROBE_CLASSES
MODELO_ESPERADO = base.MODELO_ESPERADO
THRESHOLDS_ESPERADOS = dict(base.THRESHOLDS_ESPERADOS)
DIAGNOSTICO_TIPO = "probe_detector_bruto_lowconf_caso_nao_replay_pipeline"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EPS = 1e-9

CAMPOS_CSV = (
    "diagnostico_tipo",
    "camera",
    "tempo_cam1_s",
    "tempo_camera_s",
    "cam2_offset_s",
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


def _normalizar_sha256(valor: str, nome: str) -> str:
    normalizado = str(valor or "").strip().lower()
    if not SHA256_RE.fullmatch(normalizado):
        raise SystemExit(f"{nome} precisa ser SHA-256 hexadecimal de 64 caracteres")
    return normalizado


def _validar_video(caminho: Path, esperado: str, camera: str) -> str:
    if not caminho.is_file():
        raise SystemExit(f"{camera} não encontrada: {caminho}")
    recebido = base._sha256(caminho)
    if recebido != esperado:
        raise SystemExit(
            f"SHA-256 divergente em {camera}: esperado {esperado}, recebido {recebido}"
        )
    return recebido


def _numero_finito(valor: float, nome: str) -> float:
    numero = float(valor)
    if not math.isfinite(numero):
        raise SystemExit(f"{nome} precisa ser finito")
    return numero


def parse_slots(valor: str) -> tuple[float, ...]:
    """Lê slots únicos, estritamente crescentes, no relógio da cam1."""
    texto = str(valor or "").strip()
    if not texto:
        raise argparse.ArgumentTypeError("--slots não pode ser vazio")
    partes = texto.split(",")
    if any(not parte.strip() for parte in partes):
        raise argparse.ArgumentTypeError(
            "--slots deve ser uma lista sem itens vazios, por exemplo 64,72,80"
        )
    try:
        slots = tuple(float(parte.strip()) for parte in partes)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--slots contém valor não numérico") from exc
    if any(not math.isfinite(slot) for slot in slots):
        raise argparse.ArgumentTypeError("--slots aceita somente valores finitos")
    if any(slot < 0 for slot in slots):
        raise argparse.ArgumentTypeError("--slots não aceita tempo negativo")
    if any(atual <= anterior for anterior, atual in zip(slots, slots[1:])):
        raise argparse.ArgumentTypeError(
            "--slots deve ser estritamente crescente e não pode ter duplicatas"
        )
    return slots


def cam2_tempo_s(tempo_cam1_s: float, cam2_offset_s: float) -> float:
    """Converte um slot no relógio da cam1 para o relógio do arquivo cam2."""
    return float(tempo_cam1_s) - float(cam2_offset_s)


def _duracao_video(info: dict, camera: str) -> float:
    duracao = float(info.get("duracao_s") or 0.0)
    if not math.isfinite(duracao) or duracao <= 0:
        raise SystemExit(f"duração inválida de {camera}: {duracao}")
    return duracao


def _ultimo_tempo_legivel(info: dict, camera: str) -> float:
    fps = float(info.get("fps") or 0.0)
    total_frames = int(info.get("total_frames") or 0)
    if not math.isfinite(fps) or fps <= 0 or total_frames <= 0:
        raise SystemExit(
            f"metadados insuficientes para validar seeks de {camera}: "
            f"fps={fps}, total_frames={total_frames}"
        )
    return (total_frames - 1) / fps


def _validar_cobertura_slots(
    slots: tuple[float, ...],
    cam2_offset_s: float,
    info_cam1: dict,
    info_cam2: dict,
) -> None:
    """Recusa o probe inteiro se qualquer um dos 2×N frames não existir."""
    _duracao_video(info_cam1, "cam1")
    _duracao_video(info_cam2, "cam2")
    ultimo_cam1 = _ultimo_tempo_legivel(info_cam1, "cam1")
    ultimo_cam2 = _ultimo_tempo_legivel(info_cam2, "cam2")
    fora = []
    for slot in slots:
        if slot > ultimo_cam1 + EPS:
            fora.append(
                f"cam1 slot={slot:.3f}s > último frame={ultimo_cam1:.3f}s"
            )
        tempo_cam2 = cam2_tempo_s(slot, cam2_offset_s)
        if tempo_cam2 < -EPS:
            fora.append(
                f"cam2 slot_cam1={slot:.3f}s mapeia para {tempo_cam2:.3f}s < 0"
            )
        elif tempo_cam2 > ultimo_cam2 + EPS:
            fora.append(
                f"cam2 slot_cam1={slot:.3f}s mapeia para {tempo_cam2:.3f}s "
                f"> último frame={ultimo_cam2:.3f}s"
            )
    if fora:
        raise SystemExit("slots fora da cobertura dos vídeos: " + "; ".join(fora))


def _validar_destinos(output: Path, images_dir: Path) -> None:
    """Evita sobrescrita e mistura silenciosa com artefatos de outra execução."""
    try:
        output.relative_to(images_dir)
        output_dentro_images = True
    except ValueError:
        output_dentro_images = False
    try:
        images_dir.relative_to(output)
        images_dentro_output = True
    except ValueError:
        images_dentro_output = False
    if output_dentro_images or images_dentro_output:
        raise SystemExit(
            "--output e --images-dir precisam ser caminhos separados e não aninhados"
        )
    if output.exists():
        raise SystemExit(f"--output já existe; recusei sobrescrever: {output}")
    if images_dir.exists():
        if not images_dir.is_dir():
            raise SystemExit(f"--images-dir existe e não é diretório: {images_dir}")
        try:
            primeiro = next(images_dir.iterdir())
        except StopIteration:
            primeiro = None
        if primeiro is not None:
            raise SystemExit(
                "--images-dir precisa estar vazio para não misturar execuções: "
                f"primeiro artefato={primeiro}"
            )


def _linha_base(
    camera: str,
    tempo_cam1_s: float,
    tempo_camera_s: float,
    cam2_offset_s: float,
    frame_index: int,
    frame_pos_msec: float,
    n_deteccoes: int,
    pipeline: Any,
    modelo_sha256: str,
) -> dict:
    linha = base._linha_base(
        camera,
        tempo_cam1_s,
        frame_index,
        frame_pos_msec,
        n_deteccoes,
        pipeline,
        modelo_sha256,
    )
    linha.pop("tempo_s", None)
    linha.update({
        "diagnostico_tipo": DIAGNOSTICO_TIPO,
        "tempo_cam1_s": f"{tempo_cam1_s:.3f}",
        "tempo_camera_s": f"{tempo_camera_s:.3f}",
        "cam2_offset_s": f"{cam2_offset_s:.3f}",
    })
    return linha


def _nome_imagem(
    camera: str,
    indice_slot: int,
    tempo_cam1_s: float,
    tempo_camera_s: float,
) -> str:
    cam1_ms = int(round(tempo_cam1_s * 1000.0))
    camera_ms = int(round(tempo_camera_s * 1000.0))
    return (
        f"{camera}_slot_{indice_slot + 1:02d}_"
        f"cam1_{cam1_ms:09d}ms_camera_{camera_ms:09d}ms.jpg"
    )


def _nomes_imagens_esperadas(
    slots: tuple[float, ...], cam2_offset_s: float
) -> set[str]:
    return {
        _nome_imagem(
            camera,
            indice_slot,
            tempo_cam1_s,
            (
                tempo_cam1_s
                if camera == "cam1"
                else cam2_tempo_s(tempo_cam1_s, cam2_offset_s)
            ),
        )
        for camera in ("cam1", "cam2")
        for indice_slot, tempo_cam1_s in enumerate(slots)
    }


def _anotar_frame(
    cv2: Any,
    frame: Any,
    postos_px: dict,
    deteccoes: list[dict],
    camera: str,
    tempo_cam1_s: float,
    tempo_camera_s: float,
) -> Any:
    anotado = base._anotar_frame(
        cv2,
        frame,
        postos_px,
        deteccoes,
        camera,
        tempo_camera_s,
    )
    cv2.putText(
        anotado,
        f"slot cam1={tempo_cam1_s:.3f}s | seek {camera}={tempo_camera_s:.3f}s",
        (10, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (255, 255, 255),
        2,
    )
    return anotado


def _processar_camera(
    cv2: Any,
    pipeline: Any,
    yolo: Any,
    camera: str,
    video_path: Path,
    postos: dict,
    info_video: dict,
    slots: tuple[float, ...],
    cam2_offset_s: float,
    images_dir: Path,
    modelo_sha256: str,
) -> tuple[list[dict], int]:
    largura = int(info_video["largura"])
    altura = int(info_video["altura"])
    base._validar_frame_ref(postos, largura, altura, camera)
    postos_px = pipeline._build_rois(postos, largura, altura)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"não foi possível abrir {camera}: {video_path}")
    linhas: list[dict] = []
    imagens = 0
    try:
        for indice_slot, tempo_cam1_s in enumerate(slots):
            tempo_camera_s = (
                tempo_cam1_s
                if camera == "cam1"
                else cam2_tempo_s(tempo_cam1_s, cam2_offset_s)
            )
            if not cap.set(cv2.CAP_PROP_POS_MSEC, tempo_camera_s * 1000.0):
                raise RuntimeError(
                    f"backend recusou seek em {camera}: "
                    f"slot_cam1={tempo_cam1_s:.3f}s, "
                    f"tempo_camera={tempo_camera_s:.3f}s"
                )
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError(
                    f"falha ao ler {camera}: slot_cam1={tempo_cam1_s:.3f}s, "
                    f"tempo_camera={tempo_camera_s:.3f}s"
                )
            h_frame, w_frame = frame.shape[:2]
            if int(w_frame) != largura or int(h_frame) != altura:
                raise RuntimeError(
                    f"dimensão inesperada em {camera}/slot {tempo_cam1_s:.3f}s: "
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
                base._analisar_resultado(
                    resultado,
                    pipeline,
                    postos_px,
                    camera,
                    largura,
                    altura,
                )
                if resultado is not None else []
            )
            frame_index = int(round(float(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1))
            frame_pos_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            linha_base = _linha_base(
                camera,
                tempo_cam1_s,
                tempo_camera_s,
                cam2_offset_s,
                frame_index,
                frame_pos_msec,
                len(deteccoes),
                pipeline,
                modelo_sha256,
            )
            if deteccoes:
                linhas.extend(
                    base._linha_deteccao(linha_base, deteccao)
                    for deteccao in deteccoes
                )
            else:
                linhas.append(linha_base)

            anotado = _anotar_frame(
                cv2,
                frame,
                postos_px,
                deteccoes,
                camera,
                tempo_cam1_s,
                tempo_camera_s,
            )
            destino = images_dir / _nome_imagem(
                camera,
                indice_slot,
                tempo_cam1_s,
                tempo_camera_s,
            )
            if not cv2.imwrite(str(destino), anotado):
                raise RuntimeError(f"falha ao salvar imagem: {destino}")
            imagens += 1
    finally:
        cap.release()
    return linhas, imagens


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe genérico low-confidence do detector bruto; NÃO é replay "
            "do pipeline e não altera thresholds nem presença."
        )
    )
    parser.add_argument("--cam1", type=Path, required=True)
    parser.add_argument("--cam2", type=Path, required=True)
    parser.add_argument(
        "--slots",
        type=parse_slots,
        required=True,
        help="lista crescente separada por vírgula no relógio da cam1",
    )
    parser.add_argument(
        "--cam2-offset-s",
        type=float,
        required=True,
        help="inicio(cam2)-inicio(cam1); +10 implica seek cam2=slot_cam1-10",
    )
    parser.add_argument("--sha256-cam1", required=True)
    parser.add_argument("--sha256-cam2", required=True)
    parser.add_argument("--zones-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="CSV local")
    parser.add_argument(
        "--images-dir",
        type=Path,
        required=True,
        help="diretório local para um JPG anotado por câmera-slot",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    args.cam1 = args.cam1.resolve()
    args.cam2 = args.cam2.resolve()
    args.zones_file = args.zones_file.resolve()
    args.output = args.output.resolve()
    args.images_dir = args.images_dir.resolve()
    args.cam2_offset_s = _numero_finito(
        args.cam2_offset_s, "--cam2-offset-s"
    )
    args.sha256_cam1 = _normalizar_sha256(args.sha256_cam1, "--sha256-cam1")
    args.sha256_cam2 = _normalizar_sha256(args.sha256_cam2, "--sha256-cam2")
    if args.output in {args.cam1, args.cam2, args.zones_file}:
        raise SystemExit("--output não pode sobrescrever uma entrada")
    _validar_destinos(args.output, args.images_dir)

    hashes = {
        "cam1": _validar_video(args.cam1, args.sha256_cam1, "cam1"),
        "cam2": _validar_video(args.cam2, args.sha256_cam2, "cam2"),
    }
    try:
        postos = base.carregar_postos(args.zones_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"zonas inválidas: {exc}") from exc

    if str(RAIZ_REPO) not in sys.path:
        sys.path.insert(0, str(RAIZ_REPO))
    from backend import pipeline, worker
    import cv2

    base._bloquear_integracoes(pipeline, worker)
    if pipeline.YOLO_MODEL != MODELO_ESPERADO:
        raise SystemExit(f"modelo do pipeline divergiu: {pipeline.YOLO_MODEL!r}")
    thresholds_efetivos = {
        "cam1": float(pipeline._OPERADOR_CONF),
        "cam2": float(pipeline._CAM2_CONF),
    }
    divergentes = {
        camera: valor
        for camera, valor in thresholds_efetivos.items()
        if (
            not math.isfinite(valor)
            or abs(valor - THRESHOLDS_ESPERADOS[camera]) > 1e-12
        )
    }
    if divergentes:
        raise SystemExit(
            "thresholds efetivos divergem da pergunta diagnóstica "
            f"(esperado {THRESHOLDS_ESPERADOS}, recebido {thresholds_efetivos})"
        )
    area_min_cam1 = float(pipeline._OPERADOR_AREA_MIN_RATIO)
    if not math.isfinite(area_min_cam1) or not 0.0 <= area_min_cam1 <= 1.0:
        raise SystemExit(
            "_OPERADOR_AREA_MIN_RATIO inválido no runtime: "
            f"{area_min_cam1!r}"
        )

    info_cam1 = pipeline.inspecionar_video(str(args.cam1))
    info_cam2 = pipeline.inspecionar_video(str(args.cam2))
    _validar_cobertura_slots(
        args.slots,
        args.cam2_offset_s,
        info_cam1,
        info_cam2,
    )

    yolo = worker._get_yolo()
    modelo_path = Path(worker._modelo_path(pipeline.YOLO_MODEL)).resolve()
    if not modelo_path.is_file():
        raise SystemExit(f"modelo carregado sem arquivo auditável: {modelo_path}")
    modelo_sha256 = base._sha256(modelo_path)

    args.images_dir.mkdir(parents=True, exist_ok=True)
    linhas: list[dict] = []
    imagens = 0
    for camera, caminho, info_video in (
        ("cam1", args.cam1, info_cam1),
        ("cam2", args.cam2, info_cam2),
    ):
        linhas_camera, imagens_camera = _processar_camera(
            cv2,
            pipeline,
            yolo,
            camera,
            caminho,
            postos[camera],
            info_video,
            args.slots,
            args.cam2_offset_s,
            args.images_dir,
            modelo_sha256,
        )
        linhas.extend(linhas_camera)
        imagens += imagens_camera

    imagens_esperadas = len(args.slots) * 2
    if imagens != imagens_esperadas:
        raise RuntimeError(
            f"quantidade inesperada de imagens: {imagens}, esperado {imagens_esperadas}"
        )
    nomes_esperados = _nomes_imagens_esperadas(args.slots, args.cam2_offset_s)
    itens_gerados = list(args.images_dir.iterdir())
    nomes_gerados = {item.name for item in itens_gerados if item.is_file()}
    if nomes_gerados != nomes_esperados or len(itens_gerados) != imagens_esperadas:
        raise RuntimeError(
            "conteúdo inesperado em --images-dir após a execução: "
            f"esperado={sorted(nomes_esperados)}, "
            f"recebido={sorted(item.name for item in itens_gerados)}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as arq:
        escritor = csv.DictWriter(arq, fieldnames=CAMPOS_CSV)
        escritor.writeheader()
        escritor.writerows(linhas)

    print("ATENÇÃO: este resultado NÃO é replay nem decisão do pipeline.")
    print(
        f"Probe bruto: {len(args.slots)} slots × 2 câmeras; "
        f"predict conf={PROBE_CONF:.2f}, imgsz={PROBE_IMGSZ}, "
        f"classes={PROBE_CLASSES}; thresholds de produção não foram alterados."
    )
    print(
        "Alinhamento: cam2_offset_s="
        f"{args.cam2_offset_s:+.3f}; t_cam2=t_cam1-cam2_offset_s"
    )
    print(
        "SHA-256 validados: "
        f"cam1={hashes['cam1']}; cam2={hashes['cam2']}"
    )
    print(f"CSV: {args.output} ({len(linhas)} linhas)")
    print(f"Imagens: {args.images_dir} ({imagens} JPGs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
