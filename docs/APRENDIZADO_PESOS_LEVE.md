# Aprendizado de pesos leve — implementação local

Dois classificadores lineares próprios, treinados por SGD em CPU: nome da
atividade e classificação lean. Sem nova dependência, serviço pago ou chamada
adicional a uma IA. Não altera os pesos da VLM e não treina percepção visual.

Após uma validação de evento ou classificação de atividade, são lidos os
últimos 200 eventos humanos e até 100 categorias humanas daquele processo.
Os modelos são reconstruídos com oito passagens curtas por exemplos únicos,
atualizando pesos reais. Isso não é persistência incremental de um checkpoint:
é replay limitado, que permite retirar correções reabertas/descartadas e
recuperar os modelos após reinício a partir das validações já persistidas.
Não representa treinamento sobre todo o histórico. Cada atualização faz duas
leituras adicionais do banco; não significa custo total de infraestrutura zero.

Lean exige uma escolha explícita de P/I ou categoria humana da atividade.
ABSTEM não é um exemplo P/I. Cards usam a atividade humana confirmada e a
descrição/contexto já existentes; descrições inválidas e exemplos contraditórios
são excluídos. Com apenas um nome de atividade, o modelo de cards não tem duas
classes para comparar e não emite sugestão. Lean pode atualizar já no primeiro
exemplo, mas isso não prova capacidade de generalização.

## Proteção antes de ativar

As sugestões ficam **somente em modo shadow**, no JSONB interno existente
`bbox_stats.aprendizado_pesos_shadow` dos novos eventos. Não mudam o nome,
categoria, validação humana, produtividade congelada, indicadores ou presença.
Não há tela nova nem percentuais de precisão expostos ao cliente.
Scores softmax não são confiança calibrada nem precisão medida.
O registro interno limita-se às três maiores pontuações de cada tarefa,
para não multiplicar o armazenamento de listas inteiras de atividades.

Testes offline verificam atualização real de pesos, separação das tarefas,
revogação, isolamento e preservação dos indicadores. Não são uma medição nova
de precisão em vídeos reais. Antes de usar sugestões automaticamente, comparar
as previsões shadow com validações posteriores, mantendo os acertos atuais.
Fine-tuning visual não foi iniciado: precisa de frames corrigidos e orçamento
separado. Não houve deploy nesta etapa.

Execução local: `python -m unittest tests_online_learning tests_human_learning`.

Verificação desta etapa: 27 testes unitários e 106 checagens dos contratos
existentes aprovados (133 no total), além de compilação Python e diff check.
Benchmark sintético local: 200 exemplos com 200 nomes distintos treinados em
aproximadamente 1,24 s. Não é previsão de latência no Render nem de precisão.
