"""Offline weight-training and safety checks; not a production precision test."""
import copy
import unittest
from backend.online_learning import (Classificador, atributos, treinar,
                                      atualizar, invalidar, sugerir)
from backend.productivity import classificar_observacao
from backend.human_learning import carregar_licoes, gravar_validacao
from tests_human_learning import SB, evento


def dados():
    return [evento(id="a", descricao_bruta="mede a peça com instrumento",
                   comportamento_label="medir_peca", produtividade_humana="PRODUTIVO"),
            evento(id="b", descricao_bruta="consulta celular pessoal sem atividade operacional",
                   comportamento_label="usar_celular", produtividade_humana="IMPRODUTIVO")]


class TestPesos(unittest.TestCase):
    def tearDown(self):
        for scope in (("U", "T"), ("Outra", "T")):
            invalidar(*scope)

    def test_sgd_altera_pesos_e_predicao(self):
        m = Classificador(("I", "P"))
        x = {"medir": 1.0}
        before = m.distribuicao(x)["P"]
        m.partial_fit(x, "P")
        self.assertGreater(m.distribuicao(x)["P"], before)
        self.assertNotEqual(m.pesos["P"]["medir"], 0)

    def test_duas_tarefas_aprendem(self):
        models = treinar(dados())
        for task, target in (("cards", "medir_peca"), ("lean", "PRODUTIVO")):
            probs = models[task].distribuicao(atributos(dados()[0], task))
            self.assertEqual(max(probs, key=probs.get), target)
            self.assertGreater(models[task].exemplos, 0)

    def test_nome_nao_ensina_lean(self):
        model = treinar([evento()])["lean"]
        self.assertEqual(model.exemplos, 0)

    def test_primeira_validacao_lean_treina(self):
        self.assertGreater(treinar(dados()[:1])["lean"].exemplos, 0)

    def test_sem_dados_sem_predicao(self):
        atualizar("U", "T", [])
        self.assertIsNone(sugerir("U", "T", evento())["cards"]["sugestao"])

    def test_maquina_nao_treina(self):
        rows = [dict(e, origem_validacao="humano_fonte") for e in dados()]
        self.assertEqual(treinar(rows)["lean"].exemplos, 0)

    def test_alucinacao_queima_descricao(self):
        rows = dados() + [evento(descricao_bruta=dados()[0]["descricao_bruta"], descricao_invalida=True)]
        self.assertNotIn("medir_peca", treinar(rows)["cards"].classes)

    def test_conflitos_nao_treinam_por_maioria(self):
        rows = [dados()[0], dict(dados()[0], produtividade_humana="IMPRODUTIVO")]
        self.assertEqual(treinar(rows)["lean"].exemplos, 0)

    def test_categoria_humana_treina_sem_inventar_cena(self):
        cats = [{"label": "medir_peca", "categoria_lean": "valor_agregado", "categoria_lean_origem": "humano"}]
        models = treinar([], cats)
        self.assertGreater(models["lean"].exemplos, 0)
        self.assertEqual(models["cards"].exemplos, 0)

    def test_cache_isolado_por_empresa(self):
        atualizar("U", "T", dados())
        self.assertIsNone(sugerir("Outra", "T", evento()))
        self.assertIsNotNone(sugerir("U", "T", evento()))

    def test_reabrir_retira_pesos(self):
        rows = dados()
        sb = SB(rows)
        carregar_licoes(sb, "U", "T")
        old = sugerir("U", "T", rows[0])["snapshot"]
        gravar_validacao(sb, rows[0], {"validado_humano": False, "origem_validacao": None})
        new = sugerir("U", "T", rows[1])
        self.assertNotEqual(old, new["snapshot"])
        self.assertIsNone(new["cards"]["sugestao"])

    def test_nova_correcao_retreina_sem_reinicio(self):
        rows = dados()
        sb = SB(rows)
        carregar_licoes(sb, "U", "T")
        old = sugerir("U", "T", rows[0])["snapshot"]
        gravar_validacao(sb, rows[0], {"label_corrigido": "inspecionar_peca"})
        current = sugerir("U", "T", rows[0])
        self.assertNotEqual(old, current["snapshot"])
        self.assertEqual(current["cards"]["sugestao"], "inspecionar_peca")

    def test_somente_shadow_sem_mudar_indicadores(self):
        row = dados()[0]
        before = copy.deepcopy(row)
        decision = classificar_observacao(row)
        atualizar("U", "T", dados())
        row["bbox_stats"] = {"aprendizado_pesos_shadow": sugerir("U", "T", row)}
        self.assertEqual(classificar_observacao(row), decision)
        row.pop("bbox_stats")
        self.assertEqual(row, before)

    def test_presenca_indefinida_nao_ganha_predicao(self):
        atualizar("U", "T", dados())
        for role in (None, "posto_vazio", "visitante"):
            self.assertIsNone(sugerir("U", "T", evento(papel_pessoa=role)))

    def test_sem_vazamento_alvo_card(self):
        x = atributos(dados()[0], "cards")
        self.assertEqual(x, atributos(dict(dados()[0], label_corrigido="outro", produtividade_humana="IMPRODUTIVO"), "cards"))

    def test_replay_deterministico_sem_mutacao(self):
        rows = dados()
        before = copy.deepcopy(rows)
        self.assertEqual(treinar(rows)["lean"].pesos, treinar(rows)["lean"].pesos)
        self.assertEqual(rows, before)


if __name__ == "__main__":
    unittest.main()
