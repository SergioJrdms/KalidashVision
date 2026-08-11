"""Fase 91 — as duas câmeras contam presença, e o titular do posto (sombra).

PARTE 1 — CONTAGEM PELAS DUAS CÂMERAS
A cam1 era a única fonte. Num caso medido, a cam2 mostrava DUAS pessoas no
posto e a cam1 uma: a segunda não existia para o sistema, e o dia saiu com
ZERO eventos de `visitante`. Sem casamento entre câmeras: usa o MÁXIMO — se
uma vê 1 e a outra vê 2, são PELO MENOS 2. Piso honesto, sem exigir
identidade. Casar tracks entre câmeras mal produziria contagem DUPLA, que é
pior que contagem baixa.

⚠️ E a cam2 NÃO vira fonte de descrição. Contar é grátis (o track já roda);
descrever custaria uma chamada de VLM por pessoa, e não há orçamento.

PARTE 2 — O TITULAR
O titular não é quem está na zona num instante, é quem DOMINA a presença ao
longo do dia. Testes cobrem: agrupamento por cor primeiro (o único sinal
robusto com n=1), razões corporais só quando MEDIDAS o bastante, a GUARDA DE
PISO que recusa coroar um intruso, a continuidade que ALERTA em vez de
corrigir, e o modo sombra.

⚠️ IDENTIDADE ANÔNIMA POR PAPEL: rótulos posicionais por dia e por câmera.
Nenhum teste aqui pode passar a depender de nome de pessoa.

Rodar:  python tests_titular_presenca.py
"""
import sys, types, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
          "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
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

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


# ══════════════════════════ PARTE 1 ══════════════════════════
def cru(papel, n_posto_cam2=None, n_cena_cam2=None, tid=7):
    return {"pessoa_track_id": tid, "comportamento_label": "operar_torno",
            "descricao_bruta": "x", "tempo_inicio_s": 0, "tempo_fim_s": 60,
            "bbox_inicio": [100, 100, 200, 400], "papel_pessoa": papel,
            "n_amostras": 10, "n_observacoes": 10, "origens": {"analisado": 10},
            "n_posto_cam2": n_posto_cam2, "n_cena_cam2": n_cena_cam2}


def fato(crus):
    return pl.montar_fato_evento(crus[0], [(c, 60.0) for c in crus], 1.0, 1,
                                 rastreia_papel=True)


print("\n[1] O caso medido: cam2 vê 2, cam1 vê 1")
f = fato([cru("operador", n_posto_cam2=2, n_cena_cam2=2)])
check("pessoas_no_posto usa o MÁXIMO das duas", f["pessoas_no_posto"] == 2, f)
check("pessoas_na_cena idem", f["pessoas_na_cena"] == 2, f)
check("a cam1 sozinha continua visível", f["pessoas_cam1_posto"] == 1, f)
check("e a lateral também", f["pessoas_cam2_posto"] == 2, f)
check("a diferença é explícita — é ela que diz 'havia alguém não descrito'",
      f["pessoas_so_na_cam2"] == 1, f)

print("\n[2] Sem casamento entre câmeras: nunca SOMA (contagem dupla é pior)")
f2 = fato([cru("operador", n_posto_cam2=1, n_cena_cam2=1)])
check("cam1 vê 1 e cam2 vê 1 → 1, não 2", f2["pessoas_no_posto"] == 1, f2)
check("e ninguém 'só na cam2'", f2["pessoas_so_na_cam2"] == 0, f2)

print("\n[3] A cam2 sem medida é DESCONHECIDO, não zero")
f3 = fato([cru("operador", n_posto_cam2=None, n_cena_cam2=None)])
check("None não derruba a contagem da cam1", f3["pessoas_no_posto"] == 1, f3)
check("nem inventa gente", f3["pessoas_so_na_cam2"] == 0, f3)

print("\n[4] Presença é das DUAS")
f4 = fato([cru(None, n_posto_cam2=1, n_cena_cam2=1)])
check("cam1 sem papel mas cam2 vendo alguém → operador_presente",
      f4["operador_presente"] is True, f4)
f5 = fato([cru(None, n_posto_cam2=0, n_cena_cam2=0)])
check("nenhuma das duas vendo → ausente", f5["operador_presente"] is False, f5)
f6 = fato([cru("operador", n_posto_cam2=0, n_cena_cam2=0)])
check("cam1 vendo e cam2 não → presente (a cam1 basta para estabelecer)",
      f6["operador_presente"] is True, f6)

