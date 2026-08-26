"""Fase 111C — identidade lógica, costura residual e releitura shadow."""
from copy import deepcopy
from datetime import datetime, timezone
import os
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


def hist(indice=0):
    v = [0.0, 0.0, 0.0, 0.0]
    v[indice] = 1.0
    return v


def razao(med, *, mad=0.01, n=10):
    return {"ombro_tronco": {"med": med, "mad": mad, "n": n}}


def desc(
    track, ini, fim, posto, obs, *, cam="cam1", p_ini=(0.20, 0.50, 0.30),
    p_fim=(0.30, 0.50, 0.30), cor=0, n_cor=5, razoes=None,
    visivel=None, aspecto=0.40, **extras,
):
    d = {
        "pessoa_track_id": track,
        "cam_id": cam,
        "t_ini_s": ini,
        "t_fim_s": fim,
        "bbox_ini": list(p_ini),
        "bbox_fim": list(p_fim),
        "tempo_posto_s": posto,
        "tempo_visivel_s": (fim - ini) if visivel is None else visivel,
        "n_amostras": max(obs, round(((fim - ini) or 1) / 5)),
        "n_amostras_posto": obs,
        "hist_sup": hist(cor),
        "hist_inf": hist(cor),
        "hist_bins": {"n_sup": n_cor, "n_inf": n_cor},
        "razoes": razoes,
        "altura_rel": p_fim[2],
        "aspecto": aspecto,
    }
    d.update(extras)
    return d


print("[1] Costura residual conservadora")
t4 = desc(4, 0, 50, 50, 10, p_fim=(0.30, 0.50, 0.30))
t9 = desc(9, 52, 120, 65, 13, p_ini=(0.34, 0.50, 0.30), p_fim=(0.45, 0.50, 0.30))
ids = pl.construir_identidades_logicas_segmento([t4, t9])
check("fragmentos coerentes viram uma identidade", len(ids) == 1, ids)
r1 = ids[0]
check("preserva mapping e representante determinístico", r1["track_ids"] == [4, 9] and r1["track_representante"] == 4, r1)
check("soma tempo no posto = 115s", r1["tempo_posto_s"] == 115.0, r1)
check("soma observações e visibilidade", r1["n_amostras_posto"] == 23 and r1["tempo_visivel_s"] == 118.0, r1)
check("consolida janela temporal", r1["t_ini_s"] == 0.0 and r1["t_fim_s"] == 120.0, r1)
check("expõe evidência da ligação", len(r1["evidencias_costura"]) == 1 and r1["confianca_costura"] == "alta", r1)

overlap = pl.construir_identidades_logicas_segmento([
    desc(4, 0, 100, 80, 16),
    desc(9, 40, 120, 70, 14, p_ini=(0.31, 0.50, 0.30)),
])
check("tracks simultâneos nunca são fundidos", len(overlap) == 2, overlap)

teleporte = pl.construir_identidades_logicas_segmento([
    desc(4, 0, 50, 40, 8, p_fim=(0.05, 0.50, 0.30)),
    desc(9, 52, 120, 60, 12, p_ini=(0.95, 0.50, 0.30)),
])
check("teleporte espacial veta a costura", len(teleporte) == 2, teleporte)

cor_incompativel = pl.construir_identidades_logicas_segmento([
    desc(4, 0, 50, 40, 8, cor=0, p_fim=(0.30, 0.50, 0.30)),
    desc(9, 52, 120, 60, 12, cor=1, p_ini=(0.34, 0.50, 0.30)),
])
check("cor fortemente incompatível e bem medida é veto", len(cor_incompativel) == 2, cor_incompativel)

so_cor = pl.construir_identidades_logicas_segmento([
    desc(4, 0, 50, 40, 8, cor=0),
    desc(9, 80, 120, 40, 8, cor=0, p_ini=(0.31, 0.50, 0.30)),
])
check("cor semelhante sozinha não vence gap excessivo", len(so_cor) == 2, so_cor)

sem_razoes = pl.construir_identidades_logicas_segmento([
    desc(4, 0, 50, 40, 8, razoes=None, p_fim=(0.30, 0.50, 0.30)),
    desc(9, 52, 120, 60, 12, razoes=None, p_ini=(0.34, 0.50, 0.30)),
])
check("razões ausentes são neutras e não quebram", len(sem_razoes) == 1, sem_razoes)

