"""Regressão da política simples de precisão/coverage (R1)."""

from backend import pipeline as pl
from backend import productivity as prod


def evento(label="acao_indefinida", **campos):
    base = {
        "papel_pessoa": "operador",
        "comportamento_label": label,
        "trabalho": False,
        "principal": False,
        "tempo_inicio_s": 0,
        "tempo_fim_s": 60,
    }
    base.update(campos)
    return base


def test_r1_abstem_na_classificacao_auditavel():
    for label in ("acao_indefinida", "nao_nomeado"):
        predicao, regra = prod.classificar_produtividade_auditavel(evento(label))
        assert predicao == prod.AUDIT_ABSTEM
        assert regra == "acao_indefinida_abstencao_r1"


def test_r1_abstem_no_dashboard():
    categoria, nivel, _motivo, _estado = pl.decidir_permanencia(
        evento(), None
    )
    assert categoria is None
    assert nivel == "duvida"


def test_correcao_humana_resolve_a_acao_indefinida():
    corrigido = evento(
        label_corrigido="operar_torno",
        trabalho=True,
    )
    assert prod.classificar_produtividade_auditavel(corrigido)[0] == (
        prod.AUDIT_PRODUTIVO
    )


def test_ponte_confirmada_continua_produtiva():
    antigo_prod = prod._PONTE_SEGMENTO_DECIDE
    antigo_pipeline = pl._PONTE_SEGMENTO_DECIDE
    try:
        prod._PONTE_SEGMENTO_DECIDE = True
        pl._PONTE_SEGMENTO_DECIDE = True
        ponte = evento(categoria_lean_origem=pl.ORIGEM_PONTE_SEGMENTO)
        assert prod.classificar_produtividade_auditavel(ponte)[0] == (
            prod.AUDIT_PRODUTIVO
        )
        assert pl.decidir_permanencia(ponte, None)[0] == "valor_agregado"
    finally:
        prod._PONTE_SEGMENTO_DECIDE = antigo_prod
        pl._PONTE_SEGMENTO_DECIDE = antigo_pipeline


if __name__ == "__main__":
    testes = [
        test_r1_abstem_na_classificacao_auditavel,
        test_r1_abstem_no_dashboard,
        test_correcao_humana_resolve_a_acao_indefinida,
        test_ponte_confirmada_continua_produtiva,
    ]
    for teste in testes:
        teste()
        print(f"ok  {teste.__name__}")
    print(f"\n{len(testes)} testes passaram")
