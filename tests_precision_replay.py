"""Regressão do replay interno de precisão."""
from backend.precision_replay import (
    A,
    I,
    P,
    calcular_metricas,
    carregar_manifesto,
    executar_replay,
    preparar_avaliacao,
    scope_fingerprint,
)


def evento(**mudancas):
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "video_id": "v1",
        "video_nome": "seg_teste.mp4",
        "gravado_em": "2026-08-12T10:00:00Z",
        "comportamento_label": "monitorar_maquina",
        "label_corrigido": None,
        "origem_validacao": "humano",
        "validado_humano": True,
        "validacao_correto": True,
        "papel_pessoa": "operador",
        "maos_maquina": False,
        "em_duvida": False,
        "n_amostras": 6,
        "versao_instrumento": 9,
        "tempo_inicio_s": 0,
        "tempo_fim_s": 60,
        "evidence_available": True,
    }
    base.update(mudancas)
    return base


def test_replay_e_somente_funcao_pura_e_respeita_o_escopo():
    manifesto = carregar_manifesto()
    assert scope_fingerprint("União", "Torneamento Convencional") == manifesto["scope_fingerprint"]
    original = evento()
    copia = dict(original)
    executar_replay([original])
    assert original == copia


def test_r1_abstem_e_r95_veta_so_improdutividade_insegura():
    manifesto = carregar_manifesto()
    linhas = preparar_avaliacao(
        [
            evento(id="r1", comportamento_label="acao_indefinida", n_amostras=8),
            evento(id="r8", comportamento_label="acao_indefinida", maos_maquina=True),
            evento(id="r95", comportamento_label="conversando_colega", n_amostras=2),
            evento(id="i-ok", comportamento_label="conversando_colega", n_amostras=4),
        ],
        manifesto,
    )
    por_id = {x["event_id"]: x for x in linhas}
    assert por_id["r1"]["current"] == A
    assert por_id["r8"]["current"] == P
    assert por_id["r95"]["current"] == A
    assert por_id["i-ok"]["current"] == I
    assert all(x["evidence_available"] for x in linhas)


def test_metricas_sao_recalculadas_da_matriz_e_nao_lidas_prontas():
    linhas = [
        {"truth": I, "current": I},
        {"truth": P, "current": I},
        {"truth": P, "current": P},
        {"truth": I, "current": A},
    ]
    m = calcular_metricas(linhas, "current")
    assert m["precision_improductive_pct"] == 50.0
    assert m["precision_productive_pct"] == 100.0
    assert m["coverage_pct"] == 75.0
    assert m["confusion"][I][A] == 1
