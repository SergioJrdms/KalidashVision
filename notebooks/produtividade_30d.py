"""Experimentos reproduziveis de produtividade nos eventos validados.

O modulo nunca procura nem abre arquivos com ``gabarito`` no nome. A verdade
usada aqui e uma *proxy de desenvolvimento*: comportamento confirmado/corrigido
por humano + categoria Lean do catalogo. Ela serve para minerar hipoteses e
fazer screening temporal, mas nao substitui o holdout binario independente.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import unicodedata
import zipfile

import numpy as np
import pandas as pd


P = "PRODUTIVO"
I = "IMPRODUTIVO"
A = "ABSTEM"
CLASSES = (P, I, A)


def normalizar_texto(valor: object) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


def normalizar_serie(serie: pd.Series) -> pd.Series:
    return serie.fillna("").astype(str).map(normalizar_texto)


def serie_bool(serie: pd.Series) -> pd.Series:
    return normalizar_serie(serie).isin({"true", "1", "sim", "yes"})


def _proibir_gabarito(caminho: Path) -> None:
    if "gabarito" in caminho.name.lower():
        raise ValueError("Arquivo de gabarito e proibido na etapa de desenvolvimento")


def localizar_fontes() -> tuple[Path, Path]:
    """Retorna (eventos_csv, catalogo_csv_ou_zip) sem tocar no holdout."""
    raiz_env = os.environ.get("KV_30D_DATA_DIR")
    candidatos = [
        Path(raiz_env) if raiz_env else None,
        Path("/content/drive/MyDrive/Spectra/produtividade"),
        Path.home() / "Downloads",
    ]
    nomes_eventos = (
        os.environ.get("KV_30D_EVENTS_FILE", "eventos_30d.csv"),
        "Supabase Snippet Untitled query (14).csv",
    )
    nomes_catalogo = (
        os.environ.get("KV_30D_CATALOG_FILE", "comportamentos.csv"),
        "drive-download-20260911T181952Z-1-001.zip",
    )
    for raiz in candidatos:
        if raiz is None:
            continue
        eventos = next((raiz / n for n in nomes_eventos if (raiz / n).is_file()), None)
        catalogo = next((raiz / n for n in nomes_catalogo if (raiz / n).is_file()), None)
        if eventos and catalogo:
            _proibir_gabarito(eventos)
            _proibir_gabarito(catalogo)
            return eventos.resolve(), catalogo.resolve()
    raise FileNotFoundError(
        "Defina KV_30D_DATA_DIR com eventos_30d.csv e comportamentos.csv "
        "(ou o ZIP de auditoria)."
    )


def carregar_fontes(
    eventos_path: Path | str | None = None,
    catalogo_path: Path | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[Path, Path]]:
    if eventos_path is None or catalogo_path is None:
        eventos_path, catalogo_path = localizar_fontes()
    eventos_path = Path(eventos_path)
    catalogo_path = Path(catalogo_path)
    _proibir_gabarito(eventos_path)
    _proibir_gabarito(catalogo_path)
    eventos = pd.read_csv(eventos_path, low_memory=False)
    if catalogo_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(catalogo_path) as pacote:
            with pacote.open("comportamentos.csv") as arquivo:
                catalogo = pd.read_csv(arquivo, low_memory=False)
    else:
        catalogo = pd.read_csv(catalogo_path, low_memory=False)
    return eventos, catalogo, (eventos_path, catalogo_path)


def _mapa_catalogo(catalogo: pd.DataFrame) -> pd.DataFrame:
    cat = catalogo.copy()
    for coluna in ("empresa", "processo"):
        cat[f"_{coluna}"] = normalizar_serie(cat[coluna])
    cat["_label"] = normalizar_serie(cat["label"])
    cat["_categoria"] = normalizar_serie(cat["categoria_lean"])
    # O contrato da tabela e unico por empresa/processo/label. Duplicata exata
    # nao pode multiplicar eventos; divergencia fica visivel na auditoria.
    return cat.drop_duplicates(["_empresa", "_processo", "_label"], keep="first")


def preparar_dataset(
    eventos: pd.DataFrame,
    catalogo: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Constroi proxy humana e contexto temporal sem vazamento do corrigido."""
    df = eventos.copy()
    for coluna in ("empresa", "processo"):
        df[f"_{coluna}"] = normalizar_serie(df[coluna])
    df["_label_pred"] = normalizar_serie(df["comportamento_label"])
    corrigido = normalizar_serie(df["label_corrigido"])
    df["_label_true"] = corrigido.where(corrigido != "", df["_label_pred"])

    mapa = _mapa_catalogo(catalogo)[
        ["_empresa", "_processo", "_label", "_categoria"]
    ]
    df = df.merge(
        mapa.rename(columns={"_label": "_label_pred", "_categoria": "_cat_pred"}),
        on=["_empresa", "_processo", "_label_pred"],
        how="left",
    ).merge(
        mapa.rename(columns={"_label": "_label_true", "_categoria": "_cat_true"}),
        on=["_empresa", "_processo", "_label_true"],
        how="left",
    )
    binario = {"valor_agregado": P, "desperdicio": I}
    df["y_baseline"] = df["_cat_pred"].map(binario)
    df["y_true"] = df["_cat_true"].map(binario)
    df["dia"] = pd.to_datetime(
        df["gravado_em"], errors="coerce", utc=True
    ).dt.strftime("%Y-%m-%d")
    df["_inicio"] = pd.to_numeric(df["tempo_inicio_s"], errors="coerce")
    df["_fim"] = pd.to_numeric(df["tempo_fim_s"], errors="coerce")
    df["peso_s"] = (df["_fim"] - df["_inicio"]).clip(lower=0).fillna(1.0)
    df["descricao_modelo"] = df["descricao_bruta"].fillna("").astype(str)

    # Contexto calculado sobre a populacao completa; nenhuma coluna corrigida
    # entra nessas features.
    df = df.sort_values(
        ["video_id", "pessoa_track_id", "_inicio"], kind="stable"
    ).reset_index(drop=True)
    grupo = df.groupby(["video_id", "pessoa_track_id"], dropna=False, sort=False)
    df["prev_y"] = grupo["y_baseline"].shift(1)
    df["next_y"] = grupo["y_baseline"].shift(-1)
    df["prev_label"] = grupo["_label_pred"].shift(1)
    df["next_label"] = grupo["_label_pred"].shift(-1)
    df["gap_prev_s"] = df["_inicio"] - grupo["_fim"].shift(1)
    df["gap_next_s"] = grupo["_inicio"].shift(-1) - df["_fim"]
    df["vizinho_p_30s"] = (
        ((df["prev_y"] == P) & df["gap_prev_s"].between(-1, 30))
        | ((df["next_y"] == P) & df["gap_next_s"].between(-1, 30))
    )
    df["persistencia_i_30s"] = (
        ((df["prev_y"] == I) & df["gap_prev_s"].between(-1, 30))
        | ((df["next_y"] == I) & df["gap_next_s"].between(-1, 30))
    )
    df["conflito_positivo"] = serie_bool(df["maos_maquina"]) | serie_bool(
        df["trabalho"]
    )

    humano = (
        (normalizar_serie(df["origem_validacao"]) == "humano")
        & serie_bool(df["validado_humano"])
        & serie_bool(df["validacao_correto"])
    )
    proxy = df[
        humano & df["y_true"].notna() & df["y_baseline"].notna()
    ].copy()
    proxy["ground_truth_kind"] = "comportamento_humano+catalogo_lean"
    return proxy, df


