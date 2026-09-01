"""Compatibilidade do adapter Claude com runtimes que rejeitam temperature.

Executar: python -X utf8 tests_ai_provider_claude_compat.py
"""
from __future__ import annotations

from types import SimpleNamespace

from backend import ai_provider as ai


ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


class _ClaudeMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if "temperature" in kwargs:
            raise TypeError("Messages.create() got an unexpected keyword argument 'temperature'")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"ok": true}')],
            usage=SimpleNamespace(input_tokens=11, output_tokens=5),
        )


class _ClaudeClient:
    def __init__(self):
        self.messages = _ClaudeMessages()


class _Completions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )


class _OpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())


cliente_claude = _ClaudeClient()
cliente_gpt = _OpenAIClient()
cliente_real = ai._client
retry_real = ai._retry

try:
    ai._client = lambda prov: cliente_claude if prov == "claude" else cliente_gpt
    ai._retry = lambda fn, rotulo, tentativas=None: fn()

    print("[1] Claude não recebe temperature")
    texto, uso = ai._adapter_claude(
        "claude-haiku-4-5",
        [{"role": "user", "content": "retorne JSON"}],
        True,
        200,
        0.0,
    )
    check("runtime incompatível não quebra a chamada", texto == '{"ok": true}', texto)
    check("temperature não foi enviado", "temperature" not in cliente_claude.messages.kwargs,
          cliente_claude.messages.kwargs)
    check("uso continua contabilizável", uso == {"in": 11, "out": 5}, uso)

    print("\n[2] Outros providers preservam o contrato")
    texto_gpt, _ = ai._adapter_gpt(
        "gpt-4o-mini",
        [{"role": "user", "content": "ok"}],
        False,
        100,
        0.4,
    )
    check("GPT continua funcional", texto_gpt == "ok", texto_gpt)
    check("GPT continua recebendo temperature", cliente_gpt.chat.completions.kwargs["temperature"] == 0.4,
          cliente_gpt.chat.completions.kwargs)
finally:
    ai._client = cliente_real
    ai._retry = retry_real


print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
