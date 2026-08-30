"""Contrato dependency-free do diagnóstico track-vs-predict do FP2B.

Rodar da raiz do repositório:

    python diagnostics/tests_diagnostico_track_vs_predict_caso.py

O teste usa somente a biblioteca padrão. OpenCV, Ultralytics, Torch e os vídeos
reais são substituídos por doubles estritamente observacionais.
"""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace


RAIZ = Path(__file__).resolve().parent
DIAGNOSTICS = RAIZ / "diagnostics"
SCRIPT = DIAGNOSTICS / "diagnostico_track_vs_predict_caso.py"

spec = importlib.util.spec_from_file_location(
    "diagnostico_track_vs_predict_caso", SCRIPT
)
diag = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(diag)

ok = fail = 0


def check(nome, condicao, extra=""):
    global ok, fail
    if condicao:
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
    def __init__(self, bboxes, confiancas, ids=None):
        self.xyxy = FakeTensor(bboxes)
        self.conf = FakeTensor(confiancas)
        self.id = FakeTensor(ids) if ids is not None else None
        self._n = len(bboxes)

    def __len__(self):
        return self._n


def resultado_fake(bboxes=None, confiancas=None, ids=None, kpts=None):
    boxes = FakeBoxes(bboxes or [], confiancas or [], ids)
    keypoints = (
        SimpleNamespace(xyn=FakeTensor(kpts)) if kpts is not None else None
    )
    return SimpleNamespace(boxes=boxes, keypoints=keypoints)


class FakeFrame:
    def __init__(self, rotulo, largura=510, altura=546):
        self.rotulo = str(rotulo)
        self.shape = (altura, largura, 3)

    def tobytes(self):
        return self.rotulo.encode("utf-8")


def cv2_fake():
    registro = {"seeks": [], "releases": 0}
    modulo = SimpleNamespace(CAP_PROP_POS_MSEC=1, CAP_PROP_POS_FRAMES=2)

    class FakeCapture:
        def __init__(self, caminho):
            self.caminho = str(caminho)
            self.alvo_ms = 0.0

        def isOpened(self):
            return True

        def set(self, prop, valor):
            assert prop == modulo.CAP_PROP_POS_MSEC
            self.alvo_ms = float(valor)
            registro["seeks"].append(self.alvo_ms)
            return True

        def read(self):
            return True, FakeFrame(f"{self.caminho}@{self.alvo_ms:.3f}")

        def get(self, prop):
            if prop == modulo.CAP_PROP_POS_MSEC:
                return self.alvo_ms
            if prop == modulo.CAP_PROP_POS_FRAMES:
                return self.alvo_ms / 1000.0 * 6.0 + 1.0
            return 0.0

        def release(self):
            registro["releases"] += 1

    modulo.VideoCapture = FakeCapture
    return modulo, registro


class FakeYolo:
    def __init__(self, rotulo):
        self.rotulo = rotulo
        self.predict_calls = []
        self.track_calls = []
        self.predictor = None

    @staticmethod
    def _resultado(indice):
        # Box sem ID de propósito: o detector encontrou uma pessoa mesmo quando
        # o tracker não conseguiu atribuir identidade.
        kpts = [[[0.0, 0.0] for _ in range(17)]]
        kpts[0][5] = [0.2, 0.3]
        return resultado_fake(
            [[1.2 + indice, 2.4, 11.8 + indice, 22.9]],
            [0.40],
            ids=None,
            kpts=kpts,
        )

    def predict(self, _frame, **kwargs):
        self.predict_calls.append(dict(kwargs))
        return [self._resultado(len(self.predict_calls))]

    def track(self, _frame, **kwargs):
        self.track_calls.append(dict(kwargs))
        return [self._resultado(len(self.track_calls))]


