"""Fase 65 — o relógio de parede é o da FÁBRICA, não o do servidor.

O painel de saúde usava `datetime.now().astimezone()` — o fuso do SERVIDOR. No
Render o container roda em UTC e a fábrica está em UTC−3:

  • a faixa de 24h aparecia 3h deslocada (parecia ter começado às 03h quando a
    gravação começou às 06h);
  • pior: o turno era comparado contra o relógio errado. Às 11h da fábrica
    (14h UTC) o painel dizia "em repouso" com o Pi gravando. Um painel de saúde
    que erra o estado é pior que não ter painel.

Rodar:  python tests_fuso_saude.py
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

from datetime import datetime, timezone, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from backend import main as mn  # noqa: E402

SP = ZoneInfo("America/Sao_Paulo")
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


class FakeQ:
    def __init__(self, sb, tabela, modo, payload=None):
        self.sb, self.tabela, self.modo, self.payload = sb, tabela, modo, payload
        self.eqs = {}

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self

    # Fase 81: o cliente real pagina por .range(); o dublê tem de paginar
    # também, senão a varredura "passa" no teste e trunca em produção.
    def range(self, ini, fim): self._rng = (ini, fim); return self

    def _fatia(self, linhas):
        r = getattr(self, "_rng", None)
        return linhas if r is None else linhas[r[0]: r[1] + 1]

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        casam = [l for l in linhas
                 if all(l.get(c) == v for c, v in self.eqs.items())]
        if self.modo == "update":
            for l in casam:
                l.update(self.payload)
                self.sb.escritas.append((self.tabela, dict(self.payload)))
        return types.SimpleNamespace(data=self._fatia([dict(l) for l in casam]))


class FakeSB:
    def __init__(self, dados=None):
        self.dados = dados or {}
        self.escritas = []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome, "select")
            def update(self, p): return FakeQ(sb, nome, "update", p)
            def insert(self, p): return FakeQ(sb, nome, "insert", p)
        return T()


class User:
    empresa = "U"
    email = "gestor@u.com"


TURNO = [{"nome": "Turno 1", "ativo": True,
          "intervalos": [{"inicio": "06:00", "fim": "11:30"}],
          "dias_semana": [1, 2, 3, 4, 5]}]


def montar(agora_utc, fuso="America/Sao_Paulo", pulsos_min=(0, 5, 10)):
    """Instala o ambiente e devolve a resposta de /saude para `agora_utc`."""
    hbs = [{
        "empresa": "U", "processo": "T", "device_id": "pi-1",
        "runner_versao": "1.0", "estado": "capturando",
        "cameras": [{"cam_id": "cam1", "nome": "cam1", "gravando": True,
                     "ultimo_segmento_em": (agora_utc - timedelta(minutes=m)).isoformat()}],
        "disco_livre_gb": 10.7, "disco_uso_pct": 60, "cpu_temp_c": 50,
        "uptime_s": 9999, "turno_janela": None, "turno_deadline": None,
        "recebido_em": (agora_utc - timedelta(minutes=m)).isoformat(),
    } for m in pulsos_min]
    sb = FakeSB({
        "heartbeats_edge": hbs,
        "turnos_processo": [{**t, "empresa": "U", "processo": "T"} for t in TURNO],
        "contexto_processo": [{"empresa": "U", "processo": "T", "fuso_horario": fuso}],
    })
    mn.make_supabase_client = lambda *a, **k: sb
    mn._processo_nome = lambda _sb, _u, _pid: "T"

    real_dt = mn.datetime
    class DT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return agora_utc if tz else agora_utc.replace(tzinfo=None)
    mn.datetime = DT
    try:
        return mn.saude_edge("proc-1", User()), sb
    finally:
        mn.datetime = real_dt


print("\n[1] O caso do print — 06:00 na fábrica, 09:00 UTC")
# Servidor em UTC diria 09:00; a fábrica está às 06:00, DENTRO do turno.
agora = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)   # quinta
r, _ = montar(agora)
check("estado é 'capturando'", r["estado"] == "capturando", r["estado"])
check("relógio local é 06:00 (não 09:00 do servidor)",
      r["agora_local"] == "06:00", r["agora_local"])
check("fuso vai no payload (erro de fuso deixa de ser silencioso)",
      r["fuso"] == "America/Sao_Paulo", r.get("fuso"))
check("a janela ativa é a do turno", r["turno"]["ativa"]["inicio"] == "06:00",
      r["turno"]["ativa"])

print("\n[2] O bug que mais doía — 11h na fábrica, 14h UTC")
# Pelo relógio do SERVIDOR (14:00) o turno 06:00–11:30 já teria acabado e o
# painel diria "em repouso" — com o Pi gravando.
agora = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)   # 11:00 em SP
r, _ = montar(agora)
check("11:00 na fábrica ainda está DENTRO do turno",
      r["turno"]["ativa"] is not None, r["turno"])
check("estado 'capturando', não 'em_repouso'", r["estado"] == "capturando", r["estado"])
check("relógio local é 11:00", r["agora_local"] == "11:00", r["agora_local"])

# Contraprova: com o fuso errado (UTC) o painel volta a mentir.
r_utc, _ = montar(agora, fuso="UTC")
check("com fuso UTC o mesmo instante vira 'em_repouso' (era o bug)",
      r_utc["estado"] == "em_repouso", r_utc["estado"])
check("e o relógio mostra 14:00", r_utc["agora_local"] == "14:00", r_utc["agora_local"])

print("\n[3] Fim de turno de verdade — 12h na fábrica")
agora = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)   # 12:00 em SP
r, _ = montar(agora)
check("fora do turno → em repouso (sem alarme)", r["estado"] == "em_repouso", r["estado"])
check("nenhuma janela ativa", r["turno"]["ativa"] is None, r["turno"])

print("\n[4] A faixa de 24h anda no relógio da fábrica")
agora = datetime(2026, 7, 30, 9, 0, tzinfo=timezone.utc)     # 06:00 em SP
r, _ = montar(agora)
cob = r["cobertura_24h"]
check("96 blocos de 15 min", len(cob) == 96, len(cob))
check("todos os blocos carregam o offset da fábrica",
      all(b["inicio"].endswith("-03:00") for b in cob),
      cob[0]["inicio"])
esperados = [b for b in cob if b["esperado"]]
horas_esp = {b["inicio"][11:13] for b in esperados}
check("o turno 06:00–11:30 marca as horas 06..11 (não 03..08)",
      horas_esp == {"06", "07", "08", "09", "10", "11"}, sorted(horas_esp))
check("o último bloco é o de AGORA (06:00 local)",
      cob[-1]["inicio"][11:16] in ("05:45", "06:00"), cob[-1]["inicio"])
check("os pulsos recentes aparecem preenchidos",
      any(b["houve"] for b in cob[-3:]), [b["houve"] for b in cob[-3:]])

print("\n[5] Resolução do fuso — configurado > env > padrão, e nunca quebra")
sb = FakeSB({"contexto_processo": [
    {"empresa": "U", "processo": "T", "fuso_horario": "America/Manaus"},
    {"empresa": "U", "processo": "Vazio", "fuso_horario": None},
]})
_tz, nome = mn.fuso_do_processo(sb, "U", "T")
check("usa o fuso configurado no processo", nome == "America/Manaus", nome)
_tz, nome = mn.fuso_do_processo(sb, "U", "Vazio")
check("NULL cai no padrão do ambiente", nome == mn.FUSO_PADRAO, nome)
_tz, nome = mn.fuso_do_processo(sb, "U", "NaoExiste")
check("processo inexistente cai no padrão", nome == mn.FUSO_PADRAO, nome)


class SBQuebrado:
    def table(self, _n):
        raise RuntimeError("banco fora")


_tz, nome = mn.fuso_do_processo(SBQuebrado(), "U", "T")
check("falha de leitura não derruba o painel", nome == mn.FUSO_PADRAO, nome)
check("nome inválido cai no fallback fixo em vez de explodir",
      mn._fuso("Marte/Olympus") is mn._FUSO_FALLBACK)
check("fallback é UTC-3 (erra 1h no verão, não 3h o ano todo)",
      mn._FUSO_FALLBACK.utcoffset(None) == timedelta(hours=-3))

print("\n[6] Gravar o fuso — valida antes, porque erro aqui é silencioso")
sb = FakeSB({"contexto_processo": [
    {"empresa": "U", "processo": "T", "fuso_horario": "America/Sao_Paulo"}]})
mn.make_supabase_client = lambda *a, **k: sb
mn._processo_nome = lambda _sb, _u, _pid: "T"

r = mn.setar_fuso("proc-1", mn.FusoBody(fuso_horario="America/Manaus"), User())
check("grava um IANA válido", r["efetivo"] == "America/Manaus", r)
check("persistiu na coluna",
      sb.dados["contexto_processo"][0]["fuso_horario"] == "America/Manaus")

try:
    mn.setar_fuso("proc-1", mn.FusoBody(fuso_horario="Brasilia/DF"), User())
    check("nome inválido é recusado", False, "não levantou")
except HTTPException as e:
    check("nome inválido recusado com 400", e.status_code == 400, e.detail)
check("a recusa não sobrescreveu o que estava lá",
      sb.dados["contexto_processo"][0]["fuso_horario"] == "America/Manaus")

r = mn.setar_fuso("proc-1", mn.FusoBody(fuso_horario=None), User())
check("null volta ao padrão do ambiente", r["efetivo"] == mn.FUSO_PADRAO, r)

check("todas as sugestões são fusos válidos",
      all(mn._fuso(t) is not mn._FUSO_FALLBACK for t in mn.FUSOS_SUGERIDOS),
      mn.FUSOS_SUGERIDOS)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
