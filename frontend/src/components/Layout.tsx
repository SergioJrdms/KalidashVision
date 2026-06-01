import { Link, NavLink, Outlet, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../hooks/useAuth";
import { supabase } from "../lib/supabase";
import { api } from "../lib/api";
import { Button, Spinner } from "./UI";
import { PrismProvider } from "./PrismProvider";
import { PrismPanel } from "./PrismPanel";

export function AppShell() {
  const { user, empresa } = useAuth();
  const navigate = useNavigate();

  async function signOut() {
    await supabase.auth.signOut();
    navigate("/login");
  }

  return (
    <div className="min-h-full bg-slate-50">
      <header className="bg-white border-b border-slate-200 sticky top-0 z-20">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
          <button
            onClick={() => navigate("/processos")}
            className="flex items-center gap-2"
            title="Ir para a lista de processos"
          >
            <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-kv-purple to-kv-purple-dark flex items-center justify-center text-white font-bold">
              K
            </div>
            <span className="font-semibold text-slate-900">Kalidash Vision</span>
          </button>
          <div className="flex items-center gap-3 text-sm">
            <div className="text-right leading-tight">
              <div className="text-slate-900 font-medium">{empresa}</div>
              <div className="text-slate-500 text-xs">{user?.email}</div>
            </div>
            <Button variant="ghost" onClick={signOut}>Sair</Button>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}

/** Layout interno de "estou dentro de um processo": breadcrumb + tabs. */
export function ProcessoShell() {
  const { id } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const setupMode = params.get("novo") === "1";
  const { data, isLoading } = useQuery({
    queryKey: ["processo", id],
    queryFn: () => api.processos.detalhe(id!),
    enabled: !!id,
    staleTime: 30_000,
  });

  // Modo "setup inicial do processo" — esconde tabs/breadcrumb pra
  // tela ficar limpa enquanto o usuário escreve a descrição inicial.
  if (setupMode) return <Outlet />;

  return (
    <PrismProvider processoId={id!}>
      <div>
        <div className="mb-3 flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-2 text-sm">
            <Link
              to="/processos"
              className="inline-flex items-center gap-1 text-slate-500 hover:text-kv-purple-dark"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M15 18l-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Processos
            </Link>
            <span className="text-slate-300">/</span>
            <span className="text-slate-900 font-medium truncate max-w-md">
              {isLoading ? <Spinner className="h-3 w-3" /> : data?.processo || "—"}
            </span>
          </div>
          <div className="text-xs text-slate-400">
            {data?.n_videos ?? 0} vídeo(s) analisado(s)
          </div>
        </div>
        <ProcessoTabs />
        <Outlet />
      </div>
      <PrismPanel />
    </PrismProvider>
  );
}

export function ProcessoTabs() {
  const { id } = useParams();
  const dashboard = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api.processos.dashboard(id!),
    enabled: !!id,
    staleTime: 15_000,
  });
  const pendentesValid =
    (dashboard.data?.eventos_pendentes ?? 0) +
    (dashboard.data?.perguntas_pendentes ?? 0);

  const tabs = [
    { to: `/processos/${id}/dashboard`, label: "Dashboard", count: 0 },
    { to: `/processos/${id}/upload`, label: "Novo vídeo", count: 0 },
    { to: `/processos/${id}/validacao`, label: "Validação", count: pendentesValid },
    { to: `/processos/${id}/eventos`, label: "Eventos", count: 0 },
    { to: `/processos/${id}/descricao`, label: "Descrição", count: 0 },
  ];
  return (
    <div className="border-b border-slate-200 mb-6">
      <nav className="flex gap-1 -mb-px">
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              `px-4 py-2.5 text-sm font-medium border-b-2 transition flex items-center gap-2 ${
                isActive
                  ? "border-kv-purple text-kv-purple-dark"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`
            }
          >
            {t.label}
            {t.count > 0 && (
              <span className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full text-[10px] font-semibold bg-kv-purple text-white">
                {t.count > 99 ? "99+" : t.count}
              </span>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
