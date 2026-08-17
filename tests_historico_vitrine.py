# ============================================================
# O HISTÓRICO NA VITRINE — presença sim, produtividade não.
#
# O corte original (`versao_instrumento < 9 → continue`) deixava o dashboard
# vazio: NÃO EXISTE nenhum evento V9 no banco, e há 12.878 eventos V1–V8.
#
# ⚠️ O CONTRATO DO CODEX NÃO FOI TOCADO. `productivity.py` está intacto; o
# histórico entra pela porta que ele já tinha construído — a abstenção por
# falta de evidência. Esta suíte protege as duas coisas ao mesmo tempo.
#
# MEDIDO no dia 14/08 (948 eventos reais, todos V7):
#   presença ........ 78,8%  (bate com a permanência da Fase 101 no mesmo dia)
#   cobertura ....... 100%   (`papel_pessoa` está preenchido em 100% do banco)
#   produtividade ... None, cobertura 0%
#
# E o motivo de a produtividade ficar de fora NÃO é o suposto no corte
# original. É este: no histórico a única evidência é `maos_maquina`, e ela só
# decide A FAVOR. `trabalho` é NULL em 100% dos eventos de TODAS as versões e
# `orientacao` está atrás do gate de calibração. Rodando o contrato sobre os
# principais de 14/08 sem neutralizar, sai `produtividade_pct = 100,0%` com
# 80% de cobertura — um número que parece medido e não tem como sair diferente
# de 100%. Pior que a tela vazia.
# ============================================================
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

from datetime import datetime, timezone   # noqa: E402
from backend.productivity import agregar_produtividade, classificar_observacao  # noqa: E402

RAIZ = os.path.dirname(os.path.abspath(__file__))
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


main = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()
contrato = open(os.path.join(RAIZ, "backend", "productivity.py"), encoding="utf-8").read()

T0 = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def ev(papel="operador", v=7, maos=None, trabalho=None, ini=0.0, fim=60.0, tid=1):
    return {"papel_pessoa": papel, "versao_instrumento": v, "maos_maquina": maos,
            "trabalho": trabalho, "orientacao": None, "principal": True,
            "tempo_inicio_s": ini, "tempo_fim_s": fim, "pessoa_track_id": tid,
            "video_id": "v1", "_capturado_em": T0, "_dia": "2026-08-14",
            "_cam_id": "cam1"}


def legado(e):
    """Replica EXATAMENTE o que o main.py faz com evento V<9."""
    d = dict(e)
    d["maos_maquina"] = None
    d["orientacao"] = None
    d["trabalho"] = None
    d["_instrumento_legado"] = True
    return d


print("\n[1] O contrato do Codex está INTACTO")
# Se alguém 'consertar' o dashboard mexendo no contrato, esta suíte reprova.
check("`productivity.py` não conhece o histórico — o filtro não vazou para lá",
      "_instrumento_legado" not in contrato and "KV_HISTORICO_PRESENCA" not in contrato)
check("a precedência de evidência dele continua a mesma",
      "1. ausência/papel do posto;" in contrato
      and "2. mão no torno (evidência positiva objetiva);" in contrato)
check("a nota de que V1-V8 elegeram P1 por heurística foi PRESERVADA",
      "V1–V8 elegeram P1 por permanência/bbox" in main)

print("\n[2] Presença: o histórico responde, e responde bem")
dia = ([legado(ev(ini=i * 60.0, fim=(i + 1) * 60.0)) for i in range(8)]
       + [legado(ev(papel="posto_vazio", ini=i * 60.0, fim=(i + 1) * 60.0, tid=-1))
          for i in range(8, 10)])
r = agregar_produtividade(dia, frentes_por_camera={}, janela_dias=30, agora=T0)
check("a tela deixa de ficar vazia", r["sem_dado"] is False)
check("presença sai do histórico", r["presenca_pct"] == 80.0, r["presenca_pct"])
check("posto vazio também", r["posto_vazio_pct"] == 20.0, r["posto_vazio_pct"])
check("com cobertura cheia — `papel_pessoa` está em 100% do banco",
      r["cobertura_presenca_pct"] == 100.0, r["cobertura_presenca_pct"])
check("os dois fecham 100%", abs(r["presenca_pct"] + r["posto_vazio_pct"] - 100) < 0.5)

