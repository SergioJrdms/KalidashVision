import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import type { PrismConversa, PrismMensagem } from "../lib/types";
import { Btn, Icon, Spinner } from "./UIKit";
import { PrismAvatar } from "./PrismAvatar";
import { usePrism } from "./PrismProvider";

function escopoKey(processoId: string | null): string {
  return processoId ? `proc:${processoId}` : "global";
}

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

  if (!aberto) return <PrismFAB />;

  return (
    <>
      <div
        onClick={fechar}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(26,16,49,.35)",
          backdropFilter: "blur(2px)",
          zIndex: 60,
        }}
      />
      <aside
        role="dialog"
        aria-label="Prism"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "100%",
          maxWidth: 440,
          background: "#fff",
          borderLeft: "1px solid var(--line)",
          boxShadow: "0 -40px 60px -20px rgba(26,16,49,.45)",
          zIndex: 70,
          display: "flex",
          flexDirection: "column",
        }}
        className="anim-fadeup"
      >
        <header
          className="row"
          style={{
            padding: "12px 14px",
            borderBottom: "1px solid var(--line)",
            gap: 10,
            alignItems: "center",
          }}
        >
          <PrismAvatar size={38} ring />
          <div className="grow col" style={{ gap: 0, lineHeight: 1.15, minWidth: 0 }}>
            <div className="font-display" style={{ fontSize: 15, fontWeight: 800, color: "var(--ink)" }}>
              Prism
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              {escopo === "global" ? "visão geral da operação" : "inteligência deste processo"}
            </div>
          </div>
          <IconBtn title="Nova conversa" icon="plus" onClick={() => {
            setHistoricoAberto(false);
            criarConversa.mutate();
          }} />
          <IconBtn
            title="Histórico"
            icon="list"
            onClick={() => setHistoricoAberto((v) => !v)}
            active={historicoAberto}
          />
          <IconBtn title="Fechar" icon="x" onClick={fechar} />
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
          <div className="center" style={{ flex: 1 }}>
            <Spinner size={20} />
          </div>
        )}
      </aside>
    </>
  );
}

export function PrismFAB() {
  const { aberto, abrir, escopo } = usePrism();
  if (aberto) return null;
  return (
    <button
      onClick={abrir}
      aria-label="Abrir Prism"
      title="Conversar com o Prism"
      style={{
        position: "fixed",
        bottom: 22,
        right: 22,
        zIndex: 40,
        background: "#fff",
        borderRadius: 999,
        border: "1px solid var(--p-200)",
        boxShadow: "var(--glow-lg)",
        padding: "6px 16px 6px 6px",
        display: "flex",
        alignItems: "center",
        gap: 10,
        cursor: "pointer",
      }}
    >
      <PrismAvatar size={36} />
      <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--accent-deep)" }}>
        {escopo === "global" ? "Prism · visão geral" : "Perguntar ao Prism"}
      </span>
    </button>
  );
}

function IconBtn({
  icon,
  onClick,
  title,
  active,
}: {
  icon: string;
  onClick: () => void;
  title: string;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      style={{
        width: 30,
        height: 30,
        borderRadius: 8,
        background: active ? "var(--accent-soft)" : "transparent",
        color: active ? "var(--accent-deep)" : "var(--muted)",
        border: "1px solid " + (active ? "var(--p-200)" : "transparent"),
        display: "grid",
        placeItems: "center",
      }}
    >
      <Icon name={icon} size={15} color="currentColor" />
    </button>
  );
}

