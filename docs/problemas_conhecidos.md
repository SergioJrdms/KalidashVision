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