@dataclass(frozen=True)
class DivisaoTemporal:
    treino: pd.DataFrame
    calibracao: pd.DataFrame
    teste_interno: pd.DataFrame
    dias_treino: tuple[str, ...]
    dias_calibracao: tuple[str, ...]
    dias_teste: tuple[str, ...]


def dividir_por_dia(
    df: pd.DataFrame, dias_calibracao: int = 3, dias_teste: int = 5
) -> DivisaoTemporal:
    dias = sorted(d for d in df["dia"].dropna().unique())
    if len(dias) < dias_calibracao + dias_teste + 2:
        raise ValueError("Dias insuficientes para treino/calibracao/teste por grupo")
    teste = tuple(dias[-dias_teste:])
    calibracao = tuple(dias[-dias_teste - dias_calibracao : -dias_teste])
    treino = tuple(dias[: -dias_teste - dias_calibracao])
    return DivisaoTemporal(
        df[df["dia"].isin(treino)].copy(),
        df[df["dia"].isin(calibracao)].copy(),
        df[df["dia"].isin(teste)].copy(),
        treino,
        calibracao,
        teste,
    )


def normalizar_predicao(valor: object) -> str:
    texto = normalizar_texto(valor).upper()
    if texto in {"P", P}:
        return P
    if texto in {"I", I}:
        return I
    return A


