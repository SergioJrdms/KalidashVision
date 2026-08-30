"""Contrato dependency-free do probe genérico low-confidence por caso.

Rodar: ``python tests_diagnostico_lowconf_caso.py``.
"""
from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace


RAIZ = Path(__file__).resolve().parent
SCRIPT = RAIZ / "diagnostics" / "diagnostico_lowconf_caso.py"
README = RAIZ / "diagnostics" / "README_diagnostico_lowconf_caso.md"
ZONAS = RAIZ / "diagnostics" / "fp1_zonas_camera_20260824.json"
SLOTS_FP2B = (64.0, 72.0, 80.0, 88.0, 96.0, 104.0, 112.0, 120.0)
HASH_FP2B_CAM1 = "1cbd3c52e2af6e1f6abe99fc445515104b378b64b76352be5d2235f44c4676e4"
HASH_FP2B_CAM2 = "b82a2951898e3b226c5fd5cbf626cc688abe314bb4b90d08b1f0b3d0b5e022af"

spec = importlib.util.spec_from_file_location("diagnostico_lowconf_caso", SCRIPT)
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


class FakeTensor:
    def __init__(self, valor):
        self.valor = valor

    def cpu(self):
        return self

    def tolist(self):
        return self.valor


class FakeBoxes:
    def __init__(self, bboxes, confiancas):
        self.xyxy = FakeTensor(bboxes)
        self.conf = FakeTensor(confiancas)
        self._n = len(bboxes)

    def __len__(self):
        return self._n


def resultado_fake(bboxes, confiancas, kpts=None, kpts_conf=None):
    keypoints = None
    if kpts is not None:
        keypoints = SimpleNamespace(
            xyn=FakeTensor(kpts),
            conf=FakeTensor(kpts_conf) if kpts_conf is not None else None,
        )
    return SimpleNamespace(
        boxes=FakeBoxes(bboxes, confiancas),
        keypoints=keypoints,
    )


print("\n[1] Contrato estático: probe bruto, nunca replay")
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

check("usa worker._get_yolo", "_get_yolo" in chamadas)
check("usa predict bruto", "predict" in chamadas)
check("não usa track", "track" not in chamadas)
check("não chama processar_video", "processar_video" not in chamadas)
check("não chama cadeia de replay", not {
    "etapa_detectar_e_amostrar",
    "_anexar_segundo_angulo",
    "etapa_confirmar_operador",
}.intersection(chamadas))
check("não chama VLM/persistência/jobs", not {
    "etapa_analise_vlm", "etapa_persistir", "executar_job"
}.intersection(chamadas))
check("probe usa conf=0.05", diag.PROBE_CONF == 0.05)
check("probe usa imgsz=416", diag.PROBE_IMGSZ == 416)
check("probe limita classe pessoa", diag.PROBE_CLASSES == [0])
check("saída se declara não-replay", "nao_replay_pipeline" in diag.DIAGNOSTICO_TIPO)
campos_minimos = {
    "camera", "tempo_cam1_s", "tempo_camera_s", "detection_index",
    "confidence", "bbox_xyxy", "area_ratio", "keypoints_n_validos",
    "keypoints_xyn_validos", "ancora_xy", "ancora_dentro_posto",
    "threshold_conf_producao", "passa_conf_producao",
    "operador_area_min_ratio", "passa_area_min_cam1",
    "passa_conf_e_area_cam1",
}
check("CSV contém todos os campos pedidos", campos_minimos.issubset(diag.CAMPOS_CSV))


print("\n[2] Slots, offset +10 e fail-closed")
slots = diag.parse_slots("64,72,80,88,96,104,112,120")
check("slots FP2B são exatamente os oito pedidos", slots == SLOTS_FP2B)
check("cam1 64 mapeia cam2 54", diag.cam2_tempo_s(64, 10) == 54)
check("cam1 72 mapeia cam2 62", diag.cam2_tempo_s(72, 10) == 62)
check("cam1 80 mapeia cam2 70", diag.cam2_tempo_s(80, 10) == 70)
check("cam1 88 mapeia cam2 78", diag.cam2_tempo_s(88, 10) == 78)
check("cam1 96 mapeia cam2 86", diag.cam2_tempo_s(96, 10) == 86)
check("cam1 104 mapeia cam2 94", diag.cam2_tempo_s(104, 10) == 94)
check("cam1 112 mapeia cam2 102", diag.cam2_tempo_s(112, 10) == 102)
check("cam1 120 mapeia cam2 110", diag.cam2_tempo_s(120, 10) == 110)

