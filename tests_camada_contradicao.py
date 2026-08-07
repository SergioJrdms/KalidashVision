"""Fase 68 — a primeira camada de dúvida é DADO, não palpite.

`papel_pessoa` vem do rastreamento + zonas: determinístico, não passa pelo VLM.
Quando ele diz 'operador' (identificado NO POSTO) e o rótulo diz `posto_vazio`,
os dois não podem estar certos. Não é ambiguidade a calibrar — é contradição
lógica. Por isso a camada nasce ATIVA, não em sombra.

Foi assim que o contágio da Fase 67 apareceu: 80 eventos com o operador
rastreado no posto e o rótulo dizendo posto vazio.

Rodar:  python tests_camada_contradicao.py
"""
import sys, types, os, json

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

from pathlib import Path  # noqa: E402
from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


def amostra(papel, zona="posto", t=0.0, tid=1):
    return {"pessoa_track_id": tid, "papel_pessoa": papel, "zona_contexto": zona,
            "tempo_inicio_s": t, "tempo_fim_s": t + 5,
            "bbox_inicio": [10, 10, 40, 90]}


REP = {"tempo_inicio_s": 0, "tempo_fim_s": 60}

print("\n[1] O motor expõe papel_pessoa como sinal de primeira classe")
fato = pl.montar_fato_evento(
    REP, [(amostra("operador"), 1.0), (amostra("visitante", t=5, tid=2), 1.0)],
    share=1.0, n_rotulos=1)
check("operador_presente é True quando há operador no posto",
      fato["operador_presente"] is True, fato)
check("papeis_na_cena lista os papéis vistos",
      fato["papeis_na_cena"] == ["operador", "visitante"], fato["papeis_na_cena"])
check("pessoas_no_posto segue existindo (compatibilidade)",
      fato["pessoas_no_posto"] == 1, fato)

fato_vazio = pl.montar_fato_evento(
    REP, [(amostra("visitante"), 1.0)], share=1.0, n_rotulos=1)
check("operador_presente é False sem operador",
      fato_vazio["operador_presente"] is False, fato_vazio)
check("papeis_na_cena sem operador", fato_vazio["papeis_na_cena"] == ["visitante"])

fato_nada = pl.montar_fato_evento(
    REP, [(amostra(None), 1.0)], share=1.0, n_rotulos=1)
check("papel ausente não vira papel vazio na lista",
      fato_nada["papeis_na_cena"] == [], fato_nada["papeis_na_cena"])
check("sem papel nenhum, operador_presente é False",
      fato_nada["operador_presente"] is False)

print("\n[2] A camada da contradição dispara — e só nela")
CAMADA = {
    "nome": "contradicao_posto_vazio_com_operador",
    "quando_rotulo": ["posto_vazio"],
    "se": {"operador_presente": True},
    "entao": "duvida", "modo": "ativa", "ordem": 10,
    "motivo": "O rastreamento identificou o OPERADOR no posto, mas o rótulo diz vazio.",
}

dv, disp, _av = pl.avaliar_camadas(fato, "posto_vazio", [CAMADA])
check("contradição vira dúvida", dv is True, (dv, disp))
check("é ATIVA (entra no em_duvida, não só no placar)",
      disp and disp[0]["modo"] == "ativa", disp)
check("o motivo explica de onde vem a presença",
      "rastreamento" in disp[0]["motivo"], disp)

dv, disp, _av = pl.avaliar_camadas(fato_vazio, "posto_vazio", [CAMADA])
check("posto_vazio LEGÍTIMO (só transeunte) não dispara", dv is False, disp)

dv, disp, _av = pl.avaliar_camadas(fato, "operar_torno", [CAMADA])
check("operador presente com rótulo de operação não dispara", dv is False, disp)

# Sinal ausente NUNCA dispara: processo sem zona de posto não gera alarme falso.
dv, disp, _av = pl.avaliar_camadas({"pessoas_na_cena": 2}, "posto_vazio", [CAMADA])
check("sem o sinal na cena, a camada fica quieta", dv is False, disp)

print("\n[3] O SQL semeia a camada em modo ATIVA, para todos os processos")
sql = Path("sql/schema.sql").read_text()
i = sql.index("Fase 68")
bloco = sql[i:i + 2600]
check("insere em camadas_duvida", "insert into camadas_duvida" in bloco)
check("condição é operador_presente=true",
      '{"operador_presente": true}' in bloco, bloco[:200])
