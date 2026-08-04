"""Fase 72 — a guarda que impede reprocessar e duplicar tudo.

`etapa_persistir` faz `insert` em `videos` sem upsert: processar o mesmo
arquivo duas vezes cria uma SEGUNDA linha de vídeo e um SEGUNDO conjunto de
eventos, e tudo passa a contar em dobro. Enquanto não houver substituição
idempotente, a duplicata é BARRADA com erro claro.

A guarda não é a correção — ela troca corrupção silenciosa por erro alto.

Rodar:  python tests_guarda_duplicata.py
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

from pathlib import Path  # noqa: E402
from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


_SEQ = [0]


class FakeQ:
    def __init__(self, sb, tabela, modo, payload=None):
        self.sb, self.tabela, self.modo, self.payload = sb, tabela, modo, payload
        self.eqs = {}

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self

    def range(self, ini, fim): self._rng = (ini, fim); return self

    def _fatia(self, linhas):
        r = getattr(self, "_rng", None)
        return linhas if r is None else linhas[r[0]: r[1] + 1]

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        if self.modo == "insert":
            # O insert de eventos vem em LOTE (lista); o de vídeo vem só.
            lote = self.payload if isinstance(self.payload, list) else [self.payload]
            novas = []
            for pay in lote:
                _SEQ[0] += 1
                n = {"id": f"v-{_SEQ[0]}", **pay}
                linhas.append(n); novas.append(dict(n))
                self.sb.escritas.append((self.tabela, n["id"]))
            return types.SimpleNamespace(data=novas)
        casam = [l for l in linhas if all(l.get(c) == v for c, v in self.eqs.items())]
        return types.SimpleNamespace(data=self._fatia([dict(l) for l in casam]))


class FakeSB:
    def __init__(self, dados=None):
        self.dados = dados or {}
        self.escritas = []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome, "select")
            def insert(self, p): return FakeQ(sb, nome, "insert", p)
            def update(self, p): return FakeQ(sb, nome, "update", p)
        return T()


CAM = "empresa/proc/seg_20260730_064000.mp4"

print("\n[1] Detecta o caminho já processado")
sb = FakeSB({"videos": [
    {"id": "v1", "empresa": "U", "processo": "T", "caminho": CAM,
     "nome": "seg.mp4", "processado_em": "2026-07-30T09:00:00"},
]})
check("acha o vídeo existente",
      (pl.video_ja_processado(sb, "U", "T", CAM) or {}).get("id") == "v1")
check("caminho novo passa livre", pl.video_ja_processado(sb, "U", "T", "outro.mp4") is None)
check("caminho nulo passa livre (upload manual sem storage)",
      pl.video_ja_processado(sb, "U", "T", None) is None)
check("outra EMPRESA não bloqueia", pl.video_ja_processado(sb, "OUTRA", "T", CAM) is None)
check("outro PROCESSO não bloqueia", pl.video_ja_processado(sb, "U", "Outro", CAM) is None)


class SBQuebrado:
    def table(self, _n): raise RuntimeError("banco fora")


check("falha de leitura NÃO bloqueia (a guarda não pode virar o gargalo)",
      pl.video_ja_processado(SBQuebrado(), "U", "T", CAM) is None)

print("\n[2] A guarda levanta com mensagem acionável")
try:
    pl._barrar_duplicata(sb, "U", "T", CAM)
    subiu, msg = False, ""
except pl.VideoJaProcessado as e:
    subiu, msg = True, str(e)
check("levanta VideoJaProcessado", subiu)
check("diz o que aconteceria (contar em dobro)", "em dobro" in msg, msg)
check("aponta o vídeo existente", "v1" in msg, msg)
check("aponta a documentação", "problemas_conhecidos" in msg, msg)
check("ensina o caso de borda do segmento em erro",
      "concluído" in msg and "ERRO" in msg, msg)

try:
    pl._barrar_duplicata(sb, "U", "T", "novo.mp4")
    check("caminho novo não levanta", True)
except pl.VideoJaProcessado:
    check("caminho novo não levanta", False)

print("\n[3] O INSERT é barrado no ponto da escrita")
INFO = {"duracao_s": 300.0, "fps": 10.0, "largura": 640, "altura": 480}
EV = [{"pessoa_track_id": 1, "comportamento_label": "operar_torno",
       "descricao_bruta": "d", "tempo_inicio_s": 0, "tempo_fim_s": 60,
       "frame_inicio": 0, "frame_fim": 30, "bbox_inicio": [0, 0, 10, 10],
       "zona_contexto": None, "n_amostras": 3, "confianca": 0.9, "principal": True}]

sb2 = FakeSB({"videos": [
    {"id": "v1", "empresa": "U", "processo": "T", "caminho": CAM,
     "processado_em": "2026-07-30T09:00:00"},
]})
try:
    pl.etapa_persistir(sb2, "U", "T", "/tmp/v.mp4", INFO, EV, [1],
                       {"operar_torno": "torno"}, lambda d: "pendente",
                       caminho_storage=CAM)
    barrou = False
except pl.VideoJaProcessado:
    barrou = True
check("etapa_persistir recusa a duplicata", barrou)
check("e NÃO escreveu nada", not sb2.escritas, sb2.escritas)
check("nenhuma segunda linha de vídeo", len(sb2.dados["videos"]) == 1)
check("nenhum evento gravado", not sb2.dados.get("eventos"), sb2.dados.get("eventos"))

sb3 = FakeSB({"videos": []})
pl.etapa_persistir(sb3, "U", "T", "/tmp/v.mp4", INFO, EV, [1],
                   {"operar_torno": "torno"}, lambda d: "pendente",
                   caminho_storage=CAM)
check("caminho inédito grava normalmente", len(sb3.dados["videos"]) == 1)
check("e os eventos entram", len(sb3.dados.get("eventos") or []) == 1)

print("\n[4] A guarda roda ANTES da inferência (não paga VLM para recusar)")
src = Path("backend/pipeline.py").read_text()
i_guarda = src.index('_barrar_duplicata(sb, empresa, processo, caminho_storage)\n\n    progress_cb("setup", 0')
i_detecta = src.index("etapa_detectar_e_amostrar(\n        yolo, video_path")
check("a checagem precede a detecção", i_guarda < i_detecta)
check("e também existe no ponto do insert",
      src.count("_barrar_duplicata(sb, empresa, processo, caminho_storage)") == 2)

print("\n[5] O problema está documentado, não só barrado")
doc = Path("docs/problemas_conhecidos.md").read_text()
check("documento existe e nomeia o problema", "Reprocessar um vídeo DUPLICA tudo" in doc)
check("mostra a linha exata do insert", 'insert(linha_video)' in doc)
check("explica que a guarda NÃO é a correção", "não** é a correção" in doc)
check("descreve como consertar de verdade", "Substituição idempotente" in doc)
check("lembra do binário one-shot", "one-shot" in doc)
check("lembra dos frames órfãos", "frames órfãos" in doc)
check("registra por que não foi feito agora", "parou de crescer" in doc)
check("registra o caso de borda do segmento em erro",
      "segmento em ERRO" in doc or "ERRO com vídeo já gravado" in doc)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
