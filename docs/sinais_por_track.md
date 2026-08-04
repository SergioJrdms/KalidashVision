# Sinais disponíveis por track — o que existe hoje, sem modelo novo

Levantamento para o experimento de separabilidade que antecede a identificação
do operador titular. Nada aqui propõe identificar ninguém: é o inventário do que
a detecção **já calcula** e onde cada sinal morre.

Escrito na Fase 82, junto com o conserto do `bbox` (que era o primeiro item da
lista e estava indo a zero).

---

## Resumo em uma tabela

| Sinal | Onde nasce | Custo | Persistido | Serve de descritor? |
|---|---|---|---|---|
| `bbox` (x1,y1,x2,y2) | YOLO, toda amostra | zero (já vem) | **sim** (F82) | **sim** — altura aparente |
| `bbox_stats` | agregado do evento | zero | **sim** (F82) | **sim** — mediana + dispersão |
| razões corporais | kpts do yolo11n-**pose** | zero (já vêm) | **sim** (F83) | **sim** — invariantes à escala |
| histograma de cor sup/inf | recorte HSV do frame | ~0 | **sim** (F83) | talvez — depende do uniforme |
| `altura_rel` / `aspecto` por track | agregado do track | zero | **sim** (F83) | **sim**, com ressalvas |
| tempo na zona do posto | contador da eleição | zero | **sim** (F83) | **sim** — "quem fica" |
| `kpts` crus (17 pontos) | yolo11n-pose | zero | **não** (só as razões) | matéria-prima |
| `crop` 32×32 **cinza** | `_crop_cinza_pequeno` | ~0 | **não** | fraco (sem cor) |
| `centro` (cx, cy) | derivado do bbox | zero | não | fraco sozinho |
| `zona` / `papel` | ROIs desenhadas | zero | **sim** | contexto, não aparência |
| `maos_maquina` | punho na zona 'maquina' | zero | **sim** (F82) | não |
| `track_id` | BoT-SORT | zero | **sim** | só dentro do vídeo |

> **Atualizado na Fase 83.** O que era "não persistido" nas linhas de razões,
> cor e tempo de posto passou a ser gravado em `descritores_track`, chaveado por
> `(video_id, pessoa_track_id)` e com `cam_id` junto. Continua sem identificar
> ninguém: é insumo do experimento de separabilidade.

---

## 1. `bbox` — a caixa. **Consertada nesta fase.**

**Estava zerada** para `operador` e `posto_vazio`; só `visitante` tinha
coordenada real. Duas causas, ambas na escrita:

1. **Resgate pela cam2.** Quando a cam1 não vê o operador (atrás do torno) e a
   cam2 vê, o evento nasce do 2º ângulo. O detector da cam2 calculava a caixa e
   o laço guardava **só o booleano** `achou` — a caixa era descartada na linha
   seguinte e a observação nascia com `(0,0,0,0)`.
2. **Posto vazio.** Não há pessoa, e mesmo assim se gravava `(0,0,0,0)`.

Zero não é ausência: é a afirmação de uma pessoa de tamanho nenhum no canto
superior esquerdo da imagem. E era lida como medida — `montar_fato_evento`
somava esse ponto fantasma no cálculo de deslocamento, o que fazia o sinal
`movimento` dizer **"andando"** num minuto de gente parada.

**Agora:** a caixa da cam2 é guardada (`Amostra.bbox_cam2`), `bbox_cam` diz de
qual câmera são as coordenadas, e caixa que não mede nada vira **NULL**.

### O que está gravado por evento

```
bbox_inicio  jsonb  {x1,y1,x2,y2} da 1ª amostra COM caixa — ou null
bbox_cam     text   'cam1' | 'cam2' | null
bbox_stats   jsonb  { n, cam, altura_med, altura_min, altura_max,
                      largura_med, aspecto_med, altura_rel, frame_h }
```

`altura_med` é **mediana das amostras do evento**, não um frame só — um frame
pega o operador agachado ou meio ocluso e mente. `altura_rel` = altura ÷ altura
do frame: é o que torna a medida comparável entre vídeos, resoluções e câmeras.
`aspecto_med` = largura ÷ altura (de pé é "magro", agachado é "largo").