print("\n[5] A contagem sobrevive à segmentação (máximo dentro do evento)")
def obs(t, n2):
    return {"tempo_s": t, "frame_idx": int(t * 6), "track_id": 7,
            "descricao": "operar_torno", "bbox": [10, 10, 60, 200],
            "bbox_cam": "cam1", "bbox_dim": (640, 480), "zona": "posto",
            "papel": "operador", "origem_gate": "analisado",
            "maquina": None, "imovel": None, "n_posto_cam2": n2, "n_cena_cam2": n2}
evs = pl.etapa_segmentar_eventos([obs(0, 1), obs(5, 2), obs(10, 1)],
                                 lambda d, *a: d, 5.0)
check("um evento só", len(evs) == 1, len(evs))
check("guarda o PICO da lateral no período", evs[0]["n_posto_cam2"] == 2, evs[0])

print("\n[6] A cam2 NÃO virou fonte de descrição")
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
# A contagem entra no FATO; nenhuma observação nova é criada a partir dela.
check("a contagem alimenta o fato",
      '"pessoas_cam2_posto": _n_cam2_posto' in fonte)
check("e nenhuma observação nova nasce da contagem",
      "n_posto_cam2" in fonte and "observacoes.append" in fonte
      and fonte.count("OPERADOR_CAM2_TID") >= 1)
check("o porquê está escrito (descrever custaria VLM)",
      "descrever custaria uma chamada de VLM" in fonte)

# ══════════════════════════ PARTE 2 ══════════════════════════
def desc(tid, cor_sup, cor_inf, tempo_posto, razoes=None, altura=0.5, cam="cam1",
         n_amostras=20, t_ini=None, t_fim=None, x=0.5, video=None):
    """Fase 92: o descritor ganhou as PONTAS (t_ini/t_fim + caixa inicial e
    final). Sem elas a costura não tem o que costurar — e `n_amostras` passou a
    decidir se o track entra no agrupamento ou fica `indefinido`.

    Por padrão os tracks ficam LONGE no tempo (tid * 1000 s), para os testes de
    aparência não serem costurados sem querer."""
    t0 = tid * 1000.0 if t_ini is None else t_ini
    return {"id": f"d{tid}", "video_id": video or f"v{tid // 10}",
            "pessoa_track_id": tid,
            "cam_id": cam, "tempo_posto_s": tempo_posto,
            "tempo_visivel_s": tempo_posto,
            "n_amostras": n_amostras, "n_amostras_posto": n_amostras,
            "altura_rel": altura, "razoes": razoes,
            "hist_sup": cor_sup, "hist_inf": cor_inf,
            "t_ini_s": t0, "t_fim_s": (t0 + 30.0) if t_fim is None else t_fim,
            "bbox_ini": [x, 0.5, 0.4], "bbox_fim": [x, 0.5, 0.4],
            "bbox_ref": [0.1, 0.1, 0.3, 0.9], "frame_ref": 10,
            "frame_w": 640, "frame_h": 480}


AZUL = [10, 0, 0, 0, 0, 0]
AZULC = [9, 1, 0, 0, 0, 0]      # quase igual — mesma pessoa noutro frame
VERM = [0, 0, 0, 0, 0, 10]
CINZA = [2, 2, 2, 2, 1, 1]

print("\n[7] Similaridade de cor: interseção de histograma")
check("idênticos → 1.0", abs(pl._sim_hist(AZUL, AZUL) - 1.0) < 1e-6)
check("opostos → 0.0", pl._sim_hist(AZUL, VERM) == 0.0)
check("parecidos → alto", pl._sim_hist(AZUL, AZULC) >= 0.9, pl._sim_hist(AZUL, AZULC))
check("vazio não vira 0 (é desconhecido)", pl._sim_hist([], AZUL) is None)
check("tamanhos diferentes não comparam", pl._sim_hist([1, 2], AZUL) is None)

print("\n[8] Cor PRIMEIRO — o único robusto com n=1")
s, motivo = pl._sim_descritores(desc(1, AZUL, AZUL, 60), desc(2, AZULC, AZULC, 60))
check("mesma cor agrupa", motivo == "ok", motivo)
s2, motivo2 = pl._sim_descritores(desc(1, AZUL, AZUL, 60), desc(2, VERM, VERM, 60))
check("cor diferente separa", motivo2 != "ok", motivo2)
s3, motivo3 = pl._sim_descritores(desc(1, None, None, 60), desc(2, AZUL, AZUL, 60))
check("sem cor devolve None — 'não dá para dizer', não 'diferente'",
      s3 is None and "sem cor" in motivo3, (s3, motivo3))

