"""Regressões da observação independente OPERANDO_PONTE_ROLANTE.

Executar: python -X utf8 tests_ponte_rolante.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

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


print("\n[6] Persistência paralela e neutra para produto/Lean")


class _TabelaFake:
    def __init__(self, banco, nome):
        self.banco = banco
        self.nome = nome

    def insert(self, linhas):
        self.banco.inserts.append((self.nome, linhas))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _BancoFake:
    def __init__(self):
        self.inserts = []

    def table(self, nome):
        return _TabelaFake(self, nome)


banco = _BancoFake()
n_persistidos = pr.persistir_episodios_ponte(
    banco,
    "video-1",
    "União",
    "Torneamento Convencional",
    episodios[:2],
)
check("persiste um registro por episódio", n_persistidos == 2, banco.inserts)
check("reutiliza somente a tabela eventos", [nome for nome, _ in banco.inserts] == ["eventos"],
      banco.inserts)
linhas = banco.inserts[0][1]
check("track sintético -6 identifica o sistema, não uma pessoa",
      all(l["pessoa_track_id"] == -6 and l["papel_pessoa"] is None for l in linhas),
      linhas)
check("episódio não disputa principal nem entra na fila",
      all(l["principal"] is False and l["validado_humano"] is True for l in linhas), linhas)
check("categoria Lean e origem ficam nulas",
      all(l["categoria_lean"] is None and l["categoria_lean_origem"] is None for l in linhas),
      linhas)
check("evidência mínima permanece na descrição",
      "evidências visuais" in linhas[0]["descricao_bruta"], linhas[0])


print("\n[7] Flag segura e ponto de integração")
flag_anterior = os.environ.pop("KV_PONTE_ROLANTE", None)
try:
    check("flag nasce desligada", pr.ponte_rolante_habilitada() is False)
    os.environ["KV_PONTE_ROLANTE"] = "on"
    check("ativação é deliberada", pr.ponte_rolante_habilitada() is True)
finally:
    if flag_anterior is None:
        os.environ.pop("KV_PONTE_ROLANTE", None)
    else:
        os.environ["KV_PONTE_ROLANTE"] = flag_anterior

src_pipeline = Path("backend/pipeline.py").read_text(encoding="utf-8")
persistencia_normal = src_pipeline.index("video_id, n_auto, ids_principais = etapa_persistir(")
persistencia_ponte = src_pipeline.index("n_eventos_ponte = persistir_episodios_ponte(")
retencao = src_pipeline.index("frames_stats = {\"ok\": False}", persistencia_ponte)
check("ponte roda depois da persistência normal e antes da retenção do vídeo",
      persistencia_normal < persistencia_ponte < retencao)
check("integração restringe o passe à CAM1",
      'str(cam_id or "cam1").strip().lower() == "cam1"' in src_pipeline)

env_exemplo = Path("backend/.env.example").read_text(encoding="utf-8")
check("uma única flag default off está documentada",
      env_exemplo.count("KV_PONTE_ROLANTE") == 1 and "# KV_PONTE_ROLANTE=off" in env_exemplo)


print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