def metricas(
    df: pd.DataFrame,
    predicao: pd.Series | np.ndarray | list,
    *,
    ponderar_tempo: bool = False,
) -> dict[str, float]:
    y = df["y_true"].map(normalizar_predicao).to_numpy()
    p = pd.Series(predicao, index=df.index).map(normalizar_predicao).to_numpy()
    w = (
        pd.to_numeric(df["peso_s"], errors="coerce").fillna(0).to_numpy()
        if ponderar_tempo
        else np.ones(len(df), dtype=float)
    )
    soma = lambda mascara: float(w[mascara].sum())
    tp_i = soma((p == I) & (y == I))
    fp_i = soma((p == I) & (y == P))
    fn_i = soma((p != I) & (y == I))
    tp_p = soma((p == P) & (y == P))
    fp_p = soma((p == P) & (y == I))
    total = soma(np.ones(len(df), dtype=bool))
    classificados = soma(np.isin(p, [P, I]))
    real_p = soma(y == P)
    precisao_i = tp_i / (tp_i + fp_i) if tp_i + fp_i else np.nan
    recall_i = tp_i / (tp_i + fn_i) if tp_i + fn_i else np.nan
    precisao_p = tp_p / (tp_p + fp_p) if tp_p + fp_p else np.nan
    return {
        "precision_I": precisao_i,
        "recall_I": recall_i,
        "f1_I": (
            2 * precisao_i * recall_i / (precisao_i + recall_i)
            if precisao_i + recall_i
            else np.nan
        ),
        "precision_P": precisao_p,
        "coverage": classificados / total if total else np.nan,
        "abstention": 1 - classificados / total if total else np.nan,
        "false_accusation_rate": fp_i / real_p if real_p else np.nan,
        "claims_I": int((p == I).sum()),
        "n": int(len(df)),
    }


