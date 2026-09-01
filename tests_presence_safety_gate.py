"""C1/C2 - Presence Safety Gate.

Regressao comportamental do FP2/88. O teste usa o fluxo real da cam2 e da
confirmacao de presenca, substituindo somente video/YOLO por dubles. A C1 pode
vetar ``posto_vazio``; ela nunca cria pessoa, track, identidade ou atividade.

Rodar: python -X utf8 tests_presence_safety_gate.py
"""
from __future__ import annotations

from functools import partial
import os
from pathlib import Path
import sys
import types


RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
for nome in [
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(nome, types.ModuleType(nome))
sys.modules["dotenv"].load_dotenv = lambda *_a, **_k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *_a, **_k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object


class _Vec(list):
    def __sub__(self, outro):
        return _Vec(a - b for a, b in zip(self, outro))

    def __mul__(self, outro):
        return _Vec(a * b for a, b in zip(self, outro))

    def __ge__(self, outro):
        return _Vec(a >= outro for a in self)

    def astype(self, tipo):
        return _Vec(tipo(v) for v in self)

    def tolist(self):
        return list(self)


class _Matriz:
    def __init__(self, linhas):
        self.linhas = [list(linha) for linha in linhas]

    def __getitem__(self, chave):
        if isinstance(chave, tuple):
            _linhas, coluna = chave
            return _Vec(linha[coluna] for linha in self.linhas)
        return _Vec(self.linhas[chave])

    def __iter__(self):
        return iter(_Vec(linha) for linha in self.linhas)

    def __len__(self):
        return len(self.linhas)

    def tolist(self):
        return [list(linha) for linha in self.linhas]


class _Kpts(list):
    def astype(self, _tipo):
        return self


class _KptsLote(list):
    def __getitem__(self, chave):
        valor = super().__getitem__(chave)
        return _Kpts(valor) if isinstance(chave, int) else valor


class _Tensor:
    def __init__(self, valor):
        self.valor = valor

    def cpu(self):
        return self

    def numpy(self):
        return self.valor

    def tolist(self):
        if hasattr(self.valor, "tolist"):
            return self.valor.tolist()
        return list(self.valor)


class _Boxes:
    def __init__(self, caixas=(), confiancas=(), ids=None):
        self.xyxy = _Tensor(_Matriz(caixas))
        self.conf = _Tensor(_Vec(confiancas))
        self.id = _Tensor(_Vec(ids)) if ids is not None else None
        self._n = len(caixas)

    def __len__(self):
        return self._n


class _Resultado:
    def __init__(self, caixas=(), confiancas=(), ids=None, kpts=None):
        self.boxes = _Boxes(caixas, confiancas, ids)
        self.keypoints = (
            types.SimpleNamespace(xyn=_Tensor(_KptsLote(kpts)))
            if kpts is not None else None
        )


class _Frame:
    shape = (546, 510, 3)


np_fake = sys.modules["numpy"]
np_fake.ndarray = object
np_fake.int32 = int
np_fake.array = lambda valor, **_k: [list(item) for item in valor]


def _point_polygon_test(poligono, ponto, _medida):
    """Ray casting + distância ao segmento para exercitar a geometria real."""
    x, y = ponto
    dentro = False
    distancia = float("inf")
    for i in range(len(poligono)):
        x1, y1 = poligono[i]
        x2, y2 = poligono[(i + 1) % len(poligono)]
        dx = x2 - x1
        dy = y2 - y1
        comprimento2 = dx * dx + dy * dy
        if comprimento2:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / comprimento2))
            px = x1 + t * dx
            py = y1 + t * dy
        else:
            px, py = x1, y1
        distancia = min(distancia, ((x - px) ** 2 + (y - py) ** 2) ** 0.5)
        if (y1 > y) != (y2 > y):
            x_cruza = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_cruza:
                dentro = not dentro
    if not _medida:
        return 1.0 if dentro else -1.0
    if distancia == 0:
        return 0.0
    return distancia if dentro else -distancia


cv2_fake = sys.modules["cv2"]
cv2_fake.CAP_PROP_FRAME_COUNT = 1
cv2_fake.CAP_PROP_FPS = 2
cv2_fake.CAP_PROP_POS_MSEC = 3
cv2_fake.pointPolygonTest = _point_polygon_test

os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ["KV_CAM2_CONF"] = "0.35"

from backend import pipeline as pl  # noqa: E402


ok = fail = 0


def check(nome, condicao, extra=""):
    global ok, fail
    if condicao:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def _callback_normal(*_a, **_k):
    return None


def _tracker_inicio(*_a, **_k):
    return None


def _tracker_fim(*_a, **_k):
    return None


def _postprocess_temporal_nao_reconhecido(*_a, **_k):
    """Simula uma versão futura cujo callback temporal mudou de módulo/nome."""
    return None


# Iguais ao formato registrado pelo Ultralytics: partials cujo callable vem de
# ultralytics.trackers.track. O predict de seguranca precisa suspendê-los; se o
# postprocess do tracker continuar ativo, ele pode apagar a mesma box novamente.
_tracker_inicio.__module__ = "ultralytics.trackers.track"
_tracker_inicio.__name__ = "on_predict_start"
_tracker_fim.__module__ = "ultralytics.trackers.track"
_tracker_fim.__name__ = "on_predict_postprocess_end"


def _eh_callback_tracker(callback):
    alvo = getattr(callback, "func", callback)
    return str(getattr(alvo, "__module__", "")).startswith(
        "ultralytics.trackers"
    )


class _YoloFake:
    def __init__(self, resultado_track, resultado_predict=None, erro_predict=None):
        self.resultado_track = resultado_track
        self.resultado_predict = resultado_predict
        self.erro_predict = erro_predict
        self.track_calls = []
        self.predict_calls = []
        self.callbacks_durante_predict = []
        self.predictor = None
        self.callbacks = {
            "on_predict_start": [
                _callback_normal,
                partial(_tracker_inicio, persist=True),
            ],
            "on_predict_postprocess_end": [
                _callback_normal,
                partial(_tracker_fim, persist=True),
            ],
            "on_predict_end": [_callback_normal],
        }

    def track(self, _frame, **kwargs):
        self.track_calls.append(dict(kwargs))
        return [self.resultado_track]

    def predict(self, _frame, **kwargs):
        self.predict_calls.append(dict(kwargs))
        snapshot = {nome: list(cbs) for nome, cbs in self.callbacks.items()}
        self.callbacks_durante_predict.append(snapshot)
        if any(
            _eh_callback_tracker(cb)
            for nome in ("on_predict_start", "on_predict_postprocess_end")
            for cb in snapshot.get(nome, [])
        ):
            raise AssertionError("callback temporal do tracker ficou ativo")
        if self.erro_predict is not None:
            raise self.erro_predict
        return [self.resultado_predict]


class _YoloCallbackParcial(_YoloFake):
    """Start reconhecível, mas postprocess temporal propositalmente opaco."""
    def __init__(self):
        super().__init__(VAZIO, RESULTADO_FP2)
        self.callbacks["on_predict_postprocess_end"] = [
            _callback_normal,
            _postprocess_temporal_nao_reconhecido,
        ]
        # Prova que há tracker materializado: um callback desconhecido não pode
        # ser tratado como callback normal e liberar uma falsa independência.
        self.predictor = types.SimpleNamespace(
            trackers=[object()], callbacks=self.callbacks
        )

    def predict(self, _frame, **kwargs):
        self.predict_calls.append(dict(kwargs))
        snapshot = {nome: list(cbs) for nome, cbs in self.callbacks.items()}
        self.callbacks_durante_predict.append(snapshot)
        if _postprocess_temporal_nao_reconhecido in snapshot.get(
            "on_predict_postprocess_end", []
        ):
            raise RuntimeError("postprocess_temporal_ainda_ativo")
        return [self.resultado_predict]


class _CapFake:
    def __init__(self):
        self.seeks = []
        self.liberado = False

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2_fake.CAP_PROP_FRAME_COUNT:
            return 1000
        if prop == cv2_fake.CAP_PROP_FPS:
            return 10.0
        return 0.0

    def set(self, prop, valor):
        if prop == cv2_fake.CAP_PROP_POS_MSEC:
            self.seeks.append(float(valor))
        return True

    def read(self):
        return True, _Frame()

    def release(self):
        self.liberado = True


class _CapFechado:
    def isOpened(self):
        return False

    def release(self):
        return None


