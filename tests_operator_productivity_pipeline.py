"""Identidade funcional e sinais do operador atravessando o pipeline."""
import json
import os
import sys
import types


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for modulo in [
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(modulo, types.ModuleType(modulo))
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


def pessoa(tid, rotulo, papel, bbox):
    return {
        "track_id": tid, "rotulo": rotulo, "papel": papel,
        "zona": "posto", "zona_desc": "posto do torno", "bbox": bbox,
        "kpts": None, "crop": None, "maos_maquina": False,
        "orientacao": None,
    }


def amostra(pessoas):
    a = pl.Amostra(
        frame_idx=0, tempo_s=0.0, img_b64="IMG", pessoas=pessoas,
        dim=(640, 480),
    )
    a.img_b64_secundario = None
    return a


print("[1] P1/P2 são candidatos; o VLM escolhe o ocupante")
a = amostra([
    pessoa(1, "P1", "operador", (0, 0, 300, 470)),
    pessoa(2, "P2", "visitante", (320, 80, 500, 450)),
])
original_call = pl.groq_vision_call
try:
    pl.groq_vision_call = lambda *args, **kwargs: json.dumps({
        "trechos": [{
            "i": 0,
            "operador_estado": "identificado",
            "operador": "P2",
            "acoes": {"P1": "conversando ao lado", "P2": "mãos no torno"},
            "imovel": False,
            "trabalho": True,
            "motivo": "maos_no_torno",
        }]
    })
    saida = pl._analisar_sequencia_vlm(
        object(), [a], "", {}, 5.0, frente_maquina=None
    )[0]
    check("maior bbox P1 não vence por hábito", saida["operador_track_id"] == 2, saida)
    check("produtividade pertence ao escolhido", saida["trabalho"] is True, saida)

    pl.groq_vision_call = lambda *args, **kwargs: json.dumps({
        "trechos": [{
            "i": 0, "operador_estado": "identificado", "operador": "P2",
            "acoes": {"P1": "ao lado", "P2": "de costas"},
            "trabalho": "false", "motivo": "costas_ou_lado",
        }]
    })
    invalida = pl._analisar_sequencia_vlm(object(), [a], "", {}, 5.0)[0]
    check("string 'false' é rejeitada", invalida["trabalho"] is None, invalida)

    pl.groq_vision_call = lambda *args, **kwargs: json.dumps({
        "trechos": [{
            "i": 0, "operador_estado": "identificado", "operador": "P1",
            "acoes": {"P1": "de costas conversando", "P2": "ao lado"},
            "trabalho": True, "motivo": "conversa_ou_celular",
        }]
    })
    contraditoria = pl._analisar_sequencia_vlm(object(), [a], "", {}, 5.0)[0]
    check("booleano contrário ao motivo não vira produtividade",
          contraditoria["trabalho"] is None, contraditoria)

    pl.groq_vision_call = lambda *args, **kwargs: json.dumps({
        "trechos": [{
            "i": 0, "operador_estado": "identificado", "operador": "P1",
            "acoes": {}, "trabalho": True, "motivo": "maos_no_torno",
        }]
    })
    sem_auditoria = pl._analisar_sequencia_vlm(object(), [a], "", {}, 5.0)[0]
    check("decisão sem descrição do operador se abstém",
          sem_auditoria["trabalho"] is None, sem_auditoria)

    pl.groq_vision_call = lambda *args, **kwargs: json.dumps({
        "trechos": [
            {"i": 0, "operador_estado": "identificado", "operador": "P1",
             "acoes": {"P1": "no torno", "P2": "ao lado"}, "trabalho": True},
            {"i": 0, "operador_estado": "identificado", "operador": "P2",
             "acoes": {"P1": "ao lado", "P2": "no torno"}, "trabalho": True},
        ]
    })
    duplicada = pl._analisar_sequencia_vlm(object(), [a], "", {}, 5.0)[0]
    check("índice duplicado vira identidade incerta",
          duplicada["operador_estado"] == "incerto"
          and duplicada["trabalho"] is None, duplicada)
finally:
    pl.groq_vision_call = original_call

print("\n[1f] Confirmação por duas câmeras preserva o terceiro estado")
sem_medida = amostra([])
sem_medida.op_cam2 = None
negado = amostra([])
negado.op_cam2 = False
confirmado = amostra([])
confirmado.op_cam2 = True
stats_tri = pl.etapa_confirmar_operador(
    [sem_medida, negado, confirmado], "dupla"
)
check("cam2 não medida permanece inconclusiva",
      sem_medida.operador_presente is None, sem_medida.operador_presente)
check("cam2 medida sem pessoa confirma vazio",
      negado.operador_presente is False, negado.operador_presente)
check("cam2 medida com pessoa abre resgate",
      confirmado.operador_presente is True, confirmado.operador_presente)
check("estatística não soma não-medido como vazio",
      stats_tri["vazios"] == 1 and stats_tri["inconclusivos"] == 1,
      stats_tri)

obs_sem_medida = pl.etapa_analise_vlm(
    object(), [sem_medida], "torno", {}, lambda *a, **k: None,
    zona_posto="posto", intervalo_s=5.0,
)
obs_negado = pl.etapa_analise_vlm(
    object(), [negado], "torno", {}, lambda *a, **k: None,
    zona_posto="posto", intervalo_s=5.0,
)
check("não-medido persiste como papel indefinido",
      len(obs_sem_medida) == 1 and obs_sem_medida[0]["papel"] is None,
      obs_sem_medida)
check("negação medida persiste como posto vazio",
      len(obs_negado) == 1 and obs_negado[0]["papel"] == "posto_vazio",
      obs_negado)

candidato_com_negativa = amostra([
    pessoa(1, "P1", None, (0, 0, 300, 470))
])
candidato_com_negativa.op_cam2 = False
candidato_com_negativa.img_b64_secundario = "IMG-CAM2"
ctx_negativa = pl._contexto_zonas(
    candidato_com_negativa, False, identidade_em_aberto=True
)
check("negativa lateral força desambiguação com o segundo ângulo",
      pl._cam2_ajuda([candidato_com_negativa])
      and "não detectou pessoa" in ctx_negativa,
      ctx_negativa)

print("\n[1b] Abstenção não ressuscita a heurística")
original_seq = pl._analisar_sequencia_vlm
try:
    pl._analisar_sequencia_vlm = lambda *args, **kwargs: {
        0: {
            "acoes": {1: "de costas, conversando"},
            "operador_estado": "incerto",
            "operador_track_id": None,
            "trabalho": None,
            "produtividade_motivo": "sem_leitura",
            "maquina": None,
            "imovel": None,
        }
    }
    unico = amostra([pessoa(1, "P1", "operador", (0, 0, 300, 470))])
    observadas = pl.etapa_analise_vlm(
        object(), [unico], "torno", {}, lambda *a, **k: None,
        zona_posto="posto", intervalo_s=5.0,
    )
    check("candidato único incerto continua sem papel e sem produtividade",
          len(observadas) == 1
          and observadas[0]["papel"] is None
          and observadas[0]["trabalho"] is None,
          observadas)
finally:
    pl._analisar_sequencia_vlm = original_seq

print("\n[1c] Orientação só entra depois da calibração")
orientada = amostra([pessoa(1, "P1", None, (0, 0, 300, 470))])
orientada.pessoas[0]["orientacao"] = "frente"
orientacao_antiga = os.environ.get("KV_ORIENTACAO_VERIFICADA")
try:
    os.environ["KV_ORIENTACAO_VERIFICADA"] = "off"
    ctx_off = pl._contexto_zonas(
        orientada, False, frente_maquina="camera", identidade_em_aberto=True
    )
    os.environ["KV_ORIENTACAO_VERIFICADA"] = "on"
    ctx_on = pl._contexto_zonas(
        orientada, False, frente_maquina="camera", identidade_em_aberto=True
    )
    check("flag off não injeta pose no julgamento", "DE FRENTE" not in ctx_off, ctx_off)
    check("flag on libera o fato calibrado", "DE FRENTE" in ctx_on, ctx_on)
finally:
    if orientacao_antiga is None:
        os.environ.pop("KV_ORIENTACAO_VERIFICADA", None)
    else:
        os.environ["KV_ORIENTACAO_VERIFICADA"] = orientacao_antiga

print("\n[1d] Descrição V9 vem do mesmo quadro que decidiu produtividade")
original_seq = pl._analisar_sequencia_vlm
original_gate = pl._GATE_ENABLE
original_distancia = pl._gate_distancia
try:
    chamadas = [
        {0: {
            "acoes": {1: "mãos no torno"},
            "operador_estado": "identificado", "operador_track_id": 1,
            "trabalho": True, "produtividade_motivo": "maos_no_torno",
            "maquina": None, "imovel": False,
        }},
        {0: {
            "acoes": {1: "de costas conversando"},
            "operador_estado": "identificado", "operador_track_id": 1,
            "trabalho": False, "produtividade_motivo": "conversa_ou_celular",
            "maquina": None, "imovel": False,
        }},
    ]
    pl._analisar_sequencia_vlm = lambda *args, **kwargs: chamadas.pop(0)
    pl._GATE_ENABLE = True
    pl._gate_distancia = lambda *args, **kwargs: 0.0
    primeiro = amostra([pessoa(1, "P1", "operador", (0, 0, 300, 470))])
    segundo = amostra([pessoa(1, "P1", "operador", (0, 0, 300, 470))])
    segundo.frame_idx = 60
    segundo.tempo_s = 60.0
    observadas = pl.etapa_analise_vlm(
        object(), [primeiro, segundo], "torno", {}, lambda *a, **k: None,
        zona_posto="posto", intervalo_s=5.0,
    )
    check("gate não troca descrição fresca por âncora antiga",
          len(observadas) == 2
          and observadas[1]["descricao"] == "de costas conversando"
          and observadas[1]["trabalho"] is False
          and observadas[1]["origem_gate"] == "analisado",
          observadas)
finally:
    pl._analisar_sequencia_vlm = original_seq
    pl._GATE_ENABLE = original_gate
    pl._gate_distancia = original_distancia

print("\n[1e] Parser da câmera lateral também falha fechado")
original_call = pl.groq_vision_call
try:
    lateral = amostra([])
    lateral.img_b64_secundario = "IMG-CAM2"
    pl.groq_vision_call = lambda *args, **kwargs: json.dumps({
        "trechos": [
            {"i": 0, "operador_estado": "identificado", "acao": "mãos no torno",
             "trabalho": True, "motivo": "maos_no_torno"},
            {"i": 0, "operador_estado": "identificado", "acao": "de costas",
             "trabalho": False, "motivo": "costas_ou_lado"},
        ]
    })
    duplicada_cam2 = pl._analisar_sequencia_cam2(
        object(), [lateral], "torno", {}, 5.0, zona_desc="posto"
    )[0]
    check("índice duplicado da cam2 neutraliza identidade e decisão",
          duplicada_cam2["operador_estado"] == "incerto"
          and duplicada_cam2["trabalho"] is None,
          duplicada_cam2)
finally:
    pl.groq_vision_call = original_call

print("\n[2] Interpolação não fabrica identidade/produtividade")
blocos = {
    0: {"acoes": {1: "mãos no torno"}, "operador_estado": "identificado",
        "operador_track_id": 1, "trabalho": True},
    2: {"acoes": {1: "mãos no torno"}, "operador_estado": "identificado",
        "operador_track_id": 1, "trabalho": True},
}
interp = pl._interpolar_sequencia(blocos, [0, 1, 2])
check("buraco é coberto", interp == {1}, interp)
check("mas decisão é limpa",
      blocos[1]["operador_estado"] == "incerto"
      and blocos[1]["operador_track_id"] is None
      and blocos[1]["trabalho"] is None, blocos[1])

print("\n[3] Segmentação preserva a granularidade da decisão por frame")
def obs(t, trabalho):
    return {
        "tempo_s": t, "frame_idx": int(t), "track_id": 7,
        "descricao": "no torno", "bbox": [0, 0, 50, 100],
        "bbox_cam": "cam1", "bbox_dim": (640, 480), "zona": "posto",
        "papel": "operador", "origem_gate": "analisado",
        "maquina": None, "imovel": None, "trabalho": trabalho,
        "produtividade_observada": True,
    }

segmentados = pl.etapa_segmentar_eventos(
    [obs(0, True), obs(5, False)], lambda *a: "operar_torno", 5.0
)
check("mudança de produtividade quebra o evento",
      len(segmentados) == 2
      and [e["trabalho"] for e in segmentados] == [True, False],
      segmentados)
segmentados = pl.etapa_segmentar_eventos(
    [obs(0, True), obs(5, True), obs(10, False)], lambda *a: "operar_torno", 5.0
)
check("dois quadros produtivos e um improdutivo viram duas durações",
      len(segmentados) == 2
      and segmentados[0]["trabalho"] is True
      and segmentados[0]["tempo_fim_s"] - segmentados[0]["tempo_inicio_s"] == 10
      and segmentados[1]["trabalho"] is False
      and segmentados[1]["tempo_fim_s"] - segmentados[1]["tempo_inicio_s"] == 5,
      segmentados)

print("\n[4] Consolidação não mistura visitante com operador")
def cru(tid, papel, trabalho, maos, orientacao):
    return {
        "pessoa_track_id": tid, "comportamento_label": f"acao_{tid}",
        "descricao_bruta": f"pessoa {tid}", "tempo_inicio_s": 0,
        "tempo_fim_s": 60, "frame_inicio": 0, "frame_fim": 60,
        "bbox_inicio": [0, 0, 50, 100], "bbox_cam": "cam1",
        "bbox_stats": None, "zona_contexto": "posto",
        "papel_pessoa": papel, "n_amostras": 3, "n_observacoes": 3,
        "confianca": 1.0, "trabalho": trabalho,
        "maos_maquina": maos, "orientacao": orientacao,
    }

principal = pl.etapa_consolidar_principais([
    cru(1, "operador", False, None, "costas"),
    cru(2, "visitante", True, True, "frente"),
    cru(3, "visitante", True, True, "frente"),
], {}, 60.0)[0]
check("visitantes simultâneos não somam contra o operador",
      principal["papel_pessoa"] == "operador", principal)
check("mão do visitante não é do operador", principal["maos_maquina"] is None, principal)
check("orientação vem só do operador", principal["orientacao"] == "costas", principal)
check("trabalho vem só do operador", principal["trabalho"] is False, principal)

incerto = pl.etapa_consolidar_principais([
    cru(1, None, None, None, None),
], {}, 60.0)[0]
check("papel desconhecido permanece inconclusivo", incerto["papel_pessoa"] is None, incerto)

print("\n[5] Persistência e versão do instrumento")
fonte = open(os.path.join(os.path.dirname(__file__), "backend", "pipeline.py"), encoding="utf-8").read()
persistir = fonte.split("def etapa_persistir(", 1)[1].split("def ", 1)[0]
check("trabalho entra no payload V9 principal",
      '"trabalho": (' in persistir
      and 'if PRODUTIVIDADE_OPERADOR_V9 else None' in persistir)
check("instrumento foi versionado", pl.VERSAO_INSTRUMENTO >= 9, pl.VERSAO_INSTRUMENTO)
check("V9 tem kill switch desligado por padrão no código",
      'PRODUTIVIDADE_OPERADOR_V9 = _env_ligada("KV_PRODUTIVIDADE_OPERADOR_V9")' in fonte
      and pl._env_ligada("KV_FLAG_AUSENTE_PARA_TESTE") is False)
check("rollback conserva as regras e o prompt V8",
      "NÃO classifique o trabalho como produtivo ou improdutivo" in pl._REGRAS_DESCRICAO_V8
      and "Na dúvida, null — nunca chute true" in pl.PROMPT_VLM_SEQUENCIA_V8
      and "a câmera frontal não o enxerga nestes instantes" in pl.PROMPT_VLM_SEQUENCIA_CAM2_V8)
flag_teste_antiga = os.environ.get("KV_FLAG_TESTE_FAIL_CLOSED")
try:
    for valor in ("OFF", " FALSE ", "0", "", "talvez"):
        os.environ["KV_FLAG_TESTE_FAIL_CLOSED"] = valor
        check(f"flag '{valor}' não liga por acidente",
              pl._env_ligada("KV_FLAG_TESTE_FAIL_CLOSED") is False)
    os.environ["KV_FLAG_TESTE_FAIL_CLOSED"] = " ON "
    check("flag explícita liga após normalização",
          pl._env_ligada("KV_FLAG_TESTE_FAIL_CLOSED") is True)
finally:
    if flag_teste_antiga is None:
        os.environ.pop("KV_FLAG_TESTE_FAIL_CLOSED", None)
    else:
        os.environ["KV_FLAG_TESTE_FAIL_CLOSED"] = flag_teste_antiga
main_fonte = open(os.path.join(os.path.dirname(__file__), "backend", "main.py"), encoding="utf-8").read()
check("vitrine recusa instrumentos anteriores à V9",
      'int(_e.get("versao_instrumento") or 0) < 9' in main_fonte)
check("query comercial traz o piso de evidência persistido",
      '"n_amostras, versao_instrumento"' in main_fonte)

print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
