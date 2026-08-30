#!/usr/bin/env python3
"""Diagnóstico local/read-only do FP #1 (cam1 + cam2, 120–175 s).

Este entrypoint não chama ``processar_video``. Ele orquestra diretamente as
mesmas etapas de detecção/tracking, segundo ângulo e confirmação de presença do
``backend.pipeline``. Assim, nenhum cliente Supabase/Groq é criado e nenhuma
etapa de VLM ou persistência fica alcançável.

O tracker começa em t=0 para conservar o mesmo histórico que teria no vídeo
completo. Somente o intervalo solicitado é escrito no CSV; o passe termina
logo depois do contexto futuro exigido pela ponte temporal de presença.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


RAIZ_REPO = Path(__file__).resolve().parents[1]
ZONAS_PADRAO = Path(__file__).with_name("fp1_zonas_camera_20260824.json")
ENV_OBRIGATORIO = {
    "KV_ZONA_ESTRITA": "on",
    "KV_FORA_DO_POSTO": "on",
    "KV_TRACKER": "reid",
}
VIDEOS_ESPERADOS_SHA256 = {
    "cam1": "12afc1d3f8fdb4ce7d47a76f991c5c01855b94bc2ceaa8771f4d5330aa1adc36",
    "cam2": "d68aeb8d0bee56ba1bfedd808d3789580ef9d176720f4b291718e17a9943cf26",
}


def _json_compacto(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as arq:
        for bloco in iter(lambda: arq.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _executar_texto(args: list[str], cwd: Path | None = None) -> str | None:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _normalizar_env(valor: str | None) -> str:
    return (valor or "").strip().lower()


def validar_env_obrigatorio() -> None:
    divergencias = []
    for nome, esperado in ENV_OBRIGATORIO.items():
        recebido = _normalizar_env(os.environ.get(nome))
        if recebido != esperado:
            divergencias.append(f"{nome}={recebido or '<ausente>'} (esperado {esperado})")
    if divergencias:
        raise SystemExit(
            "Ambiente recusado: " + "; ".join(divergencias)
            + ". Defina as variáveis antes de iniciar o Python, pois o pipeline "
              "congela essas opções durante o import."
        )


def _pts_rel(valor: Any, origem: str) -> list[list[float]]:
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except json.JSONDecodeError as exc:
            raise ValueError(f"pts_rel inválido em {origem}: {exc}") from exc
    if not isinstance(valor, list) or len(valor) < 3:
        raise ValueError(f"pts_rel precisa ter ao menos 3 pontos em {origem}")
    pontos: list[list[float]] = []
    for i, ponto in enumerate(valor):
        if not isinstance(ponto, (list, tuple)) or len(ponto) != 2:
            raise ValueError(f"ponto {i} inválido em {origem}")
        x, y = float(ponto[0]), float(ponto[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(f"ponto {i} fora de [0,1] em {origem}: {(x, y)}")
        pontos.append([x, y])
    return pontos


def _ativo(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    return _normalizar_env(str(valor)) in {"true", "1", "on", "yes", "sim"}


def _agrupar_linhas_zonas(linhas: list[dict]) -> dict[str, dict[str, dict]]:
    cameras: dict[str, dict[str, dict]] = {}
    for indice, linha in enumerate(linhas, start=1):
        if "ativo" in linha and not _ativo(linha.get("ativo")):
            continue
        cam_id = str(linha.get("cam_id") or "").strip()
        nome = str(linha.get("nome") or "").strip()
        papel = str(linha.get("papel") or "").strip()
        if not cam_id or not nome or not papel:
            raise ValueError(f"zona {indice} sem cam_id/nome/papel")
        info = {
            "pts_rel": _pts_rel(linha.get("pts_rel"), f"zona {cam_id}/{nome}"),
            "descricao_contexto": linha.get("descricao_contexto"),
            "papel": papel,
            "frente_maquina": linha.get("frente_maquina"),
        }
        for campo in ("frame_ref_w", "frame_ref_h", "criado_em", "atualizado_em"):
            if linha.get(campo) not in (None, ""):
                info[campo] = linha[campo]
        cameras.setdefault(cam_id, {})[nome] = info
    return cameras


def carregar_zonas(
    caminho: Path | None,
    cam1_id: str,
    cam2_id: str,
    cam1_pts: str | None,
    cam2_pts: str | None,
) -> tuple[dict[str, dict[str, dict]], str]:
    if bool(cam1_pts) != bool(cam2_pts):
        raise ValueError("--cam1-pts-rel e --cam2-pts-rel devem ser passados juntos")
    if cam1_pts and cam2_pts:
        linhas = [
            {
                "cam_id": cam1_id,
                "nome": "Posto do Torneiro",
                "papel": "posto_operador",
                "ativo": True,
                "pts_rel": cam1_pts,
            },
            {
                "cam_id": cam2_id,
                "nome": "Posto do Torneiro",
                "papel": "posto_operador",
                "ativo": True,
                "pts_rel": cam2_pts,
            },
        ]
        return _agrupar_linhas_zonas(linhas), "argumentos --*-pts-rel"

    if caminho is None:
        caminho = ZONAS_PADRAO
    caminho = caminho.resolve()
    if not caminho.is_file():
        raise FileNotFoundError(f"arquivo de zonas não encontrado: {caminho}")

    if caminho.suffix.lower() == ".csv":
        with caminho.open("r", encoding="utf-8-sig", newline="") as arq:
            linhas = list(csv.DictReader(arq))
        return _agrupar_linhas_zonas(linhas), str(caminho)

    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    if isinstance(bruto, dict) and isinstance(bruto.get("zonas_camera"), list):
        return _agrupar_linhas_zonas(bruto["zonas_camera"]), str(caminho)
    if isinstance(bruto, list):
        return _agrupar_linhas_zonas(bruto), str(caminho)
    if isinstance(bruto, dict):
        # Também aceita diretamente a estrutura do worker:
        # {cam_id: {nome: {pts_rel, papel, ...}}}.
        cameras: dict[str, dict[str, dict]] = {}
        for cam_id, zonas in bruto.items():
            if not isinstance(zonas, dict):
                continue
            cameras[str(cam_id)] = {}
            for nome, info in zonas.items():
                if not isinstance(info, dict):
                    raise ValueError(f"zona inválida em {cam_id}/{nome}")
                cameras[str(cam_id)][str(nome)] = {
                    **info,
                    "pts_rel": _pts_rel(info.get("pts_rel"), f"{cam_id}/{nome}"),
                }
        return cameras, str(caminho)
    raise ValueError(f"formato de zonas não reconhecido: {caminho}")


def validar_zonas(cameras: dict[str, dict], cam1_id: str, cam2_id: str) -> None:
    faltantes = []
    for cam_id in (cam1_id, cam2_id):
        zonas = cameras.get(cam_id) or {}
        if not any(z.get("papel") == "posto_operador" for z in zonas.values()):
            faltantes.append(cam_id)
    if faltantes:
        raise ValueError(
            "diagnóstico recusado: falta zona ativa posto_operador em "
            + ", ".join(faltantes)
        )


def validar_frame_ref(zonas: dict, largura: int, altura: int, cam_id: str) -> None:
    for nome, info in zonas.items():
        ref_w = info.get("frame_ref_w")
        ref_h = info.get("frame_ref_h")
        if ref_w in (None, "") or ref_h in (None, ""):
            continue
        if int(ref_w) != largura or int(ref_h) != altura:
            raise ValueError(
                f"zona {cam_id}/{nome} foi desenhada em {ref_w}x{ref_h}, "
                f"mas o vídeo tem {largura}x{altura}"
            )


def _bloquear_integracoes(pipeline) -> None:
    def proibido(*_args, **_kwargs):
        raise RuntimeError("integração externa proibida no diagnóstico FP #1")

    pipeline.make_supabase_client = proibido
    pipeline.make_groq_client = proibido
    pipeline.etapa_analise_vlm = proibido
    pipeline.etapa_persistir = proibido


def _progress(etapa: str, pct: int, mensagem: str) -> None:
    print(f"[{etapa} {pct:3d}%] {mensagem}", file=sys.stderr, flush=True)


def _estado_bool(valor: bool | None) -> str:
    if valor is True:
        return "presente"
    if valor is False:
        return "ausente"
    return "inconclusivo"


def _celula_bool(valor: bool | None) -> str:
    if valor is None:
        return ""
    return "true" if valor else "false"


def _indice_tempo(itens: list[dict], cam_id: str | None = None) -> dict[float, dict]:
    saida: dict[float, dict] = {}
    for item in itens:
        if cam_id is not None and str(item.get("cam_id")) != cam_id:
            continue
        saida[round(float(item["tempo_s"]), 3)] = item
    return saida


def _falhas_cobertura(
    tempos_relatorio: set[float],
    obs_cam1: dict[float, dict],
    obs_cam2: dict[float, dict],
) -> tuple[list[float], list[float]]:
    faltas_cam1 = sorted(
        t for t in tempos_relatorio
        if t not in obs_cam1 or obs_cam1[t].get("medido") is not True
    )
    falhas_cam2_esperadas = sorted(
        t for t in tempos_relatorio
        if t in obs_cam2
        and obs_cam2[t].get("medicao_esperada") is True
        and (
            obs_cam2[t].get("medido") is not True
            or bool(obs_cam2[t].get("erro"))
        )
    )
    return faltas_cam1, falhas_cam2_esperadas


def _detalhes_cam1(obs: dict | None) -> list[dict]:
    detalhes = []
    for tid, pessoa in sorted(
        ((obs or {}).get("pessoas") or {}).items(), key=lambda par: int(par[0])
    ):
        ancora = pessoa.get("ancora")
        detalhes.append({
            "track_id": int(tid),
            "bbox": list(pessoa.get("bbox") or []),
            "ancora": list(ancora) if ancora is not None else None,
            "dentro_posto": pessoa.get("estado") == "dentro",
        })
    return detalhes


def _versao_pacote(*nomes: str) -> str | None:
    for nome in nomes:
        try:
            return importlib.metadata.version(nome)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _pip_inventory() -> list[dict] | None:
    texto = _executar_texto(
        [sys.executable, "-m", "pip", "list", "--format=json"]
    )
    try:
        return json.loads(texto) if texto else None
    except json.JSONDecodeError:
        return None


def _env_kv_seguro() -> dict[str, str]:
    marcadores_sensiveis = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    return dict(sorted(
        (
            chave,
            "<redacted>"
            if any(m in chave.upper() for m in marcadores_sensiveis)
            else valor,
        )
        for chave, valor in os.environ.items()
        if chave.startswith("KV_")
    ))


def _manifesto(
    *,
    pipeline,
    worker,
    yolo,
    args,
    zonas: dict,
    origem_zonas: str,
    info_cam1: dict,
    info_cam2: dict,
    intervalo_s: float,
    fim_processamento_s: float,
    stats_presenca: dict,
    relatorio: Path,
) -> dict:
    modelo_path = Path(worker._modelo_path(pipeline.YOLO_MODEL)).resolve()
    tracker_path = Path(pipeline.TRACKER_CONFIG).resolve()
    try:
        import torch

        cuda = {
            "torch_cuda": torch.version.cuda,
            "cuda_disponivel": bool(torch.cuda.is_available()),
            "dispositivo": str(next(yolo.model.parameters()).device),
        }
    except Exception as exc:  # noqa: BLE001 — só telemetria
        cuda = {"erro": str(exc)}

    return {
        "caso": "FP #1",
        "modo": "local_read_only_sem_vlm_sem_supabase",
        "git": {
            "commit": _executar_texto(["git", "rev-parse", "HEAD"], RAIZ_REPO),
            "branch": _executar_texto(["git", "branch", "--show-current"], RAIZ_REPO),
            "status_porcelain": _executar_texto(["git", "status", "--porcelain"], RAIZ_REPO),
        },
        "runtime": {
            "python": sys.version,
            "executavel": sys.executable,
            "plataforma": platform.platform(),
            "maquina": platform.machine(),
            "ultralytics": _versao_pacote("ultralytics"),
            "torch": _versao_pacote("torch"),
            "opencv": getattr(pipeline.cv2, "__version__", None),
            "numpy": getattr(pipeline.np, "__version__", None),
            "pip_inventory": _pip_inventory(),
            **cuda,
        },
        "configuracao": {
            "env_kv": _env_kv_seguro(),
            "modelo": pipeline.YOLO_MODEL,
            "tracker_config": str(tracker_path),
            "yolo_conf_min": pipeline.YOLO_CONF_MIN,
            "operador_conf": pipeline._OPERADOR_CONF,
            "operador_area_min_ratio": pipeline._OPERADOR_AREA_MIN_RATIO,
            "cam2_conf": pipeline._CAM2_CONF,
            "cam2_confirm_stride": pipeline._CAM2_CONFIRM_STRIDE,
            "track_fps": float(os.environ.get("KV_TRACK_FPS", "6")),
            "imgsz": int(os.environ.get("KV_IMGSZ", "416")),
            "intervalo_amostragem_s": intervalo_s,
            "zona_estrita": pipeline._ZONA_ESTRITA,
            "fora_do_posto": pipeline._FORA_MODO,
            "operador_segmento": pipeline._OPERADOR_SEGMENTO_MODO,
            "autoridade_111d_configurada": pipeline.AUTORIDADE_111D_CONFIGURADA,
        },
        "escopo": {
            "relatorio_inicio_s": args.inicio,
            "relatorio_fim_s": args.fim,
            "processamento_inicio_s": 0.0,
            "processamento_fim_s": fim_processamento_s,
            "motivo_aquecimento": (
                "preservar histórico BoT-SORT/Re-ID desde o início e o contexto "
                "futuro da ponte temporal; somente 120–175 s é emitido"
            ),
            "resultado_presenca": (
                "resultado_presenca_pre_111d é a saída exata de "
                "etapa_confirmar_operador antes de VLM/111D; "
                "o diagnóstico causal mede detector, âncora, zona e resgate cam2"
            ),
        },
        "videos": {
            "cam1": {
                "path": str(args.cam1.resolve()),
                "sha256": _sha256(args.cam1),
                "info": info_cam1,
            },
            "cam2": {
                "path": str(args.cam2.resolve()),
                "sha256": _sha256(args.cam2),
                "info": info_cam2,
            },
        },
        "zonas": {"origem": origem_zonas, "por_camera": zonas},
        "artefatos": {
            "modelo_path": str(modelo_path),
            "modelo_sha256": _sha256(modelo_path) if modelo_path.is_file() else None,
            "tracker_sha256": _sha256(tracker_path) if tracker_path.is_file() else None,
            "relatorio_csv": str(relatorio),
            "relatorio_sha256": _sha256(relatorio),
        },
        "stats_presenca": stats_presenca,
        "guardas": [
            "processar_video não é chamado",
            "make_supabase_client bloqueado",
            "make_groq_client bloqueado",
            "etapa_analise_vlm bloqueada",
            "etapa_persistir bloqueada",
        ],
    }


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnóstico read-only FP #1 usando o detector/tracker/âncora/zona "
            "do backend.pipeline sem VLM nem Supabase."
        )
    )
    parser.add_argument("--cam1", type=Path, required=True, help="MP4 da cam1")
    parser.add_argument("--cam2", type=Path, required=True, help="MP4 da cam2")
    parser.add_argument("--cam1-id", default="cam1")
    parser.add_argument("--cam2-id", default="cam2")
    parser.add_argument("--inicio", type=float, default=120.0)
    parser.add_argument("--fim", type=float, default=175.0)
    parser.add_argument(
        "--intervalo",
        type=float,
        default=None,
        help="default: KV_INTERVALO_AMOSTRAGEM_S do próprio pipeline",
    )
    parser.add_argument(
        "--zones-file",
        type=Path,
        default=ZONAS_PADRAO,
        help="export JSON/CSV de zonas_camera (default: fixture real do FP #1)",
    )
    parser.add_argument(
        "--cam1-pts-rel",
        help="JSON [[x,y],...] alternativo para posto_operador da cam1",
    )
    parser.add_argument(
        "--cam2-pts-rel",
        help="JSON [[x,y],...] alternativo para posto_operador da cam2",
    )
    parser.add_argument("--output", type=Path, required=True, help="CSV de saída")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="JSON de paridade (default: mesmo nome do CSV com .manifest.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    args.cam1 = args.cam1.resolve()
    args.cam2 = args.cam2.resolve()
    args.output = args.output.resolve()
    args.manifest = (
        args.manifest.resolve()
        if args.manifest else args.output.with_suffix(".manifest.json")
    )

    if args.inicio < 0 or args.fim < args.inicio:
        raise SystemExit("intervalo inválido: exija 0 <= inicio <= fim")
    for rotulo, caminho in (("cam1", args.cam1), ("cam2", args.cam2)):
        if not caminho.is_file():
            raise SystemExit(f"{rotulo} não encontrado: {caminho}")
        recebido = _sha256(caminho)
        if recebido != VIDEOS_ESPERADOS_SHA256[rotulo]:
            raise SystemExit(
                f"{rotulo} não é o MP4 original do FP #1: SHA-256 {recebido}"
            )

    validar_env_obrigatorio()
    sys.path.insert(0, str(RAIZ_REPO))

    # Import somente depois de validar o ambiente: estas constantes são
    # materializadas durante o import do pipeline.
    from backend import pipeline
    from backend import worker

    _bloquear_integracoes(pipeline)
    if not pipeline._ZONA_ESTRITA or pipeline._FORA_MODO != "on":
        raise SystemExit("pipeline importado sem zona estrita/fora-do-posto ativos")
    tracker_path = Path(pipeline.TRACKER_CONFIG).resolve()
    if tracker_path.name != "botsort_camera_fixa_reid.yaml" or not tracker_path.is_file():
        raise SystemExit(f"KV_TRACKER=reid não resolveu o YAML real: {tracker_path}")
    if "with_reid: true" not in tracker_path.read_text(encoding="utf-8").lower():
        raise SystemExit(f"tracker selecionado não ativa Re-ID: {tracker_path}")

    try:
        zonas_por_cam, origem_zonas = carregar_zonas(
            args.zones_file,
            args.cam1_id,
            args.cam2_id,
            args.cam1_pts_rel,
            args.cam2_pts_rel,
        )
        validar_zonas(zonas_por_cam, args.cam1_id, args.cam2_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"zonas inválidas: {exc}") from exc

    info_cam1 = pipeline.inspecionar_video(str(args.cam1))
    info_cam2 = pipeline.inspecionar_video(str(args.cam2))
    validar_frame_ref(
        zonas_por_cam[args.cam1_id],
        int(info_cam1["largura"]),
        int(info_cam1["altura"]),
        args.cam1_id,
    )
    validar_frame_ref(
        zonas_por_cam[args.cam2_id],
        int(info_cam2["largura"]),
        int(info_cam2["altura"]),
        args.cam2_id,
    )

    intervalo_s = (
        float(args.intervalo)
        if args.intervalo is not None
        else float(pipeline.DEFAULT_INTERVALO_AMOSTRAGEM_S)
    )
    if intervalo_s <= 0:
        raise SystemExit("intervalo de amostragem precisa ser positivo")
    # A ponte usa até N slots futuros. Processá-los impede um artefato na borda
    # de 175 s sem rodar o restante do vídeo nem emitir linhas adicionais.
    fim_processamento_s = min(
        float(info_cam1["duracao_s"]),
        args.fim + pipeline._OPERADOR_GAP_SLOTS * intervalo_s,
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
            zonas_por_cam[args.cam1_id],
            _progress,
            cam_id=args.cam1_id,
            mapa_movimento=None,
            identidade_shadow=identidade_shadow,
            fim_s=fim_processamento_s,
        )
    )

    diagnostico_cam2: list[dict] = []
    offset_cam2 = pipeline._offset_entre_nomes(args.cam1.name, args.cam2.name)
    pipeline._anexar_segundo_angulo(
        amostras,
        str(args.cam2),
        yolo=yolo,
        rois_sec=zonas_por_cam[args.cam2_id],
        offset_s=offset_cam2,
        desc_acc={},  # mantém o reset da cam2 igual ao orquestrador de produção
        identidade_shadow=identidade_shadow,
        cam_id=args.cam2_id,
        diagnostico_presenca=diagnostico_cam2,
    )

    obs_cam1 = _indice_tempo(
        identidade_shadow.get("observacoes") or [], cam_id=args.cam1_id
    )
    obs_cam2 = _indice_tempo(diagnostico_cam2)
    tempos_relatorio = {
        round(float(am.tempo_s), 3)
        for am in amostras
        if args.inicio - 1e-9 <= float(am.tempo_s) <= args.fim + 1e-9
    }
    faltas_cam1, falhas_cam2_esperadas = _falhas_cobertura(
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

    campos = [
        "tempo_s",
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
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    linhas = 0
    with args.output.open("w", encoding="utf-8", newline="") as arq:
        escritor = csv.DictWriter(arq, fieldnames=campos)
        escritor.writeheader()
        for am in amostras:
            tempo = round(float(am.tempo_s), 3)
            if tempo < args.inicio - 1e-9 or tempo > args.fim + 1e-9:
                continue
            c1 = obs_cam1.get(tempo) or {}
            c2 = obs_cam2.get(tempo) or {}
            det1 = _detalhes_cam1(c1)
            det2 = c2.get("pessoas") or []
            medido2 = bool(c2.get("medido"))
            n_yolo1 = int(c1.get("n_deteccoes_yolo") or 0)
            n_yolo2 = int(c2.get("n_detectadas_yolo") or 0)
            escritor.writerow({
                "tempo_s": f"{tempo:.3f}",
                "cam1_pessoa_detectada": _celula_bool(n_yolo1 > 0),
                "cam1_n_detectadas_yolo": n_yolo1,
                "cam1_n_elegiveis_pipeline": int(
                    c1.get("n_elegiveis_pipeline") or 0
                ),
                "cam1_ancoras": _json_compacto(det1),
                "cam1_dentro_posto": _celula_bool(
                    any(d["dentro_posto"] for d in det1)
                ),
                "cam2_pessoa_detectada": _celula_bool(
                    (n_yolo2 > 0) if medido2 else None
                ),
                "cam2_medicao_esperada": _celula_bool(
                    c2.get("medicao_esperada") is True
                ),
                "cam2_motivo_sem_medicao": c2.get("motivo_sem_medicao") or "",
                "cam2_n_detectadas_yolo": n_yolo2 if medido2 else "",
                "cam2_ancoras": _json_compacto(det2) if medido2 else "",
                "cam2_dentro_posto": _celula_bool(
                    any(d["dentro_posto"] for d in det2) if medido2 else None
                ),
                "n_posto_cam2": (
                    am.n_posto_cam2 if am.n_posto_cam2 is not None else ""
                ),
                "op_cam2": _celula_bool(am.op_cam2),
                "resultado_presenca_pre_111d": _estado_bool(
                    am.operador_presente
                ),
                "operador_ponte": _celula_bool(bool(am.operador_ponte)),
            })
            linhas += 1

    if not linhas:
        raise SystemExit("nenhuma amostra caiu no intervalo solicitado")

    manifesto = _manifesto(
        pipeline=pipeline,
        worker=worker,
        yolo=yolo,
        args=args,
        zonas=zonas_por_cam,
        origem_zonas=origem_zonas,
        info_cam1=info_cam1,
        info_cam2=info_cam2,
        intervalo_s=intervalo_s,
        fim_processamento_s=fim_processamento_s,
        stats_presenca=stats_presenca,
        relatorio=args.output,
    )
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"CSV: {args.output}")
    print(f"Manifesto de paridade: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