CAM2_W, CAM2_H = 510, 546
FP2_BBOX = (114.834, 30.540, 182.405, 230.846)
FP2_CONF = 0.818198681
FP2_ANCORA = (159.602, 71.098)
FP1_BBOX = (185.549, 57.798, 310.86, 216.365)
FP1_CONF = 0.435480863
FP1_ANCORA = (271.921, 93.321)
FP1_OMBROS = ((292.138, 96.720), (251.705, 89.922))
OUTRA_BBOX = (372.0, 83.0, 438.0, 244.0)
OUTRA_CONF = 0.634880841
POSTO_CAM2 = {
    "Posto do Torneiro": {
        "papel": "posto_operador",
        "pts_rel": [
            [0.1981, 0.0484], [0.5412, 0.0783],
            [0.2358, 0.7978], [0.0048, 0.6663],
        ],
        "descricao_contexto": "posto do operador",
    }
}


def _kpts_na_ancora(x, y):
    pontos = [[0.0, 0.0] for _ in range(17)]
    pontos[5] = [(x - 10.0) / CAM2_W, y / CAM2_H]
    pontos[6] = [(x + 10.0) / CAM2_W, y / CAM2_H]
    return pontos


def _kpts_com_ombros(esquerdo, direito, extras=None):
    pontos = [[0.0, 0.0] for _ in range(17)]
    pontos[5] = [esquerdo[0] / CAM2_W, esquerdo[1] / CAM2_H]
    pontos[6] = [direito[0] / CAM2_W, direito[1] / CAM2_H]
    for indice, ponto in (extras or {}).items():
        pontos[indice] = [ponto[0] / CAM2_W, ponto[1] / CAM2_H]
    return pontos


KPTS_FP2 = _kpts_na_ancora(*FP2_ANCORA)
KPTS_FP1 = _kpts_com_ombros(*FP1_OMBROS)
RESULTADO_FP2 = _Resultado([FP2_BBOX], [FP2_CONF], ids=None, kpts=[KPTS_FP2])
RESULTADO_FP1 = _Resultado([FP1_BBOX], [FP1_CONF], ids=None, kpts=[KPTS_FP1])
RESULTADO_FORA = _Resultado([OUTRA_BBOX], [OUTRA_CONF], ids=None)
TRACK_FORA = _Resultado([OUTRA_BBOX], [OUTRA_CONF], ids=[8])
TRACK_DENTRO = _Resultado([FP2_BBOX], [FP2_CONF], ids=[1], kpts=[KPTS_FP2])
VAZIO = _Resultado()


def _callbacks_iguais(antes, depois):
    return (
        list(antes) == list(depois)
        and all(antes[nome] == depois[nome] for nome in antes)
    )


def _bbox_proxima(valor, esperado):
    try:
        return len(valor) == 4 and all(
            abs(float(a) - float(b)) < 1e-3 for a, b in zip(valor, esperado)
        )
    except (TypeError, ValueError):
        return False


def _amostra_vazia():
    return pl.Amostra(
        frame_idx=880, tempo_s=88.0, img_b64="", pessoas=[], dim=(806, 304)
    )


def _rodar_cam2(
    resultado_track, resultado_predict=None, erro_predict=None, *, amostra=None,
    cap=None,
):
    yolo = _YoloFake(resultado_track, resultado_predict, erro_predict)
    cap = cap if cap is not None else _CapFake()
    am = amostra if amostra is not None else _amostra_vazia()
    antigos = {
        "frame_para_base64": pl.frame_para_base64,
        "acumular_descritor": pl.acumular_descritor,
        "_ZONA_ESTRITA": pl._ZONA_ESTRITA,
        "_OPERADOR_FILTRO_ENABLE": pl._OPERADOR_FILTRO_ENABLE,
        "_CAM2_CONFIRM_STRIDE": pl._CAM2_CONFIRM_STRIDE,
    }
    video_capture_antigo = getattr(pl.cv2, "VideoCapture", None)
    try:
        pl.frame_para_base64 = lambda *_a, **_k: "IMG-CAM2"
        pl.acumular_descritor = lambda *_a, **_k: None
        pl._ZONA_ESTRITA = True
        pl._OPERADOR_FILTRO_ENABLE = True
        pl._CAM2_CONFIRM_STRIDE = 1
        pl.cv2.VideoCapture = lambda _p: cap
        pl._anexar_segundo_angulo(
            [am], "cam2.mp4", yolo=yolo, rois_sec=POSTO_CAM2,
            offset_s=-10.0, desc_acc={}, cam_id="cam2",
        )
    finally:
        for nome, valor in antigos.items():
            setattr(pl, nome, valor)
        if video_capture_antigo is None:
            delattr(pl.cv2, "VideoCapture")
        else:
            pl.cv2.VideoCapture = video_capture_antigo
    return am, yolo, cap


def _confirmar_e_analisar(
    am, *, estruturada, fora_modo="off", analisador_fora=None
):
    antigos = {
        "PRODUTIVIDADE_OPERADOR_ESTRUTURADA": (
            pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA
        ),
        "_POSTO_VAZIO_ENABLE": pl._POSTO_VAZIO_ENABLE,
        "_FORA_MODO": pl._FORA_MODO,
        "_analisar_sequencia_vlm": pl._analisar_sequencia_vlm,
        "_analisar_sequencia_cam2": pl._analisar_sequencia_cam2,
        "_analisar_sequencia_fora": pl._analisar_sequencia_fora,
    }

    def _vlm_proibido(*_a, **_k):
        raise AssertionError("C1 nao pode classificar identidade/atividade")

    try:
        pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA = estruturada
        pl._POSTO_VAZIO_ENABLE = True
        pl._FORA_MODO = fora_modo
        pl._analisar_sequencia_vlm = _vlm_proibido
        pl._analisar_sequencia_cam2 = _vlm_proibido
        if analisador_fora is not None:
            pl._analisar_sequencia_fora = analisador_fora
        stats = pl.etapa_confirmar_operador([am], "dupla")
        observacoes = pl.etapa_analise_vlm(
            object(), [am], "torneamento", {}, lambda *_a, **_k: None,
            zona_posto="posto", intervalo_s=5.0,
        )
        return stats, observacoes
    finally:
        for nome, valor in antigos.items():
            setattr(pl, nome, valor)


def _analisar_sem_reconfirmar(am, *, estruturada):
    """Exercita o plano VLM preservando o veredito temporal já calculado."""
    antigos = {
        "PRODUTIVIDADE_OPERADOR_ESTRUTURADA": (
            pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA
        ),
        "_POSTO_VAZIO_ENABLE": pl._POSTO_VAZIO_ENABLE,
        "_FORA_MODO": pl._FORA_MODO,
        "_analisar_sequencia_vlm": pl._analisar_sequencia_vlm,
        "_analisar_sequencia_cam2": pl._analisar_sequencia_cam2,
    }

    def _vlm_proibido(*_a, **_k):
        raise AssertionError("ponte nao pode classificar o slot C1")

    try:
        pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA = estruturada
        pl._POSTO_VAZIO_ENABLE = True
        pl._FORA_MODO = "off"
        pl._analisar_sequencia_vlm = _vlm_proibido
        pl._analisar_sequencia_cam2 = _vlm_proibido
        try:
            return pl.etapa_analise_vlm(
                object(), [am], "torneamento", {}, lambda *_a, **_k: None,
                zona_posto="posto", intervalo_s=5.0,
            )
        except Exception as exc:  # a asserção abaixo reporta sem abortar a suíte
            return exc
    finally:
        for nome, valor in antigos.items():
            setattr(pl, nome, valor)


def _resultado_geometrico(ombro_esq, ombro_dir, *, extras=None,
                          bbox=FP1_BBOX, confidence=FP1_CONF):
    return _Resultado(
        [bbox], [confidence], ids=None,
        kpts=[_kpts_com_ombros(ombro_esq, ombro_dir, extras=extras)],
    )


def _rodar_gate_geometrico(resultado, *, boundary_safety=True):
    yolo = _YoloFake(VAZIO, resultado)
    return pl._presenca_safety_gate(
        yolo, _Frame(), rois_reais, CAM2_W, CAM2_H,
        conf_min=pl._CAM2_CONF, imgsz=416,
        boundary_safety=boundary_safety,
    )


print("\n[1] Detector independente e callbacks do tracker")
rois_reais = pl._build_rois(POSTO_CAM2, CAM2_W, CAM2_H)
ancora = pl._ponto_ancora(
    {"bbox": FP2_BBOX, "kpts": KPTS_FP2}, CAM2_W, CAM2_H
)
check("FP2/88 reproduz a ancora exata",
      abs(ancora[0] - FP2_ANCORA[0]) < 1e-6
      and abs(ancora[1] - FP2_ANCORA[1]) < 1e-6, ancora)
