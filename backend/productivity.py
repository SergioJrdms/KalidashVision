"""Contrato canônico do caso de uso comercial do posto.

O cliente recebe somente percentuais e o estado da última leitura. Duração é
usada internamente apenas como peso estatístico; nunca sai neste contrato.

Há duas perguntas diferentes e elas não devem ser misturadas:

* presença: o operador estava no posto ou o posto estava vazio?
* produtividade: quando havia evidência válida sobre a atividade do operador,
  ela era produtiva ou improdutiva? Fora do posto, só a classificação explícita
  do gestor constitui essa evidência.

Separar os denominadores evita transformar falha de pose/VLM em acusação de
improdutividade. ``cobertura_produtividade_pct`` explicita quanto do universo
avaliável teve evidência suficiente para responder a segunda pergunta.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
import unicodedata
from typing import Any


EST_PRODUTIVO = "produtivo"
EST_IMPRODUTIVO = "improdutivo"
EST_PRODUTIVIDADE_INCONCLUSIVA = "produtividade_inconclusiva"
EST_POSTO_VAZIO = "posto_vazio"
EST_OPERADOR_AUSENTE = "operador_ausente"
EST_OPERADOR_FORA = "operador_fora"
EST_OPERADOR_FORA_PRODUTIVO = "operador_fora_produtivo"
EST_OPERADOR_FORA_IMPRODUTIVO = "operador_fora_improdutivo"
EST_SEM_LEITURA = "sem_leitura"
EST_IGNORAR = "ignorar"

# Regra comercial de conversa. Estes valores só têm efeito quando aparecem
# juntos com a evidência visual estruturada gravada em
# ``bbox_stats.interlocutor``; o texto e o rótulo, isoladamente, nunca votam.
LABEL_CONVERSANDO_GESTOR = "conversando_gestor"
LABEL_CONVERSANDO_COLEGA = "conversando_colega"
LABEL_CONVERSANDO_INCERTO = "conversando_interlocutor_incerto"
TIPO_INTERLOCUTOR_GESTOR = "gestor_cinza"
TIPO_INTERLOCUTOR_COLEGA = "outra_pessoa"
TIPO_INTERLOCUTOR_INCERTO = "incerto"
CONFIANCA_COR_GESTOR_MIN = 0.72


def decisao_conversa_evidenciada(e: dict) -> tuple[str, str] | None:
    """Decisão estreita da conversa, ou ``None`` sem o contrato completo.

    A regra exige quatro peças coerentes produzidas no mesmo frame: rótulo
    canônico ORIGINAL, booleano ``trabalho``, associação estruturada ao
    interlocutor e classificação objetiva da roupa superior. Assim, nem uma
    correção textual para "gestor" nem um label inventado pelo cluster movem o
    KPI. O recorte auditável viaja no JSONB ``bbox_stats``, já existente.
    """
    if _texto_normalizado(e.get("papel_pessoa")) != "operador":
        return None
    stats = e.get("bbox_stats")
    evidencia = stats.get("interlocutor") if isinstance(stats, dict) else None
    if not isinstance(evidencia, dict):
        return None

    label = _texto_normalizado(e.get("comportamento_label"))
    tipo = _texto_normalizado(evidencia.get("tipo"))
    cor = _texto_normalizado(evidencia.get("cor_superior"))
    conversa_estado = _texto_normalizado(evidencia.get("conversa_estado"))
    trabalho = e.get("trabalho")
    try:
        confianca = float(evidencia.get("confianca_cor"))
    except (TypeError, ValueError):
        confianca = 0.0

    if (
        label == LABEL_CONVERSANDO_GESTOR
        and trabalho is True
        and conversa_estado == "identificada"
        and tipo == TIPO_INTERLOCUTOR_GESTOR
        and cor == "cinza"
        and confianca >= CONFIANCA_COR_GESTOR_MIN
    ):
        return EST_PRODUTIVO, "conversa_com_gestor_cinza"

    if (
        label == LABEL_CONVERSANDO_COLEGA
        and trabalho is False
        and conversa_estado == "identificada"
        and tipo == TIPO_INTERLOCUTOR_COLEGA
        and cor == "nao_cinza"
        and confianca >= CONFIANCA_COR_GESTOR_MIN
    ):
        return EST_IMPRODUTIVO, "conversa_com_colega_nao_cinza"

    # A conversa foi observada, mas a roupa/associação não autorizou afirmar
    # gestor. Mantém a convenção conservadora anterior: não rende produtividade.
    if (
        label == LABEL_CONVERSANDO_INCERTO
        and trabalho is False
        and conversa_estado in {"identificada", "incerta"}
        and tipo == TIPO_INTERLOCUTOR_INCERTO
        and cor == "incerto"
    ):
        return EST_IMPRODUTIVO, "conversa_com_interlocutor_incerto"
    return None


def _numero(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _duracao_peso(e: dict) -> float:
    """Peso interno da observação; não integra o payload público."""
    ini = _numero(e.get("tempo_inicio_s")) or 0.0
    fim = _numero(e.get("tempo_fim_s")) or 0.0
    return max(0.0, fim - ini)


def _eventos_do_instrumento(eventos: list[dict]) -> list[dict]:
    """Prefere os eventos crus de auditoria quando o vídeo os possui.

    O principal resume um minuto inteiro e inevitavelmente perde transições
    como 20 s com operador + 40 s vazio. Os crus preservam a amostragem; uma
    linha do tempo abaixo resolve sobreposições de operador/visitante sem
    contar duas pessoas duas vezes.
    """
    por_video: dict[str, list[dict]] = {}
    for i, e in enumerate(eventos or []):
        chave = str(e.get("video_id") or f"__sem_video_{i}")
        por_video.setdefault(chave, []).append(e)
    escolhidos: list[dict] = []
    for grupo in por_video.values():
        crus = [e for e in grupo if e.get("principal") is False]
        if crus:
            escolhidos.extend(crus)
            continue
        # A V9 mede por quadro/fatia. Um resumo principal aplica moda/maioria
        # ao intervalo inteiro e não pode substituir os crus sem distorcer o
        # percentual. Falta de auditoria vira falta de dado, não aproximação.
        if any(int(e.get("versao_instrumento") or 0) >= 9 for e in grupo):
            continue
        escolhidos.extend(e for e in grupo if e.get("principal") is not False)
    return escolhidos


def _texto_normalizado(v: Any) -> str:
    txt = unicodedata.normalize("NFKD", str(v or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "_", txt).strip("_")


def _voltado_para_maquina(orientacao: Any, frente_maquina: Any) -> bool | None:
    """Traduz pose em relação à câmera para pose em relação ao torno.

    ``perfil`` como configuração da máquina é deliberadamente abstido: a pose
    atual informa apenas "de perfil", não se a pessoa olha para a esquerda ou
    para a direita. Afirmar produtividade nessa câmera inverteria metade dos
    casos sem que o dado pudesse denunciar o erro.
    """
    orient = _texto_normalizado(orientacao)
    frente = _texto_normalizado(frente_maquina)
    if orient not in {"frente", "costas", "perfil"}:
        return None
    if frente == "camera":
        return orient == "frente"
    if frente == "oposta":
        return orient == "costas"
    return None


def classificar_observacao(
    e: dict,
    frentes_por_camera: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Classifica uma observação sem passar por cluster ou categoria Lean.

    Precedência de evidência:

    1. ausência/papel do posto;
    2. mão no torno (evidência positiva objetiva);
    3. orientação calibrada para a câmera;
    4. decisão binária estruturada do VLM;
    5. abstenção.

    A prosa de ``descricao_bruta`` nunca decide. Ela pode ser herdada ou
    interpolada para manter a auditoria legível; transformar palavras em
    número reabriria a mesma porta de contaminação que o campo estruturado
    fechou.

    Visitantes não entram no denominador do titular. A presença deles também
    não transforma um posto sem operador em "ocupado".
    """
    papel = _texto_normalizado(e.get("papel_pessoa"))

    if papel == EST_POSTO_VAZIO:
        # Sinal de pessoa junto com "vazio" é contradição, não ausência. Há
        # casos reais assim no histórico e usá-los inflaria posto vazio.
        if e.get("maos_maquina") is True or e.get("orientacao") or isinstance(
            e.get("trabalho"), bool
        ):
            return EST_SEM_LEITURA, "vazio_com_sinal_de_pessoa"
        return EST_POSTO_VAZIO, "ausencia_detectada"
    if papel == EST_OPERADOR_FORA:
        # Fase 110: presença e produtividade são eixos independentes. O
        # operador continua FORA do posto em todos os casos; somente a decisão
        # explícita do gestor sobre a atividade pode votar em produtividade.
        origem = _texto_normalizado(e.get("categoria_lean_origem"))
        categoria = _texto_normalizado(e.get("categoria_lean"))
        if origem == "humano_rotulo" and categoria == "valor_agregado":
            return EST_OPERADOR_FORA_PRODUTIVO, "atividade_fora_classificada_produtiva"
        if origem == "humano_rotulo" and categoria == "desperdicio":
            return EST_OPERADOR_FORA_IMPRODUTIVO, "atividade_fora_classificada_improdutiva"
        return EST_OPERADOR_FORA, "atividade_fora_sem_decisao_humana"
    if papel == "visitante":
        return EST_OPERADOR_AUSENTE, "outra_pessoa_no_posto"
    if papel != "operador":
        return EST_SEM_LEITURA, "papel_indefinido"

    # Exceção comercial estreita, antes de mãos/orientação: conversar com o
    # gestor continua produtivo mesmo de lado; conversar com colega continua
    # improdutivo mesmo se a pose apontar para o torno. A evidência visual
    # completa é obrigatória — label ou prosa sozinhos não entram aqui.
    conversa = decisao_conversa_evidenciada(e)
    if conversa is not None:
        return conversa

    if e.get("maos_maquina") is True:
        return EST_PRODUTIVO, "maos_no_torno"

    cam_id = str(e.get("_cam_id") or e.get("cam_id") or "")
    frente = (frentes_por_camera or {}).get(cam_id)
    voltado = _voltado_para_maquina(e.get("orientacao"), frente)
    if voltado is True:
        return EST_PRODUTIVO, "voltado_para_o_torno"
    if voltado is False:
        return EST_IMPRODUTIVO, "costas_ou_lado"

    if e.get("trabalho") is True:
        return EST_PRODUTIVO, "julgamento_visual_direto"
    if e.get("trabalho") is False:
        return EST_IMPRODUTIVO, "julgamento_visual_direto"
    return EST_PRODUTIVIDADE_INCONCLUSIVA, "evidencia_insuficiente"


