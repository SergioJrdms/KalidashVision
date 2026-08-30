"""Contrato dependency-free do probe low-confidence do FP #1.

Rodar: ``python tests_diagnostico_fp1_lowconf.py``.
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace


RAIZ = Path(__file__).resolve().parent
SCRIPT = RAIZ / "diagnostics" / "diagnostico_fp1_lowconf.py"
README = RAIZ / "diagnostics" / "README_fp1_lowconf.md"
ZONAS = RAIZ / "diagnostics" / "fp1_zonas_camera_20260824.json"

spec = importlib.util.spec_from_file_location("diagnostico_fp1_lowconf", SCRIPT)
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


print("\n[1] Contrato estático do probe ortogonal")
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
check("não chama VLM/persistência", not {
    "etapa_analise_vlm", "etapa_persistir", "executar_job"
}.intersection(chamadas))
check("slots são exatamente os sete pedidos", diag.SLOTS_S == (
    120.0, 128.0, 136.0, 144.0, 152.0, 160.0, 168.0
))
check("probe usa conf=0.05", diag.PROBE_CONF == 0.05)
check("probe usa imgsz=416", diag.PROBE_IMGSZ == 416)
check("probe limita classe pessoa", diag.PROBE_CLASSES == [0])
check("saída se declara não-replay", "nao_replay_pipeline" in diag.DIAGNOSTICO_TIPO)


print("\n[2] Vídeos e geometria permanecem travados")
check(
    "hash cam1 é o anexo original",
    diag.VIDEOS_ESPERADOS_SHA256["cam1"]
    == "12afc1d3f8fdb4ce7d47a76f991c5c01855b94bc2ceaa8771f4d5330aa1adc36",
)
check(
    "hash cam2 é o anexo original",
    diag.VIDEOS_ESPERADOS_SHA256["cam2"]
    == "d68aeb8d0bee56ba1bfedd808d3789580ef9d176720f4b291718e17a9943cf26",
)
postos = diag.carregar_postos(ZONAS)
check("fixture fornece posto nas duas câmeras", set(postos) == {"cam1", "cam2"}
      and all(postos.values()))
check(
    "probe carrega somente posto_operador",
    all(
        info["papel"] == "posto_operador"
        for zonas in postos.values() for info in zonas.values()
    ),
)
with tempfile.TemporaryDirectory() as tmp:
    cam1_ruim = Path(tmp) / "cam1.mp4"
    cam2_ruim = Path(tmp) / "cam2.mp4"
    cam1_ruim.write_bytes(b"errado-1")
    cam2_ruim.write_bytes(b"errado-2")
    try:
        diag._validar_videos(cam1_ruim, cam2_ruim)
        recusou = False
    except SystemExit:
        recusou = True
check("hash divergente falha fechado", recusou)


print("\n[3] Bbox, keypoints, âncora, zona e fronteiras dos cortes")
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
dets_cam1 = diag._analisar_resultado(
    res_cam1,
    pipeline_fake,
    {"posto": {"polygon": "poligono-real"}},
    "cam1",
    100,
    100,
)
check("confidence 0.30 passa por comparação inclusiva", dets_cam1[0]["passa_conf"])
check("confidence 0.299 não passa", not dets_cam1[1]["passa_conf"])
check(
    "área usa bbox float como a máscara real",
    abs(dets_cam1[0]["area_ratio"] - 0.0015) < 1e-12,
    dets_cam1[0]["area_ratio"],
)
check("área exatamente no mínimo passa", dets_cam1[0]["passa_area_cam1"])
check("flag conjunta exige confiança e área", dets_cam1[0]["passa_conf_e_area_cam1"])
check(
    "bbox passada à âncora é inteira como na produção",
    chamadas_ancora[0][0]["bbox"] == (0, 0, 5, 3),
    chamadas_ancora[0][0],
)
check("keypoint disponível preserva índice/xyn/confidence",
      dets_cam1[0]["keypoints"] == [{
          "index": 5, "nome": "ombro_esq", "x": 0.2, "y": 0.3,
          "confidence": 0.88,
      }])
check("âncora vem do spy da função real", dets_cam1[0]["ancora"] == [17.25, 19.5])
check("teste de posto recebe essa mesma âncora", chamadas_roi[0] == (
    17.25, 19.5, "poligono-real"
))

res_cam2 = resultado_fake(
    [[0, 0, 10, 10], [0, 0, 10, 10]], [0.349, 0.35]
)
dets_cam2 = diag._analisar_resultado(
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


print("\n[4] Smoke do CLI: 14 seeks/predicts, sentinela e JPGs")


class FakeFrame:
    def __init__(self, largura, altura):
        self.shape = (altura, largura, 3)

    def copy(self):
        return self


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    cam1 = tmp_path / "5e88196d-6ebc-4f30-810c-44697edf80ad_seg_20260824_074000_roi.mp4"
    cam2 = tmp_path / "458c5cb8-4713-4b19-9b9b-aacd87b82cac_seg_20260824_074000_roi.mp4"
    cam1.write_bytes(b"cam1-fake")
    cam2.write_bytes(b"cam2-fake")
    modelo = tmp_path / "yolo11n-pose.pt"
    modelo.write_bytes(b"modelo-fake")
    output = tmp_path / "lowconf.csv"
    images_dir = tmp_path / "frames"
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
                [[1.2, 2.4, 11.8, 22.9]], [0.20], [kpts], [[0.5] * 17]
            )]

    yolo_fake = FakeYolo()

    def camera_de(caminho):
        return "cam1" if Path(caminho) == cam1 else "cam2"

    def inspecionar(caminho):
        if camera_de(caminho) == "cam1":
            return {"largura": 806, "altura": 304}
        return {"largura": 510, "altura": 546}

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
    hashes_antes = dict(diag.VIDEOS_ESPERADOS_SHA256)
    try:
        sys.modules["backend"] = backend_cli
        sys.modules["backend.pipeline"] = pipeline_cli
        sys.modules["backend.worker"] = worker_cli
        sys.modules["cv2"] = cv2_cli
        diag.VIDEOS_ESPERADOS_SHA256.update({
            "cam1": diag._sha256(cam1),
            "cam2": diag._sha256(cam2),
        })
        codigo = diag.main([
            "--cam1", str(cam1),
            "--cam2", str(cam2),
            "--zones-file", str(ZONAS),
            "--output", str(output),
            "--images-dir", str(images_dir),
        ])
    finally:
        diag.VIDEOS_ESPERADOS_SHA256.clear()
        diag.VIDEOS_ESPERADOS_SHA256.update(hashes_antes)
        for nome, modulo in modulos_antes.items():
            if modulo is None:
                sys.modules.pop(nome, None)
            else:
                sys.modules[nome] = modulo

    with output.open("r", encoding="utf-8", newline="") as arq:
        linhas = list(csv.DictReader(arq))
    esperados_ms = [tempo * 1000.0 for tempo in diag.SLOTS_S]
    check("CLI conclui", codigo == 0)
    check("loader real é chamado uma vez", registro["get_yolo"] == 1)
    check("há exatamente 14 predicts", len(registro["predict"]) == 14)
    check("todos os predicts usam somente os parâmetros pedidos", all(
        chamada == {
            "classes": [0], "conf": 0.05, "imgsz": 416,
            "verbose": False, "save": False,
        }
        for chamada in registro["predict"]
    ))
    check("cada câmera faz somente os sete seeks pedidos", all(
        registro["seeks"][camera] == esperados_ms
        for camera in ("cam1", "cam2")
    ))
    check("os dois vídeos são liberados", registro["released"] == ["cam1", "cam2"])
    check("CSV preserva os 14 câmera-slots", len(linhas) == 14)
    check("slot sem detecção vira sentinela explícita",
          linhas[0]["camera"] == "cam1"
          and linhas[0]["tempo_s"] == "120.000"
          and linhas[0]["deteccao_encontrada"] == "false"
          and linhas[0]["n_deteccoes_lowconf"] == "0")
    check("detecção abaixo de 0.30 fica marcada sem virar decisão",
          linhas[1]["confidence"] == "0.200000000"
          and linhas[1]["passa_conf_producao"] == "false"
          and linhas[1]["diagnostico_tipo"].endswith("nao_replay_pipeline"))
    check("funções geométricas reais são o caminho observado",
          registro["build_rois"] == 2
          and registro["ancora"] == 13
          and registro["roi"] == 13)
    check("imagens opcionais geram 14 JPGs", len(list(images_dir.glob("*.jpg"))) == 14)


print("\n[5] Documentação não confunde o probe com replay")
readme = README.read_text(encoding="utf-8")
check("README declara que não é replay", "NÃO é um replay do pipeline" in readme)
check("README declara que thresholds não mudam", "não muda os thresholds" in readme)
check("README contém o comando Render", "python diagnostics/diagnostico_fp1_lowconf.py" in readme)

print(f"\n{'=' * 60}\n  {ok} ok · {fail} falha(s)\n{'=' * 60}")
raise SystemExit(1 if fail else 0)
