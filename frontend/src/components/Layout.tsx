import { NavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { supabase } from "../lib/supabase";
import { Button } from "./UI";

export function AppShell() {
  const { user, empresa } = useAuth();
  const navigate = useNavigate();

  async function signOut() {
    await supabase.auth.signOut();
    navigate("/login");
  }

  return (
    <div className="min-h-full bg-slate-50">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
          <button
            onClick={() => navigate("/processos")}
            className="flex items-center gap-2"
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

export function ProcessoTabs() {
  const { id } = useParams();
  const tabs = [
    { to: `/processos/${id}/dashboard`, label: "Dashboard" },
    { to: `/processos/${id}/upload`, label: "Novo vídeo" },
    { to: `/processos/${id}/validacao`, label: "Validação" },
    { to: `/processos/${id}/chat`, label: "Chat" },
    { to: `/processos/${id}/descricao`, label: "Descrição" },
  ];
  return (
    <div className="border-b border-slate-200 mb-6">
      <nav className="flex gap-1 -mb-px">
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              `px-4 py-2.5 text-sm font-medium border-b-2 transition ${
                isActive
                  ? "border-kv-purple text-kv-purple-dark"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
