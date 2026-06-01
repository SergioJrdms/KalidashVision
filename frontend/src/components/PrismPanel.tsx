import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import type { PrismConversa, PrismMensagem } from "../lib/types";
import { Spinner } from "./UI";
import { PrismAvatar } from "./PrismAvatar";
import { usePrism } from "./PrismProvider";

/** Chave estável da query, distinguindo escopo global vs processo. */
function escopoKey(processoId: string | null): string {
  return processoId ? `proc:${processoId}` : "global";
}

/** Botão flutuante (visível só quando o painel está fechado). */
export function PrismFAB() {
  const { aberto, abrir, escopo } = usePrism();
  if (aberto) return null;
  return (
    <button
      onClick={abrir}
      aria-label="Abrir Prism"
      className="fixed bottom-6 right-6 z-40 rounded-full bg-white shadow-lg border border-kv-purple-200 hover:shadow-xl transition p-2 flex items-center gap-2 pr-4"
      title="Conversar com o Prism"
    >
      <PrismAvatar size={36} />
      <span className="text-sm font-medium text-kv-purple-dark">
        {escopo === "global" ? "Prism · visão geral" : "Perguntar ao Prism"}
      </span>
    </button>
  );
}

/** Painel lateral deslizante. */
export function PrismPanel() {
  const { processoId, escopo, aberto, fechar, conversaAtivaId, setConversaAtiva } = usePrism();
  const qc = useQueryClient();
  const [historicoAberto, setHistoricoAberto] = useState(false);
  const ek = escopoKey(processoId);
  const prism = useMemo(() => api.prism(processoId), [processoId]);

  const conversas = useQuery({
    queryKey: ["prism-conversas", ek],
    queryFn: () => prism.listarConversas(),
    enabled: aberto,
  });

  const criarConversa = useMutation({
    mutationFn: () => prism.criarConversa(),
    onSuccess: (nova) => {
      qc.invalidateQueries({ queryKey: ["prism-conversas", ek] });
      setConversaAtiva(nova.id);
    },
  });

  useEffect(() => {
    if (!aberto) return;
    if (conversaAtivaId) return;
    if (conversas.isLoading) return;
    const lista = conversas.data || [];
    if (lista.length > 0) setConversaAtiva(lista[0].id);
    else if (!criarConversa.isPending) criarConversa.mutate();
  }, [aberto, conversaAtivaId, conversas.data, conversas.isLoading, criarConversa, setConversaAtiva]);

  return (
    <>
      <PrismFAB />
      {aberto && (
        <div
          className="fixed inset-y-0 right-0 z-50 w-full sm:w-[420px] bg-white shadow-2xl border-l border-slate-200 flex flex-col"
          role="dialog"
          aria-label="Prism"
        >
          <header className="flex items-center justify-between gap-2 p-3 border-b border-slate-200">
            <div className="flex items-center gap-2.5 min-w-0">
              <PrismAvatar size={36} />
              <div className="min-w-0">
                <div className="font-semibold text-slate-900 leading-tight">Prism</div>
                <div className="text-[11px] text-slate-500 leading-tight truncate">
                  {escopo === "global"
                    ? "visão geral da operação"
                    : "inteligência da sua operação"}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <IconBtn
                title="Nova conversa"
                onClick={() => {
                  setHistoricoAberto(false);
                  criarConversa.mutate();
                }}
              >
                +
              </IconBtn>
              <IconBtn
                title="Histórico de conversas"
                onClick={() => setHistoricoAberto((v) => !v)}
                active={historicoAberto}
              >
                ≡
              </IconBtn>
              <IconBtn title="Fechar" onClick={fechar}>
                ×
              </IconBtn>
            </div>
          </header>

          {historicoAberto && (
            <HistoricoConversas
              conversas={conversas.data || []}
              isLoading={conversas.isLoading}
              ativaId={conversaAtivaId}
              onAtivar={(id) => {
                setConversaAtiva(id);
                setHistoricoAberto(false);
              }}
              onRenomeada={() => qc.invalidateQueries({ queryKey: ["prism-conversas", ek] })}
              onExcluida={(id) => {
                qc.invalidateQueries({ queryKey: ["prism-conversas", ek] });
                if (conversaAtivaId === id) setConversaAtiva(null);
              }}
              prism={prism}
            />
          )}

          {conversaAtivaId ? (
            <ConversaAtiva
              key={conversaAtivaId}
              ek={ek}
              conversaId={conversaAtivaId}
              prism={prism}
              escopo={escopo}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-sm text-slate-400">
              <Spinner />
            </div>
          )}
        </div>
      )}
    </>
  );
}

type PrismApi = ReturnType<typeof api.prism>;

