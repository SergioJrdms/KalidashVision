# Kalidash Vision

Plataforma SaaS de análise de operações industriais por vídeo. O cliente
sobe um vídeo da operação; a plataforma detecta as pessoas, interpreta o
que cada uma está fazendo via IA de visão, descobre os comportamentos e
gera sugestões de melhoria de produtividade (Lean / 7 desperdícios).
Quanto mais vídeos e validações o cliente acumula, mais o sistema
aprende seu contexto específico.

---

## Estrutura do repositório

```
kalidash-vision/
├── backend/                    # FastAPI + pipeline de IA
│   ├── pipeline.py             # IPYNB refatorado (lógica preservada)
│   ├── main.py                 # rotas REST
│   ├── worker.py               # execução assíncrona do pipeline
│   ├── jobs.py                 # tracker de jobs em memória
│   ├── auth.py                 # validação de JWT do Supabase
│   ├── requirements.txt
│   └── .env.example
├── frontend/                   # React + TypeScript + Tailwind (Vite)
│   ├── src/pages/              # Login, Cadastro, Processos, Descrição, Upload, Dashboard, Validação, Chat
│   ├── src/components/
│   ├── src/lib/                # supabase, api, types
│   └── .env.example
├── sql/
│   └── schema.sql              # schema + RLS + função helper
└── README.md
```

---

## Pré-requisitos