PARAMETROS_PREDICT = {
    "classes": [0],
    "conf": 0.35,
    "imgsz": 416,
    "verbose": False,
    "save": False,
}
PARAMETROS_TRACK = {
    "classes": [0],
    "conf": 0.35,
    "imgsz": 416,
    "persist": True,
    "tracker": "tracker-real.yaml",
    "verbose": False,
}


print("\n[1] Contrato estático e natureza estritamente diagnóstica")
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

check("carrega o modelo pelo worker", "_get_yolo" in chamadas)
check("modo A usa predict", "predict" in chamadas)
check("modos B/C/D usam track", "track" in chamadas)
check("D chama etapa_detectar_e_amostrar real",
      "etapa_detectar_e_amostrar" in chamadas)
check("D passa pela ponte real da cam2", "_anexar_segundo_angulo" in chamadas)
check("D observa o reset real",
      "resetar_tracker" in chamadas
      or "pipeline.resetar_tracker = reset_observado" in fonte)
check("modos são isolados por subprocess.run", "run" in chamadas)
check("geometria chama funções reais", {
    "_build_rois", "_ponto_ancora", "_ponto_em_roi"
}.issubset(chamadas))
check("não chama correção, decisão final ou integrações", not {
    "processar_video",
    "etapa_confirmar_operador",
    "etapa_analise_vlm",
    "etapa_persistir",
    "executar_job",
    "make_supabase_client",
    "make_groq_client",
}.intersection(chamadas))
check("não altera envs KV", "os.environ[" not in fonte
      and "os.environ.setdefault" not in fonte)
check("saída se declara observacional/sem correção",
      "observacional" in diag.TIPO_DIAGNOSTICO
      and "sem_correcao" in diag.TIPO_DIAGNOSTICO)
check("quatro modos estão travados", diag.MODOS == ("A", "B", "C", "D"))
check("alinhamento FP2B está travado",
      diag.TARGET_CAM1_S == 88.0
      and diag.TARGET_CAM2_S == 78.0
      and diag.CAM2_OFFSET_ESPERADO_S == 10.0)
check("contextos exatos estão travados",
      diag.CAM1_CONTEXT_S == (64.0, 72.0, 80.0, 88.0)
      and diag.CAM2_CONTEXT_S == (54.0, 62.0, 70.0, 78.0))
check("parâmetros cam2 estão travados",
      diag.TRACK_CONF == 0.35
      and diag.IMGSZ == 416
      and diag.CLASSES == [0])


print("\n[2] Hashes falham fechado antes de inferência/subprocessos")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    cam1 = tmp_path / "cam1.mp4"
    cam2 = tmp_path / "cam2.mp4"
    zonas = tmp_path / "zonas.json"
    output = tmp_path / "saida.csv"
    cam1.write_bytes(b"cam1-fake")
    cam2.write_bytes(b"cam2-fake")
    zonas.write_text("{}", encoding="utf-8")

    try:
        diag._normalizar_sha256("z" * 64, "--sha256-cam1")
        recusou_formato = False
    except SystemExit:
        recusou_formato = True
    check("SHA não hexadecimal é recusado", recusou_formato)

    hash_original = diag.lowconf_base._sha256
    subprocessos_original = diag._executar_subprocessos
    chamou_subprocesso = []
    try:
        diag.lowconf_base._sha256 = lambda _p: diag.HASHES_FP2B["cam1"]
        try:
            diag._validar_video_fp2b(cam1, "0" * 64, "cam1")
            recusou_informado = False
        except SystemExit:
            recusou_informado = True

        diag.lowconf_base._sha256 = lambda _p: "f" * 64
        try:
            diag._validar_video_fp2b(
                cam1, diag.HASHES_FP2B["cam1"], "cam1"
            )
            recusou_arquivo = False
        except SystemExit:
            recusou_arquivo = True

        def subprocesso_proibido(_args):
            chamou_subprocesso.append(True)
            raise AssertionError("subprocesso não deveria iniciar")

        diag._executar_subprocessos = subprocesso_proibido
        try:
            diag.main([
                "--cam1", str(cam1),
                "--cam2", str(cam2),
                "--cam2-offset-s", "10",
                "--runner-inicio-s", "64",
                "--runner-fim-s", "88",
                "--sha256-cam1", "0" * 64,
                "--sha256-cam2", diag.HASHES_FP2B["cam2"],
                "--zones-file", str(zonas),
                "--output", str(output),
            ])
            main_recusou = False
        except SystemExit:
            main_recusou = True
    finally:
        diag.lowconf_base._sha256 = hash_original
        diag._executar_subprocessos = subprocessos_original