check("a ancora FP2 esta dentro da zona real da cam2",
      any(pl._ponto_em_roi(*ancora, roi["polygon"])
          for roi in rois_reais.values()))

y_direto = _YoloFake(VAZIO, RESULTADO_FP2)
callbacks_antes = {nome: list(cbs) for nome, cbs in y_direto.callbacks.items()}
r_direto = pl._presenca_safety_gate(
    y_direto, _Frame(), rois_reais, CAM2_W, CAM2_H,
    conf_min=pl._CAM2_CONF, imgsz=416,
)
check("predict sem track_id pode vetar vazio", r_direto["status"] == "veto",
      r_direto)
check("usa somente o threshold/imgsz atuais da cam2",
      len(y_direto.predict_calls) == 1
      and y_direto.predict_calls[0].get("classes") == [0]
      and y_direto.predict_calls[0].get("conf") == 0.35
      and y_direto.predict_calls[0].get("imgsz") == 416,
      y_direto.predict_calls)
check("callbacks temporais ficam suspensos durante o predict independente",
      y_direto.callbacks_durante_predict
      and not any(
          _eh_callback_tracker(cb)
          for nome in ("on_predict_start", "on_predict_postprocess_end")
          for cb in y_direto.callbacks_durante_predict[0].get(nome, [])
      ), y_direto.callbacks_durante_predict)
check("callbacks nao relacionados continuam ativos durante o predict",
      all(
          _callback_normal in y_direto.callbacks_durante_predict[0].get(nome, [])
          for nome in ("on_predict_start", "on_predict_postprocess_end")
      ))
check("todos os callbacks sao restaurados na ordem original",
      _callbacks_iguais(callbacks_antes, y_direto.callbacks),
      y_direto.callbacks)


print("\n[2] Caso normal: tracker encontra dentro")
am_normal, y_normal, cap_normal = _rodar_cam2(
    TRACK_DENTRO, erro_predict=AssertionError("predict nao deveria rodar")
)
check("C1 nao roda quando o tracker ja encontrou pessoa dentro",
      len(y_normal.track_calls) == 1 and y_normal.predict_calls == [])
check("o veredito normal da cam2 permanece intacto",
      am_normal.op_cam2 is True and am_normal.n_posto_cam2 == 1
      and not am_normal.presenca_safety_gate, am_normal)


print("\n[3] FP2/88: tracker perde, detector forte dentro")
am_fp2, y_fp2, cap_fp2 = _rodar_cam2(TRACK_FORA, RESULTADO_FP2)
check("usa o instante fisico cam1 88s <-> cam2 78s",
      cap_fp2.seeks == [78000.0] and cap_fp2.liberado, cap_fp2.seeks)
check("o caminho critico custa um track e um predict condicional",
      len(y_fp2.track_calls) == 1 and len(y_fp2.predict_calls) == 1,
      (y_fp2.track_calls, y_fp2.predict_calls))
check("o track continua dizendo zero dentro; C1 apenas veta vazio",
      am_fp2.op_cam2 is False and am_fp2.n_posto_cam2 == 0
      and am_fp2.presenca_safety_gate, am_fp2)
check("telemetria guarda camera, confidence e bbox exatas",
      am_fp2.presenca_safety_camera == "cam2"
      and am_fp2.presenca_safety_confidence is not None
      and abs(am_fp2.presenca_safety_confidence - FP2_CONF) < 1e-9
      and _bbox_proxima(am_fp2.presenca_safety_bbox, FP2_BBOX), am_fp2)
check("fallback nao cria pessoa, track, bbox funcional, maos ou identidade",
      am_fp2.pessoas == [] and am_fp2.fora_posto == []
      and am_fp2.bbox_cam2 is None and am_fp2.maos_cam2 is False
      and am_fp2.identidade_autoritativa is False
      and am_fp2.identidade_track_id is None, am_fp2)

# A identidade 111D pode discordar, mas C1 nao a altera nem permite que ela
# reabra a conclusao critica de vazio.
am_fp2.identidade_autoritativa = True
am_fp2.identidade_estado = "ausente"
am_fp2.identidade_track_id = 77
stats_fp2, obs_fp2 = _confirmar_e_analisar(am_fp2, estruturada=True)
check("veto tem prioridade sobre posto_vazio inclusive com 111D ausente",
      am_fp2.operador_presente is None
      and stats_fp2["vazios"] == 0
      and len(obs_fp2) == 1
      and obs_fp2[0].get("papel") is None
      and obs_fp2[0].get("track_id") == pl.POSTO_INCONCLUSIVO_TID
      and obs_fp2[0].get("trabalho") is None
      and not any(o.get("papel") == "posto_vazio" for o in obs_fp2),
      (stats_fp2, obs_fp2))
check("C1 nao reescreve os campos da identidade logica",
      am_fp2.identidade_autoritativa is True
      and am_fp2.identidade_estado == "ausente"
      and am_fp2.identidade_track_id == 77, am_fp2)

# O kill switch da identidade estruturada nao pode desligar uma trava de
# seguranca de presenca.
am_legado = _amostra_vazia()
am_legado.op_cam2 = False
pl._marcar_presenca_safety(am_legado, r_direto, "cam2")
stats_legado, obs_legado = _confirmar_e_analisar(am_legado, estruturada=False)
check("veto independe da Fase 111/V9",
      am_legado.operador_presente is None and stats_legado["vazios"] == 0
      and len(obs_legado) == 1 and obs_legado[0].get("papel") is None
      and not any(o.get("papel") == "posto_vazio" for o in obs_legado),
      (stats_legado, obs_legado))


print("\n[4] Detector encontra somente pessoa fora")
am_fora, y_fora, _cap = _rodar_cam2(VAZIO, RESULTADO_FORA)
stats_fora, obs_fora = _confirmar_e_analisar(am_fora, estruturada=True)
check("pessoa fora nao veta nem cria presenca falsa",
      not am_fora.presenca_safety_gate
      and am_fora.operador_presente is False
      and stats_fora["vazios"] == 1
      and len(obs_fora) == 1 and obs_fora[0].get("papel") == "posto_vazio",
      (am_fora, stats_fora, obs_fora))


print("\n[5] Detector independente nao encontra ninguem")
am_vazio, y_vazio, _cap = _rodar_cam2(VAZIO, VAZIO)
stats_vazio, obs_vazio = _confirmar_e_analisar(am_vazio, estruturada=True)
check("resultado vazio nao veta; ausencia normal continua",
      len(y_vazio.predict_calls) == 1
      and not am_vazio.presenca_safety_gate
      and am_vazio.operador_presente is False
      and stats_vazio["vazios"] == 1
      and len(obs_vazio) == 1 and obs_vazio[0].get("papel") == "posto_vazio",
      (am_vazio, stats_vazio, obs_vazio))


print("\n[6] Falha tecnica e fail-safe auditavel")
avisos = []
warning_original = pl.log.warning


def _capturar_warning(mensagem, *args, **_kwargs):
    try:
        avisos.append(str(mensagem) % args if args else str(mensagem))
    except Exception:
        avisos.append(f"{mensagem} {args}")


try:
    pl.log.warning = _capturar_warning
    am_erro, y_erro, _cap = _rodar_cam2(
        VAZIO, erro_predict=RuntimeError("detector indisponivel")
    )
finally:
    pl.log.warning = warning_original

stats_erro, obs_erro = _confirmar_e_analisar(am_erro, estruturada=True)
check("erro do safety nao propaga e veta vazio de forma conservadora",
      am_erro.presenca_safety_gate
      and am_erro.operador_presente is None
      and stats_erro["vazios"] == 0
      and len(obs_erro) == 1 and obs_erro[0].get("papel") is None
      and not any(o.get("papel") == "posto_vazio" for o in obs_erro),
      (am_erro, stats_erro, obs_erro))
check("erro fica explicito na telemetria e no warning",
      am_erro.presenca_safety_motivo
      and any("safety" in aviso.lower() and "erro" in aviso.lower()
              for aviso in avisos),
      (am_erro.presenca_safety_motivo, avisos))
check("callbacks tambem sao restaurados quando predict falha",
      len(y_erro.predict_calls) == 1
      and any(_eh_callback_tracker(cb)
              for cb in y_erro.callbacks["on_predict_postprocess_end"]),
      y_erro.callbacks)
check("falha nao fabrica identidade, track ou comportamento",
      am_erro.pessoas == [] and am_erro.fora_posto == []
      and am_erro.identidade_track_id is None
      and obs_erro[0].get("trabalho") is None
      and obs_erro[0].get("track_id") == pl.POSTO_INCONCLUSIVO_TID,
      (am_erro, obs_erro))


