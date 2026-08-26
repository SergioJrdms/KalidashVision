"""P1: conversa auditável com gestor/colega pela roupa superior."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import types

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
for nome in (
    "requests", "ultralytics", "supabase", "groq", "anthropic", "openai",
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
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ["KV_PRODUTIVIDADE_OPERADOR_V9"] = "on"

from backend import pipeline as pl  # noqa: E402
from backend import productivity as prod  # noqa: E402
from backend.roupa_superior import avaliar_roupa_superior  # noqa: E402


ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def quadro_hsv(matiz, saturacao, brilho, largura=200, altura=240):
    hsv = np.uint8([[[matiz, saturacao, brilho]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return np.full((altura, largura, 3), bgr, dtype=np.uint8)


BBOX = [20, 10, 180, 230]

print("[1] Classificador isolado preserva S/V e falha fechado")
cenarios = [
    ("cinza medio", 0, 0, 130, "cinza"),
    ("cinza quente pouco saturado", 15, 20, 150, "cinza"),
    ("cinza escuro visivel", 0, 0, 75, "cinza"),
    ("cinza claro visivel", 0, 10, 190, "cinza"),
    ("vermelho", 0, 255, 220, "nao_cinza"),
    ("amarelo", 30, 255, 220, "nao_cinza"),
    ("verde", 60, 255, 220, "nao_cinza"),
    ("azul", 120, 255, 220, "nao_cinza"),
    ("branco", 0, 0, 250, "nao_cinza"),
    ("preto visivel", 0, 0, 25, "nao_cinza"),
    ("sem luz", 0, 0, 5, "incerto"),
    ("neutro escuro limitrofe", 0, 0, 45, "incerto"),
    ("neutro claro limitrofe", 0, 0, 220, "incerto"),
    ("saturacao limitrofe", 0, 60, 130, "incerto"),
]
for nome, h, s, v, esperado in cenarios:
    med = avaliar_roupa_superior(quadro_hsv(h, s, v), BBOX)
    check(nome, med["cor_superior"] == esperado, med)
    check(f"{nome}: audita S, V e pixels",
          all(k in med for k in (
              "saturacao_mediana", "brilho_mediano", "pixels_utilizaveis",
              "qualidade", "confianca_cor")), med)

base_cinza = quadro_hsv(0, 0, 130)
extras = [
    ("bbox invalido", None, None, False, "incerto"),
    ("bbox cortado", [-100, 10, 180, 230], None, False, "incerto"),
    ("pessoa pequena", [90, 90, 110, 120], None, False, "incerto"),
    ("pose obrigatoria ausente", BBOX, None, True, "incerto"),
]
for nome, bbox, pose, exigir, esperado in extras:
    med = avaliar_roupa_superior(base_cinza, bbox, kpts=pose, exigir_pose=exigir)
    check(nome, med["cor_superior"] == esperado, med)

pose_ruim = [[0.0, 0.0] for _ in range(13)]
pose_ruim[5], pose_ruim[6] = [0.49, 0.30], [0.51, 0.30]
check("ombros insuficientes não chutam gestor",
      avaliar_roupa_superior(base_cinza, BBOX, kpts=pose_ruim,
                             exigir_pose=True)["cor_superior"] == "incerto")
pose = [[0.0, 0.0] for _ in range(13)]
pose[5], pose[6], pose[11], pose[12] = (
    [0.30, 0.25], [0.70, 0.25], [0.35, 0.75], [0.65, 0.75]
)
check("pose completa cinza autoriza cinza",
      avaliar_roupa_superior(base_cinza, BBOX, kpts=pose,
                             exigir_pose=True)["cor_superior"] == "cinza")
check("pose completa colorida autoriza nao-cinza",
      avaliar_roupa_superior(quadro_hsv(120, 255, 220), BBOX, kpts=pose,
                             exigir_pose=True)["cor_superior"] == "nao_cinza")


def roupa(cor, confianca=0.95):
    return {
        "cor_superior": cor, "confianca_cor": confianca,
        "qualidade": 0.95, "pixels_utilizaveis": 1200,
        "saturacao_mediana": 5.0 if cor == "cinza" else 200.0,
        "brilho_mediano": 130.0,
    }


def pessoa(tid, rotulo, roupa_medida):
    return {
        "track_id": tid, "rotulo": rotulo, "papel": "operador" if tid == 1 else "visitante",
        "zona": "posto", "zona_desc": "posto", "bbox": BBOX,
        "kpts": pose, "crop": None, "maos_maquina": False,
        "orientacao": "costas", "roupa_superior": roupa_medida,
    }


def amostra(roupa_operador=None, roupa_interlocutor=None):
    return pl.Amostra(
        frame_idx=0, tempo_s=0.0, img_b64="IMG", dim=(200, 240),
        pessoas=[
            pessoa(1, "P1", roupa_operador or roupa("nao_cinza")),
            pessoa(2, "P2", roupa_interlocutor or roupa("cinza")),
        ],
    )


print("\n[2] Associação usa a OUTRA pessoa, sem escolha arbitrária")
trecho = {
    "conversa_estado": "identificada", "interlocutor": "P2",
}
evid_gestor = pl._evidencia_conversa_do_trecho(
    trecho, amostra(), {"P1": 1, "P2": 2}, 1, "conversa_ou_celular",
)
check("interlocutor cinza vira gestor", evid_gestor["tipo"] == "gestor_cinza", evid_gestor)
evid_colega = pl._evidencia_conversa_do_trecho(
    trecho, amostra(roupa("cinza"), roupa("nao_cinza")),
    {"P1": 1, "P2": 2}, 1, "conversa_ou_celular",
)
check("cinza do operador não contamina interlocutor colorido",
      evid_colega["tipo"] == "outra_pessoa", evid_colega)
evid_proprio = pl._evidencia_conversa_do_trecho(
    {"conversa_estado": "identificada", "interlocutor": "P1"},
    amostra(), {"P1": 1, "P2": 2}, 1, "conversa_ou_celular",
)
check("modelo apontando o operador vira incerto", evid_proprio["tipo"] == "incerto")
evid_ambiguo = pl._evidencia_conversa_do_trecho(
    {"conversa_estado": "incerta", "interlocutor": None},
    amostra(), {"P1": 1, "P2": 2}, 1, "conversa_ou_celular",
)
check("dois interlocutores possíveis ficam incertos", evid_ambiguo["tipo"] == "incerto")
check("sem contrato de conversa preserva o fluxo antigo",
      pl._evidencia_conversa_do_trecho(
          {}, amostra(), {"P1": 1, "P2": 2}, 1, "conversa_ou_celular",
      ) is None)

original_call = pl.groq_vision_call
try:
    pl.groq_vision_call = lambda *a, **k: json.dumps({"trechos": [{
        "i": 0, "operador_estado": "identificado", "operador": "P1",
        "acoes": {"P1": "conversando com P2", "P2": "conversando com P1"},
        "trabalho": False, "motivo": "conversa_ou_celular",
        "conversa_estado": "identificada", "interlocutor": "P2",
    }]})
    bloco = pl._analisar_sequencia_vlm(object(), [amostra()], "", {}, 5.0)[0]
    check("parser real aplica regra CPU e não o booleano genérico",
          bloco["trabalho"] is True
          and bloco["interlocutor_evidencia"]["tipo"] == "gestor_cinza", bloco)
finally:
    pl.groq_vision_call = original_call


def evidencia(tipo, cor, estado="identificada", confianca=0.95):
    return {
        "conversa_estado": estado, "tipo": tipo, "cor_superior": cor,
        "confianca_cor": confianca, "qualidade": 0.95,
        "pixels_utilizaveis": 1200,
        "origem": "vlm_interlocutor+roupa_superior_hsv",
    }


EV_GESTOR = evidencia("gestor_cinza", "cinza")
EV_COLEGA = evidencia("outra_pessoa", "nao_cinza")
EV_INCERTO = evidencia("incerto", "incerto", "incerta", 0.0)


def evento_prod(label, trabalho, ev_inter, **kw):
    out = {
        "video_id": "v1", "papel_pessoa": "operador",
        "comportamento_label": label, "trabalho": trabalho,
        "bbox_stats": {"interlocutor": ev_inter},
        "maos_maquina": False, "orientacao": "costas",
        "_cam_id": "cam1", "principal": False,
        "tempo_inicio_s": 0.0, "tempo_fim_s": 30.0,
        "_capturado_em": datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        "_dia": "2026-08-26", "n_amostras": 10,
    }
    out.update(kw)
    return out


gestor = evento_prod(pl.LABEL_CONVERSANDO_GESTOR, True, EV_GESTOR)
colega = evento_prod(
    pl.LABEL_CONVERSANDO_COLEGA, False, EV_COLEGA,
    maos_maquina=True, orientacao="frente", tempo_inicio_s=30, tempo_fim_s=60,
)
incerto = evento_prod(
    pl.LABEL_CONVERSANDO_INCERTO, False, EV_INCERTO,
    maos_maquina=True, orientacao="frente",
)

print("\n[3] Evidência completa vence mãos/orientação no KPI real")
check("gestor de costas é produtivo",
      prod.classificar_observacao(gestor, {"cam1": "camera"})[0] == "produtivo")
check("colega voltado ao torno e com mãos não é promovido",
      prod.classificar_observacao(colega, {"cam1": "camera"})[0] == "improdutivo")
check("conversa incerta não é promovida",
      prod.classificar_observacao(incerto, {"cam1": "camera"})[0] == "improdutivo")
check("decidir_permanencia: gestor de costas agrega valor",
      pl.decidir_permanencia(gestor, "camera")[0] == "valor_agregado")
check("decidir_permanencia: colega voltado ao torno é desperdício",
      pl.decidir_permanencia(colega, "camera")[0] == "desperdicio")
check("decidir_permanencia: incerto voltado ao torno é desperdício",
      pl.decidir_permanencia(incerto, "camera")[0] == "desperdicio")

incompletos = [
    ("sem label", {**gestor, "comportamento_label": None}),
    ("sem trabalho true", {**gestor, "trabalho": None}),
    ("sem evidencia", {**gestor, "bbox_stats": None}),
    ("baixa confianca", {**gestor, "bbox_stats": {"interlocutor": {
        **EV_GESTOR, "confianca_cor": 0.4,
    }}}),
    ("label corrigido sozinho", {
        **gestor, "comportamento_label": "conversando_colega",
        "label_corrigido": pl.LABEL_CONVERSANDO_GESTOR,
    }),
]
for nome, e in incompletos:
    check(f"{nome} não abre exceção", prod.decisao_conversa_evidenciada(e) is None)

forjado_fora = {
    **gestor, "papel_pessoa": pl.PAPEL_OPERADOR_FORA,
    "categoria_lean": "valor_agregado", "categoria_lean_origem": "ia",
}
check("Fase 110 ignora voto automático mesmo forjado completo",
      prod.classificar_observacao(forjado_fora)[0] == prod.EST_OPERADOR_FORA)
check("Fase 110 segue exigindo humano_rotulo em decidir_permanencia",
      pl.decidir_permanencia(forjado_fora, "camera")[1] == "fora_sem_classificacao")

agregado = prod.agregar_produtividade(
    [gestor, colega], agora=datetime(2026, 8, 26, 12, 5, tzinfo=timezone.utc)
)
check("30s gestor + 30s colega = 50%", agregado["produtividade_pct"] == 50.0, agregado)


def obs(t, ev_inter, trabalho):
    return {
        "tempo_s": float(t), "frame_idx": int(t * 10), "track_id": 1,
        "descricao": "conversando", "bbox": BBOX, "bbox_cam": "cam1",
        "bbox_dim": (200, 240), "zona": "posto", "papel": "operador",
        "origem_gate": "analisado", "maquina": None, "imovel": False,
        "maos_maquina": False, "orientacao": "costas", "trabalho": trabalho,
        "produtividade_observada": True,
        "produtividade_motivo": "conversa_gestor_cinza" if trabalho else "conversa_colega_nao_cinza",
        "interlocutor_evidencia": ev_inter,
    }


print("\n[4] Tag, cru, principal e interpolação preservam o contrato")
cru_gestor = pl.etapa_segmentar_eventos([obs(0, EV_GESTOR, True)], lambda *a: "outro", 5.0)[0]
check("evidência força tag gestor", cru_gestor["comportamento_label"] == pl.LABEL_CONVERSANDO_GESTOR)
check("cru persiste evidência em bbox_stats",
      cru_gestor["bbox_stats"]["interlocutor"]["tipo"] == "gestor_cinza")
principal = pl.etapa_consolidar_principais([cru_gestor], {}, 5.0)[0]
check("principal herda evidência somente do label vencedor",
      principal["bbox_stats"]["interlocutor"]["tipo"] == "gestor_cinza")
check("principal de costas move o KPI real",
      prod.classificar_observacao(principal, {"cam1": "camera"})[0] == "produtivo")
cru_colega = pl.etapa_segmentar_eventos([obs(0, EV_COLEGA, False)], lambda *a: "outro", 5.0)[0]
check("não-cinza força tag colega", cru_colega["comportamento_label"] == pl.LABEL_CONVERSANDO_COLEGA)
cru_incerto = pl.etapa_segmentar_eventos([obs(0, EV_INCERTO, False)], lambda *a: "outro", 5.0)[0]
check("incerteza ganha tag auditável própria", cru_incerto["comportamento_label"] == pl.LABEL_CONVERSANDO_INCERTO)
sem_evid = pl.etapa_segmentar_eventos([obs(0, None, False)], lambda *a: "operar_torno", 5.0)[0]
check("sem conversa confirmada preserva fluxo antigo", sem_evid["comportamento_label"] == "operar_torno")
cluster_forjado = pl.etapa_segmentar_eventos([obs(0, None, False)], lambda *a: pl.LABEL_CONVERSANDO_GESTOR, 5.0)[0]
check("cluster sozinho não fabrica gestor", cluster_forjado["comportamento_label"] == pl.LABEL_CONVERSANDO_COLEGA)

blocos = {
    0: {"acoes": {1: "conversa"}, "operador_estado": "identificado",
        "operador_track_id": 1, "trabalho": True,
        "interlocutor_evidencia": EV_GESTOR},
    2: {"acoes": {1: "conversa"}, "operador_estado": "identificado",
        "operador_track_id": 1, "trabalho": True,
        "interlocutor_evidencia": EV_GESTOR},
}
pl._interpolar_sequencia(blocos, [0, 1, 2])
check("interpolacao limpa identidade, voto e interlocutor",
      blocos[1]["operador_track_id"] is None
      and blocos[1]["trabalho"] is None
      and blocos[1]["interlocutor_evidencia"] is None, blocos[1])

main_src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
check("reload do dashboard seleciona bbox_stats", '"bbox_stats, "' in main_src)


print(f"\n{'=' * 64}\n  {ok} ok · {fail} falha(s)\n{'=' * 64}")
raise SystemExit(1 if fail else 0)
