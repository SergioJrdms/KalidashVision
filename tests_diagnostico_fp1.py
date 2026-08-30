"""Contrato do diagnóstico local/read-only do FP #1.

Roda sem OpenCV/Ultralytics porque valida o entrypoint, a geometria exportada,
as guardas de integração e a ligação explícita às etapas reais do pipeline.

Rodar:  python tests_diagnostico_fp1.py
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace


RAIZ = Path(__file__).resolve().parent
SCRIPT = RAIZ / "diagnostics" / "diagnostico_fp1_presenca.py"
ZONAS = RAIZ / "diagnostics" / "fp1_zonas_camera_20260824.json"
PIPELINE = RAIZ / "backend" / "pipeline.py"

spec = importlib.util.spec_from_file_location("diagnostico_fp1", SCRIPT)
diag = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(diag)

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


print("\n[1] Geometria real e fail-fast")
zonas, origem = diag.carregar_zonas(ZONAS, "cam1", "cam2", None, None)
diag.validar_zonas(zonas, "cam1", "cam2")
check("fixture tem as duas câmeras", set(zonas) == {"cam1", "cam2"}, set(zonas))
check("cam1 preserva posto + máquina", len(zonas["cam1"]) == 2)
check("cam2 preserva posto + máquina + interação", len(zonas["cam2"]) == 3)
check(
    "pts_rel real da cam2 não foi arredondado/reinventado",
    zonas["cam2"]["Posto do Torneiro"]["pts_rel"]
    == [[0.1981, 0.0484], [0.5412, 0.0783], [0.2358, 0.7978], [0.0048, 0.6663]],
)
check(
    "hashes travam exatamente os dois anexos do caso",
    diag.VIDEOS_ESPERADOS_SHA256["cam1"].startswith("12afc1d3")
    and diag.VIDEOS_ESPERADOS_SHA256["cam2"].startswith("d68aeb8d"),
)
try:
    diag.validar_zonas({"cam1": zonas["cam1"]}, "cam1", "cam2")
    recusou = False
except ValueError:
    recusou = True
check("falta de posto em qualquer câmera recusa o teste", recusou)


print("\n[2] Entrada por CSV ou pts_rel explícito")
with tempfile.TemporaryDirectory() as tmp:
    csv_path = Path(tmp) / "zonas.csv"
    csv_path.write_text(
        "cam_id,nome,papel,ativo,pts_rel\n"
        'cam1,posto,posto_operador,true,"[[0,0],[1,0],[1,1]]"\n'
        'cam2,posto,posto_operador,true,"[[0,0],[1,0],[1,1]]"\n',
        encoding="utf-8",
    )
    zonas_csv, _ = diag.carregar_zonas(csv_path, "cam1", "cam2", None, None)
    check("export CSV de zonas_camera é aceito", len(zonas_csv) == 2)

zonas_args, origem_args = diag.carregar_zonas(
    None,
    "cam1",
    "cam2",
    "[[0,0],[1,0],[1,1]]",
    "[[0,0],[1,0],[1,1]]",
)
check("pts_rel por argumento é aceito para as duas", len(zonas_args) == 2)
check("origem explícita fica auditável", origem_args.startswith("argumentos"))
os.environ["KV_TEST_SECRET_TOKEN"] = "nao-vazar"
try:
    check(
        "manifesto mascara KV_* potencialmente secreta",
        diag._env_kv_seguro()["KV_TEST_SECRET_TOKEN"] == "<redacted>",
    )
finally:
    os.environ.pop("KV_TEST_SECRET_TOKEN", None)


print("\n[3] Ambiente é validado antes do import pesado")
anteriores = {k: os.environ.get(k) for k in diag.ENV_OBRIGATORIO}
try:
    for chave in diag.ENV_OBRIGATORIO:
        os.environ.pop(chave, None)
    try:
        diag.validar_env_obrigatorio()
        recusou = False
    except SystemExit:
        recusou = True
    check("variável ausente falha fechado", recusou)
    for chave, valor in diag.ENV_OBRIGATORIO.items():
        os.environ[chave] = valor.upper()
    try:
        diag.validar_env_obrigatorio()
        aceitou = True
    except SystemExit:
        aceitou = False
    check("ON/REID do Render são normalizados sem mudar semântica", aceitou)
finally:
    for chave, valor in anteriores.items():
        if valor is None:
            os.environ.pop(chave, None)
        else:
            os.environ[chave] = valor


print("\n[4] Supabase, Groq, VLM e persistência ficam inalcançáveis")
fake = SimpleNamespace(
    make_supabase_client=lambda: "erro",
    make_groq_client=lambda: "erro",
    etapa_analise_vlm=lambda: "erro",
    etapa_persistir=lambda: "erro",
)
diag._bloquear_integracoes(fake)
for nome in (
    "make_supabase_client",
    "make_groq_client",
    "etapa_analise_vlm",
    "etapa_persistir",
):
    try:
        getattr(fake, nome)()
        bloqueou = False
    except RuntimeError:
        bloqueou = True
    check(f"{nome} bloqueado", bloqueou)


print("\n[5] Orquestração chama as funções reais, com warm-up e pós-roll")
fonte = SCRIPT.read_text(encoding="utf-8")
arvore = ast.parse(fonte)
chamadas = set()
for no in ast.walk(arvore):
    if not isinstance(no, ast.Call):
        continue
    if isinstance(no.func, ast.Attribute):
        chamadas.add(no.func.attr)
    elif isinstance(no.func, ast.Name):
        chamadas.add(no.func.id)
check("usa loader YOLO do worker", "_get_yolo" in chamadas)
check("usa etapa_detectar_e_amostrar real", "etapa_detectar_e_amostrar" in chamadas)
check("usa _anexar_segundo_angulo real", "_anexar_segundo_angulo" in chamadas)
check("usa etapa_confirmar_operador real", "etapa_confirmar_operador" in chamadas)
check("não chama processar_video", "processar_video" not in chamadas)
check(
    "warm-up começa em zero e só o relatório é filtrado",
    '"processamento_inicio_s": 0.0' in fonte
    and "if tempo < args.inicio" in fonte,
)
check(
    "pós-roll usa a mesma _OPERADOR_GAP_SLOTS",
    "args.fim + pipeline._OPERADOR_GAP_SLOTS * intervalo_s" in fonte,
)
check(
    "cam2 não ganha um laço contínuo paralelo",
    "cv2.VideoCapture" not in fonte,
)


print("\n[6] Instrumentação é opcional no backend")
arvore_pipeline = ast.parse(PIPELINE.read_text(encoding="utf-8"))
funcoes = {
    no.name: no for no in arvore_pipeline.body if isinstance(no, ast.FunctionDef)
}
anexar = funcoes["_anexar_segundo_angulo"]
detectar = funcoes["etapa_detectar_e_amostrar"]
defaults_anexar = anexar.args.defaults
defaults_detectar = detectar.args.defaults
check(
    "coletor cam2 tem default None",
    anexar.args.args[-1].arg == "diagnostico_presenca"
    and isinstance(defaults_anexar[-1], ast.Constant)
    and defaults_anexar[-1].value is None,
)
check(
    "fim_s tem default None e não altera produção",
    detectar.args.args[-1].arg == "fim_s"
    and isinstance(defaults_detectar[-1], ast.Constant)
    and defaults_detectar[-1].value is None,
)
check(
    "detalhe cam1 carrega a âncora medida no mesmo laço",
    '"ancora": tuple(' in PIPELINE.read_text(encoding="utf-8"),
)
check(
    "cam2 pré-registra o calendário real antes dos seeks",
    "diagnostico_slots = _preparar_diagnostico_cam2(dur_ms)"
    in PIPELINE.read_text(encoding="utf-8"),
)
fonte_pipeline = PIPELINE.read_text(encoding="utf-8")
fonte_anexar = ast.get_source_segment(fonte_pipeline, anexar) or ""
check(
    "cam2 percorre as mesmas Amostras e faz seek por alvo_ms",
    "for idx, am in enumerate(amostras)" in fonte_anexar
    and "cap.set(cv2.CAP_PROP_POS_MSEC, alvo_ms)" in fonte_anexar,
)
check(
    "cam2 aplica o _CAM2_CONFIRM_STRIDE no próprio passe real",
    "idx % _CAM2_CONFIRM_STRIDE" in fonte_anexar,
)
check(
    "cam2 usa yolo.track persistente do pipeline",
    "res = yolo.track(" in fonte_anexar
    and "persist=True" in fonte_anexar,
)


print("\n[7] Gate da cam2 falha somente onde a medição era esperada")
tempos = {120.0, 125.0, 130.0}
obs_cam1_gate = {tempo: {"medido": True} for tempo in tempos}
obs_cam2_gate = {
    120.0: {"medicao_esperada": True, "medido": True},
    125.0: {"medicao_esperada": False, "medido": False},
}
faltas_cam1, falhas_cam2 = diag._falhas_cobertura(
    tempos, obs_cam1_gate, obs_cam2_gate
)
check(
    "slot pulado ou sem telemetria não aborta",
    faltas_cam1 == [] and falhas_cam2 == [],
)
obs_cam2_gate[130.0] = {
    "medicao_esperada": True,
    "medido": False,
    "motivo_sem_medicao": "frame_nao_lido",
}
_, falhas_cam2 = diag._falhas_cobertura(tempos, obs_cam1_gate, obs_cam2_gate)
check("slot esperado sem medição aborta", falhas_cam2 == [130.0])


print("\n[8] Smoke do CLI: relatório filtra, mas o tracker recebe contexto")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    cam1 = tmp_path / "cam1.mp4"
    cam2 = tmp_path / "cam2.mp4"
    cam1.write_bytes(b"cam1-fake")
    cam2.write_bytes(b"cam2-fake")
    modelo = tmp_path / "yolo11n-pose.pt"
    modelo.write_bytes(b"modelo-fake")
    saida = tmp_path / "fp1.csv"
    recebido = {}

    def inspecionar(caminho):
        cam2_local = Path(caminho) == cam2
        return {
            "fps": 6.0,
            "total_frames": 1800,
            "largura": 510 if cam2_local else 806,
            "altura": 546 if cam2_local else 304,
            "duracao_s": 300.0,
        }

    def detectar(
        _yolo, _video, intervalo, _zonas, _progress, *,
        cam_id, mapa_movimento, identidade_shadow, fim_s,
    ):
        recebido["fim_s"] = fim_s
        amostras = []
        tempo = 0.0
        while tempo <= fim_s + 1e-9:
            amostras.append(SimpleNamespace(
                tempo_s=tempo,
                pessoas=[{"papel": "operador"}],
                n_posto_cam2=None,
                op_cam2=None,
                operador_presente=None,
                operador_ponte=False,
            ))
            identidade_shadow["observacoes"].append({
                "cam_id": cam_id,
                "tempo_s": tempo,
                "medido": True,
                "n_deteccoes_yolo": 1,
                "n_elegiveis_pipeline": 1,
                "pessoas": {
                    7: {
                        "bbox": (1, 2, 3, 4),
                        "ancora": (2.0, 2.5),
                        "estado": "dentro",
                    }
                },
            })
            tempo += intervalo
        return amostras, inspecionar(cam1), [7], [], {}, {}

    def anexar(amostras, _video, **kwargs):
        recebido["cam2_primeiro"] = amostras[0].tempo_s
        recebido["cam2_ultimo"] = amostras[-1].tempo_s
        for idx, am in enumerate(amostras):
            esperada = (idx % 2) == 0
            am.n_posto_cam2 = 1 if esperada else None
            am.op_cam2 = True if esperada else None
            kwargs["diagnostico_presenca"].append({
                "tempo_s": am.tempo_s,
                "medido": esperada,
                "medicao_esperada": esperada,
                "motivo_sem_medicao": None if esperada else "stride",
                "n_detectadas_yolo": 1 if esperada else 0,
                "n_posto_cam2": 1 if esperada else None,
                "op_cam2": True if esperada else None,
                "pessoas": ([{
                    "track_id": 9,
                    "bbox": [1, 2, 3, 4],
                    "ancora": [2.0, 2.5],
                    "dentro_posto": True,
                }] if esperada else []),
            })
        return len(amostras)

    def confirmar(amostras, _politica):
        for am in amostras:
            am.operador_presente = True
        return {"slots": len(amostras), "presentes": len(amostras)}

    pipeline_fake = ModuleType("backend.pipeline")
    pipeline_fake._ZONA_ESTRITA = True
    pipeline_fake._FORA_MODO = "on"
    pipeline_fake.YOLO_MODEL = "yolo11n-pose.pt"
    pipeline_fake.TRACKER_CONFIG = str(
        RAIZ / "backend" / "trackers" / "botsort_camera_fixa_reid.yaml"
    )
    pipeline_fake.DEFAULT_INTERVALO_AMOSTRAGEM_S = 5.0
    pipeline_fake._OPERADOR_GAP_SLOTS = 3
    pipeline_fake._CAM2_CONFIRM_STRIDE = 2
    pipeline_fake._OPERADOR_CONFIRMACAO = "dupla"
    pipeline_fake.inspecionar_video = inspecionar
    pipeline_fake.etapa_detectar_e_amostrar = detectar
    pipeline_fake._anexar_segundo_angulo = anexar
    pipeline_fake._offset_entre_nomes = lambda *_: 0.0
    pipeline_fake.etapa_confirmar_operador = confirmar
    pipeline_fake.make_supabase_client = lambda: None
    pipeline_fake.make_groq_client = lambda: None
    pipeline_fake.etapa_analise_vlm = lambda: None
    pipeline_fake.etapa_persistir = lambda: None

    worker_fake = ModuleType("backend.worker")
    worker_fake._get_yolo = lambda: SimpleNamespace()
    worker_fake._modelo_path = lambda _nome: str(modelo)
    backend_fake = ModuleType("backend")
    backend_fake.pipeline = pipeline_fake
    backend_fake.worker = worker_fake

    modulos_antes = {
        nome: sys.modules.get(nome)
        for nome in ("backend", "backend.pipeline", "backend.worker")
    }
    manifesto_antes = diag._manifesto
    hashes_antes = dict(diag.VIDEOS_ESPERADOS_SHA256)
    env_antes = {k: os.environ.get(k) for k in diag.ENV_OBRIGATORIO}
    try:
        sys.modules["backend"] = backend_fake
        sys.modules["backend.pipeline"] = pipeline_fake
        sys.modules["backend.worker"] = worker_fake
        diag._manifesto = lambda **_kwargs: {"smoke": "ok"}
        diag.VIDEOS_ESPERADOS_SHA256.update({
            "cam1": diag._sha256(cam1),
            "cam2": diag._sha256(cam2),
        })
        for chave, valor in diag.ENV_OBRIGATORIO.items():
            os.environ[chave] = valor
        codigo = diag.main([
            "--cam1", str(cam1),
            "--cam2", str(cam2),
            "--zones-file", str(ZONAS),
            "--output", str(saida),
        ])
    finally:
        diag._manifesto = manifesto_antes
        diag.VIDEOS_ESPERADOS_SHA256.clear()
        diag.VIDEOS_ESPERADOS_SHA256.update(hashes_antes)
        for chave, valor in env_antes.items():
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor
        for nome, modulo in modulos_antes.items():
            if modulo is None:
                sys.modules.pop(nome, None)
            else:
                sys.modules[nome] = modulo

    with saida.open("r", encoding="utf-8", newline="") as arq:
        linhas = list(csv.DictReader(arq))
    check("CLI conclui", codigo == 0)
    check("tracker cam1 aquece desde zero", recebido["cam2_primeiro"] == 0.0)
    check("pós-roll chega a 190 s", recebido["fim_s"] == 190.0)
    check("CSV contém só 120–175 s", len(linhas) == 12
          and linhas[0]["tempo_s"] == "120.000"
          and linhas[-1]["tempo_s"] == "175.000")
    check("slot pulado pelo stride é válido e fica explícito",
          linhas[-1]["cam2_medicao_esperada"] == "false"
          and linhas[-1]["cam2_pessoa_detectada"] == ""
          and linhas[-1]["cam2_motivo_sem_medicao"] == "stride")
    check("colunas pedidas saem preenchidas",
          linhas[0]["cam1_pessoa_detectada"] == "true"
          and linhas[0]["cam2_pessoa_detectada"] == "true"
          and linhas[0]["n_posto_cam2"] == "1"
          and linhas[0]["resultado_presenca_pre_111d"] == "presente")

print(f"\n{'=' * 56}\n  {ok} ok · {fail} falha(s)\n{'=' * 56}")
raise SystemExit(1 if fail else 0)
