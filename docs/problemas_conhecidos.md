# Problemas conhecidos

Coisas que sabemos que estão erradas, com o motivo de ainda não terem sido
consertadas. Um problema documentado é uma decisão; um problema esquecido é
uma armadilha para quem chegar depois.

---

## 1. Reprocessar um vídeo DUPLICA tudo

**Estado:** não corrigido. Uma guarda impede o acidente.
**Descoberto em:** Fase 71, ao avaliar reprocessar 48 vídeos suspeitos de
contaminação (que a medição depois mostrou não estarem contaminados).

`etapa_persistir` grava assim:

```python
video_row = sb.table("videos").insert(linha_video).execute()
```

`insert`, sem upsert e sem dedup por `caminho`. Processar o mesmo arquivo duas
vezes cria uma **segunda** linha em `videos` e um **segundo** conjunto completo
de eventos. Tudo passa a contar em dobro: minutos observados, produtividade,
placar, curva de dúvida.

O dedup que existe (`_segmento_ja_existe`) é na **inbox**, no momento do
upload. Ele impede subir o mesmo arquivo duas vezes; não protege contra
reprocessar o que já virou eventos.

### A guarda (Fase 72)

`_barrar_duplicata` levanta `VideoJaProcessado` em dois pontos:

1. no início de `processar_video`, **antes** de qualquer inferência — para não
   pagar VLM e YOLO por um vídeo que seria recusado no fim;
2. imediatamente antes do `insert` em `etapa_persistir` — a garantia real,
   porque toda escrita passa por ali.

A guarda **não** é a correção. Ela troca uma corrupção silenciosa por um erro
alto. Se a leitura da checagem falhar, ela deixa passar de propósito: a guarda
não pode virar o motivo de um vídeo legítimo não ser processado.

### Caso de borda: segmento em ERRO com vídeo já gravado

Se um job falhar **depois** do `etapa_persistir` (por exemplo em
`montar_contexto_agregado`, que não está em `try`), o segmento fica em `erro`
mas o vídeo e os eventos já existem. `POST /fila/reprocessar-erros` vai
reenfileirá-lo e a guarda vai recusá-lo — corretamente, porque reprocessar
duplicaria.

O certo nesse caso é marcar o segmento como `concluido`: o processamento
chegou ao fim. A mensagem da exceção diz isso.

### Como consertar de verdade, quando for a hora

Substituição idempotente em `etapa_persistir`:

1. procurar vídeo por `(empresa, processo, caminho)`;
2. se existir, **apagar os eventos daquele `video_id`** e reusar a linha, em
   vez de inserir outra;
3. suprimir a expiração do binário (Fase 54) nesse caminho — hoje ela roda no
   fim do processamento e apaga o arquivo, o que torna o reprocesso *one-shot*;
4. limpar os frames órfãos: eles são gravados com a chave do id do evento, e
   eventos novos geram chaves novas.

**E antes de qualquer uma dessas coisas, decidir o que fazer com as correções
humanas.** Reprocessar cria eventos com ids novos; as decisões tomadas nos
eventos antigos não sobrevivem. Reaplicá-las por aproximação (janela de tempo
+ track) **não funciona**: os `pessoa_track_id` mudam a cada vídeo desde a
Fase 64, e a consolidação por minuto pode bucketizar diferente — a decisão
acabaria colada num evento que talvez não seja o julgado, e o erro seria
silencioso. Reprocessar apenas vídeos sem correção humana é a saída segura.

### Por que não foi corrigido agora

Avaliado na Fase 71 e recusado pelo dono do processo, com razão. Duas coisas
foram medidas depois:

1. o contágio **parou de crescer** no primeiro ciclo após o conserto do prompt
   (três assinaturas independentes zeradas às 12:00 UTC contra 49 eventos às
   11:00);
