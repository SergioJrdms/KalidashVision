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

## Ordem sugerida para o experimento

1. **Altura relativa por câmera** (`bbox_stats.altura_rel`), condicionada por
   rótulo e por região do frame. É o que já está gravado a partir de agora.
2. **Razões de keypoints** — invariantes de escala, custo zero, só não estão
   persistidas.
3. **Histograma de cor** — o único que exige acrescentar código de captura, e
   ainda assim trivial.
4. `presenca_zona` como âncora de rótulo fraco: quem ficou o turno inteiro é o
   titular *daquele vídeo*, e serve para rotular os dados do experimento sem
   ninguém marcar à mão.

Dado que só vale a partir de agora: eventos anteriores à Fase 82 têm
`bbox_stats` nulo e `bbox_inicio` zerado nos papéis 'operador' e 'posto_vazio', e
não existe `descritores_track` nenhum antes da Fase 83. O experimento precisa de
vídeos processados **depois** destes deploys.