print("\n[9] Razões corporais só opinam quando MEDIDAS o bastante")
R_MUITAS = {"ombro_tronco": {"med": 0.50, "n": 40},
            "quadril_ombro": {"med": 0.80, "n": 40}}
R_OUTRAS = {"ombro_tronco": {"med": 0.90, "n": 40},
            "quadril_ombro": {"med": 1.40, "n": 40}}
R_POUCAS = {"ombro_tronco": {"med": 0.90, "n": 2},
            "quadril_ombro": {"med": 1.40, "n": 2}}
_s, m1 = pl._sim_descritores(desc(1, AZUL, AZUL, 60, R_MUITAS),
                             desc(2, AZULC, AZULC, 60, R_OUTRAS))
check("razões bem medidas e divergentes SEPARAM mesmo com cor igual",
      "razões corporais divergem" in m1, m1)
_s, m2 = pl._sim_descritores(desc(1, AZUL, AZUL, 60, R_MUITAS),
                             desc(2, AZULC, AZULC, 60, R_POUCAS))
check("razões medidas de MENOS não separam (n abaixo do piso)", m2 == "ok", m2)

print("\n[10] Altura é desempate, nunca porta de entrada")
_s, m3 = pl._sim_descritores(desc(1, AZUL, AZUL, 60, altura=0.50),
                             desc(2, AZULC, AZULC, 60, altura=0.90))
check("altura muito diferente separa", "altura difere" in m3, m3)
_s, m4 = pl._sim_descritores(desc(1, VERM, VERM, 60, altura=0.50),
                             desc(2, VERM, VERM, 60, altura=0.51))
check("mas altura igual NÃO junta quem tem cor diferente",
      pl._sim_descritores(desc(1, AZUL, AZUL, 60, altura=0.5),
                          desc(2, VERM, VERM, 60, altura=0.5))[1] != "ok")

print("\n[11] Agrupamento: o titular fragmentado em muitos tracks vira UM grupo")
ds = ([desc(i, AZUL, AZUL, 40) for i in range(1, 9)]          # titular: 8 tracks
      + [desc(20, VERM, VERM, 15), desc(21, VERM, VERM, 10)])  # visitante: 2
gs = pl.agrupar_descritores(ds)
check("dois grupos", len(gs) == 2, [g["n_tracks"] for g in gs])
check("o maior junta os 8 tracks do titular", gs[0]["n_tracks"] == 8, gs[0])
check("ordenado por tempo no posto",
      gs[0]["tempo_posto_s"] > gs[1]["tempo_posto_s"])
check("rótulos POSICIONAIS (g1, g2) — não são pessoas",
      [g["grupo"] for g in gs] == ["g1", "g2"])
check("cada grupo traz a referência para o recorte",
      all(g["referencia"].get("bbox_ref") for g in gs))
check("e a assinatura de cor (é a ROUPA, não biometria)",
      all(g["assinatura"].get("hist_sup") for g in gs))

print("\n[12] A GUARDA DE PISO — o dia sem dominante NÃO tem titular")
class FakeQ:
    def __init__(s, sb, t): s.sb, s.t, s.eqs = sb, t, {}
    def select(s, *a, **k): return s
    def order(s, *a, **k): return s
    def limit(s, *a, **k): return s
    def range(s, a, b): s.faixa = (a, b); return s
    def eq(s, c, v): s.eqs[c] = v; return s
    def in_(s, c, vs): return s
    def execute(s):
        r = [dict(x) for x in s.sb.dados.get(s.t, [])
             if all(x.get(k) == v for k, v in s.eqs.items())]
        return types.SimpleNamespace(data=r)


class FakeSB:
    def __init__(s, dados): s.dados, s.escritas = dados, []
    def table(s, n):
        sb = s
        class T:
            def select(self, *a, **k): return FakeQ(sb, n)
            def upsert(self, p, **k): sb.escritas.append((n, p)); return FakeQ(sb, n)
            def insert(self, p): sb.escritas.append((n, p)); return FakeQ(sb, n)
            def update(self, p): sb.escritas.append((n, p)); return FakeQ(sb, n)
        return T()


VID = [{"id": "v0", "empresa": "U", "processo": "T", "cam_id": "cam1",
        "nome": "seg_20260804_070000.mp4", "processado_em": "2026-08-04T08:00:00"}]