### Cuidados para o experimento

- **Nunca compare cam1 com cam2 em pixel.** Ângulo, distância e resolução são
  outros. Filtre por `bbox_cam`, ou compare só `altura_rel` — e mesmo assim,
  separadamente por câmera.
- **A distância à câmera domina a altura aparente.** Duas pessoas do mesmo
  tamanho a 3 m e a 6 m dão alturas 2× diferentes; a mesma pessoa em dois pontos
  do posto também. Com câmera fixa e zona pequena isso é uma constante — com
  zona grande, é ruído maior que o sinal. **Condicione por região**: `centro_x`
  em faixas, ou normalize pela posição do pé (`y2`), que é uma proxy grosseira
  de profundidade no plano do chão.
- **Postura é confundidor.** Agachar reduz a altura em 40%. Use `aspecto_med`
  para separar, ou filtre por rótulo (só `operar_torno`, por exemplo).
- **Histórico:** os zeros antigos ficam gravados. O filtro é

```sql
where bbox_inicio is not null
  and (bbox_inicio->>'y2')::numeric - (bbox_inicio->>'y1')::numeric > 1
```

---

## 2. Razões corporais (dos `kpts`) — **persistidas na Fase 83**

O modelo é `yolo11n-pose`: os keypoints já vêm em toda detecção, custo zero.
Eram usados só no teste de zona e no gate, e descartados. Agora viram três
razões, medianadas por track.

### O critério de escolha (a pergunta que você fez)

1. **Só landmarks rígidos.** Ombro, quadril e nariz não se articulam entre si.
   Cotovelo, punho, joelho e tornozelo estão **fora**: mudam com a ação, não com
   a pessoa — e neste enquadramento ficam atrás do torno a maior parte do tempo.
2. **Razão, nunca medida absoluta.** Dividir cancela a escala. É o ponto inteiro
   de trocar a altura aparente por isto.
3. **Mesmo eixo quando dá.** `quadril_ombro` é horizontal ÷ horizontal: não muda
   quando a pessoa se inclina para a frente (o que encurta a projeção vertical do
   tronco). As que misturam eixos são mais informativas e menos estáveis — por
   isso cada uma vai com a **sua dispersão**, e o experimento decide o peso.
4. **Denominador com tamanho mínimo** (`KV_RAZAO_MIN_PX`, 12px): abaixo disso é
   ruído dividido por ruído e a razão explode.

| razão | o que é | eixo |
|---|---|---|
| `ombro_tronco` | largura dos ombros ÷ tronco | horizontal ÷ vertical |
| `quadril_ombro` | largura do quadril ÷ ombros | horizontal ÷ horizontal |
| `cabeca_tronco` | nariz→pescoço ÷ tronco | vertical ÷ vertical |

### Duas armadilhas resolvidas no caminho

- **`xyn` normaliza x pela largura e y pela altura.** Medir distância direto no
  normalizado, num frame 640×480, estica o eixo horizontal em 33% e a razão vira
  ficção. O código volta para pixel antes de qualquer distância — há teste.
- **Keypoint não detectado vem `(0,0)`** — o mesmo zero mentiroso da caixa na
  Fase 82. Filtrado; um nariz ausente não vira `cabeca_tronco`.

### O que NÃO é invariante, e precisa estar dito

Nada disto sobrevive a uma **rotação grande do corpo** (yaw). De costas, a
largura de ombros projetada encolhe e `ombro_tronco` cai junto. A dispersão por
track (`*_mad`) é a medida disso — e é ela que responde se o sinal serve neste
ambiente.

---

## 3. Histograma de cor — **acrescentado na Fase 83**

O recorte do gate (`_crop_cinza_pequeno`) continua existindo e continua cinza:
ele serve ao gate, não ao descritor. Ao lado dele, `histograma_cor` tira do
**mesmo frame BGR** dois histogramas HSV — metade superior (camisa) e metade
inferior (calça), separadas.

Três decisões que valem a pena conhecer antes de interpretar os números:

