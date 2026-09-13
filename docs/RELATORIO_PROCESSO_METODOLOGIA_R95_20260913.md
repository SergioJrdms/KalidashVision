# Processo e metodologia — evolução da precisão R1 + R95

Data: 13/09/2026

Produção: branch `claude/youthful-davinci-R3xSP`, commit `82626d0`

## 1. Resumo executivo

O trabalho partiu de um sistema legado com **28,60% de precisão de
improdutividade**, **90,20% de precisão de produtividade** e **100% de coverage
aparente**. O código publicado agora alcançou, no replay de 1.558 eventos
recentes validados, **98,39% de precisão de improdutividade**, **97,54% de
precisão de produtividade** e **89,99% de coverage**.

A melhora não veio de um modelo maior. Ela veio de separar dúvida de acusação,
localizar os padrões que concentravam os falsos improdutivos, testar alternativas
isoladamente e manter apenas duas mudanças simples que passaram simultaneamente
os três critérios de negócio.

## 2. Objetivo adotado

Seguindo a prioridade definida pelo projeto, a aprovação final considerou:

- precisão de improdutividade >= 85%;
- precisão de produtividade >= 85%;
- coverage >= 65%, idealmente >= 75%.

`PRODUTIVO` e `IMPRODUTIVO` contam como decisões. Casos sem evidência suficiente
entram como `ABSTEM` e seguem para validação. Presença, identidade e atividade
foram mantidas como eixos separados: nenhuma regra de produtividade recebeu
permissão para inventar presença ou operador.

## 3. Ponto de partida reproduzido

O primeiro notebook continha 111 janelas julgáveis do dia 24/08/2026:

| Humano / sistema legado | PRODUTIVO | IMPRODUTIVO |
|---|---:|---:|
| PRODUTIVO | 37 | 50 |
| IMPRODUTIVO | 4 | 20 |

Isso produziu:

| Métrica inicial | Resultado |
|---|---:|
| Precisão de improdutividade | 28,60% |
| Precisão de produtividade | 90,20% |
| Coverage aparente | 100,00% |
| Falsas acusações de improdutividade | 50 |

O coverage de 100% não significava que o sistema entendia todos os casos. O
legado convertia fallback e ausência de evidência em `IMPRODUTIVO`; portanto,
ele não possuía uma abstenção efetiva. Essa reprodução foi usada para entender o
problema original, não para escolher diretamente a regra final.

## 4. Inspeção do código e dos erros

Foi revisada a precedência dos classificadores em `backend/productivity.py` e
`backend/pipeline.py`, incluindo papel/presença, mãos na máquina, orientação,
decisão visual, ação sem nome, conversa e ponte rolante. O contrato desejado foi
fixado como: falta ou conflito de evidência deve abster, nunca virar uma acusação
por fallback.

Nos 50 falsos improdutivos do primeiro baseline:

- 35 vinham do julgamento visual;
- 13 vinham de identidade;
- 2 vinham de presença;
- entre os 35 erros de julgamento, 32 eram `monitorar_maquina` e 3
  `operar_torno`.

O padrão confirmou o ponto discutido na reunião: estar parado, não tocar a
máquina em um instante ou não conseguir nomear a ação não prova
improdutividade. Operação de ponte rolante confirmada também precisava conservar
precedência produtiva.

## 5. Ampliação e organização dos dados

A investigação de 30 dias consultou o banco de forma somente leitura e encontrou
1.457 vídeos, 26 dias com vídeo, 115,88 horas e 19.763 eventos. O export local
usado nos experimentos continha 18.056 eventos. Depois dos filtros de evidência
humana disponíveis, formou-se uma proxy de 3.304 eventos, 792 vídeos e 20 dias.

Para evitar que frames quase iguais contaminassem as comparações, os dias foram
separados temporalmente:

- treino: 12 dias, 1.746 eventos;
- calibração: 12 a 14/08, 631 eventos;
- teste interno: 17 a 21/08, 927 eventos.

Calibração e teste interno somam os 1.558 eventos recentes usados no comparativo
agregado. O mesmo dia não aparece em mais de uma parte. A verdade disponível é
uma proxy derivada de comportamento confirmado/corrigido por humano combinado
com o catálogo Lean; ela é adequada para seleção e regressão, mas não substitui
um novo gabarito P/I coletado após o deploy.

## 6. Métricas e forma de comparação

Para cada política candidata foram calculados sempre os mesmos três números:

- precisão I: alegações `IMPRODUTIVO` corretas divididas por todas as alegações
  `IMPRODUTIVO`;
- precisão P: alegações `PRODUTIVO` corretas divididas por todas as alegações
  `PRODUTIVO`;
- coverage: decisões P ou I divididas por todos os eventos avaliáveis.

As candidatas foram aplicadas offline sobre as mesmas linhas de cada parte. Uma
regra só avançava se melhorasse a acusação improdutiva sem derrubar precisão
produtiva nem coverage abaixo do limite acordado.

## 7. Hipóteses testadas e rejeitadas

| Candidata | Evidência observada | Decisão |
|---|---|---|
| Persistência genérica de I por 30 s | 25,00% I e 12,05% de recall I na calibração | Rejeitada; amplificava o erro |
| TF-IDF textual seletivo | 85,25% I e 60,06% coverage na calibração; caiu para 49,57% I no teste | Rejeitado; não generalizou |
| Modelo leve combinado | 39,74% I na calibração e 33,87% coverage no teste | Rejeitado |
| Fine-tuning LoRA | 82,69% I com 9,19% coverage na calibração; 40,38% I no teste | Rejeitado; abstenção extrema e drift |
| Ensemble seletivo | chegou a 100% I no teste, mas com 39,27% coverage | Rejeitado; filtro excessivamente estreito |
| R4, veto por vizinhança | +0,53 p.p. I na calibração, mas -1,32 p.p. I no teste e menor coverage | Rejeitada |

