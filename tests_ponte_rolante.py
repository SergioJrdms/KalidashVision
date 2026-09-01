"""Regressões da observação independente OPERANDO_PONTE_ROLANTE.

Executar: python -X utf8 tests_ponte_rolante.py
"""
from __future__ import annotations

import json

from backend import ponte_rolante as pr


ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def resposta(valor, **extras):
    base = {
        "operando_ponte_rolante": valor,
        "fase": "içando" if valor is True else "nenhuma",
        "gancho_linga_visivel": valor,
        "carga_suspensa": valor,
        "evidencias_visuais": ["carga suspensa"] if valor is True else [],
        "confianca": "alta",
    }
    base.update(extras)
    return json.dumps(base, ensure_ascii=False)


print("[1] Janelas cronológicas de até três frames")
janelas = pr.montar_janelas_ponte([(0, "A"), (8, "B"), (16, "C"), (24, "D")])
check("uma janela nasce em cada posição da grade", len(janelas) == 4, janelas)
check("primeira janela contém t/t+8/t+16",
      janelas[0]["tempos_s"] == [0.0, 8.0, 16.0]
      and janelas[0]["frames_b64"] == ["A", "B", "C"], janelas[0])
check("fim da grade preserva janelas de dois e um frame",
      [j["n_frames"] for j in janelas] == [3, 3, 2, 1], janelas)


print("\n[2] Fail-closed na fronteira do VLM")
check("false não produz positivo",
      pr.analisar_janela_ponte(["A"], lambda _: resposta(False)) is None)
check("null não produz positivo",
      pr.analisar_janela_ponte(["A"], lambda _: resposta(None)) is None)
check("erro do VLM não produz positivo",
      pr.analisar_janela_ponte(
          ["A"], lambda _: (_ for _ in ()).throw(RuntimeError("provider indisponível"))
      ) is None)
check("JSON inválido não produz positivo",
      pr.analisar_janela_ponte(["A"], lambda _: "não é JSON") is None)
positivo = pr.analisar_janela_ponte(["A", "B", "C"], lambda _: resposta(True))
check("somente true literal produz positivo", positivo is not None
      and positivo["operando_ponte_rolante"] is True, positivo)
check("número 1 não é aceito como booleano true",
      pr.analisar_janela_ponte(["A"], lambda _: resposta(1)) is None)


def janela(inicio, fim, fase="içando"):
    return {
        "inicio_s": inicio,
        "fim_s": fim,
        "operando_ponte_rolante": True,
        "fase": fase,
        "evidencias_visuais": [f"evidência {inicio}"],
    }


print("\n[3] Agrupamento temporal experimental")
episodios = pr.agrupar_episodios_ponte([janela(80, 96), janela(88, 104)])
check("80–96 + 88–104 vira 80–104", len(episodios) == 1
      and (episodios[0]["inicio_s"], episodios[0]["fim_s"]) == (80.0, 104.0),
      episodios)
episodios = pr.agrupar_episodios_ponte([janela(216, 232)])
check("janela única é preservada", len(episodios) == 1
      and episodios[0]["n_janelas"] == 1
      and (episodios[0]["inicio_s"], episodios[0]["fim_s"]) == (216.0, 232.0),
      episodios)
episodios = pr.agrupar_episodios_ponte([janela(80, 104), janela(216, 232)])
check("gap real cria dois episódios", len(episodios) == 2, episodios)


print("\n[4] Fixture determinística das 13 janelas do laboratório")
fixture = [
    (80, 96), (88, 104),
    (216, 232),
    (256, 272), (264, 280), (280, 296), (288, 304),
    (296, 312), (304, 320), (312, 328),
    (360, 376), (368, 384),
    (416, 432),
]
episodios = pr.agrupar_episodios_ponte([janela(a, b) for a, b in fixture])
intervalos = [(e["inicio_s"], e["fim_s"]) for e in episodios]
check("13 janelas viram 5 episódios", len(episodios) == 5, episodios)
check("episódios reproduzem o resultado físico auditado", intervalos == [
    (80.0, 104.0),
    (216.0, 232.0),
    (256.0, 328.0),
    (360.0, 384.0),
    (416.0, 432.0),
], intervalos)


print("\n[5] Detecção usa grade RAW e devolve só positivos")
vistas = []


def fake_vlm(frames):
    vistas.append(list(frames))
    return resposta(frames[0] == "B")


positivos = pr.detectar_janelas_ponte(
    "cam1.mp4",
    8.0,
    24.1,
    chamar_vlm=fake_vlm,
    extrair_frames=lambda caminho, intervalo, duracao: [
        (0, "A"), (8, "B"), (16, "C"), (24, "D")
    ],
)
check("todas as janelas foram analisadas na ordem", vistas == [
    ["A", "B", "C"], ["B", "C", "D"], ["C", "D"], ["D"]
], vistas)
check("apenas a janela true é emitida", len(positivos) == 1
      and (positivos[0]["inicio_s"], positivos[0]["fim_s"]) == (8.0, 24.0),
      positivos)


print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
