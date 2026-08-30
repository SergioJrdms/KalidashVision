# Diagnóstico A–D: `predict` versus `track` no FP2B

`diagnostico_track_vs_predict_caso.py` é um experimento estritamente
observacional para localizar em que etapa uma pessoa visível no frame final
pode deixar de aparecer no resultado devolvido pelo detector/tracker. Ele não
altera `backend/pipeline.py`, thresholds, presença, eventos ou a regra de
`posto_vazio`.

O diagnóstico não chama `processar_video`, Supabase, Groq, VLM, jobs ou
persistência da plataforma. Os únicos resultados duráveis pedidos são um CSV e
um manifesto JSON locais. A interpretação gravada no manifesto é somente uma
classificação descritiva do experimento; não substitui a decisão do pipeline e
este documento não antecipa qual será o resultado real.

## Alvo e alinhamento

O alvo FP2B é o mesmo instante físico nos dois arquivos:

```text
cam2_offset_s = início(cam2) - início(cam1) = +10 s
t_cam2 = t_cam1 - cam2_offset_s

cam1 88 s ↔ cam2 78 s
```

O contexto cronológico controlado usa:

```text
cam1: 64, 72, 80, 88 s
cam2: 54, 62, 70, 78 s
```

Todos os modos observam o mesmo frame final da cam2 em `78 s`. O processo pai
falha fechado se o SHA-256 dos bytes decodificados desse frame divergir entre
os quatro subprocessos.

Para não confundir uma pessoa diferente no posto com o operador investigado,
o manifesto também registra a bbox de referência informada no caso
(`[114.834, 30.540, 182.405, 230.846]`) e marca correspondência somente com
IoU mínimo de `0.50` e âncora dentro do `posto_operador`. Essa marcação é
observacional e não muda nenhuma chamada ou decisão do pipeline.

## Modos A–D

| Modo | Processo limpo | Sequência antes/do alvo | API no alvo |
| --- | --- | --- | --- |
| A | sim | somente cam2 `78 s` | `yolo.predict()` |
| B | sim | somente cam2 `78 s` | `yolo.track()` |
| C | sim | cam2 `54, 62, 70, 78 s` | `yolo.track()` com `persist=True` |
| D | sim | passe real da cam1; reset real da ponte; cam2 `54, 62, 70, 78 s` | `_anexar_segundo_angulo()` e seu `yolo.track()` real |

Cada modo roda em um subprocesso Python novo. Assim, o singleton de
`worker._get_yolo()`, o predictor do Ultralytics e o BoT-SORT não vazam estado
de A para B, de B para C ou de C para D. O processo pai também confirma quatro
PIDs distintos. Não execute os argumentos internos manualmente; eles existem
somente para a orquestração dos subprocessos.

A usa o detector bruto com a confiança atual da cam2, não o probe low-confidence
de `0.05`. No frame final, os parâmetros observados são:

```python
# A
yolo.predict(
    frame,
    classes=[0],
    conf=0.35,
    imgsz=416,
    verbose=False,
    save=False,
)

# B, C e a ponte real em D
yolo.track(
    frame,
    classes=[0],
    conf=0.35,
    imgsz=416,
    persist=True,
    tracker=pipeline.TRACKER_CONFIG,
    verbose=False,
)
```

## O que torna D diferente

D carrega o modelo uma vez com `worker._get_yolo()` e mantém o mesmo objeto
YOLO nas duas câmeras. Primeiro chama a função real
`pipeline.etapa_detectar_e_amostrar()` na cam1:

- desde `t=0`, sem pular diretamente para o intervalo relatado;
- com a cadência efetiva de `KV_TRACK_FPS` e `KV_IMGSZ` do deploy;
- com o intervalo real `pipeline.DEFAULT_INTERVALO_AMOSTRAGEM_S`;
- com as zonas reais da cam1;
- até `runner_fim_s + _OPERADOR_GAP_SLOTS × intervalo`, limitado pela duração
  real do vídeo.

Para o comando FP2B abaixo, o intervalo relatado é `64–120 s`. Se o deploy usa
intervalo de `8 s` e três slots de ponte, o fim derivado é `144 s`; o script
registra os valores efetivos e não os fabrica.

Depois desse passe, D seleciona as `Amostra` reais de `64, 72, 80, 88 s` e
chama a própria `pipeline._anexar_segundo_angulo()` com:

```python
offset_s=-10.0
desc_acc={}
```

O dicionário vazio é intencional: por ser diferente de `None`, faz a ponte
executar exatamente um `pipeline.resetar_tracker(yolo)` antes da cam2, como no
caminho real. A função então faz os quatro seeks e quatro chamadas persistentes
em `54, 62, 70, 78 s`. A instrumentação envolve temporariamente `track` e
`resetar_tracker` apenas para observar argumentos, retorno e estado; as funções
originais são chamadas e restauradas, sem nova inferência.

D falha fechado se a cadência não produzir os quatro slots exatos, se
`_CAM2_CONFIRM_STRIDE != 1`, se não ocorrer exatamente um reset antes da cam2,
se não houver exatamente quatro chamadas `track` com os parâmetros reais ou se
algum slot esperado não for medido. Se o reset real retornar `falhou`, D é
marcado como inválido/inconclusivo; o script não tenta um reset extra.

Essa seleção de quatro frames é deliberada para manter C e D comparáveis. D
reproduz o ciclo de vida real `cam1 → reset da ponte → cam2` e usa as duas
funções reais, mas não é um replay integral de todas as etapas ou de todos os
slots da plataforma.

