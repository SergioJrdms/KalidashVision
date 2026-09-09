"""Detecção conservadora e independente de operação da ponte rolante.

Esta camada usa somente frames RAW da CAM1 e Claude. Ela não participa de
presença, identidade, C1-C6, consolidação principal ou classificação Lean.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable, Iterable


log = logging.getLogger("kalidash.ponte_rolante")

PONTE_ROLANTE_LABEL = "operando_ponte_rolante"
PONTE_ROLANTE_TID = -6

FASES_PONTE_ROLANTE = {
    "engatando",
    "desengatando",
    "preparando_içamento",
    "içando",
    "baixando",
    "guiando_carga",
    "posicionando_carga",
    "controle_visivel",
    "nenhuma",
    "indeterminada",
}

PROMPT_PONTE_ROLANTE = """
Você está analisando uma SEQUÊNCIA CRONOLÓGICA de até 3 frames da CAM1 de
uma oficina de torneamento convencional.

Sua ÚNICA tarefa é decidir se existe evidência VISUAL de que a pessoa está
OPERANDO A PONTE ROLANTE / SISTEMA DE IÇAMENTO.

Definição positiva:
- engata ou desengata gancho, cinta, corrente ou linga na peça;
- manipula diretamente gancho/linga/cabo para preparar a carga;
- iça ou baixa uma peça;
- guia uma carga suspensa;
- posiciona uma peça suspensa no torno ou a retira usando o sistema de içamento.

O controle pendente/remoto visível ajuda, mas NÃO é obrigatório.

NÃO marque positivo só porque:
- existe ponte amarela no cenário;
- existe gancho parado;
- a pessoa está fora do posto;
- a pessoa está perto da ponte;
- a pessoa está perto de uma peça.

Se a evidência não for suficiente, seja conservador.

Retorne SOMENTE JSON:
{
  "operando_ponte_rolante": true | false | null,
  "fase": "engatando" | "desengatando" | "preparando_içamento" |
          "içando" | "baixando" | "guiando_carga" | "posicionando_carga" |
          "controle_visivel" | "nenhuma" | "indeterminada",
  "gancho_linga_visivel": true | false | null,
  "carga_suspensa": true | false | null,
  "evidencias_visuais": ["..."],
  "confianca": "alta" | "media" | "baixa"
}

