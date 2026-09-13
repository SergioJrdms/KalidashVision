"""Matriz de robustez do gate R95 aplicado sobre a R1 em producao."""

from backend import pipeline as pl
from backend import productivity as prod


def evento(label="espera", **campos):
    base = {
        "papel_pessoa": "operador",
        "comportamento_label": label,
        "trabalho": False,
        "n_amostras": 4,
        "em_duvida": False,
        "tempo_inicio_s": 0,
        "tempo_fim_s": 60,
    }
    base.update(campos)
    return base


def test_limiar_de_quatro_amostras_independe_do_rotulo():
    labels = ("espera", "posto_vazio", "conversando_colega", "rotulo_novo")
    for label in labels:
        for n_amostras in range(4):
            assert prod.classificar_produtividade_auditavel(
                evento(label, n_amostras=n_amostras)
            )[0] == prod.AUDIT_ABSTEM
        assert prod.classificar_produtividade_auditavel(
            evento(label, n_amostras=4)
        )[0] == prod.AUDIT_IMPRODUTIVO


def test_duvida_veta_i_com_evidencia_numerosa():
    assert prod.classificar_produtividade_auditavel(
        evento(n_amostras=20, em_duvida=True)
    ) == (
        prod.AUDIT_ABSTEM,
        "improdutividade_incerta_abstencao_r95",
    )


def test_gate_nunca_veta_uma_decisao_produtiva():
    casos = (
        evento("operar_torno", n_amostras=0, em_duvida=True, trabalho=True),
        evento(
            "operar_torno",
            n_amostras=0,
            em_duvida=True,
            trabalho=False,
            maos_maquina=True,
        ),
    )
    for caso in casos:
        assert prod.classificar_produtividade_auditavel(caso)[0] == (
            prod.AUDIT_PRODUTIVO
        )


def test_maos_recuperam_sem_nome_somente_para_operador():
    esperado_por_papel = {
        "operador": prod.AUDIT_PRODUTIVO,
        "visitante": prod.AUDIT_ABSTEM,
        "posto_vazio": prod.AUDIT_ABSTEM,
        "operador_fora": prod.AUDIT_ABSTEM,
    }
    for papel, esperado in esperado_por_papel.items():
        caso = evento(
            "acao_indefinida",
            papel_pessoa=papel,
            trabalho=None,
            maos_maquina=True,
        )
        assert prod.classificar_produtividade_auditavel(caso)[0] == esperado


def test_correcao_humana_nomeada_supera_duvida_e_pouca_amostra():
    caso = evento(
        n_amostras=0,
        em_duvida=True,
        origem_validacao="humano",
        validado_humano=True,
        label_corrigido="espera",
    )
    assert prod.classificar_produtividade_auditavel(caso)[0] == (
        prod.AUDIT_IMPRODUTIVO
    )


def test_estado_de_presenca_e_invariante_ao_gate():
    for papel in ("operador", "visitante", "posto_vazio", "operador_fora"):
        caso = evento(papel_pessoa=papel, n_amostras=1, em_duvida=True)
        estado_antes = pl.estado_permanencia(caso, None)[0]
        categoria, nivel, _motivo, estado_depois = pl.decidir_permanencia(
            caso, None
        )
        assert estado_depois == estado_antes
        if papel in {"operador", "visitante", "posto_vazio"}:
            assert categoria is None
            assert nivel == "duvida"


def test_ponte_confirmada_continua_acima_do_gate():
    antigo_prod = prod._PONTE_SEGMENTO_DECIDE
    antigo_pipeline = pl._PONTE_SEGMENTO_DECIDE
    try:
        prod._PONTE_SEGMENTO_DECIDE = True
        pl._PONTE_SEGMENTO_DECIDE = True
        caso = evento(
            "acao_indefinida",
            n_amostras=0,
            em_duvida=True,
            trabalho=None,
            categoria_lean_origem=pl.ORIGEM_PONTE_SEGMENTO,
        )
        assert prod.classificar_produtividade_auditavel(caso)[0] == (
            prod.AUDIT_PRODUTIVO
        )
        assert pl.decidir_permanencia(caso, None)[0] == "valor_agregado"
    finally:
        prod._PONTE_SEGMENTO_DECIDE = antigo_prod
        pl._PONTE_SEGMENTO_DECIDE = antigo_pipeline


if __name__ == "__main__":
    testes = [obj for nome, obj in sorted(globals().items()) if nome.startswith("test_")]
    for teste in testes:
        teste()
        print(f"ok  {teste.__name__}")
    print(f"\n{len(testes)} grupos de robustez passaram")
