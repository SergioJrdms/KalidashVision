# Relatório de decisão — improdutividade ≥ 80%

Data da análise: 12/09/2026
Branch: `fix/productivity-negative-gate-20260911`

## Veredito

A meta de 80% **ainda não está comprovada**. O melhor baseline mensurável é a
H6 no conjunto de 24/08/2026, já usado durante o desenvolvimento. Ele obteve
70,65% de precisão de improdutividade, mas com apenas 18 alegações negativas e
intervalo exploratório de 40,0% a 94,7% ao reamostrar vídeos. Esse conjunto é
screening, não holdout.

Nenhuma candidata foi declarada vencedora. Promover um filtro que simplesmente
retira alegações negativas elevaria a precisão aparente ou a deixaria
indefinida, à custa de recall e coverage. O contrato correto é melhorar a
precisão mantendo os outros eixos visíveis.

## Resultado reproduzido no dia já visto (24/08)

| Política | Precisão I | Recall I | F1 I | Precisão P | Coverage | Falsa acusação |
|---|---:|---:|---:|---:|---:|---:|
| Legado | 27,72% | 85,65% | 41,88% | 92,04% | 100,00% | 57,37% |
| H6 atual | 70,65% | 56,93% | 63,05% | 87,85% | 65,06% | 6,08% |
| Separar presença/identidade | indefinida | 0,00% | indefinida | 87,85% | 48,59% | 0,00% |

Matriz H6 em unidades:

| Verdade \ Predição | Produtivo | Improdutivo | Abstém |
|---|---:|---:|---:|
| Produtivo | 47 | 5 | 35 |
| Improdutivo | 8 | 13 | 3 |

Em tempo, o conjunto possui 103,41 minutos. A H6 classificou 17,03 minutos
como improdutivos e absteve-se em 36,13 minutos. Todos os 17,03 minutos
negativos vieram dos níveis de presença ou identidade, não de evidência direta
da atividade. Portanto, o número de 70,65% não valida o classificador de
atividade que será usado daqui em diante.

## O que a biblioteca multi-dia realmente diz

`correcoes.csv` contém 423 correções, 258 episódios físicos, 19 dias e 6,62 h.
Todas as linhas são erros de rótulo; ele não possui acertos ou abstenções da
população e não pode medir precisão, recall ou coverage.

Ele contém 172 falsas acusações I→P, 151 improdutivos perdidos P→I e 100
correções sem mudança binária. Padrões recorrentes:

- `acao_indefinida → produtivo`: 118 casos em 4 dias;
- `posto_vazio → produtivo`: 36 casos em 12 dias;
- `conversando_colega → produtivo`: 15 casos em 8 dias;
- `monitorar_maquina → improdutivo`: 95 casos em 16 dias;
- `operar_torno → improdutivo`: 55 casos em 10 dias.

Os contrafactuais servem apenas para priorizar candidatas:

| Trava diagnóstica | Falsas acusações conhecidas capturadas | Negativos corretos conhecidos preservados |
|---|---:|---:|
| Abster `acao_indefinida`/`posto_vazio` | 89,53% | 30,00% |
| Veto por sinal produtivo conflitante | 48,84% | 53,33% |
| Combinação das duas | 90,70% | 23,33% |

Conclusão: os padrões são reais e recorrentes, mas uma regra global baseada
neles retiraria negativas verdadeiras demais. Ela deve ser corroborativa e
temporal, não uma troca direta de rótulo.

## Falha bloqueante corrigida no código

O gate negativo exige `produtividade_motivo` (`uso_celular` ou
`sem_atividade`). A observação possuía a causa em memória, porém a causa era
perdida em dois pontos:

1. o evento principal do minuto consolidava `trabalho`, mas não a causa;
2. os eventos crus — preferidos pelo instrumento estruturado — eram persistidos sem a
   causa.

Após um reload, negativos válidos viravam inconclusivos. O conserto agora:

- agrega a causa com os mesmos votos e pesos temporais usados em `trabalho`;
- ignora motivos do voto oposto;
- retorna causa nula em empate, sem escolha arbitrária;
- persiste a causa no principal e nos eventos crus;
- carimba os novos registros como instrumento V14 (ou V15 com autoridade
  111D), separando-os do histórico que perdia a causa;
- cobre o round-trip observação → persistência → reload → classificador.