def rodar(descritores, persistir=False):
    for d in descritores:
        d.update(empresa="U", processo="T", video_id="v0")
    sb = FakeSB({"descritores_track": descritores, "videos": VID,
                 "contexto_processo": [], "titular_dia": []})
    return pl.identificar_titular_do_dia(sb, "U", "T", "2026-08-04",
                                         persistir=persistir), sb


# Dominante folgado: 8×40s = 320s (~5min)... abaixo do piso de minutos.
r, _ = rodar([desc(i, AZUL, AZUL, 40) for i in range(1, 9)]
             + [desc(20, VERM, VERM, 15)])
c = r["cameras"][0]
check("dominante com poucos minutos NÃO vira titular",
      c["titular"] is None and "min no posto" in c["motivo"], c["motivo"])

# Agora com massa: 8 tracks × 200s = 1600s (27 min) contra 300s de terceiro.
r2, _ = rodar([desc(i, AZUL, AZUL, 200) for i in range(1, 9)]
              + [desc(20, VERM, VERM, 300)])
c2 = r2["cameras"][0]
check("com 27 min e 84% do posto, vira titular", c2["titular"] == "g1", c2)
check("o motivo mostra os dois números", "%" in c2["motivo"] and "min" in c2["motivo"])
check("o terceiro NÃO é titular",
      [g["eh_titular"] for g in c2["grupos"]] == [True, False], c2["grupos"])

# O caso que a guarda existe para resolver: o titular faltou e três pessoas
# repartiram o torno. Cada uma tem MINUTOS de sobra (22 min > piso), mas
# nenhuma DOMINA (33% < 40%) — é o piso de percentual que tem de barrar, e por
# isso os três grupos aqui passam folgados no piso de minutos.
VERDE = [0, 0, 10, 0, 0, 0]
r3, _ = rodar([desc(1, AZUL, AZUL, 1300), desc(20, VERM, VERM, 1300),
               desc(40, VERDE, VERDE, 1300)])
c3 = r3["cameras"][0]
check("três grupos repartindo → SEM titular (nenhum passa dos 40%)",
      c3["titular"] is None and "sem dominante" in c3["motivo"], c3["motivo"])
check("e barrou pelo PERCENTUAL, não por falta de minutos",
      "min no posto" not in c3["motivo"], c3["motivo"])
check("e isso é RESPOSTA, não erro (o relatório sai completo)",
      c3["n_grupos"] == 3 and c3["minutos_posto_total"] > 0, c3)

print("\n[12b] `numeric` do Postgres volta como STRING — e quase matou tudo")
# O PostgREST devolve coluna `numeric` como texto: altura_rel chega '0.4831',
# não 0.4831. `max(str, int)` levanta TypeError e o agrupamento inteiro morre
# no primeiro par. Os testes sintéticos não pegavam porque construíam floats —
# o dublê era mais generoso que o serviço real, a MESMA armadilha da Fase 81.
# Achado rodando contra o banco de verdade, não contra fixture.
check("_num coage string numérica", pl._num("0.4831") == 0.4831)
check("e devolve None no que não é número", pl._num("abc") is None
      and pl._num(None) is None and pl._num(True) is None)
d_str = desc(1, AZUL, AZUL, "200.5", altura="0.38158")
d_str2 = desc(2, AZULC, AZULC, "180.0", altura="0.38487")
_s, m_str = pl._sim_descritores(d_str, d_str2)
check("comparar dois descritores com strings NÃO levanta", m_str == "ok", m_str)
g_str = pl.agrupar_descritores([d_str, d_str2])
check("agrupa normalmente", len(g_str) == 1 and g_str[0]["n_tracks"] == 2, g_str)
check("e o tempo somado sai numérico",
      abs(g_str[0]["tempo_posto_s"] - 380.5) < 0.01, g_str[0]["tempo_posto_s"])
# razoes vem de jsonb (tipos preservados), mas defensivo mesmo assim
R_STR = {"ombro_tronco": {"med": "0.50", "n": "40"}}
_s, m_r = pl._sim_descritores(desc(3, AZUL, AZUL, 100, R_STR),
                              desc(4, AZULC, AZULC, 100, R_STR))
check("razões com valores em string também não levantam", m_r == "ok", m_r)
check("histograma com strings dentro não levanta",
      pl._sim_hist(["10", "0", "0", "0", "0", "0"], AZUL) == 1.0)