print("\n[7] Cam2 indisponivel com cam1 vazia")
am_sem_cam2 = _amostra_vazia()
y_sem_cam2 = _YoloFake(VAZIO, VAZIO)
video_capture_antigo = getattr(pl.cv2, "VideoCapture", None)
filtro_antigo = pl._OPERADOR_FILTRO_ENABLE
try:
    pl._OPERADOR_FILTRO_ENABLE = True
    pl.cv2.VideoCapture = lambda _p: _CapFechado()
    n_sem_cam2 = pl._anexar_segundo_angulo(
        [am_sem_cam2], "cam2-inexistente.mp4", yolo=y_sem_cam2,
        rois_sec=POSTO_CAM2, offset_s=-10.0, desc_acc={}, cam_id="cam2",
    )
finally:
    pl._OPERADOR_FILTRO_ENABLE = filtro_antigo
    if video_capture_antigo is None:
        delattr(pl.cv2, "VideoCapture")
    else:
        pl.cv2.VideoCapture = video_capture_antigo

stats_sem_cam2, obs_sem_cam2 = _confirmar_e_analisar(
    am_sem_cam2, estruturada=True
)
check("cam2 que nao abre vira erro safety, nao ausencia medida",
      n_sem_cam2 == 0
      and y_sem_cam2.track_calls == [] and y_sem_cam2.predict_calls == []
      and am_sem_cam2.presenca_safety_gate
      and am_sem_cam2.presenca_safety_motivo == "falha_presence_safety_gate",
      am_sem_cam2)
check("indisponibilidade total termina inconclusiva, nunca posto_vazio",
      am_sem_cam2.operador_presente is None
      and stats_sem_cam2["vazios"] == 0
      and len(obs_sem_cam2) == 1 and obs_sem_cam2[0].get("papel") is None
      and not any(o.get("papel") == "posto_vazio" for o in obs_sem_cam2),
      (stats_sem_cam2, obs_sem_cam2))


print("\n[8] Fora do posto exclui o safety gate")
am_fora_sem_cam2 = _amostra_vazia()
am_fora_sem_cam2.fora_posto = [{
    "track_id": 77,
    "bbox": (10, 10, 100, 300),
    "_fora_motivo": "operador",
    "_fora_amostras_zona": 5,
}]
am_fora_sem_cam2, y_fora_sem_cam2, _cap = _rodar_cam2(
    VAZIO, VAZIO, amostra=am_fora_sem_cam2, cap=_CapFechado()
)
analisador_fora = lambda *_a, **_k: {
    0: {"acao": "conversando_colega", "resumo": "fora do posto"}
}
stats_fora_sem_cam2, obs_fora_sem_cam2 = _confirmar_e_analisar(
    am_fora_sem_cam2,
    estruturada=False,
    fora_modo="on",
    analisador_fora=analisador_fora,
)
check("fora_posto valido nao recebe safety quando cam2 nao abre",
      am_fora_sem_cam2.fora_posto
      and not am_fora_sem_cam2.presenca_safety_gate
      and y_fora_sem_cam2.predict_calls == [],
      (am_fora_sem_cam2, y_fora_sem_cam2.predict_calls))
check("fora_posto permanece fora e nao vira inconclusivo",
      am_fora_sem_cam2.operador_presente is False
      and stats_fora_sem_cam2["vazios"] == 1
      and len(obs_fora_sem_cam2) == 1
      and obs_fora_sem_cam2[0].get("papel") == pl.PAPEL_OPERADOR_FORA
      and obs_fora_sem_cam2[0].get("track_id") == 77,
      (stats_fora_sem_cam2, obs_fora_sem_cam2))

am_fora_com_c1 = _amostra_vazia()
am_fora_com_c1.fora_posto = [{
    "track_id": 78,
    "bbox": (10, 10, 100, 300),
    "_fora_motivo": "operador",
    "_fora_amostras_zona": 5,
}]
am_fora_com_c1, y_fora_com_c1, _cap = _rodar_cam2(
    TRACK_FORA, RESULTADO_FP2, amostra=am_fora_com_c1
)
stats_fora_com_c1, obs_fora_com_c1 = _confirmar_e_analisar(
    am_fora_com_c1,
    estruturada=False,
    fora_modo="on",
    analisador_fora=analisador_fora,
)
check("fora_posto valido impede o safety mesmo com detector forte dentro",
      am_fora_com_c1.fora_posto
      and not am_fora_com_c1.presenca_safety_gate
      and y_fora_com_c1.predict_calls == [],
      (am_fora_com_c1, y_fora_com_c1.predict_calls))
check("caminho fora_posto permanece intacto diante do cenário C1",
      am_fora_com_c1.operador_presente is False
      and stats_fora_com_c1["vazios"] == 1
      and len(obs_fora_com_c1) == 1
      and obs_fora_com_c1[0].get("papel") == pl.PAPEL_OPERADOR_FORA
      and obs_fora_com_c1[0].get("track_id") == 78,
      (stats_fora_com_c1, obs_fora_com_c1))


print("\n[9] Ponte temporal nao promove um slot vetado pela C1")
antes = pl.Amostra(
    frame_idx=0, tempo_s=0.0, img_b64="IMG", dim=(640, 480),
    pessoas=[{"track_id": 1, "papel": "operador"}],
)
meio = pl.Amostra(
    frame_idx=50, tempo_s=5.0, img_b64="", dim=(640, 480), pessoas=[],
)
depois = pl.Amostra(
    frame_idx=100, tempo_s=10.0, img_b64="IMG", dim=(640, 480),
    pessoas=[{"track_id": 1, "papel": "operador"}],
)
meio.op_cam2 = False
pl._marcar_presenca_safety(meio, r_direto, "cam2")
estruturada_antiga = pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA
gap_antigo = pl._OPERADOR_GAP_SLOTS
try:
    pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA = False
    pl._OPERADOR_GAP_SLOTS = 3
    stats_ponte = pl.etapa_confirmar_operador([antes, meio, depois], "dupla")
finally:
    pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA = estruturada_antiga
    pl._OPERADOR_GAP_SLOTS = gap_antigo

obs_meio = _analisar_sem_reconfirmar(meio, estruturada=False)
check("presencas vizinhas nao transformam abstenção C1 em presença",
      antes.operador_presente is True and depois.operador_presente is True
      and meio.operador_presente is None and meio.operador_ponte is False
      and stats_ponte["pontes"] == 0,
      (stats_ponte, antes, meio, depois))
check("slot C1 segue sem VLM, identidade ou atividade apos a ponte",
      isinstance(obs_meio, list) and len(obs_meio) == 1
      and obs_meio[0].get("papel") is None
      and obs_meio[0].get("trabalho") is None
      and obs_meio[0].get("track_id") == pl.POSTO_INCONCLUSIVO_TID,
      obs_meio)


print("\n[10] Callback temporal parcial falha fechado")
y_parcial = _YoloCallbackParcial()
callbacks_parciais_antes = {
    nome: list(cbs) for nome, cbs in y_parcial.callbacks.items()
}
try:
    pl._predict_sem_tracker(
        y_parcial, _Frame(), classes=[0], conf=0.35, imgsz=416,
        verbose=False, save=False,
    )
    erro_parcial = None
except Exception as exc:  # esperado: o callback opaco impede detector "bruto"
    erro_parcial = exc
check("postprocess temporal nao reconhecido impede falsa independencia",
      isinstance(erro_parcial, RuntimeError)
      and "callbacks_do_tracker_incompletos:on_predict_postprocess_end"
      in str(erro_parcial)
      and y_parcial.predict_calls == [],
      erro_parcial)
check("callbacks parciais sao restaurados byte-a-byte apos a falha",
      _callbacks_iguais(callbacks_parciais_antes, y_parcial.callbacks),
      y_parcial.callbacks)

y_parcial_gate = _YoloCallbackParcial()
r_parcial_gate = pl._presenca_safety_gate(
    y_parcial_gate, _Frame(), rois_reais, CAM2_W, CAM2_H,
    conf_min=0.35, imgsz=416,
)
check("gate converte callback parcial em erro fail-safe, nunca livre",
      r_parcial_gate.get("status") == "erro"
      and "callbacks_do_tracker_incompletos:on_predict_postprocess_end"
      in r_parcial_gate.get("erro", "")
      and y_parcial_gate.predict_calls == [],
      r_parcial_gate)