2. e a limpeza devolveu **`contaminados: 0`** — o que estava correto. A
   decomposição dos 114 eventos que as queries manuais pegavam:
   **75** eram `principal=false, origem='auditoria'` (registro de auditoria,
   que o filtro `principal is not False` já removia de toda métrica),
   **29** eram `principal=true, origem='humano'` (correções do próprio gestor,
   que a limpeza recusa tocar de propósito) e **10** eram
   `principal=false, origem='humano'`.

**A contaminação nunca chegou às métricas.** O risco real era o prompt
ensinando o remapeamento — isso sim teria atingido eventos principais nos dias
seguintes, conforme mais correções fossem feitas. A Fase 67 fechou antes disso.

Trocar um estrago que não existia por escrita idempotente em produção, com a
campanha de 30 dias rodando, seria o negócio errado.

---

## 4. O Storage estourou porque a expiração nunca rodava

**Estado:** corrigido (Fase 76). Fica registrado porque a causa se repete.

O bucket foi de 0 a **979 MB de 1 GB em 4 dias** e a campanha quase parou.
Duas causas independentes, e a segunda era a maior:

**A varredura nunca foi agendada.** `varrer_videos_expirados` existia desde a
Fase 54 e só era alcançável pelo endpoint manual. Ninguém nunca o chamou.
*"Existe um endpoint" não é um mecanismo* — é uma tarefa manual esperando ser
esquecida num fim de semana.

**A varredura ignorava a cam2.** O segmento do 2º ângulo é outro objeto e não
tem linha em `videos`. `expirar_binarios_do_video` o apagava inline, mas só no
caminho `RETER_VIDEO_HORAS == 0`; com retenção ligada ele retorna cedo (só
carimba `frames_aquecidos_em`) e a varredura, que olhava apenas
`videos.caminho`, nunca o alcançava. Num setup de duas câmeras isso significa
**metade do bucket imortal**.

### O relógio escolhido

O **heartbeat do Pi**, com throttle de 1×/hora (`KV_VARREDURA_INTERVALO_MIN`).
Ele chega a cada poucos minutos, 24/7, e não depende de ninguém lembrar —
mesmo padrão já provado em `_limpar_heartbeats_antigos`. Render Hobby não tem
cron, e uma thread morre junto com o processo quando o serviço hiberna; o
pulso, não. Rede secundária no `startup` (cobre o Pi desligado) e o endpoint
manual continua existindo.

A varredura é **não-fatal** nos dois pontos: se ela falhar, o heartbeat e o
boot seguem. Uma limpeza quebrada não pode derrubar a coleta.

### Regra que fica

Toda rotina de manutenção precisa de um **relógio** no momento em que é
escrita. Endpoint sem gatilho é dívida, não funcionalidade.

---

## 5. O teto de linhas do PostgREST comia dias inteiros de gravação

**Estado:** corrigido (Fase 81). Fica registrado porque a falha é invisível por
natureza e o padrão errado é o que qualquer um escreveria.

O dono abriu o dia 29 — gravado do começo ao fim — e a plataforma mostrou
algumas faixinhas soltas. Pior: o dia 28, que na véspera aparecia completo,
tinha ganhado um buraco entre 8:48 e 9:00. Nada tinha sido apagado.

`.limit(50000)` **não pede 50 mil linhas**. O PostgREST corta toda resposta no
seu `max-rows` (1000 no Supabase) e devolve as primeiras 1000 **sem erro, sem
aviso, sem header**. Uma resposta truncada é indistinguível de uma completa —
é por isso que isto sobreviveu tanto tempo e por isso que nenhum teste pegava:
os dublês devolviam tudo o que tinham.

O caminho do estrago no "Dia a dia":

```
videos  → .limit(50000)  → volta só 1000
        → inicio_por_video fica sem os demais
eventos → dt0 is None    → continue   ← o dia do operador desaparece aqui
```

Como o corte segue a ordem física da tabela, ele **se move a cada gravação
nova**: um dia cheio ontem vira um dia esburacado hoje. Não havia bug de
exibição nenhum — a tela desenhava fielmente os dados que recebia.

