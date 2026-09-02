"""Regressões cirúrgicas da cor do interlocutor em duas câmeras."""
import os
import sys
import types

for nome in (
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq", "anthropic", "openai",
    "dotenv", "httpx", "PIL", "PIL.Image",
):
    sys.modules.setdefault(nome, types.ModuleType(nome))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object
sys.modules["numpy"].ndarray = object
sys.modules["cv2"].CAP_PROP_FRAME_COUNT = 7
sys.modules["cv2"].CAP_PROP_FPS = 5
sys.modules["cv2"].CAP_PROP_POS_MSEC = 0
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ["KV_PRODUTIVIDADE_OPERADOR_V9"] = "on"

from backend import pipeline as pl  # noqa: E402


ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def roupa(cor, confianca=0.95):
    return {"cor_superior": cor, "confianca_cor": confianca}


def fundir(cam1, cam2, *, obrigatoria=False, estado=None):
    return pl._fundir_evidencias_roupa_superior(
        cam1, cam2, cam2_obrigatoria=obrigatoria, estado_cam2=estado,
    )


print("[1] Fusão pura, conservadora e auditável")
casos = [
    ("cinza + cinza", roupa("cinza", .96), roupa("cinza", .83), False, None, "cinza", .83),
    ("não-cinza + não-cinza", roupa("nao_cinza"), roupa("nao_cinza", .90), False, None, "nao_cinza", .90),
    ("CAM1 cinza, CAM2 incerta legado", roupa("cinza"), roupa("incerto"), False, None, "cinza", .95),
    ("CAM1 incerta, CAM2 cinza não confirma dual", roupa("incerto"), roupa("cinza"), True, "valida", "incerto", .0),
    ("CAM1 não-cinza, CAM2 incerta legado", roupa("nao_cinza"), roupa("incerto"), False, None, "nao_cinza", .95),
    ("CAM1 incerta, CAM2 não-cinza não confirma dual", roupa("incerto"), roupa("nao_cinza"), True, "valida", "incerto", .0),
    ("conflito cinza/não-cinza", roupa("cinza"), roupa("nao_cinza"), True, "valida", "incerto", .0),
    ("conflito não-cinza/cinza", roupa("nao_cinza"), roupa("cinza"), True, "valida", "incerto", .0),
    ("ambas incertas", roupa("incerto"), roupa("incerto"), True, "incerto", "incerto", .0),
    ("CAM2 ausente preserva CAM1", roupa("cinza"), None, False, None, "cinza", .95),
    ("CAM1 ausente, CAM2 válida não confirma dual", None, roupa("cinza"), True, "valida", "incerto", .0),
    ("confiança abaixo do mínimo não confirma dual", roupa("cinza", .1), roupa("cinza"), True, "valida", "incerto", .0),
]
for nome, cam1, cam2, obrigatoria, estado, cor, confianca in casos:
    atual = fundir(cam1, cam2, obrigatoria=obrigatoria, estado=estado)
    check(nome, atual["cor_superior"] == cor and atual["confianca_cor"] == confianca, atual)

for estado in ("incerto", "ambiguo", "sem_medida", "indisponivel"):
    atual = fundir(roupa("cinza"), None, obrigatoria=True, estado=estado)
    check(f"CAM2 {estado} bloqueia confirmação CAM1", atual["cor_superior"] == "incerto", atual)


class Tensor:
    def __init__(self, valor): self.valor = valor
    def cpu(self): return self
    def numpy(self): return self.valor
    def astype(self, *args): return self.valor.astype(*args)


class Boxes:
    def __init__(self, boxes):
        self.xyxy = Tensor([Vetor(b) for b in boxes])
        self.id = Tensor(Vetor(range(1, len(boxes) + 1)))
    def __len__(self): return len(self.xyxy.valor)


class Keypoints:
    def __init__(self, n, presente=True):
        if not presente:
            self.xyn = None
            return
        poses = []
        for _ in range(n):
            pose = [Vetor((0.0, 0.0)) for _ in range(17)]
            pose[5] = Vetor((.30, .30)); pose[6] = Vetor((.70, .30))
            pose[11] = Vetor((.35, .70)); pose[12] = Vetor((.65, .70))
            poses.append(Vetor(pose))
        self.xyn = Tensor(Vetor(poses))