check("hash informado divergente é recusado", recusou_informado)
check("conteúdo do arquivo divergente é recusado", recusou_arquivo)
check("main falha antes dos subprocessos", main_recusou
      and chamou_subprocesso == [])


print("\n[3] A/B/C executam APIs, parâmetros e sequências exatos")
cv_a, reg_a = cv2_fake()
yolo_a = FakeYolo("A")
resultado_a, eventos_a, frame_a = diag._ler_sequencia_direta(
    cv2=cv_a,
    yolo=yolo_a,
    video_path=Path("cam2.mp4"),
    tempos_cam2=(78.0,),
    modo="A",
    tracker_config="tracker-real.yaml",
)
check("A faz um único seek em 78 s", reg_a["seeks"] == [78000.0])
check("A faz exatamente um predict e nenhum track",
      yolo_a.predict_calls == [PARAMETROS_PREDICT]
      and yolo_a.track_calls == [])
check("A registra o frame final", len(eventos_a) == 1
      and frame_a["tempo_cam2_s"] == 78.0
      and resultado_a is not None)

cv_b, reg_b = cv2_fake()
yolo_b = FakeYolo("B")
resultado_b, eventos_b, frame_b = diag._ler_sequencia_direta(
    cv2=cv_b,
    yolo=yolo_b,
    video_path=Path("cam2.mp4"),
    tempos_cam2=(78.0,),
    modo="B",
    tracker_config="tracker-real.yaml",
)
check("B faz um único seek em 78 s", reg_b["seeks"] == [78000.0])
check("B faz exatamente um track e nenhum predict",
      yolo_b.track_calls == [PARAMETROS_TRACK]
      and yolo_b.predict_calls == [])
check("B registra o frame final", len(eventos_b) == 1
      and frame_b["tempo_cam2_s"] == 78.0
      and resultado_b is not None)

cv_c, reg_c = cv2_fake()
yolo_c = FakeYolo("C")
resultado_c, eventos_c, frame_c = diag._ler_sequencia_direta(
    cv2=cv_c,
    yolo=yolo_c,
    video_path=Path("cam2.mp4"),
    tempos_cam2=diag.CAM2_CONTEXT_S,
    modo="C",
    tracker_config="tracker-real.yaml",
)
check("C faz os quatro seeks exatos",
      reg_c["seeks"] == [54000.0, 62000.0, 70000.0, 78000.0])
check("C faz quatro tracks persistentes no mesmo objeto",
      len(yolo_c.track_calls) == 4
      and all(chamada == PARAMETROS_TRACK for chamada in yolo_c.track_calls)
      and yolo_c.predict_calls == [])
check("C mantém contexto só na telemetria e final em 78 s",
      len(eventos_c) == 4
      and [e["tempo_cam2_s"] for e in eventos_c] == [54.0, 62.0, 70.0, 78.0]
      and frame_c["tempo_cam2_s"] == 78.0
      and resultado_c is not None)
check("A/B/C usam objetos YOLO isolados",
      len({id(yolo_a), id(yolo_b), id(yolo_c)}) == 3)
check("captures A/B/C são sempre liberados",
      reg_a["releases"] == reg_b["releases"] == reg_c["releases"] == 1)


