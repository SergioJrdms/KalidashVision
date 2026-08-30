# FP #1 — probe low-confidence do detector bruto

`diagnostico_fp1_lowconf.py` responde uma pergunta estreita: nos instantes
120, 128, 136, 144, 152, 160 e 168 s, o `yolo11n-pose.pt` ainda produz
candidatos de pessoa quando o corte diagnóstico é reduzido para `0.05`?

Este **NÃO é um replay do pipeline**. Não há tracker, warm-up, ponte temporal,
eleição de operador, VLM ou decisão de presença. São somente 14 chamadas
independentes a `predict` — sete frames da cam1 e sete da cam2 — para observar
o detector bruto. O script não muda os thresholds de produção e seus resultados
não são devolvidos ao pipeline.

O probe:

- carrega o modelo exclusivamente por `worker._get_yolo()`;
- usa `predict(frame, classes=[0], conf=0.05, imgsz=416, save=False)`;
- recusa vídeos cujos SHA-256 não sejam os dois anexos originais do FP #1;
- usa o `fp1_zonas_camera_20260824.json` real;
- chama as funções reais `_build_rois()`, `_ponto_ancora()` e
  `_ponto_em_roi()`;
- compara cada candidato com os cortes efetivos `0.30` da cam1 e `0.35` da
  cam2, sem alterá-los;
- na cam1 compara também `area_ratio` com `_OPERADOR_AREA_MIN_RATIO`;
- bloqueia Supabase, Groq, VLM, persistência e o pipeline completo no processo.

Há pelo menos uma linha por câmera/slot no CSV. Quando o modelo não retorna
nenhuma pessoa nem mesmo em `0.05`, a linha sai com
`deteccao_encontrada=false`; falha de leitura do frame aborta a execução e não
é confundida com zero detecções. Candidatos abaixo de `0.05` continuam
invisíveis por definição.

## Comando exato no Render Shell

O código deve estar presente no mesmo deploy que processa os vídeos. Coloque
os dois MP4s originais em `/tmp/kv-fp1/` com os nomes abaixo; o próprio script
valida os hashes antes de carregar o modelo.

```bash
cd /opt/render/project/src
test -f diagnostics/diagnostico_fp1_lowconf.py
mkdir -p /tmp/kv-fp1

git rev-parse HEAD
git status --short --branch
python --version
python -m pip freeze --all > /tmp/kv-fp1/render-freeze-lowconf.txt

python diagnostics/diagnostico_fp1_lowconf.py \
  --cam1 /tmp/kv-fp1/5e88196d-6ebc-4f30-810c-44697edf80ad_seg_20260824_074000_roi.mp4 \
  --cam2 /tmp/kv-fp1/458c5cb8-4713-4b19-9b9b-aacd87b82cac_seg_20260824_074000_roi.mp4 \
  --zones-file diagnostics/fp1_zonas_camera_20260824.json \
  --output /tmp/kv-fp1/fp1-lowconf.csv \
  --images-dir /tmp/kv-fp1/fp1-lowconf-frames

sed -n '1,40p' /tmp/kv-fp1/fp1-lowconf.csv
find /tmp/kv-fp1/fp1-lowconf-frames -maxdepth 1 -type f -name '*.jpg' -print | sort
```

Não exporte `KV_OPERADOR_CONF` ou `KV_CAM2_CONF` só para executar o probe. Ele
lê os valores materializados pelo pipeline no runtime do Render e aborta se
eles divergirem de `0.30`/`0.35`, porque nesse caso a pergunta diagnóstica já
não seria a mesma.

## Saídas

- `/tmp/kv-fp1/fp1-lowconf.csv`: candidatos e linhas sentinela sem detecção;
- `/tmp/kv-fp1/fp1-lowconf-frames/`: 14 JPGs anotados, um por câmera/slot;
- `/tmp/kv-fp1/render-freeze-lowconf.txt`: dependências efetivas do Render.

As colunas `passa_conf_producao`, `passa_area_min_cam1` e
`passa_conf_e_area_cam1` são apenas comparações diagnósticas. Elas não afirmam
que a detecção “passaria pelo pipeline”, pois este probe não reproduz tracking,
identidade, regras temporais ou confirmação entre câmeras.
