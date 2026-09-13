# Experimentos de produtividade em 30 dias - 12/09/2026

## Decisão executiva

Nenhuma regra, classificador leve, LoRA ou ensemble testado pode substituir o
classificador atual. Nenhuma candidata passou simultaneamente precisão I,
recall I, precisão P e coverage na calibração temporal. Portanto, a mudança
segura é **não promover uma nova decisão P/I** e instrumentar o produto para
coletar o primeiro gabarito binário independente, sem confundir confirmação do
rótulo da atividade com confirmação de produtividade.

## Fontes e limites

- banco vivo, consultado somente com `SELECT`: 1.457 vídeos, 26 dias de vídeo,
  115,88 h, 19.763 eventos e 11 versões de instrumento;
- 422 vídeos ainda têm objeto disponível no Storage;
- export local usado nos notebooks: 18.056 eventos;
- proxy humana utilizável: 3.304 eventos, 792 vídeos e 20 dias;
- verdade da proxy: comportamento confirmado/corrigido por humano + categoria
  Lean do catálogo (2.860 P e 444 I);
- o arquivo `gabarito_NAO_ABRIR.csv` não foi aberto por estes experimentos.

Essa proxy é válida para mineração e screening, mas não é a precisão atual do
produto: o humano confirmou o nome da atividade, não respondeu separadamente
"produtivo ou improdutivo?". Também há forte viés de versão e de seleção da
fila de validação.

## Divisão temporal

O mesmo dia nunca aparece em mais de uma parte:

- treino: 12 dias, 1.746 eventos e 271 I;
- calibração: 12 a 14/08, 631 eventos e 83 I;
- teste interno: 17 a 21/08, 927 eventos e 90 I.

As regras e limiares são escolhidos sem consultar os cinco dias finais
internos. Isso é melhor que sortear frames, mas ainda não substitui o holdout
novo com rótulo P/I direto.

## Mineração automática de padrões

A média agregada dos 20 dias (55,15% de precisão I e 94,26% de precisão P)
esconde uma mudança de instrumento. A precisão I da proxy caiu para 20,30% em
14/08 (V7) e subiu para 100% em 17/08, 20/08 e 21/08 (V8/V9). Nos cinco dias
internos finais, o catálogo obteve 91,46% de precisão I, 83,33% de recall I,
98,22% de precisão P e 100% de coverage. Isso é um resultado histórico da
proxy, não a precisão confirmada da versão hoje em produção.

O maior padrão de falsa acusação foi `acao_indefinida`: 121 ocorrências só em
14/08. Na mineração textual de treino, descrições com "parado junto",
"operador ausente" e "vazio" estiveram associadas a falsas acusações; termos
de conversa explícita com colega estiveram associados a negativos corretos.
Texto permanece hipótese, nunca evidência suficiente para acusar.

## Evidência estruturada

A evolução do schema também muda o que é mensurável:

- `descricao_bruta`: 100% em todas as partes;
- `trabalho`: 0,29% no treino, 0% na calibração e 59,12% no teste interno;
- `maos_maquina`: 0,17%, 60,86% e 38,30%;
- `movimento_maquina`: 23,71%, 92,87% e 100%;
- `produtividade_motivo`: 0% no export histórico.

Sem `produtividade_motivo`, o histórico não consegue testar end-to-end o novo
portão negativo. A persistência desse campo já foi corrigida na branch; novos
eventos passam a preservar a causa.

## Regras temporais

Abster `acao_indefinida` elevou a precisão I da calibração para 89,47%, com
98,27% de precisão P e 73,22% de coverage, mas recall I de apenas 61,45%.
Falhou o gate de recall de 70%. No teste interno, onde `acao_indefinida`
desapareceu, a regra ficou idêntica ao baseline.

Exigir persistência genérica de I por 30 s foi pior: 25% de precisão I e 12,05%
de recall I na calibração. Persistência só deve ser específica por motivo
(`uso_celular` 2-de-3; `sem_atividade` 3 observações), nunca aplicada a todo I.

## Classificadores leves

Nenhum passou a calibração completa:

| Modelo | Parte | Precisão I | Recall I | Precisão P | Coverage |
|---|---|---:|---:|---:|---:|
| Texto TF-IDF | calibração | 85,25% | 62,65% | 96,86% | 60,06% |
| Texto TF-IDF | teste interno | 49,57% | 64,44% | 97,93% | 38,73% |
| Combinado | calibração | 39,74% | 74,70% | 99,57% | 61,65% |
| Combinado | teste interno | 83,61% | 56,67% | 98,02% | 33,87% |

O colapso do modelo textual nos dias posteriores confirma overfitting ao estilo
das descrições e drift entre versões.

## Fine-tuning Hugging Face / LoRA

Foi executado um LoRA sobre `distilbert-base-multilingual-cased`, localmente,
por uma época, com 1.746 exemplos de treino. Nenhuma descrição/frame foi
enviada ao Hugging Face; somente os pesos públicos foram baixados.

| Parte | Precisão I | Recall I | Precisão P | Coverage |
|---|---:|---:|---:|---:|
| calibração | 82,69% | 51,81% | 100% | 9,19% |
| teste interno | 40,38% | 70,00% | indefinida | 16,83% |

O resultado rejeita este fine-tuning: atingiu a precisão alvo na calibração
apenas abstendo em mais de 90% e não generalizou para os dias seguintes.

## Ensemble seletivo

Foram comparados catálogo, modelos leve estruturado/textual/combinado, LoRA e
combinações ponderadas. Nenhum ensemble passou a calibração. Um ensemble chegou
a 100% de precisão I no teste interno, mas com 37,78% de recall I e 39,27% de
coverage; ele é um filtro de alegações, não uma melhoria operacional.

## Alteração de produção preparada em shadow

O código agora separa e congela quatro campos por evento:

- `produtividade_predita`: `PRODUTIVO`, `IMPRODUTIVO` ou `ABSTEM`;
- `produtividade_regra`: regra exata que decidiu/absteve;
- `produtividade_humana`: resposta independente do cliente;
- `produtividade_validada_em`: instante do julgamento humano.

A fila pergunta separadamente se o intervalo foi produtivo, improdutivo ou
impossível de decidir, sem mostrar a previsão antes da resposta humana, e só
então conclui a confirmação/correção do rótulo. Essa coleta cega evita ancorar
o avaliador na decisão do sistema. Reabrir ou descartar limpa o julgamento P/I. Eventos antigos recebem
apenas uma projeção na resposta; não são reescritos nem usados como se a
predição tivesse sido congelada no passado.

Essa instrumentação não muda o KPI nem a regra de classificação. Ela cria os
dados necessários para medir precisão I/P, recall, F1, coverage, abstenção,
matriz 2x3 e taxa de falsa acusação por versão/dia.

## Gate antes de promover qualquer regra

- precisão I >= 85% e limite inferior do IC95% >= 80%;
- recall I >= 70%;
- queda de precisão P <= 2 p.p.;
- coverage >= 80% e queda <= 5 p.p. contra o baseline da mesma versão;
- falsa acusação <= 5%;
- pelo menos 20 alegações I em pelo menos 10 dias novos;
- dupla anotação em 20% e adjudicação das divergências;
- split por dia/vídeo/episódio, nunca por frames aleatórios.

Até esse gate ser satisfeito, `acao_indefinida`, conflito entre sinais e
evidência insuficiente devem resultar em `ABSTEM`, não em improdutividade.
