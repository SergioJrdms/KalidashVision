"""Fase 85 — sequência no lugar do instante, e os três tetos de herança.

O PROBLEMA MEDIDO PELO DONO: dos 5 comportamentos que existiam, 4 eram
produtivos e o único improdutivo era `posto_vazio`. `monitorar_maquina` comia
31% do tempo e concentrava 100% da dúvida. Ou seja: o modelo não tinha como
registrar improdutividade com o operador presente, e a produtividade de 75% era
o TETO DO QUE O INSTRUMENTO MEDIA, não um resultado.

A CAUSA não era só viés do modelo — o prompt MANDAVA o rótulo produtivo no caso
ambíguo: "Se ele está PARADO ... é 'monitorando o ciclo da máquina' ou
'observando a operação' ... Na dúvida entre operar e monitorar, escolha
MONITORAR". Duas saídas, as duas produtivas. A dúvida não tinha para onde ir.

E três caminhos independentes fabricavam produtividade mesmo com prompt novo:
  1. o GATE suprime pose idêntica — que é exatamente o sinal de imobilidade;
  2. "ação não identificada" HERDA a última ação conhecida (Fase 34), o que
     converte DESCONHECIDO em PRODUTIVO;
  3. a PONTE temporal herda sem ver imagem nenhuma.

Rodar:  python tests_sequencia_vlm.py
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
os.environ["KV_PRODUTIVIDADE_OPERADOR_V9"] = "on"

import re  # noqa: E402

from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


def am(t, pessoas=(), sec=None, presente=None, ponte=False):
    a = pl.Amostra(frame_idx=int(t * 10), tempo_s=float(t),
                   img_b64=f"IMG{int(t)}", pessoas=list(pessoas), dim=(640, 480))
    a.img_b64_secundario = sec
    a.operador_presente = presente
    a.operador_ponte = ponte
    a.bbox_cam2 = (10, 10, 60, 300) if sec else None
    a.dim_cam2 = (640, 480) if sec else None
    return a


def pessoa(tid=7, papel="operador", rotulo="P1"):
    return {"track_id": tid, "rotulo": rotulo, "papel": papel, "zona": "posto",
            "zona_desc": "posto do torno", "bbox": (100, 60, 160, 380),
            "kpts": None, "crop": None, "maos_maquina": False}


print("[1] Agrupamento: o minuto é o mesmo balde da consolidação")
amostras = [am(t) for t in (0, 8, 16, 56, 64, 72, 130)]
g = pl._agrupar_amostras(amostras, 60.0)
check("três grupos (minuto 0, 1 e 2)", len(g) == 3, [len(x) for x in g])
check("o minuto 0 leva 0/8/16/56", [a.tempo_s for a in g[0]] == [0, 8, 16, 56],
      [a.tempo_s for a in g[0]])
check("64 e 72 vão para o minuto seguinte", [a.tempo_s for a in g[1]] == [64, 72])
check("bucket 0 desliga o agrupamento (uma amostra por grupo)",
      len(pl._agrupar_amostras(amostras, 0.0)) == len(amostras))

print("\n[2] Subamostragem preserva o começo e o FIM")
check("lista curta passa inteira", pl._subamostrar([1, 2, 3], 5) == [1, 2, 3])
s = pl._subamostrar(list(range(20)), 5)
check("respeita o teto", len(s) == 5, s)
check("mantém o primeiro e o último — é neles que está o 'começou/terminou'",
      s[0] == 0 and s[-1] == 19, s)

src = open("backend/pipeline.py", encoding="utf-8").read()

print("\n[3] O prompt DESCREVE; não escolhe rótulo nem lado")
p_seq = pl.PROMPT_VLM_SEQUENCIA
p_cam2 = pl.PROMPT_VLM_SEQUENCIA_CAM2
# As regras são texto quebrado em várias linhas; comparar substring crua daria
# falso negativo. Normaliza os espaços antes de procurar.
regras = re.sub(r"\s+", " ", pl._REGRAS_DESCRICAO_V9)
exemplos = pl._BLOCO_EXEMPLOS_DESCRICAO
check("o desempate que só tinha saídas produtivas SUMIU dos dois prompts",
      "escolha MONITORAR" not in regras
      and "escolha MONITORAR" not in p_seq and "escolha MONITORAR" not in p_cam2)
check("e o mapeamento parado→'monitorando o ciclo' também",
      "é \"monitorando o ciclo da máquina\"" not in regras)
check("e some do prompt do resgate, que era a rota alternativa",
      "escolha MONITORAR" not in p_cam2)
check("o julgamento não contamina a descrição e fica no campo estruturado",
      "decisão binária fica SOMENTE no campo `trabalho`" in regras)
check("ausência de mudança é observação legítima",
      "AUSÊNCIA DE MUDANÇA É UMA OBSERVAÇÃO" in regras)
check("e é proibido preencher com a ação mais provável",
      "NÃO preencha com a ação mais provável" in regras)
# ⚠️ Fase 99 — REVOGADO. Pedir o estado da máquina era a Fase 85; a Fase 89
# MEDIU e provou que o VLM não consegue julgá-lo a partir de imagem parada (o
# estado afirmado trocava como moeda entre minutos consecutivos). Agora a
# regra é a PROIBIÇÃO.
check("o estado da MÁQUINA é PROIBIDO — foi medido e ele não consegue ver",
      "NÃO AFIRME O ESTADO DA MÁQUINA" in regras
      and "o que a MÁQUINA está fazendo" not in regras, regras[:200])
check("a exceção do sensor de mãos continua (esperar ciclo com mãos = operar)",
      "MÃOS na máquina" in regras and "sensor" in regras)

print("\n[4] Os EXEMPLOS não ensinam mais a afirmar estado de máquina")
# Exemplo é a instrução mais forte de um prompt: enquanto "máquina em ciclo"
# estivesse aqui, a proibição competia com ele e perdia.
check("nenhum exemplo afirma estado da máquina",
      "máquina em ciclo" not in exemplos and "máquina parada" not in exemplos,
      exemplos)
check("mas a postura parada continua exemplificada (é observável)",
      "parado junto ao torno" in exemplos)
check("Fase 86: o exemplo não carrega mais a muleta 'de frente ao torno'",
      "de frente ao torno" not in exemplos, exemplos)
check("há exemplos claramente produtivos", "operando o torno, mãos na peça" in exemplos)
check("e claramente improdutivos", "mexendo no celular" in exemplos)
check("os exemplos continuam só observáveis; a regra decide fora da frase",
      "produtivo" not in exemplos.replace("improdutivo", ""))

print("\n[5] A pergunta mudou de 'o que se vê' para 'o que aconteceu'")
check("a sequência é declarada como cronológica",
      "EM ORDEM CRONOLÓGICA" in p_seq and "EM ORDEM CRONOLÓGICA" in p_cam2)
check("e o modelo é instruído a COMPARAR as imagens",
      "COMPARE as imagens entre si" in p_seq)
check("o intervalo entre imagens vai no prompt (sem ele não há 'parado há quanto')",
      "{intervalo_s}s entre imagens" in p_seq)
check("a saída é POR INSTANTE, não um resumo do minuto",
      '"trechos"' in p_seq and '"i": 0' in p_seq)
check("os rótulos P1/P2 são declarados como POR IMAGEM (o mesmo P1 pode ser "
      "outra pessoa em outro frame)",
      "desenhados em CADA imagem separadamente" in p_seq)
check("P1/P2 são candidatos, não identidade presumida",
      "NÃO presuma que P1 é o operador" in p_seq)
check("a saída distingue identificado, ausente e incerto",
      '"operador_estado"' in p_seq and all(x in p_seq for x in (
          '"identificado"', '"ausente"', '"incerto"')))
check("a cam2 é declarada como contexto, não como instante a mais",
      "não gere entrada" in re.sub(r"\s+", " ", src))

print("\n[6] Os três tetos existem e são configuráveis")
check("teto do gate", pl._GATE_MAX_REPETICOES >= 1)
check("teto da herança de indefinida/ponte", pl._HERANCA_MAX_SEGUIDAS >= 1)
check("o teto do gate FORÇA análise em vez de herdar",
      'if repeticoes_seguidas.get(tid, 0) >= _GATE_MAX_REPETICOES:' in src
      and 'd_am[tid] = ("analisar", "")' in src)
check("o contador de repetições zera quando a amostra é de fato analisada",
      'repeticoes_seguidas[tid] = 0' in src)
check("a herança de indefinida respeita o teto",
      'if heranca_seguidas >= _HERANCA_MAX_SEGUIDAS:' in src)
check("a PONTE temporal também tem teto (herda sem ver imagem nenhuma)",
      src.count('if heranca_seguidas >= _HERANCA_MAX_SEGUIDAS:') >= 2)
check("uma descrição nova zera o contador de herança",
      'heranca_seguidas = 0' in src)

print("\n[7] O resgate pela cam2 usa o prompt NOVO (3ª porta dos fundos)")
i = src.index("def _analisar_sequencia_cam2(")
corpo = src[i:src.index("def _gate_vlm_binario(")]
check("o resgate virou sequência", "PROMPT_VLM_SEQUENCIA_CAM2" in corpo)
check("com as regras V9 de descrição", "_REGRAS_DESCRICAO_V9" in corpo)
check("e os mesmos exemplos simétricos", "exemplos=_BLOCO_EXEMPLOS_DESCRICAO" in corpo)
check("o comentário registra por que ela entrou junto",
      "rota preferencial da produtividade" in corpo)

print("\n[8] Uma chamada por grupo, cam2 só no instante do meio")
i = src.index("def _analisar_sequencia_vlm(")
corpo_seq = src[i:src.index("def _analisar_sequencia_cam2(")]
check("a cam2 entra com UM frame, o do meio",
      "meio = usados[len(usados) // 2]" in corpo_seq)
check("e o comentário diz por quê (dobrar as imagens comeria o ganho)",
      "comeria o ganho de custo" in corpo_seq)
check("o teto de imagens por chamada existe", "_SEQ_MAX_IMG" in corpo_seq)
check("a resposta é remapeada para a AMOSTRA certa (índice na sequência ≠ "
      "índice no grupo quando há subamostragem)",
      "idx_no_grupo" in corpo_seq)

print("\n[9] O downstream não muda: uma observação por instante")
i = src.index("def etapa_analise_vlm(")
corpo_vlm = src[i:src.index("# ETAPA 3 · Clusterização")]
check("a etapa recebe o intervalo de amostragem",
      "intervalo_s: float = DEFAULT_INTERVALO_AMOSTRAGEM_S" in corpo_vlm)
check("e o processamento o passa adiante",
      "intervalo_s=intervalo_amostragem_s" in src)
check("posto_vazio continua determinístico e sem VLM",
      '"origem_gate": "posto_vazio"' in corpo_vlm)
check("cada amostra do grupo ainda emite a sua observação",
      'for i, (tipo, am) in enumerate(plano):' in corpo_vlm)

print("\n[10] versao_instrumento: a quebra da série fica DENTRO do dado")
check("a constante existe e foi bumpada (a medição mudou de novo)",
      pl.VERSAO_INSTRUMENTO >= 4, pl.VERSAO_INSTRUMENTO)
# Escopo na função de PERSISTÊNCIA: contar no arquivo inteiro dava falso
# positivo assim que outra função passou a expor a versão (o diagnóstico de
# sinais). O que importa é que os DOIS payloads de evento — principais e crus —
# levem o carimbo, e nenhum deles fique para trás numa refatoração.
_persist = src.split("def gravar_eventos")[1] if "def gravar_eventos" in src else src
_persist = _persist[:_persist.index("CHUNK = 100")] if "CHUNK = 100" in _persist else _persist
check("é carimbada nas DUAS linhas de evento (principais e crus)",
      _persist.count('"versao_instrumento": VERSAO_INSTRUMENTO') == 2,
      _persist.count('"versao_instrumento": VERSAO_INSTRUMENTO'))
sql = open("sql/schema.sql").read()
check("a coluna existe com default 1 (todo evento antigo foi medido com o "
      "instrumento antigo — essa é a afirmação verdadeira sobre eles)",
      "add column if not exists versao_instrumento int default 1" in sql)
check("a análise diária lê a versão",
      "versao_instrumento" in src[src.index("def montar_analise_diaria("):][:4000])
check("e devolve as versões do dia", '"versoes_instrumento": sorted(d["versoes"])' in src)

print("\n[11] Rótulo sem categoria: a tela que separa contabilidade de medição")
check("a função existe e é só leitura", callable(pl.rotulos_sem_categoria))
i = src.index("def rotulos_sem_categoria(")
corpo_rot = src[i:src.index("def relatorio_propagacao_lean(")]
check("ordena por TEMPO, não por contagem de eventos",
      "key=lambda x: -x[\"segundos\"]" in corpo_rot)
check("e o comentário explica por quê (4 eventos de 15min > 300 de 8s)",
      "4 eventos de 15 min pesam" in corpo_rot)
check("usa o mesmo filtro de toda métrica (auditoria e descartados fora)",
      'e.get("principal") is False or e.get("validacao_correto") is False' in corpo_rot)
check("distingue NUNCA classificado de ASSUMIDO pelo fallback",
      "categoria_tem_evidencia" in corpo_rot and '"origem_atual"' in corpo_rot)
check("o docstring nomeia o risco: queda por CONTABILIDADE antes de medição",
      "CONTABILIDADE antes de cair por MEDIÇÃO" in corpo_rot)

mn = open("backend/main.py").read()
check("o endpoint é GET (não muda nada)",
      '@app.get("/processos/{processo_id}/rotulos/sem-categoria")' in mn)
check("e aponta para onde se classifica de verdade",
      "PUT /processos/{id}/comportamentos/categoria" in mn)

print("\n[12] A tela existe e é alcançável")
import re as _re  # noqa: E402
from pathlib import Path  # noqa: E402


def _visivel(txt):
    txt = _re.sub(r"\{/\*.*?\*/\}", " ", txt, flags=_re.S)
    txt = _re.sub(r"/\*.*?\*/", " ", txt, flags=_re.S)
    txt = _re.sub(r"^\s*//.*$", " ", txt, flags=_re.M)
    return _re.sub(r"\s+", " ", txt)


shell = Path("frontend/src/design/Shell.tsx").read_text()
app = Path("frontend/src/App.tsx").read_text()
tela = Path("frontend/src/pages/Rotulos.tsx").read_text()
apits = Path("frontend/src/lib/api.ts").read_text()
d2 = Path("frontend/src/pages/Dashboard2.tsx").read_text()

check("a aba existe no tipo Tab",
      _re.search(r"export type Tab =[^;]*\"rotulos\"", shell) is not None)
# LIMPEZA DA VITRINE: "Classificar rótulos" saiu do MENU. Ela nasceu quando o
# RÓTULO decidia o número; desde a Fase 101 o número vem da permanência e
# nenhum rótulo decide, então classificar rótulo virou trabalho sem
# consequência. A tela e a rota continuam — o que saiu foi o convite.
check("saiu do menu (o rótulo não decide mais o número)",
      _re.search(r"\{\s*tab:\s*\"rotulos\"", shell) is None)
check("o roteador leva até a tela",
      'route.tab === "rotulos"' in app and "<Rotulos" in app)
check("a tela chama o endpoint", "api.rotulos.semCategoria" in tela)
check("e classifica pelo RÓTULO (funciona sem linha em comportamentos)",
      "setCategoriaPorLabel" in tela)
check("o cliente tem o método", "semCategoria:" in apits)
check("a tela invalida o dashboard depois de classificar (senão o gestor "
      "decide e continua vendo o número velho)",
      'queryKey: ["dashboard", proc.id]' in tela and 'queryKey: ["diaadia", proc.id]' in tela)
check("o texto diz que sem categoria conta como não-produtivo",
      "NÃO-PRODUTIVO" in _visivel(tela))
check("e manda classificar de cima para baixo",
      "o topo da lista é o que mais move o número" in _visivel(tela))
check("o dia da mudança de instrumento é marcado no gráfico",
      "versoes_instrumento" in d2 and "a medição mudou aqui" in _visivel(d2))


print("\n[13] COMPORTAMENTO do laço — não só o texto dos prompts")
# Dublê do VLM: devolve descrições por instante sem rede. O que interessa aqui
# é o CAMINHO — quantas chamadas, o que é herdado, o que é forçado.
chamadas = {"seq": 0, "cam2": 0, "binario": 0}
_seq_real, _cam2_real, _bin_real = (pl._analisar_sequencia_vlm,
                                    pl._analisar_sequencia_cam2, pl._gate_vlm_binario)


# Fase 86: o dublê acompanha o contrato REAL — a função devolve o bloco com
# `acoes` + o discriminador da cena, não um dict solto de descrições. Um dublê
# desatualizado passaria verde enquanto a produção quebra.
def _fake_seq(cli, grupo, *a, **k):
    chamadas["seq"] += 1
    return {i: {"acoes": {p["track_id"]: f"acao do instante {int(am_.tempo_s)}"
                          for p in am_.pessoas},
                "maquina": "ciclo", "imovel": False}
            for i, am_ in enumerate(grupo)}


def _fake_cam2(cli, grupo, *a, **k):
    chamadas["cam2"] += 1
    return {i: {"acao": "operador atras da maquina", "maquina": None,
                "imovel": True} for i in range(len(grupo))}


pl._analisar_sequencia_vlm = _fake_seq
pl._analisar_sequencia_cam2 = _fake_cam2
pl._gate_vlm_binario = lambda *a, **k: (chamadas.__setitem__("binario", chamadas["binario"] + 1), True)[1]
_nada = lambda *a, **k: None   # noqa: E731

try:
    # 8 amostras num minuto → UMA chamada, oito observações.
    grupo = [am(t, [pessoa()]) for t in (0, 8, 16, 24, 32, 40, 48, 56)]
    obs = pl.etapa_analise_vlm(None, grupo, "torno", {}, _nada, zona_posto="posto",
                               intervalo_s=8.0)
    check("um minuto = UMA chamada de sequência", chamadas["seq"] == 1, chamadas)
    check("mas OITO observações — o downstream continua por instante",
          len(obs) == 8, len(obs))
    check("cada observação tem o seu tempo",
          [o["tempo_s"] for o in obs] == [0, 8, 16, 24, 32, 40, 48, 56])
    check("e a descrição é a DAQUELE instante, não a do minuto",
          obs[0]["descricao"] != obs[3]["descricao"], (obs[0], obs[3]))

    # Dois minutos → duas chamadas.
    chamadas["seq"] = 0
    dois = [am(t, [pessoa()]) for t in (0, 8, 16, 64, 72, 80)]
    obs2 = pl.etapa_analise_vlm(None, dois, "torno", {}, _nada, zona_posto="posto",
                               intervalo_s=8.0)
    check("dois minutos = duas chamadas", chamadas["seq"] == 2, chamadas)
    check("e seis observações", len(obs2) == 6, len(obs2))

    # Posto vazio segue sem VLM nenhum.
    chamadas["seq"] = chamadas["cam2"] = 0
    vazios = [am(t, [], presente=False) for t in (0, 8, 16)]
    obsv = pl.etapa_analise_vlm(None, vazios, "torno", {}, _nada, zona_posto="posto",
                               intervalo_s=8.0)
    check("posto vazio não gasta VLM", chamadas["seq"] == 0 and chamadas["cam2"] == 0)
    check("e ainda assim vira observação (o tempo é preservado)",
          len(obsv) == 3 and all(o["papel"] == "posto_vazio" for o in obsv))

    # RESGATE: cam1 sem operador, cam2 vê → uma chamada de sequência da cam2.
    chamadas["seq"] = chamadas["cam2"] = 0
    resg = [am(t, [], sec=f"SEC{int(t)}", presente=True) for t in (0, 8, 16)]
    obsr = pl.etapa_analise_vlm(None, resg, "torno", {}, _nada, zona_posto="posto",
                                intervalo_s=8.0)
    check("o resgate também é UMA chamada para o grupo", chamadas["cam2"] == 1, chamadas)
    check("com uma observação por instante", len(obsr) == 3, len(obsr))
    check("e a caixa vem da cam2 (Fase 82)",
          all(o["bbox_cam"] == "cam2" for o in obsr))

    # PONTE: pode preservar cobertura textual, nunca identidade/produtividade.
    chamadas["cam2"] = 0
    ponte = [am(0, [], sec="S0", presente=True)] + [
        am(t, [], presente=True, ponte=True) for t in (8, 16, 24, 32, 40)]
    obsp = pl.etapa_analise_vlm(None, ponte, "torno", {}, _nada, zona_posto="posto",
                                intervalo_s=8.0)
    check("a ponte preserva as leituras sem inventar ausência",
          len(obsp) == len(ponte), len(obsp))
    check("ponte e falha nunca herdam identidade ou produtividade",
          all(o.get("papel") is None and o.get("trabalho") is None for o in obsp),
          obsp)
finally:
    pl._analisar_sequencia_vlm, pl._analisar_sequencia_cam2, pl._gate_vlm_binario = (
        _seq_real, _cam2_real, _bin_real)


print("\n[14] rotulos_sem_categoria EXECUTADA — não só lida")
# O teste que faltava. A suíte anterior conferia o TEXTO da função e passou
# limpa enquanto a função levantava KeyError na primeira linha com dado real:
# `itens` são as mesmas referências que estão em `agg`, então limpar a chave
# `segundos` nos itens apagava a chave usada logo depois para somar o total.
# Fonte só prova que o código existe; execução prova que ele roda.


class QFake:
    def __init__(self, sb, tabela):
        self.sb, self.tabela, self.eqs, self.rng = sb, tabela, {}, None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def range(self, i, f): self.rng = (i, f); return self

    def execute(self):
        linhas = [l for l in self.sb.dados.get(self.tabela, [])
                  if all(l.get(c) == v for c, v in self.eqs.items())]
        if self.rng is not None:
            linhas = linhas[self.rng[0]: self.rng[1] + 1]
        return types.SimpleNamespace(data=[dict(l) for l in linhas])


class SBFake:
    def __init__(self, dados): self.dados = dados
    def table(self, nome): return QFake(self, nome)


def ev(i, label, ini, fim, **kw):
    base = {"id": f"e{i}", "empresa": "U", "processo": "T",
            "comportamento_label": label, "label_corrigido": None,
            "descricao_bruta": f"descricao de {label}",
            "tempo_inicio_s": ini, "tempo_fim_s": fim, "principal": True,
            "validacao_correto": None, "video_id": "v1",
            "criado_em": None, "versao_instrumento": 2}
    base.update(kw)
    return base


SB = SBFake({
    "eventos": [
        # 900s num rótulo sem categoria — é o que deve encabeçar a lista.
        ev(1, "parado_sem_atividade", 0, 900),
        # 300 eventos curtos noutro rótulo sem categoria: 300 x 8s = 2400s.
        # (contagem maior, mas ainda assim tempo maior — o teste do critério
        #  de ordenação vem no bloco seguinte)
        *[ev(100 + k, "conversando_colega", k * 8, k * 8 + 8) for k in range(30)],
        # rótulo COM categoria decidida: não entra
        ev(2, "operar_torno", 0, 600),
        # crus de auditoria e descartados: fora, como em toda métrica
        ev(3, "parado_sem_atividade", 0, 600, principal=False),
        ev(4, "parado_sem_atividade", 0, 600, validacao_correto=False),
    ],
    "comportamentos": [
        {"id": "c1", "empresa": "U", "processo": "T", "label": "operar_torno",
         "descricao": "opera", "categoria_lean": "valor_agregado",
         "categoria_lean_origem": "humano", "total_ocorrencias": 10},
        {"id": "c2", "empresa": "U", "processo": "T", "label": "parado_sem_atividade",
         "descricao": None, "categoria_lean": None,
         "categoria_lean_origem": None, "total_ocorrencias": 1},
        # ASSUMIDO pelo fallback: tem categoria, mas ninguém decidiu.
        {"id": "c3", "empresa": "U", "processo": "T", "label": "conversando_colega",
         "descricao": "conversa", "categoria_lean": "desperdicio",
         "categoria_lean_origem": "fallback", "total_ocorrencias": 30},
    ],
})

r = pl.rotulos_sem_categoria(SB, "U", "T")
check("a função RODA e não levanta", "erro" not in r, r.get("erro"))
labels = [i["label"] for i in r["itens"]]
check("só rótulos sem categoria DECIDIDA entram",
      set(labels) == {"parado_sem_atividade", "conversando_colega"}, labels)
check("operar_torno (categoria humana) fica de fora", "operar_torno" not in labels)
check("ordena por TEMPO, não por contagem: parado tem 1 evento de 900s e "
      "encabeça; conversando tem 30 eventos de 8s (240s) e vem depois",
      labels == ["parado_sem_atividade", "conversando_colega"], labels)
top = r["itens"][0]
check("minutos do topo = 900s/60", top["minutos"] == 15.0, top)
check("auditoria e descartado NÃO entram na conta (senão seriam 2100s)",
      top["n_eventos"] == 1, top)
check("o assumido pelo fallback aparece marcado como tal",
      any(i["origem_atual"] == "fallback" for i in r["itens"]))
check("e o nunca classificado vem sem categoria nenhuma",
      top["categoria_atual"] is None, top)
check("traz exemplos de descrição para o gestor reconhecer o rótulo",
      len(top["exemplos"]) >= 1, top)
check("o total sem categoria é a soma dos dois (900 + 240 = 1140s = 19min)",
      r["minutos_sem_categoria"] == 19.0, r["minutos_sem_categoria"])
check("e o percentual usa o tempo observado TOTAL (com operar_torno dentro)",
      0 < r["pct_sem_categoria"] < 100, r["pct_sem_categoria"])
check("o limite corta a lista", len(pl.rotulos_sem_categoria(SB, "U", "T", limite=1)["itens"]) == 1)

vazio = pl.rotulos_sem_categoria(SBFake({"eventos": [], "comportamentos": []}), "U", "T")
check("processo sem eventos não quebra",
      vazio["itens"] == [] and vazio["pct_sem_categoria"] == 0.0, vazio)


# ── Fase 88 ────────────────────────────────────────────────────────────
# A partição foi DESLIGADA por padrão: medimos o discriminador contra o
# próprio dado e ele não mede (em minutos adjacentes com a mesma ação o
# estado troca como moeda). Os blocos [15] e [16] continuam existindo porque
# a máquina da partição continua existindo — atrás de flag, para voltar
# testada no dia em que houver um discriminador de verdade (o movimento a
# 6 fps). Eles rodam com a flag LIGADA à força; o default está no bloco [15b].
_PART_REAL = pl._PARTICAO_CENA
pl._PARTICAO_CENA = True

print("\n[15] Fase 86 · O DISCRIMINADOR PARTICIONA — colapso impossível, não desencorajado")
check("chave de cena separa ciclo de parada",
      pl.chave_cena("ciclo", False) != pl.chave_cena("parada", False))
check("valor inválido vira desconhecido (nunca vira discriminador)",
      pl.chave_cena("girando", False) == pl.chave_cena(None, False))
check("sufixo mecânico para máquina em ciclo", pl.sufixo_cena("ciclo", False) == "_ciclo")
check("e para máquina parada", pl.sufixo_cena("parada", True) == "_parada")
check("imobilidade só vira sufixo quando a máquina é DESCONHECIDA "
      "(com a máquina conhecida seria redundante e dobraria o vocabulário)",
      pl.sufixo_cena(None, True) == "_imovel" and pl.sufixo_cena("ciclo", True) == "_ciclo")
check("sem discriminador não há sufixo — desconhecido é resposta legítima",
      pl.sufixo_cena(None, False) == "")
check("o sufixo NÃO batiza o Lean (nada de esperar_ciclo/ocioso)",
      "ocioso" not in pl.sufixo_cena("parada", True)
      and "esperar" not in pl.sufixo_cena("ciclo", False))
check("família desfaz o sufixo",
      pl.familia_label("monitorar_maquina_ciclo") == "monitorar_maquina"
      and pl.familia_label("monitorar_maquina_parada") == "monitorar_maquina"
      and pl.familia_label("monitorar_maquina") == "monitorar_maquina")

print("\n[16] O cluster com partição — executado")
chamou = []


def _fake_cluster(cli, prompt, **k):
    chamou.append(prompt)
    # A LLM devolve o MESMO label nas duas partições — é o caso que quebrava.
    linhas = [l[2:] for l in prompt.splitlines() if l.startswith("- ")]
    import json as _j
    return _j.dumps({"comportamentos": [
        {"label": "monitorar_maquina", "descricao": "operador junto ao torno",
         "descricoes_originais": linhas}]})


_txt_real = pl.groq_text_call
pl.groq_text_call = _fake_cluster
try:
    obs = [
        {"descricao": "parado junto ao torno, observando", "maquina": "ciclo",
         "imovel": True, "track_id": 1, "tempo_s": 0, "frame_idx": 0},
        {"descricao": "parado junto ao torno, observando", "maquina": "parada",
         "imovel": True, "track_id": 1, "tempo_s": 8, "frame_idx": 80},
    ]
    _, catalogo, label_de, _ = pl.etapa_clusterizar(
        None, obs, "torno", {}, 3, lambda *a, **k: None, aprendizado_auto=False)
    check("uma chamada POR PARTIÇÃO, não uma para tudo", len(chamou) == 2, len(chamou))
    l_ciclo = label_de("parado junto ao torno, observando", "ciclo", True)
    l_parada = label_de("parado junto ao torno, observando", "parada", True)
    # ⚠️ Fase 99 — A PARTIÇÃO FICOU SEM DENTE, e isto é deliberado.
    # A guarda que remove sufixo de estado roda DEPOIS do cluster e ANTES de o
    # nome virar rótulo — INCLUSIVE com KV_PARTICAO_CENA ligado. Ou seja: mesmo
    # reativando a partição, ela separa as chamadas sem conseguir NOMEAR a
    # separação, o que a torna inútil na prática.
    #
    # É a escolha certa: "impossível" tem de ser impossível, não "improvável se
    # ninguém religar a flag". Um rótulo só pode afirmar o que algum sinal mede,
    # e nada mede estado de máquina hoje.
    check("mesmo com a partição LIGADA, o rótulo não carrega estado",
          not pl.rotulo_afirma_estado(l_ciclo)
          and not pl.rotulo_afirma_estado(l_parada), (l_ciclo, l_parada))
    check("as duas cenas colapsam no mesmo nome (a partição perdeu o dente)",
          l_ciclo == l_parada == "monitorar_maquina", (l_ciclo, l_parada))
    # O catálogo é o texto que o gestor lê na tela. Um rótulo que agora cobre
    # ciclo E parada não pode ser descrito como um dos dois — era o que
    # acontecia, decidido por ordem de iteração das partições.
    check("e o catálogo não descreve estado nenhum",
          not any("ciclo" in (d or "").lower() or "parada" in (d or "").lower()
                  for d in catalogo.values()), catalogo)
    check("temperatura ZERO no cluster", "temperatura=0.0" in src)

    # Cache — Fase 99. O histórico está cheio de `x_ciclo`; a leitura limpa o
    # sufixo. Antes, o label limpo nunca batia com uma cena discriminada e o
    # cache NUNCA acertava: o sistema pagava uma chamada por frase já conhecida
    # para receber um nome que a guarda ia limpar de novo.
    chamou.clear()
    _, _, label_de2, _ = pl.etapa_clusterizar(
        None, obs, "torno", {}, 3, lambda *a, **k: None, aprendizado_auto=False,
        cache_labels={"parado junto ao torno, observando": "monitorar_maquina_ciclo"})
    check("o cache atende — e não paga chamada nenhuma por frase conhecida",
          len(chamou) == 0, len(chamou))
    check("e devolve o label do histórico JÁ SEM o estado",
          label_de2("parado junto ao torno, observando", "ciclo", True)
          == "monitorar_maquina")
    check("as duas cenas recebem o MESMO label — não há mais partição a "
          "preservar, então não há mais o que o cache desfaça",
          label_de2("parado junto ao torno, observando", "parada", True)
          == label_de2("parado junto ao torno, observando", "ciclo", True)
          == "monitorar_maquina")
finally:
    pl.groq_text_call = _txt_real

pl._PARTICAO_CENA = _PART_REAL

print("\n[15b] Fase 88 · e o PADRÃO é desligado — o rótulo parou de afirmar")
check("por padrão a partição está OFF", pl._PARTICAO_CENA is False)
check("uma partição só", pl.chave_cena("ciclo", False) == pl.chave_cena("parada", True))
check("nenhum sufixo é colado no rótulo",
      pl.sufixo_cena("ciclo", False) == pl.sufixo_cena("parada", True)
      == pl.sufixo_cena(None, True) == "")
check("o estado passa a viver em coluna, não no nome",
      '"cena_maquina": _normalizar_maquina' in src and '"cena_imovel"' in src)
check("e o schema declara as colunas",
      "add column if not exists cena_maquina" in sql
      and "add column if not exists cena_imovel" in sql)
check("família descasca sufixo DUPLO (o histórico da Fase 86 tem)",
      pl.familia_label("monitorar_maquina_parada_ciclo") == "monitorar_maquina")

print("\n[17] Fase 86 · ORIENTAÇÃO vem da pose, não do olho do modelo")
W2, H2 = 640, 480


def _kp(pontos):
    k = [[0.0, 0.0] for _ in range(17)]
    for i, (x, y) in pontos.items():
        k[i] = [x, y]
    return k


# Tronco de referência: ombros em y=0.45, quadris em y=0.70 → 120px num
# frame de 480. De frente, ombros ~77px (0.64 do tronco); de perfil, ~6px.
_QUADRIS = {11: (0.47, 0.70), 12: (0.53, 0.70)}
de_frente = {"kpts": _kp({0: (0.5, 0.35), 1: (0.48, 0.34), 2: (0.52, 0.34),
                          5: (0.56, 0.45), 6: (0.44, 0.45), **_QUADRIS})}
de_costas = {"kpts": _kp({5: (0.44, 0.45), 6: (0.56, 0.45), **_QUADRIS})}
de_perfil = {"kpts": _kp({0: (0.5, 0.35), 5: (0.500, 0.45), 6: (0.505, 0.45),
                          **_QUADRIS})}
check("rosto visível = de frente para a câmera",
      pl.orientacao_pessoa(de_frente, W2, H2) == "frente")
check("ombros sem rosto nenhum = de costas",
      pl.orientacao_pessoa(de_costas, W2, H2) == "costas")
check("ombros colados no eixo x = de perfil, MESMO com o rosto visível "
      "(quem está de lado mostra meio rosto)",
      pl.orientacao_pessoa(de_perfil, W2, H2) == "perfil")
check("sem quadril visível, a altura da caixa serve de referência de escala",
      pl.orientacao_pessoa(
          {"kpts": _kp({5: (0.500, 0.45), 6: (0.505, 0.45)}),
           "bbox": (100, 60, 160, 400)}, W2, H2) == "perfil")
check("sem pose não inventa orientação",
      pl.orientacao_pessoa({"kpts": None}, W2, H2) is None)

check("sem configuração da zona, NÃO traduz para a máquina",
      pl.orientacao_vs_maquina("costas", None) is None)
check("com 'oposta', de costas para a câmera = de frente para a máquina",
      pl.orientacao_vs_maquina("costas", "oposta") == "de frente para a máquina")
check("com 'camera', de costas para a câmera = de costas para a máquina",
      pl.orientacao_vs_maquina("costas", "camera") == "de costas para a máquina")
check("com 'perfil' o eixo é perpendicular e não dá para inferir",
      pl.orientacao_vs_maquina("frente", "perfil") is None)

check("o prompt PROÍBE afirmar orientação sem o contexto",
      "ORIENTAÇÃO NÃO SE ADIVINHA" in regras
      and "de frente ao torno" in regras)
check("a orientação é injetada como fato do sensor, junto com as mãos",
      'medido pela pose, não é opinião' in src)
check("e a detecção calcula por pessoa", 'pessoa["orientacao"] = orientacao_pessoa' in src)

sql = open("sql/schema.sql").read()
check("o campo frente_maquina existe na zona",
      "alter table zonas_camera add column if not exists frente_maquina" in sql)
check("com constraint dos valores válidos", "zonas_frente_maquina_chk" in sql)
mn2 = open("backend/main.py").read()
check("a API recusa frente_maquina fora da zona 'maquina'",
      "só se aplica à zona de papel 'maquina'" in mn2)
zonas_tsx = open("frontend/src/pages/ConfiguracoesZonas.tsx").read()
check("a tela de zonas tem o seletor", "Onde está a máquina em relação a esta câmera?" in zonas_tsx)
check("e explica que sem preencher o sistema não afirma nada sobre o torno",
      "não afirma" in re.sub(r"\s+", " ", zonas_tsx))

print("\n[18] Fase 86 · A FAMÍLIA preserva a leitura de tendência")
check("o endpoint devolve a raiz da família", '"familia": familia_label(lbl)' in src)
check("e o retrato da família inteira, com as variantes já classificadas",
      '"familia_variantes"' in src and '"familia_minutos"' in src)
rot = open("frontend/src/pages/Rotulos.tsx").read()
# Fase 101: o "min no total" saiu — duração absoluta não vai a superfície do
# cliente (captura amostrada a ~50% de cada hora). A família continua na tela,
# que é o que este bloco protege.
check("a tela mostra a família com as variantes",
      "familia_variantes" in rot and "família" in rot)
check("e explica o histórico sem renomear nada",
      "o instrumento não coletava o estado da máquina" in rot)
check("distinguindo 'não perguntávamos' de 'perguntamos e não deu para ver'",
      "o VLM não conseguiu ver o estado da máquina" in rot)

print(f"\n{'=' * 60}\n  {ok} ok · {fail} falha(s)\n{'=' * 60}")
sys.exit(1 if fail else 0)