class Resultado:
    def __init__(self, boxes, pose=True):
        self.boxes = Boxes(boxes)
        self.keypoints = Keypoints(len(boxes), pose)


class Yolo:
    def __init__(self, boxes, pose=True): self.boxes, self.pose, self.chamadas = boxes, pose, 0
    def track(self, frame, **kwargs):
        self.chamadas += 1
        return [Resultado(self.boxes, self.pose)]


class Captura:
    def __init__(self, frame): self.frame = frame
    def isOpened(self): return True
    def get(self, _): return 30.0
    def set(self, *_): pass
    def read(self): return True, self.frame
    def release(self): pass


class Vetor(list):
    def astype(self, *_): return self


class Frame:
    shape = (240, 200, 3)


def roda_cam2(boxes, medida, *, pose=True):
    frame = Frame()
    am = pl.Amostra(0, 0.0, "cam1", [])
    yolo = Yolo(boxes, pose)
    chamadas = []
    captura_original = getattr(pl.cv2, "VideoCapture", None)
    original = {
        "build": pl._build_rois,
        "anchor": pl._ponto_ancora, "inside": pl._ponto_em_roi,
        "avaliar": pl.avaliar_roupa_superior, "b64": pl.frame_para_base64,
        "anotar": pl.anotar_frame_com_ids,
    }
    try:
        pl.cv2.VideoCapture = lambda _: Captura(frame)
        pl._build_rois = lambda *_: {"posto": {"polygon": object()}}
        pl._ponto_ancora = lambda pessoa, *_: ((pessoa["bbox"][0] + pessoa["bbox"][2]) / 2, 40)
        pl._ponto_em_roi = lambda x, *_: x < 100
        pl.anotar_frame_com_ids = lambda _, pessoas: {"pessoas": pessoas}
        pl.frame_para_base64 = lambda imagem: (
            "CAM2-ANOTADA:" + ",".join(p["rotulo"] for p in imagem["pessoas"])
            if isinstance(imagem, dict) else "cam2"
        )
        def avaliar(frame_recebido, bbox, *, kpts, exigir_pose):
            chamadas.append((frame_recebido, bbox, kpts, exigir_pose))
            return medida if kpts is not None else roupa("incerto", 0.0)
        pl.avaliar_roupa_superior = avaliar
        pl._anexar_segundo_angulo(
            [am], "cam2.mp4", yolo=yolo,
            rois_sec={"posto": {"papel": "posto_operador"}},
        )
    finally:
        if captura_original is None:
            del pl.cv2.VideoCapture
        else:
            pl.cv2.VideoCapture = captura_original
        pl._build_rois = original["build"]
        pl._ponto_ancora = original["anchor"]
        pl._ponto_em_roi = original["inside"]
        pl.avaliar_roupa_superior = original["avaliar"]
        pl.frame_para_base64 = original["b64"]
        pl.anotar_frame_com_ids = original["anotar"]
    return am, yolo, chamadas, frame


print("\n[2] CAM2 mede no mesmo passe e nunca escolhe candidato")
am, yolo, chamadas, frame = roda_cam2([[10, 10, 80, 220], [120, 10, 180, 220]], roupa("cinza"))
check("avalia somente candidato dentro do posto", len(chamadas) == 1, chamadas)
check("usa mesmo frame, bbox e keypoints existentes", chamadas and chamadas[0][0] is frame and chamadas[0][1] == (10, 10, 80, 220) and chamadas[0][3] is True, chamadas)
check("não faz nova inferência YOLO", yolo.chamadas == 1, yolo.chamadas)
check("preserva medição CAM2 cinza", am.roupas_superiores_cam2 == [roupa("cinza")], am.roupas_superiores_cam2)
check("sem associação estrutural CAM2 é ambígua", am.evidencia_cor_cam2["estado_cam2"] == "ambiguo", am.evidencia_cor_cam2)

am_multi, _, chamadas_multi, _ = roda_cam2([[10, 10, 80, 220], [20, 10, 90, 220]], roupa("nao_cinza"))
check("múltiplos candidatos são todos medidos", len(chamadas_multi) == 2, chamadas_multi)
check("múltiplos candidatos não são associados arbitrariamente", am_multi.evidencia_cor_cam2["estado_cam2"] == "ambiguo", am_multi.evidencia_cor_cam2)

