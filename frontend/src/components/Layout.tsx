import { Link, Outlet, useLocation, useMatch, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../hooks/useAuth";
import { supabase } from "../lib/supabase";
import { api } from "../lib/api";
import { Badge, Icon, Wordmark, iniciaisDe } from "./UIKit";
import { PrismProvider, usePrism } from "./PrismProvider";
import { PrismPanel } from "./PrismPanel";

// ════════════════════════════════════════════════════════════════════════
// Sidebar contextual
// ════════════════════════════════════════════════════════════════════════
type NavItem = { tab: string; label: string; icon: string; badge?: number; to?: string };
function Sidebar({ proc }: { proc?: { id: string; processo: string; eventos_pendentes?: number } | null }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { abrir: abrirPrism } = usePrism();
  const inProc = !!proc;
  const port: NavItem[] = [
    { tab: "/processos", label: "Processos", icon: "layout-grid", to: "/processos" },
  ];
  const proci: NavItem[] = inProc
    ? [
        { tab: "dashboard", label: "Dashboard", icon: "layout-dashboard", to: `/processos/${proc.id}/dashboard` },
        { tab: "validacao", label: "Validação", icon: "git-pull-request-arrow", to: `/processos/${proc.id}/validacao`, badge: proc.eventos_pendentes },
        { tab: "eventos", label: "Eventos", icon: "table-2", to: `/processos/${proc.id}/eventos` },
        { tab: "padroes", label: "Padrões", icon: "activity", to: `/processos/${proc.id}/padroes` },
        { tab: "upload", label: "Novo vídeo", icon: "upload", to: `/processos/${proc.id}/upload` },
        { tab: "descricao", label: "Descrição", icon: "file-text", to: `/processos/${proc.id}/descricao` },
      ]
    : [];

  return (
    <aside
      className="col"
      style={{
        width: 256,
        flex: "none",
        background: "#fff",
        borderRight: "1px solid var(--line)",
        height: "100vh",
        position: "sticky",
        top: 0,
        padding: "16px 14px",
      }}
    >
      <div style={{ padding: "2px 6px 14px" }}>
        <Wordmark size={17} />
      </div>

      {inProc && proc && (
        <button
          onClick={() => navigate("/processos")}
          className="row gap2 click"
          style={{
            width: "100%",
            textAlign: "left",
            border: "1px solid var(--line)",
            background: "var(--soft)",
            borderRadius: 12,
            padding: "9px 10px",
            marginBottom: 10,
          }}
        >
          <Icon name="chevron-left" size={16} color="var(--muted)" />
          <div className="grow col" style={{ gap: 1 }}>
            <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: ".06em" }}>
              Processo
            </span>
            <span className="truncate" style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>
              {proc.processo}
            </span>
          </div>
        </button>
      )}

      <NavSection title="Portfólio">
        {port.map((it) => (
          <NavLinkSide key={it.tab} item={it} active={pathname === it.to} onClick={() => it.to && navigate(it.to)} />
        ))}
        <NavLinkSide
          item={{ tab: "_prism_global", label: "Visão geral do Prism", icon: "sparkles" }}
          active={false}
          onClick={abrirPrism}
        />
      </NavSection>

      {inProc && (
        <NavSection title="Neste processo">
          {proci.map((it) => (
            <NavLinkSide key={it.tab} item={it} active={pathname === it.to} onClick={() => it.to && navigate(it.to)} />
          ))}
        </NavSection>
      )}

      <div className="grow" />

      <button
        onClick={abrirPrism}
        className="row gap2 click"
        style={{
          border: "1px solid var(--p-200)",
          background: "linear-gradient(135deg, #fff, var(--accent-soft))",
          borderRadius: 12,
          padding: "10px 12px",
          textAlign: "left",
        }}
      >
        <span className="prism-badge" style={{ width: 26, height: 26 }}>
          <img src="/prism.png" alt="Prism" />
        </span>
        <div className="grow col" style={{ gap: 1 }}>
          <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>Falar com o Prism</span>
          <span style={{ fontSize: 10.5, color: "var(--muted)" }}>
            {inProc ? "neste processo" : "visão geral"}
          </span>
        </div>
        <Icon name="message-square" size={14} color="var(--muted)" />
      </button>
    </aside>
  );
}

function NavSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 10, marginBottom: 4 }}>
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          color: "var(--muted)",
          textTransform: "uppercase",
          letterSpacing: ".1em",
          padding: "6px 10px",
        }}
      >
        {title}
      </div>
      <div className="col" style={{ gap: 2 }}>
        {children}
      </div>
    </div>
  );
}