Alcance: além do "Dia a dia", estavam truncados o **dashboard principal**, a
**série temporal**, o **snapshot do chat** e o **contexto agregado** (que vai no
prompt do modelo — memória lida pela metade faz o sistema reaprender o que já
foi corrigido). A leitura de segmentos pendentes do orquestrador também: um par
cam1/cam2 podia cair dos dois lados do corte e o vídeo era processado com um
ângulo só.

### A correção

`varrer()` (em `pipeline.py`, sobre o `_scan_todos` que já existia) — pagina por
`.range()` com `.order()` numa chave única. Toda leitura de tabela que cresce
passa por lá.

Dois defeitos vizinhos apareceram junto e foram corrigidos:

- `montar_serie_temporal` pegava os **500 vídeos mais antigos**
  (`order(processado_em, desc=False).limit(500)`): passados 500 vídeos, a curva
  de evolução congelava no começo da campanha;
- consultas **sem `.limit()` nenhum** sofrem o mesmo teto — `videos` no
  dashboard era uma delas.

### Regra que fica

Toda leitura de `eventos`, `videos`, `segmentos` e afins passa por `varrer()`.
`.limit(n)` só vale como **corte deliberado** com `n` ≤ 1000 (ex.: "as 12
sugestões mais recentes"). `tests_teto_postgrest.py` varre o fonte e falha se o
padrão voltar — e o dublê dele **aplica o teto**, como o servidor faz.

Corolário mais amplo: um dublê de teste que é mais generoso que o serviço real
não testa nada. Se o servidor corta, o dublê corta.

---

## 6. O prompt escolhia o lado — e o teto de 75% era do instrumento

**Estado:** corrigido (Fase 85). Fica registrado porque a causa é sutil e o
padrão errado é o que qualquer um escreveria.

**O sintoma medido pelo dono:** dos 5 comportamentos existentes, 4 eram
produtivos e o único improdutivo era `posto_vazio`. `monitorar_maquina` comia
31% do tempo e concentrava **100% da dúvida**. O sistema não tinha como
registrar improdutividade com o operador presente, e a produtividade de 75% era
**o teto do que o instrumento media**, não um resultado.

**A causa não era (só) viés do modelo.** O prompt *mandava* o rótulo produtivo:

> "Se ele está PARADO, de pé, braços ao lado do corpo, apenas OLHANDO/
> acompanhando a máquina ou a área, é 'monitorando o ciclo da máquina' ou
> 'observando a operação' — NÃO é operar. **Na dúvida entre operar e monitorar,
> escolha MONITORAR.**"

Duas saídas, as duas produtivas. Não existia terceira — a dúvida não tinha para
onde ir, e por isso se acumulava toda num rótulo só.

### A correção de fundo: o VLM estava classificando, não descrevendo

"é monitorando o ciclo da máquina" já é uma **escolha de rótulo**, feita na
etapa que deveria só descrever. A arquitetura é descrição → cluster → rótulo →
categoria Lean. Devolvendo a descrição ao seu lugar, o vocabulário aberto (que
já existia — foi assim que `limpando_cavaco` e `medir_peca` nasceram) volta a
funcionar sozinho, sem lista fechada de rótulos improdutivos.

A calibração vive nos **exemplos**, e é deliberadamente simétrica: dois deles
têm a **mesma postura** e diferem só no estado da máquina —
`"parado de frente ao torno, máquina em ciclo"` contra
`"parado ao lado do torno, máquina parada"`. O prompt nunca diz qual é
produtivo. Esperar ciclo é produtivo por decisão do dono do processo, tomada na
categoria Lean; ociosidade não é. O discriminador (a máquina) é o que torna os
dois separáveis.

### As três portas dos fundos

Prompt novo sozinho nasceria pela metade. Três caminhos independentes
convertiam ausência de informação em trabalho produtivo:

1. **O gate de repetição** (Fase 23) suprime pose idêntica sem chamar o VLM —
   que é *exatamente* o sinal de imobilidade que a mudança precisa observar.
2. **`_eh_indefinida` herda a última ação conhecida** (Fase 34): "ação não
   identificada" virava `operar_torno`. É a conversão mais silenciosa de
   DESCONHECIDO em PRODUTIVO que o sistema tinha.
3. **A ponte temporal** herda sem ver imagem nenhuma.

**Um princípio resolve os três: herdar é aceitável por instantes, não por
minutos.** Toda herança sem evidência nova ganhou um teto
(`KV_GATE_MAX_REPETICOES`, `KV_HERANCA_MAX_SEGUIDAS`); passado o teto, o sistema
volta a OLHAR. "Parado há 2 minutos" é informação; não é a mesma coisa que
"parado há 8 segundos".

### Regra que fica

Prompt que oferece um conjunto fechado de saídas está classificando, não
descrevendo — e o conjunto que ele oferece vira o teto do que o sistema
consegue medir. Antes de acreditar num número, pergunte que respostas o
instrumento *permitia*.

---

## 7. Rótulo novo nasce como não-produtivo — e isso mexe no número

**Estado:** mitigado (Fase 85, tela "Classificar rótulos"). Não é bug: é uma
convenção certa com um efeito colateral que precisa de tela.

Desde a Fase 63 `categoria_efetiva()` nunca devolve None: rótulo sem categoria
Lean conta como **NÃO-PRODUTIVO** (`CATEGORIA_SEM_EVIDENCIA`). A convenção está
certa — sem prova de que agrega valor, não agrega — e é melhor que a anterior,
em que `limpando_cavaco` ficava fora dos dois lados da conta.

O efeito colateral aparece quando o vocabulário cresce de uma vez: parte dos
rótulos novos é trabalho produtivo de verdade, e **a produtividade cai por
CONTABILIDADE antes de cair por MEDIÇÃO**. No gráfico de um dia as duas quedas
são indistinguíveis.

A saída não é adiar a mudança do instrumento — é conseguir classificar rápido, e
**do mais caro para o mais barato em tempo acumulado**, porque é o tempo que move
o número. A tela ordena por minutos (não por contagem de eventos: 4 eventos de
15 min pesam mais que 300 de 8 s) e distingue *nunca classificado* de
*assumido pelo fallback* — o primeiro espera decisão, o segundo esconde uma
decisão que a máquina tomou sozinha.

---

## 8. O gargalo saiu do VLM e foi para o cluster

**Estado:** corrigido (Fase 86).

Com o prompt novo (Fase 85) as descrições melhoraram — apareceram "sem mudança
de posição", "máquina parada", "máquina em ciclo". Mas no banco:

```
"parado ... máquina em ciclo, observando"   → monitorar_maquina (produtivo)
"sem mudança de posição ... máquina parada" → monitorar_maquina (produtivo)
```

Situações **opostas** — espera produtiva contra ociosidade — colapsadas no mesmo
rótulo. É exatamente o par calibrador escrito no prompt do VLM: ele sobrevive à
descrição e morre no cluster.

**A causa está no `PROMPT_CLUSTER`, e é minha.** Ele foi escrito para colapsar
sinônimos e diz isso três vezes: o exemplo é `"digitando no PC"` = `"operando o
computador"`; manda usar labels da AÇÃO *"não da localização"* (e "máquina em
ciclo" não é verbo+objeto, logo lê-se como enfeite); e tem um
**"PRIORIDADE MÁXIMA: reuse o label canônico"** com `monitorar_maquina` a 31% do
tempo servindo de atrator.

### A correção: tornar o colapso impossível, não desencorajá-lo

1. **O discriminador vira CAMPO**, não frase: o VLM devolve `"maquina":
   "ciclo"|"parada"|null` e `"imovel"` por instante. Extrair "máquina em ciclo"
   do texto por regra seria frágil — ele escreve "torno girando" com a mesma
   facilidade.
2. **Partição determinística**: as descrições são separadas por
   `(maquina, imovel)` e o cluster roda **uma vez por partição**. Duas situações
   opostas não caem no mesmo grupo porque **nunca estiveram na mesma lista**.
3. **Sufixo mecânico por código**: a LLM ainda pode devolver `monitorar_maquina`
   nas duas partições; o sufixo (`_ciclo`, `_parada`) é aplicado depois.
   Aplicado **sempre** que o discriminador existe — nunca só quando as duas
   variantes coexistem no lote, senão a mesma situação ganharia labels
   diferentes em dias diferentes.

O sufixo **não batiza o Lean**: nada de `esperar_ciclo` ou `ocioso`. Isso seria
a máquina decidindo produtivo/improdutivo, que é a decisão do gestor.

### O histórico, e por que a família importa

Depois do deploy convivem `monitorar_maquina` (histórico, produtivo, 31% do
tempo), `monitorar_maquina_ciclo` e `monitorar_maquina_parada`. A tentação é
tratar o histórico como uma quarta categoria ou renomeá-lo. As duas coisas
estão erradas.

**A soma da FAMÍLIA é comparável entre semanas; a decomposição não.** Julho tem
100% da família sem discriminador; agosto tem ciclo/parada/sem. O que mudou foi
a **resolução** com que sabemos decompor esse tempo — não o tempo. A tela mostra
a família com as variantes dentro, e o histórico aparece como o que é.

E uma armadilha que ficou registrada: **variante nova NÃO herda a categoria da
raiz.** Herdar pareceria conveniente e faria `monitorar_maquina_parada` nascer
*produtivo* — recriando exatamente o que a Fase 85 consertou.

---

## 9. "de frente ao torno" era muleta do VLM

**Estado:** corrigido (Fase 86).

A frase aparecia em quase toda descrição do dia, **inclusive com o operador de
costas para o torno lendo desenho técnico**. O VLM não enxerga orientação nessa
resolução e preenche com o plausível — o mesmo mecanismo que produzia
"monitorando a máquina".

O sinal existe e é grátis: os 17 keypoints do `yolo11n-pose`. Rosto visível =
de frente para a câmera; ombros sem rosto = de costas; ombros projetados quase
no mesmo x = de perfil. Injetado no contexto como fato de sensor, igual ao
`maos_maquina`, e o prompt ganhou **"ORIENTAÇÃO NÃO SE ADIVINHA"**.

**Um erro meu que o teste pegou:** a primeira versão usava a distância entre os
ombros como referência de escala para decidir "perfil" — mas essa é a própria
grandeza sendo medida, e a razão dava ~1 sempre. A referência certa é o
**tronco** (rígido), com a altura da caixa como reserva.

### De frente para a CÂMERA não é de frente para o TORNO

Câmera e torno são fixos, então a relação é uma constante por câmera —
configurada em `zonas_camera.frente_maquina` (`camera` | `oposta` | `perfil`).

**Sem configuração o sistema afirma só o que sabe** ("de costas para a câmera")
e proíbe o VLM de falar do torno. Isso já mata a muleta; o campo é refinamento,
não pré-requisito.

---

## 10. O cluster roda por vídeo — daí a inconsistência

**Estado:** mitigado (Fase 86, `KV_CACHE_CLUSTER`).

A mesma frase virou `monitorar_maquina` em 3 eventos e `lendo_desenho_tecnico`
em 2. Não é bug: `etapa_clusterizar` roda **dentro de `processar_video`** e
`mapa_descricao_label` é local. Dentro de um vídeo a frase tem um label só (é
chave de dict) — a divergência é **entre vídeos**, cada um re-agrupando do zero,
com lista diferente, num modelo estocástico. Agravantes: `temperatura=0.1` (agora
`0.0`) e, com a generalização desligada (Fase 62, correto), **nada** fixava o
label entre vídeos.

**Cache de consistência por match exato:** antes de chamar a LLM, procura a
frase normalizada no histórico do processo. Se já foi clusterizada, reusa.

**Isto não é a Fase 67.** Lá o problema era propagar uma *decisão humana* por
semelhança *semântica* e reescrever labels. Aqui: match exato de string, origem
máquina, não reescreve nada, não toca `validado_humano`. Duas salvaguardas
adicionais: frase com mais de um label no histórico é **descartada** do cache
(passado ambíguo não vira decisão), e o cache **só atende a cena cujo sufixo
bate** — senão ele desfaria a partição recém-construída. Atrás de
`KV_CACHE_CLUSTER`, ligado por padrão.

---

## 11. O discriminador de máquina media RUÍDO — e o rótulo afirmava mesmo assim

**Estado:** corrigido (Fase 88). O caminho para responder a pergunta de verdade
ainda está aberto.

A Fase 86 particionou o cluster pelo estado da máquina (`ciclo`/`parada`) e
colou o estado no **nome** do rótulo. Medindo o discriminador contra o próprio
dado, ele não mede.

**A prova é a persistência.** Estado de máquina é físico: um ciclo de torno dura
minutos, então minutos consecutivos têm de se parecer. Em pares REALMENTE
adjacentes (gap ≤ 90 s), com a **ação mantida** entre os dois minutos, contra o
que uma moeda com a mesma taxa-base produziria:

| ação mantida | pares | trocas | observado | moeda |
|---|---:|---:|---:|---:|
| `operar_torno` | 29 | 10 | 34,5% | 28,7% |
| `monitorar_maquina` | 24 | 10 | **41,7%** | 30,9% |

Não há persistência em lugar nenhum — o estado troca tanto quanto ou mais que um
sorteio. E a taxa-base é explicada pela **ação**, não pela máquina: 76% "ciclo"
quando o rótulo é `operar_torno`, 19% quando é `monitorar_maquina`, 0% quando é
`conversando_colega`. O VLM **deduz o estado da ação que ele mesmo acabou de
descrever** e devolve como se tivesse observado. `null` era só 3,7%: ele não
admite que não viu.

Confirmação independente: em 4 eventos o campo estruturado diz `ciclo` enquanto
a prosa da **mesma resposta** diz "máquina parada".

**Por que isso era pior que um sinal fraco:** rótulo é AFIRMAÇÃO.
`monitorar_maquina_parada` diz que a máquina estava parada, e vai para relatório
que o sócio lê. Afirmação errada custa mais que informação faltando. Além disso o
vocabulário triplicava e **cada variante nascia sem categoria Lean**, ou seja,
contando como desperdício — a queda por contabilidade do item 7, multiplicada.

**A correção:** partição e sufixo desligados (`KV_PARTICAO_CENA`, off por
padrão — a máquina fica atrás de flag para voltar testada). O estado sai do nome
e vira coluna `cena_maquina`/`cena_imovel`, que **nenhum leitor de métrica
consome**: existe para ser confrontada com o movimento medido depois.

**O que fica aberto:** a pergunta "a máquina estava trabalhando?" continua sem
resposta, e com a partição off `monitorar_maquina` volta a ser um rótulo só,
produtivo, com 31% do tempo — o teto de 75% do item 6 volta a valer. O caminho é
medir MOVIMENTO no laço de tracking, que já decodifica a 6 fps (~360 pares de
frames por minuto contra os 7 da sequência), com máscara de pessoa para separar
movimento DA máquina de movimento NA frente dela, e `indisponivel` — nunca
`ausente` — quando a zona está ocluída.

---

## 12. Sufixo mecânico no mesmo namespace do vocabulário do LLM

**Estado:** corrigido (Fase 88), com resíduo no histórico.

O sufixo da Fase 86 era string colada no rótulo. O LLM do cluster batizava o
rótulo já com o estado dentro (`monitorar_maquina_parada`) e o sufixo era colado
por cima: nasceram `monitorar_maquina_parada_ciclo`, `operar_torno_ciclo_ciclo`,
`conversando_colega_parada_imovel`. E `monitorar_maquina_parada` era **ambíguo
por construção** — não dá para saber se é raiz + sufixo ou nome escolhido pelo
modelo.

`familia_label` tirava **um** sufixo, então a família de
`monitorar_maquina_parada_ciclo` dava `monitorar_maquina_parada`: um IRMÃO, não a
raiz. A árvore da tela de rótulos apontava para o lugar errado.

Corrigido descascando em laço. Os labels do histórico continuam existindo — não
são renomeados, pelo mesmo motivo de sempre: renomear reescreve o passado.

---

## 13. Camada de contradição: o silêncio era ambíguo — e um sufixo matou sete regras

**Estado:** corrigido (Fase 88).

Dois problemas independentes, achados investigando por que
`contradicao_posto_vazio_com_operador` não pegou eventos com
`papel_pessoa='operador'` rotulados `posto_vazio`.

**(a) O sufixo da Fase 86 matou toda camada de rótulo nomeado.** `_rotulo_casa`
faz match EXATO. Com os labels virando `operar_torno_ciclo`,
`monitorar_maquina_parada` etc., `quando_rotulo: ["operar_torno", ...]` parou de
casar. A regra `contradicao_ato_do_operador_sem_operador` (7 rótulos) e a
`suspeita_conversa_sem_operador` ficaram mortas desde 06/08 12:16, sem um único
sinal. Só sobreviveram as `['*']` e as de `posto_vazio` — este último porque é
explicitamente isento do sufixo no cluster.

**(b) `camadas_disparadas` NULL significava duas coisas incompatíveis:** "rodou
e nada disparou" e "nunca rodou". `carregar_camadas_duvida` devolve lista vazia
em qualquer exceção (só um `log.warning`) e a consolidação pula com um
`if camadas:`. Sem separar os dois, silêncio não prova nada — e nenhuma camada é
confiável, nem as recém-consertadas.

A coluna `camadas_avaliadas` separa os estados:

| valor | significa |
|---|---|
| `null` | o motor NÃO rodou neste evento |
| `{"aplicaveis": []}` | rodou, mas nenhuma regra mira este rótulo — **é a assinatura de (a)** |
| `{"aplicaveis":["X"]}` | X foi perguntada ao fato e não disparou |
| `{"erro":["X"]}` | X explodiu ao avaliar (≠ não disparou) |

**O que a investigação NÃO achou:** furo no motor. Reproduzindo o minuto exato
(rótulo `posto_vazio` + `papel_pessoa='operador'`), a regra dispara em toda
configuração testada — inclusive quando o operador aparece em só parte do minuto.
`operador_presente` é derivado dos crus do minuto, que já carregam `papel_pessoa`
desde `_abrir_evento`; as duas hipóteses de divergência (papel do evento × papel
do minuto, e fato montado antes do papel) estão refutadas em
`tests_rastro_camadas.py` blocos [1]–[4].

---

---

## 2. `gravado_em` carimba a tz do servidor no timestamp do nome

**Estado:** não corrigido. Sem efeito visível hoje.

`_parse_gravado_em_nome` lê o token `seg_YYYYMMDD_HHMMSS` do nome do segmento
(que é o relógio do Pi) e carimba a timezone do **servidor**. Os números são o
relógio de parede da fábrica, então hora e dia dos gráficos saem certos, e as
duas câmeras recebem tratamento idêntico — o offset se cancela no pareamento.

Só quebraria ao comparar com `processado_em`, que é UTC de verdade. Mexer nisso
mudaria a semântica de `gravado_em` no meio da campanha sem corrigir nada que
esteja visível.

---

## 3. A parcela `categoria_assumida` da curva de dúvida ainda se reescreve

**Estado:** parcial (Fase 66).

A curva "Quanto o sistema não sabe" passou a ser histórica: validar um trecho
não o apaga mais do dia em que aconteceu. Isso vale para as parcelas de
concordância, evidência e camadas — que são a maioria.

A parcela `categoria_assumida` continua se movendo quando alguém classifica o
rótulo, porque o evento **não registra** que a categoria dele foi assumida:
isso é lido do catálogo atual. Congelar exigiria carimbar o evento na ingestão.
