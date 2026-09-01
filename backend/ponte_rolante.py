"""Detecção conservadora e independente de operação da ponte rolante.

Esta camada usa somente frames RAW da CAM1 e Claude. Ela não participa de
presença, identidade, C1-C6, consolidação principal ou classificação Lean.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterable


log = logging.getLogger("kalidash.ponte_rolante")

PONTE_ROLANTE_LABEL = "operando_ponte_rolante"
PONTE_ROLANTE_TID = -6

FASES_PONTE_ROLANTE = {
    "engatando",
    "desengatando",
    "preparando_içamento",
    "içando",
    "baixando",
    "guiando_carga",
    "posicionando_carga",
    "controle_visivel",
    "nenhuma",
    "indeterminada",
}

PROMPT_PONTE_ROLANTE = """
Você está analisando uma SEQUÊNCIA CRONOLÓGICA de até 3 frames da CAM1 de
uma oficina de torneamento convencional.

Sua ÚNICA tarefa é decidir se existe evidência VISUAL de que a pessoa está
OPERANDO A PONTE ROLANTE / SISTEMA DE IÇAMENTO.

Definição positiva:
- engata ou desengata gancho, cinta, corrente ou linga na peça;
- manipula diretamente gancho/linga/cabo para preparar a carga;
- iça ou baixa uma peça;
- guia uma carga suspensa;
- posiciona uma peça suspensa no torno ou a retira usando o sistema de içamento.

O controle pendente/remoto visível ajuda, mas NÃO é obrigatório.

NÃO marque positivo só porque:
- existe ponte amarela no cenário;
- existe gancho parado;
- a pessoa está fora do posto;
- a pessoa está perto da ponte;
- a pessoa está perto de uma peça.

Se a evidência não for suficiente, seja conservador.

Retorne SOMENTE JSON:
{
  "operando_ponte_rolante": true | false | null,
  "fase": "engatando" | "desengatando" | "preparando_içamento" |
          "içando" | "baixando" | "guiando_carga" | "posicionando_carga" |
          "controle_visivel" | "nenhuma" | "indeterminada",
  "gancho_linga_visivel": true | false | null,
  "carga_suspensa": true | false | null,
  "evidencias_visuais": ["..."],
  "confianca": "alta" | "media" | "baixa"
}