def _percentual(parte: float, total: float) -> float | None:
    if total <= 0:
        return None
    return round(100.0 * parte / total, 1)


def _resolver_sinais(
    ativos: list[dict], frentes_por_camera: dict[str, str]
) -> tuple[str, str, dict]:
    classificados = [
        (e, *classificar_observacao(e, frentes_por_camera)) for e in ativos
    ]
    estados = {estado for _e, estado, _motivo in classificados}
    operador = {
        EST_PRODUTIVO,
        EST_IMPRODUTIVO,
        EST_PRODUTIVIDADE_INCONCLUSIVA,
    }
    estados_operador = estados & operador
    fora_operador = {
        EST_OPERADOR_FORA,
        EST_OPERADOR_FORA_PRODUTIVO,
        EST_OPERADOR_FORA_IMPRODUTIVO,
    }
    estados_fora = estados & fora_operador

    def representante(estados_alvo: set[str]) -> dict:
        return next(
            (e for e, estado, _motivo in classificados if estado in estados_alvo),
            ativos[0],
        )

    # Papel indefinido ou vazio simultâneo a pessoa é contradição, não uma
    # oportunidade de escolher o lado mais conveniente.
    if EST_SEM_LEITURA in estados:
        return EST_SEM_LEITURA, "identidade_ou_sinal_contraditorio", representante({EST_SEM_LEITURA})
    if estados_fora:
        # `posto_vazio` simultâneo é compatível: ambos dizem que o polígono
        # está vazio, mas o estado de fora traz mais informação. Conflito entre
        # decisões de produtividade se abstém sem retirar a ausência do
        # denominador de presença.
        if estados_operador:
            return EST_SEM_LEITURA, "operador_dentro_e_fora", representante(estados_fora)
        if len(estados_fora) > 1:
            return EST_OPERADOR_FORA, "decisoes_fora_em_conflito", representante(estados_fora)
        estado = next(iter(estados_fora))
        e, _estado, motivo = next(
            item for item in classificados if item[1] == estado
        )
        return estado, motivo, e
    if estados_operador:
        if EST_POSTO_VAZIO in estados:
            return EST_SEM_LEITURA, "vazio_com_operador", representante(estados_operador)
        tracks = {
            e.get("pessoa_track_id")
            for e, estado, _motivo in classificados
            if estado in operador and e.get("pessoa_track_id") is not None
        }
        if len(tracks) > 1 or len(estados_operador) > 1:
            return EST_PRODUTIVIDADE_INCONCLUSIVA, "decisoes_do_operador_em_conflito", representante(estados_operador)
        estado = next(iter(estados_operador))
        e, _estado, motivo = next(
            item for item in classificados if item[1] == estado
        )
        return estado, motivo, e
    if EST_OPERADOR_AUSENTE in estados and EST_POSTO_VAZIO in estados:
        return EST_SEM_LEITURA, "ocupacao_do_posto_em_conflito", representante(estados)
    if EST_OPERADOR_AUSENTE in estados:
        e, _estado, motivo = next(
            item for item in classificados if item[1] == EST_OPERADOR_AUSENTE
        )
        return EST_OPERADOR_AUSENTE, motivo, e
    if EST_POSTO_VAZIO in estados:
        e, _estado, motivo = next(
            item for item in classificados if item[1] == EST_POSTO_VAZIO
        )
        return EST_POSTO_VAZIO, motivo, e
    return EST_SEM_LEITURA, "evidencia_insuficiente", ativos[0]


