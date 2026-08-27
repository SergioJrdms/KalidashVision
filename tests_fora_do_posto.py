"""Fase 110 — FORA DO POSTO ≠ POSTO VAZIO.

Duas exigências do cliente que parecem se contradizer, e esta suíte existe para
provar que as DUAS estão de pé ao mesmo tempo:

  Semana passada: *"o ponto é que a zona não estava sendo respeitada!!!! Só
  devemos analisar quem está dentro da zona de posto e ponto final"* — porque
  transeuntes inflavam a presença.

  Esta semana: *"se o operador estiver no frame mas FORA do posto desenhado,
  devemos detalhar e entender o que ele está fazendo... ele pode estar operando
  a ponte rolante e isso seria produtivo, mesmo estando fora do posto."*

A conciliação: quem está fora NÃO ENTRA em `am.pessoas`. Fica numa lista
paralela, passa por um teste de CONTINUIDADE (este track já foi medido dentro,
recentemente, e é o único candidato), e só então vira descrição. Transeunte
morre no portão exatamente como antes.

⚠️ O QUE ESTA SUÍTE PROTEGE ACIMA DE TUDO — três coisas, nesta ordem:

  1. A CORREÇÃO DA SEMANA PASSADA NÃO FOI DESFEITA. Quem nunca esteve dentro do
     polígono neste vídeo não produz nada, em hipótese nenhuma.

  2. O NÚMERO NÃO SE MEXE NO DIA DO DEPLOY. `operador_fora` é EST_FORA, igual a
     `posto_vazio`, e sem classificação humana vale `CATEGORIA_SEM_EVIDENCIA` —
     que é literalmente "desperdicio". O tempo troca de balde, não de valor.

  3. A DECISÃO DO GESTOR — e SÓ a dele — pode mover o número. `humano_rotulo` é
     uma string que nenhum VLM, cluster ou classificador de IA produz. Foi por
     essa porta que a produtividade saltou 41%→81% numa versão anterior.

Rodar:  python tests_fora_do_posto.py
"""
import os, sys, types
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
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
from backend import productivity as produtividade  # noqa: E402

