"""Medição conservadora da cor da roupa superior do interlocutor."""
from __future__ import annotations

import os

import cv2
import numpy as np

from .productivity import CONFIANCA_COR_GESTOR_MIN


_SAT_CINZA_MAX = int(os.environ.get("KV_GESTOR_CINZA_SAT_MAX", "48"))
_VAL_CINZA_MIN = int(os.environ.get("KV_GESTOR_CINZA_VAL_MIN", "58"))
_VAL_CINZA_MAX = int(os.environ.get("KV_GESTOR_CINZA_VAL_MAX", "205"))
_PIXELS_MIN = int(os.environ.get("KV_GESTOR_CINZA_MIN_PIXELS", "400"))
_OMBRO_ESQ, _OMBRO_DIR, _QUADRIL_ESQ, _QUADRIL_DIR = 5, 6, 11, 12


def _incerto(motivo: str, **metricas) -> dict:
    return {
        "cor_superior": "incerto",
        "confianca_cor": 0.0,
        "qualidade": round(float(metricas.pop("qualidade", 0.0) or 0.0), 3),
        "pixels_utilizaveis": int(metricas.pop("pixels_utilizaveis", 0) or 0),
        "motivo_cor": motivo,
        **metricas,
    }


def _ponto(kpts, indice: int) -> tuple[float, float] | None:
    try:
        x, y = float(kpts[indice][0]), float(kpts[indice][1])
    except (TypeError, ValueError, IndexError):
        return None
    return (x, y) if 0.0 < x <= 1.0 and 0.0 < y <= 1.0 else None


def avaliar_roupa_superior(frame, bbox, *, kpts=None, exigir_pose=False) -> dict:
    """Retorna cinza, não-cinza ou incerto preservando S, V e qualidade.

    Em produção os ombros são obrigatórios: roupa oclusa, recorte pequeno,
    pouca luz ou evidência limítrofe nunca autorizam a classe ``cinza``.
    """
    try:
        h, w = frame.shape[:2]
        bx1, by1, bx2, by2 = (float(v) for v in bbox[:4])
    except (AttributeError, TypeError, ValueError, IndexError):
        return _incerto("crop_invalido")
    bw, bh = bx2 - bx1, by2 - by1
    if bw <= 0 or bh <= 0 or w <= 1 or h <= 1:
        return _incerto("crop_invalido")

    x1, y1 = max(0.0, bx1), max(0.0, by1)
    x2, y2 = min(float(w), bx2), min(float(h), by2)
    visivel = max(0.0, (x2 - x1) * (y2 - y1)) / max(1.0, bw * bh)
    if x2 <= x1 or y2 <= y1 or visivel < 0.85:
        return _incerto("bbox_cortado", qualidade=visivel)

    ombro_e = _ponto(kpts, _OMBRO_ESQ) if kpts is not None else None
    ombro_d = _ponto(kpts, _OMBRO_DIR) if kpts is not None else None
    if exigir_pose and (ombro_e is None or ombro_d is None):
        return _incerto("roupa_oculta", qualidade=visivel)
    if ombro_e and ombro_d:
        ox1, ox2 = sorted((ombro_e[0] * w, ombro_d[0] * w))
        if ox2 - ox1 < max(6.0, 0.12 * bw):
            return _incerto("ombros_insuficientes", qualidade=visivel)
        margem = 0.08 * bw
        rx1, rx2 = max(x1, ox1 - margem), min(x2, ox2 + margem)
        ry1 = max(y1, (ombro_e[1] + ombro_d[1]) * h / 2 + 0.02 * bh)
        quadris = (
            _ponto(kpts, _QUADRIL_ESQ), _ponto(kpts, _QUADRIL_DIR)
        )
        ry2 = (
            min(y2, (quadris[0][1] + quadris[1][1]) * h / 2 - 0.04 * bh)
            if all(quadris) else min(y2, y1 + 0.60 * bh)
        )
    else:  # fallback somente para o helper isolado/testes sintéticos
        margem = 0.16 * bw
        rx1, rx2 = x1 + margem, x2 - margem
        ry1, ry2 = y1 + 0.22 * bh, y1 + 0.60 * bh

    ix1, iy1 = max(0, round(rx1)), max(0, round(ry1))
    ix2, iy2 = min(w, round(rx2)), min(h, round(ry2))
    crop = frame[iy1:iy2, ix1:ix2]
    pixels = int(crop.shape[0] * crop.shape[1]) if getattr(crop, "size", 0) else 0
    qualidade = min(1.0, visivel * pixels / max(1, _PIXELS_MIN))
    if pixels < _PIXELS_MIN or crop.shape[0] < 12 or crop.shape[1] < 12:
        return _incerto(
            "crop_insuficiente", pixels_utilizaveis=pixels, qualidade=qualidade,
        )
    try:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        sat, val = hsv[:, :, 1].astype("float32"), hsv[:, :, 2].astype("float32")
        s_med, v_med = float(np.median(sat)), float(np.median(val))
        v10, v90 = float(np.percentile(val, 10)), float(np.percentile(val, 90))
        neutros = float(np.mean(sat <= _SAT_CINZA_MAX))
        compativeis = float(np.mean(
            (sat <= _SAT_CINZA_MAX) & (val >= _VAL_CINZA_MIN) & (val <= _VAL_CINZA_MAX)
        ))
        cromaticos = float(np.mean(sat >= 72))
        luminosidade_incompativel = max(
            float(np.mean(val >= 225)), float(np.mean(val <= 35)),
        )
    except Exception:  # medida opcional falha fechada
        return _incerto("medida_indisponivel", pixels_utilizaveis=pixels)

    metricas = {
        "saturacao_mediana": round(s_med, 1), "brilho_mediano": round(v_med, 1),
        "brilho_p10": round(v10, 1), "brilho_p90": round(v90, 1),
        "fracao_neutra": round(neutros, 3),
        "fracao_cinza_compativel": round(compativeis, 3),
        "pixels_utilizaveis": pixels, "qualidade": round(qualidade, 3),
    }
    if v_med <= 12 and v90 <= 20:
        return _incerto("iluminacao_insuficiente", **metricas)

    margem_v = min(
        1.0, max(0.0, (v_med - _VAL_CINZA_MIN) / 35.0),
        max(0.0, (_VAL_CINZA_MAX - v_med) / 35.0),
    )
    confianca = min(qualidade, 0.45 * neutros + 0.35 * compativeis + 0.20 * margem_v)
    if (
        neutros >= 0.82 and compativeis >= 0.72
        and _VAL_CINZA_MIN <= v_med <= _VAL_CINZA_MAX
        and confianca >= CONFIANCA_COR_GESTOR_MIN
    ):
        return {
            "cor_superior": "cinza", "confianca_cor": round(confianca, 3),
            "motivo_cor": "baixa_saturacao_e_brilho_intermediario", **metricas,
        }

    evidencia = max(cromaticos, luminosidade_incompativel)
    confianca = min(qualidade, evidencia)
    if evidencia >= 0.75 and confianca >= CONFIANCA_COR_GESTOR_MIN:
        return {
            "cor_superior": "nao_cinza", "confianca_cor": round(confianca, 3),
            "motivo_cor": (
                "cor_cromatica" if cromaticos >= luminosidade_incompativel
                else "luminosidade_incompativel"
            ),
            **metricas,
        }
    return _incerto("evidencia_ambigua", **metricas)