print("\n[4] D usa a cadeia real simulada, na ordem e no mesmo YOLO")
ordem = []
identidades = []
yolo_d = FakeYolo("D")


def etapa_detectar(
    yolo, video, intervalo, zonas, progress, *, cam_id, mapa_movimento,
    identidade_shadow, fim_s,
):
    del video, zonas, progress, mapa_movimento, identidade_shadow
    ordem.append(("etapa_cam1", fim_s, intervalo, cam_id))
    identidades.append(id(yolo))
    amostras = [SimpleNamespace(tempo_s=float(t)) for t in range(0, 113, 8)]
    return amostras, {"duracao_s": 300.0}, [], [], {}, {}


def resetar(yolo):
    ordem.append(("reset_cam2",))
    identidades.append(id(yolo))
    return "reset"


pipeline_d = SimpleNamespace(
    DEFAULT_INTERVALO_AMOSTRAGEM_S=8.0,
    _OPERADOR_GAP_SLOTS=3,
    _CAM2_CONFIRM_STRIDE=1,
    TRACKER_CONFIG="tracker-real.yaml",
    etapa_detectar_e_amostrar=etapa_detectar,
    resetar_tracker=resetar,
)


def anexar(
    amostras, video, *, yolo, rois_sec, offset_s, desc_acc,
    identidade_shadow, cam_id, diagnostico_presenca,
):
    del video, rois_sec, identidade_shadow
    ordem.append(("anexar_cam2", tuple(am.tempo_s for am in amostras),
                  offset_s, desc_acc, cam_id))
    identidades.append(id(yolo))
    # Reproduz somente a mecânica externa relevante da função real: reset uma
    # vez porque desc_acc={} foi fornecido, depois quatro tracks persistentes.
    pipeline_d.resetar_tracker(yolo)
    for amostra in amostras:
        tempo_cam2 = float(amostra.tempo_s) + float(offset_s)
        ordem.append(("track_cam2", tempo_cam2))
        yolo.track(
            FakeFrame(f"cam2@{tempo_cam2}"),
            classes=[0],
            conf=0.35,
            imgsz=416,
            persist=True,
            tracker=pipeline_d.TRACKER_CONFIG,
            verbose=False,
        )
        diagnostico_presenca.append({
            "tempo_s": float(amostra.tempo_s),
            "medido": True,
        })
    return len(amostras)


pipeline_d._anexar_segundo_angulo = anexar
args_d = Namespace(
    runner_inicio_s=64.0,
    runner_fim_s=88.0,
    cam2_offset_s=10.0,
    cam1=Path("cam1.mp4"),
    cam2=Path("cam2.mp4"),
)
zonas_d = {
    "cam1": {"posto": {"papel": "posto_operador"}},
    "cam2": {"posto": {"papel": "posto_operador"}},
}
resultado_d, eventos_d, frame_d, contexto_d, motivo_d = diag._executar_modo_d(
    args=args_d,
    pipeline=pipeline_d,
    yolo=yolo_d,
    zonas_por_cam=zonas_d,
    info_cam1={"duracao_s": 300.0},
)

nomes_ordem = [evento[0] for evento in ordem]
check("D processa cam1 antes de anexar cam2",
      nomes_ordem[:3] == ["etapa_cam1", "anexar_cam2", "reset_cam2"])
check("D aquece cam1 desde zero e preserva pós-roll",
      ordem[0] == ("etapa_cam1", 112.0, 8.0, "cam1")
      and contexto_d["cam1"]["primeira_amostra_s"] == 0.0
      and contexto_d["cam1"]["ultima_amostra_s"] == 112.0)
check("D seleciona contexto cam1 e aplica offset -10",
      ordem[1][1] == (64.0, 72.0, 80.0, 88.0)
      and ordem[1][2] == -10.0
      and ordem[1][3] == {})
