"""C6 — OPERADOR_FORA como estado temporal persistente dentro de um vídeo.

Roda sem YOLO/vídeo real: a máquina recebe exatamente as observações externas
que a primeira passagem já coletou e verifica decisões em segundos.

Executar:  python tests_c6_operador_fora_estado.py
"""
import os
import sys
import types


RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
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


def candidato(
    tid,
    *,
    anchor=False,
    bbox=(100, 100, 200, 300),
    hist=(1.0, 0.0),
):
    return {
        "track_id": tid,
        "bbox": bbox,
        "kpts": None,
        "_fora_motivo": "operador" if anchor else "passante",
        "_fora_amostras_zona": 3 if anchor else 0,
        "_fora_hist_sup": list(hist) if hist is not None else None,
    }


def amostra(
    tempo_s,
    *candidatos,
    pessoas=None,
    presente=False,
    safety=False,
    cam2=None,
    c5=False,
):
    return pl.Amostra(
        frame_idx=int(round(float(tempo_s) * 10)),
        tempo_s=float(tempo_s),
        img_b64="",
        pessoas=list(pessoas or []),
        dim=(640, 480),
        operador_presente=presente,
        op_cam2=cam2,
        presenca_safety_gate=safety,
        operador_resgate_cam1_640=c5,
        fora_candidatos=list(candidatos),
    )


def resolver(amostras, duracao=None, *, modo="on", **limites):
    nomes = (
        "_FORA_MODO", "_ZONA_ESTRITA", "_FORA_ESTADO_GAP_S",
        "_FORA_TROCA_TRACK_GAP_S", "_FORA_TROCA_TRACK_IOU_MIN",
        "_FORA_SIM_VETO",
    )
    antigos = {nome: getattr(pl, nome) for nome in nomes}
    try:
        pl._FORA_MODO = modo
        pl._ZONA_ESTRITA = True
        for nome, valor in limites.items():
            setattr(pl, nome, valor)
        return pl.etapa_resolver_operador_fora_c6(amostras, duracao)
    finally:
        for nome, valor in antigos.items():
            setattr(pl, nome, valor)


def fora(am):
    return bool(am.operador_fora_estado)


print("[1] Abertura e manutenção")
a1 = [amostra(0, candidato(22, anchor=True))]
r1 = resolver(a1, 8)
check("1 · âncora original válida abre OPERADOR_FORA",
      fora(a1[0]) and a1[0].operador_fora_proveniencia == "anchor_regra_original"
      and r1["ancoras"] == 1, (a1[0], r1))

a2 = [
    amostra(0, candidato(22, anchor=True)),
    amostra(24, candidato(22)),
    amostra(40, candidato(22)),
]
resolver(a2, 40)
check("2 · mesmo track permanece fora mais de 30 s desde o posto",
      fora(a2[2]) and a2[2].operador_fora_proveniencia == "continuidade_track")
check("3 · _FORA_GAP_S=30 não mata estado já aberto",
      float(pl._FORA_GAP_S) == 30.0 and all(fora(a) for a in a2))


print("\n[2] Troca de track conservadora")
a4 = [
    amostra(0, candidato(22, anchor=True)),
    amostra(16, candidato(70, bbox=(110, 100, 210, 300))),
]
r4 = resolver(a4, 16)
check("4 · 22→70 religa com gap 16, IoU e aparência válidos",
      fora(a4[1]) and a4[1].operador_fora_migracao == (22, 70)
      and r4["migracoes"] == ["22->70"], (a4[1], r4))

a5 = [amostra(0, candidato(22, anchor=True)),
      amostra(16.01, candidato(70, bbox=(110, 100, 210, 300)))]
resolver(a5, 17)
check("5 · troca não ocorre quando gap >16 s", not fora(a5[1]))

a6 = [amostra(0, candidato(22, anchor=True)),
      amostra(8, candidato(70, bbox=(400, 100, 500, 300)))]
resolver(a6, 8)
check("6 · troca não ocorre quando IoU <0.25", not fora(a6[1]))

a7 = [amostra(0, candidato(22, anchor=True, hist=(1.0, 0.0))),
      amostra(8, candidato(70, bbox=(110, 100, 210, 300), hist=(0.0, 1.0)))]
resolver(a7, 8)
check("7 · troca não ocorre quando similaridade <0.45", not fora(a7[1]))

a8 = [
    amostra(0, candidato(22, anchor=True)),
    amostra(8,
            candidato(70, bbox=(105, 100, 205, 300)),
            candidato(71, bbox=(110, 100, 210, 300))),
]
r8 = resolver(a8, 8)
check("8 · múltiplos candidatos plausíveis falham fechado",
      not fora(a8[1]) and r8["ambiguidades"] == 1, r8)


print("\n[3] Pontes, fechamento e precedência")
a9 = [amostra(0, candidato(22, anchor=True)), amostra(11),
      amostra(31.9, candidato(22))]