print("\n[3] ⛔ Produtividade: o histórico NÃO responde, e é deliberado")
check("produtividade fica sem número", r["produtividade_pct"] is None, r["produtividade_pct"])
check("e a cobertura mostra o buraco em vez de preenchê-lo",
      r["cobertura_produtividade_pct"] == 0.0, r["cobertura_produtividade_pct"])
check("o tempo com operador vira INCONCLUSIVO, não improdutividade",
      r["inconclusivo_pct"] == 80.0, r["inconclusivo_pct"])
check("e a vitrine se declara não publicável", r["publicavel"] is False)

# ⭐ A PROVA DO VIÉS: sem a neutralização, o número só pode sair 100%.
sem_neutralizar = ([ev(maos=True, ini=i * 60.0, fim=(i + 1) * 60.0) for i in range(8)]
                   + [ev(papel="posto_vazio", ini=i * 60.0, fim=(i + 1) * 60.0, tid=-1)
                      for i in range(8, 10)])
r2 = agregar_produtividade(sem_neutralizar, frentes_por_camera={}, janela_dias=30, agora=T0)
check("⭐ SEM neutralizar, o histórico daria 100% produtivo — nunca outro valor",
      r2["produtividade_pct"] == 100.0 and r2["improdutividade_pct"] == 0.0, r2["produtividade_pct"])
# A razão estrutural: `maos_maquina` só produz evidência A FAVOR.
check("`maos=True` decide produtivo", classificar_observacao(ev(maos=True))[0] == "produtivo")
check("mas `maos=False` NÃO decide improdutivo — não há evidência contrária",
      classificar_observacao(ev(maos=False))[0] == "produtividade_inconclusiva")
check("e `trabalho` é o único sinal que diria improdutivo",
      classificar_observacao(ev(trabalho=False))[0] == "improdutivo")
check("o motivo medido está escrito no código, com o número",
      "produtividade_pct` = 100,0% com 80% de" in main
      and "estruturalmente" in main)

print("\n[4] A neutralização é explícita, não acidental")
check("os três campos de evidência são zerados no legado",
      '_ee["maos_maquina"] = None' in main
      and '_ee["orientacao"] = None' in main
      and '_ee["trabalho"] = None' in main)
check("e o evento é MARCADO como legado",
      '_ee["_instrumento_legado"] = True' in main)
# Sem a marcação, um evento legado com mãos no torno voltaria a decidir.
com_maos = legado(ev(maos=True))
check("evento legado NÃO decide produtividade nem com mãos no torno",
      classificar_observacao(com_maos)[0] == "produtividade_inconclusiva",
      classificar_observacao(com_maos))

print("\n[5] A proveniência viaja com o número")
check("o payload diz quantas leituras vieram do instrumento antigo",
      '"leituras_do_instrumento_legado"' in main)
check("e se o histórico está incluído", '"historico_incluido"' in main)
check("com o porquê: misturar em silêncio era a preocupação legítima",
      "Misturar instrumentos em silêncio" in main)

print("\n[6] Dá para voltar ao comportamento original sem deploy")
check("existe a chave", "KV_HISTORICO_PRESENCA" in main)
check("ligada por padrão — senão a tela segue vazia",
      'os.environ.get("KV_HISTORICO_PRESENCA", "on")' in main)
check("e `off` reproduz o corte original (só V9+)",
      "if _legado and not _HISTORICO_PRESENCA:" in main
      and "            continue" in main)

print("\n[7] Nada do que já funcionava se mexeu")
# A permanência da Fase 101 é o número determinístico e não passa por aqui.
from backend import pipeline as pl   # noqa: E402
perm = pl.permanencia_do_dia(
    [{"tempo_inicio_s": 0, "tempo_fim_s": 480, "papel_pessoa": "operador",
      "comportamento_label": "operar_torno", "principal": True},
     {"tempo_inicio_s": 480, "tempo_fim_s": 600, "papel_pessoa": "posto_vazio",
      "comportamento_label": "posto_vazio", "principal": True}], None)
check("a permanência continua 80/20 e não conhece versão de instrumento",
      perm["no_posto_pct"] == 80.0 and perm["fora_pct"] == 20.0, perm)
_p = open(os.path.join(RAIZ, "backend", "pipeline.py"),
          encoding="utf-8").read().split("def permanencia_do_dia")[1].split("\ndef ")[0]
check("e ela nem lê `versao_instrumento`", "versao_instrumento" not in _p)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