check("aplica-se só ao rótulo posto_vazio", '["posto_vazio"]' in bloco)
check("nasce ATIVA, não sombra", "'ativa'" in bloco and "'sombra'" not in bloco)
check("entao é 'duvida' — a camada nunca corrige sozinha",
      "'duvida'" in bloco)
check("semeia para TODOS os processos existentes",
      "from contexto_processo" in bloco)
check("idempotente (on conflict)", "on conflict" in bloco)
check("reaplica o modo se alguém tiver deixado em sombra",
      "modo  = excluded.modo" in bloco)

print("\n[4] O evento carrega a camada disparada (senão não há placar)")
eventos = [{
    "pessoa_track_id": 1, "comportamento_label": "posto_vazio",
    "descricao_bruta": "monitorando o ciclo da máquina",
    "tempo_inicio_s": 0, "tempo_fim_s": 60, "papel_pessoa": "operador",
    "zona_contexto": "posto", "bbox_inicio": [10, 10, 40, 90],
    "frame_inicio": 0, "frame_fim": 30, "n_amostras": 4, "confianca": 0.9,
}]
try:
    principais = pl.etapa_consolidar_principais(
        eventos, {"posto_vazio": "vazio"}, 60.0, camadas=[CAMADA])
    erro = None
except Exception as e:  # noqa: BLE001
    principais, erro = None, e
check("consolidação não explode com a camada", erro is None, erro)
if principais:
    p = principais[0]
    check("o evento sai marcado em_duvida", bool(p.get("em_duvida")), p.get("em_duvida"))
    check("e registra QUAL camada disparou",
          any(d.get("nome") == CAMADA["nome"] for d in (p.get("camadas_disparadas") or [])),
          p.get("camadas_disparadas"))
    check("o motivo viaja com o evento", bool(p.get("duvida_motivo")), p.get("duvida_motivo"))

print("\n[5] Sombra continua sendo sombra (a garantia da Fase 57 não quebrou)")
sombra = {**CAMADA, "modo": "sombra"}
dv, disp, _av = pl.avaliar_camadas(fato, "posto_vazio", [sombra])
check("em sombra NÃO marca dúvida", dv is False, dv)
check("mas conta no placar", len(disp) == 1 and disp[0]["modo"] == "sombra", disp)


# ════════════════════════════════════════════════════════════════════════
# Fase 69 — C1, C2 e a sombra. A linha entre ATIVA e SOMBRA é uma só:
# ativa quando os dois sinais NÃO PODEM estar certos ao mesmo tempo.
# ════════════════════════════════════════════════════════════════════════
ROTULOS_DO_OPERADOR = ["operar_torno", "monitorar_maquina", "ajustar_maquina",
                       "preparar_maquina", "medir_peca", "limpando_cavaco",
                       "lendo_desenho_tecnico"]
C1 = {"nome": "contradicao_ato_do_operador_sem_operador",
      "quando_rotulo": ROTULOS_DO_OPERADOR,
      "se": {"operador_presente": False}, "entao": "duvida",
      "modo": "ativa", "ordem": 11, "motivo": "ato do titular sem o titular"}
C2 = {"nome": "contradicao_posto_vazio_com_maos_na_maquina",
      "quando_rotulo": ["posto_vazio"], "se": {"maos_na_maquina": True},
      "entao": "duvida", "modo": "ativa", "ordem": 12, "motivo": "mão na máquina"}
S1 = {"nome": "suspeita_conversa_sem_operador",
      "quando_rotulo": ["conversando_colega", "interagir_com_colega_ou_lider"],
      "se": {"operador_presente": False}, "entao": "duvida",
      "modo": "sombra", "ordem": 20, "motivo": "conversa sem operador"}
TODAS = [CAMADA, C1, C2, S1]

print("\n[6] C1 — ato do titular sem o titular presente")
for rot in ROTULOS_DO_OPERADOR:
    dv, _, _av = pl.avaliar_camadas(fato_vazio, rot, [C1])
    check(f"C1 dispara em '{rot}' sem operador", dv is True)
    dv, _, _av = pl.avaliar_camadas(fato, rot, [C1])
    check(f"C1 NÃO dispara em '{rot}' com operador", dv is False)

dv, _, _av = pl.avaliar_camadas(fato_vazio, "deslocar_buscar_material_ferramenta", [C1])
check("buscar material sem operador é LEGÍTIMO — não dispara", dv is False)
dv, _, _av = pl.avaliar_camadas(fato_vazio, "posto_vazio", [C1])
check("posto_vazio sem operador não dispara C1 (é o esperado)", dv is False)

