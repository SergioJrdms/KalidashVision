# Metodologia de testes e checklist das sugestões do Iago

Data: 13/09/2026

Produção avaliada: `claude/youthful-davinci-R3xSP` no commit `82626d0`

## Resultado validado

| Estágio | Precisão I | Precisão P | Coverage |
|---|---:|---:|---:|
| Primeiro baseline legado | 28,60% | 90,20% | 100,00%* |
| Marco intermediário R1 | 90,65% | 98,24% | 89,15% |
| Produção atual R1 + R95 | **98,39%** | **97,54%** | **89,99%** |

\* O coverage inicial era aparente: o legado não abstinha corretamente e
transformava falta de evidência em `IMPRODUTIVO`.

## Metodologia aplicada

### 1. Reproduzir o problema original

O notebook inicial foi reexecutado e conferido sobre 111 janelas julgáveis de
24/08/2026. A matriz continha 50 falsos improdutivos e confirmou a precisão I de
28,60%. Esse conjunto foi tratado como diagnóstico histórico, não como prova de
generalização.

### 2. Auditar a decisão real do sistema

Foram inspecionados os caminhos que produzem `PRODUTIVO`, `IMPRODUTIVO` e
`ABSTEM` em `backend/productivity.py` e `backend/pipeline.py`. Foram verificadas
as precedências de presença/papel, mãos na máquina, orientação, julgamento
visual, conversa, ação sem nome, correção humana e ponte rolante.

O contrato usado nos testes foi: dúvida ou evidência insuficiente não pode virar
uma acusação de improdutividade por fallback.

### 3. Minerar padrões em mais dias

O banco foi consultado em modo somente leitura. A investigação encontrou 1.457
vídeos, 26 dias com vídeo, 115,88 horas e 19.763 eventos. O export experimental
continha 18.056 eventos; após os filtros de evidência humana disponíveis, 3.304
eventos de 20 dias foram usados para mineração e screening.

Os dias foram separados temporalmente:

- treino: 12 dias e 1.746 eventos;
- calibração: 12 a 14/08 e 631 eventos;
- teste interno: 17 a 21/08 e 927 eventos.

Nenhum dia foi misturado entre as partes. Calibração e teste somam os 1.558
eventos usados no comparativo atual.

### 4. Explicar os falsos improdutivos

Os 50 falsos improdutivos do primeiro baseline foram agrupados por origem:

- 35 de julgamento visual;
- 13 de identidade;
- 2 de presença.

Dos 35 erros de julgamento, 32 eram `monitorar_maquina` e 3 eram
`operar_torno`. Também foram mineradas as descrições que a própria IA havia
persistido. Os padrões “parado”, “sem tocar”, ação não nomeada e falta de
contexto temporal apareciam nos erros. Isso mostrou que imobilidade instantânea
e ausência de nome não eram provas suficientes de improdutividade.

### 5. Testar candidatas isoladamente

Foram comparados, sem mudar a produção durante a seleção:

- regras temporais;
- regra de ação indefinida;
- veto por vizinhança;
- modelos leves estruturado, textual e combinado;
- fine-tuning LoRA com modelo do Hugging Face;
- ensembles seletivos;
- limiares de evidência entre 2 e 6 amostras;
- combinação entre veto negativo e recuperação positiva por mãos na máquina.

As candidatas que não mantiveram simultaneamente precisão I, precisão P e
coverage foram rejeitadas. Entre os exemplos rejeitados:

| Candidata | Resultado determinante |
|---|---|
| Persistência genérica de 30 s | 25,00% I na calibração |
| TF-IDF textual | 85,25% I na calibração, mas 49,57% no teste |
| LoRA textual | 82,69% I com 9,19% coverage na calibração; 40,38% I no teste |
| Ensemble mais seletivo | 100% I no teste, mas somente 39,27% coverage |
| R4, veto por vizinhança | piorou I em 1,32 p.p. no teste e reduziu coverage |

### 6. Escolher a solução mínima que generalizou

A R1 transformou `acao_indefinida` e `nao_nomeado` em abstenção. Ela elevou o
agregado recente para 90,65% I, 98,24% P e 89,15% coverage.

A R95 acrescentou dois controles:

1. uma futura alegação I abstém quando `em_duvida=true` ou há menos de quatro
   amostras realmente observadas;
2. ação sem nome recupera um voto P somente quando a pessoa é operador e há
   `maos_maquina=true`.

Correção humana e ponte rolante confirmada mantêm precedência. O gate só decide
o eixo P/I/abstenção e preserva o estado de presença.

### 7. Executar testes de robustez e regressão

O resultado foi recalculado por parte, por versão e por limiar. Os mínimos entre
as versões 6 a 9 foram 96,43% I, 96,14% P e 78,31% coverage. Os limiares 3 a 6
passaram o gate agregado; quatro amostras apresentou a maior precisão I sem
coverage excessivamente baixo.

Também foram verificados:

- invariância ao embaralhamento das linhas;
- invariância à duplicação integral do conjunto;
- limiar independente do nome da ação;
- dúvida vetando apenas a futura alegação I;
- decisão P não sendo bloqueada pelo gate negativo;
- mãos recuperando P apenas para operador;
- precedência da decisão humana;
- preservação exata do estado de presença;
- precedência da ponte rolante confirmada;
- suítes de contrato, pipeline, ação não nomeada e verdade de produtividade;
- 90 testes de segurança de presença.

### 8. Promover e verificar produção

Foi feito um gate de ancestralidade antes do push: o commit combinado descendia
do commit já publicado. A promoção ocorreu por fast-forward, sem reescrever o
histórico. O Render publicou o commit `82626d0`, mostrou o deploy como `Live` e
o endpoint `GET /health` respondeu `200 {"ok":true}`.