print("\n[11] C2 Boundary Safety exclusiva da cam2")
r_fp1 = _rodar_gate_geometrico(RESULTADO_FP1)
check("FP1: ancora ~15 px fora e um ombro dentro gera veto C2",
      r_fp1.get("status") == "veto"
      and r_fp1.get("motivo") == "veto_posto_vazio_por_limite_geometrico"
      and -25.5 <= r_fp1.get("distancia_borda_px", 0) < 0
      and r_fp1.get("ombros_dentro") == (False, True),
      r_fp1)

r_ancora_dentro = _rodar_gate_geometrico(RESULTADO_FP2)
check("ancora dentro preserva o veto normal da C1",
      r_ancora_dentro.get("status") == "veto"
      and r_ancora_dentro.get("motivo")
      == "veto_posto_vazio_por_deteccao_independente",
      r_ancora_dentro)

r_longe = _rodar_gate_geometrico(
    _resultado_geometrico((250.0, 100.0), (350.0, 100.0))
)
check("ancora fora além da margem nao veta",
      r_longe.get("status") == "livre", r_longe)

r_ombros_fora = _rodar_gate_geometrico(
    _resultado_geometrico((292.0, 96.0), (292.0, 96.0))
)
check("ancora perto da borda com ambos ombros fora nao veta",
      r_ombros_fora.get("status") == "livre", r_ombros_fora)

r_punho_dentro = _rodar_gate_geometrico(
    _resultado_geometrico(
        (292.0, 96.0), (292.0, 96.0), extras={9: (150.0, 200.0)}
    )
)
check("somente braço/punho dentro nao veta",
      r_punho_dentro.get("status") == "livre", r_punho_dentro)

r_bbox_invade = _rodar_gate_geometrico(
    _resultado_geometrico(
        (300.0, 100.0), (300.0, 100.0),
        bbox=(100.0, 20.0, 300.0, 250.0),
    )
)
check("bbox invade ROI mas ombros fora nao veta",
      r_bbox_invade.get("status") == "livre", r_bbox_invade)

kpts_um_ombro = _kpts_com_ombros((292.0, 96.0), (0.0, 0.0))
r_um_ombro = _rodar_gate_geometrico(
    _Resultado([FP1_BBOX], [FP1_CONF], ids=None, kpts=[kpts_um_ombro])
)
check("apenas um ombro valido nao veta",
      r_um_ombro.get("status") == "livre", r_um_ombro)

am_fp1, y_fp1, _cap_fp1 = _rodar_cam2(TRACK_FORA, RESULTADO_FP1)
stats_fp1, obs_fp1 = _confirmar_e_analisar(am_fp1, estruturada=True)
check("C2 integrado mantem inconclusivo e zero posto_vazio",
      am_fp1.presenca_safety_gate
      and am_fp1.presenca_safety_motivo
      == "veto_posto_vazio_por_limite_geometrico"
      and am_fp1.operador_presente is None
      and stats_fp1["vazios"] == 0
      and len(obs_fp1) == 1
      and obs_fp1[0].get("papel") is None
      and not any(o.get("papel") == "posto_vazio" for o in obs_fp1),
      (am_fp1, stats_fp1, obs_fp1))

r_cam1 = _rodar_gate_geometrico(RESULTADO_FP1, boundary_safety=False)
check("CAM1 permanece sem Boundary Safety",
      r_cam1.get("status") == "livre"
      and r_cam1.get("motivo") == "sem_ancora_forte_no_posto",
      r_cam1)


print("\n[12] C3 — captura low-confidence somente pela âncora da CAM1")
C3_BBOX = (150.0, 100.0, 210.0, 300.0)
C3_FORA_BBOX = (320.0, 100.0, 400.0, 300.0)


def _resultado_c3(confidence, bbox=C3_BBOX, kpts=None):
    return _Resultado(
        [bbox], [confidence], ids=None,
        kpts=[kpts] if kpts is not None else None,
    )


def _rodar_probe_c3(resultado):
    yolo = _YoloFake(VAZIO, resultado)
    retorno = pl._presenca_safety_gate(
        yolo, _Frame(), rois_reais, CAM2_W, CAM2_H,
        conf_min=pl._OPERADOR_CONF, imgsz=416,
        capturar_c3=True,
    )
    return retorno, yolo


r_c3_fraco, y_c3_fraco = _rodar_probe_c3(_resultado_c3(0.088118829))
am_c3_fraco = pl.Amostra(
    frame_idx=720, tempo_s=72.0, img_b64="", pessoas=[], dim=(CAM2_W, CAM2_H)
)
pl._guardar_candidato_c3(am_c3_fraco, r_c3_fraco)
check("C3 usa um único predict sem tracker com conf=0.08 e imgsz atual",
      len(y_c3_fraco.predict_calls) == 1
      and y_c3_fraco.predict_calls[0].get("conf") == 0.08
      and y_c3_fraco.predict_calls[0].get("imgsz") == 416,
      y_c3_fraco.predict_calls)
check("0.088 dentro vira somente telemetria transitória C3",
      r_c3_fraco.get("status") == "livre"
      and am_c3_fraco.presenca_c3_confidence == 0.088118829
      and _bbox_proxima(am_c3_fraco.presenca_c3_bbox, C3_BBOX)
      and am_c3_fraco.presenca_c3_ancora is not None
      and not am_c3_fraco.pessoas,
      (r_c3_fraco, am_c3_fraco))

r_c3_varias, _y_c3_varias = _rodar_probe_c3(_Resultado(
    [C3_BBOX, (160.0, 110.0, 230.0, 310.0)],
    [0.088, 0.189], ids=None,
))
check("várias detecções válidas escolhem a maior confidence",
      r_c3_varias.get("c3_candidate", {}).get("confidence") == 0.189,
      r_c3_varias)

r_c3_alto, _y_c3_alto = _rodar_probe_c3(_resultado_c3(0.30))
check("confidence >= 0.30 não é candidato C3",
      "c3_candidate" not in r_c3_alto
      and r_c3_alto.get("motivo") == "veto_posto_vazio_por_deteccao_independente",
      r_c3_alto)
r_c3_baixo, _y_c3_baixo = _rodar_probe_c3(_resultado_c3(0.079999))
check("confidence < 0.08 não é candidato C3",
      "c3_candidate" not in r_c3_baixo
      and r_c3_baixo.get("status") == "livre",
      r_c3_baixo)
r_c3_fora, _y_c3_fora = _rodar_probe_c3(_resultado_c3(0.15, C3_FORA_BBOX))
check("âncora fora do posto não é candidato C3",
      "c3_candidate" not in r_c3_fora,
      r_c3_fora)
r_c3_braco, _y_c3_braco = _rodar_probe_c3(_resultado_c3(
    0.15, C3_FORA_BBOX,
    _kpts_com_ombros((350.0, 160.0), (350.0, 160.0), extras={9: (160.0, 200.0)}),
))
check("braço/punho dentro com âncora fora não é candidato C3",
      "c3_candidate" not in r_c3_braco,
      r_c3_braco)
r_c3_bbox, _y_c3_bbox = _rodar_probe_c3(
    _resultado_c3(0.15, (240.0, 100.0, 400.0, 300.0))
)
check("bbox que invade o posto com âncora fora não é candidato C3",
      "c3_candidate" not in r_c3_bbox,
      r_c3_bbox)


def _amostra_c3(confidence):
    am = pl.Amostra(
        frame_idx=0, tempo_s=0.0, img_b64="", pessoas=[], dim=(CAM2_W, CAM2_H)
    )
    am.presenca_c3_confidence = confidence
    am.presenca_c3_bbox = C3_BBOX
    am.presenca_c3_ancora = (180.0, 160.0)
    return am


def _amostra_forte(tempo_s=0.0):
    return pl.Amostra(
        frame_idx=int(tempo_s * 10), tempo_s=tempo_s, img_b64="IMG",
        pessoas=[{"track_id": 1, "papel": "operador"}], dim=(CAM2_W, CAM2_H),
    )


def _confirmar_c3(amostras):
    estruturada_antiga = pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA
    gap_antigo = pl._OPERADOR_GAP_SLOTS
    try:
        pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA = False
        pl._OPERADOR_GAP_SLOTS = 3
        return pl.etapa_confirmar_operador(amostras, "dupla")
    finally:
        pl.PRODUTIVIDADE_OPERADOR_ESTRUTURADA = estruturada_antiga
        pl._OPERADOR_GAP_SLOTS = gap_antigo


print("\n[13] C3 — regra temporal exata e isolamento da presença física")
am_72 = [_amostra_forte(64.0), _amostra_c3(0.088118829), _amostra_forte(80.0)]
stats_72 = _confirmar_c3(am_72)
check("72s: 0.088 + dois vizinhos fortes gera veto C3 fraco",
      am_72[1].presenca_safety_gate
      and am_72[1].presenca_safety_motivo
      == "veto_posto_vazio_por_confianca_temporal"
      and am_72[1].operador_presente is None
      and not am_72[1].operador_ponte
      and stats_72["c3_vetos"] == 1,
      (stats_72, am_72[1]))