## Validações de paridade

Antes da inferência, cada subprocesso valida:

- `yolo11n-pose.pt` carregado por `worker._get_yolo()`;
- `_CAM2_CONF=0.35`;
- `botsort_camera_fixa_reid.yaml` existente e `with_reid: true`;
- `KV_ZONA_ESTRITA=on`, `KV_FORA_DO_POSTO=on` e `KV_TRACKER=reid`;
- geometria real de `posto_operador` para cam1 e cam2, incluindo dimensões de
  referência;
- cobertura dos quatro frames da cam2;
- offset público `+10 s`;
- os MP4s originais do FP2B pelos hashes:
  - cam1: `1cbd3c52e2af6e1f6abe99fc445515104b378b64b76352be5d2235f44c4676e4`;
  - cam2: `b82a2951898e3b226c5fd5cbf626cc688abe314bb4b90d08b1f0b3d0b5e022af`.

O arquivo de zonas usado no comando é
`diagnostics/fp1_zonas_camera_20260824.json`, o export real já validado para
essas duas câmeras.

## Saídas

Para `--output "$RUN_DIR/fp2b-track-vs-predict.csv"`, são criados:

- `$RUN_DIR/fp2b-track-vs-predict.csv` — uma linha sentinela por modo sem box,
  ou uma linha por detecção devolvida no frame final;
- `$RUN_DIR/fp2b-track-vs-predict.manifest.json` — entradas, hashes, quatro
  PIDs, parâmetros, sequência de inferências, snapshots best-effort do tracker,
  reset da ponte em D, configuração efetiva, resumos detalhados serializáveis e
  interpretação limitada;
- `$RUN_DIR/render-freeze-track-vs-predict.txt` — inventário do ambiente gerado
  pelo comando abaixo.

O CSV inclui, entre outros campos, modo, API final, sequência cam2, validade do
modo, quantidade de boxes, quantidade dentro do `posto_operador`, IDs de track,
confidence, bbox, âncora, IoU contra o bbox conhecido do operador, SHA-256 do
frame final, retorno do reset e parâmetros da inferência final.

Os destinos CSV e manifesto existentes são recusados; nada é sobrescrito.

## Comando exato no Render Shell — FP2B

Coloque os dois arquivos originais com os nomes recebidos em
`/tmp/kv-fp2b/7461b1f9-ed17-48ff-a70d-05bda6aa6fdc_seg_20260824_070057_roi.mp4`
e
`/tmp/kv-fp2b/bb62392d-d369-426b-8ae0-4aadd236489b_seg_20260824_070107_roi.mp4`.
Rode no Shell do mesmo serviço/deploy que contém as dependências e
configurações de produção:

```bash
set -euo pipefail
cd /opt/render/project/src

test -f diagnostics/diagnostico_track_vs_predict_caso.py
test -f diagnostics/fp1_zonas_camera_20260824.json
CAM1=/tmp/kv-fp2b/7461b1f9-ed17-48ff-a70d-05bda6aa6fdc_seg_20260824_070057_roi.mp4
CAM2=/tmp/kv-fp2b/bb62392d-d369-426b-8ae0-4aadd236489b_seg_20260824_070107_roi.mp4
test -f "$CAM1"
test -f "$CAM2"

RUN_DIR="$(mktemp -d /tmp/kv-fp2b-track-vs-predict.XXXXXX)"

git rev-parse HEAD
git status --short --branch
python --version
python -m pip freeze --all > "$RUN_DIR/render-freeze-track-vs-predict.txt"

test "${KV_ZONA_ESTRITA:-}" = on
test "${KV_FORA_DO_POSTO:-}" = on
test "${KV_TRACKER:-}" = reid

python diagnostics/diagnostico_track_vs_predict_caso.py \
  --cam1 "$CAM1" \
  --cam2 "$CAM2" \
  --cam2-offset-s 10 \
  --runner-inicio-s 64 \
  --runner-fim-s 120 \
  --sha256-cam1 1cbd3c52e2af6e1f6abe99fc445515104b378b64b76352be5d2235f44c4676e4 \
  --sha256-cam2 b82a2951898e3b226c5fd5cbf626cc688abe314bb4b90d08b1f0b3d0b5e022af \
  --zones-file diagnostics/fp1_zonas_camera_20260824.json \
  --output "$RUN_DIR/fp2b-track-vs-predict.csv"

test -s "$RUN_DIR/fp2b-track-vs-predict.csv"
test -s "$RUN_DIR/fp2b-track-vs-predict.manifest.json"

sed -n '1,120p' "$RUN_DIR/fp2b-track-vs-predict.csv"
python -m json.tool "$RUN_DIR/fp2b-track-vs-predict.manifest.json" | sed -n '1,320p'
sha256sum \
  "$RUN_DIR/fp2b-track-vs-predict.csv" \
  "$RUN_DIR/fp2b-track-vs-predict.manifest.json" \
  "$RUN_DIR/render-freeze-track-vs-predict.txt"
printf 'Artefatos desta execução: %s\n' "$RUN_DIR"
```

Não instale dependências e não altere `KV_CAM2_CONF`,
`KV_CAM2_CONFIRM_STRIDE`, `KV_INTERVALO_AMOSTRAGEM_S`, `KV_TRACK_FPS`,
`KV_IMGSZ` ou o YAML do tracker para fazer o teste passar. O diagnóstico deve
observar a configuração materializada no Render e falhar fechado quando ela não
satisfizer o contrato FP2B.