function NavLinkSide({ item, active, onClick }: { item: NavItem; active: boolean; onClick?: () => void }) {
  return (
    <button
      onClick={onClick}
      className="row gap2 click"
      style={{
        border: "1px solid transparent",
        background: active ? "var(--accent-soft)" : "transparent",
        color: active ? "var(--accent-deep)" : "var(--text)",
        borderRadius: 10,
        padding: "8px 10px",
        textAlign: "left",
        fontSize: 13.5,
        fontWeight: active ? 700 : 500,
        position: "relative",
      }}
    >
      <Icon name={item.icon} size={16} color={active ? "var(--accent)" : "var(--muted)"} />
      <span className="grow truncate">{item.label}</span>
      {item.badge != null && item.badge > 0 && (
        <span
          style={{
            background: active ? "var(--accent)" : "var(--p-100)",
            color: active ? "#fff" : "var(--accent-deep)",
            borderRadius: 999,
            fontSize: 10.5,
            fontWeight: 700,
            padding: "1px 6px",
            minWidth: 18,
            textAlign: "center",
          }}
        >
          {item.badge}
        </span>
      )}
    </button>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Topbar
// ════════════════════════════════════════════════════════════════════════
function Topbar({
  proc,
  action,
}: {
  proc?: { id: string; processo: string; area?: string | null } | null;
  action?: React.ReactNode;
}) {
  const { user, empresa } = useAuth();
  const navigate = useNavigate();
  const nome = (user?.user_metadata?.nome as string) || user?.email || "Usuário";
  const iniciais = iniciaisDe(nome);

  async function signOut() {
    await supabase.auth.signOut();
    navigate("/login");
  }

  return (
    <header
      className="row"
      style={{
        background: "#fff",
        borderBottom: "1px solid var(--line)",
        padding: "10px 22px",
        gap: 14,
        position: "sticky",
        top: 0,
        zIndex: 10,
      }}
    >
      <div className="row gap2" style={{ flex: 1, minWidth: 0 }}>
        {proc ? (
          <>
            <Link to="/processos" className="row gap1" style={{ color: "var(--muted)", fontSize: 13 }}>
              <Icon name="arrow-left" size={14} />
              <span>Processos</span>
            </Link>
            <span style={{ color: "var(--faint)" }}>/</span>
            <span className="truncate" style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }} title={proc.processo}>
              {proc.processo}
            </span>
            {proc.area && (
              <Badge tone="purple" className="font-mono" >
                {proc.area}
              </Badge>
            )}
          </>
        ) : (
          <div className="col" style={{ gap: 0 }}>
            <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: ".08em" }}>
              Empresa
            </span>
            <span style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>{empresa || "—"}</span>
          </div>
        )}
      </div>

      {action}

      <button
        onClick={signOut}
        title="Sair"
        className="row gap2 click"
        style={{
          background: "var(--accent-soft)",
          color: "var(--accent-deep)",
          border: "1px solid var(--line)",
          borderRadius: 999,
          padding: "5px 10px 5px 5px",
          fontSize: 12.5,
          fontWeight: 700,
        }}
      >
        <span
          style={{
            width: 26,
            height: 26,
            borderRadius: "50%",
            background: "#fff",
            color: "var(--accent-deep)",
            display: "grid",
            placeItems: "center",
            fontSize: 11,
            border: "1px solid var(--p-200)",
          }}
        >
          {iniciais}
        </span>
        <span className="truncate" style={{ maxWidth: 130 }}>
          {nome}
        </span>
        <Icon name="log-out" size={13} />
      </button>
    </header>
  );
}

// ════════════════════════════════════════════════════════════════════════
// AppShell — único shell. Prism deduz escopo pela rota.
// ════════════════════════════════════════════════════════════════════════
export function AppShell() {
  return (
    <PrismProvider>
      <Shell />
      <PrismPanel />
    </PrismProvider>
  );
}

function Shell() {
  const match = useMatch("/processos/:id/*");
  const processoId = match?.params.id;
  const { data: processo } = useQuery({
    queryKey: ["processo", processoId],
    queryFn: () => api.processos.detalhe(processoId!),
    enabled: !!processoId,
    staleTime: 30_000,
  });

  return (
    <div className="row" style={{ alignItems: "stretch", minHeight: "100vh" }}>
      <Sidebar proc={processo as { id: string; processo: string; eventos_pendentes?: number } | null} />
      <div className="grow col" style={{ minWidth: 0 }}>
        <Topbar proc={processo as { id: string; processo: string; area?: string | null } | null} />
        <main style={{ padding: "26px 28px 60px", flex: 1 }}>
          <div key={processoId || "root"} className="anim-fadeup">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

