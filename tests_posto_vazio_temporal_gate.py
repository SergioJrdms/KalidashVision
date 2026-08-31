"""Gate temporal conservador da decisão final de papel por minuto."""
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


def evento(papel, inicio, fim, *, n_observacoes=None, n_amostras=None,
           origens=None):
    e = {
        "papel_pessoa": papel,
        "tempo_inicio_s": float(inicio),
        "tempo_fim_s": float(fim),
    }
    if n_observacoes is not None:
        e["n_observacoes"] = n_observacoes
    if n_amostras is not None:
        e["n_amostras"] = n_amostras
    if origens is not None:
        e["origens"] = origens
    return e


def papel(*eventos):
    no_bucket = [
        (e, min(60.0, e["tempo_fim_s"]) - max(0.0, e["tempo_inicio_s"]))
        for e in eventos
    ]
    return pl._papel_do_minuto(no_bucket, 0.0, 60.0)


print("[gate temporal posto_vazio]")

# A) A duração não fabrica a segunda observação.
a = evento("posto_vazio", 0, 5, n_observacoes=1)
check("A · um slot vazio de 5 s vira abstenção", papel(a) is None, papel(a))

# Fronteira: o evento agrega três observações, mas só [60, 68) sobrepõe o
# segundo minuto. As duas observações anteriores não podem satisfazer >= 2.
def observacao_vazia(tempo_s):
    return {
        "tempo_s": float(tempo_s), "frame_idx": int(tempo_s),
        "track_id": pl.POSTO_VAZIO_TID, "descricao": pl.POSTO_VAZIO_DESC,
        "bbox": None, "zona": "posto", "papel": "posto_vazio",
        "origem_gate": "posto_vazio",
    }


fronteira_eventos = pl.etapa_segmentar_eventos(
    [observacao_vazia(t) for t in (44, 52, 60)],
    lambda *_a: pl.POSTO_VAZIO_LABEL,
    8.0,
)
fronteira_bucket = [(fronteira_eventos[0], 8.0)]
fronteira_obs = pl._n_observacoes_reais_posto_vazio(
    fronteira_bucket, 60.0, 120.0)
check("A · fronteira conta somente o único slot que sobrepõe o novo minuto",
      len(fronteira_eventos) == 1 and fronteira_obs == 1
      and pl._papel_do_minuto(fronteira_bucket, 60.0, 120.0) is None,
      (fronteira_eventos, fronteira_obs))

# B) Três medições existem, mas a fatia inconclusiva deixa margem em 7 s.
b_vazio = evento("posto_vazio", 0, 15, origens={"posto_vazio": 3})
b_contra = evento(None, 11, 15, origens={"resgate_cam2": 1})
check("B · três slots com margem menor que 8 s viram abstenção",
      papel(b_vazio, b_contra) is None, papel(b_vazio, b_contra))

# C) Fatias exclusivas: 32 s de vazio contra 28 s de ponte/inconclusivo.
c_vazio = evento("posto_vazio", 0, 32, origens={"posto_vazio": 4})
c_contra = evento(None, 32, 60, origens={"ponte_temporal": 4})
check("C · vazio 32 s e anti 28 s viram abstenção",
      papel(c_vazio, c_contra) is None, papel(c_vazio, c_contra))

# D) A mesma conta acima do piso preserva a decisão anterior.
d_vazio = evento("posto_vazio", 0, 36, n_observacoes=2)
d_contra = evento(None, 36, 60,
                  origens={"confirmacao_presenca_indisponivel": 3})
check("D · vazio 36 s e anti 24 s preservam posto_vazio",
      papel(d_vazio, d_contra) == "posto_vazio",
      papel(d_vazio, d_contra))

# E) Minuto inequivocamente vazio com várias medições reais.
e = evento("posto_vazio", 0, 60, n_amostras=6)
check("E · minuto claramente vazio permanece posto_vazio",
      papel(e) == "posto_vazio", papel(e))

# Um evento longo sem contador não ganha observações implícitas por duração.
e_sem_metadado = evento("posto_vazio", 0, 60)
check("E · duração longa sem metadado não cria observações fictícias",
      papel(e_sem_metadado) is None, papel(e_sem_metadado))

# F) O gate não toca papéis já decididos como operador.
f = evento("operador", 0, 60, n_observacoes=6,
           origens={"analisado": 6})
check("F · operador existente permanece operador",
      papel(f) == "operador", papel(f))

# G) A decisão antiga de empate/dúvida continua sendo abstenção.
g_vazio = evento("posto_vazio", 0, 30, origens={"posto_vazio": 4})
g_duvida = evento(None, 30, 60, origens={"falha_descricao_vlm": 4})
check("G · empate continua None", papel(g_vazio, g_duvida) is None,
      papel(g_vazio, g_duvida))

# H) Fora do posto conserva o próprio papel e não entra em anti_empty.
h_fora = evento(pl.PAPEL_OPERADOR_FORA, 0, 60, n_observacoes=6,
                origens={"fora_do_posto": 6})
check("H · operador_fora não é reinterpretado",
      papel(h_fora) == pl.PAPEL_OPERADOR_FORA, papel(h_fora))
h_vazio = evento("posto_vazio", 0, 32, origens={"posto_vazio": 4})
h_fora_depois = evento(pl.PAPEL_OPERADOR_FORA, 32, 60,
                       origens={"fora_do_posto": 4})
check("H · operador_fora não conta como anti_empty",
      papel(h_vazio, h_fora_depois) == "posto_vazio",
      papel(h_vazio, h_fora_depois))

# Visitante é presença de terceiro, não evidência física do operador.
visitante_vazio = evento("posto_vazio", 0, 32, origens={"posto_vazio": 4})
visitante = evento("visitante", 32, 60, n_observacoes=4,
                   origens={"analisado": 4})
check("visitante é neutro para anti_empty",
      papel(visitante_vazio, visitante) == "posto_vazio",
      papel(visitante_vazio, visitante))


print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
