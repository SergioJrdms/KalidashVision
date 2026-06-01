import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

interface PrismCtx {
  processoId: string;
  aberto: boolean;
  abrir: () => void;
  fechar: () => void;
  toggle: () => void;
  conversaAtivaId: string | null;
  setConversaAtiva: (id: string | null) => void;
}

const Ctx = createContext<PrismCtx | null>(null);

export function PrismProvider({
  processoId,
  children,
}: {
  processoId: string;
  children: React.ReactNode;
}) {
  const [aberto, setAberto] = useState(false);
  const [conversaAtivaId, setConversaAtivaId] = useState<string | null>(null);

  // Trocou de processo: reseta tudo
  useEffect(() => {
    setConversaAtivaId(null);
    setAberto(false);
  }, [processoId]);

  const abrir = useCallback(() => setAberto(true), []);
  const fechar = useCallback(() => setAberto(false), []);
  const toggle = useCallback(() => setAberto((v) => !v), []);
  const setConversaAtiva = useCallback((id: string | null) => setConversaAtivaId(id), []);

  const value = useMemo(
    () => ({ processoId, aberto, abrir, fechar, toggle, conversaAtivaId, setConversaAtiva }),
    [processoId, aberto, abrir, fechar, toggle, conversaAtivaId, setConversaAtiva]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePrism(): PrismCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePrism precisa estar dentro de PrismProvider");
  return v;
}
