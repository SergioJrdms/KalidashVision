"""Contrato comercial: presença, posto e produtividade sem tempo público."""
from datetime import datetime, timezone
import json

from backend import productivity as prod

AGORA = datetime(2026, 8, 17, 12, 3, tzinfo=timezone.utc)

ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


def ev(papel="operador", ini=0, fim=60, **kw):
    out = {
        "video_id": "v1",
        "papel_pessoa": papel,
        "tempo_inicio_s": ini,
        "tempo_fim_s": fim,
        "principal": True,
        "_capturado_em": datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc),
        "_dia": "2026-08-17",
        "_cam_id": "cam1",
    }
    out.update(kw)
    return out


print("[1] Classificação direta e falha segura")
check("papel ausente é inconclusivo, nunca presença",
      prod.classificar_observacao(ev(None))[0] == prod.EST_SEM_LEITURA)
check("label legado não transforma candidato incerto em posto vazio",
      prod.classificar_observacao(
          ev(None, comportamento_label="posto_vazio", pessoa_track_id=7,
             maos_maquina=True, orientacao="frente")
      )[0] == prod.EST_SEM_LEITURA)
check("visitante com mão no torno não vira operador",
      prod.classificar_observacao(ev("visitante", maos_maquina=True))[0]
      == prod.EST_OPERADOR_AUSENTE)
check("mão no torno vence e é produtivo",
      prod.classificar_observacao(ev(maos_maquina=True, trabalho=False))[0]
      == prod.EST_PRODUTIVO)
check("texto herdado não decide produtividade",
      prod.classificar_observacao(ev(descricao_bruta="de costas conversando"))[0]
      == prod.EST_PRODUTIVIDADE_INCONCLUSIVA)
check("a decisão estruturada marca a conversa como improdutiva",
      prod.classificar_observacao(
          ev(descricao_bruta="de costas conversando", trabalho=False)
      )[0] == prod.EST_IMPRODUTIVO)
check("orientação calibrada decide",
      prod.classificar_observacao(
          ev(orientacao="frente"), {"cam1": "camera"}
      )[0] == prod.EST_PRODUTIVO)
check("configuração perfil não inventa esquerda/direita",
      prod.classificar_observacao(
          ev(orientacao="perfil"), {"cam1": "perfil"}
      )[0] == prod.EST_PRODUTIVIDADE_INCONCLUSIVA)
check("booleano estruturado é o fallback final",
      prod.classificar_observacao(ev(trabalho=False))[0] == prod.EST_IMPRODUTIVO)
check("vazio contradito por sinal de pessoa é inconclusivo",
      prod.classificar_observacao(ev("posto_vazio", maos_maquina=True))[0]
      == prod.EST_SEM_LEITURA)
check("operador fora sem decisão é ausência observada, não sem_leitura",
      prod.classificar_observacao(ev("operador_fora"))[0]
      == prod.EST_OPERADOR_FORA)
check("somente humano_rotulo pode classificar atividade fora",
      all(prod.classificar_observacao(ev(
          "operador_fora", categoria_lean="valor_agregado",
          categoria_lean_origem=origem,
      ))[0] == prod.EST_OPERADOR_FORA
          for origem in (None, "herdado", "ia", "aprendido", "fallback")))
check("humano_rotulo produtivo é aceito sem virar presença",
      prod.classificar_observacao(ev(
          "operador_fora", categoria_lean="valor_agregado",
          categoria_lean_origem="humano_rotulo",
      ))[0] == prod.EST_OPERADOR_FORA_PRODUTIVO)

print("\n[2] Denominadores separados")
eventos = [
    ev(trabalho=True),
    ev(ini=60, fim=120, trabalho=False),
    ev(ini=120, fim=180),
    ev("visitante", ini=180, fim=240),
    ev("posto_vazio", ini=240, fim=300),
    ev(None, ini=300, fim=360),
]
r = prod.agregar_produtividade(eventos, janela_dias=7, agora=AGORA)
check("produtividade usa só decisões válidas", r["produtividade_pct"] == 50.0, r)
check("presença usa só identidade resolvida", r["presenca_pct"] == 60.0, r)
check("visitante e vazio são fatias distintas",
      r["outra_pessoa_no_posto_pct"] == 20.0
      and r["posto_vazio_pct"] == 20.0, r)
check("falha de produtividade aparece como cobertura",
      r["cobertura_produtividade_pct"] == 66.7, r)
check("falha de identidade não entra como presença",
      r["cobertura_presenca_pct"] == 83.3, r)
check("vazio não infla a cobertura de identificação",
      r["cobertura_identificacao_pct"] == 80.0, r)
check("cobertura insuficiente bloqueia criativo", r["publicavel"] is False, r)

