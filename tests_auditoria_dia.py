"""Fase 79 — auditar um dia que ficou invisível.

O dia 29 tinha 463 eventos com `validado_humano=True` e NENHUM validado por
gente: 245 de origem `posto_vazio`, 163 de `auditoria`. Nos dois casos a flag é
o MECANISMO que os mantém fora da fila. O dia estava correto (o operador
faltou), mas se estivesse errado não haveria como perceber — dia inteiramente
classificado como posto vazio é invisível por construção.

Rodar:  python tests_auditoria_dia.py
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


class FakeQ:
    def __init__(self, sb, tabela):
        self.sb, self.tabela, self.eqs, self.ins = sb, tabela, {}, {}

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def in_(self, c, vs): self.ins[c] = list(vs); return self

    def execute(self):
        linhas = self.sb.dados.get(self.tabela, [])
        r = [l for l in linhas
             if all(l.get(c) == v for c, v in self.eqs.items())
             and all(l.get(c) in vs for c, vs in self.ins.items())]
        return types.SimpleNamespace(data=[dict(l) for l in r])


class FakeSB:
    def __init__(self, dados):
        self.dados = dados
        self.escritas = []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome)
            def update(self, p): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
            def insert(self, p): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
        return T()


def ev(i, label, *, papel=None, origem="posto_vazio", vid="v1"):
    return {"id": f"e{i}", "video_id": vid, "empresa": "U", "processo": "T",
            "comportamento_label": label, "label_corrigido": None,
            "descricao_bruta": "posto de trabalho vazio (operador ausente)",
            "tempo_inicio_s": i * 60, "tempo_fim_s": i * 60 + 60,
            "principal": True, "papel_pessoa": papel,
            "origem_validacao": origem, "validado_humano": True,
            "validacao_correto": None, "confianca": 0.9, "n_amostras": 4,
            "pessoa_track_id": 1}


# 20 minutos de posto vazio + 1 de operação. 95% vazio → atípico.
EVENTOS = [ev(i, "posto_vazio") for i in range(20)]
EVENTOS += [ev(20, "operar_torno", papel="operador", origem="pendente")]
# 3 deles com a contradição da C1 (operador rastreado no posto).
for i in (5, 10, 15):
    EVENTOS[i]["papel_pessoa"] = "operador"

SB = FakeSB({
    "eventos": EVENTOS,
    "videos": [{"id": "v1", "empresa": "U", "processo": "T",
                "nome": "seg_20260729_070000.mp4", "cam_id": "cam1",
                "duracao_s": 1260, "processado_em": "2026-07-29T10:00:00"}],
})

print("\n[1] O dia abre — e nada é validado")
r = pl.auditar_dia(SB, "U", "T", "2026-07-29")
check("achou os eventos do dia", r["eventos"] == 21, r["eventos"])
check("NÃO escreveu nada (auditar ≠ validar)", not SB.escritas, SB.escritas)
check("lista os vídeos do dia", len(r["videos"]) == 1 and r["videos"][0]["cam_id"] == "cam1")
check("a nota deixa claro que é só leitura", "só leitura" in r["nota"])

print("\n[2] Amostragem: início, meio e fim de cada bloco")
check("agrupou em blocos contíguos", len(r["blocos"]) == 2, r["blocos"])
check("o bloco de posto vazio tem os 20 eventos",
      r["blocos"][0]["eventos"] == 20, r["blocos"][0])
check("a amostra é MUITO menor que a lista",
      len(r["amostras"]) <= 6, len(r["amostras"]))
vazio = [a for a in r["amostras"] if a["rotulo"] == "posto_vazio"]
check("3 amostras do bloco longo", len(vazio) == 3, vazio)
check("marca início, meio e fim",
      {a["posicao"] for a in vazio} == {"inicio", "meio", "fim"},
      [a["posicao"] for a in vazio])
check("a amostra carrega a hora de parede", all(":" in a["hora"] for a in vazio))
check("e diz o tamanho do bloco que representa",
      vazio[0]["bloco_eventos"] == 20, vazio[0])
check("bloco de 1 evento vira amostra única",
      any(a["posicao"] == "unico" for a in r["amostras"]),
      [a["posicao"] for a in r["amostras"]])

r2 = pl.auditar_dia(SB, "U", "T", "2026-07-29", por_bloco=1)
check("por_bloco=1 devolve só uma amostra por bloco",
      len([a for a in r2["amostras"] if a["rotulo"] == "posto_vazio"]) == 1)

print("\n[3] Dia atípico se destaca sozinho")
check("mede o % de posto vazio", r["posto_vazio_pct"] > 90, r["posto_vazio_pct"])
check("marca como atípico acima do limiar", r["atipico"] is True)
check("e informa qual é o limiar", r["limiar_atipico"] == pl.VAZIO_ATIPICO_PCT)

SB2 = FakeSB({"eventos": [ev(i, "operar_torno", origem="pendente") for i in range(10)],
              "videos": SB.dados["videos"]})
r3 = pl.auditar_dia(SB2, "U", "T", "2026-07-29")
check("dia normal NÃO é atípico", r3["atipico"] is False, r3["posto_vazio_pct"])

print("\n[4] A contradição da C1 aparece sobre o que JÁ está gravado")
check("conta os eventos posto_vazio com operador rastreado",
      r["contradicoes_c1"] == 3, r["contradicoes_c1"])
check("dia sem contradição devolve zero", r3["contradicoes_c1"] == 0)

print("\n[5] Escopo e bordas")
check("outro dia não devolve nada",
      pl.auditar_dia(SB, "U", "T", "2026-07-28")["eventos"] == 0)
check("dia vazio não quebra",
      pl.auditar_dia(SB, "U", "T", "2026-07-28")["atipico"] is False)
check("outra empresa não vaza", pl.auditar_dia(SB, "OUTRA", "T", "2026-07-29")["eventos"] == 0)

from pathlib import Path  # noqa: E402
mn = Path("backend/main.py").read_text()
check("o endpoint é GET", '@app.get("/processos/{processo_id}/auditoria/dia")' in mn)
check("e o docstring diz por que existe",
      "INVIS" in mn.upper() and "Auditar não é validar" in mn)
pipe = Path("backend/pipeline.py").read_text()
check("a análise diária marca o dia atípico", '"atipico_vazio"' in pipe)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