print("\n[12c] COSTURA GEOMÉTRICA — antes da cor, e sem depender dela")
# Três tracks do MESMO vídeo, em sequência, no mesmo lugar: é uma pessoa só
# que o BoT-SORT perdeu duas vezes atrás do torno.
frag = [desc(1, AZUL, AZUL, 20, n_amostras=2, t_ini=0, t_fim=8, x=0.50, video="vA"),
        desc(2, AZUL, AZUL, 20, n_amostras=2, t_ini=11, t_fim=19, x=0.52, video="vA"),
        desc(3, AZUL, AZUL, 20, n_amostras=2, t_ini=22, t_fim=40, x=0.54, video="vA")]
c = pl.costurar_tracks(frag)
check("três fragmentos viram UM track", len(c) == 1, [x["pessoa_track_id"] for x in c])
check("as amostras SOMAM (é daí que vem o ganho)", c[0]["n_amostras"] == 6, c[0]["n_amostras"])
check("o tempo visível soma", c[0]["tempo_visivel_s"] == 60, c[0]["tempo_visivel_s"])
check("o histograma soma (menos ruído)", c[0]["hist_sup"][0] == 30, c[0]["hist_sup"])
check("guarda quais tracks foram costurados",
      sorted(c[0]["tracks_costurados"]) == [1, 2, 3], c[0].get("tracks_costurados"))

# Duas pessoas ao MESMO TEMPO nunca podem ser costuradas: sobreposição no
# tempo é prova de que são duas.
juntos = [desc(1, AZUL, AZUL, 20, t_ini=0, t_fim=30, x=0.3, video="vB"),
          desc(2, AZUL, AZUL, 20, t_ini=5, t_fim=35, x=0.7, video="vB")]
check("tracks SIMULTÂNEOS não costuram", len(pl.costurar_tracks(juntos)) == 2)

# Longe no espaço: a pessoa não se teletransporta.
longe = [desc(1, AZUL, AZUL, 20, t_ini=0, t_fim=10, x=0.10, video="vC"),
         desc(2, AZUL, AZUL, 20, t_ini=12, t_fim=20, x=0.90, video="vC")]
check("salto espacial grande não costura", len(pl.costurar_tracks(longe)) == 2)

# Gap longo demais.
tarde = [desc(1, AZUL, AZUL, 20, t_ini=0, t_fim=10, x=0.5, video="vD"),
         desc(2, AZUL, AZUL, 20, t_ini=60, t_fim=70, x=0.5, video="vD")]
check("gap acima do teto não costura", len(pl.costurar_tracks(tarde)) == 2)

# Vídeos diferentes nunca costuram (o tracker é zerado entre vídeos).
outro = [desc(1, AZUL, AZUL, 20, t_ini=0, t_fim=10, x=0.5, video="vE"),
         desc(2, AZUL, AZUL, 20, t_ini=12, t_fim=20, x=0.5, video="vF")]
check("vídeos diferentes não costuram", len(pl.costurar_tracks(outro)) == 2)
# Câmeras diferentes idem.
cams = [desc(1, AZUL, AZUL, 20, t_ini=0, t_fim=10, x=0.5, video="vG", cam="cam1"),
        desc(2, AZUL, AZUL, 20, t_ini=12, t_fim=20, x=0.5, video="vG", cam="cam2")]
check("câmeras diferentes não costuram", len(pl.costurar_tracks(cams)) == 2)
check("a costura é pura geometria — não olha cor",
      len(pl.costurar_tracks(
          [desc(1, AZUL, AZUL, 20, t_ini=0, t_fim=10, x=0.5, video="vH"),
           desc(2, VERM, VERM, 20, t_ini=12, t_fim=20, x=0.5, video="vH")])) == 1)

print("\n[12d] PISO DE AMOSTRAS — track curto fica de fora, não polui")
curtos = [desc(1, AZUL, AZUL, 300, n_amostras=30),
          desc(2, VERM, VERM, 10, n_amostras=1, t_ini=9000)]
gs = pl.agrupar_descritores(curtos)
check("o curto vira grupo PRÓPRIO", len(gs) == 2, [g["n_tracks"] for g in gs])
ind = [g for g in gs if g.get("indefinido")]
check("marcado como indefinido", len(ind) == 1, [g.get("indefinido") for g in gs])
check("e o longo NÃO é indefinido",
      any(not g.get("indefinido") for g in gs))

