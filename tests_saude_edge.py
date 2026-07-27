"""Fase 52 — testes da lógica de SAÚDE (observado × esperado).

Cobre os critérios de aceitação 1, 3, 4, 5 e 6 sem banco e sem Pi:
  1) capturando  3) uma câmera caída  4) fora do turno = repouso (sem alarme)
  5) heartbeat parado dentro do turno = sem sinal  6) nenhum heartbeat = vazio
Rodar:  python test_saude.py
"""
import sys, types, os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/user/KalidashVision")

# ── stubs das libs pesadas que backend.pipeline importa no topo ──────────────
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
_np = sys.modules["numpy"]
_np.array = lambda s, dtype=None: [list(r) for r in s]
_np.int32 = "i"; _np.float32 = "f"
sys.modules["cv2"].pointPolygonTest = lambda *a, **k: -1.0
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

from backend import main as M  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


AGORA = datetime.now().astimezone()
UTC = timezone.utc


class FakeQ:
    def __init__(self, dados): self._d = dados
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lt(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def delete(self, *a, **k): return self
    def insert(self, d, *a, **k): self._ins = d; return self
    def execute(self): return types.SimpleNamespace(data=self._d)


class FakeSB:
    def __init__(self, hbs, turnos, proc="Torneamento"):
        self.tabelas = {
            "heartbeats_edge": hbs,
            "turnos_processo": turnos,
            "contexto_processo": [{"processo": proc}],
        }
    def table(self, nome): return FakeQ(self.tabelas.get(nome, []))


class U:
    empresa = "União"; id = "u1"; email = "f@u.com"


def hb(min_atras, cams, disco=20.0, estado="capturando"):
    return {
        "device_id": "pi-01", "runner_versao": "51", "estado": estado,
        "cameras": cams,
        "disco_livre_gb": disco, "disco_uso_pct": 55.0, "cpu_temp_c": 51.2,
        "uptime_s": 90000, "turno_janela": "06:00-11:30", "turno_deadline": None,
        "recebido_em": (datetime.now(UTC) - timedelta(minutes=min_atras)).isoformat(),
    }


def cam(cid, nome, gravando, seg_min_atras=1, falhas=0):
    return {"cam_id": cid, "nome": nome, "gravando": gravando,
            "ultimo_segmento_em": (datetime.now(UTC) - timedelta(minutes=seg_min_atras)).isoformat(),
            "ultimo_segmento_bytes": 12345, "falhas": falhas}


# Janelas ANCORADAS no dia, não relativas a "agora": construir com
# AGORA±2h fazia o teste falhar quando rodava perto da meia-noite (a janela
# cruzava o dia, e turno que cruza meia-noite é rejeitado de propósito).
def turno_cobrindo_agora():
    """Turno que CONTÉM qualquer instante do dia."""
    return [{"nome": "Turno 1", "intervalos": [{"inicio": "00:00", "fim": "23:59"}],
             "dias_semana": [1, 2, 3, 4, 5, 6, 7], "ativo": True}]


def turno_fora_de_agora():
    """Turno de 1h que NUNCA contém o instante atual (escolhido no hemisfério
    oposto do relógio)."""
    ini_h = (AGORA.hour + 12) % 24
    return [{"nome": "Turno 1",
             "intervalos": [{"inicio": f"{ini_h:02d}:00", "fim": f"{ini_h:02d}:59"}],
             "dias_semana": [1, 2, 3, 4, 5, 6, 7], "ativo": True}]


def saude(hbs, turnos):
    return M.saude_edge("proc-1", user=U(), )  # placeholder (substituído abaixo)


# saude_edge chama make_supabase_client(); trocamos por uma fábrica controlada.
def rodar(hbs, turnos):
    M.make_supabase_client = lambda *a, **k: FakeSB(hbs, turnos)
    return M.saude_edge("proc-1", user=U())


print("\n[1] Critério 1 — capturando dentro do turno")
r = rodar([hb(1, [cam("cam1", "posto-1-cam-frontal", True),
                  cam("cam2", "posto-1-cam-lateral", True)])], turno_cobrindo_agora())
check("estado geral = capturando", r["estado"] == "capturando", r["estado"])
check("2 câmeras retornadas", len(r["cameras"]) == 2, r["cameras"])
check("ambas capturando", all(c["estado"] == "capturando" for c in r["cameras"]),
      [c["estado"] for c in r["cameras"]])
check("nome amigável preservado", r["cameras"][0]["nome"] == "posto-1-cam-frontal")
check("turno ativo informado", r["turno"]["ativa"] is not None, r["turno"])
check("cobertura 24h tem 96 blocos", len(r["cobertura_24h"]) == 96, len(r["cobertura_24h"]))
check("bloco atual marcado como houve",
      any(b["houve"] for b in r["cobertura_24h"][-3:]), r["cobertura_24h"][-3:])
check("disco reportado", r["disco"] and r["disco"]["livre_gb"] == 20.0, r["disco"])

print("\n[2] Critério 3 — uma câmera cai, a outra segue")
r = rodar([hb(1, [cam("cam1", "posto-1-cam-frontal", True),
                  cam("cam2", "posto-1-cam-lateral", False, seg_min_atras=40, falhas=7)])],
          turno_cobrindo_agora())
c1 = next(c for c in r["cameras"] if c["cam_id"] == "cam1")
c2 = next(c for c in r["cameras"] if c["cam_id"] == "cam2")
check("cam1 continua capturando", c1["estado"] == "capturando", c1)
check("cam2 vira sem_sinal", c2["estado"] == "sem_sinal", c2)
check("falhas da cam2 preservadas", c2["falhas"] == 7, c2)
check("estado GERAL segue capturando (pulso é recente)", r["estado"] == "capturando")

print("\n[3] Critério 4 — fora do turno = repouso, SEM alarme")
r = rodar([hb(120, [cam("cam1", "posto-1-cam-frontal", False, seg_min_atras=120)])],
          turno_fora_de_agora())
check("estado = em_repouso", r["estado"] == "em_repouso", r["estado"])
check("NENHUMA câmera em sem_sinal fora do turno",
      all(c["estado"] == "em_repouso" for c in r["cameras"]),
      [c["estado"] for c in r["cameras"]])
check("turno.ativa é None fora da janela", r["turno"]["ativa"] is None)
# Pulso velho fora do turno NÃO pode virar alarme
r2 = rodar([hb(600, [cam("cam1", "f", False, seg_min_atras=600)])], turno_fora_de_agora())
check("pulso de 10h atrás fora do turno segue em_repouso", r2["estado"] == "em_repouso", r2["estado"])

print("\n[4] Critério 5 — heartbeat parado DENTRO do turno = sem sinal")
r = rodar([hb(47, [cam("cam1", "posto-1-cam-frontal", True, seg_min_atras=47)])],
          turno_cobrindo_agora())
check("estado = sem_sinal", r["estado"] == "sem_sinal", r["estado"])
check("idade_s ≈ 47min", 2700 <= r["idade_s"] <= 2900, r["idade_s"])
check("câmera também sem_sinal", r["cameras"][0]["estado"] == "sem_sinal")
# limiar: 3× 5min = 15min. 10min ainda é saudável, 20min não.
r_ok = rodar([hb(10, [cam("cam1", "f", True, seg_min_atras=1)])], turno_cobrindo_agora())
r_no = rodar([hb(20, [cam("cam1", "f", True, seg_min_atras=1)])], turno_cobrindo_agora())
check("10min dentro do turno = capturando (< 3x intervalo)", r_ok["estado"] == "capturando")
check("20min dentro do turno = sem_sinal (> 3x intervalo)", r_no["estado"] == "sem_sinal")

print("\n[5] Critério 6 — nenhum heartbeat ainda")
r = rodar([], turno_cobrindo_agora())
check("estado = sem_dados", r["estado"] == "sem_dados", r["estado"])
check("sem erro, listas vazias", r["cameras"] == [] and r["disco"] is None)
check("cobertura ainda desenhada (esperado do turno)",
      len(r["cobertura_24h"]) == 96 and any(b["esperado"] for b in r["cobertura_24h"]))
check("ultimo_heartbeat_em é None", r["ultimo_heartbeat_em"] is None)

print("\n[6] Buraco visível na faixa de cobertura")
# pulsos só na 1ª metade da janela → blocos recentes ficam sem 'houve'
hbs = [hb(m, [cam("cam1", "f", True)]) for m in range(200, 260, 5)]
r = rodar(hbs, turno_cobrindo_agora())
recentes = r["cobertura_24h"][-4:]
check("blocos recentes sem heartbeat = buraco", not any(b["houve"] for b in recentes))
check("blocos antigos preenchidos", any(b["houve"] for b in r["cobertura_24h"]))
esperados = [b for b in r["cobertura_24h"] if b["esperado"]]
check("faixa marca o ESPERADO a partir do turno", len(esperados) > 0, len(esperados))

print("\n[7] Sem turno cadastrado → nunca alarma")
r = rodar([hb(300, [cam("cam1", "f", False, seg_min_atras=300)])], [])
check("sem turno = em_repouso (não sem_sinal)", r["estado"] == "em_repouso", r["estado"])
check("turno.configurado = False", r["turno"]["configurado"] is False)
check("nenhum bloco esperado", not any(b["esperado"] for b in r["cobertura_24h"]))

print("\n[8] Projeção de disco")
antigo = hb(23 * 60, [cam("cam1", "f", True)], disco=40.0)
novo = hb(1, [cam("cam1", "f", True)], disco=20.0)
r = rodar([novo, antigo], turno_cobrindo_agora())      # ordem: mais recente primeiro
check("projeção de dias calculada quando o disco cai",
      r["disco"]["dias_restantes"] is not None, r["disco"])
check("projeção ≈ 1 dia (20GB restantes, 20GB/23h)",
      0.5 <= r["disco"]["dias_restantes"] <= 1.5, r["disco"])
r = rodar([hb(1, [cam("cam1", "f", True)], disco=20.0),
           hb(23 * 60, [cam("cam1", "f", True)], disco=20.0)], turno_cobrindo_agora())
check("disco estável → sem projeção inventada", r["disco"]["dias_restantes"] is None, r["disco"])

print("\n[9] Pulso 24/7 — Pi vivo mas NÃO gravando dentro do turno")
# Só existe porque o --heartbeat manda pulso o tempo todo. Sem este estado, um
# ffmpeg morto continuaria mostrando "Capturando agora".
r = rodar([hb(1, [cam("cam1", "posto-1-cam-frontal", False, seg_min_atras=90)],
               estado="ocioso")], turno_cobrindo_agora())
check("pulso fresco + runner ocioso dentro do turno = sem_captura",
      r["estado"] == "sem_captura", r["estado"])
check("câmera parada aparece como sem_sinal", r["cameras"][0]["estado"] == "sem_sinal")
r = rodar([hb(1, [cam("cam1", "f", True)], estado="capturando")], turno_cobrindo_agora())
check("runner capturando = capturando", r["estado"] == "capturando")
r = rodar([hb(1, [cam("cam1", "f", True)], estado="processando")], turno_cobrindo_agora())
check("runner processando NÃO vira sem_captura", r["estado"] == "capturando", r["estado"])
r = rodar([hb(1, [cam("cam1", "f", False)], estado="fora_de_turno")], turno_fora_de_agora())
check("pulso fora do turno segue em_repouso (sem alarme)", r["estado"] == "em_repouso")
r = rodar([hb(90, [cam("cam1", "f", False)], estado="ocioso")], turno_cobrindo_agora())
check("pulso VELHO tem prioridade: sem_sinal, não sem_captura",
      r["estado"] == "sem_sinal", r["estado"])

print("\n[10] helpers de turno")
j = M._turno_janelas_do_dia(
    [{"nome": "T", "intervalos": [{"inicio": "06:00", "fim": "11:30"},
                                  {"inicio": "12:30", "fim": "15:48"}],
      "dias_semana": [AGORA.isoweekday()], "ativo": True}], AGORA)
check("2 janelas do dia", len(j) == 2, j)
check("ordenadas", j[0][0] < j[1][0])
almoco = AGORA.replace(hour=12, minute=0, second=0, microsecond=0)
check("12:00 está no gap do almoço", M._dentro_de_janela(j, almoco) is None)
dentro = AGORA.replace(hour=9, minute=0, second=0, microsecond=0)
check("09:00 está na 1ª janela", M._dentro_de_janela(j, dentro) is not None)
j_off = M._turno_janelas_do_dia(
    [{"nome": "T", "intervalos": [{"inicio": "06:00", "fim": "11:30"}],
      "dias_semana": [(AGORA.isoweekday() % 7) + 1], "ativo": True}], AGORA)
check("turno de outro dia não gera janela hoje", j_off == [], j_off)
j_inativo = M._turno_janelas_do_dia(
    [{"nome": "T", "intervalos": [{"inicio": "06:00", "fim": "11:30"}],
      "dias_semana": [AGORA.isoweekday()], "ativo": False}], AGORA)
check("turno inativo é ignorado", j_inativo == [], j_inativo)

print(f"\n{'=' * 56}\n== {ok} ok, {fail} fail ==\n{'=' * 56}")
sys.exit(1 if fail else 0)
