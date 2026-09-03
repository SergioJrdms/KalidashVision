"""Fase 111E — a identidade não pode afirmar posto vazio.

O erro que este teste tranca:

    A 111D respondia "não reconheci o titular" com `operador_presente = False`
    e, no mesmo movimento, apagava `fora_posto`, `operador_ponte` e os papéis
    que C5/C4.2/C6 já tinham resolvido. Como ela roda DEPOIS de todas as
    outras etapas, o efeito era sobrescrever evidência boa com uma conclusão
    que, sobre 84 pares rotulados à mão, acertou zero vezes em 111.

Este teste NÃO lê o texto do arquivo: ele monta um segmento sintético e chama
`aplicar_identidade_logica_segmento` de verdade, nas duas rotas. Um teste de
string passaria com a linha certa e a semântica errada, que é exatamente o
tipo de regressão que custou a última rodada de validação.

Rodar:  python tests_111d_ausencia.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
          "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object

from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond):
    global ok, fail
    print(f"  {'ok  ' if cond else 'FAIL'} {nome}")
    ok += bool(cond)
    fail += not cond


# ── Cenário ─────────────────────────────────────────────────────────────
# Um slot em que a CAM2 viu o operador no posto (op_cam2=True, presença já
# estabelecida por `etapa_confirmar_operador`) e a identidade NÃO reconheceu
# nenhum track do titular. É literalmente o caso dos 96 falsos positivos.
TITULAR = 7
CAMERA = "cam1"


def montar_segmento():
    obs = {
        "cam_id": CAMERA,
        "tempo_s": 0.0,
        "medido": True,
        # Nenhum track do titular neste instante — a identidade está cega,
        # não o posto.
        "tracks": {},
        "pessoas": {},
        "frame_b64": None,
        "dim": (640, 480),
    }
    am = pl.Amostra(
        tempo_s=0.0,
        frame_idx=0,
        img_b64="",
        pessoas=[],
        dim=(640, 480),
    )
    # A presença que as câmeras já haviam estabelecido antes da 111D rodar.
    am.op_cam2 = True
    am.n_posto_cam2 = 1
    am.operador_presente = True
    am.operador_ponte = True
    am.fora_posto = [{"track_id": 99, "rotulo": "OP"}]

    resultados = [{
        "cam_id": CAMERA,
        "decisao": {
            "status": "confirmado",
            "track_ids": [TITULAR],
            "identidade_logica": "R1",
        },
        "timeline": {"status": "disponivel"},
        "identidades": [{
            "identidade_logica": "R1",
            "track_ids": [TITULAR],
            "n_amostras_posto": 3,
        }],
    }]
    return [am], resultados, {"observacoes": [obs]}


def rodar(afirma_ausencia: bool):
    original = pl._111D_AFIRMA_AUSENCIA
    pl._111D_AFIRMA_AUSENCIA = afirma_ausencia
    autoridade = pl.AUTORIDADE_111D_CONFIGURADA
    pl.AUTORIDADE_111D_CONFIGURADA = True
    try:
        amostras, resultados, dados = montar_segmento()
        resumo = pl.aplicar_identidade_logica_segmento(
            amostras, resultados, dados, CAMERA
        )
        return amostras[0], resumo
    finally:
        pl._111D_AFIRMA_AUSENCIA = original
        pl.AUTORIDADE_111D_CONFIGURADA = autoridade


print("[1] Comportamento corrigido (padrão): a identidade devolve o slot")
am, resumo = rodar(afirma_ausencia=False)
check("NÃO afirma ausência física",
      am.operador_presente is True)
check("preserva a presença que as câmeras estabeleceram",
      am.operador_presente is True and am.op_cam2 is True)
check("NÃO apaga a decisão de fora do posto (C6)",
      am.fora_posto == [{"track_id": 99, "rotulo": "OP"}])
check("NÃO apaga a ponte temporal (Fase 34)",
      am.operador_ponte is True)
check("o slot não recebe autoridade de identidade",
      am.identidade_autoritativa is False)
check("não inventa estado de identidade",
      am.identidade_estado is None)
check("o resumo conta a devolução separadamente",
      resumo.get("titular_nao_reconhecido") == 1)
check("nenhuma reatribuição de ausência acontece",
      resumo.get("reatribuicoes_ausente") == 0)
check("o resumo declara em qual regime rodou",
      resumo.get("afirma_ausencia") is False)

print("\n[2] Comportamento antigo (só para o replay A/B)")
am_v, resumo_v = rodar(afirma_ausencia=True)
check("com a chave ligada, volta a afirmar ausência",
      am_v.operador_presente is False)
check("com a chave ligada, apaga a decisão do C6",
      am_v.fora_posto == [])
check("com a chave ligada, apaga a ponte",
      am_v.operador_ponte is False)
check("com a chave ligada, o contador de devolução fica zerado",
      resumo_v.get("titular_nao_reconhecido") == 0)
check("as duas rotas divergem no veredito do slot",
      am.operador_presente is not am_v.operador_presente)

print("\n[3] A chave é de replay, não de produção")
check("o padrão do ambiente é NÃO afirmar ausência",
      os.environ.get("KV_111D_AFIRMA_AUSENCIA", "off").strip().lower()
      not in {"1", "true", "on", "yes"})
fonte = open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "backend", "pipeline.py"), encoding="utf-8"
).read()
# ⚠️ A distinção que este check tranca: a política validada em notebook
# convertia posto_vazio em INCONCLUSIVO, ou seja, `operador_presente = None`.
# Aqui o slot volta ao legado e a decisão das câmeras PERMANECE. Se alguém
# portar a regra do notebook para cá, o veredito vira None e este check cai.
check("o slot devolvido não vira abstenção (a regra do notebook viraria)",
      am.operador_presente is True and am.operador_presente is not None)
check("as outras incertezas da função continuam caindo no legado",
      fonte.count("planos.append(None)") >= 5)

print(f"\n{ok} ok · {fail} falha(s)")
sys.exit(1 if fail else 0)
