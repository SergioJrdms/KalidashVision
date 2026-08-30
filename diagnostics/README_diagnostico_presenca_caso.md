# Runner genérico read-only de presença

`diagnostico_presenca_caso.py` aplica a mesma cadeia real já validada pelo
diagnóstico FP #1:

```text
worker._get_yolo()
pipeline.etapa_detectar_e_amostrar()
pipeline._anexar_segundo_angulo()
pipeline.etapa_confirmar_operador()
```

Ele não chama `processar_video`, Supabase, Groq, VLM ou persistência. O CSV e o
manifesto JSON são artefatos locais de diagnóstico; nenhuma correção de
`posto_vazio` é aplicada.

## Semântica de `--cam2-offset-s`

O argumento representa:

```text
cam2_offset_s = início(cam2) - início(cam1)
t_cam2 = t_cam1 - cam2_offset_s
```

Para cam1 iniciando `07:00:57` e cam2 iniciando `07:01:07`, use `+10`:

```text
cam1 t=10  ↔ cam2 t=0
cam1 t=60  ↔ cam2 t=50
cam1 t=120 ↔ cam2 t=110
```

O backend usa internamente a convenção inversa. O runner passa `-10` para
`_anexar_segundo_angulo()` e registra os dois valores no manifesto.

O relatório é recusado antes de carregar o YOLO quando seu início mapear para
tempo negativo da cam2. Warm-up anterior à cam2 continua permitido fora do
intervalo relatado, porque ele é parte da lista real de `Amostra` usada pelo
stride. Cobertura parcial apenas no fim da cam2 é preservada e fica explícita:

- no stderr, como `COBERTURA_CAM2_RELATORIO`;
- no manifesto, com status e segundos descobertos;
- por linha, em `cam2_tempo_s_alinhado` e `cam2_cobertura_temporal`;
- pelas colunas reais `cam2_medicao_esperada` e
  `cam2_motivo_sem_medicao=fora_janela_cam2`.

## Comando no Render Shell

Coloque os dois MP4s e o export de zonas no diretório indicado. Como os nomes,
hashes e o intervalo variam por caso, preencha `CAM1`, `CAM2`, `INICIO`, `FIM`
e `ZONES` antes de executar o bloco.

```bash
cd /opt/render/project/src
test -f diagnostics/diagnostico_presenca_caso.py
mkdir -p /tmp/kv-caso

export CAM1=/tmp/kv-caso/cam1.mp4
export CAM2=/tmp/kv-caso/cam2.mp4
export ZONES=diagnostics/fp1_zonas_camera_20260824.json
export INICIO=120
export FIM=175

export SHA256_CAM1="$(sha256sum "$CAM1" | awk '{print $1}')"
export SHA256_CAM2="$(sha256sum "$CAM2" | awk '{print $1}')"

git rev-parse HEAD
git status --short --branch
python --version
python -m pip freeze --all > /tmp/kv-caso/render-freeze.txt

export KV_ZONA_ESTRITA=on
export KV_FORA_DO_POSTO=on
export KV_TRACKER=reid

python diagnostics/diagnostico_presenca_caso.py \
  --cam1 "$CAM1" \
  --cam2 "$CAM2" \
  --inicio "$INICIO" \
  --fim "$FIM" \
  --cam2-offset-s 10 \
  --sha256-cam1 "$SHA256_CAM1" \
  --sha256-cam2 "$SHA256_CAM2" \
  --zones-file "$ZONES" \
  --output /tmp/kv-caso/presenca-caso.csv

sed -n '1,40p' /tmp/kv-caso/presenca-caso.csv
python -m json.tool /tmp/kv-caso/presenca-caso.manifest.json | sed -n '1,220p'
```

Não passe `--intervalo`: o runner herda `KV_INTERVALO_AMOSTRAGEM_S` do próprio
deploy. O processamento da cam1 sempre começa em zero e termina somente após o
pós-roll exigido por `_OPERADOR_GAP_SLOTS`, limitado pela duração real da cam1.