- **Sem o canal V (brilho).** Só matiz × saturação. É o brilho que muda entre a
  luz das 6h e a das 15h; deixá-lo entrar faria a mesma pessoa virar duas ao
  longo do dia. Há teste: a mesma roupa com 45% menos luz continua parecida
  consigo mesma.
- **Faixa central da caixa** (60% da largura, `KV_HIST_FAIXA`). A bbox de uma
  pessoa tem fundo nos cantos; a coluna do meio é quase toda corpo.
- **Média dos histogramas do track**, renormalizada — histograma é distribuição,
  somar amostras é o agregado natural.

**Ressalva que não muda:** com uniforme igual nos dois torneiros, isto não separa
ninguém. Vai junto porque é o mais barato de todos e porque "não separa" também
é resultado do experimento.

---

## 4. Tempo na zona do posto — **persistido na Fase 83**

`tempo_posto_s` = nº de amostras do track dentro da zona × intervalo de
amostragem. É **estimativa**, não cronometragem: a amostragem é sistemática.

É um sinal comportamental forte — o titular passa o turno ali, o visitante passa
minutos — e some no fim do vídeo. Não é aparência, mas para o problema real
("medir o torneiro, não o posto") pode valer mais que aparência. Serve também
como **rótulo fraco** para montar o dataset do experimento sem marcar nada à mão.

---

## 4b. Onde tudo isso é gravado

Tabela `descritores_track`, uma linha por `(video_id, pessoa_track_id)`:

```
cam_id, papel_predominante, n_amostras, n_amostras_posto,
tempo_posto_s, tempo_visivel_s, altura_rel, aspecto,
razoes {nome: {med, mad, n}}, hist_sup[32], hist_inf[32], hist_bins,
bbox_ref (NORMALIZADA 0-1), frame_ref, frame_w, frame_h
```

`upsert` na chave, e a gravação é **não-fatal**: um experimento não pode ser o
motivo de um vídeo da campanha falhar.

### Exportação

```
GET /processos/{id}/descritores/dia?dia=AAAA-MM-DD
```

Devolve um `.zip` com `descritores.csv`, `descritores.json`, `recortes/*.jpg`
(um por track) e um `LEIA-ME.md`. Os recortes saem do frame que **já está** no
Storage, cortados pela `bbox_ref` normalizada — nenhum byte novo é gravado, o
que importa depois de o bucket ter estourado uma vez nesta campanha.

---

## 5. O que NÃO existe e seria custo novo

- **Embedding de reidentificação** (OSNet, FastReID e afins): outro modelo, outro
  peso, outra inferência por pessoa por frame. É a solução canônica e é a mais
  cara.
- **Face**: a câmera é de posto industrial, o operador fica de costas ou de lado
  a maior parte do tempo, e há a questão de privacidade a decidir com o cliente
  antes de qualquer coisa.
- **Continuidade entre vídeos**: `track_id` **não** atravessa vídeos — o tracker
  é resetado de propósito a cada vídeo (Fase 64), senão o estado do BoT-SORT
  vazava de um vídeo para o outro. Qualquer identificação entre segmentos precisa
  ser por descritor, nunca por id.

---

## 6. Fase 84 — a cam2 não estava sendo descrita

**Sintoma:** num dia com 53 segmentos de cada câmera e 54 vídeos processados,
`descritores_track` tinha 90 tracks de cam1 e **4 de cam2**, todos de um único
vídeo, sem nada novo desde o dia anterior.

**Causa — estrutural, não de zona.** O pareamento elege como primária a câmera
de **menor id**: `# primário = câmera de MENOR id (cam1) — dirige a
detecção/tracking`. O tracking (e, com ele, o acumulador do descritor) roda em
`etapa_detectar_e_amostrar`, que só vê o vídeo primário. A cam2 entrava apenas
em `_anexar_segundo_angulo`, com `yolo.predict` — **sem tracker, logo sem id**,
e do resultado só sobreviviam dois booleanos (`op_cam2`, `maos_cam2`).

As 4 linhas de cam2 que existiam vieram de um segmento processado **solo**:
sem par, a cam2 vira primária e é rastreada como qualquer vídeo. Por isso eram
de um vídeo só e de um horário só.

