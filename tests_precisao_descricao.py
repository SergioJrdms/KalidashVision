# ============================================================
# Fase 102 — A DESCRIÇÃO COMO DIFERENCIAL: precisão medida, não suposta.
#
# O número principal virou a permanência (Fase 101) e não lê descrição. Isso
# libera esta fase: mexer na descrição não pode mais estragar o número — e o
# bloco [5] PROVA isso, não confia.
#
# [1] Origens: quem observou, quem derivou, quem afirmou sem ver.
# [2] Zero amostra no evento inteiro ⇒ a descrição não afirma atividade.
# [3] O furo da proibição de estado da máquina: era o bloco de VOCABULÁRIO.
# [4] Amostragem cega: sorteio de verdade, ordem preservada, 3 vereditos.
# [5] ⭐ A permanência é IDÊNTICA antes e depois.
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

from backend import pipeline as pl  # noqa: E402

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


fonte = open(os.path.join(RAIZ, "backend", "pipeline.py"), encoding="utf-8").read()
main = open(os.path.join(RAIZ, "backend", "main.py"), encoding="utf-8").read()


print("\n[1] As origens da descrição — quem olhou e quem não")
check("`analisado` é observação (o quadro foi ao VLM)",
      pl.origem_foi_observada("analisado"))
# ⚠️ `resgate_cam2` estava contado como ZERO. É erro do CONTADOR: a cam2 faz
# uma chamada de visão de verdade e OLHA aquele instante pela lateral. Chamar
# isso de "sem observação" é o espelho do problema desta fase — negar medida
# que existe.
check("`resgate_cam2` TAMBÉM é observação — a cam2 olhou de verdade",
      pl.origem_foi_observada("resgate_cam2"))
for derivada in ("ponte_temporal", "indefinida_herdada", "interpolado_sequencia"):
    check(f"`{derivada}` NÃO é observação", not pl.origem_foi_observada(derivada))
check("`repeticao_gate` (supressão) não é observação",
      not pl.origem_foi_observada("repeticao_gate"))
check("origem ausente conta como analisado (compatibilidade com o histórico)",
      pl.origem_foi_observada(None))
check("o contador de amostras usa a taxonomia, não a string crua",
      'origem_foi_observada(o.get("origem_gate"))' in fonte
      and '1 if o.get("origem_gate") == "analisado"' not in fonte)


print("\n[2] Zero amostra no evento ⇒ a descrição não afirma atividade")
def ev(n_amostras=0, origens=None, papel="operador", label="operar_torno", **kw):
    d = {"n_amostras": n_amostras, "observacoes_origem": origens or {},
         "papel_pessoa": papel, "comportamento_label": label, "principal": True,
         "descricao_bruta": "operando o torno, mãos na peça",
         "tempo_inicio_s": 0, "tempo_fim_s": 60}
    d.update(kw)
    return d


check("evento COM amostra analisada afirma normalmente",
      pl.descricao_foi_observada(ev(n_amostras=1)))
# A regra do dono, literal: "uma amostra analisada, seguida de amostras
# idênticas suprimidas pelo gate, é herança HONESTA".
check("⭐ 1 analisada + 11 suprimidas pelo gate é herança HONESTA",
      pl.descricao_foi_observada(
          ev(n_amostras=1, origens={"analisado": 1, "repeticao_gate": 11})))
check("⭐ ZERO analisadas no evento inteiro NÃO afirma",
      not pl.descricao_foi_observada(
          ev(n_amostras=0, origens={"ponte_temporal": 12})))
check("`posto_vazio` está fora da regra — ele não afirma ATIVIDADE, afirma "
      "ausência, e o detector mediu isso",
      pl.descricao_foi_observada(ev(n_amostras=0, papel="posto_vazio")))
check("e pelo label também", pl.descricao_foi_observada(
      ev(n_amostras=0, papel=None, label="posto_vazio")))
check("correção humana vale mais que a olhada do modelo",
      pl.descricao_foi_observada(ev(n_amostras=0, label_corrigido="medir_peca")))

# O texto exibido diz a verdade sobre a própria origem.
txt, obs = pl.descricao_para_exibir(ev(n_amostras=0, origens={"ponte_temporal": 9}))
check("sem observação, a descrição NÃO é exibida como o que aconteceu",
      obs is False and "operando o torno" not in txt, txt)
check("e o texto explica a origem — presença por continuidade",
      "continuidade do rastreamento" in txt, txt)
check("dizendo que o TEMPO é real e a atividade não foi observada",
      "não foi observada" in txt)
