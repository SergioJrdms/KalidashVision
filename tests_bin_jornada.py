"""Fase 87 — abrir o bloco de 15 min da faixa "A jornada de …".

O que esta suíte protege, e por quê:

1. O DETALHE TEM DE CONCORDAR COM O DESENHO. A faixa é pintada por
   `montar_analise_diaria`; o clique é respondido por `eventos_do_bin`. São
   dois caminhos de leitura diferentes sobre o mesmo dado. Se divergirem, o
   detalhe desmente o gráfico e as duas telas ficam inúteis — pior do que não
   ter o clique. Aqui as PROPORÇÕES do bloco são comparadas com as larguras
   desenhadas naquele bloco.

2. FATIA NÃO É HORÁRIO. Dentro do bucket as larguras são proporção de tempo em
   ordem fixa (va, desp, vazio). O bloco é a menor janela sobre a qual o
   desenho afirma alguma coisa — por isso o clique abre o BLOCO.

3. O EVENTO QUE ATRAVESSA A BORDA entra só com a fatia que caiu dentro. Sem
   isso um bloco de 15 min "teria" 4 min de um evento que mal encostou nele.

4. ABRIR NÃO É VALIDAR. Zero escrita, como na auditoria.

5. O TETO DO POSTGREST. O dublê pagina de verdade (`.range()` com corte real):
   um dublê mais generoso que o serviço real não testa nada — foi exatamente
   assim que a Fase 81 passou meses escondendo um dia inteiro da tela.

Rodar:  python tests_bin_jornada.py
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

# Fase 97: a produtividade passou a vir da PERMANÊNCIA. O sujeito desta suíte
# é outro (o bloco da jornada / a árvore da Fase 95), e os dois mecanismos
# coexistem atrás de flag — então ela roda no caminho que está testando.
pl._PERMANENCIA = False

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


class FakeQ:
    """Dublê que PAGINA de verdade — `.range(a, b)` corta como o PostgREST."""

    def __init__(self, sb, tabela):
        self.sb, self.tabela = sb, tabela
        self.eqs, self.ins, self.faixa = {}, {}, None
        self.ordem = None

    def select(self, *a, **k): return self
    def order(self, c=None, **k): self.ordem = c; return self
    def limit(self, n, **k): self.faixa = (0, n - 1); return self
    def range(self, a, b): self.faixa = (a, b); return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def in_(self, c, vs): self.ins[c] = list(vs); return self

    def execute(self):
        linhas = [dict(l) for l in self.sb.dados.get(self.tabela, [])]
        r = [l for l in linhas
             if all(l.get(c) == v for c, v in self.eqs.items())
             and all(l.get(c) in vs for c, vs in self.ins.items())]
        if self.ordem:
            r.sort(key=lambda x: str(x.get(self.ordem)))
        self.sb.leituras.append(self.tabela)
        if self.faixa is not None:
            a, b = self.faixa
            r = r[a : b + 1]
        else:
            r = r[: pl.TETO_POSTGREST]      # o corte silencioso do serviço real
        return types.SimpleNamespace(data=r)


class FakeSB:
    def __init__(self, dados):
        self.dados = dados
        self.escritas = []
        self.leituras = []

    def table(self, nome):
        sb = self
        class T:
            def select(self, *a, **k): return FakeQ(sb, nome)
            def update(self, p): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
            def insert(self, p): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
            def upsert(self, p, **k): sb.escritas.append((nome, p)); return FakeQ(sb, nome)
            def delete(self): sb.escritas.append((nome, "delete")); return FakeQ(sb, nome)
        return T()


# ── Um dia montado à mão: o vídeo começa às 08:00, e cada evento tem o
#    deslocamento em segundos dentro dele. Assim o minuto-do-relógio de cada
#    evento é previsível e o bloco em que ele cai é conferível na mão.
VID = "seg_20260806_080000.mp4"


def ev(i, label, ini_s, dur_s, *, papel="operador", desc=None, vid="v1", cam="cam1"):
    return {"id": f"e{i:03d}", "video_id": vid, "empresa": "U", "processo": "T",
            "comportamento_label": label, "label_corrigido": None,
            "descricao_bruta": desc or f"descrição de {label}",
            "tempo_inicio_s": ini_s, "tempo_fim_s": ini_s + dur_s,
            "principal": True, "papel_pessoa": papel,
            "origem_validacao": "vlm", "validado_humano": False,
            "validacao_correto": None, "confianca": 0.8, "n_amostras": 6,
            "em_duvida": False, "pessoa_track_id": 3, "versao_instrumento": 3}


EVENTOS = [
    # bloco 08:00–08:15 (bin 32) — 10 min produtivo + 5 min desperdício
    ev(1, "operar_torno", 0, 600),
    ev(2, "aguardar_ciclo", 600, 300),
    # bloco 08:15–08:30 (bin 33) — posto vazio inteiro
    ev(3, "posto_vazio", 900, 900, papel="posto_vazio"),
    # bloco 08:30–08:45 (bin 34) — um evento que ATRAVESSA a borda:
    # começa 08:44 e dura 4 min → só 1 min cai no bin 34, 3 min no bin 35.
    ev(4, "operar_torno", 1800, 240 * 6),       # 08:30 → 08:54 (24 min)
]
VIDEOS = [{"id": "v1", "empresa": "U", "processo": "T", "nome": VID,
           "cam_id": "cam1", "duracao_s": 3600,
           "processado_em": "2026-08-06T09:30:00"}]
COMPS = [
    {"label": "operar_torno", "empresa": "U", "processo": "T",
     "categoria_lean": "valor_agregado"},
    {"label": "aguardar_ciclo", "empresa": "U", "processo": "T",
     "categoria_lean": "desperdicio"},
    {"label": "posto_vazio", "empresa": "U", "processo": "T",
     "categoria_lean": "desperdicio"},
]


def novo_sb(eventos=None, videos=None):
    return FakeSB({"eventos": list(eventos if eventos is not None else EVENTOS),
                   "videos": list(videos if videos is not None else VIDEOS),
                   "comportamentos": list(COMPS)})


DIA = "2026-08-06"

print("\n[1] O bloco abre pelo minuto clicado — e é o bloco, não a fatia")
SB = novo_sb()
r = pl.eventos_do_bin(SB, "U", "T", DIA, 8 * 60 + 7)     # clicou às 08:07
check("caiu no bloco 08:00–08:15", (r["de"], r["ate"]) == ("08:00", "08:15"), r)
check("o índice do bin é 32", r["bin"] == 32, r["bin"])
check("qualquer minuto do bloco abre o MESMO bloco",
      pl.eventos_do_bin(novo_sb(), "U", "T", DIA, 8 * 60 + 14.9)["bin"] == 32)
check("o passo é o mesmo da linha_tempo", pl.BIN_JORNADA_MIN == 15)
check("a nota avisa que largura é proporção, não horário",
      "PROPORÇÃO" in r["nota"] and "horário" in r["nota"], r["nota"])

print("\n[2] Abrir NÃO é validar")
check("zero escrita", not SB.escritas, SB.escritas)
check("não mexeu em validado_humano",
      all(not it["validado"] for it in r["itens"]))

print("\n[3] O que compõe o bloco: rótulo + descrição, que é o pedido")
check("2 eventos no bloco", r["n_eventos"] == 2, r["n_eventos"])
labels = [it["rotulo"] for it in r["itens"]]
check("os rótulos aparecem", labels == ["operar_torno", "aguardar_ciclo"], labels)
check("a descrição do VLM vem junto",
      all(it["descricao"] for it in r["itens"]),
      [it["descricao"] for it in r["itens"]])
check("em ordem cronológica", [it["hora"] for it in r["itens"]] == ["08:00:00", "08:10:00"],
      [it["hora"] for it in r["itens"]])
check("cada trecho carrega a categoria que o pintou",
      [it["cat"] for it in r["itens"]] == ["va", "desp"], [it["cat"] for it in r["itens"]])

print("\n[4] As proporções do bloco batem com o desenho da faixa")
check("produtivo = 2/3 do bloco", abs(r["por_categoria"]["va"]["pct"] - 66.7) < 0.2,
      r["por_categoria"])
check("desperdício = 1/3", abs(r["por_categoria"]["desp"]["pct"] - 33.3) < 0.2,
      r["por_categoria"])
check("15 min medidos", abs(r["segundos"] - 900) < 0.5, r["segundos"])

# O invariante que importa: a MESMA proporção que `montar_analise_diaria`
# desenhou naquele bucket. Duas leituras independentes do mesmo dado.
dd = pl.montar_analise_diaria(novo_sb(), "U", "T", dias=30)
dia = next(d for d in dd["dias"] if d["dia"] == DIA)
faixas = [f for f in dia["linha_tempo"] if 32 * 15 <= f["ini_m"] < 33 * 15]
larg = {}
for f in faixas:
    larg[f["cat"]] = larg.get(f["cat"], 0.0) + (f["fim_m"] - f["ini_m"])
tot_larg = sum(larg.values()) or 1
for cat, det in r["por_categoria"].items():
    desenhado = larg.get(cat, 0.0) / tot_larg * 100
    check(f"'{cat}': detalhe {det['pct']:.1f}% == desenho {desenhado:.1f}%",
          abs(det["pct"] - desenhado) < 0.6, (det, desenhado))

print("\n[5] Posto vazio é categoria própria, não desperdício genérico")
r2 = pl.eventos_do_bin(novo_sb(), "U", "T", DIA, 8 * 60 + 20)
check("bloco 08:15–08:30", (r2["de"], r2["ate"]) == ("08:15", "08:30"))
check("100% posto vazio", r2["por_categoria"]["vazio"]["pct"] == 100.0, r2["por_categoria"])
check("não caiu em desp", "desp" not in r2["por_categoria"], r2["por_categoria"])
check("papel preservado no item", r2["itens"][0]["papel"] == "posto_vazio")

print("\n[6] Evento que atravessa a borda entra só com a fatia dele")
r3 = pl.eventos_do_bin(novo_sb(), "U", "T", DIA, 8 * 60 + 35)   # bin 34: 08:30–08:45
it = r3["itens"][0]
check("o evento de 24 min aparece", it["rotulo"] == "operar_torno")
check("mas só 15 min caem neste bloco", abs(it["segundos_no_bin"] - 900) < 0.5,
      it["segundos_no_bin"])
check("a duração TOTAL continua visível", abs(it["segundos"] - 1440) < 0.5, it["segundos"])
check("marcado como parcial", it["parcial"] is True)
r4 = pl.eventos_do_bin(novo_sb(), "U", "T", DIA, 8 * 60 + 50)   # bin 35: 08:45–09:00
check("no bloco seguinte entra o RESTO (9 min)",
      abs(r4["itens"][0]["segundos_no_bin"] - 540) < 0.5, r4["itens"][0]["segundos_no_bin"])
check("o bloco 08:45 não fecha 15 min — o evento acaba 08:54",
      abs(r4["segundos"] - 540) < 0.5, r4["segundos"])
soma = sum(pl.eventos_do_bin(novo_sb(), "U", "T", DIA, b * 15 + 1)["segundos"]
           for b in range(96))
check("somando todos os blocos dá o tempo observado do dia",
      abs(soma - dia["tempo_obs_s"]) < 1.0, (soma, dia["tempo_obs_s"]))

print("\n[7] Rollup por rótulo — 'quais ações compõem o bloco'")
check("o bloco 08:00 tem 2 rótulos", len(r["acoes"]) == 2, r["acoes"])
check("ordenado por tempo (o que move o número primeiro)",
      r["acoes"][0]["rotulo"] == "operar_torno", r["acoes"])
check("cada rótulo traz minutos, % e nº de trechos",
      all({"segundos", "pct", "n", "cat"} <= set(a) for a in r["acoes"]))

print("\n[8] Bloco vazio e data inválida não viram erro mudo")
r5 = pl.eventos_do_bin(novo_sb(), "U", "T", DIA, 3 * 60)     # 03:00, sem filmagem
check("bloco sem evento responde com contrato completo",
      r5["n_eventos"] == 0 and r5["itens"] == [] and "por_categoria" in r5, r5)
check("marcado como buraco", r5["buraco"] is True)
r6 = pl.eventos_do_bin(novo_sb(), "U", "T", "06/08/2026", 480)
check("data inválida devolve erro explicando o formato",
      "erro" in r6 and "AAAA-MM-DD" in r6["erro"], r6)
r7 = pl.eventos_do_bin(novo_sb(), "U", "T", "2026-01-01", 480)
check("dia sem vídeo nenhum diz isso", r7["n_eventos"] == 0 and "vídeo" in r7["nota"], r7)
r8 = pl.eventos_do_bin(novo_sb(), "U", "T", DIA, 1439.9)
check("o último minuto do dia não estoura", r8["bin"] == 95 and r8["ate"] == "24:00", r8)

print("\n[9] Filtros: o que a faixa não conta, o detalhe não mostra")
extras = EVENTOS + [
    {**ev(90, "operar_torno", 60, 120), "principal": False},
    {**ev(91, "operar_torno", 120, 120), "validacao_correto": False},
    {**ev(92, "operar_torno", 180, 0)},                    # duração zero
]
r9 = pl.eventos_do_bin(novo_sb(eventos=extras), "U", "T", DIA, 8 * 60 + 2)
ids = {it["id"] for it in r9["itens"]}
check("evento não-principal fica de fora", "e090" not in ids, ids)
check("evento marcado como errado fica de fora", "e091" not in ids, ids)
check("evento de duração zero fica de fora", "e092" not in ids, ids)
check("o total do bloco não mudou", abs(r9["segundos"] - 900) < 0.5, r9["segundos"])

print("\n[10] O teto do PostgREST — o dublê corta como o serviço real")
# 1500 vídeos: se a leitura não paginasse, os 500 últimos ficariam sem instante
# de gravação e TODOS os eventos deles sumiriam do bloco (o bug da Fase 81).
muitos_v = [{"id": f"z{i:04d}", "empresa": "U", "processo": "T",
             "nome": f"seg_20260805_{7 + i // 600:02d}0000.mp4", "cam_id": "cam1",
             "duracao_s": 60, "processado_em": "2026-08-05T09:00:00"}
            for i in range(1400)] + VIDEOS
sb10 = novo_sb(videos=muitos_v)
r10 = pl.eventos_do_bin(sb10, "U", "T", DIA, 8 * 60 + 7)
check("o vídeo do dia foi achado mesmo depois de 1400 outros",
      r10["n_eventos"] == 2, r10["n_eventos"])
check("paginou (mais de uma leitura de vídeos)",
      sb10.leituras.count("videos") > 1, sb10.leituras.count("videos"))

print("\n[11] Vídeo que vira a meia-noite: quem decide o dia é o EVENTO")
v_noite = [{"id": "vn", "empresa": "U", "processo": "T",
            "nome": "seg_20260805_235000.mp4", "cam_id": "cam1",
            "duracao_s": 1800, "processado_em": "2026-08-05T23:50:00"}]
e_noite = [ev(70, "operar_torno", 0, 300, vid="vn"),          # 23:50 do dia 05
           ev(71, "operar_torno", 900, 300, vid="vn")]        # 00:05 do dia 06
r11 = pl.eventos_do_bin(novo_sb(eventos=e_noite, videos=v_noite), "U", "T",
                        "2026-08-06", 5)
check("o trecho após a meia-noite conta no dia 06",
      r11["n_eventos"] == 1 and r11["itens"][0]["id"] == "e071", r11["itens"])
r12 = pl.eventos_do_bin(novo_sb(eventos=e_noite, videos=v_noite), "U", "T",
                        "2026-08-05", 23 * 60 + 55)
check("e o de antes conta no dia 05",
      r12["n_eventos"] == 1 and r12["itens"][0]["id"] == "e070", r12["itens"])

print("\n[12] Corte de lista: trunca, mas AVISA que truncou")
muitos_e = [ev(100 + i, "operar_torno", i * 5, 5) for i in range(60)]   # 5 min
r13 = pl.eventos_do_bin(novo_sb(eventos=muitos_e), "U", "T", DIA, 8 * 60 + 1,
                        limite=10)
check("mostra só o limite pedido", len(r13["itens"]) == 10, len(r13["itens"]))
check("mas conta o total de verdade", r13["n_eventos"] == 60, r13["n_eventos"])
check("e marca truncado", r13["truncado"] is True)
check("o total de tempo NÃO é truncado (a conta usa todos)",
      abs(r13["segundos"] - 300) < 0.5, r13["segundos"])

print("\n[13] Nenhuma leitura nova com .limit() acima do teto")
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
trecho = fonte[fonte.index("def eventos_do_bin"):fonte.index("def _hhmm_do_minuto")]
check("eventos_do_bin lê por varrer(), não por .limit()",
      ".limit(" not in trecho and "varrer(" in trecho)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