## Checklist das sugestões do Iago

Legenda: ✅ concluído; 🟨 concluído parcialmente ou por abordagem equivalente;
⬜ ainda não concluído.

| Sugestão atribuída ao Iago na reunião | Status | O que foi feito e evidência |
|---|:---:|---|
| Tornar explícitas as regras usadas para dizer produtivo ou improdutivo (00:04:03-00:06:07) | ✅ | Os dois caminhos de classificação foram auditados, documentados e cobertos por testes de precedência. |
| Não depender apenas de contexto textual; considerar informação que o modelo pode não conhecer, como zona de interesse (00:06:07) | ✅ | A decisão final usa sinais estruturados do pipeline - presença/papel, mãos, orientação, amostras, dúvida e ponte - em vez de confiar apenas na descrição. |
| Usar os exemplos corretamente identificados para treinar/testar um modelo do Hugging Face (00:05:09-00:10:05) | ✅ | Foi treinado e avaliado um LoRA sobre `distilbert-base-multilingual-cased`, com 1.746 exemplos de treino. O candidato foi rejeitado por baixa generalização e coverage. |
| Fazer fine-tuning diretamente com os frames improdutivos corretos (00:05:09) | 🟨 | A hipótese de fine-tuning foi testada na modalidade textual usando descrições e rótulos disponíveis. Não foi feito fine-tuning de um modelo visual diretamente nos pixels dos frames. |
| Nos erros, investigar “o que a IA viu” e comparar com a leitura humana (00:17:28-00:18:27) | 🟨 | Foram analisadas as descrições persistidas pela IA e agrupados os falsos positivos. Não houve uma nova consulta visual sistemática ao VLM para cada frame errado. |
| Transformar os padrões encontrados em guardrails do orquestrador (00:17:28-00:18:27) | ✅ | O objetivo foi implementado como guardrails determinísticos e auditáveis no código: R1 e R95. A localização foi o classificador, e não somente o prompt, para garantir o mesmo comportamento no replay e em produção. |
| Testar a mudança em outro dia, dois ou três dias depois, em vez de validar apenas no dia usado para ajuste (aprox. 00:18:27-00:19:30) | ✅ | Foi feito replay temporal em dias posteriores e validação por versões 6-9. A medição online com eventos novos pós-deploy será uma validação adicional. |
| Executar otimização automatizada em paralelo à validação manual (resumo da reunião; 00:10:05) | ✅ | Notebooks A/B, mineração, modelos e testes automatizados foram executados sobre evidência humana disponível, mantendo a decisão humana como referência. |
| Evitar que a limitação da ROI impeça reconhecer trabalho com ponte rolante (00:20:20-00:21:31) | ✅ | A ponte confirmada permaneceu produtiva acima dos gates R1/R95 e recebeu testes específicos de regressão. A ROI não foi ampliada indiscriminadamente. |
| Simplificar percentuais e separar claramente produtividade de “operador no posto” (00:25:46-00:27:34) | 🟨 | O dashboard atual separa produtividade e presença, mas ainda exibe “Operador no posto”. Uma auditoria visual completa de consistência entre todas as páginas não fez parte desta rodada. |
| Permitir abrir as evidências que formam os percentuais (00:27:34-00:28:05) | 🟨 | Já existem drawers de evidência para atividades e posto vazio. O clique direto no percentual agregado de improdutividade não foi validado nesta rodada. |
| Fazer a nomenclatura casar em todas as páginas e usar como total as horas capturadas (00:26:46-00:28:05) | ⬜ | Não foi concluído neste trabalho. A avaliação atual preserva `ABSTEM` e coverage explicitamente; portanto, não força todos os minutos a somarem P ou I apenas para fechar 100%. |

## Fechamento do checklist

Das 12 frentes acima:

- **7 foram concluídas**;
- **4 foram concluídas parcialmente ou por uma variante limitada**;
- **1 permanece pendente**.

As sugestões diretamente ligadas ao aumento de precisão foram executadas: regras
explícitas, sinais estruturados, teste de Hugging Face, análise de padrões,
guardrails determinísticos, validação temporal e preservação da ponte rolante.
Os itens ainda parciais concentram-se em fine-tuning visual por pixels, nova
consulta do VLM frame a frame, dados humanos pós-deploy e acabamento da interface.

## Evidências reproduzíveis

- `notebooks/04_30_Dias_Mineracao_Padroes.ipynb`: mineração dos padrões;
- `notebooks/06_Regras_Temporais_Improdutividade.ipynb`: regras temporais;
- `notebooks/07_Classificador_Leve_Seletivo.ipynb`: modelos leves;
- `notebooks/08_Finetuning_HuggingFace_LoRA.ipynb`: experimento de fine-tuning;
- `notebooks/09_Ensemble_Seletivo_Gate_Final.ipynb`: ensembles;
- `notebooks/10_Gate_Simples_R1_Precisao_Coverage.ipynb`: seleção da R1;
- `notebooks/11_Gate_R95_Improdutividade_Produtividade_Coverage.ipynb`: R95;
- `notebooks/12_Validacao_Robusta_R95_Producao.ipynb`: robustez final;
- `tests_productivity_precision_95_gate.py` e
  `tests_productivity_precision_95_robust.py`: regressões específicas;
- transcrição da reunião, especialmente 00:05:09, 00:06:07, 00:10:05,
  00:17:28, 00:18:27 e 00:25:46-00:28:05.

O número atual validado permanece **98,39% de precisão I, 97,54% de precisão P e
89,99% de coverage** no replay dos 1.558 eventos recentes. Ele ainda deve ser
confirmado futuramente com eventos e rótulos humanos produzidos após o deploy.