**Correção:** o passe da cam2 usa `track` no lugar de `predict` — mesmo
detector, mesmos parâmetros, mesmas caixas (o veredito `op_cam2` não muda), no
mesmo frame já decodificado e na mesma inferência que já acontecia. Custo
adicional: o overhead do associador, desprezível.

**Três consequências que precisam estar ditas:**

1. **Os tracks da cam2 fragmentam mais que os da cam1.** Os frames da cam2 vêm
   por *seek* (instante alvo = `tempo_s + offset`), não em sequência: a predição
   de movimento do BoT-SORT erra mais e troca de id com mais frequência. Para
   agrupar-por-aparência-primeiro isso é aceitável. Para contar tempo por track
   na cam2, **não é** — não faça isso.
2. **Depende das zonas da cam2.** A inferência na cam2 só roda quando há zona de
   `posto_operador` desenhada nela (`posto_sec`). Sem zona na cam2, não há
   descritor de cam2 — a suspeita de dependência de ROI existe, só não era a
   causa principal.
3. **A chave mudou.** cam1 e cam2 numeram tracks de forma independente: as duas
   têm um track 1. A unicidade passou a ser `(video_id, cam_id,
   pessoa_track_id)`, e `cam_id` virou `not null` — coluna de chave com NULL não
   deduplica.

---

## 7. Fragmentação: o que ela faz com o descritor

Medição do dono num turno de 8h48 na cam1: **57 de 90 tracks com 8 segundos** (o
mínimo — que, com o intervalo de amostragem configurado, é **uma única
amostra**), média 17 s, só 3 acima de 60 s.

O que isso faz com cada sinal:

| sinal | com n=1 | por quê |
|---|---|---|
| histograma de cor | **utilizável** | uma amostra são milhares de pixels; a distribuição já é densa |
| `altura_rel`, `aspecto` | utilizável, ruidoso | uma medida de um instante; postura entra inteira |
| razões corporais | **frágil** | exigem ombros **e** quadris detectados no mesmo frame — atrás do torno, muitas amostras não produzem razão nenhuma |
| `*_mad` | **inexistente** | não há dispersão em uma medida |

**Correção que veio junto:** `_mad` devolvia `0.0` com uma amostra. Numa planilha,
0 lê-se como "perfeitamente estável" — a leitura oposta da verdade, que é "não
há como saber". Com 57 de 90 tracks nessa situação, esse zero seria a maioria da
coluna e o experimento concluiria estabilidade onde ninguém mediu duas vezes.
Agora vem **nulo** abaixo de 3 amostras. É o mesmo erro do `bbox` (0,0,0,0) da
Fase 82: ausência de medida vestida de medida.

**O descritor continua servindo — como unidade fraca.** Agrupar primeiro e somar
o tempo depois é justamente o desenho que tolera n baixo: 90 observações fracas
formam grupos fortes, enquanto 90 julgamentos por track não formariam nada. O
que não se pode fazer é ler uma linha isolada como "esta pessoa é assim".

---

## Ordem sugerida para o experimento

Revista depois da medição de fragmentação (Fase 84). A ordem antiga supunha
tracks longos; com 57 de 90 tracks de uma amostra só, a robustez a n baixo passa
a ser o primeiro critério de escolha.

1. **Histograma de cor** — o mais robusto com n=1, porque uma amostra já são
   milhares de pixels. Comece por ele, **uma câmera de cada vez**.
2. **Razões corporais**, filtrando por `*_n` — a razão só existe quando ombros e
   quadris aparecem no mesmo frame, o que atrás do torno é minoria. Espere
   perder linhas; é melhor perder do que preencher.
3. **`altura_rel`** condicionada por região do frame (o confundidor da distância
   continua lá; as razões existem justamente para não depender disto).
4. **`tempo_posto_s` como rótulo fraco**, não como sinal de agrupamento: some o
   tempo *depois* de agrupar. Somar antes é a ordem que a fragmentação quebrou.

Dado que só vale a partir de agora: eventos anteriores à Fase 82 têm
`bbox_stats` nulo e `bbox_inicio` zerado nos papéis 'operador' e 'posto_vazio', e
não existe `descritores_track` nenhum antes da Fase 83. O experimento precisa de
vídeos processados **depois** destes deploys.
