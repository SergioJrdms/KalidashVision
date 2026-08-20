"""Fase 103 — A ZONA DO POSTO É LEI.

O relato: "o sistema está considerando pessoas fora do posto". A correção do
usuário foi cirúrgica: *"o ponto não é que a zona está larga demais, ela não
está. O ponto é que a zona NÃO ESTAVA SENDO RESPEITADA!!!! Só devemos analisar
quem está dentro da zona de posto e ponto final"*.

Havia DUAS frouxidões somadas — nenhuma delas no desenho do polígono:

  1. QUALQUER UM DOS 17 KEYPOINTS contava. A semântica escrita era "um pé ou um
     braço dentro da zona já conta". Então quem estava AO LADO do posto, de
     braço estendido, encostava na borda do polígono e virava o operador. A
     regra nasceu para sobreviver à oclusão pelo torno — mas a ÂNCORA já
     resolve oclusão sem abrir mão da localização: ela diz ONDE A PESSOA ESTÁ,
     não até onde ela ALCANÇA.

  2. A ZONA `interacao` CLASSIFICAVA PESSOA. Quem passava por ali virava
     `visitante`, gerava evento, descrição e card de validação — sem nunca ter
     estado no posto. O gestor via na fila gente que não é do posto dele.

Esta suíte trava as duas: âncora-só, `posto_operador`-só, e o DESCARTE
acontecendo ANTES de a pessoa virar evento/descrição/card.

⚠️ Não é um afrouxamento com nome novo: `KV_ZONA_ESTRITA=off` existe só para
comparar o número dos dois jeitos no mesmo dia. O PADRÃO é estrito.

Rodar:  python tests_zona_e_lei.py
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
sys.modules["numpy"].array = lambda x, **k: list(x)
sys.modules["numpy"].int32 = int


def _dentro(poly, pt, _medida):
    """Ray casting — o cv2 aqui é um stub, e o teste é justamente sobre
    ESTAR DENTRO. Retorna >=0 quando o ponto está dentro/na borda."""
    x, y = pt
    n, dentro = len(poly), False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xc = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xc:
                dentro = not dentro
    return 1.0 if dentro else -1.0


sys.modules["cv2"].pointPolygonTest = _dentro
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

from backend import pipeline as pl  # noqa: E402

ok = fail = 0
AQUI = os.path.dirname(os.path.abspath(__file__))


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


W = H = 1000

# Posto = quadrado à ESQUERDA. Interação = quadrado à DIREITA, sem encostar.
ROIS = {
    "posto_torno": {"papel": "posto_operador",
                    "polygon": [[100, 100], [400, 100], [400, 600], [100, 600]],
                    "descricao_contexto": "bancada do torno"},
    "corredor": {"papel": "interacao",
                 "polygon": [[700, 100], [950, 100], [950, 600], [700, 600]],
                 "descricao_contexto": "corredor"},
    "torno": {"papel": "maquina",
              "polygon": [[420, 100], [560, 100], [560, 600], [420, 600]]},
}


def pessoa(ombro_x, ombro_y, punho_x=None, punho_y=None, bbox=None):
    """Pessoa com ombros em (ombro_x, ombro_y) px e, opcionalmente, UM PUNHO
    esticado para (punho_x, punho_y) px. Coordenadas de kpt são normalizadas."""
    kpts = [[0.0, 0.0] for _ in range(17)]
    kpts[5] = [(ombro_x - 15) / W, ombro_y / H]     # ombro esq
    kpts[6] = [(ombro_x + 15) / W, ombro_y / H]     # ombro dir
    if punho_x is not None:
        kpts[10] = [punho_x / W, punho_y / H]       # punho dir esticado
    bb = bbox or (ombro_x - 60, ombro_y - 80, ombro_x + 60, ombro_y + 300)
    return {"track_id": 1, "bbox": bb, "kpts": kpts,
            "centro": ((bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2)}


def zona(p, estrito=True):
    antes = pl._ZONA_ESTRITA
    pl._ZONA_ESTRITA = estrito
    try:
        return pl._zona_da_pessoa(pl._pontos_da_pessoa(p, W, H), ROIS,
                                  ancora=pl._ponto_ancora(p, W, H))
    finally:
        pl._ZONA_ESTRITA = antes


# ═══════════════ [1] O CASO QUE MOTIVOU: o braço estendido ═══════════════
print("\n[1] O braço estendido — a pessoa AO LADO do posto")

# Corpo fora (x=650), punho esticado para dentro do polígono do posto (x=380).
fora_com_braco = pessoa(650, 300, punho_x=380, punho_y=300)
check("ANTES (frouxo): o punho sozinho a colocava NO POSTO",
      zona(fora_com_braco, estrito=False)[1] == "posto_operador",
      zona(fora_com_braco, estrito=False))
check("AGORA: o corpo está fora → não é do posto",
      zona(fora_com_braco)[1] is None, zona(fora_com_braco))

# E o inverso: o operador de verdade, DENTRO, com o braço para fora.
dentro_com_braco = pessoa(250, 300, punho_x=800, punho_y=300)
check("o operador DENTRO, mesmo esticando o braço para fora, continua no posto",
      zona(dentro_com_braco)[1] == "posto_operador", zona(dentro_com_braco))
check("e leva o nome e o contexto da zona",
      zona(dentro_com_braco)[0] == "posto_torno"
      and zona(dentro_com_braco)[2] == "bancada do torno")


# ═══════════════ [2] A oclusão pelo torno continua resolvida ═════════════
print("\n[2] Oclusão pelo torno — o motivo pelo qual a frouxidão existia")

# Operador atrás do torno: pernas invisíveis (kpts 11-16 zerados), só tronco.
ocluso = pessoa(250, 300)
check("sem NENHUM keypoint inferior válido, ainda é reconhecido no posto",
      zona(ocluso)[1] == "posto_operador", zona(ocluso))
check("e a telemetria confirma que ele está ocluso",
      pl._fracao_inferior_visivel(ocluso["kpts"]) == 0.0)

# Só UM ombro visível (o outro atrás da máquina).
um_ombro = pessoa(250, 300)
um_ombro["kpts"][5] = [0.0, 0.0]
check("com só UM ombro visível, a âncora ainda acha o posto",
      zona(um_ombro)[1] == "posto_operador", zona(um_ombro))

# Só o nariz.
so_nariz = pessoa(250, 300)
so_nariz["kpts"][5] = [0.0, 0.0]
so_nariz["kpts"][6] = [0.0, 0.0]
so_nariz["kpts"][0] = [250 / W, 250 / H]
check("com só o NARIZ, idem (âncora desce ~pescoço)",
      zona(so_nariz)[1] == "posto_operador", zona(so_nariz))

# Sem pose alguma → topo do tronco do bbox.
sem_pose = {"track_id": 3, "bbox": (200, 150, 320, 560), "centro": (260, 355)}
check("SEM POSE, a âncora cai no topo-do-tronco do bbox e ainda funciona",
      zona(sem_pose)[1] == "posto_operador", zona(sem_pose))


# ═══════════════ [3] A zona `interacao` não classifica mais ══════════════
print("\n[3] A zona `interacao` parou de fabricar visitante")

no_corredor = pessoa(830, 300)
check("ANTES (frouxo): virava `interacao` → visitante → evento → card",
      zona(no_corredor, estrito=False)[1] == "interacao",
      zona(no_corredor, estrito=False))
check("AGORA: quem está no corredor não é classificado",
      zona(no_corredor) == (None, None, None), zona(no_corredor))

em_cima_da_maquina = pessoa(490, 300)
check("a zona `maquina` nunca classificou pessoa, e continua sem classificar",
      zona(em_cima_da_maquina) == (None, None, None))
check("(nem no modo frouxo)", zona(em_cima_da_maquina, estrito=False)
      == (None, None, None))
check("mas o punho na máquina continua sendo sinal de 'mãos no torno'",
      pl._maos_na_maquina(pessoa(250, 300, punho_x=490, punho_y=300),
                          ROIS, W, H) is True)


# ═══════════════ [4] O descarte acontece ANTES de virar evento ═══════════
print("\n[4] Onde o descarte acontece — antes de virar pessoa/evento/card")

src = open(os.path.join(AQUI, "backend", "pipeline.py"), encoding="utf-8").read()
codigo = "\n".join(l for l in src.splitlines()
                   if not l.lstrip().startswith("#"))

# Fase 110 — o ramo ganhou a retenção em lista PARALELA (`fora_frame`), mas o
# contrato desta suíte não mudou nem um milímetro: quem está fora do polígono
# NUNCA entra em `pessoas`. O `continue` continua fechando o ramo, e nada entre
# o `if` e ele toca `pessoas`.
_ramo = codigo.split('if papel_z != "posto_operador":')[1].split("pessoa[\"zona\"]")[0]
check("o loop de detecção descarta quem não é do posto com `continue`",
      'if papel_z != "posto_operador":' in codigo
      and _ramo.rstrip().endswith("continue"))
check("⭐ e NADA no ramo do descarte encosta em `pessoas`",
      "pessoas.append" not in _ramo and "pessoas[" not in _ramo, _ramo)
check("⭐ o que ele guarda vai para uma lista SEPARADA",
      "fora_frame.append(pessoa)" in _ramo)
check("com o motivo escrito: guardar não é admitir",
      "Guardar não\n                            # é admitir" in src)
check("o descarte vem ANTES de gravar zona/orientação na pessoa",
      codigo.index('if papel_z != "posto_operador":')
      < codigo.index('pessoa["zona"] = nome_z'))
check("e ANTES de a pessoa entrar na lista que vira amostra/VLM",
      codigo.index('if papel_z != "posto_operador":')
      < codigo.index("pessoas.append(pessoa)"))
check("a âncora é passada explicitamente no chamador",
      "ancora=_ponto_ancora(pessoa, w, h)" in codigo)


# ═══════════════ [4b] A LATERAL obedece à mesma lei ══════════════════════
print("\n[4b] A cam2 tinha a mesma frouxidão — e ela alimenta o número")

bloco = codigo[codigo.index("n_posto2 = 0"):codigo.index("am.n_posto_cam2 = n_posto2")]
check("a cam2 usa a ÂNCORA quando a zona é estrita",
      "_ponto_ancora(pessoa2, w2, h2)" in bloco)
check("e o teste de 17 keypoints só sobrevive no ramo FROUXO",
      "_pontos_da_pessoa(pessoa2" in bloco
      and bloco.index("if _ZONA_ESTRITA:") < bloco.index("_pontos_da_pessoa(pessoa2"))
check("a mesma chave governa as duas câmeras (não há dois regimes)",
      bloco.count("_ZONA_ESTRITA") == 1 and "_ZONA_ESTRITA" in codigo)
check("por que importa: n_posto_cam2 vira pessoas_no_posto (máx das câmeras)",
      "n_posto_cam2" in codigo and "A ZONA DO POSTO É LEI, também na lateral" in src)


# ═══════════════ [5] O estado de permanência obedece a mesma lei ═════════
print("\n[5] Permanência: só conta quem está no posto")

check("visitante (segunda pessoa no posto) não vira permanência do titular",
      pl.estado_permanencia({"papel_pessoa": "visitante"}, "esquerda")[0]
      == pl.EST_FORA)
check("papel NULO (sem eleição) vira INCONCLUSIVO, nunca presença",
      pl.estado_permanencia({"papel_pessoa": None}, "esquerda")[0]
      == pl.EST_INCONCLUSIVO)
check("e o operador de verdade entra na conta",
      pl.estado_permanencia({"papel_pessoa": "operador"}, "esquerda")[0]
      in (pl.EST_NO_TORNO, pl.EST_OUTRO_LADO))
check("a guarda de papel em estado_permanencia é INCONDICIONAL (sem flag)",
      'if papel != "operador":' in codigo
      and "REGRA PRIMÁRIA: SÓ CONTA QUEM ESTÁ NO POSTO" in src)


# ═══════════════ [6] A saída de emergência é honesta ═════════════════════
print("\n[6] `KV_ZONA_ESTRITA` — comparar, não afrouxar de volta")

check("o padrão é ESTRITO (sem variável de ambiente, a zona é lei)",
      pl._ZONA_ESTRITA is True)
check('a variável se chama KV_ZONA_ESTRITA', "KV_ZONA_ESTRITA" in src)
check("`off` é o único caminho de volta, e está documentado como COMPARAÇÃO",
      'os.environ.get("KV_ZONA_ESTRITA", "on")' in codigo
      and "comparar o número" in src)
check("o motivo do desenho está escrito no código, não só aqui",
      "A ZONA DO POSTO É LEI" in src
      and "ONDE A PESSOA ESTÁ" in src)

print(f"\n{'='*56}\n  {ok} ok · {fail} falha(s)\n{'='*56}")
sys.exit(1 if fail else 0)
