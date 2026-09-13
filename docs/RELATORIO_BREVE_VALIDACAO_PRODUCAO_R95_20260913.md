# Validação breve de produção — R1 + R95

## Resultado

Comparação entre o primeiro baseline legado, medido em 111 janelas julgáveis de
24/08/2026, e o replay do código hoje em produção sobre **1.558 eventos recentes
validados**:

| Cenário | Precisão improdutividade | Precisão produtividade | Coverage |
|---|---:|---:|---:|
| Primeiro resultado — legado | 28,60% | 90,20% | 100,00%* |
| Agora — R1 + R95 | **98,39%** | **97,54%** | **89,99%** |

Evolução observada: **+69,79 pontos** de precisão de improdutividade,
**+7,34 pontos** de produtividade e **-10,01 pontos** de coverage. O gate
`95% / 95% / 65%` foi aprovado.

\* O coverage inicial de 100% era apenas aparente: o legado transformava
fallback e ausência de evidência em `IMPRODUTIVO`, sem opção real de abstenção.
Como o baseline inicial e o resultado atual usam universos diferentes, esta é
uma comparação de evolução do produto, não um A/B pareado sobre as mesmas linhas.

## O que foi investigado e resolvido

- A R1 eliminou acusações causadas por ações que o sistema não conseguiu nomear.
- Como marco intermediário, a R1 alcançou 90,65% I, 98,24% P e 89,15% de
  coverage no universo recente combinado.
- O erro restante se concentrou em alegações improdutivas já marcadas como
  duvidosas ou apoiadas por poucas observações.
- A R95 passou a abster uma acusação I com `em_duvida=true` ou menos de quatro
  amostras reais.
- Para recuperar coverage, ação sem nome com operador confirmado e mãos na
  máquina pode votar como produtiva.
- Nenhuma regra cria presença ou identidade; o estado de presença é preservado.

## Validação robusta

- Todas as versões recentes 6–9 passaram o gate; mínimos observados:
  **96,43% I**, **96,14% P** e **78,31% coverage**.
- Limiares entre 3 e 6 amostras também passaram o gate agregado.
- Embaralhar ou duplicar integralmente as linhas não alterou os percentuais.
- Passaram as suítes de gate, contrato, pipeline, ação não nomeada, ponte
  rolante, verdade de produtividade e os **90 testes de segurança de presença**.

## Produção

- Branch: `claude/youthful-davinci-R3xSP`
- Commit: `82626d00cbbc4a69abd7da9bd6821a2c982912db`
- Render: `Live`; deploy automático concluído em 5m17s.
- Saúde pública: `GET /health` respondeu `200 {"ok":true}`.

Os percentuais acima são o replay validado do código agora publicado. Eventos
novos gerados após o deploy ainda precisarão de validação humana para formar uma
medição pós-deploy independente.

O processo completo, as hipóteses rejeitadas e a metodologia estão em
`RELATORIO_PROCESSO_METODOLOGIA_R95_20260913.md`.
