"""Fase 83 — descritor por track (insumo do experimento de separabilidade).

NÃO identifica ninguém e não consolida nada. O que este arquivo trava:

  • as razões corporais são de fato INVARIANTES À ESCALA — é o motivo inteiro
    de escolhê-las no lugar da altura aparente;
  • os keypoints voltam para PIXEL antes de virar distância. `xyn` normaliza x
    pela largura e y pela altura: num frame 640x480 medir direto no normalizado
    esticaria o eixo horizontal em 33% e a razão ombro/tronco viraria ficção;
  • keypoint não detectado vem (0,0) e é descartado — o mesmo zero mentiroso
    que a caixa tinha na Fase 82;
  • o histograma é de MATIZ×SATURAÇÃO, sem o brilho: é o brilho que muda entre
    a luz das 6h e a das 15h, e essa variação não pode virar "outra pessoa";
  • a dispersão (MAD) vai junto de cada razão, porque é ela que responde se o
    sinal serve NESTE ambiente.

Rodar:  python tests_descritor_track.py
"""
import sys, types, os, math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["requests", "ultralytics", "supabase", "groq",
          "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object
sys.modules["anthropic"].Anthropic = object
sys.modules["openai"].OpenAI = object
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")

# cv2/numpy REAIS quando disponíveis: o histograma é o ponto do exercício e um
# dublê de cv2 provaria só que o dublê funciona.
try:
    import numpy as np  # noqa: F401
    import cv2  # noqa: F401
    TEM_CV = True
except Exception:  # pragma: no cover
    TEM_CV = False
    for m in ["cv2", "numpy"]:
        sys.modules.setdefault(m, types.ModuleType(m))
    sys.modules["numpy"].ndarray = object

from backend import pipeline as pl  # noqa: E402

ok = fail = pulados = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ok   {nome}")
    else:
        fail += 1; print(f"  FAIL {nome} {extra}")


def kpts(pontos):
    """17 keypoints COCO em coordenada NORMALIZADA; ausentes = (0,0)."""
    k = [[0.0, 0.0] for _ in range(17)]
    for i, (x, y) in pontos.items():
        k[i] = [x, y]
    return k


W, H = 640, 480


def pessoa_padrao(escala=1.0, cx=0.5, cy=0.5):
    """Mesma pessoa, projetada maior ou menor (= mais perto ou mais longe).
    Em pixel: ombros 60px, tronco 120px, quadril 40px, cabeça 30px (×escala)."""
    om_meia = (30 * escala) / W
    tronco = (120 * escala) / H
    qua_meia = (20 * escala) / W
    cabeca = (30 * escala) / H
    return kpts({
        0: (cx, cy - cabeca),
        5: (cx - om_meia, cy), 6: (cx + om_meia, cy),
        11: (cx - qua_meia, cy + tronco), 12: (cx + qua_meia, cy + tronco),
    })


print("[1] As razões são invariantes à ESCALA (a distância à câmera cancela)")
perto = pl.razoes_corporais(pessoa_padrao(1.0), W, H)
longe = pl.razoes_corporais(pessoa_padrao(0.5), W, H)
check("as três razões saem", set(perto) == {"ombro_tronco", "quadril_ombro", "cabeca_tronco"},
      perto)
check("ombro/tronco = 60/120 = 0.5", abs(perto["ombro_tronco"] - 0.5) < 1e-3, perto)
check("quadril/ombro = 40/60 = 0.667", abs(perto["quadril_ombro"] - 0.6667) < 1e-3, perto)
check("cabeça/tronco = 30/120 = 0.25", abs(perto["cabeca_tronco"] - 0.25) < 1e-3, perto)
check("pessoa a METADE do tamanho dá as MESMAS razões",
      all(abs(perto[k] - longe[k]) < 1e-3 for k in perto), (perto, longe))
alt_perto, alt_longe = 150 / H, 75 / H
check("...enquanto a altura aparente muda 2x (o confundidor que isto resolve)",
      abs(alt_perto / alt_longe - 2.0) < 1e-6)

print("\n[2] Keypoint volta para PIXEL antes de virar distância")
# Num frame 640x480 o normalizado comprime y contra x. Medir sem desfazer isso
# distorceria toda razão que mistura eixos.
p = pessoa_padrao(1.0)
r_certo = pl.razoes_corporais(p, W, H)


def _razao_ingenua(k):
    """O jeito ERRADO: distância direto no normalizado."""
    om = math.dist(k[5], k[6]); tr = math.dist(
        ((k[5][0] + k[6][0]) / 2, (k[5][1] + k[6][1]) / 2),
        ((k[11][0] + k[12][0]) / 2, (k[11][1] + k[12][1]) / 2))
    return om / tr


check("o cálculo ingênuo em normalizado erra por ~4/3 (640/480)",
      abs(_razao_ingenua(p) / r_certo["ombro_tronco"] - (H / W) ** -1 * (H / W) * (W / H)) < 0.02
      or abs(_razao_ingenua(p) - r_certo["ombro_tronco"] * (H / W)) < 1e-3,
      (_razao_ingenua(p), r_certo["ombro_tronco"]))
check("e o cálculo correto independe do formato do frame",
      abs(pl.razoes_corporais(pessoa_padrao(1.0), 1920, 1440)["ombro_tronco"]
          - r_certo["ombro_tronco"]) < 1e-3)

print("\n[3] Zero de keypoint não detectado é descartado")
so_ombros = kpts({5: (0.4, 0.5), 6: (0.6, 0.5)})   # sem quadril, sem nariz
r = pl.razoes_corporais(so_ombros, W, H)
check("sem quadril não há tronco, logo não há razão nenhuma", r == {}, r)
com_nariz_fantasma = kpts({
    0: (0.0, 0.0),                       # nariz "não detectado"
    5: (0.45, 0.4), 6: (0.55, 0.4), 11: (0.47, 0.7), 12: (0.53, 0.7)})
r2 = pl.razoes_corporais(com_nariz_fantasma, W, H)
check("as razões do tronco saem", "ombro_tronco" in r2 and "quadril_ombro" in r2)
check("mas o nariz em (0,0) NÃO vira cabeça/tronco", "cabeca_tronco" not in r2, r2)
check("kpts ausente devolve vazio", pl.razoes_corporais(None, W, H) == {})

print("\n[4] Denominador minúsculo não vira razão")
minusculo = kpts({5: (0.499, 0.5), 6: (0.501, 0.5),
                    11: (0.4995, 0.502), 12: (0.5005, 0.502)})
check("segmento abaixo do mínimo é recusado (ruído ÷ ruído)",
      pl.razoes_corporais(minusculo, W, H) == {}, pl.razoes_corporais(minusculo, W, H))

print("\n[5] Agregação: mediana + dispersão + n")
check("mediana de lista par", pl._mediana([1, 2, 3, 4]) == 2.5)
check("MAD resiste a um frame ruim",
      pl._mad([1.0, 1.0, 1.0, 9.0], 1.0) == 0.0, pl._mad([1.0, 1.0, 1.0, 9.0], 1.0))

if not TEM_CV:  # pragma: no cover
    print("\n[6..8] PULADOS — cv2/numpy indisponíveis neste ambiente")
    pulados = 1
else:
    print("\n[6] Histograma: matiz×saturação, metades separadas, sem brilho")
    import numpy as np
    import cv2

    def frame_bicolor(cor_cima, cor_baixo, w=200, h=400):
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[: h // 2, :] = cor_cima
        img[h // 2:, :] = cor_baixo
        return img

    AZUL, VERMELHO = (200, 60, 30), (30, 40, 210)      # BGR
    f = frame_bicolor(AZUL, VERMELHO)
    hc = pl.histograma_cor(f, (0, 0, 200, 400))
    check("devolve as duas metades", hc and hc.get("sup") and hc.get("inf"))
    check("cada metade tem H*S bins",
          len(hc["sup"]) == pl._HIST_BINS_H * pl._HIST_BINS_S, len(hc["sup"]))
    check("cada metade soma 1", abs(sum(hc["sup"]) - 1.0) < 1e-3
          and abs(sum(hc["inf"]) - 1.0) < 1e-3)
    check("camisa e calça de cores diferentes dão histogramas diferentes",
          max(abs(a - b) for a, b in zip(hc["sup"], hc["inf"])) > 0.5)

    # O teste que importa para a fábrica: a MESMA roupa sob luz mais fraca.
    escuro = (f.astype("float32") * 0.55).astype("uint8")
    hc_escuro = pl.histograma_cor(escuro, (0, 0, 200, 400))
    dif_luz = max(abs(a - b) for a, b in zip(hc["sup"], hc_escuro["sup"]))
    dif_cor = max(abs(a - b) for a, b in zip(hc["sup"], hc["inf"]))
    check("a mesma roupa com 45% menos luz continua parecida consigo mesma",
          dif_luz < dif_cor / 2, (dif_luz, dif_cor))
    check("caixa inválida não produz histograma",
          pl.histograma_cor(f, (0, 0, 0, 0)) is None)

    print("\n[7] Acumular e fechar o descritor de um track")
    acc = {}
    for i in range(6):
        pessoa = {"bbox": (50, 40, 110, 340), "kpts": pessoa_padrao(1.0),
                  "frame_idx": i * 10}
        pl.acumular_descritor(acc, 7, frame=frame_bicolor(AZUL, VERMELHO, 640, 480),
                              pessoa=pessoa, w=W, h=H, tempo_s=i * 2.0,
                              no_posto=(i < 4), papel="operador")
    # Um visitante no mesmo vídeo, menor e de outra cor.
    pl.acumular_descritor(acc, 99, frame=frame_bicolor(VERMELHO, AZUL, 640, 480),
                          pessoa={"bbox": (300, 200, 330, 320),
                                  "kpts": pessoa_padrao(0.6, cx=0.7),
                                  "frame_idx": 5},
                          w=W, h=H, tempo_s=2.0, no_posto=False, papel="visitante")
    descs = pl.fechar_descritores(acc, intervalo_s=2.0, cam_id="cam1", w=W, h=H)
    check("um descritor por track", len(descs) == 2, len(descs))
    d = next(x for x in descs if x["pessoa_track_id"] == 7)
    check("conta as amostras", d["n_amostras"] == 6, d["n_amostras"])
    check("tempo no posto = 4 amostras x 2s", d["tempo_posto_s"] == 8.0, d["tempo_posto_s"])
    check("tempo visível = 6 x 2s", d["tempo_visivel_s"] == 12.0)
    check("papel predominante", d["papel_predominante"] == "operador")
    check("carrega a câmera (cam1 e cam2 não são a mesma régua)", d["cam_id"] == "cam1")
    check("altura_rel = 300/480", abs(d["altura_rel"] - 0.625) < 1e-3, d["altura_rel"])
    check("aspecto = 60/300", abs(d["aspecto"] - 0.2) < 1e-3, d["aspecto"])
    check("as três razões, com mediana/dispersão/n",
          set(d["razoes"]) == {"ombro_tronco", "quadril_ombro", "cabeca_tronco"}
          and set(d["razoes"]["ombro_tronco"]) == {"med", "mad", "n"}, d["razoes"])
    check("razão estável tem dispersão zero", d["razoes"]["ombro_tronco"]["mad"] == 0.0)
    check("n da razão = amostras em que deu para medir",
          d["razoes"]["ombro_tronco"]["n"] == 6)
    check("histogramas das duas metades", d["hist_sup"] and d["hist_inf"])
    check("bbox de referência é NORMALIZADA (funciona no frame redimensionado)",
          all(0.0 <= v <= 1.0 for v in d["bbox_ref"]), d["bbox_ref"])
    check("e aponta o frame de onde saiu", d["frame_ref"] is not None)

    v = next(x for x in descs if x["pessoa_track_id"] == 99)
    check("o visitante tem tempo de posto ZERO", v["tempo_posto_s"] == 0.0)
    check("e altura bem menor que a do titular",
          v["altura_rel"] < d["altura_rel"] / 2, (v["altura_rel"], d["altura_rel"]))
    check("mas as MESMAS razões corporais quando é o mesmo corpo — "
          "a escala não separa pessoa, é isso que o experimento vai testar",
          abs(v["razoes"]["ombro_tronco"]["med"]
              - d["razoes"]["ombro_tronco"]["med"]) < 1e-2)

    print("\n[8] Track sem sinal nenhum não inventa descritor")
    vazio = {}
    pl.acumular_descritor(vazio, 1, frame=frame_bicolor(AZUL, VERMELHO, 640, 480),
                          pessoa={"bbox": (0, 0, 0, 0), "kpts": None},
                          w=W, h=H, tempo_s=0.0, no_posto=False, papel=None)
    dv = pl.fechar_descritores(vazio, 2.0, "cam1", W, H)[0]
    check("sem caixa válida, sem altura", dv["altura_rel"] is None)
    check("sem kpts, sem razões", dv["razoes"] is None)
    check("sem cor", dv["hist_sup"] is None and dv["hist_inf"] is None)
    check("mas o track continua contado", dv["n_amostras"] == 1)

print("\n[9] Fiação: onde entra, onde é gravado, como sai")
from pathlib import Path  # noqa: E402
src = Path("backend/pipeline.py").read_text()
mn = Path("backend/main.py").read_text()
sql = Path("sql/schema.sql").read_text()

check("o acumulador roda DEPOIS da eleição de papel (o descritor precisa "
      "saber se este track é o titular)",
      src.index('p["rotulo"] = f"P{i + 1}"')
      < src.index("acumular_descritor(\n                            desc_acc"))
check("a detecção devolve os descritores",
      "return amostras, info, ids_unicos, descritores" in src)
check("e o processamento os repassa à persistência",
      "descritores_track=descritores_track" in src)
check("a gravação é upsert na chave (video_id, track)",
      'on_conflict="video_id,pessoa_track_id"' in src)
check("e é NÃO-FATAL (experimento não derruba vídeo da campanha)",
      "o vídeo segue normal" in src)
check("a tabela existe com a chave única",
      "create table if not exists descritores_track" in sql
      and "unique (video_id, pessoa_track_id)" in sql)
check("com RLS e GRANT explícito (sem grant a Data API devolve vazio)",
      "descritores_track enable row level security" in sql
      and "grant select, insert, update, delete on table descritores_track" in sql)
check("o export é GET e não escreve nada",
      '@app.get("/processos/{processo_id}/descritores/dia")' in mn)
check("o recorte sai do frame JÁ guardado, sem gravar byte novo",
      "chave_frame_evento(caminho, evento[\"id\"], 1)" in mn
      and "Nenhum byte novo é gravado" in mn)
check("o recorte usa a bbox normalizada (frame do storage é redimensionado)",
      "float(bbox_ref[0]) * w" in mn)
check("o zip leva csv, json e recortes",
      'z.writestr("descritores.csv"' in mn and 'z.writestr("descritores.json"' in mn
      and '"recortes/' in mn)
check("e um LEIA-ME que avisa para NÃO misturar câmeras",
      "NÃO misture câmeras" in mn)
check("que explica o corte por n", "`*_n` baixo é ruído" in mn)
check("e que diz o que significa dispersão alta",
      "não separa — e isso é a resposta do experimento" in mn)

doc = Path("docs/sinais_por_track.md").read_text()
check("o levantamento foi atualizado com o que passou a existir",
      "Fase 83" in doc)

print(f"\n{'=' * 60}\n  {ok} ok · {fail} falha(s)"
      + (" · [6-8] pulados (sem cv2)" if pulados else "") + f"\n{'=' * 60}")
sys.exit(1 if fail else 0)
