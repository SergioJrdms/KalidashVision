"""Fase 112-A' — a identidade relaxada só pode afirmar PRESENÇA.

O erro que este teste tranca:

    O A/B nos 84 pares mostrou que a eleição relaxada funciona (identidade
    confirmada de 6,0% para 16,7%) mas envenena o outro lado. A matriz de
    confusão:

        INCONCLUSIVO -> DENTRO          53 slots   o ganho
        DENTRO -> OPERADOR_FORA         28 slots   o dano
        POSTO_VAZIO -> OPERADOR_FORA     8 slots   inócuo

    Precisão de ausência por card: 98,41% -> 95,38%, com P(B>A) = 0,0%.
    E a precisão de presença NÃO subiu (81,05% -> 80,08%).

    Com `_DOMINANCIA_SO_PRESENCA`, um plano cujo estado não seja "dentro" é
    DEVOLVIDO: o slot fica com o que C1-C6 decidiram. É a lição da 111E um
    nível acima — reconhecer o titular fora do posto, ou não reconhecê-lo,
    nunca vira afirmação de ausência vinda da identidade.

⚠️ LIMITE DECLARADO DESTE TESTE: o ramo `fora` exige `obs["frame_b64"]` real e
passa por decodificação de imagem; o `cv2` é stubado aqui. Este teste exercita
o ramo `ausente`, que afirma ausência sem imagem. A guarda é UMA condição só
(`estado != "dentro"`), então ela cobre os dois ramos pelo mesmo caminho — mas
a evidência do ramo `fora` é o A/B, não este arquivo.

Rodar:  python tests_112a2_so_presenca.py
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


for nome in ("_DOMINANCIA_RELAXADA", "_DOMINANCIA_SO_PRESENCA"):
    if not hasattr(pl, nome):
        print(f"  FAIL o patch 112-A' nao foi aplicado (falta {nome})")
        sys.exit(1)


TITULAR = 7
CAMERA = "cam1"
FORA_LEGADO = [{"track_id": 99, "rotulo": "OP"}]


def montar_segmento():
    """Slot em que a cam1 VIU alguem no posto que NAO e o titular.

    `tracks` vazio => nenhum track do titular reconhecido; com `am.pessoas`
    nao vazio, a funcao monta um plano `ausente` — o caminho pelo qual a
    identidade afirma ausencia.
    """
    obs = {
        "cam_id": CAMERA,
        "tempo_s": 0.0,
        "medido": True,
        "tracks": {},
        "pessoas": {},
        "frame_b64": None,
        "dim": (640, 480),
    }
    am = pl.Amostra(
        tempo_s=0.0,
        frame_idx=0,
        img_b64="",
        pessoas=[{"track_id": 99, "rotulo": "OP", "bbox": (10, 10, 50, 120)}],
        dim=(640, 480),
    )
    # A presenca que as cameras ja haviam estabelecido antes da 111D rodar.
    am.op_cam2 = True
    am.n_posto_cam2 = 1
    am.operador_presente = True
    am.operador_ponte = True
    am.fora_posto = [dict(p) for p in FORA_LEGADO]
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


def rodar(relaxada: bool, so_presenca: bool):
    antes = {
        "_DOMINANCIA_RELAXADA": pl._DOMINANCIA_RELAXADA,
        "_DOMINANCIA_SO_PRESENCA": pl._DOMINANCIA_SO_PRESENCA,
        "_111D_AFIRMA_AUSENCIA": pl._111D_AFIRMA_AUSENCIA,
        "AUTORIDADE_111D_CONFIGURADA": pl.AUTORIDADE_111D_CONFIGURADA,
    }
    pl._DOMINANCIA_RELAXADA = relaxada
    pl._DOMINANCIA_SO_PRESENCA = so_presenca
    pl._111D_AFIRMA_AUSENCIA = False        # o estado de producao
    pl.AUTORIDADE_111D_CONFIGURADA = True
    try:
        amostras, resultados, dados = montar_segmento()
        resumo = pl.aplicar_identidade_logica_segmento(
            amostras, resultados, dados, CAMERA)
        return amostras[0], resumo
    finally:
        for k, v in antes.items():
            setattr(pl, k, v)


# ══════════════════════════════════════════════════════════════════════════
print("\n[1] Guarda LIGADA — a identidade devolve o slot em vez de assumi-lo")
# ══════════════════════════════════════════════════════════════════════════
am, resumo = rodar(relaxada=True, so_presenca=True)
check("NAO afirma ausencia", am.operador_presente is True)
check("preserva a presenca que as cameras estabeleceram",
      am.operador_presente is True and am.op_cam2 is True)
check("NAO apaga a decisao de fora do posto (C6)", am.fora_posto == FORA_LEGADO)
check("NAO apaga a ponte temporal (Fase 34)", am.operador_ponte is True)
check("o slot nao recebe autoridade de identidade",
      am.identidade_autoritativa is False)
check("nenhuma reatribuicao de ausencia acontece",
      resumo.get("reatribuicoes_ausente") == 0)
check("o papel da pessoa no posto nao foi mexido",
      am.pessoas[0].get("papel") is None)

# ══════════════════════════════════════════════════════════════════════════
print("\n[2] Guarda DESLIGADA — o teste esta medindo a coisa certa")
# ══════════════════════════════════════════════════════════════════════════
am_off, resumo_off = rodar(relaxada=True, so_presenca=False)
check("sem a guarda, a identidade assume o slot e afirma ausencia",
      am_off.operador_presente is False)
check("sem a guarda, a reatribuicao de ausencia acontece",
      resumo_off.get("reatribuicoes_ausente") == 1)
check("sem a guarda, a decisao do C6 e apagada", am_off.fora_posto == [])
check("as duas rotas divergem no veredito do slot",
      am.operador_presente is not am_off.operador_presente)

# ══════════════════════════════════════════════════════════════════════════
print("\n[3] A guarda so age com a dominancia relaxada LIGADA")
# ══════════════════════════════════════════════════════════════════════════
am_hoje, resumo_hoje = rodar(relaxada=False, so_presenca=True)
check("com a dominancia desligada, o comportamento e o de hoje",
      am_hoje.operador_presente is False)
check("com a dominancia desligada, a reatribuicao acontece como sempre",
      resumo_hoje.get("reatribuicoes_ausente") == 1)
check("producao (dominancia off) e identica ao braco sem guarda",
      am_hoje.operador_presente is am_off.operador_presente
      and resumo_hoje.get("reatribuicoes_ausente")
      == resumo_off.get("reatribuicoes_ausente"))

# ══════════════════════════════════════════════════════════════════════════
print("\n[4] O padrao de ambiente e o modo seguro")
# ══════════════════════════════════════════════════════════════════════════
check("KV_DOMINANCIA_SO_PRESENCA nasce LIGADA",
      pl._env_ligada("KV_DOMINANCIA_SO_PRESENCA", "on") is True)
check("KV_DOMINANCIA_RELAXADA nasce DESLIGADA",
      pl._env_ligada("KV_DOMINANCIA_RELAXADA", "off") is False)

print("\n" + "=" * 64)
print(f"  {ok} ok · {fail} falha(s)")
print("=" * 64)
sys.exit(1 if fail else 0)