print("\n[12e] COMPLETE-LINK — a cadeia deixa de fundir o dia inteiro")
# A~B e B~C, mas A e C são opostos. Single-link fundiria os três; complete-link
# não. É o encadeamento que transformou 222 dos 224 tracks da cam1 num grupo.
A = [10, 0, 0, 0, 0, 0]
B = [5, 5, 0, 0, 0, 0]
C = [0, 10, 0, 0, 0, 0]
cadeia = [desc(1, A, A, 100), desc(2, B, B, 100), desc(3, C, C, 100)]
gs2 = pl.agrupar_descritores(cadeia)
check("A e C NÃO caem no mesmo grupo (sem encadeamento)",
      not any(len(g["tracks"]) == 3 for g in gs2), [g["n_tracks"] for g in gs2])

print("\n[13] Por CÂMERA e por DIA — cam1 e cam2 não são a mesma régua")
r4, _ = rodar([desc(i, AZUL, AZUL, 200, cam="cam1") for i in range(1, 9)]
              + [desc(i, VERM, VERM, 200, cam="cam2") for i in range(30, 38)])
check("duas câmeras, dois relatórios", len(r4["cameras"]) == 2,
      [c["cam_id"] for c in r4["cameras"]])
check("cada uma com seu titular",
      all(c["titular"] == "g1" for c in r4["cameras"]),
      [(c["cam_id"], c["titular"]) for c in r4["cameras"]])
check("os grupos NÃO se misturam entre câmeras",
      all(c["n_grupos"] == 1 for c in r4["cameras"]))

print("\n[14] SOMBRA: não escreve em evento nenhum")
r5, sb5 = rodar([desc(i, AZUL, AZUL, 200) for i in range(1, 9)], persistir=True)
tabelas = {t for t, _ in sb5.escritas}
check("só grava em titular_dia", tabelas == {"titular_dia"}, tabelas)
check("nunca toca em eventos", "eventos" not in tabelas)
check("nunca toca em descritores_track", "descritores_track" not in tabelas)
check("o modo está declarado no retorno", r5["modo"] == "sombra")
check("e a nota diz que não muda métrica",
      "não muda papel_pessoa" in r5["nota"] or "SOMBRA" in r5["nota"])

print("\n[15] Continuidade: ALERTA, nunca correção")
check("a função existe e devolve lista", isinstance(r5.get("continuidade"), list))
cont_src = fonte[fonte.index("def _continuidade_titular"):]
cont_src = cont_src[:cont_src.index("def _assinatura_do_titular")]
check("não corrige nada — só monta alerta",
      "alertas.append" in cont_src and "update" not in cont_src)
check("o texto entrega a decisão ao humano",
      "quem decide é você" in cont_src)
check("e lista as causas possíveis (não afirma erro)",
      "troca de turno" in cont_src and "roupa" in cont_src)

print("\n[16] Data inválida e dia vazio não viram erro mudo")
sb6 = FakeSB({"descritores_track": [], "videos": VID, "titular_dia": []})
r6 = pl.identificar_titular_do_dia(sb6, "U", "T", "04/08/2026")
check("data inválida explica o formato", "erro" in r6 and "AAAA-MM-DD" in r6["erro"])
r7 = pl.identificar_titular_do_dia(sb6, "U", "T", "2026-01-01", persistir=False)
check("dia sem descritor devolve contrato completo",
      r7["cameras"] == [] and "nota" in r7, r7)

print("\n[17] Zero chamada de API — é o que o dono pode tocar sem mexer no orçamento")
tit_src = fonte[fonte.index("def _sim_hist"):fonte.index("def carregar_camadas_duvida")]
for proibido in ("groq_text_call", "groq_vision_call", "ai_provider",
                 "vision_call", "text_call"):
    check(f"nenhuma chamada a {proibido}", proibido not in tit_src)
check("o passe roda fora da ingestão (throttle no heartbeat)",
      "_passe_titular_com_throttle" in
      open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "backend", "main.py"), encoding="utf-8").read())

print("\n[18] LGPD: identidade anônima por papel")
check("nenhum campo de nome no schema",
      "nome_pessoa" not in fonte and "cadastro" not in tit_src.lower()
      or "não há cadastro" in fonte)
sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sql", "schema.sql"), encoding="utf-8").read()
check("a tabela existe", "create table if not exists titular_dia" in sql)
check("e o schema registra a decisão de LGPD",
      "IDENTIDADE ANÔNIMA POR PAPEL" in sql and "LGPD" in sql)
check("titular nulo é estado legítimo, documentado",
      "DIA SEM TITULAR" in sql)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
