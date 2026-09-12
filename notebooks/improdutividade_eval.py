"""Utilitários auditáveis dos notebooks de produtividade/improdutividade.

Sem dependência do backend e sem leitura de descrição como verdade. O módulo
mantém explícitas as três saídas do sistema: PRODUTIVO, IMPRODUTIVO e ABSTEM.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
from typing import Iterable

import numpy as np
import pandas as pd


P = "PRODUTIVO"
I = "IMPRODUTIVO"
A = "ABSTEM"
PREDICOES = (P, I, A)


def localizar_dados(*nomes: str) -> Path:
    """Localiza um diretório que contenha todos os CSVs solicitados."""
    raiz_env = os.environ.get("KV_EVAL_DATA_DIR")
    candidatos = [
        Path(raiz_env) if raiz_env else None,
        Path.cwd() / "output" / "execucao_drive_20260911",
        Path.cwd().parent / "output" / "execucao_drive_20260911",
        Path("/content/KalidashVision/output/execucao_drive_20260911"),
    ]
    for raiz in candidatos:
        if raiz and all((raiz / nome).is_file() for nome in nomes):
            return raiz.resolve()
    procurados = "\n".join(str(p) for p in candidatos if p)
    raise FileNotFoundError(
        f"Não encontrei {nomes}. Defina KV_EVAL_DATA_DIR. Procurei em:\n{procurados}"
    )


def divisao_segura(num: float, den: float) -> float:
    return float(num / den) if den > 0 else float("nan")


def normalizar_predicao(valor: object) -> str:
    texto = str(valor or "").strip().upper()
    aliases = {
        "P": P,
        "PRODUTIVA": P,
        "PRODUTIVO": P,
        "I": I,
        "IMPRODUTIVA": I,
        "IMPRODUTIVO": I,
        "ABSTAIN": A,
        "ABSTEM": A,
        "PENDENTE": A,
        "INCONCLUSIVO": A,
        "NAN": A,
        "NONE": A,
        "": A,
    }
    return aliases.get(texto, A)


def preparar_avaliacao(
    dados: pd.DataFrame,
    pred_col: str,
    true_col: str = "y_true",
    weight_col: str = "peso_s",
) -> pd.DataFrame:
    df = dados.copy()
    df["_pred"] = df[pred_col].map(normalizar_predicao)
    df["_true"] = df[true_col].map(normalizar_predicao)
    df = df[df["_true"].isin([P, I])].copy()
    if weight_col in df:
        df["_w"] = pd.to_numeric(df[weight_col], errors="coerce").fillna(0).clip(lower=0)
    else:
        df["_w"] = 1.0
    return df


def metricas_seletivas(
    dados: pd.DataFrame,
    pred_col: str,
    true_col: str = "y_true",
    weight_col: str = "peso_s",
) -> dict[str, float]:
    """Métricas ternárias ponderadas; abstenção conta como perda de recall."""
    df = preparar_avaliacao(dados, pred_col, true_col, weight_col)
    w = df["_w"]
    y = df["_true"]
    p = df["_pred"]
    soma = lambda mascara: float(w[mascara].sum())

    tp_i = soma((p == I) & (y == I))
    fp_i = soma((p == I) & (y == P))
    fn_i = soma((p != I) & (y == I))
    tp_p = soma((p == P) & (y == P))
    fp_p = soma((p == P) & (y == I))
    total = float(w.sum())
    classificados = soma(p.isin([P, I]))
    corretos = soma(((p == I) & (y == I)) | ((p == P) & (y == P)))
    real_p = soma(y == P)

    precision_i = divisao_segura(tp_i, tp_i + fp_i)
    recall_i = divisao_segura(tp_i, tp_i + fn_i)
    precision_p = divisao_segura(tp_p, tp_p + fp_p)
    return {
        "precision_I": precision_i,
        "recall_I": recall_i,
        "f1_I": divisao_segura(2 * precision_i * recall_i, precision_i + recall_i),
        "precision_P": precision_p,
        "coverage": divisao_segura(classificados, total),
        "abstention": divisao_segura(total - classificados, total),
        "selective_accuracy": divisao_segura(corretos, classificados),
        "operational_accuracy": divisao_segura(corretos, total),
        "false_accusation_rate": divisao_segura(fp_i, real_p),
        "claims_I": float(((p == I)).sum()),
        "claims_I_min": soma(p == I) / 60.0,
        "abstention_min": soma(p == A) / 60.0,
        "total_min": total / 60.0,
    }


def tabela_metricas(
    dados: pd.DataFrame,
    politicas: dict[str, str],
    weight_col: str = "peso_s",
) -> pd.DataFrame:
    linhas = []
    for nome, coluna in politicas.items():
        linhas.append({"politica": nome, **metricas_seletivas(dados, coluna, weight_col=weight_col)})
    return pd.DataFrame(linhas).set_index("politica")


def matriz_2x3(
    dados: pd.DataFrame,
    pred_col: str,
    weight_col: str | None = None,
) -> pd.DataFrame:
    df = preparar_avaliacao(dados, pred_col, weight_col=weight_col or "__contagem__")
    if weight_col is None:
        df["_w"] = 1.0
    tabela = df.pivot_table(
        index="_true", columns="_pred", values="_w", aggfunc="sum", fill_value=0,
    )
    return tabela.reindex(index=[P, I], columns=[P, I, A], fill_value=0)


def bootstrap_precision_i_cluster(
    dados: pd.DataFrame,
    pred_col: str,
    cluster_col: str = "video_id",
    weight_col: str = "peso_s",
    n: int = 4000,
    seed: int = 20260912,
) -> tuple[float, float, int]:
    """IC percentil, reamostrando episódios/vídeos inteiros."""
    df = dados.copy()
    grupos = [g for _, g in df.groupby(cluster_col, sort=False)]
    if len(grupos) < 2:
        return float("nan"), float("nan"), len(grupos)
    rng = np.random.default_rng(seed)
    valores = []
    for _ in range(n):
        amostra = pd.concat(
            [grupos[i] for i in rng.integers(0, len(grupos), len(grupos))],
            ignore_index=True,
        )
        valor = metricas_seletivas(amostra, pred_col, weight_col=weight_col)["precision_I"]
        if np.isfinite(valor):
            valores.append(valor)
    if not valores:
        return float("nan"), float("nan"), len(grupos)
    return tuple(np.quantile(valores, [0.025, 0.975])) + (len(grupos),)


def avaliar_guardrails(
    linha: pd.Series,
    baseline: pd.Series,
    min_claims_i: int = 20,
) -> dict[str, bool]:
    """Gate pré-registrado; NaN sempre reprova."""
    finito = lambda x: bool(np.isfinite(float(x)))
    return {
        "precision_I>=85%": finito(linha["precision_I"]) and linha["precision_I"] >= 0.85,
        "recall_I>=70%": finito(linha["recall_I"]) and linha["recall_I"] >= 0.70,
        "precision_P_queda<=2pp": (
            finito(linha["precision_P"]) and finito(baseline["precision_P"])
            and linha["precision_P"] >= baseline["precision_P"] - 0.02
        ),
        "coverage>=80%": finito(linha["coverage"]) and linha["coverage"] >= 0.80,
        "coverage_queda<=5pp": (
            finito(linha["coverage"]) and finito(baseline["coverage"])
            and linha["coverage"] >= baseline["coverage"] - 0.05
        ),
        "false_accusation<=5%": (
            finito(linha["false_accusation_rate"])
            and linha["false_accusation_rate"] <= 0.05
        ),
        "claims_I_suficientes": finito(linha["claims_I"]) and linha["claims_I"] >= min_claims_i,
    }


def classe_lean(valor: object) -> str:
    texto = str(valor or "").strip().lower()
    if texto == "valor_agregado":
        return P
    if texto == "desperdicio":
        return I
    return A


def episode_id(nome: object, gravado_em: object = None) -> str:
    """Une CAM1/CAM2 pelo token físico, sem usar o UUID de cada câmera."""
    achou = re.search(r"seg_(\d{8}_\d{6})", str(nome or ""), re.I)
    if achou:
        return achou.group(1)
    return str(gravado_em or nome or "sem_episodio")


def catalogo_candidatas() -> pd.DataFrame:
    return pd.DataFrame([
        ("C0", "baseline vigente", "controle", "estimável"),
        ("C1", "separar presença/identidade de atividade", "nivel/origem", "estimável parcialmente"),
        ("C2", "motivo negativo em whitelist", "produtividade_motivo", "não estimável no histórico"),
        ("C3", "veto por mãos/máquina ativa", "mãos+movimento+modo", "não estimável end-to-end"),
        ("C4", "persistência 2-de-3 do mesmo motivo", "quadros+track+motivo", "não estimável no agregado"),
        ("C5", "conversa só com interlocutor confirmado", "bbox_stats.interlocutor", "não estimável no CSV"),
        ("C6", "resgate de monitoramento/ponte por episódio", "movimento+CAM2+janela", "não estimável sem GT"),
    ], columns=["id", "regra", "evidencia_necessaria", "status_historico"]).set_index("id")


def percentuais(df: pd.DataFrame, colunas: Iterable[str]) -> pd.DataFrame:
    out = df[list(colunas)].copy()
    for c in out:
        out[c] = out[c].map(lambda x: f"{100*x:.2f}%" if pd.notna(x) else "indefinida")
    return out
