"""Fase 111F — uma foto sozinha não afirma ausência.

A amostragem é de 5s: um trecho de 5s de "posto sem operador" é UMA imagem
decidindo. Pelos rótulos humanos, cards de 5s acertavam 23% contra 64% dos de
duas ou mais amostras; no replay A/B do commit cb94a9b, um piso de 20s levou a
precisão por card de 67,8% para 98,4% retendo 97% do tempo verdadeiro.

O que este teste tranca não é o número — é a semântica:

    o trecho curto vira ABSTENÇÃO, nunca presença.

Afirmar que o operador está lá a partir da mesma falta de evidência seria o
erro da Fase 111E invertido. Um teste de string passaria com a linha certa e a
semântica trocada, então aqui a função roda de verdade.

Rodar:  python tests_piso_ausencia.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for m in ["cv2", "numpy", "requests", "ultralytics", "supabase", "groq",
          "anthropic", "openai", "dotenv", "httpx", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, types.ModuleType(m))
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["ultralytics"].YOLO = object
sys.modules["supabase"].create_client = lambda *a, **k: None
sys.modules["supabase"].Client = object
sys.modules["groq"].Groq = object

from backend import pipeline as pl  # noqa: E402

ok = fail = 0


def check(nome, cond):
    global ok, fail
    print(f"  {'ok  ' if cond else 'FAIL'} {nome}")
    ok += bool(cond)
    fail += not cond


def am(tempo, presente, **kw):
    a = pl.Amostra(frame_idx=int(tempo * 10), tempo_s=float(tempo),
                   img_b64="", pessoas=[], dim=(640, 480),
                   operador_presente=presente)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def estados(amostras):
    return [a.operador_presente for a in amostras]


print("[1] O trecho curto vira abstenção — nunca presença")
a = [am(0, False)]                      # 1 amostra = 5s
r = pl.etapa_piso_afirmacao_ausencia(a, 5.0, piso_s=20)
check("5s com piso de 20s é rebaixado", a[0].operador_presente is None)
check("NÃO vira presença (o erro da 111E invertido)", a[0].operador_presente is not True)
check("fica marcado para auditoria", a[0].ausencia_curta_rebaixada is True)
check("o resumo conta o trecho", r["trechos_rebaixados"] == 1 and r["slots_rebaixados"] == 1)

print("\n[2] O trecho longo sobrevive intacto")
b = [am(t, False) for t in (0, 5, 10, 15, 20)]     # 25s
pl.etapa_piso_afirmacao_ausencia(b, 5.0, piso_s=20)
check("25s com piso de 20s permanece afirmação", all(x is False for x in estados(b)))
check("e não recebe a marca", not any(x.ausencia_curta_rebaixada for x in b))

print("\n[3] A borda é inclusiva: exatamente o piso passa")
c = [am(t, False) for t in (0, 5, 10, 15)]         # 20s exatos
pl.etapa_piso_afirmacao_ausencia(c, 5.0, piso_s=20)
check("20s exatos com piso 20s sobrevivem", all(x is False for x in estados(c)))
d = [am(t, False) for t in (0, 5, 10)]             # 15s
pl.etapa_piso_afirmacao_ausencia(d, 5.0, piso_s=20)
check("15s com piso 20s é rebaixado", all(x is None for x in estados(d)))

print("\n[4] Presença no meio parte o trecho — dois curtos não viram um longo")
e = [am(0, False), am(5, False), am(10, True), am(15, False), am(20, False)]
r = pl.etapa_piso_afirmacao_ausencia(e, 5.0, piso_s=20)
check("dois trechos identificados", r["trechos"] == 2)
check("os dois são rebaixados (10s cada)",
      estados(e) == [None, None, True, None, None])
check("a presença no meio não é tocada", e[2].operador_presente is True)

print("\n[5] Buraco de amostragem também parte — falta de medida não é continuidade")
f = [am(0, False), am(5, False), am(60, False), am(65, False)]
r = pl.etapa_piso_afirmacao_ausencia(f, 5.0, piso_s=20)
check("salto de 55s separa os trechos", r["trechos"] == 2)
check("os dois curtos caem", all(x is None for x in estados(f)))

print("\n[6] A afirmação de FORA também é uma afirmação de ausência")
g = [am(0, False, operador_fora_estado=True,
        fora_posto=[{"track_id": 9}], operador_ponte=True)]
pl.etapa_piso_afirmacao_ausencia(g, 5.0, piso_s=20)
check("o estado fora é retirado junto", g[0].operador_fora_estado is False)
check("a lista fora_posto é esvaziada", g[0].fora_posto == [])
check("a ponte temporal também sai", g[0].operador_ponte is False)

print("\n[7] Inconclusivo não é afirmação e não entra no trecho")
h = [am(0, False), am(5, None), am(10, False)]
r = pl.etapa_piso_afirmacao_ausencia(h, 5.0, piso_s=20)
check("None separa os trechos", r["trechos"] == 2)
check("o inconclusivo permanece inconclusivo", h[1].operador_presente is None)
check("nada foi promovido a presença", not any(x is True for x in estados(h)))

print("\n[8] Desligado é desligado")
i = [am(0, False)]
r = pl.etapa_piso_afirmacao_ausencia(i, 5.0, piso_s=0)
check("piso 0 não mexe em nada", i[0].operador_presente is False)
check("e o resumo diz que está inativo", r["ativo"] is False)
check("lista vazia não explode",
      pl.etapa_piso_afirmacao_ausencia([], 5.0, piso_s=20)["trechos"] == 0)

print("\n[9] Ordem: o piso julga a afirmação FINAL")
fonte = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backend", "pipeline.py"), encoding="utf-8").read()
pos_111d = fonte.find("resumo_111d = aplicar_identidade_logica_segmento")
pos_piso = fonte.find("resumo_piso = etapa_piso_afirmacao_ausencia")
pos_vlm = fonte.find("observacoes = etapa_analise_vlm(")
check("o piso roda DEPOIS da 111D", 0 < pos_111d < pos_piso)
check("e ANTES da VLM", pos_piso < pos_vlm)
check("o padrão do ambiente é desligado",
      float(os.environ.get("KV_PISO_AUSENCIA_S", "0")) == 0.0)

print("\n[10] O piso não inventa presença em lugar nenhum")
j = [am(t, False) for t in range(0, 100, 5)]   # trecho longo
j += [am(100, False)]                          # trecho curto isolado? não: contíguo
pl.etapa_piso_afirmacao_ausencia(j, 5.0, piso_s=60)
check("nenhuma amostra virou True em nenhum cenário",
      not any(x is True for x in estados(j)))

print(f"\n{ok} ok · {fail} falha(s)")
sys.exit(1 if fail else 0)