print("\n[2] Transitividade, fronteiras e câmeras")
a = desc(1, 0, 10, 10, 2, razoes=razao(0.50, mad=0.002), p_fim=(0.25, 0.50, 0.30))
b = desc(2, 12, 22, 10, 2, razoes=razao(0.61, mad=0.002), p_ini=(0.27, 0.50, 0.30), p_fim=(0.35, 0.50, 0.30))
c = desc(3, 24, 34, 10, 2, razoes=razao(0.75, mad=0.002), p_ini=(0.37, 0.50, 0.30))
transitivo = pl.construir_identidades_logicas_segmento([a, b, c])
check("A~B e B~C não criam A~C por transitividade cega", [i["track_ids"] for i in transitivo] == [[1, 2], [3]], transitivo)

bifurcacao = pl.construir_identidades_logicas_segmento([
    desc(10, 0, 50, 40, 8, p_fim=(0.30, 0.50, 0.30)),
    desc(11, 52, 100, 40, 8, p_ini=(0.34, 0.50, 0.30)),
    desc(12, 52, 100, 40, 8, p_ini=(0.35, 0.50, 0.30)),
])
check("um predecessor com dois sucessores simultâneos não escolhe por track_id",
      len(bifurcacao) == 3, bifurcacao)

# Caso adversarial: o último fragmento isolado não aprova nenhum sucessor,
# mas uma evidência anterior do grupo torna ambos elegíveis. A decisão precisa
# usar o mesmo grupo do merge real e recusar a bifurcação inteira.
p = desc(20, 0, 10, 10, 2, razoes=razao(0.50), p_fim=(0.25, 0.50, 0.30))
a_grupo = desc(21, 12, 22, 10, 2, razoes=razao(0.50),
               p_ini=(0.27, 0.50, 0.30), p_fim=(0.35, 0.50, 0.30))
a_grupo["hist_sup"] = a_grupo["hist_inf"] = [0.5, 0.5, 0.0, 0.0]
b_grupo = desc(22, 24, 34, 10, 2, p_ini=(0.37, 0.50, 0.30))
c_grupo = desc(23, 24, 34, 10, 2, p_ini=(0.38, 0.50, 0.30))
bifurcacao_grupo = pl.construir_identidades_logicas_segmento([
    p, a_grupo, b_grupo, c_grupo,
])
check("bifurcação avaliada contra o grupo inteiro permanece sem escolha",
      [i["track_ids"] for i in bifurcacao_grupo] == [[20, 21], [22], [23]],
      bifurcacao_grupo)

# O lote precisa fechar o componente inteiro de sobreposição. D não coincide
# diretamente com B, mas coincide com C; portanto não pode escapar para o lote
# seguinte e ser escolhido pelo predecessor comum A.
bifurcacao_encadeada = pl.construir_identidades_logicas_segmento([
    desc(30, 0, 50, 40, 8, p_fim=(0.30, 0.50, 0.30)),
    desc(31, 52, 53, 1, 1, p_ini=(0.32, 0.50, 0.30),
         p_fim=(0.90, 0.50, 0.30)),
    desc(32, 52.5, 54, 1.5, 1, p_ini=(0.33, 0.50, 0.30),
         p_fim=(0.90, 0.50, 0.30)),
    desc(33, 53.5, 60, 6.5, 1, p_ini=(0.34, 0.50, 0.30)),
])
check("cadeia de sobreposição inteira bloqueia escolha tardia do predecessor",
      [i["track_ids"] for i in bifurcacao_encadeada]
      == [[30], [31], [32], [33]], bifurcacao_encadeada)

gap_limite = pl.construir_identidades_logicas_segmento([
    desc(1, 0, 50, 40, 8, p_fim=(0.30, 0.50, 0.30)),
    desc(2, 56, 100, 40, 8, p_ini=(0.34, 0.50, 0.30)),
])
gap_fora = pl.construir_identidades_logicas_segmento([
    desc(1, 0, 50, 40, 8, p_fim=(0.30, 0.50, 0.30)),
    desc(2, 56.01, 100, 40, 8, p_ini=(0.34, 0.50, 0.30)),
])
check("gap de 6s passa e 6,01s falha", len(gap_limite) == 1 and len(gap_fora) == 2, (gap_limite, gap_fora))

mesmo_id_duas_cams = pl.construir_identidades_logicas_segmento([
    desc(4, 0, 100, 90, 18, cam="cam1"),
    desc(4, 0, 100, 90, 18, cam="cam2"),
])
check("track 4 cam1 e track 4 cam2 permanecem universos separados", len(mesmo_id_duas_cams) == 2 and {i["cam_id"] for i in mesmo_id_duas_cams} == {"cam1", "cam2"}, mesmo_id_duas_cams)
check("R1 pode repetir apenas porque cam_id explicita o universo", all(i["identidade_logica"] == "R1" for i in mesmo_id_duas_cams), mesmo_id_duas_cams)

