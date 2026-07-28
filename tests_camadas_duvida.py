"""Fase 57 — camadas de dúvida declarativas + modo sombra + placar por camada.

Rodar:  python tests_camadas_duvida.py
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
sys.modules["numpy"].array = lambda s, dtype=None: [list(r) for r in s]
sys.modules["cv2"].pointPolygonTest = lambda *a, **k: -1.0
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ["KV_PRINCIPAL_DOMINANCIA"] = "0.0"

from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def ck(n, c, e=""):
    global ok, fail
    c = bool(c)
    print(("  ok   " if c else "  FAIL ") + n + ("" if c else f"  {e}")); ok += c; fail += (not c)


# ── as duas camadas do exemplo, exatamente como documentadas ──────────────
CAMADA_INTERACAO = {
    "nome": "interacao_sem_segunda_pessoa",
    "quando_rotulo": ["conversar_com_colega", "conversar_com_colega_ou_lider"],
    "se": {"pessoas_na_cena": {"<=": 1}},
    "motivo": "O rótulo implica interação, mas só uma pessoa foi detectada.",
    "modo": "ativa", "ordem": 10,
}
CAMADA_OPERAR = {
    "nome": "operar_sem_mao_na_maquina",
    "quando_rotulo": ["operar_torno", "operar_maquina_industrial"],
    "se": {"e": [{"maos_na_maquina": {"==": False}}, {"concordancia": {"<": 0.7}}]},
    "motivo": "Rótulo de operação, mas sem mão na máquina e com amostras discordantes.",
    "modo": "ativa", "ordem": 20,
}

print("\n[1] Critério 4 — cena com 1 pessoa + rótulo de interação → dúvida")
fato = {"pessoas_na_cena": 1, "concordancia": 0.9}
dv, disp = pl.avaliar_camadas(fato, "conversar_com_colega", [CAMADA_INTERACAO])
ck("marca dúvida", dv is True, (dv, disp))
ck("motivo registrado", disp and "interação" in disp[0]["motivo"], disp)
ck("nome da camada registrado", disp[0]["nome"] == "interacao_sem_segunda_pessoa")
dv2, _ = pl.avaliar_camadas({"pessoas_na_cena": 2}, "conversar_com_colega", [CAMADA_INTERACAO])
ck("2 pessoas → não dispara", dv2 is False)
dv3, _ = pl.avaliar_camadas(fato, "operar_torno", [CAMADA_INTERACAO])
ck("outro rótulo → não dispara", dv3 is False)

print("\n[2] Lista de rótulos e curinga")
ck("casa qualquer da lista",
   pl.avaliar_camadas({"pessoas_na_cena": 1}, "conversar_com_colega_ou_lider",
                      [CAMADA_INTERACAO])[0] is True)
curinga = {**CAMADA_INTERACAO, "quando_rotulo": ["*"], "nome": "todos"}
ck("['*'] vale para todo rótulo",
   pl.avaliar_camadas({"pessoas_na_cena": 1}, "qualquer_coisa", [curinga])[0] is True)

print("\n[3] Combinadores e/ou/nao")
f = {"maos_na_maquina": False, "concordancia": 0.5}
ck("E: ambas verdadeiras → dispara",
   pl.avaliar_camadas(f, "operar_torno", [CAMADA_OPERAR])[0] is True)
ck("E: uma falsa → não dispara",
   pl.avaliar_camadas({"maos_na_maquina": True, "concordancia": 0.5},
                      "operar_torno", [CAMADA_OPERAR])[0] is False)
c_ou = {"nome": "ou", "quando_rotulo": ["*"], "modo": "ativa",
        "se": {"ou": [{"pessoas_na_cena": {"==": 0}}, {"concordancia": {"<": 0.4}}]}}
ck("OU: segunda verdadeira → dispara",
   pl.avaliar_camadas({"pessoas_na_cena": 3, "concordancia": 0.3}, "x", [c_ou])[0] is True)
c_nao = {"nome": "nao", "quando_rotulo": ["*"], "modo": "ativa",
         "se": {"nao": {"maos_na_maquina": {"==": True}}}}
ck("NAO inverte", pl.avaliar_camadas({"maos_na_maquina": False}, "x", [c_nao])[0] is True)
ck("NAO inverte (2)", pl.avaliar_camadas({"maos_na_maquina": True}, "x", [c_nao])[0] is False)

print("\n[4] Sinal AUSENTE nunca vira dúvida")
ck("sem maos_na_maquina no fato → não dispara",
   pl.avaliar_camadas({"concordancia": 0.5}, "operar_torno", [CAMADA_OPERAR])[0] is False)
ck("sinal None → não dispara",
   pl.avaliar_camadas({"maos_na_maquina": None, "concordancia": 0.5},
                      "operar_torno", [CAMADA_OPERAR])[0] is False)
ck("operador desconhecido → não dispara (não quebra)",
   pl.avaliar_camadas({"x": 1}, "y", [{"nome": "z", "quando_rotulo": ["*"],
                                       "modo": "ativa", "se": {"x": {"~~": 1}}}])[0] is False)

print("\n[5] MODO SOMBRA — mede sem contaminar")
sombra = {**CAMADA_INTERACAO, "modo": "sombra"}
dv, disp = pl.avaliar_camadas({"pessoas_na_cena": 1}, "conversar_com_colega", [sombra])
ck("sombra NÃO marca dúvida", dv is False, dv)
ck("mas ENTRA no placar (disparo registrado)", len(disp) == 1 and disp[0]["modo"] == "sombra", disp)
mix = [sombra, CAMADA_OPERAR]
dv, disp = pl.avaliar_camadas({"pessoas_na_cena": 1, "maos_na_maquina": False,
                               "concordancia": 0.5}, "conversar_com_colega", mix)
ck("sombra sozinha não marca", dv is False, (dv, disp))
off = {**CAMADA_INTERACAO, "modo": "off"}
ck("modo off é ignorado por completo",
   pl.avaliar_camadas({"pessoas_na_cena": 1}, "conversar_com_colega", [off]) == (False, []))

print("\n[6] Deslocamento em ALTURAS-DE-CORPO (invariante de escala)")
def bucket(dx, alt, t0=0.0, t1=10.0):
    return [({"pessoa_track_id": 1, "bbox_inicio": [0, 0, alt, alt],
              "tempo_inicio_s": t0, "papel_pessoa": "operador", "zona_contexto": "posto"}, 1),
            ({"pessoa_track_id": 1, "bbox_inicio": [dx, 0, dx + alt, alt],
              "tempo_inicio_s": t1, "papel_pessoa": "operador", "zona_contexto": "posto"}, 1)]
rep = {"tempo_inicio_s": 0, "tempo_fim_s": 60}
f_baixa = pl.montar_fato_evento(rep, bucket(300, 100), 1.0, 1)
f_alta = pl.montar_fato_evento(rep, bucket(600, 200), 1.0, 1)   # 2x resolução
ck("mesma cena em resoluções diferentes → mesmo deslocamento relativo",
   f_baixa["deslocamento_rel"] == f_alta["deslocamento_rel"],
   (f_baixa["deslocamento_rel"], f_alta["deslocamento_rel"]))
ck("classifica como 'andando'", f_baixa["movimento"] == "andando", f_baixa)
f_parado = pl.montar_fato_evento(rep, bucket(2, 100), 1.0, 1)
ck("deslocamento pequeno → 'parado'", f_parado["movimento"] == "parado", f_parado)
c_mov = {"nome": "conversa_andando", "quando_rotulo": ["conversar_com_colega"],
         "modo": "ativa", "se": {"movimento": {"==": "andando"}},
         "motivo": "Rótulo de conversa parada, mas há trajetória de deslocamento."}
ck("regra pensa em chão de fábrica, não em pixel",
   pl.avaliar_camadas(f_baixa, "conversar_com_colega", [c_mov])[0] is True)

print("\n[7] Sinais montados da cena (sem custo de VLM)")
b = [({"pessoa_track_id": 1, "bbox_inicio": [0, 0, 50, 100], "tempo_inicio_s": 0,
       "papel_pessoa": "operador", "zona_contexto": "posto", "maos_maquina": True}, 1),
     ({"pessoa_track_id": 2, "bbox_inicio": [0, 0, 50, 100], "tempo_inicio_s": 5,
       "papel_pessoa": "visitante", "zona_contexto": "interacao"}, 1)]
f = pl.montar_fato_evento(rep, b, 0.6, 2)
ck("conta pessoas distintas", f["pessoas_na_cena"] == 2, f)
ck("conta quem está no posto", f["pessoas_no_posto"] == 1, f)
ck("lista zonas ocupadas", set(f["zonas_ocupadas"]) == {"posto", "interacao"}, f)
ck("carrega concordância (B1)", f["concordancia"] == 0.6, f)
ck("maos_na_maquina presente quando há zona de máquina", f["maos_na_maquina"] is True, f)

print("\n[8] Integração com a consolidação — dúvida gravada no evento")
def cru(label, ini, fim, tid=1, n=1):
    return {"pessoa_track_id": tid, "comportamento_label": label, "descricao_bruta": label,
            "tempo_inicio_s": ini, "tempo_fim_s": fim, "frame_inicio": 0, "frame_fim": 1,
            "bbox_inicio": [0, 0, 50, 100], "zona_contexto": "posto",
            "papel_pessoa": "operador", "n_amostras": n, "confianca": 0.7}
ps = pl.etapa_consolidar_principais([cru("conversar_com_colega", 0, 60)], {}, 60.0,
                                    camadas=[CAMADA_INTERACAO])
ck("evento marcado em_duvida", ps and ps[0].get("em_duvida") is True, ps)
ck("camadas_disparadas gravadas", ps[0].get("camadas_disparadas"), ps[0])
ck("motivo no evento", "interação" in (ps[0].get("duvida_motivo") or ""), ps[0])
ps_s = pl.etapa_consolidar_principais([cru("conversar_com_colega", 0, 60)], {}, 60.0,
                                      camadas=[{**CAMADA_INTERACAO, "modo": "sombra"}])
ck("sombra registra disparo mas NÃO marca dúvida",
   ps_s[0].get("em_duvida") is False and ps_s[0].get("camadas_disparadas"), ps_s[0])
ps_n = pl.etapa_consolidar_principais([cru("operar_torno", 0, 60)], {}, 60.0, camadas=[])
ck("sem camadas → pipeline igual ao de antes",
   "camadas_disparadas" not in ps_n[0] and not ps_n[0].get("em_duvida"), ps_n[0])

print("\n[9] PLACAR POR CAMADA — acerto x falso alarme")
class Q:
    def __init__(s, d): s.d = d
    def select(s, *a, **k): return s
    def eq(s, *a, **k): return s
    def is_(s, *a, **k): return s
    def order(s, *a, **k): return s
    def limit(s, *a, **k): return s
    def range(s, *a, **k): return s
    @property
    def not_(s): return s
    def execute(s): return types.SimpleNamespace(data=s.d)
class SB:
    def __init__(s, ev): s.ev = ev
    def table(s, n): return Q(s.ev if n == "eventos" else [])

def evd(id_, camada, modo, dur, validado=False, correto=None, corrigido=None):
    return {"id": id_, "camadas_disparadas": [{"nome": camada, "modo": modo, "motivo": "m"}],
            "tempo_inicio_s": 0, "tempo_fim_s": dur, "validado_humano": validado,
            "validacao_correto": correto, "label_corrigido": corrigido}

ev = [
    evd("1", "boa", "ativa", 60, True, True, "outro_label"),   # corrigiu → acerto
    evd("2", "boa", "ativa", 60, True, False, None),           # descartou → acerto
    evd("3", "boa", "ativa", 60, True, True, None),            # confirmou → falso alarme
    evd("4", "boa", "ativa", 60, False),                       # não validado
    evd("5", "ruim", "ativa", 180, True, True, None),          # confirmou
    evd("6", "ruim", "ativa", 180, True, True, None),          # confirmou
]
r = pl.placar_camadas(SB(ev), "U", "P")
boa = next(c for c in r["camadas"] if c["nome"] == "boa")
ruim = next(c for c in r["camadas"] if c["nome"] == "ruim")
ck("conta disparos", boa["disparos"] == 4, boa)
ck("soma minutos em dúvida", boa["minutos_em_duvida"] == 4.0, boa)
ck("corrigir e descartar contam como ACERTO", boa["acertos"] == 2, boa)
ck("confirmar conta como FALSO ALARME", boa["falsos_alarmes"] == 1, boa)
ck("taxa de acerto sobre os VALIDADOS", boa["taxa_acerto"] == 66.7, boa)
ck("camada que só confirma → 0% de acerto", ruim["taxa_acerto"] == 0.0, ruim)
ck("ordena por minutos em dúvida", r["camadas"][0]["nome"] == "ruim", r["camadas"])
sem_val = pl.placar_camadas(SB([evd("9", "nova", "sombra", 60)]), "U", "P")
ck("sem validação → taxa None (não inventa)",
   sem_val["camadas"][0]["taxa_acerto"] is None, sem_val)
ck("sombra também entra no placar", sem_val["camadas"][0]["modo"] == "sombra", sem_val)

print(f"\n{'=' * 56}\n== {ok} ok, {fail} fail ==\n{'=' * 56}")
sys.exit(1 if fail else 0)