def _linha_do_tempo(
    eventos: list[dict], frentes_por_camera: dict[str, str]
) -> list[tuple[float, float, str, str, dict]]:
    """Fatias exclusivas: cada instante observado tem um único estado."""
    por_video: dict[str, list[dict]] = {}
    for i, e in enumerate(_eventos_do_instrumento(eventos)):
        if e.get("validacao_correto") is False or _duracao_peso(e) <= 0:
            continue
        chave = str(e.get("video_id") or f"__sem_video_{i}")
        por_video.setdefault(chave, []).append(e)

    fatias: list[tuple[float, float, str, str, dict]] = []
    for grupo in por_video.values():
        limites = sorted({
            valor
            for e in grupo
            for valor in (
                _numero(e.get("tempo_inicio_s")) or 0.0,
                _numero(e.get("tempo_fim_s")) or 0.0,
            )
        })
        for inicio, fim in zip(limites, limites[1:]):
            if fim <= inicio:
                continue
            ativos = [
                e for e in grupo
                if (_numero(e.get("tempo_inicio_s")) or 0.0) < fim
                and (_numero(e.get("tempo_fim_s")) or 0.0) > inicio
            ]
            if not ativos:
                continue
            estado, motivo, rep = _resolver_sinais(ativos, frentes_por_camera)
            fatias.append((inicio, fim, estado, motivo, rep))
    return fatias


