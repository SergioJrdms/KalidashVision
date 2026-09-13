# Resultado experimental do gate R95 — 13/09/2026

## Resultado

| Parte | Precisão improdutividade | Precisão produtividade | Coverage |
|---|---:|---:|---:|
| Calibração | **100,00%** | **96,36%** | **81,77%** |
| Teste interno | **97,56%** | **98,22%** | **95,58%** |

A combinação passa `95% / 95% / 65%` nas duas partes e também supera o
coverage ideal de 75%.

## Regras combinadas

1. A R1 continua abstendo quando a ação não foi nomeada.
2. Uma alegação de improdutividade abstém quando `em_duvida=true` ou quando há
   menos de quatro amostras realmente observadas.
3. Para recuperar coverage, uma ação ainda sem nome pode votar como produtiva
   somente quando `papel_pessoa=operador` e `maos_maquina=true`.

As regras 2 e 3 só alteram o eixo `PRODUTIVO / IMPRODUTIVO / ABSTEM`. O estado
de presença/identidade recebido pelo classificador é preservado.

## Impacto isolado e combinação

Na calibração, o veto R95 sozinho elevou a precisão de improdutividade de
89,47% para 100,00%, mas reduziu o coverage de 73,22% para 67,51%. A evidência
de mãos sozinha recuperou coverage para 87,48%, com produtividade em 96,36%.
Combinadas, as duas mantiveram os ganhos: 100,00% de improdutividade, 96,36% de
produtividade e 81,77% de coverage.

No teste interno, não havia ação sem nome elegível para a recuperação por mãos;
o resultado combinado foi 97,56% de improdutividade, 98,22% de produtividade e
95,58% de coverage.

## Status

Implementação e notebook estão na branch experimental
`exp/productivity-95-gate-20260913`. Esta rodada não foi promovida para
produção.