invalidos = ("", "64,", "64,64", "72,64", "nan", "-1,8")
for valor in invalidos:
    try:
        diag.parse_slots(valor)
        recusou = False
    except (SystemExit, ValueError, argparse.ArgumentTypeError):
        recusou = True
    check(f"slots inválidos recusados: {valor!r}", recusou)
try:
    diag._normalizar_sha256("abc", "--sha256-cam1")
    sha_formato_recusado = False
except SystemExit:
    sha_formato_recusado = True
check("SHA-256 com formato inválido falha fechado", sha_formato_recusado)

with tempfile.TemporaryDirectory() as tmp:
    video = Path(tmp) / "video.mp4"
    video.write_bytes(b"video-errado")
    try:
        diag._validar_video(video, "0" * 64, "cam1")
        sha_conteudo_recusado = False
    except SystemExit:
        sha_conteudo_recusado = True
check("SHA-256 divergente falha fechado", sha_conteudo_recusado)

with tempfile.TemporaryDirectory() as tmp:
    images_aninhado = Path(tmp) / "frames"
    output_aninhado = images_aninhado / "resultado.csv"
    try:
        diag._validar_destinos(output_aninhado, images_aninhado)
        aninhamento_recusado = False
    except SystemExit:
        aninhamento_recusado = True
check("CSV dentro do diretório de JPGs é recusado", aninhamento_recusado)

try:
    diag._validar_cobertura_slots(
        (8.0, 16.0),
        10.0,
        {"duracao_s": 200.0, "fps": 10.0, "total_frames": 2000},
        {"duracao_s": 200.0, "fps": 10.0, "total_frames": 2000},
    )
    cobertura_recusada = False
except SystemExit:
    cobertura_recusada = True
check("slot que exige cam2 negativa falha fechado", cobertura_recusada)
try:
    diag._validar_cobertura_slots(
        (20.0,),
        0.0,
        {"duracao_s": 20.0, "fps": 10.0, "total_frames": 200},
        {"duracao_s": 20.0, "fps": 10.0, "total_frames": 200},
    )
    eof_recusado = False
except SystemExit:
    eof_recusado = True
check("slot exatamente no EOF é recusado antes do YOLO", eof_recusado)


print("\n[3] Geometria real e cortes somente observacionais")
chamadas_ancora = []
chamadas_roi = []


def ancora_spy(pessoa, largura, altura):
    chamadas_ancora.append((pessoa, largura, altura))
    return 17.25, 19.5


def roi_spy(x, y, polygon):
    chamadas_roi.append((x, y, polygon))
    return True


pipeline_fake = SimpleNamespace(
    _OPERADOR_CONF=0.30,
    _CAM2_CONF=0.35,
    _OPERADOR_AREA_MIN_RATIO=0.0015,
    _ponto_ancora=ancora_spy,
    _ponto_em_roi=roi_spy,
)
kpt = [[0.0, 0.0] for _ in range(17)]
kpt[5] = [0.20, 0.30]
kpt_conf = [0.0] * 17
kpt_conf[5] = 0.88
res_cam1 = resultado_fake(
    [[0.2, 0.4, 5.2, 3.4], [10.0, 10.0, 20.0, 20.0]],
    [0.30, 0.299],
    [kpt, kpt],
    [kpt_conf, kpt_conf],
)
dets_cam1 = diag.base._analisar_resultado(
    res_cam1,
    pipeline_fake,
    {"posto": {"polygon": "poligono-real"}},
    "cam1",
    100,
    100,
)
check("confidence 0.30 passa por comparação inclusiva", dets_cam1[0]["passa_conf"])
check("confidence 0.299 não passa", not dets_cam1[1]["passa_conf"])
check("área usa bbox float como a função validada", abs(
    dets_cam1[0]["area_ratio"] - 0.0015
) < 1e-12)
check("área exatamente no mínimo passa", dets_cam1[0]["passa_area_cam1"])
check("flag conjunta exige confiança e área", dets_cam1[0]["passa_conf_e_area_cam1"])
check("bbox inteira é entregue à âncora real", chamadas_ancora[0][0]["bbox"] == (
    0, 0, 5, 3
))
check("keypoint preserva índice, xyn e confidence", dets_cam1[0]["keypoints"] == [{
    "index": 5,
    "nome": "ombro_esq",
    "x": 0.2,
    "y": 0.3,
    "confidence": 0.88,
}])
check("a mesma âncora é testada no posto", chamadas_roi[0] == (
    17.25, 19.5, "poligono-real"
))
res_cam2 = resultado_fake(
    [[0, 0, 10, 10], [0, 0, 10, 10]],
    [0.349, 0.35],
)
dets_cam2 = diag.base._analisar_resultado(
    res_cam2,
    pipeline_fake,
    {"posto": {"polygon": "poligono-real"}},
    "cam2",
    100,
    100,
)
check("cam2 0.349 não passa", not dets_cam2[0]["passa_conf"])
check("cam2 0.35 passa", dets_cam2[1]["passa_conf"])
check("cam2 não inventa corte de área", dets_cam2[0]["passa_area_cam1"] is None)