ok = fail = 0
FONTE = open(os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read()
MAIN = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()
SQL = open(os.path.join(RAIZ, "sql", "schema.sql"), encoding="utf-8").read()
SQL110 = open(os.path.join(RAIZ, "sql", "110_fora_do_posto.sql"), encoding="utf-8").read()


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


def pessoa(tid, bbox=(100, 100, 200, 400)):
    return {"track_id": tid, "bbox": bbox, "kpts": None}


def julgar(tid, *, zona=5, visto=100.0, agora=110.0, candidatos=1):
    """Roda o teste do passante de verdade, sem YOLO e sem frame."""
    return pl._e_o_operador_que_saiu(
        pessoa(tid), tempo_s=agora,
        presenca_zona={tid: zona} if zona else {},
        ultimo_no_posto={tid: visto} if visto is not None else {},
        desc_acc={}, frame=None, candidatos=candidatos,
    )


def amostra_fora(tempo_s=5.0, *, tid=7, auditoria=None):
    """Amostra mínima que atravessa o plano real da etapa VLM."""
    return pl.Amostra(
        frame_idx=int(tempo_s), tempo_s=float(tempo_s), img_b64="",
        pessoas=[], dim=(640, 480), operador_presente=False,
        fora_posto=([{
            "track_id": tid,
            "bbox": (10, 10, 100, 300),
            "_fora_motivo": "operador",
            "_fora_amostras_zona": 5,
        }] if auditoria is None else []),
        img_b64_fora=("IMG" if auditoria is None else None),
        fora_auditoria=auditoria,
        fora_auditoria_amostras_zona=(5 if auditoria else None),
    )


def analisar_fora(amostras, analisador, *, modo="on", teto=40):
    """Executa `etapa_analise_vlm` e restaura todas as flags alteradas."""
    antigos = (pl._FORA_MODO, pl._FORA_MAX_CHAMADAS,
               pl._analisar_sequencia_fora, pl._POSTO_VAZIO_ENABLE)
    try:
        pl._FORA_MODO = modo
        pl._FORA_MAX_CHAMADAS = teto
        pl._analisar_sequencia_fora = analisador
        pl._POSTO_VAZIO_ENABLE = True
        return pl.etapa_analise_vlm(
            None, amostras, "torneamento", {}, lambda *_a, **_k: None,
            zona_posto="posto", intervalo_s=5.0,
        )
    finally:
        (pl._FORA_MODO, pl._FORA_MAX_CHAMADAS,
         pl._analisar_sequencia_fora, pl._POSTO_VAZIO_ENABLE) = antigos


class _Vec(list):
    def __sub__(self, outro):
        return _Vec(a - b for a, b in zip(self, outro))

    def __mul__(self, outro):
        return _Vec(a * b for a, b in zip(self, outro))

    def __ge__(self, outro):
        return _Vec(a >= outro for a in self)

    def astype(self, tipo):
        return _Vec(tipo(v) for v in self)


class _Matriz:
    def __init__(self, linhas):
        self.linhas = [list(l) for l in linhas]

    def __getitem__(self, chave):
        if isinstance(chave, tuple):
            _linhas, coluna = chave
            return _Vec(l[coluna] for l in self.linhas)
        return _Vec(self.linhas[chave])

    def tolist(self):
        return [list(l) for l in self.linhas]


class _Tensor:
    def __init__(self, valor):
        self.valor = valor

    def cpu(self):
        return self

    def numpy(self):
        return self.valor


class _Boxes:
    def __init__(self, caixas, ids):
        self.xyxy = _Tensor(_Matriz(caixas))
        self.id = _Tensor(_Vec(ids))

    def __len__(self):
        return len(self.id.valor)


class _Resultado:
    def __init__(self, caixas, ids):
        self.boxes = _Boxes(caixas, ids)
        self.keypoints = None


class _YoloFake:
    def __init__(self, cenas):
        self.cenas = list(cenas)
        self.i = 0

    def track(self, *_a, **_k):
        caixas, ids = self.cenas[self.i]
        self.i += 1
        return [_Resultado(caixas, ids)]


class _CapFake:
    def __init__(self, n):
        self.n, self.i = n, 0

    def isOpened(self):
        return self.i < self.n

    def grab(self):
        if self.i >= self.n:
            return False
        self.i += 1
        return True

    def retrieve(self):
        return True, object()

    def release(self):
        return None


class _MedidorFake:
    ativo = False
    grade = None
    n_pares_grade = 0
    zona_nome = None

    def __init__(self, *_a, **_k):
        pass

    def passo(self, *_a, **_k):
        pass

    def por_minuto(self):
        return {}


def detectar_cenas(cenas, *, modo="on"):
    """Roda a etapa de detecção real com I/O/YOLO substituídos por dublês."""
    nomes = (
        "_FORA_MODO", "_ZONA_ESTRITA", "inspecionar_video", "_build_rois",
        "_zona_da_pessoa", "MedidorMovimento", "frame_para_base64",
        "anotar_frame_com_ids", "acumular_descritor", "fechar_descritores",
        "_crop_cinza_pequeno",
    )
    antigos = {n: getattr(pl, n) for n in nomes}
    video_capture_antigo = getattr(pl.cv2, "VideoCapture", None)
    try:
        pl._FORA_MODO = modo
        pl._ZONA_ESTRITA = True
        pl.inspecionar_video = lambda _p: {
            "fps": 1.0, "total_frames": len(cenas), "largura": 640,
            "altura": 480, "duracao_s": float(len(cenas)),
        }
        pl._build_rois = lambda *_a, **_k: {
            "posto": {"papel": "posto_operador", "descricao_contexto": "posto"}
        }
        pl._zona_da_pessoa = lambda _pts, _rois, ancora=None: (
            ("posto", "posto_operador", "posto")
            if ancora and ancora[0] < 300 else (None, None, None)
        )
        pl.MedidorMovimento = _MedidorFake
        pl.frame_para_base64 = lambda *_a, **_k: "IMG"
        pl.anotar_frame_com_ids = lambda frame, _pessoas: frame
        pl.acumular_descritor = lambda *_a, **_k: None
        pl.fechar_descritores = lambda *_a, **_k: []
        pl._crop_cinza_pequeno = lambda *_a, **_k: None
        pl.cv2.VideoCapture = lambda _p: _CapFake(len(cenas))
        return pl.etapa_detectar_e_amostrar(
            _YoloFake(cenas), "fake.mp4", 1.0,
            {"posto": {"papel": "posto_operador"}},
            lambda *_a, **_k: None,
        )[0]
    finally:
        for n, valor in antigos.items():
            setattr(pl, n, valor)
        if video_capture_antigo is None:
            delattr(pl.cv2, "VideoCapture")
        else:
            pl.cv2.VideoCapture = video_capture_antigo


# ═══════ [1] A CORREÇÃO DA SEMANA PASSADA CONTINUA DE PÉ ═══════════════
print("\n[1] ⭐ Transeunte morre no portão, como antes")

check("⭐ quem NUNCA esteve dentro do polígono é passante",
      julgar(7, zona=0) == (False, "passante"))
check("quem esteve POUCO também (abaixo do piso de amostras)",
      julgar(7, zona=pl._FORA_MIN_ZONA - 1) == (False, "passante"))
check("no piso exato, passa", julgar(7, zona=pl._FORA_MIN_ZONA)[0] is True)
check("sem instante registrado, passante",
      julgar(7, visto=None) == (False, "passante"))
check("⭐ visto há MUITO tempo é passante, não 'o operador que saiu'",
      julgar(7, visto=0.0, agora=pl._FORA_GAP_S + 1.0) == (False, "passante"))
check("dentro da janela, é ele",
      julgar(7, visto=0.0, agora=pl._FORA_GAP_S - 1.0)[0] is True)
check("track sem id nunca vira nada",
      pl._e_o_operador_que_saiu({"track_id": None, "bbox": (0, 0, 1, 1)},
                                tempo_s=1.0, presenca_zona={}, ultimo_no_posto={},
                                desc_acc={}, frame=None, candidatos=1)
      == (False, "passante"))

# O portão em si: o `continue` continua fechando o ramo e nada nele toca `pessoas`.
_ramo = FONTE.split('if papel_z != "posto_operador":')[1].split('pessoa["zona"]')[0]
check("⭐ o ramo do descarte termina em `continue`",
      _ramo.rstrip().endswith("continue"))
check("⭐ e NADA nele encosta em `pessoas`",
      "pessoas.append" not in _ramo and "pessoas[" not in _ramo, _ramo)
check("o que ele guarda vai para lista SEPARADA",
      "fora_frame.append(pessoa)" in _ramo)
check("e só quando o recurso está ligado E a caixa é válida",
      "_fora_ativo()" in _ramo and "_bbox_valido" in _ramo)

_dentro = (100, 100, 200, 400)
_fora_bbox = (400, 100, 500, 400)
_am_integracao = detectar_cenas([
    ([_dentro], [7]), ([_dentro], [7]), ([_dentro], [7]),
    ([_fora_bbox], [7]),
])
check("⭐ integração: o track fora nunca volta para `am.pessoas`",
      _am_integracao[-1].pessoas == [], _am_integracao[-1].pessoas)
check("⭐ integração: track válido e único vai só para `fora_posto`",
      [p["track_id"] for p in _am_integracao[-1].fora_posto] == [7],
      _am_integracao[-1].fora_posto)

_sem_hist = detectar_cenas([
    ([_dentro], [7]), ([_fora_bbox], [7]),
])
check("⭐ integração: histórico insuficiente continua passante",
      _sem_hist[-1].pessoas == [] and _sem_hist[-1].fora_posto == []
      and _sem_hist[-1].fora_auditoria is None, _sem_hist[-1])

_off = detectar_cenas([
    ([_dentro], [7]), ([_dentro], [7]), ([_dentro], [7]),
    ([_fora_bbox], [7]),
], modo="off")
check("⭐ flag off preserva o descarte anterior",
      _off[-1].pessoas == [] and _off[-1].fora_posto == []
      and _off[-1].fora_auditoria is None, _off[-1])


# ═══════ [2] Ambiguidade não vira palpite ══════════════════════════════
print("\n[2] Dois candidatos, ou cor que desmente → 'não sei'")

check("⭐ dois ex-ocupantes fora ao mesmo tempo → indeciso",
      julgar(7, candidatos=2) == (False, "indeciso"))
check("zero candidatos idem", julgar(7, candidatos=0) == (False, "indeciso"))
check("⭐ 'indeciso' NÃO é 'passante' — são estados distintos no dado",
      julgar(7, candidatos=2)[1] != julgar(7, zona=0)[1])

# Veto de aparência: cor muito diferente do que o track tinha dentro da zona.
_veto = pl._e_o_operador_que_saiu(
    pessoa(9), tempo_s=110.0, presenca_zona={9: 5}, ultimo_no_posto={9: 100.0},
    desc_acc={9: {"hist_sup": [[1.0] + [0.0] * 31]}}, frame=object(), candidatos=1)
check("cor incomputável NÃO veta (ausência de medida não é medida)",
      _veto[0] is True, _veto)
check("o limiar do veto é MENOR que o de identificação",
      pl._FORA_SIM_VETO < 0.62)
check("com o motivo escrito: aqui a cor não identifica, só recusa o absurdo",
      "só recusa\n    # o absurdo" in FONTE or "não identifica, rejeita o" in FONTE
      or "aparência entra só como VETO" in FONTE)

_dois = detectar_cenas([
    ([_dentro, _dentro], [7, 8]),
    ([_dentro, _dentro], [7, 8]),
    ([_dentro, _dentro], [7, 8]),
    ([_fora_bbox, _fora_bbox], [7, 8]),
])
check("⭐ integração: dois candidatos não viram pessoa/evento fora",
      _dois[-1].pessoas == [] and _dois[-1].fora_posto == [], _dois[-1])
check("⭐ integração: dois candidatos carregam auditoria indeciso",
      _dois[-1].fora_auditoria == "indeciso", _dois[-1].fora_auditoria)

_hist_antigo, _media_antiga, _sim_antiga = (
    pl.histograma_cor, pl._media_hist, pl._sim_hist,
)
try:
    pl.histograma_cor = lambda *_a, **_k: {"sup": [0.0, 1.0]}
    pl._media_hist = lambda *_a, **_k: [1.0, 0.0]
    pl._sim_hist = lambda *_a, **_k: pl._FORA_SIM_VETO - 0.01
    _veto_real = pl._e_o_operador_que_saiu(
        pessoa(9), tempo_s=110.0, presenca_zona={9: 5},
        ultimo_no_posto={9: 100.0}, desc_acc={9: {"hist_sup": [[1.0, 0.0]]}},
        frame=object(), candidatos=1,
    )
finally:
    pl.histograma_cor, pl._media_hist, pl._sim_hist = (
        _hist_antigo, _media_antiga, _sim_antiga,
    )
check("⭐ veto de aparência medido de verdade → indeciso",
      _veto_real == (False, "indeciso"), _veto_real)


# ═══════ [3] ⭐ O NÚMERO NÃO SE MEXE NO DIA DO DEPLOY ══════════════════
print("\n[3] ⭐ Permanência bit a bit igual — a trava mais importante")

check("⭐ `operador_fora` é EST_FORA, igual a posto_vazio",
      pl.estado_permanencia({"papel_pessoa": pl.PAPEL_OPERADOR_FORA}, "esquerda")
      == (pl.EST_FORA, None))
check("NÃO é EST_INCONCLUSIVO (que sairia do denominador e INFLARIA presença)",
      pl.estado_permanencia({"papel_pessoa": pl.PAPEL_OPERADOR_FORA}, "esquerda")[0]
      != pl.EST_INCONCLUSIVO)
check("posto_vazio segue igual",
      pl.estado_permanencia({"papel_pessoa": "posto_vazio"}, "esquerda")[0] == pl.EST_FORA)
check("papel desconhecido segue INCONCLUSIVO",
      pl.estado_permanencia({"papel_pessoa": "sei_la"}, "esquerda")[0]
      == pl.EST_INCONCLUSIVO)
check("o motivo está escrito no código, não só aqui",
      "A LINHA QUE IMPEDE O NÚMERO DE SE MEXER" in FONTE)

# Permanência do dia: trocar todo posto_vazio por operador_fora não muda nada.
def _ev(papel, ini, fim):
    return {"papel_pessoa": papel, "tempo_inicio_s": ini, "tempo_fim_s": fim,
            "principal": True, "validacao_correto": None,
            "comportamento_label": "x", "video_id": "v1"}

_base = [_ev("operador", 0, 60), _ev("posto_vazio", 60, 120), _ev("operador", 120, 180)]
_trocado = [_ev("operador", 0, 60), _ev(pl.PAPEL_OPERADOR_FORA, 60, 120),
            _ev("operador", 120, 180)]
_a = pl.permanencia_do_dia(_base, "esquerda")
_b = pl.permanencia_do_dia(_trocado, "esquerda")
check("⭐ no_posto_pct IDÊNTICO", _a.get("no_posto_pct") == _b.get("no_posto_pct"),
      (_a.get("no_posto_pct"), _b.get("no_posto_pct")))
check("⭐ fora_pct IDÊNTICO", _a.get("fora_pct") == _b.get("fora_pct"))
check("⭐ cobertura IDÊNTICA", _a.get("cobertura_pct") == _b.get("cobertura_pct"))


def _ev_comercial(papel, ini, fim, **extra):
    e = {
        "video_id": "fase110", "papel_pessoa": papel,
        "tempo_inicio_s": ini, "tempo_fim_s": fim, "principal": True,
        "_capturado_em": datetime(2026, 8, 17, 12, tzinfo=timezone.utc),
        "n_amostras": 20,
    }
    e.update(extra)
    return e


_com_base = [
    _ev_comercial("operador", 0, 60, trabalho=True),
    _ev_comercial("operador", 60, 120, trabalho=False),
    _ev_comercial("posto_vazio", 120, 180),
]
_com_fora = [*_com_base[:2], _ev_comercial(pl.PAPEL_OPERADOR_FORA, 120, 180)]
_mc_base = produtividade.agregar_produtividade(
    _com_base, agora=datetime(2026, 8, 17, 12, 4, tzinfo=timezone.utc))
_mc_fora = produtividade.agregar_produtividade(
    _com_fora, agora=datetime(2026, 8, 17, 12, 4, tzinfo=timezone.utc))
_invariantes_comerciais = (
    "presenca_pct", "posto_vazio_pct", "cobertura_presenca_pct",
    "cobertura_identificacao_pct", "produtividade_pct",
    "improdutividade_pct", "cobertura_produtividade_pct", "inconclusivo_pct",
)
check("⭐ contrato comercial idêntico antes da decisão humana",
      all(_mc_base[k] == _mc_fora[k] for k in _invariantes_comerciais),
      {k: (_mc_base[k], _mc_fora[k]) for k in _invariantes_comerciais})

_com_fora_humano = [
    *_com_base[:2],
    _ev_comercial(
        pl.PAPEL_OPERADOR_FORA, 120, 180,
        categoria_lean="valor_agregado",
        categoria_lean_origem=pl.ORIGEM_HUMANO_ROTULO,
    ),
]
_mc_humano = produtividade.agregar_produtividade(
    _com_fora_humano,
    agora=datetime(2026, 8, 17, 12, 4, tzinfo=timezone.utc),
)
check("⭐ só o clique produtivo aumenta produtividade, sem inventar presença",
      _mc_humano["produtividade_pct"] > _mc_fora["produtividade_pct"]
      and _mc_humano["presenca_pct"] == _mc_fora["presenca_pct"]
      and _mc_humano["posto_vazio_pct"] == _mc_fora["posto_vazio_pct"],
      (_mc_fora, _mc_humano))

check("o rollup do card de processo também conta como FORA",
      'PAPEL_OPERADOR_FORA)' in FONTE.split("_fora = (e.get")[1][:200])


# ═══════ [4] ⭐ Só o gestor move o número ══════════════════════════════
print("\n[4] ⭐ A decisão humana, e só ela, muda a categoria")


def _fora(cat=None, origem=None):
    return {"papel_pessoa": pl.PAPEL_OPERADOR_FORA, "categoria_lean": cat,
            "categoria_lean_origem": origem}


check("sem classificação → CATEGORIA_SEM_EVIDENCIA",
      pl.decidir_permanencia(_fora(), "esquerda")[0] == pl.CATEGORIA_SEM_EVIDENCIA)
check("⭐ e isso é, numericamente, o mesmo 'desperdicio' de hoje",
      pl.CATEGORIA_SEM_EVIDENCIA == "desperdicio")
check("o motivo do trecho diz que ainda não foi classificado",
      "ainda não foi classificado" in pl.decidir_permanencia(_fora(), "esquerda")[2])
check("⭐ com a marca humana → vale a decisão dele",
      pl.decidir_permanencia(_fora("valor_agregado", pl.ORIGEM_HUMANO_ROTULO),
                             "esquerda")[0] == "valor_agregado")
check("e ele também pode dizer que é improdutivo",
      pl.decidir_permanencia(_fora("desperdicio", pl.ORIGEM_HUMANO_ROTULO),
                             "esquerda")[0] == "desperdicio")
for _org in ("herdado", "ia", "aprendido", "fallback", None):
    check(f"⭐ origem {_org!r} NÃO move o número (guarda do 41%→81%)",
          pl.decidir_permanencia(_fora("valor_agregado", _org), "esquerda")[0]
          == pl.CATEGORIA_SEM_EVIDENCIA)
check("categoria inválida com marca humana também não passa",
      pl.decidir_permanencia(_fora("apoio", pl.ORIGEM_HUMANO_ROTULO), "esquerda")[0]
      == pl.CATEGORIA_SEM_EVIDENCIA)
check("⭐ posto_vazio com a marca humana continua desperdício (ramo inalcançável)",
      pl.decidir_permanencia(
          {"papel_pessoa": "posto_vazio", "categoria_lean": "valor_agregado",
           "categoria_lean_origem": pl.ORIGEM_HUMANO_ROTULO}, "esquerda")[0]
      == "desperdicio")
check("⭐ visitante idem",
      pl.decidir_permanencia(
          {"papel_pessoa": "visitante", "categoria_lean": "valor_agregado",
           "categoria_lean_origem": pl.ORIGEM_HUMANO_ROTULO}, "esquerda")[0]
      == "desperdicio")

check("a origem humana é escrita SÓ pelo endpoint da árvore",
      MAIN.count("origem=pl.ORIGEM_HUMANO_ROTULO") == 2
      and "ORIGEM_HUMANO_ROTULO" not in FONTE.split("def classificar_comportamentos_lean")[1][:6000])
check("e a propagação a aceita como origem",
      'origem: str = "herdado"' in FONTE
      and '"categoria_lean_origem": origem' in FONTE)


# ═══════ [5] A tag nasce sem categoria ═════════════════════════════════
print("\n[5] O rótulo de fora do posto espera um humano")

_lean = FONTE[FONTE.index("def classificar_comportamentos_lean"):]
_lean = _lean[:_lean.index("\ndef ", 1)]
check("⭐ a guarda existe",
      'if c.get("exige_decisao_humana") and not c.get("categoria_lean"):' in _lean)
# A posição do GUARDA, não da primeira menção — a coluna também aparece no
# SELECT, lá no começo da função.
_g = _lean.index('if c.get("exige_decisao_humana") and not c.get("categoria_lean"):')
check("⭐ vem DEPOIS da regra do posto_vazio (que segue forçando desperdício)",
      _lean.index("POSTO_VAZIO_LABEL") < _g)
check("⭐ e ANTES de rotulo_e_ausencia (que é CORRETIVA e mantém prioridade)",
      _g < _lean.index('if rotulo_e_ausencia(c.get("label")):'))
check("decisão humana no topo continua inviolável",
      _lean.index('origem == "humano"') < _g)
check("a marca fica inerte depois que alguém decide",
      "and not c.get(\"categoria_lean\")" in _lean)
check("⭐ coluna ausente AVISA em vez de silenciar",
      "A guarda do 'fora do posto' está" in FONTE and "INATIVA" in FONTE)
check("o rótulo é marcado na ingestão só quando TODOS os eventos são de fora",
      "_por_fora[_lbl] = _por_fora.get(_lbl, True) and _eh_fora" in FONTE)
check("e a marca nunca é DESLIGADA por um vídeo de dentro",
      "Só LIGA a marca, nunca desliga" in FONTE)


class _LeanQuery:
    def __init__(self, sb):
        self.sb = sb

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self

    def update(self, payload):
        self.sb.escritas.append(dict(payload))
        return self

    def execute(self):
        return types.SimpleNamespace(data=[dict(self.sb.comportamento)])


class _LeanSB:
    def __init__(self):
        self.comportamento = {
            "id": "c1", "label": "operando_ponte",
            "descricao": "operando a ponte rolante",
            "categoria_lean": None, "categoria_lean_origem": None,
            "exige_decisao_humana": True,
        }
        self.escritas = []

    def table(self, _nome):
        return _LeanQuery(self)


_lean_auto_antigo = pl._LEAN_AUTO
try:
    pl._LEAN_AUTO = True
    _sb_lean = _LeanSB()
    _n_lean = pl.classificar_comportamentos_lean(
        _sb_lean, None, "U", "Torneamento")
finally:
    pl._LEAN_AUTO = _lean_auto_antigo
check("⭐ comportamento fora/exige humano não entra no classificador automático",
      _n_lean == 0 and _sb_lean.escritas == []
      and _sb_lean.comportamento["categoria_lean"] is None,
      (_n_lean, _sb_lean.escritas))


# ═══════ [6] A observação e o transporte ═══════════════════════════════
print("\n[6] A observação de fora do posto")

_emis = FONTE.split('if tipo == "fora_posto":')[1].split('if tipo == "vazio":')[0]
check("⭐ usa o track id REAL, não sentinela",
      '"track_id": pf.get("track_id")' in _emis)
check("com o motivo: sentinela fundiria duas pessoas num evento",
      "fundiria duas" in _emis)
check("papel é `operador_fora`", '"papel": PAPEL_OPERADOR_FORA' in _emis)

_slots = [amostra_fora(t) for t in (0.0, 5.0, 10.0)]
_sem_acao = analisar_fora(_slots, lambda *_a, **_k: {})
check("⭐ VLM sem ação emite posto_vazio para TODOS os slots",
      len(_sem_acao) == len(_slots)
      and all(o.get("papel") == "posto_vazio" for o in _sem_acao), _sem_acao)
check("⭐ VLM sem ação não altera nem apaga os instantes",
      [o["tempo_s"] for o in _sem_acao] == [a.tempo_s for a in _slots], _sem_acao)
check("falha do VLM fica auditável sem inventar atividade",
      all(o.get("fora_do_posto") == "falha_vlm" for o in _sem_acao), _sem_acao)


def _levantar(*_a, **_k):
    raise RuntimeError("provider indisponível")


_excecao = analisar_fora([amostra_fora()], _levantar)
check("⭐ exceção do VLM também volta a posto_vazio",
      len(_excecao) == 1 and _excecao[0].get("papel") == "posto_vazio"
      and _excecao[0].get("fora_do_posto") == "falha_vlm", _excecao)

_chamou_no_teto = []
_teto = analisar_fora(
    [amostra_fora(0), amostra_fora(5)],
    lambda *_a, **_k: _chamou_no_teto.append(True) or {}, teto=0,
)
check("⭐ teto não chama VLM e preserva todos os slots como posto_vazio",
      not _chamou_no_teto and len(_teto) == 2
      and all(o.get("papel") == "posto_vazio" for o in _teto), _teto)
check("⭐ teto persiste auditoria `teto_chamadas`",
      all(o.get("fora_do_posto") == "teto_chamadas" for o in _teto), _teto)

_indeciso_obs = analisar_fora(
    [amostra_fora(auditoria="indeciso")], lambda *_a, **_k: {})
check("⭐ indeciso continua posto_vazio, não inconclusivo",
      len(_indeciso_obs) == 1
      and _indeciso_obs[0].get("papel") == "posto_vazio"
      and _indeciso_obs[0].get("fora_do_posto") == "indeciso", _indeciso_obs)

_valida_obs = analisar_fora(
    [amostra_fora()],
    lambda _g, grupo, *_a, **_k: {
        i: {"acao": "operando a ponte rolante", "resumo": "uso da ponte"}
        for i, _am in enumerate(grupo)
    },
)
check("⭐ ação válida vira operador_fora com track real",
      len(_valida_obs) == 1
      and _valida_obs[0].get("papel") == pl.PAPEL_OPERADOR_FORA
      and _valida_obs[0].get("track_id") == 7, _valida_obs)

_off_obs = analisar_fora(
    [amostra_fora()], lambda *_a, **_k: {0: {"acao": "não deve rodar"}},
    modo="off",
)
check("⭐ flag off mantém a observação anterior de posto_vazio",
      len(_off_obs) == 1 and _off_obs[0].get("papel") == "posto_vazio"
      and _off_obs[0].get("fora_do_posto") is None, _off_obs)

_cru_indeciso = pl.etapa_segmentar_eventos(
    _indeciso_obs, lambda *_a, **_k: pl.POSTO_VAZIO_LABEL, 5.0)
_principal_indeciso = pl.etapa_consolidar_principais(
    _cru_indeciso, {pl.POSTO_VAZIO_LABEL: pl.POSTO_VAZIO_DESC}, 10.0)
check("⭐ auditoria atravessa observação → cru → principal",
      _cru_indeciso[0].get("fora_do_posto") == "indeciso"
      and _principal_indeciso[0].get("fora_do_posto") == "indeciso"
      and _principal_indeciso[0].get("fora_amostras_zona") == 5,
      (_cru_indeciso, _principal_indeciso))
check("⛔ não afirma trabalho, mãos, orientação nem estado da máquina",
      '"trabalho": None' in _emis and '"maos_maquina": None' in _emis
      and '"orientacao": None' in _emis and '"maquina": None' in _emis)
check("com o motivo: `trabalho` pergunta sobre o POSTO, e ele não está nele",
      "a pergunta não cabe" in _emis)
check("carrega a auditoria do teste do passante",
      '"fora_do_posto"' in _emis and '"fora_amostras_zona"' in _emis)
check("a origem conta como OBSERVADA (foi uma olhada de verdade)",
      '"fora_do_posto"' in FONTE.split("ORIGENS_OBSERVADAS = ")[1][:120])

check("⭐ o prompt NÃO pede julgamento de produtividade",
      '"trabalho"' not in pl.PROMPT_VLM_FORA_POSTO
      and '"motivo"' not in pl.PROMPT_VLM_FORA_POSTO)
check("e proíbe a resposta preguiçosa 'fora do posto'",
      'NÃO diga "fora do posto"' in pl.PROMPT_VLM_FORA_POSTO)
check("o gabarito pede ação e resumo", '{{"resumo"' in pl.PROMPT_VLM_FORA_POSTO
      and '"acao"' in pl.PROMPT_VLM_FORA_POSTO)

check("⭐ evento de fora do posto NÃO é auto-validado",
      "`operador_fora` NÃO entra aqui" in FONTE)
check("posto_vazio continua sendo (é determinístico e afirma AUSÊNCIA)",
      'if e.get("papel_pessoa") == "posto_vazio":\n            origem = "posto_vazio"'
      in FONTE)


# ═══════ [7] Bandeira, ordem e persistência ════════════════════════════
print("\n[7] Fail-closed, ordem do plano e colunas")

check("⭐ o padrão é DESLIGADO",
      'os.environ.get("KV_FORA_DO_POSTO", "off")' in FONTE)
check("três estados: off, sombra, on",
      '_FORA_MODO not in ("off", "sombra", "on")' in FONTE)
check("⭐ exige a zona ESTRITA ligada", "if not _ZONA_ESTRITA:" in
      FONTE.split("def _fora_ativo")[1][:600])
check("com o motivo: presenca_zona contou outra noção de 'dentro'",
      "contou outra noção de \"dentro\"" in FONTE)
check("⭐ em `sombra` nenhuma observação é emitida",
      '_FORA_MODO == "on"' in FONTE.split("def etapa_analise_vlm", 1)[1])
check("a amostra só recebe gente de fora no modo `on`",
      'am_fora = fora_ok if _FORA_MODO == "on" else []' in FONTE)
_i_ponte = FONTE.index('plano.append(("ponte" if am.operador_ponte')
_i_fora_autoritativo = FONTE.index('plano.append(("fora_posto", am))')
_i_fora_legado = FONTE.index('plano.append(("fora_posto", am))', _i_ponte)
check("⭐ no legado resgate/ponte ganha; 111D autoritativa ganha quando R1 está fora",
      _i_fora_autoritativo < _i_ponte < _i_fora_legado)
check("e o fora-do-posto vem ANTES de vazio",
      FONTE.index('plano.append(("fora_posto", am))')
      < FONTE.index('plano.append(("vazio", am))'))

for col in ("fora_do_posto text", "fora_amostras_zona int",
            "pessoas_cena_cam2 int", "exige_decisao_humana boolean"):
    check(f"SQL cria `{col.split()[0]}`", col in SQL)
check("⭐ a degradação de coluna ausente é genérica, não um if por coluna",
      "_COLUNAS_OPCIONAIS_EVENTO" in FONTE
      and "while True:" in FONTE
      and "c not in removidas and c in str(erro)" in FONTE)
check("e cobre as quatro", all(
    c in FONTE.split("_COLUNAS_OPCIONAIS_EVENTO = ")[1][:220]
    for c in ("narrativa", "fora_do_posto", "fora_amostras_zona", "pessoas_cena_cam2")))
check("⭐ `n_cena_cam2` finalmente chega ao banco",
    '"pessoas_cena_cam2": (e.get("_fato") or {}).get("pessoas_na_cena")' in FONTE)
check("com a ressalva de que é MÁXIMO sem casamento entre câmeras",
      "MÁXIMO SEM CASAMENTO ENTRE CÂMERAS" in FONTE)
check("os leitores que decidem passaram a ler QUEM decidiu",
      FONTE.count("categoria_lean, categoria_lean_origem, ") >= 3
      and "categoria_lean, categoria_lean_origem, " in MAIN)
check("⭐ migração dedicada é idempotente e contém só a Fase 110",
      all(t in SQL110 for t in (
          "add column if not exists fora_do_posto text",
          "add column if not exists fora_amostras_zona int",
          "add column if not exists pessoas_cena_cam2 int",
          "add column if not exists exige_decisao_humana boolean not null default false",
          "create index if not exists idx_eventos_fora_do_posto",
      )))
check("migração inclui conferência por information_schema.columns",
      "information_schema.columns" in SQL110)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