check("C3 fraco registra confidence e câmera CAM1",
      am_72[1].presenca_safety_confidence == 0.088118829
      and am_72[1].presenca_safety_camera == "cam1",
      am_72[1])

am_fraco_um = [_amostra_c3(0.088), _amostra_forte(80.0)]
stats_fraco_um = _confirmar_c3(am_fraco_um)
check("0.088 com somente um vizinho forte não veta",
      not am_fraco_um[0].presenca_safety_gate
      and am_fraco_um[0].operador_presente is False
      and stats_fraco_um["c3_vetos"] == 0,
      (stats_fraco_um, am_fraco_um))
am_fraco_zero = [_amostra_c3(0.088)]
stats_fraco_zero = _confirmar_c3(am_fraco_zero)
check("0.088 sem vizinhos não veta",
      not am_fraco_zero[0].presenca_safety_gate
      and am_fraco_zero[0].operador_presente is False
      and stats_fraco_zero["c3_vetos"] == 0,
      (stats_fraco_zero, am_fraco_zero))

am_112 = [_amostra_c3(0.289957821), _amostra_forte(120.0)]
stats_112 = _confirmar_c3(am_112)
check("112s: 0.289 + um vizinho forte gera veto C3 moderado",
      am_112[0].presenca_safety_gate
      and am_112[0].presenca_safety_motivo
      == "veto_posto_vazio_por_confianca_temporal"
      and am_112[0].operador_presente is None
      and stats_112["c3_vetos"] == 1,
      (stats_112, am_112[0]))
am_moderado_zero = [_amostra_c3(0.289)]
stats_moderado_zero = _confirmar_c3(am_moderado_zero)
check("0.289 sem vizinho forte não veta",
      not am_moderado_zero[0].presenca_safety_gate
      and am_moderado_zero[0].operador_presente is False
      and stats_moderado_zero["c3_vetos"] == 0,
      (stats_moderado_zero, am_moderado_zero))

am_cam2_forte = [_amostra_c3(0.088), _amostra_forte(80.0)]
am_cam2_forte[0].op_cam2 = True
stats_cam2_forte = _confirmar_c3(am_cam2_forte)
check("slot já presente pela CAM2 não recebe veto C3",
      not am_cam2_forte[0].presenca_safety_gate
      and am_cam2_forte[0].operador_presente is True
      and stats_cam2_forte["c3_vetos"] == 0,
      (stats_cam2_forte, am_cam2_forte[0]))

am_cam1_normal = [_amostra_c3(0.088), _amostra_forte(80.0)]
am_cam1_normal[0].pessoas = [{"track_id": 7, "papel": "operador"}]
stats_cam1_normal = _confirmar_c3(am_cam1_normal)
check("slot já presente pela CAM1 normal não recebe veto C3",
      not am_cam1_normal[0].presenca_safety_gate
      and am_cam1_normal[0].operador_presente is True
      and stats_cam1_normal["c3_vetos"] == 0,
      (stats_cam1_normal, am_cam1_normal[0]))

am_c3_c3 = [_amostra_c3(0.088), _amostra_c3(0.15), _amostra_c3(0.289)]
stats_c3_c3 = _confirmar_c3(am_c3_c3)
check("candidatos C3 nunca validam outros candidatos C3",
      stats_c3_c3["c3_vetos"] == 0
      and all(not am.presenca_safety_gate for am in am_c3_c3),
      (stats_c3_c3, am_c3_c3))

am_ponte_c3 = [_amostra_forte(64.0), _amostra_c3(0.088), _amostra_forte(80.0)]
stats_ponte_c3 = _confirmar_c3(am_ponte_c3)
check("ponte temporal não promove slot vetado pela C3",
      am_ponte_c3[1].operador_presente is None
      and not am_ponte_c3[1].operador_ponte
      and stats_ponte_c3["pontes"] == 0,
      (stats_ponte_c3, am_ponte_c3[1]))

obs_c3 = _analisar_sem_reconfirmar(am_ponte_c3[1], estruturada=False)
check("veto C3 não cria pessoa, track, identidade ou atividade",
      am_ponte_c3[1].pessoas == []
      and am_ponte_c3[1].fora_posto == []
      and am_ponte_c3[1].identidade_track_id is None
      and isinstance(obs_c3, list)
      and len(obs_c3) == 1
      and obs_c3[0].get("papel") is None
      and obs_c3[0].get("track_id") == pl.POSTO_INCONCLUSIVO_TID
      and obs_c3[0].get("trabalho") is None,
      (am_ponte_c3[1], obs_c3))

am_hard_miss = [_amostra_c3(0.079999)]
stats_hard_miss = _confirmar_c3(am_hard_miss)
check("104s hard miss sem candidato C3 permanece ausência normal",
      not am_hard_miss[0].presenca_safety_gate
      and am_hard_miss[0].operador_presente is False
      and stats_hard_miss["c3_vetos"] == 0,
      (stats_hard_miss, am_hard_miss[0]))


print("\n[14] C4.2 — consenso multicâmera temporal em 640")


class _YoloC42(_YoloFake):
    def __init__(self, resultados=(), erro=None):
        super().__init__(VAZIO)
        self.resultados_c42 = list(resultados)
        self.erro_c42 = erro

    def predict(self, _frame, **kwargs):
        self.predict_calls.append(dict(kwargs))
        if kwargs.get("imgsz") != 640:
            raise AssertionError(kwargs)
        snapshot = {nome: list(cbs) for nome, cbs in self.callbacks.items()}
        self.callbacks_durante_predict.append(snapshot)
        if any(
            _eh_callback_tracker(cb)
            for nome in ("on_predict_start", "on_predict_postprocess_end")
            for cb in snapshot.get(nome, [])
        ):
            raise AssertionError("callback temporal do tracker ficou ativo")
        if self.erro_c42 is not None:
            raise self.erro_c42
        if not self.resultados_c42:
            raise AssertionError("resultado 640 inesperado")
        return [self.resultados_c42.pop(0)]


def _amostra_c42(tempo_s, *, pessoas=None, operador_presente=False):
    return pl.Amostra(
        frame_idx=int(tempo_s * 10), tempo_s=float(tempo_s), img_b64="",
        pessoas=list(pessoas or []), dim=(CAM2_W, CAM2_H),
        operador_presente=operador_presente,
    )


def _rodar_c42(amostras, resultados=(), *, offset_s=0.0, erro=None,
               video_path_cam2="cam2.mp4"):
    cap = _CapFake()
    yolo = _YoloC42(resultados, erro=erro)
    video_capture_antigo = getattr(pl.cv2, "VideoCapture", None)
    pl.cv2.VideoCapture = lambda _p: cap
    try:
        n = pl.etapa_consenso_multicamera_640(
            amostras, "cam1.mp4", video_path_cam2, yolo,
            POSTO_CAM2, POSTO_CAM2, offset_s=offset_s,
        )
        return n, yolo, cap
    finally:
        if video_capture_antigo is None:
            delattr(pl.cv2, "VideoCapture")
        else:
            pl.cv2.VideoCapture = video_capture_antigo


def _c42_check(nome, condicao, extra=""):
    check(nome, condicao, extra)


am_mesmo = [_amostra_c42(10.0)]
n_mesmo, y_mesmo, _ = _rodar_c42(
    am_mesmo, [RESULTADO_FP2, RESULTADO_FP2],
)
_c42_check("CAM1 + CAM2 no mesmo instante dispara C4.2",
           n_mesmo == 1
           and am_mesmo[0].presenca_safety_gate
           and am_mesmo[0].presenca_safety_motivo
           == "veto_posto_vazio_por_consenso_multicamera_640"
           and am_mesmo[0].operador_presente is None
           and am_mesmo[0].pessoas == []
           and am_mesmo[0].identidade_track_id is None
           and am_mesmo[0].presenca_safety_delta_s == 0.0,
           (n_mesmo, am_mesmo[0]))
_c42_check("C4.2 usa imgsz 640 e thresholds atuais CAM1/CAM2",
           [c.get("imgsz") for c in y_mesmo.predict_calls] == [640, 640]
           and [c.get("conf") for c in y_mesmo.predict_calls]
           == [pl._OPERADOR_CONF, pl._CAM2_CONF],
           y_mesmo.predict_calls)

_gate_original = pl._presenca_safety_gate
_gate_chamadas = []