resolver(a9, 32)
check("9 · gap interno <=32 s é preenchido",
      fora(a9[1]) and a9[1].operador_fora_proveniencia == "ponte_estado_fora")

a10 = [amostra(0, candidato(22, anchor=True)), amostra(20), amostra(32.01),
       amostra(40, candidato(22))]
resolver(a10, 40)
check("10 · gap >32 s encerra o estado",
      fora(a10[0]) and not any(fora(a) for a in a10[1:]))

a11 = [amostra(0, candidato(22, anchor=True)), amostra(8, presente=True),
       amostra(16, candidato(22))]
resolver(a11, 16)
check("11 · presença confirmada dentro encerra imediatamente",
      not fora(a11[1]) and not fora(a11[2]) and a11[1].operador_presente is True)
check("12 · ponte de fora não atravessa operador_presente=True",
      a11[2].operador_fora_proveniencia is None)


print("\n[4] Backfill e bordas")
a13 = [amostra(0, candidato(22)), amostra(8),
       amostra(16, candidato(22, anchor=True))]
resolver(a13, 16)
check("13 · backfill do mesmo track funciona",
      fora(a13[0]) and a13[0].operador_fora_proveniencia == "backfill_mesmo_track"
      and a13[1].operador_fora_proveniencia == "ponte_estado_fora")

a14 = [amostra(0, candidato(22)), amostra(8, presente=True),
       amostra(16, candidato(22, anchor=True))]
resolver(a14, 16)
check("14 · backfill não atravessa presença dentro",
      not fora(a14[0]) and not fora(a14[1]) and fora(a14[2]))

a15 = [amostra(0, candidato(22)), amostra(20),
       amostra(40, candidato(22, anchor=True))]
resolver(a15, 40)
check("15 · backfill não atravessa gap >32 s",
      not fora(a15[0]) and not fora(a15[1]) and fora(a15[2]))

a16 = [amostra(0), amostra(8), amostra(16, candidato(22)),
       amostra(24, candidato(22, anchor=True))]
resolver(a16, 24)
check("16 · borda inicial <=32 s é reconstruída",
      all(fora(a) for a in a16)
      and a16[0].operador_fora_proveniencia == "continuidade_borda_inicio")

a17 = [amostra(0), amostra(8), amostra(40, candidato(22)),
       amostra(48, candidato(22, anchor=True))]
resolver(a17, 48)
check("17 · borda inicial >32 s não é fabricada",
      not fora(a17[0]) and not fora(a17[1]) and fora(a17[2]) and fora(a17[3]))

a18 = [amostra(0, candidato(22, anchor=True)), amostra(12), amostra(24)]
resolver(a18, 30)
check("18 · borda final <=32 s mantém o estado até o fim",
      all(fora(a) for a in a18)
      and a18[-1].operador_fora_proveniencia == "continuidade_borda_fim")


print("\n[5] Fail-closed, câmeras e isolamento")
a19 = [amostra(0, candidato(91)), amostra(8, candidato(91))]
resolver(a19, 8)
check("19 · passante sem âncora nunca vira OPERADOR_FORA",
      not any(fora(a) for a in a19))

a20 = [amostra(0), amostra(8), amostra(16)]
resolver(a20, 16)
check("20 · vídeo sem âncora não inventa operador",
      not any(fora(a) for a in a20))

a21 = [amostra(0, candidato(22, anchor=True))]
a21[0].fora_posto = list(a21[0].fora_candidatos)
c5_enxerga_pendente = pl._candidatos_reais_posto_vazio(a21) == [(0, a21[0])]
a21[0].operador_presente = True
a21[0].operador_resgate_cam1_640 = True
resolver(a21, 8)
check("21 · C5 positivo vence C6",
      c5_enxerga_pendente
      and a21[0].operador_presente is True and not fora(a21[0]))

a22_cam2 = [amostra(0, candidato(22, anchor=True), presente=True, cam2=True)]
a22_c42 = [amostra(0, candidato(22, anchor=True), presente=None, safety=True)]
resolver(a22_cam2, 8)
resolver(a22_c42, 8)
check("22 · CAM2 e C4.2 vencem C6",
      not fora(a22_cam2[0]) and not fora(a22_c42[0]))

a23 = [amostra(0, candidato(22, anchor=True), cam2=None),
       amostra(7.5, candidato(22), cam2=None)]
resolver(a23, 8)
check("23 · CAM1-only funciona", all(fora(a) for a in a23))

a24 = [amostra(0, candidato(22, anchor=True))]
resolver(a24, 8)
check("24 · fora_posto permanece paralelo e nunca entra em pessoas",
      a24[0].pessoas == [] and len(a24[0].fora_posto) == 1
      and a24[0].fora_posto[0]["track_id"] == 22)

a25 = [amostra(0, candidato(22, anchor=True))]
r25 = resolver(a25, 8, modo="off")
check("25 · KV_FORA_DO_POSTO=off mantém C6 inativo",
      not fora(a25[0]) and a25[0].fora_posto == [] and r25["ancoras"] == 0)