print("\n[3] 111B RAW versus identidades lógicas")
fragmentos = [
    desc(4, 0, 50, 50, 10, p_fim=(0.30, 0.50, 0.30)),
    desc(9, 52, 100, 45, 9, p_ini=(0.34, 0.50, 0.30)),
]
raw = pl.eleger_operador_segmento(fragmentos)
logicas = pl.construir_identidades_logicas_segmento(fragmentos)
consolidada = pl.eleger_operador_segmento(logicas)
check("fragmentos RAW abaixo do piso ficam indefinidos", raw["status"] == "indefinido", raw)
check("grupo agregado de 95s confirma operador lógico", consolidada["status"] == "confirmado" and consolidada["track_id"] == 4, consolidada)
check("mapping resolve vencedor lógico para tracks reais", next(i for i in logicas if i["pessoa_track_id"] == consolidada["track_id"])["track_ids"] == [4, 9])

quase_empate = pl.eleger_operador_segmento([
    {**desc(1, 0, 120, 120, 24), "identidade_logica": "R1", "track_ids": [1]},
    {**desc(2, 0, 110, 110, 22), "identidade_logica": "R2", "track_ids": [2]},
])
check("quase empate lógico continua indefinido", quase_empate["status"] == "indefinido" and quase_empate["motivo"] == "dominancia_ambigua", quase_empate)

nunca_entra = pl.eleger_operador_segmento(
    pl.construir_identidades_logicas_segmento([
        desc(1, 0, 280, 0, 0, visivel=280),
        desc(2, 0, 250, 0, 0, visivel=250),
    ])
)
check("visíveis sem entrar continuam indefinidos", nunca_entra["status"] == "indefinido" and nunca_entra["motivo"] == "sem_evidencia_posto", nunca_entra)

print("\n[4] Releitura diagnóstica da janela")
identidades_fora_dentro = pl.construir_identidades_logicas_segmento([
    desc(4, 0, 5, 0, 0, visivel=10, p_fim=(0.30, 0.50, 0.30)),
    desc(9, 7, 20, 65, 13, visivel=13, p_ini=(0.34, 0.50, 0.30)),
])
identidade = identidades_fora_dentro[0]
check("fragmento somente fora é costurado ao dominante posterior",
      len(identidades_fora_dentro) == 1 and identidade["track_ids"] == [4, 9],
      identidades_fora_dentro)
observacoes = [
    {"cam_id": "cam1", "tempo_s": 0, "medido": True, "tracks": {4: "fora"}},
    {"cam_id": "cam1", "tempo_s": 5, "medido": True, "tracks": {4: "fora"}},
    {"cam_id": "cam1", "tempo_s": 10, "medido": True, "tracks": {}},
    {"cam_id": "cam1", "tempo_s": 15, "medido": True, "tracks": {9: "dentro"}},
]
timeline = pl.construir_timeline_identidade_segmento(observacoes, identidade, 20)
check("timeline fica disponível após identidade conhecida", timeline["status"] == "disponivel", timeline)
check("fora inicial vira apenas candidato shadow a operador_fora", timeline["intervalos"][0]["estado"] == "fora_posto_candidato" and timeline["intervalos"][0]["leitura_shadow"] == "seria_operador_fora", timeline)
check("lacuna não observada não é inventada como oclusão", timeline["intervalos"][1]["estado"] == "nao_observado" and timeline["intervalos"][1]["motivo"] == "identidade_nao_observada", timeline)
check("fragmento posterior do mesmo R1 aparece dentro", timeline["intervalos"][2]["estado"] == "no_posto" and timeline["intervalos"][2]["track_ids_observados"] == [9], timeline)
check("intervalos comprimidos cobrem a janela amostral", [(i["t_ini_s"], i["t_fim_s"]) for i in timeline["intervalos"]] == [(0.0, 10.0), (10.0, 15.0), (15.0, 20.0)], timeline)

conflito = pl.construir_timeline_identidade_segmento([
    {"cam_id": "cam1", "tempo_s": 0, "medido": True, "tracks": {4: "fora", 9: "dentro"}},
], identidade, 5)
check("conflito dentro/fora falha fechado", conflito["intervalos"][0]["estado"] == "nao_observado" and conflito["intervalos"][0]["motivo"] == "conflito_estado", conflito)

print("\n[5] Feature flag e invariância do shadow")

class LogFake:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []

    def info(self, *args):
        self.info_calls.append(args)

    def warning(self, *args):
        self.warning_calls.append(args)