print("\n[4] Smoke CLI: 16 predicts, offset, sentinela e 16 JPGs")


class FakeFrame:
    def __init__(self, largura, altura):
        self.shape = (altura, largura, 3)

    def copy(self):
        return self


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    cam1 = tmp_path / "cam1.mp4"
    cam2 = tmp_path / "cam2.mp4"
    cam1.write_bytes(b"cam1-fake-fp2b")
    cam2.write_bytes(b"cam2-fake-fp2b")
    modelo = tmp_path / "yolo11n-pose.pt"
    modelo.write_bytes(b"modelo-fake")
    output = tmp_path / "lowconf-caso.csv"
    output_fora = tmp_path / "fora.csv"
    output_stale = tmp_path / "stale.csv"
    output_nan = tmp_path / "nan.csv"
    output_area_nan = tmp_path / "area-nan.csv"
    images_dir = tmp_path / "frames"
    images_stale = tmp_path / "frames-stale"
    images_nan = tmp_path / "frames-nan"
    images_area_nan = tmp_path / "frames-area-nan"
    images_stale.mkdir()
    (images_stale / "anterior.jpg").write_bytes(b"stale")
    registro = {
        "get_yolo": 0,
        "predict": [],
        "seeks": {"cam1": [], "cam2": []},
        "released": [],
        "build_rois": 0,
        "ancora": 0,
        "roi": 0,
    }

    class FakeYolo:
        def predict(self, _frame, **kwargs):
            indice = len(registro["predict"])
            registro["predict"].append(kwargs)
            if indice == 0:
                return [resultado_fake([], [])]
            kpts = [[0.0, 0.0] for _ in range(17)]
            kpts[5] = [0.2, 0.3]
            return [resultado_fake(
                [[1.2, 2.4, 11.8, 22.9]],
                [0.20],
                [kpts],
                [[0.5] * 17],
            )]

    yolo_fake = FakeYolo()

    def camera_de(caminho):
        return "cam1" if Path(caminho) == cam1 else "cam2"

    def inspecionar(caminho):
        if camera_de(caminho) == "cam1":
            return {
                "largura": 806,
                "altura": 304,
                "duracao_s": 200.0,
                "fps": 10.0,
                "total_frames": 2000,
            }
        return {
            "largura": 510,
            "altura": 546,
            "duracao_s": 180.0,
            "fps": 10.0,
            "total_frames": 1800,
        }

    def build_rois(zonas, largura, altura):
        registro["build_rois"] += 1
        return {
            nome: {**info, "polygon": ("poly", largura, altura)}
            for nome, info in zonas.items()
        }

    def ponto_ancora(pessoa, _largura, _altura):
        registro["ancora"] += 1
        x1, y1, x2, y2 = pessoa["bbox"]
        return (x1 + x2) / 2.0, y1 + 0.3 * (y2 - y1)

    def ponto_roi(_x, _y, _polygon):
        registro["roi"] += 1
        return True

    pipeline_cli = ModuleType("backend.pipeline")
    pipeline_cli.YOLO_MODEL = "yolo11n-pose.pt"
    pipeline_cli._OPERADOR_CONF = 0.30
    pipeline_cli._CAM2_CONF = 0.35
    pipeline_cli._OPERADOR_AREA_MIN_RATIO = 0.0015
    pipeline_cli.inspecionar_video = inspecionar
    pipeline_cli._build_rois = build_rois
    pipeline_cli._ponto_ancora = ponto_ancora
    pipeline_cli._ponto_em_roi = ponto_roi
    pipeline_cli.make_supabase_client = lambda: None
    pipeline_cli.make_groq_client = lambda: None
    pipeline_cli.etapa_analise_vlm = lambda: None
    pipeline_cli.etapa_persistir = lambda: None
    pipeline_cli.processar_video = lambda: None

    worker_cli = ModuleType("backend.worker")

    def get_yolo():
        registro["get_yolo"] += 1
        return yolo_fake

    worker_cli._get_yolo = get_yolo
    worker_cli._modelo_path = lambda _nome: str(modelo)
    worker_cli.make_supabase_client = lambda: None
    worker_cli.make_groq_client = lambda: None
    worker_cli._baixar_video = lambda: None
    worker_cli._buscar_zonas_por_cam = lambda: None
    worker_cli.executar_job = lambda: None

    backend_cli = ModuleType("backend")
    backend_cli.pipeline = pipeline_cli
    backend_cli.worker = worker_cli

    cv2_cli = ModuleType("cv2")
    cv2_cli.CAP_PROP_POS_MSEC = 1
    cv2_cli.CAP_PROP_POS_FRAMES = 2
    cv2_cli.FONT_HERSHEY_SIMPLEX = 0

    class FakeCapture:
        def __init__(self, caminho):
            self.camera = camera_de(caminho)
            self.seek_atual = 0.0

        def isOpened(self):
            return True

        def set(self, prop, valor):
            assert prop == cv2_cli.CAP_PROP_POS_MSEC
            self.seek_atual = float(valor)
            registro["seeks"][self.camera].append(float(valor))
            return True

        def read(self):
            if self.camera == "cam1":
                return True, FakeFrame(806, 304)
            return True, FakeFrame(510, 546)

        def get(self, prop):
            if prop == cv2_cli.CAP_PROP_POS_FRAMES:
                return self.seek_atual / 1000.0 * 6.0 + 1.0
            if prop == cv2_cli.CAP_PROP_POS_MSEC:
                return self.seek_atual
            return 0.0

        def release(self):
            registro["released"].append(self.camera)

    cv2_cli.VideoCapture = FakeCapture
    cv2_cli.polylines = lambda *_args, **_kwargs: None
    cv2_cli.putText = lambda *_args, **_kwargs: None
    cv2_cli.rectangle = lambda *_args, **_kwargs: None
    cv2_cli.circle = lambda *_args, **_kwargs: None

    def imwrite(caminho, _frame):
        Path(caminho).write_bytes(b"jpg-fake")
        return True

    cv2_cli.imwrite = imwrite

    modulos_antes = {
        nome: sys.modules.get(nome)
        for nome in ("backend", "backend.pipeline", "backend.worker", "cv2")
    }
    try:
        sys.modules["backend"] = backend_cli
        sys.modules["backend.pipeline"] = pipeline_cli
        sys.modules["backend.worker"] = worker_cli
        sys.modules["cv2"] = cv2_cli
        base_args = [
            "--cam1", str(cam1),
            "--cam2", str(cam2),
            "--cam2-offset-s", "10",
            "--sha256-cam1", diag.base._sha256(cam1),
            "--sha256-cam2", diag.base._sha256(cam2),
            "--zones-file", str(ZONAS),
            "--images-dir", str(images_dir),
        ]

        try:
            diag.main([
                *base_args,
                "--slots", "64,72,80,88,96,104,112,120",
                "--sha256-cam1", "0" * 64,
                "--output", str(output),
            ])
            hash_main_recusado = False
        except SystemExit:
            hash_main_recusado = True

        try:
            diag.main([
                *base_args,
                "--slots", "8,16",
                "--output", str(output_fora),
            ])
            slot_main_recusado = False
        except SystemExit:
            slot_main_recusado = True

        try:
            diag.main([
                *base_args,
                "--slots", "64,72,80,88,96,104,112,120",
                "--output", str(output_stale),
                "--images-dir", str(images_stale),
            ])
            destino_stale_recusado = False
        except SystemExit:
            destino_stale_recusado = True

        pipeline_cli._OPERADOR_CONF = float("nan")
        try:
            diag.main([
                *base_args,
                "--slots", "64,72,80,88,96,104,112,120",
                "--output", str(output_nan),
                "--images-dir", str(images_nan),
            ])
            threshold_nan_recusado = False
        except SystemExit:
            threshold_nan_recusado = True
        finally:
            pipeline_cli._OPERADOR_CONF = 0.30

        pipeline_cli._OPERADOR_AREA_MIN_RATIO = float("nan")
        try:
            diag.main([
                *base_args,
                "--slots", "64,72,80,88,96,104,112,120",
                "--output", str(output_area_nan),
                "--images-dir", str(images_area_nan),
            ])
            area_nan_recusada = False
        except SystemExit:
            area_nan_recusada = True
        finally:
            pipeline_cli._OPERADOR_AREA_MIN_RATIO = 0.0015

        pre_inferencia_limpo = (
            registro["get_yolo"] == 0
            and not registro["predict"]
            and not registro["seeks"]["cam1"]
            and not registro["seeks"]["cam2"]
            and not output.exists()
            and not output_fora.exists()
            and not output_stale.exists()
            and not output_nan.exists()
            and not output_area_nan.exists()
        )

        codigo = diag.main([
            *base_args,
            "--slots", "64,72,80,88,96,104,112,120",
            "--output", str(output),
        ])
    finally:
        for nome, modulo in modulos_antes.items():
            if modulo is None:
                sys.modules.pop(nome, None)
            else:
                sys.modules[nome] = modulo

    with output.open("r", encoding="utf-8", newline="") as arq:
        linhas = list(csv.DictReader(arq))
    cam1_ms = [tempo * 1000.0 for tempo in SLOTS_FP2B]
    cam2_ms = [(tempo - 10.0) * 1000.0 for tempo in SLOTS_FP2B]
    imagens = sorted(images_dir.glob("*.jpg"))

    check("hash divergente no CLI é recusado", hash_main_recusado)
    check("slot cam2 negativo no CLI é recusado", slot_main_recusado)
    check("diretório de imagens não vazio é recusado", destino_stale_recusado)
    check("threshold NaN é recusado", threshold_nan_recusado)
    check("threshold de área NaN é recusado", area_nan_recusada)
    check("falhas fechadas acontecem antes do YOLO/seek/CSV", pre_inferencia_limpo)
    check("CLI conclui", codigo == 0)
    check("loader real é chamado uma vez", registro["get_yolo"] == 1)
    check("há exatamente 16 predicts", len(registro["predict"]) == 16)
    check("todos os predicts usam exatamente os parâmetros pedidos", all(
        chamada == {
            "classes": [0],
            "conf": 0.05,
            "imgsz": 416,
            "verbose": False,
            "save": False,
        }
        for chamada in registro["predict"]
    ))
    check("cam1 faz exatamente os oito seeks pedidos", registro["seeks"]["cam1"] == cam1_ms)
    check("cam2 aplica offset +10 como subtração", registro["seeks"]["cam2"] == cam2_ms)
    check("os dois vídeos são liberados", registro["released"] == ["cam1", "cam2"])
    check("CSV preserva os 16 câmera-slots", len(linhas) == 16)
    check("slot sem detecção vira sentinela explícita", (
        linhas[0]["camera"] == "cam1"
        and linhas[0]["tempo_cam1_s"] == "64.000"
        and linhas[0]["tempo_camera_s"] == "64.000"
        and linhas[0]["deteccao_encontrada"] == "false"
        and linhas[0]["n_deteccoes_lowconf"] == "0"
        and linhas[0]["confidence"] == ""
        and linhas[0]["bbox_xyxy"] == ""
        and linhas[0]["ancora_xy"] == ""
        and linhas[0]["passa_conf_producao"] == ""
    ))
    check("cam2 registra slot lógico 64 e seek real 54", (
        linhas[8]["camera"] == "cam2"
        and linhas[8]["tempo_cam1_s"] == "64.000"
        and linhas[8]["tempo_camera_s"] == "54.000"
        and linhas[8]["cam2_offset_s"] == "10.000"
    ))
    check("candidato abaixo de 0.30 continua só observacional", (
        linhas[1]["confidence"] == "0.200000000"
        and linhas[1]["passa_conf_producao"] == "false"
        and linhas[1]["diagnostico_tipo"].endswith("nao_replay_pipeline")
    ))
    check("funções geométricas reais são o caminho observado", (
        registro["build_rois"] == 2
        and registro["ancora"] == 15
        and registro["roi"] == 15
    ))
    check("são gerados exatamente 16 JPGs", len(imagens) == 16)
    check("nomes dos JPGs distinguem slot e seek da cam2", any(
        "cam2_slot_01_cam1_000064000ms_camera_000054000ms" in imagem.name
        for imagem in imagens
    ))


print("\n[5] README trava o comando do FP2B")
readme = README.read_text(encoding="utf-8")
check("README declara que não é replay", "NÃO é um replay do pipeline" in readme)
check("README declara que thresholds não mudam", "não mudam thresholds" in readme)
check("README contém os oito slots", "64,72,80,88,96,104,112,120" in readme)
check("README contém offset +10", "--cam2-offset-s 10" in readme)
check("README contém hash cam1", HASH_FP2B_CAM1 in readme)
check("README contém hash cam2", HASH_FP2B_CAM2 in readme)
check("README contém comando Render", "python diagnostics/diagnostico_lowconf_caso.py" in readme)
check("README exige 16 JPGs", "-eq 16" in readme)
check("README usa shell fail-fast", "set -euo pipefail" in readme)
check("README usa diretório novo por execução", "mktemp -d" in readme)

print(f"\n{'=' * 64}\n  {ok} ok · {fail} falha(s)\n{'=' * 64}")
raise SystemExit(1 if fail else 0)
