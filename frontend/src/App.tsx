// ============================================================
// App — roteamento por estado (porte de main.jsx), auth real,
// tweaks fixos: Foco Único · Denso · Vibrante.
// ============================================================
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "./hooks/useAuth";
import { supabase } from "./lib/supabase";
import { api } from "./lib/api";
import { mapProcessos, mapHeader, type ProcHeaderMock } from "./lib/adapt";
import { iniciaisDe } from "./design/helpers";
import { ToastHost, Btn } from "./design/ui";
import { Sidebar, Topbar, type Route, type Go } from "./design/Shell";
import Login from "./pages/Login";
import Processos from "./pages/Processos";
import Dashboard from "./pages/Dashboard";
import Validacao from "./pages/Validacao";
import Eventos from "./pages/Eventos";
import Padroes from "./pages/Padroes";
import { Upload, Descricao } from "./pages/Extras";
import { PrismPanel } from "./pages/PrismChat";

export type Tweaks = { validacao: "fila" | "cards"; dashboard: "minimal" | "equilibrado" | "denso"; tom: "sobrio" | "vibrante" };
const TWEAKS: Tweaks = { validacao: "fila", dashboard: "denso", tom: "vibrante" };

export default function App() {
  const { user, loading } = useAuth();

  useEffect(() => {
    document.documentElement.classList.toggle("tone-vibrant", TWEAKS.tom === "vibrante");
  }, []);

  if (loading) {
    return (
      <div className="center" style={{ minHeight: "100vh" }}>
        <span className="spin" style={{ width: 28, height: 28, border: "3px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} />
      </div>
    );
  }
  if (!user) {
    return (
      <>
        <Login />
        <ToastHost />
      </>
    );
  }
  return <AppShell />;
}

function AppShell() {
  const { user, empresa } = useAuth();
  const [route, setRoute] = useState<Route>({ screen: "processos", processId: null, tab: "dashboard" });
  const [prism, setPrism] = useState<{ open: boolean; scope: "global" | "processo" }>({ open: false, scope: "global" });

  function go(screen: Route["screen"], processId: string | null = null, tab: Route["tab"] = "dashboard") {
    setPrism((p) => ({ ...p, open: false }));
    setRoute({ screen, processId, tab });
    window.scrollTo(0, 0);
  }
  const goTyped: Go = go;

  function openPrism() {
    setPrism({ open: true, scope: route.screen === "processo" ? "processo" : "global" });
  }

  async function signOut() {
    await supabase.auth.signOut();
    setRoute({ screen: "processos", processId: null, tab: "dashboard" });
  }

  // Lista de processos (sidebar)
  const procsQ = useQuery({ queryKey: ["processos"], queryFn: () => api.processos.list() });
  const processos = useMemo(() => mapProcessos(procsQ.data || []), [procsQ.data]);

  // Cabeçalho do processo ativo
  const procQ = useQuery({
    queryKey: ["processo", route.processId],
    queryFn: () => api.processos.detalhe(route.processId!),
    enabled: route.screen === "processo" && !!route.processId,
  });
  const proc: ProcHeaderMock | null = procQ.data ? mapHeader(procQ.data) : null;

  const usuario = {
    nome: (user?.user_metadata?.nome as string) || user?.email?.split("@")[0] || "Usuário",
    cargo: (user?.user_metadata?.cargo as string) || "Gestor de Operações",
    iniciais: iniciaisDe((user?.user_metadata?.nome as string) || user?.email || "U"),
  };

  // Conteúdo
  let content: ReactNode = null;
  if (route.screen === "processos") content = <Processos go={goTyped} />;
  else if (route.screen === "processo") {
    if (!proc) {
      content = (
        <div className="center" style={{ padding: 80 }}>
          <span className="spin" style={{ width: 24, height: 24, border: "3px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} />
        </div>
      );
    } else if (route.tab === "dashboard") content = <Dashboard proc={proc} go={goTyped} t={TWEAKS} />;
    else if (route.tab === "validacao") content = <Validacao proc={proc} go={goTyped} t={TWEAKS} />;
    else if (route.tab === "eventos") content = <Eventos proc={proc} />;
    else if (route.tab === "padroes") content = <Padroes proc={proc} go={goTyped} />;
    else if (route.tab === "upload") content = <Upload proc={proc} go={goTyped} />;
    else if (route.tab === "descricao") content = <Descricao proc={proc} go={goTyped} />;
  }
  if (!content) content = <Processos go={goTyped} />;

  const topAction =
    route.screen === "processo" && proc && route.tab !== "upload" ? (
      <Btn size="sm" icon="upload" onClick={() => go("processo", proc.id, "upload")}>Novo vídeo</Btn>
    ) : null;

  return (
    <div className="row" style={{ alignItems: "stretch", minHeight: "100vh" }}>
      <Sidebar route={route} go={goTyped} proc={proc} processos={processos} empresa={empresa} usuario={usuario} onOpenPrism={openPrism} onSignOut={signOut} />
      <div className="grow col" style={{ minWidth: 0 }}>
        <Topbar route={route} proc={proc} go={goTyped} onOpenPrism={openPrism} action={topAction} />
        <main style={{ padding: "26px 28px 60px", flex: 1 }}>
          <div key={route.screen + route.processId + route.tab} className="anim-fadeup">
            {content}
          </div>
        </main>
      </div>

      <PrismPanel open={prism.open} onClose={() => setPrism((p) => ({ ...p, open: false }))} scope={prism.scope} proc={proc} />
      <ToastHost />
    </div>
  );
}