Isso não aumenta artificialmente a precisão. Ele torna possível medir a regra
nova depois do replay, sem que a evidência desapareça no banco.

## Candidatas priorizadas

### C2 — motivo negativo explícito

Manter a whitelist atual. Nunca aceitar `trabalho=False` sozinho, texto livre,
`costas_ou_lado` ou `conversa_ou_celular` como prova. É a base segura e já está
implementada.

Em processos novos, rótulo desconhecido ou categoria ainda não decidida deve
virar `ABSTEM`, nunca improdutividade por padrão. O histórico de
`acao_indefinida` mostra por que o default negativo é perigoso.

### C3 — veto positivo antes de acusar

Se houver mãos no torno, máquina em operação manual/automática, movimento
compatível com ciclo ou episódio confirmado de ponte, a saída negativa deve
virar produtiva quando a evidência positiva for forte ou abstém quando houver
conflito. Movimento parado ou imobilidade isolada nunca autorizam
improdutividade.

### C4 — persistência específica do motivo

- celular: exigir o mesmo operador e o motivo em pelo menos 2 de 3 observações
  reais consecutivas, sem mãos na máquina;
- sem atividade: exigir pelo menos 3 observações/20 s, ausência de mãos,
  máquina realmente parada e nenhum episódio produtivo ativo;
- frames herdados/interpolados não contam como confirmação.

Os limiares são hipóteses pré-registradas para shadow; não devem ser ligados em
produção antes do holdout.

### C5 — conversa verificada

Conversa com colega só pode ser negativa com associação inequívoca ao outro
indivíduo, roupa não cinza e confiança calibrada. Testar 0,72/0,80/0,85/0,90 e
concordância entre câmeras. Rótulo ou descrição isolados não decidem.

### C6 — resgate episódico de ponte/monitoramento

Classificar o episódio completo como produtivo quando houver sequência coerente
de controle, gancho/linga/cabo, carga suspensa e coordenação da pessoa com a
carga. Gancho parado ou pessoa fora do polígono não bastam. CAM2 deve
desambiguar a operação do torno quando CAM1 vê o gancho à frente.

## Gate de promoção

No holdout final, uma candidata só passa com:

- precisão I ≥ 85% e limite inferior do IC95% ≥ 80%;
- recall I ≥ 70%;
- precisão P com queda máxima de 2 p.p. em relação ao baseline congelado;
- coverage ≥ 80% e queda máxima de 5 p.p.;
- taxa de falsa acusação ≤ 5%;
- pelo menos 20 alegações I, distribuídas em vários dias.

Entre candidatas aprovadas, escolher maior coverage e depois maior recall I.

## Holdout necessário

- pelo menos 10 dias completamente novos;
- amostra da linha do tempo, incluindo acertos, erros e abstenções;
- `episode_id` comum para unir CAM1/CAM2 do mesmo instante;
- estratificação por turno e cenário, sem sortear frames quase idênticos entre
  desenvolvimento e teste;
- presença e atividade rotuladas em campos separados;
- pelo menos 20% de dupla anotação e adjudicação de divergências;
- gabarito oculto até congelar todas as predições.

O notebook final para automaticamente se esses requisitos não forem atendidos.

## Lacuna da validação dentro do produto

A fila atual de pendências não é uma projeção direta das abstenções de
produtividade: ela lista eventos principais ainda não validados e aplica outro
gate de relevância. A confirmação humana também corrige o rótulo de atividade,
mas não grava um julgamento binário independente de produtividade.

Para medir continuamente em produção, a próxima alteração deve expor
`produtividade_predita`, `produtividade_motivo` e a saída `ABSTEM` no item de
validação, além de gravar `produtividade_humana` separadamente de
`label_corrigido`. Sem essa separação, confirmar um nome de atividade não é
ground truth suficiente para precisão I.

## Pré-requisitos operacionais

1. aplicar a coluna `produtividade_motivo` de `sql/schema.sql` no banco;
2. confirmar as flags V9/V11 no ambiente real;
3. reprocessar vídeos após o deploy, pois o histórico sem causa não pode ser
   reconstruído com segurança por texto;
4. coletar as saídas em shadow antes de alterar o KPI público;
5. não confundir `improdutividade_pct` do dashboard com precisão: ele é apenas
   proporção de tempo previsto I entre P+I.