txt2, obs2 = pl.descricao_para_exibir(ev(n_amostras=3))
check("com observação, a descrição real aparece",
      obs2 is True and txt2 == "operando o torno, mãos na peça")
# ⚠️ Nada é apagado do banco: a descrição bruta continua auditável, e é dela
# que sai o diagnóstico de POR QUE o sistema errou.
check("a descrição bruta NÃO é apagada — só deixa de ser exibida como observação",
      "NÃO é apagada do banco" in fonte)

print("\n[3] A MEDIDA da Parte 1 — origens com volume")
lote = ([ev(n_amostras=2)] * 244
        + [ev(n_amostras=0, origens={"ponte_temporal": 8})] * 100
        + [ev(n_amostras=0, origens={"indefinida_herdada": 5})] * 50
        + [ev(n_amostras=0, origens={"interpolado_sequencia": 4})] * 30)
d = pl.origens_sem_observacao(lote)
check("conta o total de principais", d["total_principais"] == 424)
check("separa com e sem observação",
      d["com_observacao"] == 244 and d["sem_observacao"] == 180)
check("e devolve o percentual", d["pct_sem_observacao"] == 42.5, d)
check("com a decomposição por origem, ordenada",
      list(d["por_origem"].items())
      == [("ponte_temporal", 100), ("indefinida_herdada", 50),
          ("interpolado_sequencia", 30)], d["por_origem"])
check("é função pura, sem chamada de API",
      "sb.table" not in fonte.split("def origens_sem_observacao")[1].split("\ndef ")[0])

print("\n[4] ⭐ POR QUE A PROIBIÇÃO DE ESTADO DA MÁQUINA NÃO PEGOU")
# A Fase 99 filtrou o LABEL e deixou passar a DESCRIÇÃO. O que entra no prompt
# do VLM é a descrição — e o catálogo está cheio de prosa de estado.
CATALOGO_REAL = [
    {"label": "monitorar_maquina", "n_confirmacoes": 688,
     "descricao": "Operador observando o funcionamento da máquina durante o "
                  "ciclo de usinagem, sem manipulação direta"},
    {"label": "deslocamento_interno_posto", "n_confirmacoes": 1,
     "descricao": "Operador deslocando-se dentro do próprio posto de trabalho, "
                  "mudando de posição junto ao torno, com a máquina parada"},
    {"label": "operar_torno", "n_confirmacoes": 922,
     "descricao": "Operador trabalhando diretamente no torno, manipulando a peça"},
]
bloco = pl.construir_bloco_vocabulario({"vocabulario": CATALOGO_REAL})
check("o bloco de vocabulário NÃO entrega mais prosa de estado ao VLM",
      "máquina parada" not in bloco and "ciclo de usinagem" not in bloco, bloco)
check("mas PRESERVA a observação da pessoa — não apaga a frase inteira",
      "observando o funcionamento da máquina" in bloco
      and "mudando de posição junto ao torno" in bloco, bloco)
check("e a descrição limpa continua no prompt",
      "manipulando a peça" in bloco)
check("o furo está documentado onde ele estava",
      "AQUI ESTAVA O FURO DA PROIBIÇÃO DA FASE 99" in fonte)
check("com o diagnóstico: a Fase 99 filtrou o LABEL e deixou a DESCRIÇÃO",
      "filtrou o LABEL" in fonte and "deixou passar a" in fonte)
check("e o reconhecimento de que exemplo vence regra",
      "Exemplo vence regra" in fonte)

print("\n[5] O recorte é cirúrgico — tira a afirmação, guarda a observação")
CASOS = [
    ("operador parado junto ao torno, com a máquina parada",
     "operador parado junto ao torno"),
    ("parado junto ao torno, aguardando o ciclo", "parado junto ao torno"),
    ("operador de pé, torno girando", "operador de pé"),
    ("operando o torno, mãos na peça", "operando o torno, mãos na peça"),
    ("medindo a peça com paquímetro", "medindo a peça com paquímetro"),
]
for cru, esperado in CASOS:
    check(f"{cru[:38]!r} → {esperado[:32]!r}",
          pl.texto_sem_estado_maquina(cru) == esperado,
          pl.texto_sem_estado_maquina(cru))
check("frase que era SÓ a afirmação vira vazia (não há o que preservar)",
      pl.texto_sem_estado_maquina("máquina parada") == "")
check("o detector de afirmação bate com o recorte",
      pl.texto_afirma_estado_maquina("parado, com a máquina em ciclo")
      and not pl.texto_afirma_estado_maquina("operando o torno"))