// ════════════════════════════════════════════════════════════════════════
type PrismApi = ReturnType<typeof api.prism>;

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
    <div style={{ borderBottom: "1px solid var(--line)", background: "var(--soft)", maxHeight: 280, overflowY: "auto" }}>
      {isLoading && (
        <div className="row gap2" style={{ padding: 12, color: "var(--muted)", fontSize: 12 }}>
          <Spinner size={14} /> carregando…
        </div>
      )}
      {!isLoading && conversas.length === 0 && (
        <div style={{ padding: 12, color: "var(--muted)", fontSize: 12, fontStyle: "italic" }}>
          Nenhuma conversa ainda.
        </div>
      )}
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
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
      className="row gap2"
      style={{
        padding: "8px 12px",
        borderBottom: "1px solid var(--line-2)",
        background: ativa ? "var(--accent-soft)" : "transparent",
      }}
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
          className="field"
          style={{ padding: "4px 8px", fontSize: 13 }}
        />
      ) : (
        <button
          onClick={onAtivar}
          className="grow col click"
          style={{ background: 0, border: 0, textAlign: "left", gap: 1, minWidth: 0 }}
          title={c.titulo}
        >
          <span
            className="truncate"
            style={{
              fontSize: 13,
              fontWeight: ativa ? 700 : 500,
              color: ativa ? "var(--accent-deep)" : "var(--ink)",
            }}
          >
            {c.titulo}
          </span>
          <span style={{ fontSize: 10, color: "var(--faint)" }}>
            {new Date(c.atualizada_em).toLocaleString("pt-BR", {
              day: "2-digit",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </button>
      )}
      <button onClick={() => setEditando((v) => !v)} title="Renomear" style={{ background: 0, border: 0, color: "var(--muted)" }}>
        <Icon name="pencil" size={12} />
      </button>
      <button
        onClick={() => {
          if (window.confirm(`Excluir a conversa "${c.titulo}"?`)) excluir.mutate();
        }}
        title="Excluir conversa"
        style={{ background: 0, border: 0, color: "var(--muted)" }}
      >
        <Icon name="trash-2" size={12} />
      </button>
    </li>
  );
}

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
      <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "16px 14px" }} className="col gap3">
        {vazia && <Saudacao escopo={escopo} />}
        {mensagens.map((m, i) =>
          m.papel === "user" ? (
            <BolhaUser key={m.id || i} conteudo={m.conteudo} />
          ) : (
            <BolhaPrism key={m.id || i} conteudo={m.conteudo} />
          )
        )}
        {pensando && <BolhaPensando />}
        {erro && (
          <div
            style={{
              fontSize: 12,
              color: "var(--desp)",
              background: "var(--desp-bg)",
              border: "1px solid rgba(229,72,77,.2)",
              borderRadius: 8,
              padding: "8px 10px",
            }}
          >
            {erro}
          </div>
        )}
        {vazia && sugestoes.data?.sugestoes && sugestoes.data.sugestoes.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div
              style={{
                fontSize: 10.5,
                color: "var(--muted)",
                textTransform: "uppercase",
                letterSpacing: ".08em",
                fontWeight: 700,
                marginBottom: 6,
              }}
            >
              Sugestões
            </div>
            <div className="row gap1 wrap">
              {sugestoes.data.sugestoes.map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    setPergunta(s);
                    window.setTimeout(() => submeter(), 0);
                  }}
                  className="chip click"
                  style={{ textAlign: "left", lineHeight: 1.3 }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <form
        onSubmit={submeter}
        className="col gap2"
        style={{ borderTop: "1px solid var(--line)", padding: "12px 14px" }}
      >
        <textarea
          value={pergunta}
          onChange={(e) => setPergunta(e.target.value)}
          onKeyDown={aoTeclar}
          rows={2}
          placeholder={
            escopo === "global"
              ? "Pergunte sobre o conjunto dos seus processos…"
              : "Pergunte ao Prism sobre esta operação…"
          }
          disabled={pensando}
          className="field"
          style={{ resize: "none" }}
        />
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span style={{ fontSize: 10.5, color: "var(--faint)" }}>Enter envia · Shift+Enter quebra linha</span>
          <Btn type="submit" size="sm" disabled={pensando || !pergunta.trim()} icon="send">
            Enviar
          </Btn>
        </div>
      </form>
    </>
  );
}

function Saudacao({ escopo }: { escopo: "global" | "processo" }) {
  return (
    <div className="row gap2">
      <PrismAvatar size={30} />
      <div
        style={{
          background: "var(--accent-soft)",
          border: "1px solid var(--p-100)",
          borderRadius: 14,
          borderTopLeftRadius: 4,
          padding: "10px 12px",
          fontSize: 13,
          color: "var(--text)",
          lineHeight: 1.5,
        }}
      >
        {escopo === "global" ? (
          <>
            Sou o <b>Prism</b>. Estou olhando <b>todos os seus processos</b>. Posso
            comparar, priorizar e achar padrões entre eles.
          </>
        ) : (
          <>
            Sou o <b>Prism</b>. Posso te ajudar a entender onde o tempo da sua
            operação vai, achar gargalos e ler seus indicadores.
          </>
        )}
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
          Sobre o que você quer falar?
        </div>
      </div>
    </div>
  );
}

function BolhaUser({ conteudo }: { conteudo: string }) {
  return (
    <div className="row" style={{ justifyContent: "flex-end" }}>
      <div
        style={{
          maxWidth: "85%",
          background: "var(--ink)",
          color: "#fff",
          borderRadius: 14,
          borderTopRightRadius: 4,
          padding: "9px 12px",
          fontSize: 13.5,
          whiteSpace: "pre-wrap",
          lineHeight: 1.45,
        }}
      >
        {conteudo}
      </div>
    </div>
  );
}

function BolhaPrism({ conteudo }: { conteudo: string }) {
  return (
    <div className="row gap2" style={{ alignItems: "flex-start" }}>
      <PrismAvatar size={26} />
      <div
        style={{
          maxWidth: "85%",
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 14,
          borderTopLeftRadius: 4,
          padding: "10px 12px",
          fontSize: 13.5,
          color: "var(--text)",
        }}
        className="prose-chat"
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{conteudo}</ReactMarkdown>
      </div>
    </div>
  );
}

function BolhaPensando() {
  return (
    <div className="row gap2">
      <PrismAvatar size={26} />
      <div
        className="row gap2"
        style={{
          background: "#fff",
          border: "1px solid var(--line)",
          borderRadius: 14,
          borderTopLeftRadius: 4,
          padding: "8px 12px",
          fontSize: 12.5,
          color: "var(--muted)",
        }}
      >
        <Spinner size={12} />
        <span>Prism está pensando…</span>
      </div>
    </div>
  );
}