- Conta no [Supabase](https://supabase.com) (banco + Auth + Storage).
- Chave da [Groq](https://console.groq.com) com acesso aos modelos Llama 4 Scout e gpt-oss-120b.
- Python 3.10+ e Node 18+.
- **GPU CUDA** na máquina que roda o worker — o YOLO11-pose precisa de GPU
  para processar vídeos em tempo razoável. Em CPU, o pipeline funciona mas
  fica lento demais para uso real.

---

## Setup do Supabase

1. Crie um projeto novo no Supabase.
2. No SQL Editor, cole e execute o conteúdo de `sql/schema.sql`. Isso cria
   as tabelas, índices, RLS e a função `auth_empresa()` que isola dados
   por empresa.
3. Em **Storage**, crie um bucket chamado `videos` (privado).
4. Em **Authentication → Settings**, habilite "Email signups". Opcional:
   desabilite "Confirm email" para testes mais rápidos.
5. Anote: `Project URL`, `anon key` (para o front) e `service_role key`
   (para o backend — segredo).

> **Mapeamento empresa↔usuário.** Quando o cliente se cadastra, o front
> grava `empresa` em `user_metadata`. O backend lê esse campo do JWT em
> cada request e usa como `EMPRESA` em todas as queries do pipeline. A
> RLS na função `auth_empresa()` faz o mesmo filtro como defesa adicional.

---

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# preencha SUPABASE_URL, SUPABASE_KEY (service_role), GROQ_API_KEY

# (opcional) baixe o modelo YOLO previamente:
python -c "from ultralytics import YOLO; YOLO('yolo11n-pose.pt')"

# rode a API
uvicorn backend.main:app --port 8000
```

> ⚠️ **Não use `--reload` para processar vídeos.** O Ultralytics e o
> próprio pipeline escrevem arquivos durante a execução; com `--reload`
> ativo, o uvicorn reinicia no meio do processamento, mata a
> BackgroundTask e o job nunca termina. Use `--reload` só para iterar
> nas rotas (sem subir vídeo). Você também pode forçar o YOLO a baixar
> o `.pt` pra fora da pasta do projeto: o backend já faz isso por
> padrão (`%TEMP%/kalidash_models`) ou via `KV_MODELS_DIR=/caminho`.

A API expõe:

| Método | Rota | O que faz |
|---|---|---|
| `POST` | `/processos` | cria processo |
| `GET`  | `/processos` | lista processos do usuário |
| `GET`  | `/processos/{id}` | detalhe + lista de vídeos |
| `PUT`  | `/processos/{id}/descricao` | atualiza a descrição do processo |
| `POST` | `/processos/{id}/videos` | upload + dispara job → `{ job_id }` |
| `GET`  | `/jobs/{job_id}` | status do job (polling do front) |
| `GET`  | `/processos/{id}/dashboard` | snapshot agregado + sugestões + pendências |
| `GET`  | `/processos/{id}/sugestoes` | todas as sugestões do contexto |
| `GET`  | `/processos/{id}/eventos?status=pendente` | fila de eventos para validação |
| `GET`  | `/processos/{id}/eventos/tabela` | planilha mestre paginada/filtrável (auditoria) |
| `GET`  | `/eventos/{id}/frames` | 3 frames com bounding box (data URLs) |
| `POST` | `/eventos/{id}/validar` | `{ acao: "confirmar"\|"corrigir"\|"descartar"\|"reabrir", label_corrigido? }` |
| `POST` | `/eventos/{id}/reabrir` | devolve o evento à fila (estado pendente) |
| `POST` | `/eventos/lote` | `{ ids, acao, label_corrigido? }` — ação em lote |
| `POST` | `/processos/{id}/chat` | pergunta em linguagem natural (legado; o front usa o Prism abaixo) |
| `GET`  | `/processos/{id}/prism/conversas` | lista de conversas com o Prism |
| `POST` | `/processos/{id}/prism/conversas` | cria nova conversa |
| `GET`  | `/processos/{id}/prism/conversas/{cid}` | conversa + mensagens |
| `PATCH`| `/processos/{id}/prism/conversas/{cid}` | renomeia (`{ titulo }`) |
| `DELETE`| `/processos/{id}/prism/conversas/{cid}` | exclui conversa (cascata) |
| `POST` | `/processos/{id}/prism/conversas/{cid}/mensagens` | envia pergunta; resposta + `titulo_auto?` |
| `GET`  | `/processos/{id}/prism/sugestoes?excluir=a\|b` | sugestões dinâmicas (3-4 perguntas curtas) |
| `GET`  | `/processos/{id}/perguntas?status=pendente` | perguntas que a IA fez ao cliente |
| `GET`  | `/processos/{id}/perguntas/contagem` | `{ pendentes }` para badge na UI |
| `POST` | `/perguntas/{id}/responder` | `{ resposta }` — vira contexto de domínio |
| `POST` | `/perguntas/{id}/dispensar` | cliente prefere não responder |
| `PUT`  | `/comportamentos/{id}/categoria` | gestor classifica em `valor_agregado` / `apoio` / `desperdicio` (override da IA) |
| `GET`  | `/health` | health check |

Autenticação: header `Authorization: Bearer <JWT do Supabase>`.

### Sobre o worker (precisa de GPU)

O upload retorna `{ job_id }` imediatamente. O processamento roda em
background via `BackgroundTasks` do FastAPI dentro do mesmo processo.
Isso é simples e funciona para o MVP, **mas exige que o processo do
FastAPI tenha acesso à GPU CUDA** (o YOLO11-pose roda no `ultralytics`).
Para produção com muitos vídeos simultâneos, troque por uma fila externa
(Celery/RQ + Redis) com workers em uma máquina GPU dedicada — é só apontar
`executar_job` para a fila no lugar de `BackgroundTasks.add_task`.

---

## Frontend

```bash
cd frontend
npm install

cp .env.example .env.local
# preencha VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_API_URL

npm run dev
# abre em http://localhost:5173
```

Telas:

- **/login** e **/cadastro** — autenticação via Supabase Auth.
- **/processos** — lista de processos da empresa, com botão "Novo processo".
- **/processos/:id/descricao** — texto opcional de domínio (e/ou ancoragem).
- **/processos/:id/upload** — drag-and-drop do vídeo + barra com etapas.
- **/processos/:id/dashboard** — KPIs, sugestões, distribuição de comportamentos.
- **/processos/:id/validacao** — *caixa de entrada*: fila enxuta dos eventos que
  pedem atenção agora (auto-validados já saem da fila) + perguntas proativas da IA.
- **/processos/:id/eventos** — *planilha mestre*: tabela com **todos** os eventos
  acumulados do processo, com busca, filtros (status / comportamento / vídeo),
  ordenação, paginação, edição por linha (com frames sob demanda e autocomplete de
  labels) e ações em lote. Aqui o cliente audita e **corrige a qualquer momento** —
  inclusive eventos já validados.
- **Prism** — *painel lateral deslizante* (estilo Copilot), disponível em **todas**
  as telas de dentro de um processo, aberto pelo botão flutuante no canto inferior
  direito. É o chat conversacional com a IA da plataforma:
  - **Conversas persistidas** em `prism_conversas` + `prism_mensagens` — recarregar
    a página traz o histórico.
  - **Tópicos**: você pode criar nova conversa, alternar entre elas, **renomear** e
    **excluir** as antigas. O título é gerado **automaticamente pela IA** após a
    primeira troca (e fica editável depois).
  - **Sugestões de assunto** geradas dinamicamente a partir do estado atual dos
    dados do processo — não são fixas; mudam a cada nova conversa.
  - **Escopo restrito**: o Prism só fala de melhorar processos com base nos dados
    do cliente. Fora disso, recusa em 1 frase e redireciona.
  - **Avatar**: dropar `frontend/public/prism.png` (256×256, fundo transparente).
    Enquanto o arquivo não existir, o avatar mostra um placeholder roxo neutro com
    a letra "P" — UI continua funcional.

> **Editar re-treina o sistema.** A memória do negócio
> (`carregar_memoria_do_negocio`) é recalculada do estado atual dos eventos a cada
> `processar_video`. Por isso, confirmar / corrigir / descartar / reabrir na tela
> "Eventos" tem exatamente o mesmo efeito de aprendizado que na "Validação": o
> próximo vídeo já reflete a correção. Reabrir devolve o evento à fila como pendente.

---

## Como o pipeline aprende

A lógica do notebook foi preservada 1-pra-1 em `backend/pipeline.py`:

- `carregar_memoria_do_negocio()` lê o histórico validado do par
  `(empresa, processo)` no Supabase e monta: vocabulário canônico,
  correções aprendidas (descrição → label correto) e descartes (falsos
  positivos).
- Essa memória é **injetada nos prompts** do VLM e do clusterizador, então
  o sistema fica mais consistente e mais barato a cada execução.
- Eventos cuja descrição bruta cai numa correção aprendida ou num label
  canônico com ≥ `LIMIAR_AUTO_VALIDACAO` confirmações são **auto-validados**
  e não vão para a fila humana.
- Tudo é isolado por `(empresa, processo)`. Dados de clientes diferentes
  nunca se misturam, mesmo que tenham labels idênticos.

### Classificação Lean dos comportamentos (Value-Added analysis)

Cada comportamento canônico (`comportamentos.label`) recebe uma
**categoria Lean**: `valor_agregado`, `apoio` ou `desperdicio`. Essa
classificação alimenta o **Índice de Valor Agregado** (KPI estrela do
dashboard) e colore os gráficos de Pareto e "Tempo por comportamento".

Como é obtida:
1. **Pela IA** (origem `'ia'`): ao final de `processar_video()`,
   `classificar_comportamentos_lean()` pede ao `gpt-oss-120b` que
   classifique cada comportamento ainda não-classificado, usando a
   descrição do processo + as respostas das perguntas proativas como
   base. Não-fatal.
2. **Pelo gestor** (origem `'humano'`): no dashboard, clicando no chip
   da categoria. O override do gestor **nunca é sobrescrito pela IA**.
   Limpar a categoria (botão `×` no popover) devolve o comportamento ao
   conjunto candidato à IA reclassificar na próxima execução.

A plataforma só mede **como o tempo das pessoas é gasto**. Por isso o
dashboard não inventa métricas que dependem de dados que não temos
(OEE, scrap, peças/hora, custo, parada de máquina). Tudo o que aparece
é derivado de `eventos`, `comportamentos`, durações, %, sequências,
validações e da categoria Lean.

### Perguntas proativas (loop de aprendizado pelo diálogo)

Ao final de cada vídeo processado, a IA também pode **fazer perguntas** ao
cliente sobre lacunas que detectou (descrições parecidas com labels
diferentes, ações que ficaram como `acao_indefinida`, transições estranhas,
ordem de passos pouco clara). As perguntas são geradas pelo próprio LLM —
não vêm de um catálogo — e ficam em `perguntas_processo`.

> Ciclo: **IA detecta lacuna → cria pergunta → cliente responde no fluxo de
> validação → resposta é injetada nos prompts do VLM, cluster, análise e
> chat junto da descrição do processo**.

Sem isso, as respostas não realimentariam nada. Com isso, perguntas
respondidas viram verdade do domínio e ficam permanentes para as próximas
análises daquele `(empresa, processo)`. O cliente também pode dispensar
uma pergunta — ela não volta a aparecer.

---

## Não exponha segredos no front

- `SUPABASE_KEY` (service_role) e `GROQ_API_KEY` vivem só no `.env` do
  backend.
- O front usa apenas `VITE_SUPABASE_ANON_KEY` — mesmo se vazasse, a RLS
  só permite ler dados da própria empresa.

---

## Limitações do MVP

- Job tracker em memória — se o servidor reiniciar durante um job, o
  status do job é perdido (o vídeo já está no Supabase, então é só fazer
  upload de novo). Para produção, use Redis/RQ/Celery.
- Sem billing, sem múltiplos papéis dentro da mesma empresa.
- O bucket `videos` deve ser privado (a API baixa via service_role).
