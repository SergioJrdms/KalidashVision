"""Contrato dependency-free do runner genérico read-only de presença.

Rodar: ``python tests_diagnostico_presenca_caso.py``.
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
SCRIPT = RAIZ / "diagnostics" / "diagnostico_presenca_caso.py"
README = RAIZ / "diagnostics" / "README_diagnostico_presenca_caso.md"
ZONAS = RAIZ / "diagnostics" / "fp1_zonas_camera_20260824.json"

spec = importlib.util.spec_from_file_location("diagnostico_presenca_caso", SCRIPT)
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


print("\n[1] Cadeia real e contrato estritamente diagnóstico")
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

for nome in (
    "_get_yolo",
    "etapa_detectar_e_amostrar",
    "_anexar_segundo_angulo",
    "etapa_confirmar_operador",
):
    check(f"chama {nome}", nome in chamadas)
check("não chama processar_video", "processar_video" not in chamadas)
check("não deriva offset do nome", "_offset_entre_nomes" not in chamadas)
check("não chama integrações externas", not {
    "make_supabase_client", "make_groq_client", "etapa_analise_vlm",
    "etapa_persistir", "executar_job",
}.intersection(chamadas))
check("warm-up continua em zero", 'calcular_cobertura(\n        0.0' in fonte)
check("pós-roll usa _OPERADOR_GAP_SLOTS",
      "args.fim + pipeline._OPERADOR_GAP_SLOTS * intervalo_s" in fonte)
check("CSV preserva todos os campos mínimos", set((
    "tempo_s",
    "cam1_pessoa_detectada",
    "cam1_n_detectadas_yolo",
    "cam1_n_elegiveis_pipeline",
    "cam1_ancoras",
    "cam1_dentro_posto",
    "cam2_pessoa_detectada",
    "cam2_medicao_esperada",
    "cam2_motivo_sem_medicao",
    "cam2_n_detectadas_yolo",
    "cam2_ancoras",
    "cam2_dentro_posto",
    "n_posto_cam2",
    "op_cam2",
    "resultado_presenca_pre_111d",
    "operador_ponte",
)).issubset(diag.CAMPOS_CSV))


print("\n[2] Semântica positiva de +10 segundos")
check("cam1 10 mapeia cam2 0", diag.cam2_tempo_s(10, 10) == 0)
check("cam1 60 mapeia cam2 50", diag.cam2_tempo_s(60, 10) == 50)
check("cam1 120 mapeia cam2 110", diag.cam2_tempo_s(120, 10) == 110)
check("pipeline recebe o sinal inverso", diag.offset_pipeline_s(10) == -10)
cobertura_normal = diag.calcular_cobertura(120, 175, 10, 300)
check("relatório normal fica completamente coberto",
      cobertura_normal["status"] == "completa"
      and cobertura_normal["faixa_alvo_cam2_s"] == [110.0, 165.0])
cobertura_warmup = diag.calcular_cobertura(0, 190, 10, 300)
check("warm-up anterior à cam2 fica explícito, sem renumerar slots",
      cobertura_warmup["status"] == "parcial_inicio"
      and cobertura_warmup["sem_cobertura_inicio_s"] == 10.0)


print("\n[3] Guardas de cobertura inicial e parcialidade final")
cobertura_inicio = diag.calcular_cobertura(5, 20, 10, 300)
try:
    diag._validar_cobertura_relatorio(cobertura_inicio)
    recusou_inicio = False
except SystemExit:
    recusou_inicio = True
check("intervalo que exige cam2 negativa falha fechado", recusou_inicio)
cobertura_limite = diag.calcular_cobertura(10, 20, 10, 300)
try:
    diag._validar_cobertura_relatorio(cobertura_limite)
    aceitou_limite = True
except SystemExit:
    aceitou_limite = False
check("inicio cam1=10 é o limite válido para offset +10", aceitou_limite)
cobertura_fim = diag.calcular_cobertura(160, 180, 10, 163)
check("fim parcialmente descoberto é permitido e quantificado",
      cobertura_fim["status"] == "parcial_fim"
      and cobertura_fim["sem_cobertura_fim_s"] == 7.0)
try:
    diag._validar_cobertura_relatorio(cobertura_fim)
    aceitou_parcial_fim = True
except SystemExit:
    aceitou_parcial_fim = False
check("parcialidade somente no fim não aborta", aceitou_parcial_fim)
check("slot depois do fim fica explícito", diag._cobertura_slot(175, 10, 163)
      == (165.0, "fora_depois_fim"))
try:
    diag._normalizar_sha256("abc", "--sha256-cam1")
    recusou_sha = False
except SystemExit:
    recusou_sha = True
check("SHA inválido falha fechado", recusou_sha)


print("\n[4] Smoke CLI: warm-up, stride, -10 interno e parcial no fim")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    cam1 = tmp_path / "cam1.mp4"
    cam2 = tmp_path / "cam2.mp4"
    cam1.write_bytes(b"cam1-generica")
    cam2.write_bytes(b"cam2-generica")
    modelo = tmp_path / "yolo11n-pose.pt"
    modelo.write_bytes(b"modelo-generico")
    output = tmp_path / "caso.csv"
    output_prestart = tmp_path / "prestart.csv"
    recebido = {
        "get_yolo": 0,
        "offset_pipeline": None,
        "cam2_primeiro": None,
        "cam2_ultimo": None,
        "fim_processamento": None,
        "confirmar_n": None,
        "manifestos": [],
    }

    def inspecionar(caminho):
        secundario = Path(caminho) == cam2
        return {
            "fps": 6.0,
            "total_frames": int((163 if secundario else 200) * 6),
            "largura": 510 if secundario else 806,
            "altura": 546 if secundario else 304,
            "duracao_s": 163.0 if secundario else 200.0,
        }

    def detectar(
        _yolo, _video, intervalo, _zonas, _progress, *,
        cam_id, mapa_movimento, identidade_shadow, fim_s,
    ):
        recebido["fim_processamento"] = fim_s
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
        recebido["offset_pipeline"] = kwargs["offset_s"]
        recebido["cam2_primeiro"] = amostras[0].tempo_s
        recebido["cam2_ultimo"] = amostras[-1].tempo_s
        for idx, am in enumerate(amostras):
            alvo = am.tempo_s + kwargs["offset_s"]
            fora = alvo < 0 or alvo > 163.0
            esperada = not fora and idx % 2 == 0
            if fora:
                motivo = "fora_janela_cam2"
            elif not esperada:
                motivo = "stride"
            else:
                motivo = None
            am.n_posto_cam2 = 1 if esperada else None
            am.op_cam2 = True if esperada else None
            kwargs["diagnostico_presenca"].append({
                "tempo_s": am.tempo_s,
                "medido": esperada,
                "medicao_esperada": esperada,
                "motivo_sem_medicao": motivo,
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
        recebido["confirmar_n"] = len(amostras)
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
    pipeline_fake.etapa_confirmar_operador = confirmar
    pipeline_fake.make_supabase_client = lambda: None
    pipeline_fake.make_groq_client = lambda: None
    pipeline_fake.etapa_analise_vlm = lambda: None
    pipeline_fake.etapa_persistir = lambda: None
    pipeline_fake.processar_video = lambda: None

    worker_fake = ModuleType("backend.worker")

    def get_yolo():
        recebido["get_yolo"] += 1
        return SimpleNamespace()

    worker_fake._get_yolo = get_yolo
    worker_fake._modelo_path = lambda _nome: str(modelo)
    worker_fake.make_supabase_client = lambda: None
    worker_fake.make_groq_client = lambda: None
    worker_fake._baixar_video = lambda: None
    worker_fake._buscar_zonas_por_cam = lambda: None
    worker_fake.executar_job = lambda: None

    backend_fake = ModuleType("backend")
    backend_fake.pipeline = pipeline_fake
    backend_fake.worker = worker_fake

    modulos_antes = {
        nome: sys.modules.get(nome)
        for nome in ("backend", "backend.pipeline", "backend.worker")
    }
    manifesto_antes = diag._manifesto
    env_antes = {k: os.environ.get(k) for k in diag.base.ENV_OBRIGATORIO}

    def manifesto_fake(**kwargs):
        recebido["manifestos"].append({
            "relatorio": kwargs["cobertura_relatorio"],
            "processamento": kwargs["cobertura_processamento"],
        })
        return recebido["manifestos"][-1]

    try:
        sys.modules["backend"] = backend_fake
        sys.modules["backend.pipeline"] = pipeline_fake
        sys.modules["backend.worker"] = worker_fake
        diag._manifesto = manifesto_fake
        for chave, valor in diag.base.ENV_OBRIGATORIO.items():
            os.environ[chave] = valor
        sha1 = diag.base._sha256(cam1)
        sha2 = diag.base._sha256(cam2)

        try:
            diag.main([
                "--cam1", str(cam1),
                "--cam2", str(cam2),
                "--inicio", "5",
                "--fim", "20",
                "--cam2-offset-s", "10",
                "--sha256-cam1", sha1,
                "--sha256-cam2", sha2,
                "--zones-file", str(ZONAS),
                "--output", str(output_prestart),
            ])
            recusou_prestart = False
        except SystemExit as exc:
            recusou_prestart = "anterior ao arquivo" in str(exc)
        get_yolo_apos_prestart = recebido["get_yolo"]

        codigo = diag.main([
            "--cam1", str(cam1),
            "--cam2", str(cam2),
            "--inicio", "160",
            "--fim", "180",
            "--cam2-offset-s", "10",
            "--sha256-cam1", sha1,
            "--sha256-cam2", sha2,
            "--zones-file", str(ZONAS),
            "--output", str(output),
        ])
    finally:
        diag._manifesto = manifesto_antes
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

    with output.open("r", encoding="utf-8", newline="") as arq:
        linhas = list(csv.DictReader(arq))
    manifesto_path = output.with_suffix(".manifest.json")
    manifesto_escrito = json.loads(manifesto_path.read_text(encoding="utf-8"))

    check("pré-início falha fechado", recusou_prestart)
    check("pré-início falha antes de carregar YOLO", get_yolo_apos_prestart == 0)
    check("pré-início não cria CSV", not output_prestart.exists())
    check("execução parcial no fim conclui", codigo == 0)
    check("offset +10 vira -10 no anexador real", recebido["offset_pipeline"] == -10.0)
    check("lista entregue à cam2 começa em zero", recebido["cam2_primeiro"] == 0.0)
    check("pós-roll chega a 195 s", recebido["fim_processamento"] == 195.0
          and recebido["cam2_ultimo"] == 195.0)
    check("confirmação recebe warm-up, relatório e pós-roll",
          recebido["confirmar_n"] == 40)
    check("CSV contém somente 160–180 s", len(linhas) == 5
          and linhas[0]["tempo_s"] == "160.000"
          and linhas[-1]["tempo_s"] == "180.000")
    check("tempo alinhado usa t_cam2=t_cam1-10",
          linhas[0]["cam2_tempo_s_alinhado"] == "150.000"
          and linhas[-1]["cam2_tempo_s_alinhado"] == "170.000")
    check("slots finais ficam fora da cobertura sem falso erro",
          linhas[-2]["cam2_cobertura_temporal"] == "fora_depois_fim"
          and linhas[-2]["cam2_medicao_esperada"] == "false"
          and linhas[-2]["cam2_motivo_sem_medicao"] == "fora_janela_cam2"
          and linhas[-2]["cam2_pessoa_detectada"] == ""
          and linhas[-2]["cam2_n_detectadas_yolo"] == "")
    check("manifesto marca parcialidade final",
          recebido["manifestos"][-1]["relatorio"]["status"] == "parcial_fim"
          and manifesto_escrito["relatorio"]["status"] == "parcial_fim")


print("\n[5] README torna offset e comando Render auditáveis")
readme = README.read_text(encoding="utf-8")
check("README explica o sinal +10", "cam1 t=120 ↔ cam2 t=110" in readme)
check("README declara zero correção", "nenhuma correção" in readme)
check("README contém todos os argumentos", all(
    argumento in readme for argumento in (
        "--cam1", "--cam2", "--inicio", "--fim", "--cam2-offset-s",
        "--sha256-cam1", "--sha256-cam2", "--zones-file", "--output",
    )
))

print(f"\n{'=' * 64}\n  {ok} ok · {fail} falha(s)\n{'=' * 64}")
raise SystemExit(1 if fail else 0)