a26 = [amostra(0, candidato(22, anchor=True))]
r26 = resolver(a26, 8, modo="sombra")
check("26 · modo sombra mede sem alterar downstream",
      r26["ancoras"] == 1 and r26["slots_fora"] == 1
      and not fora(a26[0]) and a26[0].fora_posto == [])

fonte = open(os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read()
check("27 · tracker continua resetando no início de cada vídeo",
      "resetar_tracker(yolo)" in fonte.split("def etapa_detectar_e_amostrar", 1)[1][:1200])

video1 = [amostra(0, candidato(22, anchor=True))]
video2 = [amostra(0, candidato(22))]
resolver(video1, 8)
resolver(video2, 8)
check("28 · não existe persistência C6 entre vídeos independentes",
      fora(video1[0]) and not fora(video2[0]))

a29_ok = [amostra(0, candidato(22, anchor=True)),
          amostra(15.9, candidato(70, bbox=(110, 100, 210, 300)))]
a29_nao = [amostra(0, candidato(22, anchor=True)),
           amostra(16.1, candidato(70, bbox=(110, 100, 210, 300)))]
resolver(a29_ok, 16)
resolver(a29_nao, 17)
check("29 · limites usam segundos e não quantidade fixa de slots",
      fora(a29_ok[1]) and not fora(a29_nao[1]))

check("30 · gate temporal POSTO_VAZIO permanece 2 observações e margem 8 s",
      pl._POSTO_VAZIO_MIN_OBSERVACOES == 2
      and pl._POSTO_VAZIO_MARGEM_MIN_S == 8.0)


print("\n[6] Caso sintético equivalente ao replay real")
tempos = [float(t) for t in range(0, 241, 8)]
fora_22 = {24, 32, 40, 48, 80, 88, 96, 104, 112, 120, 128, 136, 144, 152}
fora_70 = {160, 168, 176, 184, 192, 216, 224, 232}
timeline = []
for t in tempos:
    if t in fora_22:
        timeline.append(amostra(t, candidato(22, anchor=(t == 80))))
    elif t in fora_70:
        timeline.append(amostra(t, candidato(
            70, bbox=(110, 100, 210, 300),
        )))
    else:
        timeline.append(amostra(t))
resumo_real = resolver(timeline, 244)
check("31 · caso 244 s forma um único episódio lógico contínuo",
      all(fora(a) for a in timeline)
      and {a.operador_fora_episodio for a in timeline} == {1}, resumo_real)
check("32 · caso real cobre backfill, ponte, re-ID e as duas bordas",
      resumo_real["backfill"] >= 4
      and resumo_real["pontes"] >= 5
      and resumo_real["trocas_track"] == 1
      and resumo_real["borda_inicio"] >= 3
      and resumo_real["borda_fim"] >= 1, resumo_real)


print("\n[7] Identidade C6 independe do VLM")
a33 = [amostra(0, candidato(22, anchor=True))]
resolver(a33, 8)
antigos = (pl._FORA_MODO, pl._POSTO_VAZIO_ENABLE, pl._analisar_sequencia_fora)
try:
    pl._FORA_MODO = "on"
    pl._POSTO_VAZIO_ENABLE = True
    pl._analisar_sequencia_fora = lambda *_a, **_k: {}
    observacoes = pl.etapa_analise_vlm(
        None, a33, "torneamento", {}, lambda *_a, **_k: None,
        zona_posto="posto", intervalo_s=8.0,
    )
finally:
    pl._FORA_MODO, pl._POSTO_VAZIO_ENABLE, pl._analisar_sequencia_fora = antigos
check("33 · falha do VLM abstém na atividade, mas preserva OPERADOR_FORA",
      len(observacoes) == 1
      and observacoes[0]["papel"] == pl.PAPEL_OPERADOR_FORA
      and observacoes[0]["trabalho"] is None
      and observacoes[0]["origem_gate"] == "c6_operador_fora_estado",
      observacoes)

processamento = fonte.split("def processar_video", 1)[1]
ordem = [
    processamento.index("stats_op = etapa_confirmar_operador"),
    processamento.index("n_cam1_640, resultados_cam1_640 = etapa_resgate_cam1_640"),
    processamento.index("n_c42 = etapa_consenso_multicamera_640"),
    processamento.index("etapa_resolver_operador_fora_c6("),
    processamento.index("resumo_111d = aplicar_identidade_logica_segmento"),
    processamento.index("observacoes = etapa_analise_vlm"),
]
check("34 · ordem efetiva é confirmação → C5 → C4.2 → C6 → 111D → VLM",
      ordem == sorted(ordem), ordem)
check("35 · os três knobs C6 têm somente os defaults congelados",
      'os.environ.get("KV_FORA_ESTADO_GAP_S", "32")' in fonte
      and 'os.environ.get("KV_FORA_TROCA_TRACK_GAP_S", "16")' in fonte
      and 'os.environ.get("KV_FORA_TROCA_TRACK_IOU_MIN", "0.25")' in fonte)


print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
