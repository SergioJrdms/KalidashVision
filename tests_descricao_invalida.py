"""Fase 70 — a tela pergunta pela DESCRIÇÃO antes do RÓTULO.

O gestor errou porque a tela perguntou na ordem errada: ofereceu "Corrigir" e
"Descartar" sobre o rótulo, sem nunca perguntar se a descrição batia com a
imagem. O VLM tinha alucinado um operador num posto vazio; corrigir o rótulo
daquele evento virou o mapa que contaminou ~91 eventos.

Dois erros, tratamentos opostos:
  (a) descrição certa, rótulo errado → corrigir o rótulo faz sentido;
  (b) descrição ERRADA               → descartar E queimar a frase.

Rodar:  python tests_descricao_invalida.py
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
from backend import main as mn  # noqa: E402
from backend import pipeline as pl  # noqa: E402

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
    def eq(self, c, v): self.eqs[c] = v; return self

    def execute(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        casam = [l for l in linhas if all(l.get(c) == v for c, v in self.eqs.items())]
        return types.SimpleNamespace(data=[dict(l) for l in casam])


class FakeSB:
    def __init__(self, dados=None): self.dados = dados or {}
    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome, "select")
        return T()


print("\n[1] A transição grava um estado PRÓPRIO, não um descarte")
t = mn._montar_update_validacao("descricao_invalida", "operar_torno", None)
check("sai das métricas (validacao_correto=False)", t["validacao_correto"] is False, t)
check("marca descricao_invalida", t["descricao_invalida"] is True, t)
check("limpa label_corrigido — não há rótulo certo para cena que não houve",
      t["label_corrigido"] is None, t)
check("é decisão humana", t["origem_validacao"] == "humano", t)

d = mn._montar_update_validacao("descartar", "operar_torno", None)
check("descartar NÃO marca descricao_invalida", "descricao_invalida" not in d, d)
check("descartar mantém label_corrigido intocado", "label_corrigido" not in d, d)

r = mn._montar_update_validacao("reabrir", "x", None)
check("reabrir limpa a marca", r["descricao_invalida"] is False, r)

print("\n[2] O status é distinguível de 'descartado'")
check("descricao_invalida tem status próprio",
      mn._status_efetivo({"validado_humano": True, "validacao_correto": False,
                          "descricao_invalida": True}) == "descricao_invalida")
check("descartado continua descartado",
      mn._status_efetivo({"validado_humano": True, "validacao_correto": False,
                          "descricao_invalida": False}) == "descartado")
check("pendente vem antes de tudo",
      mn._status_efetivo({"validado_humano": False, "descricao_invalida": True}) == "pendente")

print("\n[3] A frase QUEIMADA sai do aprendizado, para sempre")
FRASE = "operando o torno, manipulando a máquina"
OUTRA = "medindo a peça com paquímetro"


def ev(id_, desc, label, *, corr=None, correto=True, invalida=False):
    return {"id": id_, "empresa": "U", "processo": "T",
            "comportamento_label": label, "label_corrigido": corr,
            "descricao_bruta": desc, "validacao_correto": correto,
            "principal": True, "origem_validacao": "humano",
            "validado_humano": True, "descricao_invalida": invalida}


sb = FakeSB({"eventos": [
    # A frase foi declarada INVÁLIDA uma vez.
    ev("i1", FRASE, "operar_torno", invalida=True, correto=False),
    # Estes tentam ensinar a partir da mesma frase queimada.
    ev("c1", FRASE, "operar_torno", corr="posto_vazio"),
    ev("c2", FRASE, "operar_torno"),
    ev("d1", FRASE, "operar_torno", correto=False),
    # Esta frase é sã e continua ensinando.
    ev("s1", OUTRA, "medir_peca"),
    ev("s2", OUTRA, "medir_peca"),
], "comportamentos": [
    {"label": "medir_peca", "descricao": "mede a peça", "empresa": "U", "processo": "T"},
    {"label": "operar_torno", "descricao": "opera", "empresa": "U", "processo": "T"},
]})
mem = pl.carregar_memoria_do_negocio(sb, "U", "T")
check("a frase entra na lista de queimadas",
      FRASE in mem["descricoes_queimadas"], mem["descricoes_queimadas"])
check("nenhuma correção nasce da frase queimada",
      FRASE not in (mem["correcoes_aprendidas"] or {}), mem["correcoes_aprendidas"])
vocab = {v["label"]: v["n_confirmacoes"] for v in mem["vocabulario"]}
check("a frase queimada não confirma rótulo nenhum",
      "operar_torno" not in vocab, vocab)
check("a frase queimada não descarta rótulo nenhum",
      "operar_torno" not in (mem["descartados"] or {}), mem["descartados"])
check("a frase SÃ continua ensinando", vocab.get("medir_peca") == 2, vocab)

print("\n[4] O vocabulário mandado ao VLM não devolve a alucinação")
bloco = pl.construir_bloco_vocabulario({
    "vocabulario": [{"label": "operar_torno", "descricao": FRASE, "n_confirmacoes": 9},
                    {"label": "medir_peca", "descricao": OUTRA, "n_confirmacoes": 4}],
    "descricoes_queimadas": [FRASE],
})
check("a frase queimada não volta como vocabulário", FRASE not in bloco, bloco)
check("a frase sã continua no bloco", OUTRA in bloco, bloco)

print("\n[5] A ORDEM das perguntas — é o ponto central da mudança")
val = Path("frontend/src/pages/Validacao.tsx").read_text()
i_desc = val.index('passo === "descricao" && phase === "idle"')
i_rot = val.index('phase === "idle" && passo === "rotulo"')
check("o bloco da DESCRIÇÃO vem antes do bloco do RÓTULO no fluxo",
      i_desc < i_rot)
check("a pergunta 1 é sobre a imagem",
      "É isso que está acontecendo nas imagens?" in val)
check("a pergunta 2 só aparece depois", 'A descrição bate. E o rótulo' in val)
check("o passo reseta a cada card (senão a ordem se perde)",
      'setPasso("descricao");' in val)
check("dá para voltar para a descrição", "voltar para a descrição" in val)
check("a descrição continua visível no passo 2 (divergência é o sinal)",
      'passo === "rotulo" || phase !== "idle"' in val)
check("o texto avisa para NÃO corrigir o rótulo no caso (b)",
      "Não corrija o rótulo neste caso" in val)

duv = Path("frontend/src/pages/Duvidas.tsx").read_text()
check("a fila de dúvidas oferece a mesma saída",
      'onValidar("descricao_invalida")' in duv)
check("e mostra a descrição junto", "O Prism disse que viu" in duv)

print("\n[6] Escopo: vale para o EVENTO, nunca reclassifica o passado")
sql = Path("sql/schema.sql").read_text()
bloco70 = sql[sql.index("Fase 70 — `descricao_invalida`"):]
check("a coluna existe", "descricao_invalida boolean not null default false" in bloco70)
check("o escopo está escrito", "vale para o EVENTO marcado" in bloco70)
check("e diz que não reclassifica o passado",
      "nunca reclassifica o passado" in bloco70)
check("nenhum UPDATE em massa por descrição no bloco",
      "update eventos" not in bloco70.lower(), "há update em massa")

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
