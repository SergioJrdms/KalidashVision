# Diagnóstico FP #1 — presença cam1 + cam2

O script `diagnostico_fp1_presenca.py` usa diretamente o loader YOLO do worker
e as etapas reais de detecção, segundo ângulo e confirmação de presença de
`backend.pipeline`. Ele não chama `processar_video`, bloqueia Supabase, Groq,
VLM e persistência, e usa o export real de `zonas_camera` incluído nesta pasta.

O tracker é aquecido desde 0 s para manter a continuidade BoT-SORT/Re-ID. O
CSV contém somente 120–175 s; o processamento termina após o pequeno pós-roll
necessário para a ponte temporal.

A coluna `resultado_presenca_pre_111d` é deliberadamente explícita: ela vem da
mesma `etapa_confirmar_operador` usada antes de VLM/autoridade 111D. A decisão
final V11 depende da janela inteira e não é fabricada a partir deste recorte.
O alvo deste teste é a cadeia causal detector → âncora → zona → resgate cam2.

## Comando recomendado — no mesmo runtime do Render

Coloque os dois anexos originais em `/tmp/kv-fp1/` com os nomes abaixo. O
script confere seus SHA-256 e recusa qualquer arquivo diferente.

```bash
cd /opt/render/project/src
test -f diagnostics/diagnostico_fp1_presenca.py
mkdir -p /tmp/kv-fp1

git rev-parse HEAD
git status --short --branch
python --version
python -m pip freeze --all > /tmp/kv-fp1/render-freeze.txt

export KV_ZONA_ESTRITA=on
export KV_FORA_DO_POSTO=on
export KV_TRACKER=reid

python diagnostics/diagnostico_fp1_presenca.py \
  --cam1 /tmp/kv-fp1/5e88196d-6ebc-4f30-810c-44697edf80ad_seg_20260824_074000_roi.mp4 \
  --cam2 /tmp/kv-fp1/458c5cb8-4713-4b19-9b9b-aacd87b82cac_seg_20260824_074000_roi.mp4 \
  --inicio 120 \
  --fim 175 \
  --zones-file diagnostics/fp1_zonas_camera_20260824.json \
  --output /tmp/kv-fp1/fp1-presenca.csv
```

O comando omite `--intervalo` de propósito: o script lê exatamente
`KV_INTERVALO_AMOSTRAGEM_S` do deploy. Também não altera
`KV_OPERADOR_SEGMENTO`; o valor efetivo do Render é herdado e registrado.
A cam2 recebe a lista completa de `Amostra` desde t=0 e o próprio
`_anexar_segundo_angulo()` reproduz os seeks, offset,
`KV_CAM2_CONFIRM_STRIDE` e chamadas `track(persist=True)` de produção. Um slot
deliberadamente pulado pelo stride ou fora da janela temporal é válido e sai
com `cam2_medicao_esperada=false` e o motivo correspondente. O script só
aborta quando falta uma medição que o passe real marcou como esperada.

Saídas:

- `/tmp/kv-fp1/fp1-presenca.csv`: uma linha por amostra no intervalo;
- `/tmp/kv-fp1/fp1-presenca.manifest.json`: commit, árvore Git, Python,
  inventário `pip`, Torch/CUDA, versões, variáveis `KV_*` não secretas,
  configuração resolvida e
  SHA-256 dos vídeos, modelo, YAML Re-ID e relatório.

## Execução local no Windows

Só use esta opção como prova de paridade depois de reproduzir as versões do
manifesto/freeze do Render no mesmo tipo de runtime. O `requirements.txt` usa
limites `>=` e, sozinho, não recria o deploy exato.

```powershell
$env:KV_ZONA_ESTRITA = 'on'
$env:KV_FORA_DO_POSTO = 'on'
$env:KV_TRACKER = 'reid'

python diagnostics\diagnostico_fp1_presenca.py `
  --cam1 'C:\caminho\5e88196d-6ebc-4f30-810c-44697edf80ad_seg_20260824_074000_roi.mp4' `
  --cam2 'C:\caminho\458c5cb8-4713-4b19-9b9b-aacd87b82cac_seg_20260824_074000_roi.mp4' `
  --inicio 120 `
  --fim 175 `
  --zones-file diagnostics\fp1_zonas_camera_20260824.json `
  --output work\fp1-presenca.csv
```

Um CSV exportado diretamente de `zonas_camera` também pode ser passado em
`--zones-file`. Para uma checagem isolada, os polígonos de posto podem ser
fornecidos como JSON por `--cam1-pts-rel` e `--cam2-pts-rel`.
