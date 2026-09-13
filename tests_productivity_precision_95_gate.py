"""Regressao do gate experimental de precisao R95."""

from backend import pipeline as pl
from backend import productivity as prod


def evento(label="conversando_colega", **campos):
    base = {
        "papel_pessoa": "operador",
        "comportamento_label": label,
        "trabalho": False,
        "n_amostras": 3,
        "em_duvida": False,
        "tempo_inicio_s": 0,
        "tempo_fim_s": 60,
    }
    base.update(campos)
    return base


def test_poucas_amostras_nao_acusam_improdutividade():
    predicao, regra = prod.classificar_produtividade_auditavel(
        evento(label="espera")
    )
    assert predicao == prod.AUDIT_ABSTEM
    assert regra == "improdutividade_incerta_abstencao_r95"


def test_quatro_amostras_liberam_a_decisao_binaria():
    predicao, regra = prod.classificar_produtividade_auditavel(
        evento(n_amostras=4)
    )
    assert predicao == prod.AUDIT_IMPRODUTIVO
    assert regra == "julgamento_visual_direto"


def test_duvida_veta_somente_a_futura_acusacao():
    assert prod.classificar_produtividade_auditavel(
        evento(label="espera", em_duvida=True)
    )[0] == prod.AUDIT_ABSTEM
    assert prod.classificar_produtividade_auditavel(
        evento(label="operar_torno", em_duvida=True, maos_maquina=True)
    )[0] == prod.AUDIT_PRODUTIVO


def test_acao_sem_nome_com_maos_recupera_coverage():
    sem_nome = evento(
        label="acao_indefinida",
        trabalho=None,
        maos_maquina=True,
    )
    assert prod.classificar_produtividade_auditavel(sem_nome) == (
        prod.AUDIT_PRODUTIVO,
        "acao_indefinida_maos_na_maquina_r8",
    )
    assert pl.decidir_permanencia(sem_nome, None)[0] == "valor_agregado"


def test_excecao_de_maos_nao_inventa_presenca():
    visitante = evento(
        label="acao_indefinida",
        papel_pessoa="visitante",
        trabalho=None,
        maos_maquina=True,
    )
    assert prod.classificar_produtividade_auditavel(visitante)[0] == (
        prod.AUDIT_ABSTEM
    )


def test_abstencao_de_produtividade_preserva_estado_de_presenca():
    duvidoso = evento(
        label="posto_vazio",
        papel_pessoa="posto_vazio",
        trabalho=None,
        n_amostras=0,
        em_duvida=True,
    )
    categoria, nivel, _motivo, estado = pl.decidir_permanencia(duvidoso, None)
    assert categoria is None
    assert nivel == "duvida"
    assert estado == pl.EST_FORA


def test_decisao_humana_resolve_o_veto_de_duvida():
    validado = evento(
        label="espera",
        em_duvida=True,
        origem_validacao="humano",
        validado_humano=True,
    )
    assert prod.classificar_produtividade_auditavel(validado)[0] == (
        prod.AUDIT_IMPRODUTIVO
    )


if __name__ == "__main__":
    testes = [obj for nome, obj in sorted(globals().items()) if nome.startswith("test_")]
    for teste in testes:
        teste()
        print(f"ok  {teste.__name__}")
    print(f"\n{len(testes)} testes passaram")
