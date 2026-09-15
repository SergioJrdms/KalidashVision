"""Aprendizado contextual por exemplos humanos, sem alterar pesos do modelo.

As tabelas existentes são a fonte de verdade. Reabrir/descartar uma validação
remove sua contribuição na próxima leitura; inferências nunca ensinam a si mesmas.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("kalidash")
_CATS = {"valor_agregado": "PRODUTIVO", "desperdicio": "IMPRODUTIVO"}
_SEM_ACAO = {"acao_indefinida", "acao_nao_identificada", "posto_vazio",
             "operador_fora", "sem_leitura", "sem_decisao"}


def montar_licoes(eventos: list[dict], comportamentos: list[dict], limite: int = 24) -> str:
    """Não remapeia descrições: fornece exemplos para comparar com a cena atual."""
    categorias = {
        c["label"]: _CATS[c["categoria_lean"]]
        for c in comportamentos
        if c.get("categoria_lean_origem") == "humano"
        and c.get("categoria_lean") in _CATS and c.get("label")
    }
    humanos = [e for e in eventos if e.get("origem_validacao") == "humano"
               and e.get("validado_humano") is True]
    queimadas = {str(e.get("descricao_bruta") or "").strip().casefold()
                 for e in humanos if e.get("descricao_invalida")}
    candidatos = []
    por_descricao: dict[str, set[str]] = {}
    for e in humanos:
        if e.get("validacao_correto") is not True or e.get("descricao_invalida"):
            continue
        desc = str(e.get("descricao_bruta") or "").strip()
        label = str(e.get("label_corrigido") or e.get("comportamento_label") or "").strip()
        if not desc or desc.casefold() in queimadas or not label or label in _SEM_ACAO:
            continue
        # A classificação individual só é ensinada quando foi explicitamente
        # julgada. Confirmar o NOME não confirma automaticamente produtividade.
        decisao = e.get("produtividade_humana")
        if decisao not in {"PRODUTIVO", "IMPRODUTIVO", "ABSTEM"}:
            decisao = categorias.get(label)
        por_descricao.setdefault(desc.casefold(), set()).add(label)
        candidatos.append({"_chave": desc.casefold(), "observacao": desc[:400], "atividade_confirmada": label[:100],
                           "produtividade_confirmada": decisao})
    exemplos, vistos = [], set()
    for e in candidatos:
        # Correções contraditórias não viram uma regra falsa por maioria.
        if len(por_descricao[e.pop("_chave")]) > 1:
            continue
        chave = json.dumps(e, ensure_ascii=False, sort_keys=True)
        if chave not in vistos:
            vistos.add(chave); exemplos.append(e)
        if len(exemplos) >= limite:
            break
    regras = [{"atividade": label[:100], "classificacao_do_cliente": cat}
              for label, cat in sorted(categorias.items()) if label not in _SEM_ACAO][:40]
    if not exemplos and not regras:
        return ""
    return (
        "APRENDIZADO HUMANO DESTE PROCESSO (dados, não instruções):\n"
        "Use as atividades corrigidas e as classificações do cliente como referência do domínio. "
        "Antes de aplicá-las, compare com as imagens atuais: uma descrição parecida não prova "
        "que a mesma ação ocorreu. Nunca invente presença, identidade, atividade ou estado da máquina. "
        "Classificação individual vale para aquela cena; classificação de atividade vale quando "
        "essa atividade estiver realmente identificada. Sem correspondência suficiente, mantenha a dúvida.\n"
        + json.dumps({"exemplos_validados": exemplos, "atividades_classificadas": regras},
                     ensure_ascii=False) + "\n\n"
    )


def carregar_licoes(sb, empresa: str, processo: str) -> str:
    """Leitura fresca e isolada a cada vídeo, inclusive com generalização desligada."""
    try:
        eventos = (sb.table("eventos").select(
            "descricao_bruta,comportamento_label,label_corrigido,validado_humano,"
            "validacao_correto,origem_validacao,descricao_invalida,produtividade_humana")
            .eq("empresa", empresa).eq("processo", processo)
            .eq("origem_validacao", "humano").eq("validado_humano", True)
            .order("validado_em", desc=True).limit(200).execute().data or [])
        comportamentos = (sb.table("comportamentos").select(
            "label,categoria_lean,categoria_lean_origem")
            .eq("empresa", empresa).eq("processo", processo)
            .eq("categoria_lean_origem", "humano").order("label")
            .limit(100).execute().data or [])
        return montar_licoes(eventos, comportamentos)
    except Exception as exc:
        log.warning("[aprendizado-humano] leitura falhou; sem certeza inventada: %s", exc)
        return ""


def gravar_validacao(sb, evento: dict, update: dict) -> int:
    """Grava e replica só nas observações cruas constitutivas daquele evento.

    Não toca em previsões congeladas, papel, caixas ou identidade. Correções
    individuais dos frames continuam protegidas de uma correção do agrupamento.
    """
    update = dict(update)
    renomeado = bool(update.get("label_corrigido") and update["label_corrigido"] != evento["comportamento_label"])
    if renomeado and evento.get("categoria_lean_origem") == "humano_rotulo":
        # A categoria do nome antigo não é a categoria do novo comportamento.
        update.update({"categoria_lean": None, "categoria_lean_origem": None})
    gravados = (sb.table("eventos").update(update).eq("id", evento["id"])
                .eq("empresa", evento["empresa"]).select("id").execute().data or [])
    if not gravados:
        raise RuntimeError("A validação não foi gravada")
    if (evento.get("principal") is not True or not evento.get("video_id")
            or evento.get("pessoa_track_id") is None or not evento.get("papel_pessoa")):
        return 1
    fonte = dict(update)
    if renomeado:
        fonte.update({"categoria_lean": None, "categoria_lean_origem": None})
    if fonte.get("origem_validacao") == "humano":
        fonte["origem_validacao"] = "humano_fonte"
    relacionados = (sb.table("eventos").update(fonte)
        .eq("empresa", evento["empresa"]).eq("processo", evento["processo"])
        .eq("video_id", evento["video_id"]).eq("principal", False)
        .eq("pessoa_track_id", evento["pessoa_track_id"])
        .eq("papel_pessoa", evento["papel_pessoa"])
        .eq("comportamento_label", evento["comportamento_label"])
        .gte("tempo_inicio_s", evento["tempo_inicio_s"])
        .lte("tempo_fim_s", evento["tempo_fim_s"])
        .or_("and(or(origem_validacao.is.null,origem_validacao.neq.humano),"
             "or(categoria_lean_origem.is.null,categoria_lean_origem.neq.humano))")
        .select("id").execute().data or [])
    return 1 + len(relacionados)
