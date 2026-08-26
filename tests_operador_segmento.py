"""Fase 111B — eleição conservadora do operador lógico por segmento."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import math
import os
from pathlib import Path
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for nome in [
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(nome, types.ModuleType(nome))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object
sys.modules["numpy"].ndarray = object
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
for chave in [
    "KV_OPERADOR_SEGMENTO", "KV_OPERADOR_SEGMENTO_MIN_TEMPO_POSTO_S",
    "KV_OPERADOR_SEGMENTO_MIN_OBS_POSTO", "KV_OPERADOR_SEGMENTO_MIN_SHARE",
    "KV_OPERADOR_SEGMENTO_MIN_GAP",
]:
    os.environ.pop(chave, None)

from backend import pipeline as pl  # noqa: E402
from backend import productivity as prod  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def desc(track, posto, obs, visivel=None, cam="cam1", **extras):
    d = {
        "pessoa_track_id": track,
        "cam_id": cam,
        "tempo_posto_s": posto,
        "tempo_visivel_s": posto if visivel is None else visivel,
        "n_amostras_posto": obs,
        "n_amostras": max(obs, int((visivel or posto) / 5)),
    }
    d.update(extras)
    return d


print("[1] Casos obrigatórios de eleição")
claro = pl.eleger_operador_segmento([
    desc(7, 180, 36), desc(12, 25, 5), desc(15, 5, 1),
])
check("180/25/5 confirma o dominante", claro["status"] == "confirmado" and claro["track_id"] == 7, claro)
check("share usa todos os tempos positivos", claro["share_dominancia"] == 0.8571, claro)
check("gap mede a distância relativa ao segundo", claro["gap_segundo"] == 0.7381, claro)

empate = pl.eleger_operador_segmento([desc(7, 120, 24), desc(12, 110, 22)])
check("120/110 fica indefinido", empate["status"] == "indefinido" and empate["track_id"] is None, empate)
check("quase empate explica ambiguidade relativa", empate["motivo"] == "dominancia_ambigua", empate)

pouco = pl.eleger_operador_segmento([desc(7, 10, 2, visivel=290)])
check("poucos segundos/amostras não coroam ninguém", pouco["status"] == "indefinido" and pouco["motivo"] == "evidencia_insuficiente", pouco)

visitante = pl.eleger_operador_segmento([
    desc(4, 10, 2, visivel=290), desc(9, 120, 24, visivel=130),
])
check("muito tempo visível fora não vence a dominância no posto", visitante["track_id"] == 9, visitante)
check("tempo visível é só contexto do líder", visitante["tempo_visivel_s"] == 130.0, visitante)

ninguem = pl.eleger_operador_segmento([
    desc(4, 0, 0, visivel=280), desc(9, 0, 0, visivel=250),
])
check("ninguém no posto resulta indefinido", ninguem["status"] == "indefinido" and ninguem["track_id"] is None, ninguem)
check("não inventa posto_vazio como status", ninguem["motivo"] == "sem_evidencia_posto", ninguem)

unico = pl.eleger_operador_segmento([desc(31, 90, 18, visivel=115)])
check("um candidato com evidência razoável pode ser confirmado", unico["status"] == "confirmado" and unico["track_id"] == 31, unico)
check("candidato único tem separação total do segundo", unico["share_dominancia"] == 1.0 and unico["gap_segundo"] == 1.0, unico)

print("\n[2] Contrato, thresholds e sinais proibidos")
chaves = {
    "status", "track_id", "confianca", "tempo_posto_s", "tempo_visivel_s",
    "share_dominancia", "gap_segundo", "n_observacoes", "motivo",
}
check("resultado tem exatamente o contrato estruturado", set(claro) == chaves, set(claro))
check("campos numéricos são finitos", all(
    math.isfinite(float(claro[k])) for k in (
        "confianca", "tempo_posto_s", "tempo_visivel_s",
        "share_dominancia", "gap_segundo", "n_observacoes",
    )
), claro)

fronteira = pl.eleger_operador_segmento([
    desc(1, 60, 6), desc(2, 35, 7), desc(3, 5, 1),
])
check("todos os quatro thresholds são inclusivos na fronteira", fronteira["status"] == "confirmado", fronteira)
check("tempo abaixo de 60s falha", pl.eleger_operador_segmento([desc(1, 59.9, 20)])["status"] == "indefinido")
check("menos de 6 observações no posto falha", pl.eleger_operador_segmento([desc(1, 90, 5)])["status"] == "indefinido")
check("share abaixo de 0,60 falha", pl.eleger_operador_segmento([
    desc(1, 599.9, 120), desc(2, 300, 60), desc(3, 100.1, 20),
])["motivo"] == "dominancia_insuficiente")
check("gap abaixo de 0,25 falha independentemente do share", pl.eleger_operador_segmento([
    desc(1, 600, 120), desc(2, 350.1, 70), desc(3, 49.9, 10),
])["motivo"] == "dominancia_ambigua")

base = [desc(7, 180, 36), desc(12, 25, 5)]
proibidos = deepcopy(base)
for d in proibidos:
    d.update({
        "comportamento_label": "operando torno" if d["pessoa_track_id"] == 12 else "celular",
        "descricao_bruta": "VLM diz que este é o operador",
        "trabalho": d["pessoa_track_id"] == 12,
        "hist_sup": [1.0], "altura_rel": 0.99, "razoes": {"ombro": 7},
    })
check("atividade, VLM, cor e corpo não mudam a eleição", pl.eleger_operador_segmento(base) == pl.eleger_operador_segmento(proibidos))
antes = deepcopy(proibidos)
pl.eleger_operador_segmento(proibidos)
check("função pura não muta a entrada", proibidos == antes)

print("\n[3] Estado local e modo sombra")
segmento_1 = pl.eleger_operador_segmento([desc(7, 180, 36)])
segmento_2 = pl.eleger_operador_segmento([])
check("segmento seguinte não herda identidade anterior", segmento_1["track_id"] == 7 and segmento_2["track_id"] is None)

class LogFake:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []

    def info(self, *args):
        self.info_calls.append(args)

    def warning(self, *args):
        self.warning_calls.append(args)


modo_antigo, log_antigo = pl._OPERADOR_SEGMENTO_MODO, pl.log
eleger_real = pl.eleger_operador_segmento
try:
    fake = LogFake()
    pl.log = fake
    pl._OPERADOR_SEGMENTO_MODO = "off"
    chamadas = 0

    def espiao(ds):
        globals()["chamadas"] += 1
        return eleger_real(ds)

    pl.eleger_operador_segmento = espiao
    check("off retorna antes de qualquer eleição", pl._registrar_operador_segmento_sombra(base, ["cam1"]) == [] and chamadas == 0)
    check("off não gera log", fake.info_calls == [] and fake.warning_calls == [])

    pl._OPERADOR_SEGMENTO_MODO = "sombra"
    duas_cams = [
        desc(7, 180, 36, cam="cam1"), desc(8, 20, 4, cam="cam1"),
        desc(4, 120, 24, cam="cam2"), desc(5, 10, 2, cam="cam2"),
    ]
    copia_cams = deepcopy(duas_cams)
    diag = pl._registrar_operador_segmento_sombra(duas_cams, ["cam1", "cam2"])
    check("sombra decide uma vez por câmera", len(diag) == 2 and chamadas == 2, diag)
    check("câmeras permanecem independentes", [(d["cam_id"], d["track_id"]) for d in diag] == [("cam1", 7), ("cam2", 4)], diag)
    check("gera exatamente um log estruturado por câmera", len(fake.info_calls) == 2 and all("[operador-segmento]" in c[0] for c in fake.info_calls), fake.info_calls)
    check("diagnóstico não muta descritores", duas_cams == copia_cams)
finally:
    pl.eleger_operador_segmento = eleger_real
    pl._OPERADOR_SEGMENTO_MODO = modo_antigo
    pl.log = log_antigo

print("\n[4] Eventos, KPI, integração e default seguro")
agora = datetime(2026, 8, 17, 12, 5, tzinfo=timezone.utc)
eventos = [{
    "video_id": "v1", "papel_pessoa": "operador", "principal": False,
    "versao_instrumento": 10, "tempo_inicio_s": 0, "tempo_fim_s": 60,
    "maos_maquina": True, "_capturado_em": agora, "_dia": "2026-08-17",
    "_cam_id": "cam1", "n_amostras": 20,
}]
eventos_antes = deepcopy(eventos)
kpi_antes = prod.agregar_produtividade(eventos, agora=agora)
modo_antigo = pl._OPERADOR_SEGMENTO_MODO
try:
    pl._OPERADOR_SEGMENTO_MODO = "sombra"
    pl._registrar_operador_segmento_sombra([desc(7, 180, 36)], ["cam1"])
finally:
    pl._OPERADOR_SEGMENTO_MODO = modo_antigo
kpi_depois = prod.agregar_produtividade(eventos, agora=agora)
check("sombra não altera eventos", eventos == eventos_antes)
check("sombra não altera o contrato/KPI", kpi_antes == kpi_depois)

fonte = Path("backend/pipeline.py").read_text(encoding="utf-8")
i_proc = fonte.index("def processar_video(")
i_cam2 = fonte.index("descritores_cam2 = fechar_descritores(", i_proc)
i_shadow = fonte.index("_registrar_operador_segmento_sombra(", i_proc)
i_eventos = fonte.index("observacoes = etapa_analise_vlm(", i_proc)
check("hook roda depois de fechar a janela da cam2", i_cam2 < i_shadow)
check("hook roda antes de observações/eventos/persistência", i_shadow < i_eventos)
check("não existe SQL/migration da 111B", not list(Path("sql").glob("*111*b*")))

check("default da flag é off", pl._OPERADOR_SEGMENTO_MODO == "off", pl._OPERADOR_SEGMENTO_MODO)
os.environ["KV_OPERADOR_SEGMENTO"] = "valor_invalido"
pl_recarregado = importlib.reload(pl)
check("valor inválido também falha fechado para off", pl_recarregado._OPERADOR_SEGMENTO_MODO == "off", pl_recarregado._OPERADOR_SEGMENTO_MODO)
os.environ.pop("KV_OPERADOR_SEGMENTO", None)
importlib.reload(pl)

print(f"\n{'=' * 60}\n  {ok} ok · {fail} falha(s)\n{'=' * 60}")
sys.exit(1 if fail else 0)
