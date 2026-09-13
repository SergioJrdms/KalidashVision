# Validação breve de produção — R1 + R95

## Resultado

Replay do código de produção sobre os mesmos **1.558 eventos recentes
validados** usados no gate:

| Cenário | Precisão improdutividade | Precisão produtividade | Coverage |
|---|---:|---:|---:|
| Antes — somente R1 | 90,65% | 98,24% | 89,15% |
| Agora — R1 + R95 | **98,39%** | **97,54%** | **89,99%** |

Resultado líquido: **+7,74 pontos** de precisão de improdutividade,
**-0,70 ponto** de produtividade e **+0,84 ponto** de coverage. O gate
`95% / 95% / 65%` foi aprovado.

## O que foi investigado e resolvido

- A R1 eliminou acusações causadas por ações que o sistema não conseguiu nomear.
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
