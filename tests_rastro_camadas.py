"""Fase 88 — a partição sai do rótulo, e o silêncio das camadas deixa de ser ambíguo.

TRÊS COISAS, e a ordem entre elas é a da investigação que as produziu:

[1-4] A INVESTIGAÇÃO DOS 82. 82 eventos com `papel_pessoa='operador'` e rótulo
      `posto_vazio` não foram pegos por `contradicao_posto_vazio_com_operador`,
      que está ATIVA no banco. Duas hipóteses foram levantadas e as duas são
      testadas aqui:
        (a) `operador_presente` vem dos tracks do minuto e `papel_pessoa` vem
            do evento — poderiam divergir;
        (b) o fato seria montado antes do papel ser atribuído.
      As duas são REFUTADAS: em toda configuração em que um evento sai com
      `papel_pessoa='operador'`, o fato sai com `operador_presente=True` e a
      regra dispara. O motor não tem o furo. O que faltava era saber se ele
      RODOU — que é o item [7].

[5-6] A PARTIÇÃO DESLIGADA. O discriminador media ruído (em minutos adjacentes
      com a mesma ação, o estado troca como moeda), então o estado sai do NOME
      do rótulo e vira coluna. Rótulo afirma; coluna só registra.

[7-9] O RASTRO. `camadas_disparadas` NULL significava "rodou e nada disparou"
      OU "nunca rodou". Agora são estados distinguíveis — inclusive o terceiro,
      que é a assinatura da regressão da Fase 86: rodou, mas nenhuma regra
      mirava aquele rótulo porque o sufixo quebrou o match exato.

Rodar:  python tests_rastro_camadas.py
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


# A regra REAL, como está no banco do processo (modo=ativa, ordem=10).
REGRA_PV = {"nome": "contradicao_posto_vazio_com_operador",
            "quando_rotulo": ["posto_vazio"], "se": {"operador_presente": True},
            "entao": "duvida", "modo": "ativa", "ordem": 10,
            "motivo": "operador presente no posto"}
# A da ordem 11 — a que o sufixo da Fase 86 matou.
ROT_OP = ["operar_torno", "monitorar_maquina", "ajustar_maquina",
          "preparar_maquina", "medir_peca"]
REGRA_ATO = {"nome": "contradicao_ato_do_operador_sem_operador",
             "quando_rotulo": ROT_OP, "se": {"operador_presente": False},
             "entao": "duvida", "modo": "ativa", "ordem": 11,
             "motivo": "ato do titular sem o titular"}


def cru(label, papel, ini, fim, tid, desc="x"):
    return {"pessoa_track_id": tid, "comportamento_label": label,
            "descricao_bruta": desc, "tempo_inicio_s": ini, "tempo_fim_s": fim,
            "frame_inicio": int(ini * 6), "frame_fim": int(fim * 6),
            "bbox_inicio": [100, 100, 200, 400], "bbox_cam": "cam1",
            "bbox_stats": None, "maos_maquina": None,
            "maquina": None, "imovel": None, "zona_contexto": "posto",
            "papel_pessoa": papel, "n_amostras": max(1, int((fim - ini) / 5))}


def consolidar(crus, camadas=(REGRA_PV,)):
    p = pl.etapa_consolidar_principais(crus, {"posto_vazio": "vazio"}, 60.0,
                                       camadas=list(camadas))
    return p[0] if p else None


print("\n[1] Hipótese (a): papel do evento × operador_presente do minuto")
# O caso exato dos 82: o cluster mapeou uma descrição de gente presente para
# `posto_vazio`, então o cru sai com label posto_vazio E papel operador.
e = consolidar([cru("posto_vazio", "operador", 0, 60, 7)])
check("o evento sai como os 82 (posto_vazio + operador)",
      e["comportamento_label"] == "posto_vazio" and e["papel_pessoa"] == "operador")
check("operador_presente NÃO diverge do papel — é True",
      e["_fato"]["operador_presente"] is True, e["_fato"])
check("a regra DISPARA", e.get("em_duvida") is True, e.get("camadas_disparadas"))
check("com o nome certo",
      [d["nome"] for d in e["camadas_disparadas"]] == [REGRA_PV["nome"]])

print("\n[2] Hipótese (a), versão difícil: operador só em PARTE do minuto")
# posto_vazio determinístico domina (40 s) e o operador aparece em 20 s com
# outro rótulo. O vencedor do minuto é posto_vazio, com papel posto_vazio.
e2 = consolidar([cru("posto_vazio", "posto_vazio", 0, 40, -1),
                 cru("operar_torno", "operador", 40, 60, 7)])
check("o rótulo do minuto é posto_vazio", e2["comportamento_label"] == "posto_vazio")
check("o papel do minuto é posto_vazio (veio do vencedor)",
      e2["papel_pessoa"] == "posto_vazio")
check("mas operador_presente é True — basta UM track de operador no minuto",
      e2["_fato"]["operador_presente"] is True)
check("e a regra dispara mesmo com papel != operador",
      e2.get("em_duvida") is True)

print("\n[3] Hipótese (b): o fato é montado DEPOIS do papel")
# `_abrir_evento` grava `papel_pessoa` a partir de `papel` da observação, e
# `montar_fato_evento` lê `papel_pessoa` dos crus. Se a ordem estivesse
# invertida, nenhum cru teria papel na hora do fato e o fato viria False.
e3 = consolidar([cru("posto_vazio", "operador", 0, 30, 7),
                 cru("posto_vazio", "operador", 30, 60, 7)])
check("o fato enxerga o papel dos crus", e3["_fato"]["operador_presente"] is True)
check("a chave existe (rastreia_papel ligado por haver papel)",
      "operador_presente" in e3["_fato"])
# E o contrapositivo: sem papel nenhum no vídeo, a chave é OMITIDA — falta de
# dado não pode virar suspeita.
e3b = consolidar([cru("posto_vazio", None, 0, 60, 7)])
check("sem papel em vídeo NENHUM, a chave é omitida (não False)",
      "operador_presente" not in e3b["_fato"], e3b["_fato"])
check("e nada dispara (ausência de sinal nunca acusa)",
      not e3b.get("camadas_disparadas"))

print("\n[4] O caso LEGÍTIMO continua não disparando")
e4 = consolidar([cru("posto_vazio", "posto_vazio", 0, 60, -1)])
check("posto vazio de verdade não vira dúvida", not e4.get("em_duvida"))
check("operador_presente é False", e4["_fato"]["operador_presente"] is False)

print("\n[5] Partição de cena DESLIGADA — o estado sai do nome do rótulo")
check("chave_cena colapsa em uma partição só",
      pl.chave_cena("ciclo", True) == pl.chave_cena("parada", False) == pl.chave_cena(None, None))
for maq, imo in (("ciclo", False), ("parada", False), (None, True), (None, None)):
    check(f"sufixo_cena({maq!r},{imo!r}) é vazio", pl.sufixo_cena(maq, imo) == "")
check("a descrição do catálogo não ganha o estado em prosa",
      pl._descricao_com_cena("operando o torno", "ciclo", False) == "operando o torno")
# >= e não ==: o contrato desta fase é "a versão foi bumpada AQUI". Cada fase
# nova bumpa de novo, e uma suíte antiga não pode quebrar por isso.
check("versão do instrumento foi bumpada nesta fase (>= 4)",
      pl.VERSAO_INSTRUMENTO >= 4, pl.VERSAO_INSTRUMENTO)
check("a partição fica atrás de flag, não apagada",
      hasattr(pl, "_PARTICAO_CENA") and pl._PARTICAO_CENA is False)

print("\n[6] familia_label descasca em LAÇO (os sufixos duplos do histórico)")
# O LLM batizava o rótulo já com o estado dentro e o sufixo mecânico era colado
# por cima. Tirando um só, a família apontava para um IRMÃO.
for label, raiz in [("monitorar_maquina_parada_ciclo", "monitorar_maquina"),
                    ("monitorar_maquina_parada_parada", "monitorar_maquina"),
                    ("operar_torno_ciclo_ciclo", "operar_torno"),
                    ("conversando_colega_parada_imovel", "conversando_colega"),
                    ("monitorar_maquina_ciclo", "monitorar_maquina"),
                    ("operar_torno", "operar_torno"),
                    ("posto_vazio", "posto_vazio")]:
    check(f"{label} → {raiz}", pl.familia_label(label) == raiz, pl.familia_label(label))
check("um sufixo sozinho não é descascado até virar nada",
      pl.familia_label("_ciclo") == "_ciclo")

print("\n[7] O RASTRO: 'rodou e nada disparou' ≠ 'nunca rodou'")
_dv, _disp, av = pl.avaliar_camadas({"operador_presente": False}, "posto_vazio",
                                    [REGRA_PV])
check("nada disparou", not _disp)
check("mas o rastro prova que a regra FOI perguntada",
      av["aplicaveis"] == [REGRA_PV["nome"]], av)
check("e quantas camadas o motor tinha", av["carregadas"] == 1, av)
e7 = consolidar([cru("posto_vazio", "posto_vazio", 0, 60, -1)])
check("o evento carrega o rastro mesmo sem disparo",
      e7.get("camadas_avaliadas", {}).get("aplicaveis") == [REGRA_PV["nome"]],
      e7.get("camadas_avaliadas"))
check("e camadas_disparadas continua ausente", not e7.get("camadas_disparadas"))
# Sem camadas o motor não roda: a AUSÊNCIA do rastro é o sinal.
e7b = pl.etapa_consolidar_principais(
    [cru("posto_vazio", "posto_vazio", 0, 60, -1)], {"posto_vazio": "v"}, 60.0,
    camadas=[])[0]
check("motor sem camadas não deixa rastro (null = não rodou)",
      e7b.get("camadas_avaliadas") is None, e7b.get("camadas_avaliadas"))

print("\n[8] A assinatura da regressão da Fase 86 fica LEGÍVEL no dado")
# Rótulo com sufixo + regra de rótulo nomeado: o match exato falha. Antes isso
# era indistinguível de "não havia contradição"; agora aplicaveis=[] denuncia.
_dv, _disp, av_suf = pl.avaliar_camadas({"operador_presente": False},
                                        "operar_torno_ciclo", [REGRA_ATO])
check("não dispara (o sufixo quebrou o match)", not _disp)
check("e o rastro mostra POR QUE: nenhuma regra mira este rótulo",
      av_suf["aplicaveis"] == [] and av_suf["carregadas"] == 1, av_suf)
_dv, _disp, av_sem = pl.avaliar_camadas({"operador_presente": False},
                                        "operar_torno", [REGRA_ATO])
check("sem sufixo a mesma regra dispara", _dv is True)
check("e o rastro a lista como aplicável",
      av_sem["aplicaveis"] == [REGRA_ATO["nome"]], av_sem)
check("com a partição off os rótulos voltam a casar",
      pl.familia_label("operar_torno") + pl.sufixo_cena("ciclo", False) == "operar_torno")

print("\n[9] Regra que EXPLODE ≠ regra que não disparou")
ruim = {"nome": "quebrada", "quando_rotulo": ["*"], "se": {"x": {"??": 1}},
        "modo": "ativa", "ordem": 1}
_dv, _disp, av_err = pl.avaliar_camadas({"x": 1}, "qualquer", [ruim])
check("não dispara", not _disp)
check("é listada como aplicável (o rótulo casou)", av_err["aplicaveis"] == ["quebrada"])
_dv, _disp, av_boom = pl.avaliar_camadas({"x": None}, "qualquer", [ruim])
check("sinal ausente não vira erro nem disparo", not _disp and "erro" not in av_boom)

print("\n[10] O discriminador continua sendo COLETADO — só não afirma nada")
check("o normalizador segue vivo (a coluna precisa dele)",
      pl._normalizar_maquina("Ciclo") == "ciclo" and pl._normalizar_maquina("talvez") is None)
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
check("o prompt continua pedindo 'maquina'", '"maquina"' in fonte)
check("e a persistência grava cena_maquina/cena_imovel",
      '"cena_maquina": _normalizar_maquina' in fonte and '"cena_imovel"' in fonte)
check("o rastro é gravado sempre que o motor roda",
      'row["camadas_avaliadas"] = e["camadas_avaliadas"]' in fonte)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