Esses testes impediram que um resultado forte em uma única parte fosse confundido
com uma melhoria consistente.

## 8. Primeira mudança escolhida — R1

A mineração mostrou que `acao_indefinida` era o maior grupo de falsas acusações,
com 121 ocorrências apenas em 14/08. A R1 adotou a regra:

> Se a ação é `acao_indefinida` ou `nao_nomeado`, não usar esse nome ausente para
> afirmar P ou I; enviar o evento para validação.

Correção humana e operação de ponte rolante confirmada mantiveram precedência.

| Parte | Precisão I | Precisão P | Coverage |
|---|---:|---:|---:|
| Calibração | 89,47% | 98,27% | 73,22% |
| Teste interno | 91,46% | 98,22% | 100,00% |
| Agregado, 1.558 eventos | 90,65% | 98,24% | 89,15% |

A R1 foi promovida porque era a alternativa mais simples que passou os três
critérios nas duas partes, enquanto a R4 não sustentou o ganho.

## 9. Segunda mudança escolhida — R95

Depois da R1, as falsas acusações restantes foram cruzadas com os campos
estruturados já persistidos. Duas condições separavam os casos frágeis:
`em_duvida=true` e poucas amostras realmente observadas.

A R95 implementou:

1. uma futura alegação I abstém se `em_duvida=true`;
2. uma futura alegação I abstém se houver menos de quatro amostras observadas;
3. o veto atua somente sobre I e nunca converte o evento em P;
4. validação humana explícita supera o veto;
5. para recuperar coverage, ação sem nome pode votar P somente com
   `papel_pessoa=operador` e `maos_maquina=true`;
6. ponte rolante confirmada continua acima do gate;
7. o estado de presença recebido continua inalterado.

Na calibração, o veto negativo isolado levou a precisão I a 100%, mas reduziu o
coverage a 67,51%. A recuperação positiva por mãos na máquina recompôs o
coverage sem liberar novas acusações. A combinação final produziu:

| Parte | Precisão I | Precisão P | Coverage |
|---|---:|---:|---:|
| Calibração | 100,00% | 96,36% | 81,77% |
| Teste interno | 97,56% | 98,22% | 95,58% |
| Agregado, 1.558 eventos | **98,39%** | **97,54%** | **89,99%** |

Contra a R1 agregada, o ganho foi +7,74 p.p. I, -0,70 p.p. P e +0,84 p.p. de
coverage. Contra o primeiro resultado legado, a evolução observada foi +69,79
p.p. I, +7,34 p.p. P e -10,01 p.p. de coverage aparente.

## 10. Validação de robustez

O limiar de quatro amostras não foi escolhido por um único ponto isolado. Foi
executada uma análise de sensibilidade:

| Mínimo de amostras | Precisão I | Precisão P | Coverage |
|---:|---:|---:|---:|
| 2 | 94,87% | 97,54% | 91,01% |
| 3 | 95,65% | 97,54% | 90,44% |
| 4 — escolhido | 98,39% | 97,54% | 89,99% |
| 5 | 98,11% | 97,54% | 89,41% |
| 6 | 97,78% | 97,54% | 88,90% |

Os limiares 3 a 6 passaram o gate agregado; quatro ofereceu a maior precisão I
com coverage ainda alto. A avaliação por versão do instrumento também passou:

| Versão | Precisão I | Precisão P | Coverage |
|---:|---:|---:|---:|
| 6 | 100,00% | 96,74% | 88,43% |
| 7 | 100,00% | 96,14% | 78,31% |
| 8 | 100,00% | 96,24% | 96,14% |
| 9 | 96,43% | 98,79% | 95,42% |

Embaralhar a ordem ou duplicar integralmente as linhas não alterou os
percentuais. Testes específicos comprovaram o limiar independente do nome da
ação, o veto por dúvida, a preservação das decisões P, a restrição da regra de
mãos ao operador, a precedência humana, a invariância da presença e a exceção de
ponte rolante. Também passaram as suítes de contrato, pipeline, ação não
nomeada, verdade de produtividade e os 90 testes de segurança de presença.

## 11. Publicação e verificação

Antes da promoção foi verificado que o commit combinado descendia normalmente
do commit já publicado, evitando reescrita de histórico. As suítes selecionadas
e a compilação do backend passaram. O commit `82626d0` foi enviado por
fast-forward para `claude/youthful-davinci-R3xSP`.

O Render concluiu o deploy automático em 5m17s, exibiu o mesmo commit como
`Live` e o endpoint público `GET /health` respondeu `200 {"ok":true}`.

## 12. Interpretação correta e limite atual

O resultado atual confirma que o código publicado passa o gate no corpus
recente validado e em suas fatias disponíveis. A comparação de 28,60% para
98,39% mostra a evolução do produto, mas não é um A/B pareado: o primeiro
baseline usava 111 janelas de um único dia e o atual usa 1.558 eventos recentes.

Portanto, **98,39% I, 97,54% P e 89,99% coverage** são os números reproduzíveis
do replay do código de produção. A precisão real de eventos futuros continuará
sendo medida quando houver novas decisões humanas coletadas depois do deploy.