def _metricas(eventos: list[dict], frentes_por_camera: dict[str, str]) -> dict:
    pesos = {
        EST_PRODUTIVO: 0.0,
        EST_IMPRODUTIVO: 0.0,
        EST_PRODUTIVIDADE_INCONCLUSIVA: 0.0,
        EST_POSTO_VAZIO: 0.0,
        EST_OPERADOR_AUSENTE: 0.0,
        EST_OPERADOR_FORA: 0.0,
        EST_OPERADOR_FORA_PRODUTIVO: 0.0,
        EST_OPERADOR_FORA_IMPRODUTIVO: 0.0,
        EST_SEM_LEITURA: 0.0,
    }
    evidencias_produtividade: dict[str, int] = {}
    evidencias_presenca: dict[str, int] = {}
    for inicio, fim, estado, _motivo, _e in _linha_do_tempo(
        eventos, frentes_por_camera
    ):
        pesos[estado] = pesos.get(estado, 0.0) + (fim - inicio)
        if estado != EST_SEM_LEITURA:
            chave = str(_e.get("id") or id(_e))
            try:
                n = max(1, int(_e.get("n_amostras") or 1))
            except (TypeError, ValueError):
                n = 1
            evidencias_presenca[chave] = max(
                evidencias_presenca.get(chave, 0), n
            )
            if estado in {
                EST_PRODUTIVO,
                EST_IMPRODUTIVO,
                EST_OPERADOR_FORA_PRODUTIVO,
                EST_OPERADOR_FORA_IMPRODUTIVO,
            }:
                evidencias_produtividade[chave] = max(
                    evidencias_produtividade.get(chave, 0), n
                )

    operador_presente = (
        pesos[EST_PRODUTIVO]
        + pesos[EST_IMPRODUTIVO]
        + pesos[EST_PRODUTIVIDADE_INCONCLUSIVA]
    )
    posto_ocupado = operador_presente + pesos[EST_OPERADOR_AUSENTE]
    operador_fora = (
        pesos[EST_OPERADOR_FORA]
        + pesos[EST_OPERADOR_FORA_PRODUTIVO]
        + pesos[EST_OPERADOR_FORA_IMPRODUTIVO]
    )
    fora_observado = pesos[EST_POSTO_VAZIO] + operador_fora
    observado = posto_ocupado + fora_observado
    total_bruto = observado + pesos[EST_SEM_LEITURA]
    produtivo = pesos[EST_PRODUTIVO] + pesos[EST_OPERADOR_FORA_PRODUTIVO]
    improdutivo = pesos[EST_IMPRODUTIVO] + pesos[EST_OPERADOR_FORA_IMPRODUTIVO]
    classificado = produtivo + improdutivo
    # Antes do clique, operador_fora é numericamente idêntico a posto_vazio e
    # não entra neste universo. Depois, a decisão humana acrescenta aquele
    # intervalo à pergunta de produtividade sem transformá-lo em presença.
    universo_produtividade = (
        operador_presente
        + pesos[EST_OPERADOR_FORA_PRODUTIVO]
        + pesos[EST_OPERADOR_FORA_IMPRODUTIVO]
    )

    presenca = _percentual(operador_presente, observado)
    vazio = _percentual(fora_observado, observado)
    produtividade = _percentual(produtivo, classificado)
    cobertura = _percentual(classificado, universo_produtividade)
    cobertura_presenca = _percentual(observado, total_bruto)
    # Mede especificamente a capacidade de distinguir o operador funcional de
    # outra pessoa quando o posto está ocupado. Posto vazio não infla esta
    # qualidade, pois ali não há identidade a resolver.
    cobertura_identificacao = _percentual(
        operador_presente + pesos[EST_OPERADOR_AUSENTE],
        posto_ocupado + pesos[EST_SEM_LEITURA],
    )
    inconclusivo = _percentual(
        pesos[EST_SEM_LEITURA] + pesos[EST_PRODUTIVIDADE_INCONCLUSIVA],
        total_bruto,
    )
    try:
        minimo_evidencias = max(
            1, int(os.environ.get("KV_PRODUTIVIDADE_MIN_LEITURAS", "20"))
        )
    except (TypeError, ValueError):
        minimo_evidencias = 20
    publicavel = bool(
        produtividade is not None
        and presenca is not None
        and cobertura is not None
        and cobertura >= 80.0
        and cobertura_presenca is not None
        and cobertura_presenca >= 80.0
        and cobertura_identificacao is not None
        and cobertura_identificacao >= 80.0
        # Um turno quase todo vazio não pode tornar publicável uma
        # produtividade baseada em uma única identificação do operador.
        and sum(evidencias_produtividade.values()) >= minimo_evidencias
        and sum(evidencias_presenca.values()) >= minimo_evidencias
    )
    return {
        "produtividade_pct": produtividade,
        "improdutividade_pct": (
            round(100.0 - produtividade, 1) if produtividade is not None else None
        ),
        "presenca_pct": presenca,
        "posto_vazio_pct": vazio,
        "outra_pessoa_no_posto_pct": _percentual(
            pesos[EST_OPERADOR_AUSENTE], observado
        ),
        "cobertura_produtividade_pct": cobertura,
        "cobertura_presenca_pct": cobertura_presenca,
        "cobertura_identificacao_pct": cobertura_identificacao,
        "inconclusivo_pct": inconclusivo,
        "publicavel": publicavel,
        "sem_dado": observado <= 0,
    }