modo_antigo, log_antigo = pl._OPERADOR_SEGMENTO_MODO, pl.log
construir_real = pl.construir_identidades_logicas_segmento
eleger_real = pl.eleger_operador_segmento
timeline_real = pl.construir_timeline_identidade_segmento
try:
    pl._OPERADOR_SEGMENTO_MODO = "off"
    fake = LogFake()
    pl.log = fake
    chamadas = {"construir": 0, "eleger": 0, "timeline": 0}

    def nao_construir(*_a, **_k):
        chamadas["construir"] += 1
        return []

    def nao_eleger(*_a, **_k):
        chamadas["eleger"] += 1
        return {}

    def nao_timeline(*_a, **_k):
        chamadas["timeline"] += 1
        return {}

    pl.construir_identidades_logicas_segmento = nao_construir
    pl.eleger_operador_segmento = nao_eleger
    pl.construir_timeline_identidade_segmento = nao_timeline
    off = pl._registrar_identidades_segmento_sombra(
        {"descritores": fragmentos, "observacoes": observacoes}, ["cam1"],
        duracao_s=20,
    )
    check("off retorna antes de toda computação 111C", off == [] and not any(chamadas.values()), chamadas)
    check("off não gera log 111C", fake.info_calls == [] and fake.warning_calls == [])
finally:
    pl.construir_identidades_logicas_segmento = construir_real
    pl.eleger_operador_segmento = eleger_real
    pl.construir_timeline_identidade_segmento = timeline_real
    pl._OPERADOR_SEGMENTO_MODO = modo_antigo
    pl.log = log_antigo

agora = datetime(2026, 8, 17, 12, 5, tzinfo=timezone.utc)
eventos = [{
    "video_id": "v1", "papel_pessoa": "operador", "principal": False,
    "versao_instrumento": 10, "tempo_inicio_s": 0, "tempo_fim_s": 60,
    "maos_maquina": True, "_capturado_em": agora, "_dia": "2026-08-17",
    "_cam_id": "cam1", "n_amostras": 20,
}]
dados = {"descritores": deepcopy(fragmentos), "observacoes": deepcopy(observacoes)}
dados_antes, eventos_antes = deepcopy(dados), deepcopy(eventos)
kpi_antes = prod.agregar_produtividade(eventos, agora=agora)
modo_antigo, log_antigo = pl._OPERADOR_SEGMENTO_MODO, pl.log
try:
    pl._OPERADOR_SEGMENTO_MODO = "sombra"
    fake = LogFake()
    pl.log = fake
    saida = pl._registrar_identidades_segmento_sombra(
        dados, ["cam1"], duracao_s=20,
    )
finally:
    pl._OPERADOR_SEGMENTO_MODO = modo_antigo
    pl.log = log_antigo
kpi_depois = prod.agregar_produtividade(eventos, agora=agora)
check("shadow produz identidade, decisão e timeline em memória", len(saida) == 1 and saida[0]["decisao"]["identidade_logica"] == "R1" and saida[0]["timeline"]["status"] == "disponivel", saida)
check("shadow não muta coletor/descritores", dados == dados_antes)
check("shadow não altera evento nem papel", eventos == eventos_antes)
check("shadow não altera presença/produtividade/KPI", kpi_antes == kpi_depois)
check("logs são por câmera, nunca por frame", len(fake.info_calls) == 3, fake.info_calls)

fonte = open("backend/pipeline.py", encoding="utf-8").read()
i_det = fonte.index("def etapa_detectar_e_amostrar(")
i_coleta = fonte.index("acumular_descritor(\n                                    desc_acc_identidade", i_det)
i_descarte = fonte.index('if papel_z != "posto_operador":', i_coleta)
i_proc = fonte.index("def processar_video(")
i_raw = fonte.index("_registrar_operador_segmento_sombra(", i_proc)
i_logica = fonte.index("_registrar_identidades_segmento_sombra(", i_raw)
i_vlm = fonte.index("observacoes = etapa_analise_vlm(", i_logica)
check("coletor shadow captura o track antes do descarte externo", i_coleta < i_descarte)
check("111B RAW roda antes da 111C consolidada", i_raw < i_logica)
check("111C inteira roda antes de VLM/eventos", i_logica < i_vlm)
check("coletor paralelo nunca substitui descritores_track persistidos",
      "descritores_track=descritores_track" in fonte[i_vlm:])
check("IDs opcionais são normalizados na cópia exclusivamente diagnóstica",
      'cam_primaria_efetiva = str(cam_id or "cam1")' in fonte[i_proc:i_raw]
      and 'cam_secundaria_efetiva = str(cam_id_secundario or "cam2")' in fonte[i_proc:i_raw]
      and '{**d, "cam_id": cam_secundaria_efetiva}' in fonte[i_proc:i_raw])

print(f"\n{'=' * 64}\n  {ok} ok · {fail} falha(s)\n{'=' * 64}")
sys.exit(1 if fail else 0)