check("D observa exatamente um reset da ponte",
      nomes_ordem.count("reset_cam2") == 1
      and contexto_d["reset_ponte"]["retorno"] == "reset")
check("D faz tracks cam2 54/62/70/78",
      [evento[1] for evento in ordem if evento[0] == "track_cam2"]
      == [54.0, 62.0, 70.0, 78.0])
check("D usa kwargs cam2 exatos",
      len(yolo_d.track_calls) == 4
      and all(chamada == PARAMETROS_TRACK for chamada in yolo_d.track_calls))
check("D usa o mesmo objeto no estágio, anexo, reset e tracks",
      identidades and set(identidades) == {id(yolo_d)})
check("D registra somente o resultado final",
      len(eventos_d) == 4
      and frame_d["tempo_cam2_s"] == 78.0
      and resultado_d is not None
      and motivo_d is None)


print("\n[5] Geometria real, box sem ID e linha sentinela")
chamadas_ancora = []
chamadas_roi = []


def ancora(pessoa, largura, altura):
    chamadas_ancora.append((pessoa, largura, altura))
    return 17.25, 19.5


def em_roi(x, y, poligono):
    chamadas_roi.append((x, y, poligono))
    return True


pipeline_geometria = SimpleNamespace(
    _ponto_ancora=ancora,
    _ponto_em_roi=em_roi,
)
kpts = [[[0.0, 0.0] for _ in range(17)]]
kpts[0][5] = [0.2, 0.3]
deteccoes = diag._extrair_deteccoes(
    resultado_fake([[1.2, 2.4, 11.8, 22.9]], [0.40], ids=None, kpts=kpts),
    pipeline_geometria,
    {"posto": {"polygon": "poligono-real"}},
    510,
    546,
)
check("box sem ID continua sendo detecção",
      len(deteccoes) == 1 and deteccoes[0]["track_id"] is None)
check("bbox inteira/keypoints chegam à âncora real",
      chamadas_ancora[0][0]["bbox"] == (1, 2, 11, 22)
      and chamadas_ancora[0][0]["kpts"] == kpts[0])
check("mesma âncora chega à função de zona real",
      chamadas_roi == [(17.25, 19.5, "poligono-real")]
      and deteccoes[0]["ancora_dentro_posto"] is True)


def item_csv(modo, deteccoes_item, n_boxes):
    return {
        "diagnostico_tipo": diag.TIPO_DIAGNOSTICO,
        "modo": modo,
        "modo_descricao": "teste",
        "api_final": "predict" if modo == "A" else "track",
        "camera": "cam2",
        "tempo_cam1_s": 88.0,
        "tempo_cam2_s": 78.0,
        "sequencia_cam2_s": [78.0],
        "modo_valido": True,
        "motivo_invalidade": None,
        "n_pessoas_retornadas": n_boxes,
        "n_pessoas_dentro_posto": 0,
        "n_candidatos_operador_referencia": 0,
        "n_com_track_id": 0,
        "deteccoes": deteccoes_item,
        "frame_final": {
            "frame_sha256": "abc",
            "frame_shape": [546, 510, 3],
            "frame_pos_msec_apos_leitura": 78000.0,
        },
        "tracker_reset_antes_cam2": None,
        "eventos_inferencia": [{"parametros": PARAMETROS_PREDICT}],
        "configuracao": {
            "modelo": "yolo11n-pose.pt",
            "tracker_config": "tracker-real.yaml",
        },
    }


sentinela = diag._linhas_csv([item_csv("A", [], 0)])[0]
linha_sem_id = diag._linhas_csv([item_csv("B", deteccoes, 1)])[0]
check("resultado vazio vira uma sentinela explícita",
      sentinela["resultado_tem_box"] == "false"
      and sentinela["detection_index"] == ""
      and sentinela["confidence"] == ""
      and sentinela["bbox_xyxy"] == ""
      and sentinela["ancora_xy"] == "")