def _instante(e: dict) -> datetime | None:
    base = e.get("_capturado_em")
    if isinstance(base, str):
        try:
            base = datetime.fromisoformat(base.replace("Z", "+00:00"))
        except ValueError:
            base = None
    if not isinstance(base, datetime):
        return None
    fim = _numero(e.get("tempo_fim_s")) or 0.0
    return base + timedelta(seconds=max(0.0, fim))


def _estado_atual(
    eventos: list[dict], frentes_por_camera: dict[str, str]
) -> dict:
    candidatos: list[tuple[datetime, dict, str, str]] = []
    for _inicio, fim, estado, motivo, e in _linha_do_tempo(
        eventos, frentes_por_camera
    ):
        base = e.get("_capturado_em")
        if isinstance(base, str):
            try:
                base = datetime.fromisoformat(base.replace("Z", "+00:00"))
            except ValueError:
                base = None
        if isinstance(base, datetime):
            candidatos.append((base + timedelta(seconds=fim), e, estado, motivo))
    if not candidatos:
        return {
            "presenca": EST_SEM_LEITURA,
            "posto": "indeterminado",
            "produtividade": None,
            "motivo": "sem_observacao",
            "leitura_em": None,
        }

    instante, e, estado, motivo = max(candidatos, key=lambda item: item[0])
    papel = _texto_normalizado(e.get("papel_pessoa"))
    if estado == EST_SEM_LEITURA:
        presenca = EST_SEM_LEITURA
        produtividade = None
    elif estado == EST_POSTO_VAZIO:
        presenca = EST_POSTO_VAZIO
        produtividade = None
    elif estado == EST_OPERADOR_AUSENTE:
        presenca = "fora_do_posto"
        produtividade = None
    elif estado in {
        EST_OPERADOR_FORA,
        EST_OPERADOR_FORA_PRODUTIVO,
        EST_OPERADOR_FORA_IMPRODUTIVO,
    }:
        presenca = "fora_do_posto"
        produtividade = (
            EST_PRODUTIVO if estado == EST_OPERADOR_FORA_PRODUTIVO
            else EST_IMPRODUTIVO if estado == EST_OPERADOR_FORA_IMPRODUTIVO
            else None
        )
    elif papel == "operador":
        presenca = "no_posto"
        produtividade = estado if estado in {EST_PRODUTIVO, EST_IMPRODUTIVO} else None
    else:
        presenca = EST_SEM_LEITURA
        produtividade = None
    return {
        "presenca": presenca,
        "posto": (
            "indeterminado" if estado == EST_SEM_LEITURA
            else "vazio" if estado in {
                EST_POSTO_VAZIO,
                EST_OPERADOR_FORA,
                EST_OPERADOR_FORA_PRODUTIVO,
                EST_OPERADOR_FORA_IMPRODUTIVO,
            }
            else "ocupado" if estado == EST_OPERADOR_AUSENTE or papel == "operador"
            else "indeterminado"
        ),
        "produtividade": produtividade,
        "motivo": motivo,
        "leitura_em": instante.isoformat(),
    }


