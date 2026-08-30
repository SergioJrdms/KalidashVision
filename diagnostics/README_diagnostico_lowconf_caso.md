# Probe genérico low-confidence por caso

`diagnostico_lowconf_caso.py` observa o detector bruto em slots informados no
relógio da cam1. Ele **NÃO é um replay do pipeline**, não usa tracker e não toma
decisão de presença ou `posto_vazio`.

Cada câmera-slot executa somente:

```python
worker._get_yolo()
yolo.predict(
    frame,
    classes=[0],
    conf=0.05,
    imgsz=416,
    verbose=False,
    save=False,
)
```

O probe não chama `track`, `processar_video`, Supabase, Groq, VLM, jobs ou
persistência da plataforma. As únicas escritas são o CSV e os JPGs locais
pedidos na CLI. As detecções não retornam ao pipeline e não mudam thresholds.

O script reutiliza as mesmas funções reais de geometria já validadas no FP1:

- `pipeline._build_rois()`;
- `pipeline._ponto_ancora()`;
- `pipeline._ponto_em_roi()`.

Também compara cada candidato com os thresholds materializados do runtime:
cam1 `0.30`, cam2 `0.35` e, somente na cam1,
`_OPERADOR_AREA_MIN_RATIO`. Essas comparações são observacionais; não afirmam
que o candidato passaria por tracking ou pelas demais regras do pipeline.

## Offset e cobertura

`--slots` é uma lista crescente, separada por vírgula, no relógio da cam1. A
semântica de `--cam2-offset-s` é a mesma do runner genérico:

```text
cam2_offset_s = início(cam2) - início(cam1)
t_cam2 = t_cam1 - cam2_offset_s
```

No FP2B, a cam2 começa 10 segundos depois:

```text
cam1 64  → cam2 54
cam1 72  → cam2 62
cam1 80  → cam2 70
cam1 88  → cam2 78
cam1 96  → cam2 86
cam1 104 → cam2 94
cam1 112 → cam2 102
cam1 120 → cam2 110
```

Todos os 16 seeks são validados antes de carregar o YOLO. Slot negativo,
duplicado, não finito ou fora da duração de qualquer vídeo aborta a execução.
Falha de leitura também aborta e nunca é confundida com ausência de pessoa.

Quando `predict(conf=0.05)` não retorna detecção, o CSV contém uma linha
sentinela explícita com `deteccao_encontrada=false` para aquele câmera-slot.

## Comando exato no Render Shell — FP2B

O código deve estar no mesmo deploy que processa os vídeos. Coloque os dois
MP4s originais como `/tmp/kv-fp2b/cam1.mp4` e `/tmp/kv-fp2b/cam2.mp4`.

```bash
set -euo pipefail
cd /opt/render/project/src
test -f diagnostics/diagnostico_lowconf_caso.py
mkdir -p /tmp/kv-fp2b
test -f /tmp/kv-fp2b/cam1.mp4
test -f /tmp/kv-fp2b/cam2.mp4
RUN_DIR="$(mktemp -d /tmp/kv-fp2b-lowconf.XXXXXX)"

git rev-parse HEAD
git status --short --branch
python --version
python -m pip freeze --all > "$RUN_DIR/render-freeze-lowconf.txt"

python diagnostics/diagnostico_lowconf_caso.py \
  --cam1 /tmp/kv-fp2b/cam1.mp4 \
  --cam2 /tmp/kv-fp2b/cam2.mp4 \
  --slots 64,72,80,88,96,104,112,120 \
  --cam2-offset-s 10 \
  --sha256-cam1 1cbd3c52e2af6e1f6abe99fc445515104b378b64b76352be5d2235f44c4676e4 \
  --sha256-cam2 b82a2951898e3b226c5fd5cbf626cc688abe314bb4b90d08b1f0b3d0b5e022af \
  --zones-file diagnostics/fp1_zonas_camera_20260824.json \
  --output "$RUN_DIR/fp2b-lowconf.csv" \
  --images-dir "$RUN_DIR/fp2b-lowconf-frames"

sed -n '1,80p' "$RUN_DIR/fp2b-lowconf.csv"
find "$RUN_DIR/fp2b-lowconf-frames" -maxdepth 1 -type f -name '*.jpg' -print | sort
test "$(find "$RUN_DIR/fp2b-lowconf-frames" -maxdepth 1 -type f -name '*.jpg' | wc -l)" -eq 16
printf 'Artefatos desta execução: %s\n' "$RUN_DIR"
```

Não instale dependências nem exporte novos valores de `KV_OPERADOR_CONF` ou
`KV_CAM2_CONF` para rodar o probe. O objetivo é usar o Python, o modelo e a
configuração já materializados no próprio serviço do Render; se os thresholds
efetivos não forem `0.30`/`0.35`, o script falha fechado.

## Saídas

- `$RUN_DIR/fp2b-lowconf.csv`: candidatos brutos e sentinelas;
- `$RUN_DIR/fp2b-lowconf-frames/`: exatamente 16 JPGs gerados nesta execução;
- `$RUN_DIR/render-freeze-lowconf.txt`: inventário do ambiente do Render.

O diretório novo por execução é proposital. O probe recusa um CSV existente ou
um diretório de imagens não vazio; não apaga nem mistura artefatos anteriores.

As colunas `tempo_cam1_s` e `tempo_camera_s` deixam explícitos, respectivamente,
o slot lógico do caso e o instante realmente seekado em cada MP4.
