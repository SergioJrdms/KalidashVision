"""Testes offline: correção humana -> memória fresca -> próximas análises."""
import json
import unittest
from types import SimpleNamespace
from backend.human_learning import montar_licoes, carregar_licoes, gravar_validacao
from backend.productivity import classificar_observacao, indicador_produtividade_do_estado


class Q:
    def __init__(self, sb, table):
        self.sb, self.name = sb, table
        self.filters, self.bounds, self.payload, self.rule = {}, [], None, ""
    def select(self, *a): return self
    def eq(self, k, v): self.filters[k] = v; return self
    def order(self, *a, **kw): return self
    def limit(self, *a): return self
    def gte(self, k, v): self.bounds.append((k, v, True)); return self
    def lte(self, k, v): self.bounds.append((k, v, False)); return self
    def or_(self, rule): self.rule = rule; return self
    def update(self, payload): self.payload = payload; return self
    def execute(self):
        rows = []
        for e in self.sb.data[self.name]:
            if any(e.get(k) != v for k, v in self.filters.items()): continue
            if any((e.get(k, 0) < v if ge else e.get(k, 0) > v) for k, v, ge in self.bounds): continue
            if self.rule and (e.get("origem_validacao") == "humano" or e.get("categoria_lean_origem") == "humano"): continue
            if self.payload: e.update(self.payload)
            rows.append(dict(e))
        return SimpleNamespace(data=rows)
class SB:
    def __init__(self, rows, cats=None): self.data = {"eventos": rows, "comportamentos": cats or []}
    def table(self, name): return Q(self, name)

def evento(**kw):
    return dict({"id": "e", "empresa": "U", "processo": "T", "video_id": "v",
                 "principal": True, "pessoa_track_id": 1, "papel_pessoa": "operador",
                 "tempo_inicio_s": 0, "tempo_fim_s": 30,
                 "comportamento_label": "acompanhar_maquina", "descricao_bruta": "observa a peça",
                 "origem_validacao": "humano", "validado_humano": True,
                 "validacao_correto": True}, **kw)


class LearningTests(unittest.TestCase):
    def test_primeira_correcao_ensina_nome(self):
        self.assertIn("inspecionar_peca", montar_licoes([evento(label_corrigido="inspecionar_peca")], []))
    def test_confirmar_nome_nao_confirma_produtividade(self):
        self.assertIn('"produtividade_confirmada": null', montar_licoes([evento()], []))
    def test_categoria_humana_ensina_sem_validacao_evento(self):
        self.assertIn("IMPRODUTIVO", montar_licoes([], [{"label": "conversar", "categoria_lean": "desperdicio", "categoria_lean_origem": "humano"}]))
    def test_ia_nao_ensina_a_si_mesma(self):
        self.assertEqual(montar_licoes([evento(origem_validacao="auditoria")], [{"label": "x", "categoria_lean": "desperdicio", "categoria_lean_origem": "ia"}]), "")
    def test_descricao_invalida_nao_ensina(self):
        self.assertEqual(montar_licoes([evento(), evento(descricao_invalida=True)], []), "")
    def test_correcao_contraditoria_nao_viram_regra(self):
        self.assertEqual(montar_licoes([evento(label_corrigido="a"), evento(label_corrigido="b")], []), "")
    def test_descricao_longa_nao_quebra(self):
        self.assertIn("atividade_confirmada", montar_licoes([evento(descricao_bruta="x" * 900)], []))
    def test_leitura_fresca_isolada_e_revogavel(self):
        row = evento()
        sb = SB([row, evento(empresa="Outra", label_corrigido="nao_vazar")])
        self.assertNotIn("nao_vazar", carregar_licoes(sb, "U", "T"))
        row["label_corrigido"] = "medir_peca"
        self.assertIn("medir_peca", carregar_licoes(sb, "U", "T"))
        row["validado_humano"] = False
        self.assertEqual(carregar_licoes(sb, "U", "T"), "")
    def test_julgamento_individual_vence_catalogo(self):
        row = evento(produtividade_humana="IMPRODUTIVO", maos_maquina=True,
                     categoria_lean="valor_agregado", categoria_lean_origem="humano_rotulo")
        self.assertEqual(indicador_produtividade_do_estado(classificar_observacao(row)[0]), "improdutivo")
    def test_nao_inventa_operador(self):
        row = evento(papel_pessoa=None, produtividade_humana="PRODUTIVO")
        self.assertEqual(indicador_produtividade_do_estado(classificar_observacao(row)[0]), "sem_decisao")
    def test_propagacao_apenas_fontes_exatas(self):
        origem = evento(origem_validacao=None, validado_humano=False)
        raw = evento(id="raw", principal=False, origem_validacao="auditoria")
        protegido = evento(id="p", principal=False)
        fora = evento(id="fora", principal=False, origem_validacao="auditoria", tempo_fim_s=40)
        outro = evento(id="outro", principal=False, origem_validacao="auditoria", pessoa_track_id=2)
        sb = SB([origem, raw, protegido, fora, outro])
        update = {"label_corrigido": "medir_peca", "origem_validacao": "humano",
                  "validado_humano": True, "validacao_correto": True}
        self.assertEqual(gravar_validacao(sb, origem, update), 2)
        self.assertEqual(raw["label_corrigido"], "medir_peca")
        self.assertEqual(raw["origem_validacao"], "humano_fonte")
        for e in (protegido, fora, outro): self.assertNotIn("label_corrigido", e)
        gravar_validacao(sb, origem, dict(update, label_corrigido="inspecionar_peca"))
        self.assertEqual(raw["label_corrigido"], "inspecionar_peca")

if __name__ == "__main__": unittest.main()