def _gate_capture(*args, **kwargs):
    _gate_chamadas.append(dict(kwargs))
    return _gate_original(*args, **kwargs)


pl._presenca_safety_gate = _gate_capture
try:
    am_offset = [_amostra_c42(10.0)]
    n_offset, _, cap_offset = _rodar_c42(
        am_offset, [RESULTADO_FP2, RESULTADO_FP2], offset_s=-10.0,
    )
finally:
    pl._presenca_safety_gate = _gate_original
_c42_check("C4.2 respeita o offset existente no seek da CAM2",
           n_offset == 1 and cap_offset.seeks == [10000.0, 0.0]
           and _gate_chamadas[0].get("boundary_safety") is False
           and _gate_chamadas[1].get("boundary_safety") is True,
           (n_offset, cap_offset.seeks, _gate_chamadas))

am_oito = [_amostra_c42(0.0), _amostra_c42(8.0)]
n_oito, _, _ = _rodar_c42(
    am_oito, [RESULTADO_FP2, VAZIO, VAZIO, RESULTADO_FP2],
)
_c42_check("CAM1 + CAM2 com delta de 8s dispara",
           n_oito == 2
           and all(am.presenca_safety_gate for am in am_oito)
           and all(am.presenca_safety_delta_s == 8.0 for am in am_oito),
           (n_oito, am_oito))

am_longe = [_amostra_c42(0.0), _amostra_c42(8.02)]
n_longe, y_longe, _ = _rodar_c42(
    am_longe, [RESULTADO_FP2, VAZIO, VAZIO],
)
_c42_check("delta acima de 8.01s não dispara",
           n_longe == 0
           and not any(am.presenca_safety_gate for am in am_longe)
           and len(y_longe.predict_calls) == 3,
           (n_longe, y_longe.predict_calls))

am_so_cam1 = [_amostra_c42(0.0)]
n_so_cam1, _, _ = _rodar_c42(am_so_cam1, [RESULTADO_FP2, VAZIO])
_c42_check("somente hit CAM1 não dispara",
           n_so_cam1 == 0 and not am_so_cam1[0].presenca_safety_gate
           and am_so_cam1[0].operador_presente is False,
           am_so_cam1[0])

am_so_cam2 = [_amostra_c42(0.0)]
n_so_cam2, y_so_cam2, _ = _rodar_c42(am_so_cam2, [VAZIO, RESULTADO_FP2])
_c42_check("somente hit CAM2 não dispara",
           n_so_cam2 == 0 and not am_so_cam2[0].presenca_safety_gate
           and len(y_so_cam2.predict_calls) == 1,
           (n_so_cam2, y_so_cam2.predict_calls))

am_multiplos = [
    _amostra_c42(0.0), _amostra_c42(4.0),
    _amostra_c42(20.0), _amostra_c42(30.0),
]
n_multiplos, _, _ = _rodar_c42(
    am_multiplos,
    [RESULTADO_FP2, VAZIO, RESULTADO_FP2, VAZIO,
     VAZIO, RESULTADO_FP2, RESULTADO_FP2],
)
_c42_check("múltiplos hits vetam somente slots participantes dos pares",
           n_multiplos == 3
           and [am.presenca_safety_gate for am in am_multiplos]
           == [True, True, True, False],
           (n_multiplos, am_multiplos))

am_pessoa = [_amostra_c42(
    0.0, pessoas=[{"track_id": 4, "papel": "operador"}],
    operador_presente=True,
)]
n_pessoa, y_pessoa, _ = _rodar_c42(am_pessoa, [RESULTADO_FP2, RESULTADO_FP2])
_c42_check("pessoa normal presente não sofre interferência",
           n_pessoa == 0 and y_pessoa.predict_calls == []
           and not am_pessoa[0].presenca_safety_gate,
           am_pessoa[0])

am_true = [_amostra_c42(0.0, operador_presente=True)]
n_true, y_true, _ = _rodar_c42(am_true, [RESULTADO_FP2, RESULTADO_FP2])
_c42_check("operador_presente=True não sofre interferência",
           n_true == 0 and y_true.predict_calls == []
           and am_true[0].operador_presente is True,
           am_true[0])

am_gate = [_amostra_c42(0.0)]
pl._marcar_presenca_safety(am_gate[0], {
    "status": "veto", "motivo": "veto_posto_vazio_por_deteccao_independente",
    "confidence": FP2_CONF, "bbox": FP2_BBOX,
}, "cam1")
am_gate[0].operador_presente = None
n_gate, y_gate, _ = _rodar_c42(am_gate, [RESULTADO_FP2, RESULTADO_FP2])
_c42_check("safety C1/C2/C3 ativo preserva o motivo original",
           n_gate == 0 and y_gate.predict_calls == []
           and am_gate[0].presenca_safety_motivo
           == "veto_posto_vazio_por_deteccao_independente",
           am_gate[0])

am_erro = [_amostra_c42(0.0)]
n_erro, y_erro, _ = _rodar_c42(
    am_erro, erro=RuntimeError("falha simulada 640"),
)
_c42_check("erro na inferência 640 é fail-open para C4.2",
           n_erro == 0 and not am_erro[0].presenca_safety_gate
           and am_erro[0].operador_presente is False
           and len(y_erro.predict_calls) == 1,
           (n_erro, am_erro[0]))

am_sem_cam2 = [_amostra_c42(0.0)]
n_sem_cam2, y_sem_cam2, _ = _rodar_c42(
    am_sem_cam2, [RESULTADO_FP2], video_path_cam2=None,
)
_c42_check("fluxo sem CAM2 mantém comportamento antigo",
           n_sem_cam2 == 0 and y_sem_cam2.predict_calls == []
           and not am_sem_cam2[0].presenca_safety_gate
           and am_sem_cam2[0].operador_presente is False,
           am_sem_cam2[0])

am_nunca_true = [_amostra_c42(0.0)]
n_nunca_true, _, _ = _rodar_c42(
    am_nunca_true, [RESULTADO_FP2, RESULTADO_FP2],
)
_c42_check("C4.2 nunca define operador_presente=True",
           n_nunca_true == 1 and am_nunca_true[0].operador_presente is None,
           am_nunca_true[0])


print("\n[15] C5 — resgate positivo independente CAM1 em 640")


def _rodar_cam1_640(amostras, resultados=(), *, rois=POSTO_CAM2,
                     erro=None, cap=None):
    cap = cap or _CapFake()
    yolo = _YoloC42(resultados, erro=erro)
    video_capture_antigo = getattr(pl.cv2, "VideoCapture", None)
    pl.cv2.VideoCapture = lambda _p: cap
    try:
        n, cache = pl.etapa_resgate_cam1_640(
            amostras, "cam1.mp4", yolo, rois,
        )
        return n, cache, yolo, cap
    finally:
        if video_capture_antigo is None:
            delattr(pl.cv2, "VideoCapture")
        else:
            pl.cv2.VideoCapture = video_capture_antigo


# 1 + 6 — candidato real, inclusive no fluxo CAM1-only, vira presença positiva.
am_c5_hit = [_amostra_c42(8.0)]
_gate_c5_original = pl._presenca_safety_gate
_gate_c5_chamadas = []


def _gate_c5_capture(*args, **kwargs):
    _gate_c5_chamadas.append(dict(kwargs))
    return _gate_c5_original(*args, **kwargs)


pl._presenca_safety_gate = _gate_c5_capture
try:
    n_c5_hit, cache_c5_hit, y_c5_hit, cap_c5_hit = _rodar_cam1_640(
        am_c5_hit, [RESULTADO_FP2],
    )
finally:
    pl._presenca_safety_gate = _gate_c5_original
check("C5 CAM1-only promove hit 640 a operador_presente=True",
      n_c5_hit == 1
      and am_c5_hit[0].operador_presente is True
      and am_c5_hit[0].operador_resgate_cam1_640
      and not am_c5_hit[0].presenca_safety_gate
      and cache_c5_hit[0].get("status") == "veto"
      and cap_c5_hit.seeks == [8000.0],
      (n_c5_hit, cache_c5_hit, am_c5_hit[0]))
check("C5 usa exatamente conf 0.30, imgsz 640, sem boundary/C3",
      len(y_c5_hit.predict_calls) == 1
      and y_c5_hit.predict_calls[0].get("conf") == pl._OPERADOR_CONF
      and y_c5_hit.predict_calls[0].get("imgsz") == 640
      and _gate_c5_chamadas[0].get("boundary_safety") is False
      and _gate_c5_chamadas[0].get("capturar_c3") is False,
      (y_c5_hit.predict_calls, _gate_c5_chamadas))