Use null quando não der para decidir.
""".strip()


def _chamar_claude(frames_b64: list[str]) -> str:
    """Chama exclusivamente Claude, sem fallback para provider não validado."""
    from . import ai_provider

    return ai_provider.vision_call(
        frames_b64[0],
        PROMPT_PONTE_ROLANTE,
        imagens_extra=frames_b64[1:] or None,
        json_mode=True,
        max_tokens=700,
        temperatura=0.0,
        provedor="claude",
    )


def ponte_rolante_habilitada() -> bool:
    """Única flag da feature; o default seguro é desligado."""
    return os.environ.get("KV_PONTE_ROLANTE", "off").strip().lower() not in {
        "off", "0", "false", "",
    }


def _bool_ou_none(valor):
    return valor if valor is True or valor is False else None


def analisar_janela_ponte(
    frames_b64: list[str],
    chamar_vlm: Callable[[list[str]], object] | None = None,
) -> dict | None:
    """Retorna diagnóstico somente quando Claude afirma ``true`` literalmente.

    False, null, resposta vazia, JSON inválido e qualquer exceção abstêm.
    """
    if not frames_b64:
        return None
    try:
        bruto = (chamar_vlm or _chamar_claude)(frames_b64)
        decisao = bruto if isinstance(bruto, dict) else json.loads(str(bruto))
        if not isinstance(decisao, dict):
            return None
        if decisao.get("operando_ponte_rolante") is not True:
            return None
    except Exception as exc:  # noqa: BLE001 - fronteira fail-closed por contrato
        log.warning("[ponte-rolante] janela inconclusiva: %s", exc)
        return None

    fase = str(decisao.get("fase") or "indeterminada")
    if fase not in FASES_PONTE_ROLANTE:
        fase = "indeterminada"
    evidencias = decisao.get("evidencias_visuais")
    if not isinstance(evidencias, list):
        evidencias = []
    evidencias = [str(e).strip() for e in evidencias if str(e).strip()]
    confianca = str(decisao.get("confianca") or "baixa").lower()
    if confianca not in {"alta", "media", "baixa"}:
        confianca = "baixa"

    return {
        "operando_ponte_rolante": True,
        "fase": fase,
        "gancho_linga_visivel": _bool_ou_none(decisao.get("gancho_linga_visivel")),
        "carga_suspensa": _bool_ou_none(decisao.get("carga_suspensa")),
        "evidencias_visuais": evidencias,
        "confianca": confianca,
    }


def montar_janelas_ponte(frames_grade: Iterable[tuple[float, str | None]]) -> list[dict]:
    """Monta janelas deslizantes de até três posições da grade temporal."""
    grade = [(float(t), img) for t, img in frames_grade]
    janelas: list[dict] = []
    for indice in range(len(grade)):
        trecho = grade[indice : indice + 3]
        frames = [img for _, img in trecho if img]
        if not frames:
            continue
        janelas.append({
            "inicio_s": trecho[0][0],
            "fim_s": trecho[-1][0],
            "tempos_s": [t for t, _ in trecho],
            "frames_b64": frames,
            "n_frames": len(frames),
        })
    return janelas


def _extrair_frames_raw(
    video_path: str,
    intervalo_s: float,
    duracao_s: float | None = None,
) -> list[tuple[float, str | None]]:
    """Lê diretamente o vídeo CAM1; nenhuma ``Amostra.img_b64`` é reutilizada."""
    import cv2
    from .pipeline import frame_para_base64

    if intervalo_s <= 0:
        return []
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        return []
    try:
        if duracao_s is None:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duracao_s = n_frames / fps if fps > 0 else 0.0
        grade: list[tuple[float, str | None]] = []
        tempo_s = 0.0
        while tempo_s < float(duracao_s or 0.0):
            cap.set(cv2.CAP_PROP_POS_MSEC, tempo_s * 1000.0)
            ok, frame = cap.read()
            grade.append((round(tempo_s, 3), frame_para_base64(frame) if ok else None))
            tempo_s += float(intervalo_s)
        return grade
    finally:
        cap.release()


def detectar_janelas_ponte(
    video_path: str,
    intervalo_s: float,
    duracao_s: float | None = None,
    *,
    chamar_vlm: Callable[[list[str]], object] | None = None,
    extrair_frames: Callable[[str, float, float | None], list[tuple[float, str | None]]] | None = None,
) -> list[dict]:
    """Analisa todas as janelas RAW e devolve somente afirmações positivas."""
    if chamar_vlm is None and not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("[ponte-rolante] Claude indisponível; nenhuma afirmação positiva")
        return []
    try:
        grade = (extrair_frames or _extrair_frames_raw)(
            video_path, float(intervalo_s), duracao_s
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[ponte-rolante] leitura RAW indisponível: %s", exc)
        return []

    positivos: list[dict] = []
    for janela in montar_janelas_ponte(grade):
        decisao = analisar_janela_ponte(janela["frames_b64"], chamar_vlm)
        if decisao is None:
            continue
        positivos.append({
            "inicio_s": janela["inicio_s"],
            "fim_s": janela["fim_s"],
            "n_frames": janela["n_frames"],
            "tempos_s": janela["tempos_s"],
            **decisao,
        })
    return positivos


def agrupar_episodios_ponte(janelas_positivas: Iterable[dict]) -> list[dict]:
    """Une somente janelas positivas que se sobrepõem ou encostam."""
    janelas = sorted(
        (j for j in janelas_positivas if j.get("operando_ponte_rolante") is True),
        key=lambda j: float(j["inicio_s"]),
    )
    episodios: list[dict] = []
    for janela in janelas:
        inicio = float(janela["inicio_s"])
        fim = float(janela["fim_s"])
        if not episodios or inicio > episodios[-1]["fim_s"]:
            episodios.append({
                "inicio_s": inicio,
                "fim_s": fim,
                "n_janelas": 1,
                "fases_observadas": [janela.get("fase") or "indeterminada"],
                "evidencias_visuais": list(janela.get("evidencias_visuais") or []),
                "janelas_origem": [(inicio, fim)],
            })
            continue
        episodio = episodios[-1]
        episodio["fim_s"] = max(float(episodio["fim_s"]), fim)
        episodio["n_janelas"] += 1
        episodio["janelas_origem"].append((inicio, fim))
        episodio["fases_observadas"].append(janela.get("fase") or "indeterminada")
        episodio["evidencias_visuais"].extend(janela.get("evidencias_visuais") or [])

    for episodio in episodios:
        episodio["duracao_s"] = round(episodio["fim_s"] - episodio["inicio_s"], 3)
        episodio["fases_observadas"] = list(dict.fromkeys(episodio["fases_observadas"]))
        episodio["evidencias_visuais"] = list(dict.fromkeys(episodio["evidencias_visuais"]))
    return episodios


def persistir_episodios_ponte(
    sb,
    video_id: str,
    empresa: str,
    processo: str,
    episodios: Iterable[dict],
) -> int:
    """Persiste episódios paralelos como auditoria, sem catálogo ou Lean."""
    linhas: list[dict] = []
    for episodio in episodios:
        fases = list(episodio.get("fases_observadas") or [])
        evidencias = list(episodio.get("evidencias_visuais") or [])
        detalhes = []
        if fases:
            detalhes.append("fases observadas: " + ", ".join(str(f) for f in fases))
        if evidencias:
            detalhes.append("evidências visuais: " + "; ".join(str(e) for e in evidencias))
        descricao = "operação visual do sistema de içamento"
        if detalhes:
            descricao += " (" + " | ".join(detalhes) + ")"
        linhas.append({
            "video_id": video_id,
            "empresa": empresa,
            "processo": processo,
            "pessoa_track_id": PONTE_ROLANTE_TID,
            "comportamento_label": PONTE_ROLANTE_LABEL,
            "descricao_bruta": descricao,
            "tempo_inicio_s": float(episodio["inicio_s"]),
            "tempo_fim_s": float(episodio["fim_s"]),
            "n_amostras": int(episodio.get("n_janelas") or 1),
            "papel_pessoa": None,
            "origem_validacao": "ponte_rolante",
            # O mesmo mecanismo oficial dos eventos crus de auditoria:
            # não disputa o minuto, não entra em métrica, memória ou fila.
            "principal": False,
            "validado_humano": True,
            "categoria_lean": None,
            "categoria_lean_origem": None,
            "em_duvida": False,
            "descricao_invalida": False,
        })
    if not linhas:
        return 0
    sb.table("eventos").insert(linhas).execute()
    return len(linhas)


# ══════════════════════════════════════════════════════════════════════════
# Fase 113 — O SEGMENTO INTEIRO, QUANDO A PONTE É CERTA
#
# Decisão de negócio: com CERTEZA de operação de ponte rolante no segmento,
# o segmento inteiro vale como ponte rolante. Sem micro-ação, sem disputa
# minuto a minuto. EXCLUSIVO da ponte rolante.
#
# A marca que move número NÃO é o rótulo — `decidir_permanencia` ignora
# rótulo por contrato. É a origem `PONTE_SEGMENTO_ORIGEM`, lida lá num nível
# próprio, abaixo da correção humana.
#
# ⚠️ Fail-closed em todos os pontos: sem duração, sem janelas, sem confiança
# alta ou sem cobertura, o veredito é `certo=False` e nada é escrito.
# ══════════════════════════════════════════════════════════════════════════
PONTE_SEGMENTO_ORIGEM = "ponte_rolante_segmento"

# As mesmas origens mecânicas do pipeline: `validado_humano` nelas é só
# "fora da fila", nunca decisão de gente.
_ORIGENS_MECANICAS_PONTE = frozenset({"posto_vazio", "auditoria", "ponte_rolante"})


def _env_ligada_ponte(nome: str, padrao: str) -> bool:
    return os.environ.get(nome, padrao).strip().lower() not in {
        "off", "0", "false", "",
    }


def _env_num_ponte(nome: str, padrao: float, minimo: float, maximo: float) -> float:
    try:
        valor = float(os.environ.get(nome, padrao))
    except (TypeError, ValueError):
        return float(padrao)
    return valor if minimo <= valor <= maximo else float(padrao)


def ponte_segmento_habilitada() -> bool:
    """Chave própria, separada da detecção. Padrão DESLIGADO."""
    return _env_ligada_ponte("KV_PONTE_SEGMENTO", "off")


def segmento_certeza_ponte(
    janelas_positivas, episodios, duracao_s: float | None,
) -> dict:
    """Veredito por segmento — pura, sem banco, sem VLM.

    Devolve sempre o mesmo dicionário, com os números que sustentam o "sim"
    ou a lista de motivos do "não". O motivo é o que vai para o log: um
    veredito sem motivo legível é um veredito que ninguém audita.
    """
    min_janelas = int(_env_num_ponte("KV_PONTE_SEG_MIN_JANELAS", 3, 1, 1000))
    min_cobertura = _env_num_ponte("KV_PONTE_SEG_MIN_COBERTURA", 0.5, 0.0, 1.0)
    exige_alta = _env_ligada_ponte("KV_PONTE_SEG_EXIGE_ALTA", "on")

    janelas = [
        j for j in (janelas_positivas or [])
        if isinstance(j, dict) and j.get("operando_ponte_rolante") is True
    ]
    n_janelas = len(janelas)
    n_alta = sum(1 for j in janelas if str(j.get("confianca") or "") == "alta")
    tempo = 0.0
    for ep in (episodios or []):
        try:
            tempo += max(0.0, float(ep.get("duracao_s") or 0.0))
        except (TypeError, ValueError):
            continue
    try:
        dur = float(duracao_s or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    cobertura = (tempo / dur) if dur > 0 else 0.0

    motivos: list[str] = []
    if n_janelas < min_janelas:
        motivos.append(f"janelas positivas {n_janelas} < {min_janelas}")
    if exige_alta and n_alta < 1:
        motivos.append("nenhuma janela de confianca alta")
    if dur <= 0:
        motivos.append("duracao do segmento desconhecida")
    elif cobertura < min_cobertura:
        motivos.append(f"cobertura {cobertura:.0%} < {min_cobertura:.0%}")

    return {
        "certo": not motivos,
        "n_janelas": n_janelas,
        "n_janelas_alta": n_alta,
        "tempo_ponte_s": round(tempo, 1),
        "duracao_s": round(dur, 1),
        "cobertura": round(cobertura, 4),
        "min_janelas": min_janelas,
        "min_cobertura": min_cobertura,
        "motivo": "; ".join(motivos) if motivos else "certeza_ponte_rolante",
    }


def _decisao_humana_evento(e: dict) -> bool:
    """Espelha o nível 0 de `decidir_permanencia`. Gente ganha da máquina."""
    mecanico = (e.get("origem_validacao") or "") in _ORIGENS_MECANICAS_PONTE
    return (
        (not mecanico)
        and bool(e.get("validado_humano"))
        and bool(e.get("label_corrigido") or e.get("validacao_correto") is True)
    )


def garantir_catalogo_ponte(sb, empresa: str, processo: str) -> str:
    """O rótulo precisa existir no catálogo COM categoria — o painel calcula
    a categoria pelo catálogo, e rótulo ausente cai em desperdício."""
    try:
        atual = (
            sb.table("comportamentos")
            .select("id, categoria_lean, categoria_lean_origem")
            .eq("empresa", empresa).eq("processo", processo)
            .eq("label", PONTE_ROLANTE_LABEL).limit(1).execute().data
        ) or []
        if not atual:
            sb.table("comportamentos").insert({
                "empresa": empresa, "processo": processo,
                "label": PONTE_ROLANTE_LABEL,
                "descricao": "Operação do sistema de içamento (ponte rolante).",
                "categoria_lean": "valor_agregado",
                "categoria_lean_origem": "humano",
            }).execute()
            return "criado"
        if atual[0].get("categoria_lean") != "valor_agregado":
            log.warning(
                "[ponte-rolante/segmento] catalogo tem %s = %r, nao "
                "'valor_agregado' — o painel vai divergir do nivel novo. "
                "Corrija no catalogo; nao sobrescrevo escolha ja registrada.",
                PONTE_ROLANTE_LABEL, atual[0].get("categoria_lean"),
            )
            return "divergente"
        return "ok"
    except Exception as exc:  # noqa: BLE001
        log.warning("[ponte-rolante/segmento] catalogo nao conferido: %s", exc)
        return "indisponivel"


_CAMPOS_ALVO_PONTE = (
    "id, comportamento_label, principal, origem_validacao, validado_humano, "
    "label_corrigido, validacao_correto"
)


def _atualizar_lote(sb, ids: list, campos: dict) -> str | None:
    """UPDATE em lotes de 100. Devolve o erro, ou None se foi tudo."""
    for i in range(0, len(ids), 100):
        try:
            sb.table("eventos").update(campos).in_("id", ids[i : i + 100]).execute()
        except Exception as exc:  # noqa: BLE001
            return str(exc)
    return None


def marcar_segmento_como_ponte(
    sb, video_id: str, empresa: str, processo: str, veredito: dict,
) -> dict:
    """Marca o segmento inteiro — nos DOIS instrumentos.

    ⚠️ SÃO DOIS, e marcar só um não move número:
      · a TELA (cards, árvore) decide por `decidir_permanencia`, que lê os
        eventos PRINCIPAIS;
      · a PRODUTIVIDADE do posto decide por `classificar_observacao`, e
        `_eventos_do_instrumento` prefere os eventos CRUS (`principal=False`)
        sempre que o vídeo os tem — que é sempre, na V9.
    Por isso o principal troca de RÓTULO (é o que você lê) e o cru recebe a
    MARCA (é o que conta). Rótulo de evento cru não é tocado: ele é o registro
    de auditoria do que foi observado em cada amostra.

    Não toca em evento decidido por gente. Reprocessar é idempotente, e
    `label_original` nunca é sobrescrito.
    """
    resumo = {"video_id": str(video_id), "principais_marcados": 0,
              "crus_marcados": 0, "preservados_humanos": 0,
              "ja_marcados": 0, "erro": None}
    try:
        linhas = (
            sb.table("eventos").select(_CAMPOS_ALVO_PONTE)
            .eq("video_id", video_id).execute().data
        ) or []
    except Exception as exc:  # noqa: BLE001
        resumo["erro"] = f"leitura falhou: {exc}"
        return resumo

    por_label: dict[str, list] = {}
    so_marca: list = []
    for e in linhas:
        if _decisao_humana_evento(e):
            resumo["preservados_humanos"] += 1
            continue
        if e.get("principal") is False:
            so_marca.append(e["id"])
            continue
        if e.get("comportamento_label") == PONTE_ROLANTE_LABEL:
            # Já é ponte: recarimbar as marcas é idempotente, e trocar o
            # rótulo por ele mesmo apagaria o `label_original` de verdade.
            resumo["ja_marcados"] += 1
            so_marca.append(e["id"])
            continue
        por_label.setdefault(str(e.get("comportamento_label") or ""), []).append(e["id"])

    marca = {"categoria_lean": "valor_agregado",
             "categoria_lean_origem": PONTE_SEGMENTO_ORIGEM}

    for label_antigo, ids in por_label.items():
        campos = {**marca, "comportamento_label": PONTE_ROLANTE_LABEL,
                  "label_original": label_antigo}
        erro = _atualizar_lote(sb, ids, campos)
        if erro and "label_original" in erro:
            log.warning(
                "[ponte-rolante/segmento] sem a coluna `label_original` neste "
                "banco (%s) — marcando sem ela.", erro,
            )
            campos.pop("label_original")
            erro = _atualizar_lote(sb, ids, campos)
        if erro:
            resumo["erro"] = f"update principal falhou: {erro}"
        else:
            resumo["principais_marcados"] += len(ids)

    if so_marca:
        erro = _atualizar_lote(sb, so_marca, marca)
        if erro:
            resumo["erro"] = f"update cru falhou: {erro}"
        else:
            resumo["crus_marcados"] += len(so_marca)

    resumo["veredito"] = veredito
    return resumo
