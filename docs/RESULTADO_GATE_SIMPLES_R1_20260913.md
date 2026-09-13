# Resultado do gate simples R1 — 13/09/2026

## Decisão

Promover na branch de teste somente a regra **R1 — ação indefinida abstém**:
quando `comportamento_label` é `acao_indefinida` ou `nao_nomeado`, o evento
vai para validação em vez de votar como produtivo ou improdutivo. Uma correção
humana com uma ação real resolve a dúvida.

A regra já existente de ponte rolante continua acima da R1: um segmento
confirmado como operação de ponte permanece produtivo, inclusive quando o
operador está fora do centro da área de interesse.

## Resultado executado

| Parte | Política | Precisão improdutividade | Precisão produtividade | Coverage |
|---|---|---:|---:|---:|
| Calibração | Baseline | 33,63% | 98,27% | 100,00% |
| Calibração | **R1 escolhida** | **89,47%** | **98,27%** | **73,22%** |
| Calibração | R1 + veto de vizinho (R4) | 90,00% | 98,27% | 72,11% |
| Teste interno | Baseline | 91,46% | 98,22% | 100,00% |
| Teste interno | **R1 escolhida** | **91,46%** | **98,22%** | **100,00%** |
| Teste interno | R1 + veto de vizinho (R4) | 90,14% | 98,22% | 98,81% |

A R1 passa os três critérios nas duas partes. Na calibração, o coverage fica
1,78 ponto abaixo do ideal de 75%, mas 8,22 pontos acima do mínimo de 65%. No
teste interno, os três números ficam acima da meta, inclusive o coverage ideal.

## Por que não combinar com a R4

Na calibração, a segunda regra acrescentaria apenas 0,53 ponto de precisão de
improdutividade e custaria 1,11 ponto de coverage. No teste interno, ela faria
o contrário do desejado: reduziria a precisão de improdutividade em 1,32 ponto
e o coverage em 1,19 ponto. A R1 sozinha é a escolha mais simples e consistente.

## Relação com a reunião

A mudança materializa o caminho mais simples discutido pelo Iago e pelo César:
agrupar o principal padrão de erro, transformar esse padrão em guardrail e não
forçar uma classificação quando o próprio sistema diz que não conseguiu nomear
a ação. Também preserva a decisão explícita de que operação confirmada de ponte
rolante deve ser produtiva.

## Verificação

- notebook `10_Gate_Simples_R1_Precisao_Coverage.ipynb` executado com sucesso;
- 4 testes novos da R1 passaram;
- 34 testes do contrato de produtividade passaram;
- 70 testes de ação não nomeada passaram;
- suíte de ponte rolante passou integralmente;
- compilação do backend concluída sem erro.

O notebook grava localmente `resultado_gate_simples.csv`,
`decisao_gate_simples.json` e uma cópia executada em
`notebooks/outputs/productivity_simple_gate/`.