check("box sem ID não é convertida em sentinela",
      linha_sem_id["resultado_tem_box"] == "true"
      and linha_sem_id["track_id"] == ""
      and linha_sem_id["track_confirmado"] == "false"
      and linha_sem_id["bbox_xyxy"] != "")


print("\n[6] IoU é somente chave observacional, não uma correção")
def resultado_interpretacao(n_dentro, n_referencia):
    return {
        "modo": "A",
        "modo_valido": True,
        "n_pessoas_dentro_posto": n_dentro,
        "n_candidatos_operador_referencia": n_referencia,
    }


interpretacao_com_referencia = diag._interpretar([
    {**resultado_interpretacao(0, 1), "modo": modo}
    for modo in diag.MODOS
])
interpretacao_sem_referencia = diag._interpretar([
    {**resultado_interpretacao(1, 0), "modo": modo}
    for modo in diag.MODOS
])
check("classifica pela correspondência observada, não por mutação",
      interpretacao_com_referencia["codigo"] == "4_sem_divergencia_reprodutivel"
      and interpretacao_sem_referencia["codigo"] == "inconclusivo_predict_bruto_nao_reproduzido")
check("IoU da bbox de referência é geométrico e determinístico",
      diag._bbox_iou(diag.OPERADOR_REFERENCIA_BBOX,
                     diag.OPERADOR_REFERENCIA_BBOX) == 1.0
      and diag._bbox_iou((0, 0, 1, 1), (2, 2, 3, 3)) == 0.0)


print("\n[7] Orquestrador cria quatro subprocessos realmente separados")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    args_sub = Namespace(
        cam1=tmp_path / "cam1.mp4",
        cam2=tmp_path / "cam2.mp4",
        cam2_offset_s=10.0,
        runner_inicio_s=64.0,
        runner_fim_s=88.0,
        sha256_cam1=diag.HASHES_FP2B["cam1"],
        sha256_cam2=diag.HASHES_FP2B["cam2"],
        zones_file=tmp_path / "zonas.json",
        output=tmp_path / "saida.csv",
    )
    chamadas_sub = []
    run_original = diag.subprocess.run

    def run_fake(comando, **kwargs):
        chamadas_sub.append((list(comando), dict(kwargs)))
        modo = comando[comando.index("--internal-mode") + 1]
        interno = Path(comando[comando.index("--internal-output") + 1])
        interno.write_text(json.dumps({
            "modo": modo,
            "processo": {"pid": 1000 + diag.MODOS.index(modo)},
            "frame_final": {"frame_sha256": "mesmo-frame-78"},
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    try:
        diag.subprocess.run = run_fake
        resultados_sub = diag._executar_subprocessos(args_sub)
    finally:
        diag.subprocess.run = run_original

check("um filho é criado por modo, em ordem",
      [cmd[cmd.index("--internal-mode") + 1] for cmd, _ in chamadas_sub]
      == ["A", "B", "C", "D"])
check("todos os filhos usam Python, shell=False e cwd do repo",
      len(chamadas_sub) == 4
      and all(cmd[0] == sys.executable for cmd, _ in chamadas_sub)
      and all(kwargs["shell"] is False for _, kwargs in chamadas_sub)
      and all(kwargs["cwd"] == str(diag.RAIZ_REPO) for _, kwargs in chamadas_sub))
check("cada modo usa arquivo interno e PID próprios",
      len({cmd[cmd.index("--internal-output") + 1]
           for cmd, _ in chamadas_sub}) == 4
      and [item["processo"]["pid"] for item in resultados_sub]
      == [1000, 1001, 1002, 1003])
check("frame final auditado é idêntico entre processos",
      {item["frame_final"]["frame_sha256"] for item in resultados_sub}
      == {"mesmo-frame-78"})


print(f"\n{'=' * 68}\n  {ok} ok · {fail} falha(s)\n{'=' * 68}")
raise SystemExit(1 if fail else 0)