# Prova de integração com o caminho legado do gate: o slot não depende da
# descrição da CAM2 e materializa papel operador sem inventar produtividade.
obs_c5_hit = _analisar_sem_reconfirmar(am_c5_hit[0], estruturada=False)
check("C5 materializa papel operador sem VLM/produtividade",
      isinstance(obs_c5_hit, list)
      and len(obs_c5_hit) == 1
      and obs_c5_hit[0].get("papel") == "operador"
      and obs_c5_hit[0].get("origem_gate") == "resgate_cam1_640"
      and obs_c5_hit[0].get("track_id") == pl.OPERADOR_CAM1_640_TID
      and obs_c5_hit[0].get("trabalho") is None
      and obs_c5_hit[0].get("produtividade_observada") is False,
      obs_c5_hit)
obs_c5_hit_estruturado = _analisar_sem_reconfirmar(
    am_c5_hit[0], estruturada=True,
)
check("C5 preserva papel operador também com gate estruturado ligado",
      isinstance(obs_c5_hit_estruturado, list)
      and len(obs_c5_hit_estruturado) == 1
      and obs_c5_hit_estruturado[0].get("papel") == "operador"
      and obs_c5_hit_estruturado[0].get("trabalho") is None
      and obs_c5_hit_estruturado[0].get("produtividade_observada") is False,
      obs_c5_hit_estruturado)

# 2 — miss continua sendo ausência candidata; C5 não cria safety veto.
am_c5_miss = [_amostra_c42(0.0)]
n_c5_miss, cache_c5_miss, y_c5_miss, _ = _rodar_cam1_640(
    am_c5_miss, [VAZIO],
)
check("C5 sem hit mantém candidato a posto vazio",
      n_c5_miss == 0
      and am_c5_miss[0].operador_presente is False
      and not am_c5_miss[0].operador_resgate_cam1_640
      and not am_c5_miss[0].presenca_safety_gate
      and cache_c5_miss[0].get("status") == "livre"
      and len(y_c5_miss.predict_calls) == 1,
      (n_c5_miss, cache_c5_miss, am_c5_miss[0]))

# 3 — presença já resolvida nunca paga o passe 640.
am_c5_true = [_amostra_c42(0.0, operador_presente=True)]
n_c5_true, cache_c5_true, y_c5_true, _ = _rodar_cam1_640(
    am_c5_true, [RESULTADO_FP2],
)
check("C5 ignora slot que já é operador",
      n_c5_true == 0 and cache_c5_true == {}
      and y_c5_true.predict_calls == []
      and am_c5_true[0].operador_presente is True,
      (n_c5_true, cache_c5_true, am_c5_true[0]))

# 4 — safety/inconclusivo não é candidato e não pode ser promovido.
am_c5_safety = [_amostra_c42(0.0, operador_presente=None)]
pl._marcar_presenca_safety(am_c5_safety[0], {
    "status": "veto", "motivo": "veto_existente",
    "confidence": FP2_CONF, "bbox": FP2_BBOX,
}, "cam1")
n_c5_safety, cache_c5_safety, y_c5_safety, _ = _rodar_cam1_640(
    am_c5_safety, [RESULTADO_FP2],
)
check("C5 preserva safety/inconclusivo não elegível",
      n_c5_safety == 0 and cache_c5_safety == {}
      and y_c5_safety.predict_calls == []
      and am_c5_safety[0].operador_presente is None
      and am_c5_safety[0].presenca_safety_motivo == "veto_existente",
      (n_c5_safety, cache_c5_safety, am_c5_safety[0]))

# 5 — fora_posto mantém semântica própria e não entra no candidato simplificado.
am_c5_fora = [_amostra_c42(0.0)]
am_c5_fora[0].fora_posto = [{"track_id": 19, "papel": pl.PAPEL_OPERADOR_FORA}]
n_c5_fora, cache_c5_fora, y_c5_fora, _ = _rodar_cam1_640(
    am_c5_fora, [RESULTADO_FP2],
)
check("C5 preserva fora_posto sem inferência ou promoção",
      n_c5_fora == 0 and cache_c5_fora == {}
      and y_c5_fora.predict_calls == []
      and am_c5_fora[0].operador_presente is False
      and am_c5_fora[0].fora_posto[0]["papel"] == pl.PAPEL_OPERADOR_FORA,
      (n_c5_fora, cache_c5_fora, am_c5_fora[0]))

# 7 + 8 — com CAM2, C5 continua primeiro e C4.2 reutiliza o cache. O hit já
# promovido segue True; a única chamada adicional é a leitura CAM2 do vizinho.
am_c5_dupla = [_amostra_c42(0.0), _amostra_c42(8.0)]
cap_c5_dupla = _CapFake()
y_c5_dupla = _YoloC42([RESULTADO_FP2, VAZIO, RESULTADO_FP2])
video_capture_antigo = getattr(pl.cv2, "VideoCapture", None)
pl.cv2.VideoCapture = lambda _p: cap_c5_dupla
try:
    n_c5_dupla, cache_c5_dupla = pl.etapa_resgate_cam1_640(
        am_c5_dupla, "cam1.mp4", y_c5_dupla, POSTO_CAM2,
    )
    chamadas_apos_c5 = len(y_c5_dupla.predict_calls)
    n_c42_reuso = pl.etapa_consenso_multicamera_640(
        am_c5_dupla, "cam1.mp4", "cam2.mp4", y_c5_dupla,
        POSTO_CAM2, POSTO_CAM2,
        resultados_cam1_640=cache_c5_dupla,
    )
finally:
    if video_capture_antigo is None:
        delattr(pl.cv2, "VideoCapture")
    else:
        pl.cv2.VideoCapture = video_capture_antigo
check("C5 funciona com CAM2 e C4.2 não desfaz presença positiva",
      n_c5_dupla == 1 and n_c42_reuso == 1
      and am_c5_dupla[0].operador_presente is True
      and am_c5_dupla[0].operador_resgate_cam1_640
      and not am_c5_dupla[0].presenca_safety_gate
      and am_c5_dupla[1].operador_presente is None
      and am_c5_dupla[1].presenca_safety_gate,
      (n_c5_dupla, n_c42_reuso, am_c5_dupla))
check("C4.2 reutiliza CAM1@640 sem inferência duplicada",
      chamadas_apos_c5 == 2
      and len(y_c5_dupla.predict_calls) == 3
      and [c.get("conf") for c in y_c5_dupla.predict_calls]
      == [pl._OPERADOR_CONF, pl._OPERADOR_CONF, pl._CAM2_CONF],
      y_c5_dupla.predict_calls)

# 9 — sem geometria do posto não existe evidência positiva válida.
am_c5_sem_roi = [_amostra_c42(0.0)]
n_c5_sem_roi, cache_c5_sem_roi, y_c5_sem_roi, _ = _rodar_cam1_640(
    am_c5_sem_roi, [RESULTADO_FP2], rois={},
)
check("C5 sem ROI posto_operador é no-op seguro",
      n_c5_sem_roi == 0 and cache_c5_sem_roi == {}
      and y_c5_sem_roi.predict_calls == []
      and am_c5_sem_roi[0].operador_presente is False,
      (n_c5_sem_roi, cache_c5_sem_roi, am_c5_sem_roi[0]))

# 10 — leitura e inferência falham fechadas para presença: nunca promovem.
am_c5_erro = [_amostra_c42(0.0)]
n_c5_erro, cache_c5_erro, y_c5_erro, _ = _rodar_cam1_640(
    am_c5_erro, erro=RuntimeError("falha simulada C5"),
)
check("C5 em falha de inferência nunca inventa presença",
      n_c5_erro == 0 and len(y_c5_erro.predict_calls) == 1
      and cache_c5_erro[0].get("status") == "erro"
      and am_c5_erro[0].operador_presente is False
      and not am_c5_erro[0].operador_resgate_cam1_640,
      (n_c5_erro, cache_c5_erro, am_c5_erro[0]))

am_c5_video = [_amostra_c42(0.0)]
n_c5_video, cache_c5_video, y_c5_video, _ = _rodar_cam1_640(
    am_c5_video, [RESULTADO_FP2], cap=_CapFechado(),
)
check("C5 em falha de leitura/vídeo nunca inventa presença",
      n_c5_video == 0 and y_c5_video.predict_calls == []
      and cache_c5_video[0].get("status") == "erro"
      and am_c5_video[0].operador_presente is False,
      (n_c5_video, cache_c5_video, am_c5_video[0]))


print(f"\n{'=' * 68}\n  {ok} ok - {fail} falha(s)\n{'=' * 68}")
raise SystemExit(1 if fail else 0)
