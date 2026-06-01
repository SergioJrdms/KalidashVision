import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useMatch } from "react-router-dom";

type Escopo = "global" | "processo";

interface PrismCtx {
  escopo: Escopo;
  processoId: string | null; // null no modo global
  aberto: boolean;
  abrir: () => void;
  fechar: () => void;
  toggle: () => void;
  conversaAtivaId: string | null;
  setConversaAtiva: (id: string | null) => void;
}

const Ctx = createContext<PrismCtx | null>(null);

/**
 * Provider único, montado no AppShell. Detecta o escopo pela rota:
 * - dentro de /processos/:id/... → modo processo
 * - em /processos e demais rotas  → modo global (visão de portfólio)
 */
export function PrismProvider({ children }: { children: React.ReactNode }) {
  const match = useMatch("/processos/:id/*");
  const processoId = match?.params.id ?? null;
  const escopo: Escopo = processoId ? "processo" : "global";

  const [aberto, setAberto] = useState(false);
  const [conversaAtivaId, setConversaAtivaId] = useState<string | null>(null);

  // Mudou o contexto (entrou/saiu de um processo, ou trocou de processo):
  // reseta a conversa ativa para o Prism recarregar no escopo certo.
  useEffect(() => {
    setConversaAtivaId(null);
  }, [processoId]);

  const abrir = useCallback(() => setAberto(true), []);
  const fechar = useCallback(() => setAberto(false), []);
  const toggle = useCallback(() => setAberto((v) => !v), []);
  const setConversaAtiva = useCallback((id: string | null) => setConversaAtivaId(id), []);

  const value = useMemo(
    () => ({ escopo, processoId, aberto, abrir, fechar, toggle, conversaAtivaId, setConversaAtiva }),
    [escopo, processoId, aberto, abrir, fechar, toggle, conversaAtivaId, setConversaAtiva]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePrism(): PrismCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePrism precisa estar dentro de PrismProvider");
  return v;
}