function IconBtn({
  children,
  onClick,
  title,
  active,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className={`h-8 w-8 rounded-md flex items-center justify-center text-lg leading-none transition ${
        active
          ? "bg-kv-purple-100 text-kv-purple-dark"
          : "text-slate-500 hover:text-slate-800 hover:bg-slate-100"
      }`}
    >
      {children}
    </button>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Histórico
// ════════════════════════════════════════════════════════════════════════
function HistoricoConversas({
  conversas,
  isLoading,
  ativaId,
  onAtivar,
  onRenomeada,
  onExcluida,
  prism,
}: {
  conversas: PrismConversa[];
  isLoading: boolean;
  ativaId: string | null;
  onAtivar: (id: string) => void;
  onRenomeada: () => void;
  onExcluida: (id: string) => void;
  prism: PrismApi;
}) {
  return (
    <div className="border-b border-slate-200 max-h-64 overflow-y-auto bg-slate-50">
      {isLoading && (
        <div className="p-3 flex items-center gap-2 text-xs text-slate-500">
          <Spinner /> carregando…
        </div>
      )}
      {!isLoading && conversas.length === 0 && (
        <div className="p-3 text-xs text-slate-500 italic">Nenhuma conversa ainda.</div>
      )}
      <ul>
        {conversas.map((c) => (
          <LinhaConversa
            key={c.id}
            c={c}
            ativa={c.id === ativaId}
            onAtivar={() => onAtivar(c.id)}
            onRenomeada={onRenomeada}
            onExcluida={() => onExcluida(c.id)}
            prism={prism}
          />
        ))}
      </ul>
    </div>
  );
}

function LinhaConversa({
  c,
  ativa,
  onAtivar,
  onRenomeada,
  onExcluida,
  prism,
}: {
  c: PrismConversa;
  ativa: boolean;
  onAtivar: () => void;
  onRenomeada: () => void;
  onExcluida: () => void;
  prism: PrismApi;
}) {
  const [editando, setEditando] = useState(false);
  const [titulo, setTitulo] = useState(c.titulo);
  const renomear = useMutation({
    mutationFn: (t: string) => prism.renomear(c.id, t),
    onSuccess: () => {
      setEditando(false);
      onRenomeada();
    },
  });
  const excluir = useMutation({
    mutationFn: () => prism.excluir(c.id),
    onSuccess: onExcluida,
  });

  return (
    <li
      className={`px-3 py-2 border-b border-slate-100 last:border-0 flex items-center gap-2 ${
        ativa ? "bg-kv-purple-100/50" : "hover:bg-white"
      }`}
    >
      {editando ? (
        <input
          autoFocus
          value={titulo}
          onChange={(e) => setTitulo(e.target.value)}
          onBlur={() => {
            const t = titulo.trim();
            if (t && t !== c.titulo) renomear.mutate(t);
            else setEditando(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            if (e.key === "Escape") {
              setTitulo(c.titulo);
              setEditando(false);
            }
          }}
          className="flex-1 text-sm bg-white border border-kv-purple-300 rounded px-2 py-1 outline-none"
        />
      ) : (
        <button onClick={onAtivar} className="flex-1 text-left min-w-0" title={c.titulo}>
          <div className={`text-sm truncate ${ativa ? "font-medium text-kv-purple-dark" : "text-slate-800"}`}>
            {c.titulo}
          </div>
          <div className="text-[10px] text-slate-400">
            {new Date(c.atualizada_em).toLocaleString("pt-BR", {
              day: "2-digit",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </div>
        </button>
      )}
      <button
        onClick={() => setEditando((v) => !v)}
        title="Renomear"
        className="text-slate-400 hover:text-kv-purple-dark text-xs px-1"
      >
        ✎
      </button>
      <button
        onClick={() => {
          if (window.confirm(`Excluir a conversa "${c.titulo}"?`)) excluir.mutate();
        }}
        title="Excluir conversa"
        className="text-slate-400 hover:text-red-600 text-xs px-1"
      >
        ✕
      </button>
    </li>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Conversa ativa
// ════════════════════════════════════════════════════════════════════════
function ConversaAtiva({
  ek,
  conversaId,
  prism,
  escopo,
}: {
  ek: string;
  conversaId: string;
  prism: PrismApi;
  escopo: "global" | "processo";
}) {
  const qc = useQueryClient();
  const conversa = useQuery({
    queryKey: ["prism-conversa", ek, conversaId],
    queryFn: () => prism.getConversa(conversaId),
  });
  const mensagens: PrismMensagem[] = conversa.data?.mensagens || [];
  const vazia = !conversa.isLoading && mensagens.length === 0;

  const sugestoes = useQuery({
    queryKey: ["prism-sugestoes", ek, conversaId],
    queryFn: () => prism.sugestoes([]),
    enabled: vazia,
    staleTime: 0,
  });

  const [pergunta, setPergunta] = useState("");
  const [pensando, setPensando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [mensagens.length, pensando]);

  const enviar = useMutation({
    mutationFn: (txt: string) => prism.enviarMensagem(conversaId, txt),
    onMutate: () => {
      setPensando(true);
      setErro(null);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["prism-conversa", ek, conversaId] });
      qc.invalidateQueries({ queryKey: ["prism-conversas", ek] });
    },
    onError: (e: Error) => setErro(e.message),
    onSettled: () => setPensando(false),
  });

  function submeter(e?: FormEvent) {
    e?.preventDefault();
    const t = pergunta.trim();
    if (!t || pensando) return;
    setPergunta("");
    enviar.mutate(t);
  }

  function aoTeclar(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submeter();
    }
  }

  return (
    <>
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-4 space-y-3">
        {vazia && <SaudacaoInicial escopo={escopo} />}
        {mensagens.map((m, i) =>
          m.papel === "user" ? (
            <BolhaUser key={m.id || i} conteudo={m.conteudo} />
          ) : (
            <BolhaPrism key={m.id || i} conteudo={m.conteudo} />
          )
        )}
        {pensando && <BolhaPensando />}
        {erro && (
          <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2">
            {erro}
          </div>
        )}

        {vazia && sugestoes.data?.sugestoes && sugestoes.data.sugestoes.length > 0 && (
          <div className="mt-4">
            <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1.5 font-medium">
              Sugestões
            </div>
            <div className="flex flex-wrap gap-1.5">
              {sugestoes.data.sugestoes.map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    setPergunta(s);
                    setTimeout(() => submeter(), 0);
                  }}
                  className="text-xs px-2.5 py-1 rounded-full bg-kv-purple-50 text-kv-purple-dark border border-kv-purple-200 hover:bg-kv-purple-100 text-left"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <form onSubmit={submeter} className="border-t border-slate-200 p-3 space-y-2">
        <textarea
          value={pergunta}
          onChange={(e) => setPergunta(e.target.value)}
          onKeyDown={aoTeclar}
          rows={2}
          placeholder={
            escopo === "global"
              ? "Pergunte sobre o conjunto dos seus processos…"
              : "Pergunte ao Prism sobre sua operação…"
          }
          disabled={pensando}
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-kv-purple focus:ring-2 focus:ring-kv-purple/20 outline-none resize-none disabled:bg-slate-50"
        />
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-slate-400">Enter envia · Shift+Enter quebra linha</span>
          <button
            type="submit"
            disabled={pensando || !pergunta.trim()}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-kv-purple text-white hover:bg-kv-purple-dark disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Enviar
          </button>
        </div>
      </form>
    </>
  );
}

function SaudacaoInicial({ escopo }: { escopo: "global" | "processo" }) {
  return (
    <div className="flex items-start gap-2.5">
      <PrismAvatar size={32} />
      <div className="bg-kv-purple-50 border border-kv-purple-100 rounded-2xl rounded-tl-sm px-3.5 py-2.5 text-sm text-slate-800 leading-relaxed">
        {escopo === "global" ? (
          <>
            Sou o <b>Prism</b>. Estou vendo <b>todos os seus processos</b>. Posso
            comparar, priorizar e achar padrões entre eles.
          </>
        ) : (
          <>
            Sou o <b>Prism</b>. Posso te ajudar a entender onde o tempo da sua
            operação está indo, achar gargalos e ler seus indicadores.
          </>
        )}
        <br />
        <span className="text-xs text-slate-500">Sobre o que você quer falar?</span>
      </div>
    </div>
  );
}

function BolhaUser({ conteudo }: { conteudo: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] bg-kv-indigo-bg border border-indigo-200/60 rounded-2xl rounded-tr-sm px-3.5 py-2 text-sm text-slate-800 whitespace-pre-wrap">
        {conteudo}
      </div>
    </div>
  );
}

function BolhaPrism({ conteudo }: { conteudo: string }) {
  return (
    <div className="flex items-start gap-2.5">
      <PrismAvatar size={28} />
      <div className="max-w-[85%] bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-3.5 py-2.5 text-sm text-slate-800 prose-chat">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{conteudo}</ReactMarkdown>
      </div>
    </div>
  );
}

function BolhaPensando() {
  return (
    <div className="flex items-start gap-2.5">
      <PrismAvatar size={28} />
      <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-3.5 py-2 text-sm text-slate-500 flex items-center gap-2">
        <Spinner className="h-3.5 w-3.5" />
        <span>Prism está pensando…</span>
      </div>
    </div>
  );
}