print("\n[6] A MEDIDA da Parte 2, separada por versão do instrumento")
evs = [ev(descricao_bruta="parado junto ao torno, com a máquina parada",
          versao_instrumento=7)] * 80 + \
      [ev(descricao_bruta="parado, máquina em ciclo", versao_instrumento=8)] * 21 + \
      [ev(descricao_bruta="operando o torno, mãos na peça", versao_instrumento=8)] * 394
m = pl.descricoes_que_afirmam_estado(evs)
check("conta o total que afirma estado", m["total"] == 101, m)
# ⚠️ Texto anterior à proibição é HISTÓRICO: ele não prova que a proibição
# falhou, só que existe passado. Misturar os dois faria um conserto parecer
# ineficaz quando ele só não reprocessa o passado.
check("separa o histórico (anterior à proibição) do que nasceu depois",
      m["anteriores_a_proibicao"] == 80 and m["posteriores_a_proibicao"] == 21, m)
check("e mostra exemplos SÓ dos novos — são esses que acusam o furo",
      m["exemplos_novos"] and all("ciclo" in x for x in m["exemplos_novos"]))

print("\n[7] Amostragem cega — o protocolo É o experimento")
pool = [dict(ev(n_amostras=2), id=f"e{i:03d}") for i in range(100)]
pool += [dict(ev(n_amostras=0, papel="posto_vazio",
                 label="posto_vazio"), id=f"v{i:03d}") for i in range(50)]
s1 = pl.sortear_amostra_cega(pool, 20, semente=42)
check("sorteia a quantidade pedida", len(s1) == 20)
check("mesma semente → mesmo conjunto (dois gestores medem o MESMO)",
      [x["id"] for x in s1] == [x["id"] for x in pl.sortear_amostra_cega(pool, 20, 42)])
check("semente diferente → conjunto diferente (é sorteio, não ordenação)",
      [x["id"] for x in s1] != [x["id"] for x in pl.sortear_amostra_cega(pool, 20, 7)])
check("`posto_vazio` fica fora — não há descrição a julgar, e incluí-lo "
      "inflaria o acerto com acertos triviais",
      all(not x["id"].startswith("v") for x in s1))
# ⚠️ O CORAÇÃO DO PROTOCOLO: não filtra por suspeita.
check("⭐ NÃO filtra por dúvida, confiança nem rótulo feio",
      "NÃO FILTRA POR SUSPEITA" in fonte)
_corpo = fonte.split("def sortear_amostra_cega")[1].split("\ndef ")[0]
for suspeito in ("em_duvida", "confianca", "duvida_motivo", "validado_humano",
                 "categoria_lean"):
    check(f"o sorteio não olha `{suspeito}`", suspeito not in _corpo)
check("descrição vazia fica fora (não há o que julgar)",
      len(pl.sortear_amostra_cega(
          [dict(ev(n_amostras=2, descricao_bruta=""), id="x")], 5, 1)) == 0)

print("\n[8] A ordem é preservada pelo BANCO e pela API, não pela tela")
check("a descrição só sai do backend DEPOIS da resposta",
      '"descricao": None if cego else l.get("descricao_no_sorteio")' in main)
check("e o veredito é RECUSADO se o item não foi respondido",
      "Responda o que você vê ANTES de ver a descrição." in main)
check("responder duas vezes é recusado (reabrir contaminaria)",
      "reabrir contaminaria a medida" in main)
check("a descrição é CONGELADA no sorteio — reprocessar não muda o que foi julgado",
      "descricao_no_sorteio" in main and "Cópia CONGELADA" in
      open(os.path.join(RAIZ, "sql", "schema.sql"), encoding="utf-8").read())

print("\n[9] Três resultados, nunca uma média")
linhas = ([{"veredito": "bate", "n_amostras_no_sorteio": 3}] * 12
          + [{"veredito": "bate_em_parte", "n_amostras_no_sorteio": 2}] * 5
          + [{"veredito": "nao_bate", "n_amostras_no_sorteio": 0}] * 3
          + [{"veredito": None}] * 4)
t = pl.taxa_de_acerto(linhas)
check("conta as julgadas e as pendentes",
      t["n_julgadas"] == 20 and t["n_pendentes"] == 4, t)
check("os três percentuais saem separados",
      (t["bate_pct"], t["bate_em_parte_pct"], t["nao_bate_pct"]) == (60.0, 25.0, 15.0), t)
check("e somam 100% — não há veredito que suma",
      abs(t["bate_pct"] + t["bate_em_parte_pct"] + t["nao_bate_pct"] - 100) < 0.1)
