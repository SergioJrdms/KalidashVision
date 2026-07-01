"""Provedor de IA multi-fornecedor com fallback (Fase 13).

Abstrai TODAS as chamadas de LLM/VLM do pipeline atrás de uma cadeia de fallback:

    Claude (principal) → GPT → Groq → Gemini

Cada chamada tenta os provedores nessa ordem; se um falha (erro/limite/timeout/
sem chave configurada), passa automaticamente pro próximo. Foco no Claude. Nada
do resto do pipeline muda — só o "módulo de provedor de inteligência".

3 "tiers" lógicos (batem com as 3 constantes GROQ_MODEL_* do pipeline):
  - VISION  : análise de frames (VLM) — alto volume, imagens já recortadas
  - ANALISE : raciocínio pesado (cluster/sugestões/insights/lean/perguntas)
  - RAPIDO  : chat/títulos do Prism — barato e rápido

Configuração (tudo por env, backend-only):
  Chaves ......... ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, GEMINI_API_KEY
  Ordem .......... KV_AI_FALLBACK_ORDER  (default "claude,gpt,groq,gemini")
  Forçar um só ... KV_AI_PROVIDER        (ex.: "claude")
  Modelos ........ KV_MODELO_<PROV>_<TIER>  (ex.: KV_MODELO_CLAUDE_ANALISE)
  Claude thinking KV_CLAUDE_THINKING="1"   (default desligado p/ extração barata)

Um provedor só entra na cadeia se a chave dele existir; sem chave, é pulado em
silêncio. As mensagens são sempre montadas no formato OpenAI (system/user/
assistant, content str ou lista de blocos text/image_url) e cada adapter traduz
pro seu SDK.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger("kalidash.ai")

# ═════════════════════════════════════════════════════════════════════════
# Tiers e modelos por provedor (todos sobrescrevíveis por env)
# ═════════════════════════════════════════════════════════════════════════
VISION = "vision"
ANALISE = "analise"
RAPIDO = "rapido"

# Racional (equilíbrio custo/eficiência p/ análise de vídeo industrial):
#   VISION  = alto volume (~20 chamadas/vídeo, imagens recortadas) → Haiku 4.5
#             (multimodal, barato/rápido: $1/$5 por 1M tokens)
#   ANALISE = raciocínio pesado → Sonnet 5 (melhor qualidade/custo: $3/$15,
#             intro $2/$10 até 2026-08-31)
#   RAPIDO  = chat/títulos → Haiku 4.5
# Groq mantém os modelos atuais. GPT/Gemini são defaults sensatos e
# sobrescrevíveis por env (bumpar quando quiser: gpt-5 / gemini-2.5 etc).
_DEFAULT_MODELOS: dict[str, dict[str, str]] = {
    "claude": {
        VISION:  "claude-haiku-4-5",
        ANALISE: "claude-sonnet-5",
        RAPIDO:  "claude-haiku-4-5",
    },
    "gpt": {
        VISION:  "gpt-4o-mini",
        ANALISE: "gpt-4o",
        RAPIDO:  "gpt-4o-mini",
    },
    "groq": {
        VISION:  "meta-llama/llama-4-scout-17b-16e-instruct",
        ANALISE: "openai/gpt-oss-120b",
        RAPIDO:  "llama-3.3-70b-versatile",
    },
    "gemini": {
        VISION:  "gemini-2.0-flash",
        ANALISE: "gemini-2.0-flash",
        RAPIDO:  "gemini-2.0-flash",
    },
}

_CHAVE_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_INSTRUCAO_JSON = (
    "Responda APENAS com um objeto JSON válido, sem nenhum texto antes ou depois "
    "e sem blocos de markdown (```)."
)


def _modelo(prov: str, tier: str) -> str:
    return os.environ.get(f"KV_MODELO_{prov.upper()}_{tier.upper()}", _DEFAULT_MODELOS[prov][tier])


def _tem_chave(prov: str) -> bool:
    if prov == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    return bool(os.environ.get(_CHAVE_ENV.get(prov, "")))


def _ordem() -> list[str]:
    forcado = os.environ.get("KV_AI_PROVIDER")
    if forcado:
        return [forcado.strip().lower()]
    raw = os.environ.get("KV_AI_FALLBACK_ORDER", "claude,gpt,groq,gemini")
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def provedores_disponiveis() -> list[str]:
    """Provedores na ordem de fallback que têm chave configurada."""
    return [p for p in _ordem() if p in _CHAVE_ENV and _tem_chave(p)]


# ═════════════════════════════════════════════════════════════════════════
# Clients (lazy singletons por provedor)
# ═════════════════════════════════════════════════════════════════════════
_CLIENTS: dict[str, Any] = {}
_CLIENT_LOCK = threading.Lock()


def _client(prov: str) -> Any:
    with _CLIENT_LOCK:
        if prov in _CLIENTS:
            return _CLIENTS[prov]
        c = _criar_client(prov)
        _CLIENTS[prov] = c
        return c


def _criar_client(prov: str) -> Any:
    if prov == "claude":
        import anthropic
        return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=0, timeout=90.0)
    if prov == "gpt":
        from openai import OpenAI
        return OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=90.0)
    if prov == "groq":
        from groq import Groq
        # max_retries=0: nós somos a autoridade de retry/cadência (groq_throttle).
        return Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=0, timeout=60.0)
    if prov == "gemini":
        from google import genai
        chave = os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"]
        return genai.Client(api_key=chave)
    raise ValueError(f"provedor de IA desconhecido: {prov}")


# ═════════════════════════════════════════════════════════════════════════
# Erros / retry genéricos
# ═════════════════════════════════════════════════════════════════════════
def _status(exc: BaseException) -> int | None:
    r = getattr(exc, "response", None)
    for cand in (
        getattr(r, "status_code", None),
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(exc, "code", None),
    ):
        if isinstance(cand, int):
            return cand
    return None


def _eh_4xx_nao_retryable(exc: BaseException) -> bool:
    """4xx que não vale retry (400/401/403/404/413...). 408/409/429 são retryable."""
    sc = _status(exc)
    return isinstance(sc, int) and 400 <= sc < 500 and sc not in (408, 409, 429)


def _retry(fn, rotulo: str, tentativas: int | None = None):
    """Retry curto p/ erros transitórios (429/5xx/timeout/conexão). 4xx
    definitivo levanta na hora (dispara o fallback pro próximo provedor)."""
    import random
    if tentativas is None:
        try:
            tentativas = int(os.environ.get("KV_AI_RETRIES", "2"))
        except Exception:
            tentativas = 2
    tentativas = max(1, tentativas)
    ultimo: BaseException | None = None
    for i in range(tentativas):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if _eh_4xx_nao_retryable(e):
                raise
            if i == tentativas - 1:
                raise
            espera = min(2.0 ** i, 20.0) * (0.75 + 0.5 * random.random())
            log.info(f"[ai] {rotulo} transitório ({e}); retry em {espera:.1f}s")
            time.sleep(espera)
    if ultimo:
        raise ultimo
    raise RuntimeError(f"[ai] {rotulo} falhou")


# ── Helpers específicos do Groq (limite diário / json inválido / retry-after) ──
def _groq_retry_after_s(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    try:
        headers = getattr(resp, "headers", None) or {}
        v = headers.get("retry-after") or headers.get("Retry-After")
        if not v:
            return None
        s = float(v)
        return s if s >= 0 else None
    except Exception:
        return None


def _groq_eh_limite_diario(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return ("per day" in msg) or ("(tpd)" in msg) or ("tokens per day" in msg)


def _groq_segundos_ate_reset(exc: BaseException) -> float:
    ra = _groq_retry_after_s(exc)
    if ra is not None:
        return ra
    import re
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)s", str(exc))
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2) or 0)
    return 3600.0


def _groq_eh_json_invalido(exc: BaseException) -> bool:
    return "json_validate_failed" in str(exc).lower()


def _groq_espera(tentativa: int, exc: BaseException) -> float:
    import random
    ra = _groq_retry_after_s(exc)
    if ra is not None:
        return min(ra, 90.0)
    base = min(2.0 ** tentativa, 60.0)
    return base * (0.75 + 0.5 * random.random())


# ═════════════════════════════════════════════════════════════════════════
# Utilidades de conversão de mensagens (formato OpenAI é o "canônico")
# ═════════════════════════════════════════════════════════════════════════
def _strip_json(txt: str | None) -> str:
    """Remove cercas de markdown (```json ... ```) que alguns modelos colocam
    mesmo pedindo JSON puro. O pipeline já faz parsing defensivo por cima."""
    if not txt:
        return txt or ""
    s = txt.strip()
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s[:4].lower() == "json":
            s = s[4:].strip()
    return s


def _bloco_para_claude(bloco: dict) -> dict:
    t = bloco.get("type")
    if t == "text":
        return {"type": "text", "text": bloco.get("text", "")}
    if t == "image_url":
        url = (bloco.get("image_url") or {}).get("url", "")
        if url.startswith("data:"):
            try:
                cab, dados = url.split(",", 1)
                mt = cab.split(";")[0].split(":", 1)[1] or "image/jpeg"
            except Exception:
                dados, mt = url, "image/jpeg"
            return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": dados}}
    return {"type": "text", "text": str(bloco)}


def _para_claude(mensagens: list[dict]) -> tuple[str | None, list[dict]]:
    sistema: list[str] = []
    msgs: list[dict] = []
    for m in mensagens:
        role = m.get("role")
        cont = m.get("content")
        if role == "system":
            if isinstance(cont, str):
                sistema.append(cont)
            continue
        if isinstance(cont, list):
            msgs.append({"role": role, "content": [_bloco_para_claude(b) for b in cont]})
        else:
            msgs.append({"role": role, "content": cont if isinstance(cont, str) else str(cont)})
    return ("\n\n".join(sistema).strip() or None), msgs


def _para_gemini(mensagens: list[dict], types: Any) -> tuple[str | None, list]:
    sistema: list[str] = []
    contents: list = []
    for m in mensagens:
        role = m.get("role")
        cont = m.get("content")
        if role == "system":
            if isinstance(cont, str):
                sistema.append(cont)
            continue
        grole = "model" if role == "assistant" else "user"
        partes: list = []
        if isinstance(cont, list):
            for b in cont:
                if b.get("type") == "text":
                    partes.append(types.Part.from_text(text=b.get("text", "")))
                elif b.get("type") == "image_url":
                    url = (b.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        try:
                            cab, dados = url.split(",", 1)
                            mt = cab.split(";")[0].split(":", 1)[1] or "image/jpeg"
                        except Exception:
                            dados, mt = url, "image/jpeg"
                        partes.append(types.Part.from_bytes(data=base64.b64decode(dados), mime_type=mt))
        else:
            partes.append(types.Part.from_text(text=cont if isinstance(cont, str) else str(cont)))
        contents.append(types.Content(role=grole, parts=partes))
    return ("\n\n".join(sistema).strip() or None), contents


# ═════════════════════════════════════════════════════════════════════════
# Adapters (um por provedor) — consomem mensagens no formato OpenAI
# ═════════════════════════════════════════════════════════════════════════
def _adapter_claude(modelo, mensagens, json_mode, max_tokens, temperatura):
    cli = _client("claude")
    system, msgs = _para_claude(mensagens)
    if json_mode:
        system = ((system or "") + "\n\n" + _INSTRUCAO_JSON).strip()
    kwargs: dict[str, Any] = dict(model=modelo, max_tokens=max_tokens, messages=msgs)
    if system:
        kwargs["system"] = system
    # Sonnet 5 / Opus rejeitam `temperature` (400). Haiku aceita.
    if "haiku" in modelo.lower():
        kwargs["temperature"] = temperatura
    elif os.environ.get("KV_CLAUDE_THINKING", "0") not in ("1", "true", "True"):
        # Extração barata/rápida: desliga o thinking nos modelos que o ligam
        # por padrão (Sonnet 5). Ligue via KV_CLAUDE_THINKING=1.
        kwargs["thinking"] = {"type": "disabled"}
    r = _retry(lambda: cli.messages.create(**kwargs), rotulo=f"claude:{modelo}")
    texto = ""
    for b in getattr(r, "content", []) or []:
        if getattr(b, "type", None) == "text":
            texto = b.text
            break
    return _strip_json(texto) if json_mode else texto


def _adapter_openai_like(prov, modelo, mensagens, json_mode, max_tokens, temperatura):
    cli = _client(prov)
    kwargs: dict[str, Any] = dict(model=modelo, messages=mensagens, temperature=temperatura, max_tokens=max_tokens)
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    r = _retry(lambda: cli.chat.completions.create(**kwargs), rotulo=f"{prov}:{modelo}")
    return r.choices[0].message.content


def _adapter_gpt(modelo, mensagens, json_mode, max_tokens, temperatura):
    return _adapter_openai_like("gpt", modelo, mensagens, json_mode, max_tokens, temperatura)


def _adapter_groq(modelo, mensagens, json_mode, max_tokens, temperatura):
    """Groq mantém retry + throttle (groq_throttle) e o fail-fast de limite
    diário da Fase 12; só é usado quando é o provedor da vez no fallback."""
    from . import groq_throttle
    cli = _client("groq")
    kwargs: dict[str, Any] = dict(
        model=modelo, messages=mensagens, temperature=temperatura, max_completion_tokens=max_tokens
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    texto_p = "".join(
        (m.get("content") if isinstance(m.get("content"), str) else "") for m in mensagens
    )
    try:
        retries = int(os.environ.get("KV_GROQ_RETRIES", "5"))
    except Exception:
        retries = 5
    for tentativa in range(retries):
        resta = groq_throttle.em_limite_diario(modelo)
        if resta > 0:
            raise RuntimeError(
                f"Groq: limite diário do modelo {modelo} atingido — retoma em ~{resta / 60:.0f}min."
            )
        try:
            groq_throttle.reserve(modelo, groq_throttle.estimar_tokens(texto_p, max_tokens))
        except Exception:
            pass
        try:
            r = cli.chat.completions.create(**kwargs)
            return r.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            if _groq_eh_limite_diario(e):
                groq_throttle.marcar_limite_diario(modelo, _groq_segundos_ate_reset(e))
                raise
            if _eh_4xx_nao_retryable(e) and not _groq_eh_json_invalido(e):
                raise
            if tentativa == retries - 1:
                raise
            espera = _groq_espera(tentativa, e)
            log.warning(f"[ai] groq:{modelo} falhou ({e}); retry em {espera:.1f}s ({tentativa + 1}/{retries})")
            time.sleep(espera)
    raise RuntimeError("[ai] groq falhou após retries")


def _adapter_gemini(modelo, mensagens, json_mode, max_tokens, temperatura):
    from google.genai import types
    cli = _client("gemini")
    system, contents = _para_gemini(mensagens, types)
    cfg: dict[str, Any] = dict(max_output_tokens=max_tokens, temperature=temperatura)
    if system:
        cfg["system_instruction"] = system
    if json_mode:
        cfg["response_mime_type"] = "application/json"

    def _chamar():
        return cli.models.generate_content(
            model=modelo, contents=contents, config=types.GenerateContentConfig(**cfg)
        )

    r = _retry(_chamar, rotulo=f"gemini:{modelo}")
    return getattr(r, "text", "") or ""


_ADAPTERS = {
    "claude": _adapter_claude,
    "gpt": _adapter_gpt,
    "groq": _adapter_groq,
    "gemini": _adapter_gemini,
}


# ═════════════════════════════════════════════════════════════════════════
# Dispatch com fallback
# ═════════════════════════════════════════════════════════════════════════
def _chamar(tier: str, mensagens: list[dict], json_mode: bool, max_tokens: int, temperatura: float) -> str:
    provs = provedores_disponiveis()
    if not provs:
        raise RuntimeError(
            "[ai] nenhum provedor de IA disponível — defina ANTHROPIC_API_KEY, "
            "OPENAI_API_KEY, GROQ_API_KEY ou GEMINI_API_KEY."
        )
    ultimo: BaseException | None = None
    for prov in provs:
        modelo = _modelo(prov, tier)
        try:
            return _ADAPTERS[prov](modelo, mensagens, json_mode, max_tokens, temperatura)
        except Exception as e:  # noqa: BLE001
            ultimo = e
            log.warning(f"[ai] {prov} ({modelo}) falhou ({e}) — tentando próximo provedor")
            continue
    raise RuntimeError(f"[ai] todos os provedores falharam no tier '{tier}': {ultimo}")


# ═════════════════════════════════════════════════════════════════════════
# API pública
# ═════════════════════════════════════════════════════════════════════════
def text_call(
    prompt: str,
    tier: str = ANALISE,
    *,
    system: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 2048,
    temperatura: float = 0.3,
) -> str:
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return _chamar(tier, msgs, json_mode, max_tokens, temperatura)


def vision_call(
    image_b64: str,
    prompt: str,
    *,
    imagens_extra: list[str] | None = None,
    json_mode: bool = True,
    max_tokens: int = 1024,
    temperatura: float = 0.2,
) -> str:
    conteudo: list[dict] = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
    ]
    for extra in (imagens_extra or []):
        if extra:
            conteudo.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{extra}"}})
    return _chamar(VISION, [{"role": "user", "content": conteudo}], json_mode, max_tokens, temperatura)


def chat_call(
    mensagens: list[dict],
    tier: str = ANALISE,
    *,
    json_mode: bool = False,
    max_tokens: int = 1500,
    temperatura: float = 0.4,
) -> str:
    """Chat multi-turno (system + histórico + user), formato OpenAI."""
    return _chamar(tier, list(mensagens), json_mode, max_tokens, temperatura)