def tabela_por_dia(df: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for dia, grupo in df.groupby("dia", sort=True):
        m = metricas(grupo, grupo["y_baseline"])
        linhas.append(
            {
                "dia": dia,
                "n": len(grupo),
                "verdade_I": int((grupo["y_true"] == I).sum()),
                "alegacoes_I": int((grupo["y_baseline"] == I).sum()),
                "acao_indefinida": int((grupo["_label_pred"] == "acao_indefinida").sum()),
                "versoes": ",".join(
                    sorted(set(normalizar_serie(grupo["versao_instrumento"])))
                ),
                **{k: m[k] for k in (
                    "precision_I", "recall_I", "precision_P", "coverage"
                )},
            }
        )
    return pd.DataFrame(linhas)


def minerar_categorias(
    df: pd.DataFrame,
    colunas: tuple[str, ...] = (
        "_label_pred",
        "prev_y",
        "next_y",
        "vizinho_p_30s",
        "persistencia_i_30s",
        "movimento_maquina",
        "cena_imovel",
        "versao_instrumento",
    ),
    suporte_minimo: int = 10,
) -> pd.DataFrame:
    """Mineracao somente nas alegacoes I do desenvolvimento."""
    base = df[df["y_baseline"] == I]
    linhas = []
    for coluna in colunas:
        for valor, grupo in base.groupby(coluna, dropna=False):
            if len(grupo) < suporte_minimo:
                continue
            linhas.append(
                {
                    "feature": coluna,
                    "valor": str(valor),
                    "n": len(grupo),
                    "dias": grupo["dia"].nunique(),
                    "precision_I": float((grupo["y_true"] == I).mean()),
                    "falsas_acusacoes": int((grupo["y_true"] == P).sum()),
                }
            )
    return pd.DataFrame(linhas).sort_values(
        ["precision_I", "n"], ascending=[True, False]
    )


def minerar_ngramas(
    df: pd.DataFrame,
    suporte_minimo: int = 10,
    max_features: int = 8000,
) -> pd.DataFrame:
    """Descobre termos associados a acerto/erro sem imprimir descricoes."""
    from sklearn.feature_extraction.text import CountVectorizer

    base = df[df["y_baseline"] == I].copy()
    vetor = CountVectorizer(
        ngram_range=(1, 2),
        min_df=suporte_minimo,
        max_features=max_features,
        binary=True,
        strip_accents="unicode",
    )
    x = vetor.fit_transform(base["descricao_modelo"])
    termos = vetor.get_feature_names_out()
    y = (base["y_true"] == I).to_numpy()
    linhas = []
    for j, termo in enumerate(termos):
        presente = x[:, j].toarray().ravel().astype(bool)
        n = int(presente.sum())
        linhas.append(
            {
                "termo": termo,
                "n": n,
                "dias": int(base.loc[presente, "dia"].nunique()),
                "precision_I": float(y[presente].mean()),
                "delta_vs_base": float(y[presente].mean() - y.mean()),
            }
        )
    return pd.DataFrame(linhas).sort_values(
        ["delta_vs_base", "n"], ascending=[True, False]
    )


def aplicar_regras(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["R0_catalogo"] = df["y_baseline"]

    r1 = df["y_baseline"].copy()
    r1[(r1 == I) & (df["_label_pred"] == "acao_indefinida")] = A
    out["R1_indefinida_abstem"] = r1

    r2 = df["y_baseline"].copy()
    r2[(r2 == I) & df["vizinho_p_30s"]] = A
    out["R2_veto_vizinho_produtivo"] = r2

    r3 = df["y_baseline"].copy()
    r3[(r3 == I) & ~df["persistencia_i_30s"]] = A
    out["R3_persistencia_I_30s"] = r3

    r4 = df["y_baseline"].copy()
    r4[
        (r4 == I)
        & ((df["_label_pred"] == "acao_indefinida") | df["vizinho_p_30s"])
    ] = A
    out["R4_indefinida_mais_veto"] = r4
    return out


def avaliar_politicas(df: pd.DataFrame, politicas: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for coluna in politicas.columns:
        linhas.append(
            {
                "politica": coluna,
                "unidade": "eventos",
                **metricas(df, politicas[coluna]),
            }
        )
        linhas.append(
            {
                "politica": coluna,
                "unidade": "tempo",
                **metricas(df, politicas[coluna], ponderar_tempo=True),
            }
        )
    return pd.DataFrame(linhas)


FEATURES_CATEGORICAS = [
    "_label_pred",
    "papel_pessoa",
    "trabalho",
    "orientacao",
    "maos_maquina",
    "modo_operacao",
    "movimento_maquina",
    "cena_maquina",
    "cena_imovel",
    "em_duvida",
    "versao_instrumento",
    "decidido_por",
    "prev_y",
    "next_y",
    "prev_label",
    "next_label",
]
FEATURES_NUMERICAS = [
    "peso_s",
    "n_amostras",
    "n_observacoes",
    "gap_prev_s",
    "gap_next_s",
]


def preparar_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for coluna in FEATURES_CATEGORICAS:
        out[coluna] = out[coluna].fillna("<NULL>").astype(str)
    for coluna in FEATURES_NUMERICAS:
        out[coluna] = pd.to_numeric(out[coluna], errors="coerce")
    return out


def criar_modelo(tipo: str):
    from sklearn.compose import ColumnTransformer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    blocos = []
    if tipo in {"estruturado", "combinado"}:
        blocos.extend(
            [
                (
                    "categorico",
                    OneHotEncoder(handle_unknown="ignore", min_frequency=3),
                    FEATURES_CATEGORICAS,
                ),
                (
                    "numerico",
                    Pipeline(
                        [
                            ("imputar", SimpleImputer(strategy="median")),
                            ("escalar", StandardScaler()),
                        ]
                    ),
                    FEATURES_NUMERICAS,
                ),
            ]
        )
    if tipo in {"texto", "combinado"}:
        blocos.append(
            (
                "texto",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=3,
                    max_features=12000,
                    sublinear_tf=True,
                    strip_accents="unicode",
                ),
                "descricao_modelo",
            )
        )
    if not blocos:
        raise ValueError(f"Tipo de modelo invalido: {tipo}")
    return Pipeline(
        [
            ("features", ColumnTransformer(blocos)),
            (
                "classificador",
                LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0),
            ),
        ]
    )


def probabilidade_i(modelo, df: pd.DataFrame) -> np.ndarray:
    indice = list(modelo.classes_).index(I)
    return modelo.predict_proba(df)[:, indice]


def predicao_seletiva(
    prob_i: np.ndarray, limiar_i: float, limiar_p: float
) -> np.ndarray:
    return np.where(prob_i >= limiar_i, I, np.where(prob_i <= limiar_p, P, A))


def buscar_limiares(
    df: pd.DataFrame,
    prob_i: np.ndarray,
    *,
    min_precision_i: float = 0.80,
    min_recall_i: float = 0.70,
    min_coverage: float = 0.60,
    max_queda_precision_p: float = 0.02,
    min_claims_i: int = 10,
) -> dict:
    base = metricas(df, df["y_baseline"])
    candidatos = []
    for limiar_i in np.arange(0.50, 0.961, 0.02):
        for limiar_p in np.arange(0.04, 0.501, 0.02):
            if limiar_p >= limiar_i:
                continue
            pred = predicao_seletiva(prob_i, limiar_i, limiar_p)
            m = metricas(df, pred)
            checagens = {
                "precision_I": bool(m["precision_I"] >= min_precision_i),
                "recall_I": bool(m["recall_I"] >= min_recall_i),
                "precision_P": bool(
                    m["precision_P"] >= base["precision_P"] - max_queda_precision_p
                ),
                "coverage": bool(m["coverage"] >= min_coverage),
                "claims_I": bool(m["claims_I"] >= min_claims_i),
            }
            passou = all(checagens.values())
            f1 = m["f1_I"] if np.isfinite(m["f1_I"]) else -1.0
            ranking = (
                int(passou),
                sum(checagens.values()),
                m["coverage"] if passou else f1,
                m["recall_I"],
                m["precision_I"] if np.isfinite(m["precision_I"]) else -1.0,
            )
            candidatos.append(
                (ranking, limiar_i, limiar_p, m, checagens, passou)
            )
    _ranking, limiar_i, limiar_p, m, checagens, passou = max(
        candidatos, key=lambda item: item[0]
    )
    return {
        "limiar_I": float(limiar_i),
        "limiar_P": float(limiar_p),
        "metricas": m,
        "checagens": checagens,
        "passou": passou,
    }


def resumo_dataset(proxy: pd.DataFrame, populacao: pd.DataFrame) -> dict:
    return {
        "eventos_exportados": int(len(populacao)),
        "eventos_proxy_humana": int(len(proxy)),
        "dias_proxy": int(proxy["dia"].nunique()),
        "videos_proxy": int(proxy["video_id"].nunique()),
        "verdade_P": int((proxy["y_true"] == P).sum()),
        "verdade_I": int((proxy["y_true"] == I).sum()),
        "ground_truth_kind": "comportamento_humano+catalogo_lean",
        "restricao": (
            "screening; nao e rotulo binario independente de produtividade"
        ),
    }