Use null quando não der para decidir.
""".strip()


def _chamar_claude(frames_b64: list[str]) -> str:
    """Chama exclusivamente Claude, sem fallback para provider não validado."""
    from . import ai_provider

    return ai_provider.vision_call(
        frames_b64[0],
        PROMPT_PONTE_ROLANTE,
        imagens_extra=frames_b64[1:] or None,
        json_mode=True,
        max_tokens=700,
        temperatura=0.0,
        provedor="claude",
    )


def _bool_ou_none(valor):
    return valor if valor is True or valor is False else None


def analisar_janela_ponte(
    frames_b64: list[str],
    chamar_vlm: Callable[[list[str]], object] | None = None,
) -> dict | None:
    """Retorna diagnóstico somente quando Claude afirma ``true`` literalmente.

    False, null, resposta vazia, JSON inválido e qualquer exceção abstêm.
    """
    if not frames_b64:
        return None
    try:
        bruto = (chamar_vlm or _chamar_claude)(frames_b64)
        decisao = bruto if isinstance(bruto, dict) else json.loads(str(bruto))
        if not isinstance(decisao, dict):
            return None
        if decisao.get("operando_ponte_rolante") is not True:
            return None
    except Exception as exc:  # noqa: BLE001 - fronteira fail-closed por contrato
        log.warning("[ponte-rolante] janela inconclusiva: %s", exc)
        return None

    fase = str(decisao.get("fase") or "indeterminada")
    if fase not in FASES_PONTE_ROLANTE:
        fase = "indeterminada"
    evidencias = decisao.get("evidencias_visuais")
    if not isinstance(evidencias, list):
        evidencias = []
    evidencias = [str(e).strip() for e in evidencias if str(e).strip()]
    confianca = str(decisao.get("confianca") or "baixa").lower()
    if confianca not in {"alta", "media", "baixa"}:
        confianca = "baixa"

    return {
        "operando_ponte_rolante": True,
        "fase": fase,
        "gancho_linga_visivel": _bool_ou_none(decisao.get("gancho_linga_visivel")),
        "carga_suspensa": _bool_ou_none(decisao.get("carga_suspensa")),
        "evidencias_visuais": evidencias,
        "confianca": confianca,
    }


def montar_janelas_ponte(frames_grade: Iterable[tuple[float, str | None]]) -> list[dict]:
    """Monta janelas deslizantes de até três posições da grade temporal."""
    grade = [(float(t), img) for t, img in frames_grade]
    janelas: list[dict] = []
    for indice in range(len(grade)):
        trecho = grade[indice : indice + 3]
        frames = [img for _, img in trecho if img]
        if not frames:
            continue
        janelas.append({
            "inicio_s": trecho[0][0],
            "fim_s": trecho[-1][0],
            "tempos_s": [t for t, _ in trecho],
            "frames_b64": frames,
            "n_frames": len(frames),
        })
    return janelas


def _extrair_frames_raw(
    video_path: str,
    intervalo_s: float,
    duracao_s: float | None = None,
) -> list[tuple[float, str | None]]:
    """Lê diretamente o vídeo CAM1; nenhuma ``Amostra.img_b64`` é reutilizada."""
    import cv2
    from .pipeline import frame_para_base64

    if intervalo_s <= 0:
        return []
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        return []
    try:
        if duracao_s is None:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duracao_s = n_frames / fps if fps > 0 else 0.0
        grade: list[tuple[float, str | None]] = []
        tempo_s = 0.0
        while tempo_s < float(duracao_s or 0.0):
            cap.set(cv2.CAP_PROP_POS_MSEC, tempo_s * 1000.0)
            ok, frame = cap.read()
            grade.append((round(tempo_s, 3), frame_para_base64(frame) if ok else None))
            tempo_s += float(intervalo_s)
        return grade
    finally:
        cap.release()


def detectar_janelas_ponte(
    video_path: str,
    intervalo_s: float,
    duracao_s: float | None = None,
    *,
    chamar_vlm: Callable[[list[str]], object] | None = None,
    extrair_frames: Callable[[str, float, float | None], list[tuple[float, str | None]]] | None = None,
) -> list[dict]:
    """Analisa todas as janelas RAW e devolve somente afirmações positivas."""
    try:
        grade = (extrair_frames or _extrair_frames_raw)(
            video_path, float(intervalo_s), duracao_s
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[ponte-rolante] leitura RAW indisponível: %s", exc)
        return []

    positivos: list[dict] = []
    for janela in montar_janelas_ponte(grade):
        decisao = analisar_janela_ponte(janela["frames_b64"], chamar_vlm)
        if decisao is None:
            continue
        positivos.append({
            "inicio_s": janela["inicio_s"],
            "fim_s": janela["fim_s"],
            "n_frames": janela["n_frames"],
            "tempos_s": janela["tempos_s"],
            **decisao,
        })
    return positivos


def agrupar_episodios_ponte(janelas_positivas: Iterable[dict]) -> list[dict]:
    """Une somente janelas positivas que se sobrepõem ou encostam."""
    janelas = sorted(
        (j for j in janelas_positivas if j.get("operando_ponte_rolante") is True),
        key=lambda j: float(j["inicio_s"]),
    )
    episodios: list[dict] = []
    for janela in janelas:
        inicio = float(janela["inicio_s"])
        fim = float(janela["fim_s"])
        if not episodios or inicio > episodios[-1]["fim_s"]:
            episodios.append({
                "inicio_s": inicio,
                "fim_s": fim,
                "n_janelas": 1,
                "fases_observadas": [janela.get("fase") or "indeterminada"],
                "evidencias_visuais": list(janela.get("evidencias_visuais") or []),
                "janelas_origem": [(inicio, fim)],
            })
            continue
        episodio = episodios[-1]
        episodio["fim_s"] = max(float(episodio["fim_s"]), fim)
        episodio["n_janelas"] += 1
        episodio["janelas_origem"].append((inicio, fim))
        episodio["fases_observadas"].append(janela.get("fase") or "indeterminada")
        episodio["evidencias_visuais"].extend(janela.get("evidencias_visuais") or [])

    for episodio in episodios:
        episodio["duracao_s"] = round(episodio["fim_s"] - episodio["inicio_s"], 3)
        episodio["fases_observadas"] = list(dict.fromkeys(episodio["fases_observadas"]))
        episodio["evidencias_visuais"] = list(dict.fromkeys(episodio["evidencias_visuais"]))
    return episodios
