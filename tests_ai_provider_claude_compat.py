"""Compatibilidade cirúrgica de ``temperature`` no adapter Claude.

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


def _resposta_claude():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"ok": true}')],
        usage=SimpleNamespace(input_tokens=11, output_tokens=5),
    )


class _ClaudeMessages:
    def __init__(self, modo="aceita"):
        self.modo = modo
        self.chamadas = []

    def create(self, **kwargs):
        self.chamadas.append(dict(kwargs))
        if self.modo == "rejeita_temperature" and "temperature" in kwargs:
            raise TypeError(
                "Messages.create() got an unexpected keyword argument 'temperature'"
            )
        if self.modo == "outro_typeerror":
            raise TypeError("temperature must be a number")
        if self.modo == "erro_rede":
            raise RuntimeError("network unavailable")
        return _resposta_claude()


class _ClaudeClient:
    def __init__(self, modo="aceita"):
        self.messages = _ClaudeMessages(modo)


cliente_real = ai._client
retry_real = ai._retry
tem_chave_real = ai._tem_chave
disponiveis_real = ai.provedores_disponiveis
acima_limite_real = ai._acima_do_limite
registrar_real = ai.registrar
adapter_claude_real = ai._ADAPTERS["claude"]
adapter_gpt_real = ai._ADAPTERS["gpt"]

try:
    ai._retry = lambda fn, rotulo, tentativas=None: fn()

    print("[1] Runtime compatível recebe temperature normalmente")
    cliente = _ClaudeClient()
    ai._client = lambda _prov: cliente
    texto, uso = ai._adapter_claude(
        "claude-haiku-4-5",
        [{"role": "user", "content": "retorne JSON"}],
        True,
        200,
        0.35,
    )
    check("Haiku recebe temperature", cliente.messages.chamadas[0]["temperature"] == 0.35,
          cliente.messages.chamadas)
    check("chamada compatível não é repetida", len(cliente.messages.chamadas) == 1,
          cliente.messages.chamadas)
    check("resposta e uso são preservados",
          texto == '{"ok": true}' and uso == {"in": 11, "out": 5}, (texto, uso))

    print("\n[2] Só a rejeição específica repete sem temperature")
    cliente = _ClaudeClient("rejeita_temperature")
    ai._client = lambda _prov: cliente
    texto, _ = ai._adapter_claude(
        "claude-haiku-4-5",
        [{"role": "user", "content": "ok"}],
        False,
        100,
        0.2,
    )
    check("fallback compatível conclui a chamada", texto == '{"ok": true}', texto)
    check("primeira tentativa contém temperature e a segunda não",
          len(cliente.messages.chamadas) == 2
          and "temperature" in cliente.messages.chamadas[0]
          and "temperature" not in cliente.messages.chamadas[1],
          cliente.messages.chamadas)

    print("\n[3] Erros alheios não acionam o fallback especial")
    cliente = _ClaudeClient("outro_typeerror")
    ai._client = lambda _prov: cliente
    try:
        ai._adapter_claude(
            "claude-haiku-4-5", [{"role": "user", "content": "ok"}],
            False, 100, 0.2,
        )
        typeerror_propagou = False
    except TypeError as exc:
        typeerror_propagou = str(exc) == "temperature must be a number"
    check("outro TypeError segue o fluxo normal", typeerror_propagou, cliente.messages.chamadas)
    check("outro TypeError fez uma única chamada", len(cliente.messages.chamadas) == 1,
          cliente.messages.chamadas)

    cliente = _ClaudeClient("erro_rede")
    ai._client = lambda _prov: cliente
    try:
        ai._adapter_claude(
            "claude-haiku-4-5", [{"role": "user", "content": "ok"}],
            False, 100, 0.2,
        )
        erro_rede_propagou = False
    except RuntimeError as exc:
        erro_rede_propagou = str(exc) == "network unavailable"
    check("exceção não relacionada segue o fluxo normal", erro_rede_propagou,
          cliente.messages.chamadas)
    check("exceção não relacionada fez uma única chamada", len(cliente.messages.chamadas) == 1,
          cliente.messages.chamadas)

    print("\n[4] Sonnet/Opus preservam a regra sem temperature")
    cliente = _ClaudeClient()
    ai._client = lambda _prov: cliente
    ai._adapter_claude(
        "claude-sonnet-4-5", [{"role": "user", "content": "ok"}],
        False, 100, 0.7,
    )
    check("Sonnet continua sem temperature", "temperature" not in cliente.messages.chamadas[0],
          cliente.messages.chamadas)
    check("configuração de thinking continua presente", "thinking" in cliente.messages.chamadas[0],
          cliente.messages.chamadas)

    print("\n[5] Fallback global antigo e seleção Claude por chamada")
    chamados = []

    def falha_claude(*_args, **_kwargs):
        chamados.append("claude")
        raise RuntimeError("claude indisponível")

    def sucesso(prov):
        def _adapter(*_args, **_kwargs):
            chamados.append(prov)
            return '{"provedor": "' + prov + '"}', {"in": 0, "out": 0}
        return _adapter

    ai.provedores_disponiveis = lambda: ["claude", "gpt"]
    ai._tem_chave = lambda _prov: True
    ai._acima_do_limite = lambda _prov: False
    ai.registrar = lambda *args, **kwargs: None
    ai._ADAPTERS["claude"] = falha_claude
    ai._ADAPTERS["gpt"] = sucesso("gpt")
    resposta = ai.vision_call("IMG", "prompt")
    check("chamada antiga mantém fallback Claude → GPT", chamados == ["claude", "gpt"], chamados)
    check("fallback devolve a resposta seguinte", resposta == '{"provedor": "gpt"}', resposta)

    chamados.clear()
    ai._ADAPTERS["claude"] = sucesso("claude")
    resposta = ai.vision_call("IMG", "prompt", provedor="claude")
    check("seleção por chamada usa somente Claude", chamados == ["claude"], chamados)
    check("resposta do Claude é preservada", resposta == '{"provedor": "claude"}', resposta)
finally:
    ai._client = cliente_real
    ai._retry = retry_real
    ai._tem_chave = tem_chave_real
    ai.provedores_disponiveis = disponiveis_real
    ai._acima_do_limite = acima_limite_real
    ai.registrar = registrar_real
    ai._ADAPTERS["claude"] = adapter_claude_real
    ai._ADAPTERS["gpt"] = adapter_gpt_real


print(f"\n{ok} ok · {fail} falha(s)")
raise SystemExit(1 if fail else 0)
