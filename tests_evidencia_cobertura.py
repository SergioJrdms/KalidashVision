"""Fase 90 — quadro OLHADO deixa de ser o mesmo que minuto COBERTO.

O QUE ESTA SUÍTE PROTEGE, E POR QUE CADA COISA EXISTE

[1-3] A INTERPOLAÇÃO. `_subamostrar` manda no máximo KV_SEQUENCIA_MAX_IMG-1
      quadros ao VLM; os demais não recebiam descrição e a observação morria
      num `if not desc: continue`. O efeito não era perder detalhe — era o
      minuto SE PARTIR (o intervalo passa da janela de continuidade de 8 s) e
      o `tempo_obs_s`, denominador de toda métrica, cair junto: 55 s viravam
      25 s com MAX_IMG=6. Isto seria a Fase 86 de novo, no denominador em vez
      do rótulo.

[4-6] O VOTO. Observação herdada ou interpolada COBRE tempo e NÃO é evidência.
      Doze observações com a mesma descrição herdada dão share 1,00 — certeza
      máxima num minuto em que ninguém olhou nada. Número que mente com selo
      de certeza é pior que número faltando.

[7-8] "NÃO OLHEI" ≠ "OLHEI E NÃO SEI". Curvas separadas. Um se resolve com
      mais amostragem, o outro com melhor decisão; misturá-los faria um corte
      de orçamento parecer perda de confiança do modelo — e a curva da dúvida
      é a única métrica que responde se o produto funciona.

[9]   A PARCELA DO GATE fica visível: se o teto está agressivo demais, isso
      aparece na tela, não é descoberto por acaso semanas depois.

[10-11] A CAM2 só vai quando desambigua, e sai da checagem binária do gate —
      onde ela dobrava o custo e movia o break-even para ~7 checagens/minuto.

Rodar:  python tests_evidencia_cobertura.py
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


INTERV = 5.0


def obs(t, label, origem="analisado"):
    return {"tempo_s": t, "frame_idx": int(t * 6), "track_id": 7,
            "descricao": label, "bbox": [100, 100, 200, 400], "bbox_cam": "cam1",
            "bbox_dim": (640, 480), "zona": "posto", "papel": "operador",
            "origem_gate": origem, "maquina": None, "imovel": None}


def segmentar(obs_l):
    evs = pl.etapa_segmentar_eventos(obs_l, lambda d, *a: d, INTERV)
    return {
        "eventos": len(evs),
        "dur": sum(e["tempo_fim_s"] - e["tempo_inicio_s"] for e in evs),
        "votos": sum(e["n_amostras"] for e in evs),
        "cobertura": sum(e["n_observacoes"] for e in evs),
        "evs": evs,
    }


print("\n[1] A interpolação preenche os buracos do subamostreio")
idx_cam1 = list(range(12))
desc = {i: {"acoes": {7: "operar_torno"}, "maquina": None, "imovel": False}
        for i in pl._subamostrar(idx_cam1, 5)}          # MAX_IMG=6 → 5 quadros
check("antes: só 5 índices têm descrição", len(desc) == 5, sorted(desc))
interp = pl._interpolar_sequencia(desc, idx_cam1)
check("depois: os 12 têm", len(desc) == 12, sorted(desc))
check("7 foram interpolados", len(interp) == 7, sorted(interp))
check("os enviados NÃO são marcados como interpolados",
      not (set(pl._subamostrar(idx_cam1, 5)) & interp))
check("sem nenhum quadro analisado não há o que estender",
      pl._interpolar_sequencia({}, idx_cam1) == set())

print("\n[2] Cobertura restaurada: o minuto não se parte mais")
todos = [i * INTERV for i in range(12)]
enviados = pl._subamostrar(list(range(12)), 5)
r_quebrado = segmentar([obs(todos[i], "operar_torno") for i in enviados])
r_inteiro = segmentar([obs(t, "operar_torno",
                           "analisado" if i in enviados else "interpolado_sequencia")
                       for i, t in enumerate(todos)])
check(f"SEM o conserto: {r_quebrado['eventos']} eventos, {r_quebrado['dur']:.0f}s",
      r_quebrado["eventos"] == 5 and r_quebrado["dur"] == 25.0, r_quebrado)
check(f"COM o conserto: {r_inteiro['eventos']} evento, {r_inteiro['dur']:.0f}s",
      r_inteiro["eventos"] == 1 and r_inteiro["dur"] == 60.0, r_inteiro)
check("o denominador volta ao que era com MAX_IMG=12",
      r_inteiro["dur"] >= 55.0, r_inteiro["dur"])

print("\n[3] …mas a EVIDÊNCIA cai para o número real de quadros olhados")
check("cobertura = 12 observações", r_inteiro["cobertura"] == 12, r_inteiro)
check("votos = 5 (só os analisados)", r_inteiro["votos"] == 5, r_inteiro)
check("interpolada NÃO vota",
      r_inteiro["votos"] < r_inteiro["cobertura"], r_inteiro)

print("\n[4] Observação herdada pelo gate: mesma regra")
r_herda = segmentar([obs(todos[0], "operar_torno", "analisado")]
                    + [obs(t, "operar_torno", "repeticao_pose") for t in todos[1:]])
check("o minuto continua coberto", r_herda["dur"] == 60.0, r_herda)
check("mas só 1 quadro foi olhado", r_herda["votos"] == 1, r_herda)
check("e a composição fica registrada",
      r_herda["evs"][0]["origens"].get("repeticao_pose") == 11,
      r_herda["evs"][0]["origens"])

print("\n[5] Minuto 100% herdado: cobre o tempo e NÃO afirma nada")
r_zero = segmentar([obs(t, "operar_torno", "repeticao_pose") for t in todos])
check("cobertura cheia", r_zero["dur"] == 60.0 and r_zero["cobertura"] == 12, r_zero)
check("ZERO votos", r_zero["votos"] == 0, r_zero)
check("confiança do cru é None (não 1.0)",
      all(e["confianca"] is None for e in r_zero["evs"]),
      [e["confianca"] for e in r_zero["evs"]])

print("\n[6] A consolidação usa VOTOS, não observações")
crus = r_zero["evs"]
p = pl.etapa_consolidar_principais(crus, {"operar_torno": "x"}, 60.0)[0]
check("n_amostras do principal = votos = 0", p["n_amostras"] == 0, p["n_amostras"])
check("n_observacoes = 12", p["n_observacoes"] == 12, p.get("n_observacoes"))
check("confiança NÃO é 1.00 num minuto sem olhar",
      p["confianca"] is None, p["confianca"])
check("a composição chega ao principal",
      (p.get("observacoes_origem") or {}).get("repeticao_pose") == 12,
      p.get("observacoes_origem"))
# O contraste: minuto realmente analisado mantém confiança.
p2 = pl.etapa_consolidar_principais(
    segmentar([obs(t, "operar_torno") for t in todos])["evs"],
    {"operar_torno": "x"}, 60.0)[0]
check("minuto analisado de verdade mantém confiança 1.00",
      p2["confianca"] == 1.0 and p2["n_amostras"] == 12, (p2["confianca"], p2["n_amostras"]))

print("\n[7] 'NÃO OLHEI' é tipo próprio, separado de 'olhei e não sei'")
ev_herdado = {"n_amostras": 0, "confianca": None, "n_rotulos_no_minuto": 1,
              "observacoes_origem": {"repeticao_pose": 12}}
dv, motivo, tp = pl.evento_em_duvida(ev_herdado, 0.7)
check("entra na fila", dv is True)
check("tipo é 'nao_observado'", tp == "nao_observado", tp)
check("NÃO é 'sem_evidencia'", tp != "sem_evidencia")
check("o motivo explica que o tempo está coberto mas não é evidência",
      "herdada" in motivo and "não são evidência" in motivo, motivo)
ev_pouco = {"n_amostras": 1, "confianca": 1.0, "n_rotulos_no_minuto": 1,
            "observacoes_origem": {"analisado": 1}}
dv2, _, tp2 = pl.evento_em_duvida(ev_pouco, 0.7)
check("trecho curto de verdade continua 'sem_evidencia'",
      dv2 is True and tp2 == "sem_evidencia", tp2)

print("\n[8] As curvas do dia não se misturam")
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
check("existe curva própria nao_observado_pct", '"nao_observado_pct"' in fonte)
check("o dia sem trabalho também a declara (contrato completo)",
      fonte.count('"nao_observado_pct"') >= 2)
check("nao_observado NÃO soma em duvida",
      'd["nao_observado"] += dur' in fonte
      and 'if _tp == "nao_observado":' in fonte)
check("e NÃO soma em sem_evidencia",
      fonte.index('d["nao_observado"] += dur')
      < fonte.index('d["sem_evidencia"] += dur'))

print("\n[9] A parcela causada pelo TETO DO GATE fica visível")
check("existe nao_observado_gate_pct", '"nao_observado_gate_pct"' in fonte)
check("separada por origem 'repeticao'",
      'k.startswith("repeticao")' in fonte)
check("com o porquê escrito no código",
      "teto está agressivo demais" in fonte)

print("\n[10] A cam2 só vai quando tem o que desambiguar")
class Am:
    def __init__(self, pessoas, op=None, maos=False):
        self.pessoas = pessoas; self.operador_presente = op; self.maos_cam2 = maos
        self.img_b64 = "x"; self.img_b64_secundario = "y"
kp_ok = [(0.5, 0.5)] * 17
kp_meio = [(0.5, 0.5)] * 8 + [(0.0, 0.0)] * 9
check("operador inteiro e visível → NÃO manda a cam2",
      pl._cam2_ajuda([Am([{"papel": "operador", "kpts": kp_ok}])]) is False)
check("pose parcial (corpo cortado pela máquina) → manda",
      pl._cam2_ajuda([Am([{"papel": "operador", "kpts": kp_meio}])]) is True)
check("operador presente mas invisível na cam1 (oclusão total) → manda",
      pl._cam2_ajuda([Am([], op=True)]) is True)
check("mãos na máquina pela cam2 → manda",
      pl._cam2_ajuda([Am([{"papel": "operador", "kpts": kp_ok}], maos=True)]) is True)
check("sem pose → manda (não dá para saber se está inteiro)",
      pl._cam2_ajuda([Am([{"papel": "operador", "kpts": None}])]) is True)
check("basta UMA amostra do minuto precisar",
      pl._cam2_ajuda([Am([{"papel": "operador", "kpts": kp_ok}]),
                      Am([{"papel": "operador", "kpts": kp_meio}])]) is True)

print("\n[11] A checagem binária do gate perdeu a segunda imagem")
bin_src = fonte[fonte.index("def _gate_vlm_binario"):]
bin_src = bin_src[:bin_src.index("def _agrupar_amostras")]
check("não manda imagens_extra", "imagens_extra" not in bin_src, bin_src[-400:])
check("o porquê está escrito (break-even do gate)",
      "break-even" in bin_src)
check("a pergunta continua sendo sobre a âncora da cam1",
      "mesma ação" in bin_src)

print("\n[12] Persistência e versão")
check("n_observacoes é gravado", '"n_observacoes": e.get("n_observacoes")' in fonte)
check("observacoes_origem é gravado",
      '"observacoes_origem": e.get("observacoes_origem")' in fonte)
check("versão do instrumento subiu para 5", pl.VERSAO_INSTRUMENTO == 5)
sql = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sql", "schema.sql"), encoding="utf-8").read()
check("schema declara as colunas novas",
      "add column if not exists n_observacoes" in sql
      and "add column if not exists observacoes_origem" in sql)
check("e cria a ai_uso que nunca existiu",
      "create table if not exists ai_uso" in sql)
check("com o registro de que a trava nunca travou",
      "nunca travou" in sql)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