def agregar_produtividade(
    eventos_periodo: list[dict],
    *,
    frentes_por_camera: dict[str, str] | None = None,
    eventos_estado_atual: list[dict] | None = None,
    janela_dias: int = 7,
    agora: datetime | None = None,
) -> dict:
    """Monta o único contrato consumido pela vitrine comercial."""
    frentes = dict(frentes_por_camera or {})
    base = _metricas(eventos_periodo or [], frentes)

    por_dia: dict[str, list[dict]] = {}
    for e in eventos_periodo or []:
        dia = e.get("_dia")
        if dia:
            por_dia.setdefault(str(dia), []).append(e)
    serie = []
    for dia in sorted(por_dia):
        m = _metricas(por_dia[dia], frentes)
        serie.append({"dia": dia, **m})

    atual = _estado_atual(
        eventos_estado_atual if eventos_estado_atual is not None else eventos_periodo,
        frentes,
    )
    leitura = atual.get("leitura_em")
    try:
        leitura_dt = datetime.fromisoformat(str(leitura).replace("Z", "+00:00"))
        if leitura_dt.tzinfo is None:
            leitura_dt = leitura_dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        leitura_dt = None
    agora_dt = agora or datetime.now(timezone.utc)
    if agora_dt.tzinfo is None:
        agora_dt = agora_dt.replace(tzinfo=timezone.utc)
    try:
        frescor_min = max(
            1, int(os.environ.get("KV_PRODUTIVIDADE_FRESCOR_MIN", "30"))
        )
    except (TypeError, ValueError):
        frescor_min = 30
    # Ausência total de leitura e leitura antiga são estados distintos para a UI.
    # Quando há evidência no período, porém o instante não pôde ser comprovado,
    # continuamos bloqueando a publicação pelo mesmo fail-safe de frescor.
    captura_atrasada = bool(
        not base["sem_dado"]
        and (
            leitura_dt is None
            or leitura_dt < agora_dt - timedelta(minutes=frescor_min)
            or leitura_dt > agora_dt + timedelta(minutes=10)
        )
    )
    base["publicavel"] = bool(base["publicavel"] and not captura_atrasada)

    return {
        **base,
        "janela_dias": int(janela_dias),
        "captura_atrasada": captura_atrasada,
        "estado_atual": atual,
        "serie_diaria": serie,
        "regra": {
            "produtivo": "mãos no torno, voltado para o torno ou julgamento visual direto",
            "improdutivo": "de costas/de lado, conversa/celular ou julgamento visual direto",
            "presenca": "operador dentro da zona do posto",
        },
    }