coexistencia = prod.agregar_produtividade([
    ev(trabalho=True),
    ev("visitante", trabalho=True),
], agora=AGORA)
check("visitante simultâneo não duplica o denominador nem apaga o operador",
      coexistencia["presenca_pct"] == 100.0
      and coexistencia["produtividade_pct"] == 100.0, coexistencia)

crus = [
    ev("posto_vazio", principal=True),
    ev(trabalho=True, fim=20, principal=False),
    ev("posto_vazio", ini=20, fim=60, principal=False),
]
transicao = prod.agregar_produtividade(crus, agora=AGORA)
check("eventos crus preservam a transição dentro do minuto",
      transicao["presenca_pct"] == 33.3
      and transicao["posto_vazio_pct"] == 66.7, transicao)

uma_leitura = prod.agregar_produtividade([ev(trabalho=True)], agora=AGORA)
check("uma leitura não libera apresentação", uma_leitura["publicavel"] is False,
      uma_leitura)

amostra_fraca = prod.agregar_produtividade([
    ev(trabalho=True), ev("posto_vazio", ini=60, fim=120, n_amostras=20),
], agora=AGORA)
check("posto vazio não infla o piso de evidência da produtividade",
      amostra_fraca["publicavel"] is False, amostra_fraca)

publicavel = prod.agregar_produtividade([
    ev(trabalho=True, n_amostras=20), ev(ini=60, fim=120, trabalho=True),
    ev("posto_vazio", ini=120, fim=180),
], agora=AGORA)
check("boa cobertura libera a leitura", publicavel["publicavel"] is True, publicavel)

base_fora = [
    ev(trabalho=True),
    ev(ini=60, fim=120, trabalho=False),
    ev("posto_vazio", ini=120, fim=180),
]
substituido = [
    *base_fora[:2], ev("operador_fora", ini=120, fim=180),
]
m_base = prod.agregar_produtividade(base_fora, agora=AGORA)
m_sub = prod.agregar_produtividade(substituido, agora=AGORA)
invariantes = (
    "produtividade_pct", "improdutividade_pct", "presenca_pct",
    "posto_vazio_pct", "cobertura_produtividade_pct",
    "cobertura_presenca_pct", "cobertura_identificacao_pct", "inconclusivo_pct",
)
check("posto_vazio → operador_fora sem decisão preserva todos os percentuais",
      all(m_base[k] == m_sub[k] for k in invariantes),
      {k: (m_base[k], m_sub[k]) for k in invariantes})

decidido_fora = prod.agregar_produtividade([
    *base_fora[:2],
    ev("operador_fora", ini=120, fim=180,
       categoria_lean="valor_agregado",
       categoria_lean_origem="humano_rotulo"),
], agora=AGORA)
check("decisão humana fora aumenta produtividade sem alterar presença",
      decidido_fora["produtividade_pct"] > m_sub["produtividade_pct"]
      and decidido_fora["presenca_pct"] == m_sub["presenca_pct"]
      and decidido_fora["posto_vazio_pct"] == m_sub["posto_vazio_pct"],
      decidido_fora)

print("\n[3] Estado atual e contrato público")
atual = prod.agregar_produtividade([
    ev(trabalho=True),
    ev("posto_vazio", ini=60, fim=120),
], agora=AGORA)["estado_atual"]
check("última leitura é posto vazio", atual["presenca"] == "posto_vazio", atual)
check("expõe instante absoluto da captura", atual["leitura_em"].startswith("2026-08-17T12:02"), atual)

contraditorio = prod.agregar_produtividade([
    ev("posto_vazio"), ev(trabalho=True),
], agora=AGORA)["estado_atual"]
check("contradição atual nunca aparece como operador no posto",
      contraditorio["presenca"] == "sem_leitura"
      and contraditorio["posto"] == "indeterminado", contraditorio)

fora_atual = prod.agregar_produtividade([
    ev("operador_fora", categoria_lean="valor_agregado",
       categoria_lean_origem="humano_rotulo"),
], agora=AGORA)["estado_atual"]
check("estado atual fora mantém posto vazio e decisão produtiva separada",
      fora_atual["presenca"] == "fora_do_posto"
      and fora_atual["posto"] == "vazio"
      and fora_atual["produtividade"] == "produtivo", fora_atual)

vazio = prod.agregar_produtividade([], agora=AGORA)
check("sem denominador devolve null, não zero inventado",
      vazio["produtividade_pct"] is None and vazio["presenca_pct"] is None, vazio)
check("ausência de leitura não é apresentada como captura atrasada",
      vazio["sem_dado"] is True and vazio["captura_atrasada"] is False, vazio)

serializado = json.dumps(r, ensure_ascii=False).lower()
check("payload não publica duração ou minutos",
      all(chave not in serializado for chave in (
          "tempo_inicio", "tempo_fim", "duracao", "duração", "minutos", "segundos"
      )), serializado)

print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