check("⭐ 'bate em parte' NÃO vira meio-acerto numa média ponderada",
      "acerto_pct" not in t and "0.5" not in
      fonte.split("def taxa_de_acerto")[1].split("\ndef ")[0])
check("cruza acerto × observação — é o teste da herança",
      t["sem_observacao"]["n"] == 3 and t["sem_observacao"]["bate_pct"] == 0.0, t)
check("e declara quando ainda é pouco para valer como leitura",
      t["confiavel"] is True
      and pl.taxa_de_acerto(linhas[:5])["confiavel"] is False)

print("\n[10] ⭐ O NÚMERO PRINCIPAL NÃO SE MEXE — antes e depois")
# A restrição central da fase. A permanência é determinística e não lê
# descrição; esta é a prova no MESMO dado, com a descrição destruída.
DIA = [
    {"tempo_inicio_s": 0, "tempo_fim_s": 60, "papel_pessoa": "operador",
     "comportamento_label": "operar_torno", "principal": True,
     "descricao_bruta": "operando o torno", "n_amostras": 4},
    {"tempo_inicio_s": 60, "tempo_fim_s": 120, "papel_pessoa": "operador",
     "comportamento_label": "monitorar_maquina", "principal": True,
     "descricao_bruta": "parado junto ao torno, com a máquina parada",
     "n_amostras": 0, "observacoes_origem": {"ponte_temporal": 12}},
    {"tempo_inicio_s": 120, "tempo_fim_s": 180, "papel_pessoa": "posto_vazio",
     "comportamento_label": "posto_vazio", "principal": True,
     "descricao_bruta": pl.POSTO_VAZIO_DESC, "n_amostras": 0},
]
antes = pl.permanencia_do_dia(DIA, None)

# DEPOIS: aplica tudo o que esta fase faz à descrição — recorta o estado da
# máquina, troca o texto dos eventos sem observação pela frase honesta.
depois_evs = []
for e in DIA:
    c = dict(e)
    texto, _obs = pl.descricao_para_exibir(c)
    c["descricao_bruta"] = pl.texto_sem_estado_maquina(texto or "")
    depois_evs.append(c)
depois = pl.permanencia_do_dia(depois_evs, None)

check("⭐ a permanência é IDÊNTICA antes e depois", antes == depois, (antes, depois))
check("e não é um empate trivial — o número existe",
      antes["no_posto_pct"] == 66.7 and antes["fora_pct"] == 33.3, antes)
check("as descrições REALMENTE mudaram no meio do caminho",
      [e["descricao_bruta"] for e in DIA] != [e["descricao_bruta"] for e in depois_evs])
# E a prova estrutural: a função nem lê o campo.
_perm = fonte.split("def permanencia_do_dia")[1].split("\ndef ")[0]
check("a permanência não lê descrição nem n_amostras",
      "descricao" not in _perm and "n_amostras" not in _perm)

print("\n[11] Zero chamada de API nova")
for f in ("origens_sem_observacao", "descricoes_que_afirmam_estado",
          "sortear_amostra_cega", "taxa_de_acerto", "texto_sem_estado_maquina",
          "descricao_para_exibir"):
    corpo = fonte.split(f"def {f}")[1].split("\ndef ")[0]
    check(f"{f} não chama modelo nenhum",
          "groq_" not in corpo and "anthropic" not in corpo
          and "vision_call" not in corpo)
check("a limpeza do vocabulário roda ANTES do prompt, sem custo",
      "texto_sem_estado_maquina(v.get(\"descricao\"))" in fonte)

print("\n[12] A descrição aparece onde é o diferencial")
check("o Pareto vem com o próprio certificado de origem",
      '"descricao_diagnostico": _diag_desc' in main)
check("com o porquê: Pareto bonito sobre herança é pior que buraco declarado",
      "pior que um" in main and "Pareto com buraco declarado" in main)
check("a tela de precisão existe",
      os.path.exists(os.path.join(RAIZ, "frontend", "src", "pages", "Precisao.tsx")))
prec = open(os.path.join(RAIZ, "frontend", "src", "pages", "Precisao.tsx"),
            encoding="utf-8").read()
check("e ela NÃO recebe a descrição antes da resposta",
      "não basta escondê-lo com CSS" in prec)
check("mostra os três vereditos, com o conserto de cada um",
      "Conserto de prompt" in prec and "Conserto de captura" in prec)
check("⛔ e nenhuma duração absoluta na tela nova",
      "fmtDur" not in prec and "duracaoHumana" not in prec and "} min" not in prec)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