am_sem_pose, _, chamadas_sem_pose, _ = roda_cam2([[10, 10, 80, 220]], roupa("cinza"), pose=False)
check("CAM2 sem pose produz medida incerta", chamadas_sem_pose and chamadas_sem_pose[0][2] is None and am_sem_pose.roupas_superiores_cam2[0]["cor_superior"] == "incerto", am_sem_pose.roupas_superiores_cam2)
check("CAM2 sem pose abstém na fusão", am_sem_pose.evidencia_cor_cam2["estado_cam2"] == "incerto", am_sem_pose.evidencia_cor_cam2)

print("\n[3] Decisão canônica só muda quando CAM2 foi fornecida")
pessoas = [
    {"track_id": 1, "roupa_superior": roupa("nao_cinza")},
    {"track_id": 2, "roupa_superior": roupa("cinza")},
]
am_cam1 = pl.Amostra(0, 0.0, "cam1", pessoas)
trecho = {"conversa_estado": "identificada", "interlocutor": "P2"}
legado = pl._evidencia_conversa_do_trecho(
    trecho, am_cam1, {"P1": 1, "P2": 2}, 1, "conversa_ou_celular",
)
check("CAM1-only preserva gestor cinza", legado["tipo"] == pl.TIPO_INTERLOCUTOR_GESTOR, legado)
am_dual = pl.Amostra(0, 0.0, "cam1", pessoas)
am_dual.evidencia_cor_cam2 = {"estado_cam2": "ambiguo", "candidatos": [roupa("cinza")]}
dual = pl._evidencia_conversa_do_trecho(
    trecho, am_dual, {"P1": 1, "P2": 2}, 1, "conversa_ou_celular",
)
check("CAM2 ambígua torna o campo canônico incerto", dual["tipo"] == pl.TIPO_INTERLOCUTOR_INCERTO and dual["cor_superior"] == "incerto", dual)
check("auditoria preserva o estado e os candidatos CAM2", dual["evidencia_cor_cameras"]["estado_cam2"] == "ambiguo" and dual["evidencia_cor_cameras"]["cam2_candidatos"] == [roupa("cinza")], dual)

print("\n[4] Sequência dual associa P2 a C2P2 mesmo sem ajuda de atividade")
am_seq, _, _, _ = roda_cam2(
    [[10, 10, 80, 220], [20, 10, 90, 220]], roupa("cinza"),
)
am_seq.img_b64 = "CAM1"
am_seq.pessoas = [
    {"track_id": 1, "rotulo": "P1", "papel": "operador", "roupa_superior": roupa("nao_cinza")},
    {"track_id": 2, "rotulo": "P2", "papel": "visitante", "roupa_superior": roupa("cinza")},
]
capturada = {}
ajuda_original, groq_original = pl._cam2_ajuda, pl.groq_vision_call
try:
    pl._cam2_ajuda = lambda _: False
    def chamada_sequencia(*args, **kwargs):
        capturada["prompt"] = args[2]
        capturada["extras"] = kwargs.get("imagens_extra")
        return '{"trechos":[{"i":0,"operador_estado":"identificado","operador":"P1","acoes":{"P1":"conversando com P2","P2":"conversando com P1"},"trabalho":false,"motivo":"conversa_ou_celular","conversa_estado":"identificada","interlocutor":"P2","interlocutor_cam2":"C2P2"}]}'
    pl.groq_vision_call = chamada_sequencia
    saida = pl._analisar_sequencia_vlm(object(), [am_seq], "", {}, 5.0)
finally:
    pl._cam2_ajuda = ajuda_original
    pl.groq_vision_call = groq_original
bloco = saida[0]
evidencia = bloco["interlocutor_evidencia"]
check("CAM2 anotada entra na mesma chamada quando _cam2_ajuda é falsa", capturada.get("extras") == [am_seq.img_b64_secundario_interlocutor] and "C2P1, C2P2" in capturada.get("prompt", ""), capturada)
check("P2 + C2P2 cinza chega a gestor_cinza pelo pipeline", evidencia["tipo"] == pl.TIPO_INTERLOCUTOR_GESTOR and evidencia.get("interlocutor_cam2") == "C2P2", evidencia)

print(f"\n{'=' * 64}\n  {ok} ok · {fail} falha(s)\n{'=' * 64}")
raise SystemExit(1 if fail else 0)
