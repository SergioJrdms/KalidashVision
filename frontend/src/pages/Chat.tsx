import { FormEvent, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import { Button, Card, Spinner, Textarea } from "../components/UI";
import { ProcessoTabs } from "../components/Layout";

const EXEMPLOS = [
  "Onde estamos perdendo mais tempo?",
  "Quais as 3 maiores oportunidades de produtividade?",
  "O que foge do fluxo esperado do processo?",
];

interface Msg {
  role: "user" | "assistant";
  content: string;
}

export default function Chat() {
  const { id } = useParams<{ id: string }>();
  const [historico, setHistorico] = useState<Msg[]>([]);
  const [pergunta, setPergunta] = useState("");
  const [pensando, setPensando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function enviar(p?: string) {
    const txt = (p ?? pergunta).trim();
    if (!txt) return;
    setErro(null);
    setPergunta("");
    const next: Msg[] = [...historico, { role: "user", content: txt }];
    setHistorico(next);
    setPensando(true);
    try {
      const r = await api.processos.chat(id!, txt, historico);
      setHistorico([...next, { role: "assistant", content: r.resposta }]);
    } catch (e: unknown) {
      setErro((e as Error).message);
    } finally {
      setPensando(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    enviar();
  }

  return (
    <div>
      <ProcessoTabs />
      <Card className="p-6">
        <h2 className="font-semibold text-slate-900">Chat com os dados do processo</h2>
        <p className="text-sm text-slate-500 mt-1 mb-4">
          Pergunte em linguagem natural. A IA combina seus dados agregados com
          boas práticas de mercado (Lean / produtividade industrial).
        </p>

        <div className="flex flex-wrap gap-2 mb-4">
          <span className="text-xs text-slate-500 self-center mr-1">Sugestões:</span>
          {EXEMPLOS.map((e) => (
            <button
              key={e}
              onClick={() => setPergunta(e)}
              className="text-xs px-3 py-1.5 rounded-full bg-kv-purple-50 text-kv-purple-dark border border-kv-purple-200 hover:bg-kv-purple-100"
            >
              {e}
            </button>
          ))}
        </div>

        <div className="space-y-4 min-h-[180px] mb-4">
          {historico.length === 0 && (
            <p className="text-sm text-slate-400 italic">
              Faça uma pergunta para começar a conversa.
            </p>
          )}
          {historico.map((m, i) =>
            m.role === "user" ? (
              <div
                key={i}
                className="bg-kv-indigo-bg border border-kv-indigo/40 rounded-2xl p-4"
              >
                <div className="text-xs font-medium text-indigo-900 mb-1">Você</div>
                <div className="text-sm text-slate-800 whitespace-pre-wrap">{m.content}</div>
              </div>
            ) : (
              <div key={i} className="border-l-4 border-kv-purple pl-4">
                <div className="text-xs font-medium text-kv-purple-dark mb-1">Consultor</div>
                <div className="text-sm text-slate-800 prose-chat">
                  <ReactMarkdown>{m.content}</ReactMarkdown>
                </div>
              </div>
            )
          )}
          {pensando && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Spinner /> Pensando...
            </div>
          )}
          {erro && (
            <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
              {erro}
            </div>
          )}
        </div>

        <form onSubmit={submit} className="space-y-3">
          <Textarea
            value={pergunta}
            onChange={(e) => setPergunta(e.target.value)}
            rows={3}
            placeholder="Ex.: Onde estamos perdendo mais tempo?"
          />
          <div className="flex gap-2 justify-end">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setHistorico([])}
              disabled={pensando || historico.length === 0}
            >
              Limpar conversa
            </Button>
            <Button type="submit" disabled={pensando || !pergunta.trim()}>
              {pensando ? "Pensando..." : "Perguntar"}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