print("\n[7] C2 — mão na máquina com posto vazio (independe do corpo)")
fato_maos = pl.montar_fato_evento(
    REP, [({**amostra("visitante"), "maos_maquina": True}, 1.0)],
    share=1.0, n_rotulos=1)
check("o sinal da mão existe quando há zona de máquina",
      fato_maos.get("maos_na_maquina") is True, fato_maos)
dv, disp, _av = pl.avaliar_camadas(fato_maos, "posto_vazio", [C2])
check("C2 dispara: mão na máquina com posto vazio", dv is True, disp)

fato_sem_maos = pl.montar_fato_evento(
    REP, [({**amostra("visitante"), "maos_maquina": False}, 1.0)],
    share=1.0, n_rotulos=1)
dv, _, _av = pl.avaliar_camadas(fato_sem_maos, "posto_vazio", [C2])
check("sem mão na máquina, C2 fica quieta", dv is False)
dv, _, _av = pl.avaliar_camadas(fato_vazio, "posto_vazio", [C2])
check("sem zona de máquina (sinal ausente), C2 fica quieta", dv is False)

print("\n[8] S1 — conversa sem operador MEDE, não alarma")
dv, disp, _av = pl.avaliar_camadas(fato_vazio, "conversando_colega", TODAS)
check("S1 não marca dúvida (é sombra)", dv is False, dv)
check("mas conta no placar",
      any(d["nome"] == S1["nome"] for d in disp), disp)
check("C1 não pega conversa (rótulo fora da lista)",
      not any(d["nome"] == C1["nome"] for d in disp), disp)

print("\n[9] A ARMADILHA: processo SEM zona de posto não pode alarmar")
# `operador_presente=false` é a condição de C1 e S1. Se o sinal fosse emitido
# como False em processo sem zona, C1 dispararia em TODO evento de operação —
# uma tempestade de alarme falso vinda de ausência de informação.
fato_sem_zona = pl.montar_fato_evento(
    REP, [({"pessoa_track_id": 1, "tempo_inicio_s": 0, "tempo_fim_s": 5,
            "bbox_inicio": [10, 10, 40, 90]}, 1.0)],
    share=1.0, n_rotulos=1, rastreia_papel=False)
check("sem rastreio de papel, a chave é OMITIDA (não vira False)",
      "operador_presente" not in fato_sem_zona, fato_sem_zona)
for c in (C1, S1):
    dv, disp, _av = pl.avaliar_camadas(fato_sem_zona, "operar_torno", [c])
    check(f"{c['nome'][:28]}… fica quieta sem a zona", dv is False and not disp)

# E o inverso: com rastreio, o sinal existe e as regras voltam a valer.
fato_com_zona = pl.montar_fato_evento(
    REP, [(amostra("visitante"), 1.0)], share=1.0, n_rotulos=1,
    rastreia_papel=True)
check("com rastreio, a chave existe e é False",
      fato_com_zona.get("operador_presente") is False)
dv, _, _av = pl.avaliar_camadas(fato_com_zona, "operar_torno", [C1])
check("e aí C1 volta a disparar", dv is True)

print("\n[10] O SQL semeia as quatro (duas ativas, uma sombra, S2 recusada)")
sql = Path("sql/schema.sql").read_text()
bloco = sql[sql.index("Fase 69"):]
check("C1 está no SQL", "contradicao_ato_do_operador_sem_operador" in bloco)
check("C1 tem os 7 rótulos", all(r in bloco for r in ROTULOS_DO_OPERADOR))
check("C1 é ativa", "'ativa', 11" in bloco)
check("C2 está no SQL e é ativa",
      "contradicao_posto_vazio_com_maos_na_maquina" in bloco and "'ativa', 12" in bloco)
check("S1 é sombra", "suspeita_conversa_sem_operador" in bloco and "'sombra', 20" in bloco)
check("deslocar_buscar_material NÃO virou regra",
      "deslocar_buscar_material" not in bloco, "escreveram regra para um caso legítimo")
check("S2 foi recusada com o motivo escrito",
      "NÃO FOI ESCRITA" in bloco and "insert into camadas_duvida" not in
      bloco[bloco.index("S2 (rótulo × zona)"):])
check("todas idempotentes", bloco.count("on conflict (empresa, processo, nome)") == 3)

print(f"\n{'='*56}\n  TOTAL {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
