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


print(f"\n{'=' * 68}\n  {ok} ok - {fail} falha(s)\n{'=' * 68}")
raise SystemExit(1 if fail else 0)
