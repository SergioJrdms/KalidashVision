#!/usr/bin/env python3
"""Runner genérico, local e read-only para diagnosticar presença em um caso.

Executa a mesma cadeia observacional validada no FP #1. Não chama
``processar_video`` e não acessa Supabase, Groq, VLM ou persistência.

Convenção pública do offset:
    cam2_offset_s = início(cam2) - início(cam1)
    t_cam2 = t_cam1 - cam2_offset_s

A função real ``_anexar_segundo_angulo`` usa a convenção inversa; por isso o
valor passado a ela é ``offset_s=-cam2_offset_s``.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
import sys
from pathlib import Path


DIRETORIO_DIAGNOSTICS = Path(__file__).resolve().parent
RAIZ_REPO = DIRETORIO_DIAGNOSTICS.parent
if str(DIRETORIO_DIAGNOSTICS) not in sys.path:
    sys.path.insert(0, str(DIRETORIO_DIAGNOSTICS))

import diagnostico_fp1_presenca as base


CAM1_ID = "cam1"
CAM2_ID = "cam2"
EPS = 1e-9
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CAMPOS_CSV = (
    "tempo_s",
    "cam2_tempo_s_alinhado",
    "cam2_cobertura_temporal",
    "cam1_pessoa_detectada",
    "cam1_n_detectadas_yolo",
    "cam1_n_elegiveis_pipeline",
    "cam1_ancoras",
    "cam1_dentro_posto",
    "cam2_pessoa_detectada",
    "cam2_medicao_esperada",
    "cam2_motivo_sem_medicao",
    "cam2_n_detectadas_yolo",
    "cam2_ancoras",
    "cam2_dentro_posto",
    "n_posto_cam2",
    "op_cam2",
    "resultado_presenca_pre_111d",
    "operador_ponte",
)


def _normalizar_sha256(valor: str, nome: str) -> str:
    normalizado = str(valor or "").strip().lower()
    if not SHA256_RE.fullmatch(normalizado):
        raise SystemExit(f"{nome} precisa ser SHA-256 hexadecimal de 64 caracteres")
    return normalizado


def _validar_numero_finito(valor: float, nome: str) -> float:
    numero = float(valor)
    if not math.isfinite(numero):
        raise SystemExit(f"{nome} precisa ser finito")
    return numero


def _validar_video(caminho: Path, esperado: str, camera: str) -> str:
    if not caminho.is_file():
        raise SystemExit(f"{camera} não encontrada: {caminho}")
    recebido = base._sha256(caminho)
    if recebido != esperado:
        raise SystemExit(
            f"SHA-256 divergente em {camera}: esperado {esperado}, recebido {recebido}"
        )
    return recebido


def cam2_tempo_s(cam1_tempo_s: float, cam2_offset_s: float) -> float:
    """Converte tempo relativo da cam1 para tempo relativo da cam2."""
    return float(cam1_tempo_s) - float(cam2_offset_s)


def offset_pipeline_s(cam2_offset_s: float) -> float:
    """Converte a convenção pública para a usada pelo pipeline real."""
    return -float(cam2_offset_s)


def calcular_cobertura(
    inicio_cam1_s: float,
    fim_cam1_s: float,
    cam2_offset_s: float,
    duracao_cam2_s: float,
) -> dict:
    """Descreve cobertura temporal da cam2 para uma faixa no relógio cam1."""
    alvo_inicio = cam2_tempo_s(inicio_cam1_s, cam2_offset_s)
    alvo_fim = cam2_tempo_s(fim_cam1_s, cam2_offset_s)
    antes = alvo_inicio < -EPS
    depois = alvo_fim > duracao_cam2_s + EPS
    sem_sobreposicao = (
        alvo_fim < -EPS or alvo_inicio > duracao_cam2_s + EPS
    )
    if sem_sobreposicao:
        status = "sem_cobertura"
    elif antes and depois:
        status = "parcial_inicio_e_fim"
    elif antes:
        status = "parcial_inicio"
    elif depois:
        status = "parcial_fim"
    else:
        status = "completa"

    inter_inicio = max(0.0, alvo_inicio)
    inter_fim = min(float(duracao_cam2_s), alvo_fim)
    segundos_cobertos = max(0.0, inter_fim - inter_inicio)
    return {
        "status": status,
        "faixa_cam1_s": [float(inicio_cam1_s), float(fim_cam1_s)],
        "faixa_alvo_cam2_s": [alvo_inicio, alvo_fim],
        "faixa_disponivel_cam2_s": [0.0, float(duracao_cam2_s)],
        "intersecao_cam2_s": (
            [inter_inicio, inter_fim] if inter_fim + EPS >= inter_inicio else None
        ),
        "sem_cobertura_inicio_s": max(0.0, -alvo_inicio),
        "sem_cobertura_fim_s": max(0.0, alvo_fim - duracao_cam2_s),
        "segundos_cobertos": segundos_cobertos,
    }


def _validar_cobertura_relatorio(cobertura: dict) -> None:
    alvo_inicio, _ = cobertura["faixa_alvo_cam2_s"]
    if alvo_inicio < -EPS:
        raise SystemExit(
            "intervalo recusado: o início pedido exige cam2 anterior ao arquivo; "
            f"cam2_tempo_inicio={alvo_inicio:.3f}s, cobertura={cobertura}"
        )
    if cobertura["status"] == "sem_cobertura":
        raise SystemExit(
            "intervalo recusado: nenhuma sobreposição temporal com a cam2; "
            f"cobertura={cobertura}"
        )


def _cobertura_slot(
    tempo_cam1_s: float,
    cam2_offset_s: float,
    duracao_cam2_s: float,
) -> tuple[float, str]:
    alvo = cam2_tempo_s(tempo_cam1_s, cam2_offset_s)
    if alvo < -EPS:
        return alvo, "fora_antes_inicio"
    if alvo > duracao_cam2_s + EPS:
        return alvo, "fora_depois_fim"
    return alvo, "coberta"


def _bloquear_integracoes(pipeline, worker) -> None:
    base._bloquear_integracoes(pipeline)

    def proibido(*_args, **_kwargs):
        raise RuntimeError("integração externa proibida no diagnóstico genérico")

    if hasattr(pipeline, "processar_video"):
        pipeline.processar_video = proibido
    for nome in (
        "make_supabase_client",
        "make_groq_client",
        "_baixar_video",
        "_buscar_zonas_por_cam",
        "executar_job",
    ):
        if hasattr(worker, nome):
            setattr(worker, nome, proibido)


def _manifesto(
    *,
    pipeline,
    worker,
    args,
    hashes: dict,
    zonas: dict,
    origem_zonas: str,
    info_cam1: dict,
    info_cam2: dict,
    intervalo_s: float,
    fim_processamento_s: float,
    cobertura_relatorio: dict,
    cobertura_processamento: dict,
    stats_presenca: dict,
    relatorio: Path,
) -> dict:
    modelo_path = Path(worker._modelo_path(pipeline.YOLO_MODEL)).resolve()
    tracker_path = Path(pipeline.TRACKER_CONFIG).resolve()
    return {
        "caso": "diagnostico_presenca_generico",
        "modo": "local_read_only_sem_vlm_sem_supabase",
        "git": {
            "commit": base._executar_texto(["git", "rev-parse", "HEAD"], RAIZ_REPO),
            "branch": base._executar_texto(
                ["git", "branch", "--show-current"], RAIZ_REPO
            ),
            "status_porcelain": base._executar_texto(
                ["git", "status", "--porcelain"], RAIZ_REPO
            ),
        },
        "runtime": {
            "python": sys.version,
            "executavel": sys.executable,
            "plataforma": platform.platform(),
            "ultralytics": base._versao_pacote("ultralytics"),
            "torch": base._versao_pacote("torch"),
            "opencv": getattr(pipeline.cv2, "__version__", None),
            "numpy": getattr(pipeline.np, "__version__", None),
            "pip_inventory": base._pip_inventory(),
        },
        "configuracao": {
            "env_kv": base._env_kv_seguro(),
            "modelo": pipeline.YOLO_MODEL,
            "tracker_config": str(tracker_path),
            "operador_conf": pipeline._OPERADOR_CONF,
            "operador_area_min_ratio": pipeline._OPERADOR_AREA_MIN_RATIO,
            "cam2_conf": pipeline._CAM2_CONF,
            "cam2_confirm_stride": pipeline._CAM2_CONFIRM_STRIDE,
            "intervalo_amostragem_s": intervalo_s,
            "zona_estrita": pipeline._ZONA_ESTRITA,
            "fora_do_posto": pipeline._FORA_MODO,
        },
        "alinhamento_temporal": {
            "semantica_cli": "cam2_offset_s = inicio(cam2) - inicio(cam1)",
            "formula": "t_cam2 = t_cam1 - cam2_offset_s",
            "cam2_offset_s_cli": args.cam2_offset_s,
            "offset_s_passado_pipeline": offset_pipeline_s(args.cam2_offset_s),
            "exemplos": {
                "cam1_10": cam2_tempo_s(10.0, args.cam2_offset_s),
                "cam1_60": cam2_tempo_s(60.0, args.cam2_offset_s),
                "cam1_120": cam2_tempo_s(120.0, args.cam2_offset_s),
            },
        },
        "cobertura": {
            "relatorio": cobertura_relatorio,
            "processamento_com_warmup_e_pos_roll": cobertura_processamento,
        },
        "escopo": {
            "relatorio_inicio_s": args.inicio,
            "relatorio_fim_s": args.fim,
            "processamento_inicio_s": 0.0,
            "processamento_fim_s": fim_processamento_s,
            "resultado_presenca": (
                "saída exata de etapa_confirmar_operador antes de VLM/111D; "
                "não é correção nem decisão nova de posto_vazio"
            ),
        },
        "videos": {
            "cam1": {
                "path": str(args.cam1),
                "sha256_esperado": args.sha256_cam1,
                "sha256_recebido": hashes["cam1"],
                "info": info_cam1,
            },
            "cam2": {
                "path": str(args.cam2),
                "sha256_esperado": args.sha256_cam2,
                "sha256_recebido": hashes["cam2"],
                "info": info_cam2,
            },
        },
        "zonas": {"origem": origem_zonas, "por_camera": zonas},
        "artefatos": {
            "modelo_path": str(modelo_path),
            "modelo_sha256": (
                base._sha256(modelo_path) if modelo_path.is_file() else None
            ),
            "tracker_sha256": (
                base._sha256(tracker_path) if tracker_path.is_file() else None
            ),
            "relatorio_csv": str(relatorio),
            "relatorio_sha256": base._sha256(relatorio),
        },
        "stats_presenca": stats_presenca,
        "guardas": [
            "processar_video bloqueado e não chamado",
            "Supabase/Groq/VLM/persistência bloqueados",
            "somente artefatos locais CSV/manifesto são escritos",
        ],
    }


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnóstico genérico read-only da cadeia real de presença, sem "
            "processar_video, VLM, Groq ou Supabase."
        )
    )
    parser.add_argument("--cam1", type=Path, required=True)
    parser.add_argument("--cam2", type=Path, required=True)
    parser.add_argument("--inicio", type=float, required=True)
    parser.add_argument("--fim", type=float, required=True)
    parser.add_argument(
        "--cam2-offset-s",
        type=float,
        required=True,
        help="inicio(cam2)-inicio(cam1); +10 significa cam2 começou 10 s depois",
    )
    parser.add_argument("--sha256-cam1", required=True)
    parser.add_argument("--sha256-cam2", required=True)
    parser.add_argument("--zones-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    args.cam1 = args.cam1.resolve()
    args.cam2 = args.cam2.resolve()
    args.zones_file = args.zones_file.resolve()
    args.output = args.output.resolve()
    args.inicio = _validar_numero_finito(args.inicio, "--inicio")
    args.fim = _validar_numero_finito(args.fim, "--fim")
    args.cam2_offset_s = _validar_numero_finito(
        args.cam2_offset_s, "--cam2-offset-s"
    )
    args.sha256_cam1 = _normalizar_sha256(args.sha256_cam1, "--sha256-cam1")
    args.sha256_cam2 = _normalizar_sha256(args.sha256_cam2, "--sha256-cam2")
    if args.inicio < 0 or args.fim < args.inicio:
        raise SystemExit("intervalo inválido: exija 0 <= inicio <= fim")

    hashes = {
        "cam1": _validar_video(args.cam1, args.sha256_cam1, CAM1_ID),
        "cam2": _validar_video(args.cam2, args.sha256_cam2, CAM2_ID),
    }
    base.validar_env_obrigatorio()
    if str(RAIZ_REPO) not in sys.path:
        sys.path.insert(0, str(RAIZ_REPO))
    from backend import pipeline, worker

    _bloquear_integracoes(pipeline, worker)
    if not pipeline._ZONA_ESTRITA or pipeline._FORA_MODO != "on":
        raise SystemExit("pipeline importado sem zona estrita/fora-do-posto ativos")
    tracker_path = Path(pipeline.TRACKER_CONFIG).resolve()
    if tracker_path.name != "botsort_camera_fixa_reid.yaml" or not tracker_path.is_file():
        raise SystemExit(f"KV_TRACKER=reid não resolveu o YAML real: {tracker_path}")
    if "with_reid: true" not in tracker_path.read_text(encoding="utf-8").lower():
        raise SystemExit(f"tracker selecionado não ativa Re-ID: {tracker_path}")

    try:
        zonas_por_cam, origem_zonas = base.carregar_zonas(
            args.zones_file, CAM1_ID, CAM2_ID, None, None
        )
        base.validar_zonas(zonas_por_cam, CAM1_ID, CAM2_ID)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"zonas inválidas: {exc}") from exc

    info_cam1 = pipeline.inspecionar_video(str(args.cam1))
    info_cam2 = pipeline.inspecionar_video(str(args.cam2))
    base.validar_frame_ref(
        zonas_por_cam[CAM1_ID],
        int(info_cam1["largura"]),
        int(info_cam1["altura"]),
        CAM1_ID,
    )
    base.validar_frame_ref(
        zonas_por_cam[CAM2_ID],
        int(info_cam2["largura"]),
        int(info_cam2["altura"]),
        CAM2_ID,
    )
    duracao_cam1_s = float(info_cam1["duracao_s"])
    duracao_cam2_s = float(info_cam2["duracao_s"])
    if not math.isfinite(duracao_cam1_s) or duracao_cam1_s <= 0:
        raise SystemExit(f"duração inválida da cam1: {duracao_cam1_s}")
    if not math.isfinite(duracao_cam2_s) or duracao_cam2_s <= 0:
        raise SystemExit(f"duração inválida da cam2: {duracao_cam2_s}")
    if args.fim > duracao_cam1_s + EPS:
        raise SystemExit(
            f"intervalo ultrapassa a cam1: fim={args.fim}, duração={duracao_cam1_s}"
        )

    cobertura_relatorio = calcular_cobertura(
        args.inicio, args.fim, args.cam2_offset_s, duracao_cam2_s
    )
    _validar_cobertura_relatorio(cobertura_relatorio)
    intervalo_s = float(pipeline.DEFAULT_INTERVALO_AMOSTRAGEM_S)
    if not math.isfinite(intervalo_s) or intervalo_s <= 0:
        raise SystemExit("intervalo de amostragem do pipeline precisa ser positivo")
    fim_processamento_s = min(
        duracao_cam1_s,
        args.fim + pipeline._OPERADOR_GAP_SLOTS * intervalo_s,
    )
    cobertura_processamento = calcular_cobertura(
        0.0, fim_processamento_s, args.cam2_offset_s, duracao_cam2_s
    )

    print(
        "ALINHAMENTO_CAM2 "
        f"cli={args.cam2_offset_s:+.3f}s "
        f"pipeline={offset_pipeline_s(args.cam2_offset_s):+.3f}s "
        "formula=t_cam2=t_cam1-cam2_offset_s",
        file=sys.stderr,
    )
    print(
        "COBERTURA_CAM2_RELATORIO "
        + json.dumps(cobertura_relatorio, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
    )
    print(
        "COBERTURA_CAM2_PROCESSAMENTO "
        + json.dumps(cobertura_processamento, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
    )

    yolo = worker._get_yolo()
    modelo_path = Path(worker._modelo_path(pipeline.YOLO_MODEL)).resolve()
    if not modelo_path.is_file():
        raise SystemExit(
            "modelo carregado sem arquivo auditável no caminho do worker: "
            f"{modelo_path}"
        )
    identidade_shadow = {
        "observacoes": [],
        "descritores": [],
        "guardar_detalhes": True,
        "guardar_frames": False,
    }
    (amostras, info_cam1, _ids, _descritores, _movimento, _grade) = (
        pipeline.etapa_detectar_e_amostrar(
            yolo,
            str(args.cam1),
            intervalo_s,
            zonas_por_cam[CAM1_ID],
            base._progress,
            cam_id=CAM1_ID,
            mapa_movimento=None,
            identidade_shadow=identidade_shadow,
            fim_s=fim_processamento_s,
        )
    )

    diagnostico_cam2: list[dict] = []
    pipeline._anexar_segundo_angulo(
        amostras,
        str(args.cam2),
        yolo=yolo,
        rois_sec=zonas_por_cam[CAM2_ID],
        offset_s=offset_pipeline_s(args.cam2_offset_s),
        desc_acc={},
        identidade_shadow=identidade_shadow,
        cam_id=CAM2_ID,
        diagnostico_presenca=diagnostico_cam2,
    )

    obs_cam1 = base._indice_tempo(
        identidade_shadow.get("observacoes") or [], cam_id=CAM1_ID
    )
    obs_cam2 = base._indice_tempo(diagnostico_cam2)
    tempos_relatorio = {
        round(float(am.tempo_s), 3)
        for am in amostras
        if args.inicio - EPS <= float(am.tempo_s) <= args.fim + EPS
    }
    faltas_cam1, falhas_cam2_esperadas = base._falhas_cobertura(
        tempos_relatorio, obs_cam1, obs_cam2
    )
    if faltas_cam1 or falhas_cam2_esperadas:
        raise SystemExit(
            "cobertura inválida no intervalo: "
            f"cam1_sem_medida={faltas_cam1}; "
            f"cam2_falha_em_slot_esperado={falhas_cam2_esperadas}"
        )

    stats_presenca = pipeline.etapa_confirmar_operador(
        amostras, pipeline._OPERADOR_CONFIRMACAO
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    linhas = 0
    with args.output.open("w", encoding="utf-8", newline="") as arq:
        escritor = csv.DictWriter(arq, fieldnames=CAMPOS_CSV)
        escritor.writeheader()
        for am in amostras:
            tempo = round(float(am.tempo_s), 3)
            if tempo < args.inicio - EPS or tempo > args.fim + EPS:
                continue
            c1 = obs_cam1.get(tempo) or {}
            c2 = obs_cam2.get(tempo) or {}
            det1 = base._detalhes_cam1(c1)
            det2 = c2.get("pessoas") or []
            medido2 = bool(c2.get("medido"))
            n_yolo1 = int(c1.get("n_deteccoes_yolo") or 0)
            n_yolo2 = int(c2.get("n_detectadas_yolo") or 0)
            tempo_cam2, cobertura_slot = _cobertura_slot(
                tempo, args.cam2_offset_s, duracao_cam2_s
            )
            escritor.writerow({
                "tempo_s": f"{tempo:.3f}",
                "cam2_tempo_s_alinhado": f"{tempo_cam2:.3f}",
                "cam2_cobertura_temporal": cobertura_slot,
                "cam1_pessoa_detectada": base._celula_bool(n_yolo1 > 0),
                "cam1_n_detectadas_yolo": n_yolo1,
                "cam1_n_elegiveis_pipeline": int(
                    c1.get("n_elegiveis_pipeline") or 0
                ),
                "cam1_ancoras": base._json_compacto(det1),
                "cam1_dentro_posto": base._celula_bool(
                    any(d["dentro_posto"] for d in det1)
                ),
                "cam2_pessoa_detectada": base._celula_bool(
                    (n_yolo2 > 0) if medido2 else None
                ),
                "cam2_medicao_esperada": base._celula_bool(
                    c2.get("medicao_esperada") is True
                ),
                "cam2_motivo_sem_medicao": c2.get("motivo_sem_medicao") or "",
                "cam2_n_detectadas_yolo": n_yolo2 if medido2 else "",
                "cam2_ancoras": base._json_compacto(det2) if medido2 else "",
                "cam2_dentro_posto": base._celula_bool(
                    any(d["dentro_posto"] for d in det2) if medido2 else None
                ),
                "n_posto_cam2": (
                    am.n_posto_cam2 if am.n_posto_cam2 is not None else ""
                ),
                "op_cam2": base._celula_bool(am.op_cam2),
                "resultado_presenca_pre_111d": base._estado_bool(
                    am.operador_presente
                ),
                "operador_ponte": base._celula_bool(bool(am.operador_ponte)),
            })
            linhas += 1

    if not linhas:
        raise SystemExit("nenhuma amostra caiu no intervalo solicitado")

    manifesto_path = args.output.with_suffix(".manifest.json")
    manifesto = _manifesto(
        pipeline=pipeline,
        worker=worker,
        args=args,
        hashes=hashes,
        zonas=zonas_por_cam,
        origem_zonas=origem_zonas,
        info_cam1=info_cam1,
        info_cam2=info_cam2,
        intervalo_s=intervalo_s,
        fim_processamento_s=fim_processamento_s,
        cobertura_relatorio=cobertura_relatorio,
        cobertura_processamento=cobertura_processamento,
        stats_presenca=stats_presenca,
        relatorio=args.output,
    )
    manifesto_path.write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"CSV: {args.output}")
    print(f"Manifesto: {manifesto_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
