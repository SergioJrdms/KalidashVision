"""Fase 110 — persistência real das auditorias e degradação sem schema."""
import os
import sys
import types

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
for modulo in [
    "cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
    "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(modulo, types.ModuleType(modulo))
sys.modules["dotenv"].load_dotenv = lambda *_a, **_k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *_a, **_k: None
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


class FakeQuery:
    def __init__(self, sb, tabela, acao, payload=None):
        self.sb, self.tabela, self.acao = sb, tabela, acao
        self.payload = payload
        self.iguais = {}
        self.diferentes = {}
        self.faixa = None

    def select(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def range(self, inicio, fim):
        self.faixa = (inicio, fim)
        return self

    def eq(self, campo, valor):
        self.iguais[campo] = valor
        return self

    def neq(self, campo, valor):
        self.diferentes[campo] = valor
        return self

    def _filtradas(self):
        linhas = self.sb.dados.setdefault(self.tabela, [])
        saida = [
            l for l in linhas
            if all(l.get(c) == v for c, v in self.iguais.items())
            and all(l.get(c) != v for c, v in self.diferentes.items())
        ]
        if self.faixa:
            inicio, fim = self.faixa
            saida = saida[inicio:fim + 1]
        return saida

    def execute(self):
        if self.acao == "select":
            return types.SimpleNamespace(data=[dict(l) for l in self._filtradas()])
        if self.acao == "update":
            for linha in self._filtradas():
                linha.update(self.payload)
            return types.SimpleNamespace(data=[dict(l) for l in self._filtradas()])

        linhas = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.tabela == "comportamentos" and self.sb.sem_coluna_comportamento:
            if any("exige_decisao_humana" in l for l in linhas):
                raise RuntimeError("column exige_decisao_humana does not exist")
        if self.tabela == "eventos":
            for coluna in self.sb.colunas_evento_ausentes:
                if any(coluna in l for l in linhas):
                    self.sb.erros_eventos.append(coluna)
                    raise RuntimeError(f"column {coluna} does not exist")

        inseridas = []
        for original in linhas:
            linha = dict(original)
            linha.setdefault("id", f"{self.tabela}-{self.sb.proximo_id}")
            self.sb.proximo_id += 1
            self.sb.dados.setdefault(self.tabela, []).append(linha)
            inseridas.append(dict(linha))
        return types.SimpleNamespace(data=inseridas)


class FakeTable:
    def __init__(self, sb, nome):
        self.sb, self.nome = sb, nome

    def select(self, *_a, **_k):
        return FakeQuery(self.sb, self.nome, "select")

    def insert(self, payload):
        return FakeQuery(self.sb, self.nome, "insert", payload)

    def update(self, payload):
        return FakeQuery(self.sb, self.nome, "update", payload)


class FakeSB:
    def __init__(self, *, sem_schema=False):
        self.dados = {"videos": [], "comportamentos": [], "eventos": []}
        self.proximo_id = 1
        self.sem_coluna_comportamento = sem_schema
        self.colunas_evento_ausentes = (
            list(("fora_do_posto", "fora_amostras_zona", "pessoas_cena_cam2"))
            if sem_schema else []
        )
        self.erros_eventos = []

    def table(self, nome):
        return FakeTable(self, nome)


def evento_principal():
    return {
        "pessoa_track_id": 7,
        "comportamento_label": "operando_ponte_rolante",
        "descricao_bruta": "operando a ponte rolante",
        "tempo_inicio_s": 0.0, "tempo_fim_s": 60.0,
        "frame_inicio": 0, "frame_fim": 10,
        "bbox_inicio": [10, 10, 100, 300], "bbox_cam": "cam1",
        "bbox_stats": None, "zona_contexto": "posto",
        "papel_pessoa": pl.PAPEL_OPERADOR_FORA,
        "maos_maquina": None, "orientacao": None, "trabalho": None,
        "maquina": None, "imovel": None,
        "fora_do_posto": "operador", "fora_amostras_zona": 5,
        "n_amostras": 3, "n_observacoes": 3,
        "observacoes_origem": {"fora_do_posto": 3},
        "confianca": 1.0, "principal": True,
        "_fato": {"pessoas_na_cena": 2},
    }


def evento_auditoria_indeciso():
    return {
        "pessoa_track_id": pl.POSTO_VAZIO_TID,
        "comportamento_label": pl.POSTO_VAZIO_LABEL,
        "descricao_bruta": pl.POSTO_VAZIO_DESC,
        "tempo_inicio_s": 60.0, "tempo_fim_s": 65.0,
        "frame_inicio": 11, "frame_fim": 11,
        "bbox_inicio": None, "bbox_cam": None, "bbox_stats": None,
        "zona_contexto": "posto", "papel_pessoa": "posto_vazio",
        "maos_maquina": None, "orientacao": None, "trabalho": None,
        "fora_do_posto": "indeciso", "fora_amostras_zona": 5,
        "n_amostras": 0, "n_observacoes": 1,
        "origens": {"posto_vazio": 1}, "confianca": None,
    }


def persistir(sb):
    return pl.etapa_persistir(
        sb, "U", "Torneamento", "video.mp4",
        {"duracao_s": 60.0, "fps": 1.0, "largura": 640, "altura": 480},
        [evento_principal()], [7],
        {"operando_ponte_rolante": "operando a ponte rolante"},
        lambda *_a, **_k: "vlm",
        eventos_auditoria=[evento_auditoria_indeciso()],
    )


print("[1] Schema ausente degrada sem perder a ingestão")
sb_sem = FakeSB(sem_schema=True)
persistir(sb_sem)
check("colunas opcionais são recusadas uma por tentativa",
      sb_sem.erros_eventos == [
          "fora_do_posto", "fora_amostras_zona", "pessoas_cena_cam2"
      ], sb_sem.erros_eventos)
check("ingestão termina mesmo sem as quatro colunas da Fase 110",
      len(sb_sem.dados["videos"]) == 1
      and len(sb_sem.dados["eventos"]) == 2
      and len(sb_sem.dados["comportamentos"]) == 1, sb_sem.dados)
check("payload-base é preservado no retry",
      all(e.get("comportamento_label") for e in sb_sem.dados["eventos"]),
      sb_sem.dados["eventos"])


print("\n[2] Schema presente persiste auditoria e tag sem classificação")
sb = FakeSB()
persistir(sb)
comp = sb.dados["comportamentos"][0]
check("tag exclusiva de operador_fora exige decisão humana",
      comp.get("exige_decisao_humana") is True, comp)
check("tag nova continua sem categoria Lean",
      comp.get("categoria_lean") is None, comp)
principal = next(e for e in sb.dados["eventos"] if e.get("principal") is True)
cru = next(e for e in sb.dados["eventos"] if e.get("principal") is False)
check("principal persiste auditoria e contagem da cam2",
      principal.get("fora_do_posto") == "operador"
      and principal.get("fora_amostras_zona") == 5
      and principal.get("pessoas_cena_cam2") == 2, principal)
check("cru posto_vazio persiste indeciso sem virar pessoa",
      cru.get("papel_pessoa") == "posto_vazio"
      and cru.get("fora_do_posto") == "indeciso"
      and cru.get("fora_amostras_zona") == 5, cru)


print("\n[3] Decisão do gestor vale nas ocorrências futuras sem generalizar P3")
comp.update({
    "categoria_lean": "valor_agregado",
    "categoria_lean_origem": "humano",
})
persistir(sb)
novos_principais = [e for e in sb.dados["eventos"] if e.get("principal") is True]
novo = novos_principais[-1]
check("nova ocorrência fora herda a decisão como humano_rotulo",
      novo.get("categoria_lean") == "valor_agregado"
      and novo.get("categoria_lean_origem") == pl.ORIGEM_HUMANO_ROTULO, novo)

evento_normal = evento_principal()
evento_normal["papel_pessoa"] = "operador"
evento_normal["comportamento_label"] = "operando_ponte_rolante"
sb_normal = FakeSB()
sb_normal.dados["comportamentos"].append({
    "id": "c-normal", "empresa": "U", "processo": "Torneamento",
    "label": "operando_ponte_rolante", "total_ocorrencias": 1,
    "categoria_lean": "valor_agregado", "categoria_lean_origem": "humano",
    "exige_decisao_humana": True,
})
pl.etapa_persistir(
    sb_normal, "U", "Torneamento", "normal.mp4",
    {"duracao_s": 60.0, "fps": 1.0, "largura": 640, "altura": 480},
    [evento_normal], [7],
    {"operando_ponte_rolante": "operando a ponte rolante"},
    lambda *_a, **_k: "vlm",
)
normal = next(e for e in sb_normal.dados["eventos"] if e.get("principal") is True)
check("evento normal com a mesma tag continua apenas herdado",
      normal.get("categoria_lean_origem") == "herdado", normal)

print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
