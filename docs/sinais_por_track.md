# Sinais disponíveis por track — o que existe hoje, sem modelo novo

Levantamento para o experimento de separabilidade que antecede a identificação
do operador titular. Nada aqui propõe identificar ninguém: é o inventário do que
a detecção **já calcula** e onde cada sinal morre.

Escrito na Fase 82, junto com o conserto do `bbox` (que era o primeiro item da
lista e estava indo a zero).

---

## Resumo em uma tabela

| Sinal | Onde nasce | Custo | Persistido hoje | Serve de descritor? |
|---|---|---|---|---|
| `bbox` (x1,y1,x2,y2) | YOLO, toda amostra | zero (já vem) | **sim** (Fase 82) | **sim** — altura aparente |
| `bbox_stats` | agregado do evento | zero | **sim** (Fase 82) | **sim** — mediana + dispersão |
| `kpts` (17 COCO, normalizados) | yolo11n-**pose**, toda amostra | zero (já vem) | **não** | **sim** — proporções do corpo |
| `crop` 32×32 **cinza** | `_crop_cinza_pequeno` | ~0 | **não** | fraco (sem cor) |
| histograma de cor | **não existe** | ~0 se adicionado | não | **sim** — uniforme/cabelo |
| `centro` (cx, cy) | derivado do bbox | zero | não (dá pra derivar) | fraco sozinho |
| `zona` / `papel` | ROIs desenhadas | zero | **sim** | contexto, não aparência |
| `maos_maquina` | punho na zona 'maquina' | zero | **sim** (Fase 82) | não |
| `presenca_zona[tid]` | contador da eleição | zero | **não** | **sim** — "quem fica" |
| `track_id` | BoT-SORT | zero | **sim** | só dentro do vídeo |

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

## 2. `kpts` — 17 keypoints COCO, normalizados. **O mais promissor não usado.**

O modelo é `yolo11n-pose`: os keypoints **já vêm em toda detecção**, custo zero.
Hoje são lidos em `etapa_detectar` (`results[0].keypoints.xyn`) e usados em dois
lugares — o teste de zona (`_pontos_da_pessoa`) e o gate de repetição
(`_dist_pose`) — e depois **descartados**. Nunca chegam ao evento.

Por que interessam mais que a altura da caixa: **razões entre partes do corpo
são invariantes de escala**. Ombro/quadril, tronco/perna, largura de ombros ÷
altura do tronco não mudam com a distância à câmera — que é exatamente o
confundidor que estraga a altura aparente.

Estão em coordenada **normalizada pelo frame** (`xyn`, 0–1) e um keypoint não
detectado vem `(0,0)` — tem de ser filtrado, senão vira o mesmo tipo de ponto
fantasma que a caixa zerada era.

Para o experimento: derivar por amostra, medianar por evento. Não persistidos
hoje; se o experimento pedir, o custo de gravar é uma coluna jsonb.

---

## 3. `crop` — 32×32, **em tons de cinza**

`_crop_cinza_pequeno` já recorta a pessoa e reduz para 32×32, para o termo de
movimento do gate. É uma assinatura visual, mas **converte para cinza** — joga
fora exatamente a informação que distinguiria um uniforme azul de um cinza.

Vive só em memória (dicionário `ancoras`), por track, durante o vídeo.

**Histograma de cor não existe hoje.** O frame BGR está em mãos nesse ponto: um
histograma HSV de 3×8 bins sobre o recorte (ou sobre o terço superior, para
pegar camisa e cabelo) custa microssegundos e é o descritor de aparência clássico
para reidentificação com câmera fixa. É o acréscimo mais barato de todos, mas é
acréscimo — não está lá.

Ressalvas conhecidas: iluminação de galpão muda ao longo do dia; se os dois
torneiros usam o mesmo uniforme, a cor da camisa não separa (cabelo e pele
talvez, num recorte de cabeça).

---

## 4. `presenca_zona` — quem FICA no posto

Contador `track_id → nº de amostras dentro da zona do posto`, mantido em
`etapa_detectar` e usado para eleger o titular do vídeo (desempate: maior área).

É um sinal comportamental forte — o titular é quem passa o turno ali, o visitante
passa minutos — e some no fim do vídeo. Não é aparência, mas para o problema
real ("medir o torneiro, não o posto") pode valer mais que aparência.

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

Dado que só vale a partir de agora: os eventos anteriores à Fase 82 têm
`bbox_stats` nulo e `bbox_inicio` zerado nos papéis 'operador' e 'posto_vazio'.
O experimento precisa de vídeos processados **depois** deste deploy.
