// ============================================================
// Shell: Sidebar (contextual) + Topbar. Porte fiel de shell.jsx.
// ============================================================
import type { CSSProperties, ReactNode } from "react";
import { Icon, Prism, Wordmark, MaturityMeter } from "./ui";
import type { ProcMock, ProcHeaderMock } from "../lib/adapt";

export type Screen = "login" | "processos" | "processo" | "ajuda";
export type Tab = "dashboard" | "validacao" | "eventos" | "padroes" | "upload" | "descricao" | "configuracoes";
export type Route = { screen: Screen; processId: string | null; tab: Tab };
export type Go = (screen: Screen, processId?: string | null, tab?: Tab) => void;

export function Sidebar({
  route,
  go,
  proc,
  processos,
  empresa,
  usuario,
  onOpenPrism,
  onSignOut,
  isMobile = false,
  open = false,
  onClose,
}: {
  route: Route;
  go: Go;
  proc?: ProcHeaderMock | null;
  processos: ProcMock[];
  empresa: string;
  usuario: { nome: string; cargo: string; iniciais: string };
  onOpenPrism: () => void;
  onSignOut: () => void;
  isMobile?: boolean;
  open?: boolean;
  onClose?: () => void;
}) {
  const inProc = route.screen === "processo";
  // No mobile, a sidebar vira um drawer off-canvas; no desktop, coluna fixa.
  const asideStyle: CSSProperties = isMobile
    ? {
        width: "min(280px, 86vw)",
        background: "#fff",
        borderRight: "1px solid var(--line)",
        height: "100dvh",
        position: "fixed",
        top: 0,
        left: 0,
        zIndex: 95,
        padding: "16px 14px",
        transform: open ? "translateX(0)" : "translateX(-100%)",
        transition: "transform .28s cubic-bezier(.3,.8,.3,1)",
        boxShadow: open ? "12px 0 40px -12px rgba(26,16,49,.35)" : "none",
        overflowY: "auto",
      }
    : { width: 256, flex: "none", background: "#fff", borderRight: "1px solid var(--line)", height: "100vh", position: "sticky", top: 0, padding: "16px 14px" };
  const portfolioNav = [
    { tab: "processos", label: "Processos", icon: "layout-grid" as const },
    { tab: "_prism", label: "Visão geral do Prism", icon: "sparkles" as const, onClick: onOpenPrism },
  ];
  const procNav = [
    { tab: "dashboard", label: "Dashboard", icon: "layout-dashboard" },
    { tab: "validacao", label: "Validação", icon: "git-pull-request-arrow", badge: proc?.pendencias },
    { tab: "eventos", label: "Eventos", icon: "table-2" },
    { tab: "padroes", label: "Padrões", icon: "activity" },
    { tab: "upload", label: "Novo vídeo", icon: "upload" },
    { tab: "descricao", label: "Descrição", icon: "file-text" },
    { tab: "configuracoes", label: "Configurações", icon: "settings" },
  ];

  return (
    <>
      {isMobile && (
        <div
          onClick={onClose}
          style={{ position: "fixed", inset: 0, background: "rgba(26,16,49,.32)", backdropFilter: "blur(2px)", zIndex: 94, opacity: open ? 1 : 0, pointerEvents: open ? "auto" : "none", transition: "opacity .25s" }}
        />
      )}
      <aside className="col" style={asideStyle}>
      <div className="row" style={{ padding: "2px 6px 14px", justifyContent: "space-between" }}>
        <Wordmark size={18} empresa={empresa} />
        {isMobile && (
          <button onClick={onClose} title="Fechar" className="center" style={{ width: 32, height: 32, borderRadius: 8, border: "none", background: "transparent", color: "var(--muted)", flex: "none" }}>
            <Icon name="x" size={18} />
          </button>
        )}
      </div>

      {inProc && proc && (
        <button onClick={() => go("processos")} className="row gap2 click" style={{ width: "100%", textAlign: "left", border: "1px solid var(--line)", background: "var(--soft)", borderRadius: 12, padding: "9px 10px", marginBottom: 10 }}>
          <Icon name="chevron-left" size={16} color="var(--muted)" />
          <div className="grow col" style={{ gap: 1 }}>
            <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 600 }}>Processo</span>
            <span className="truncate" style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{proc.nome}</span>
          </div>
          <MaturityMeter pct={proc.maturidade} size={34} compact />
        </button>
      )}

      <nav className="col" style={{ gap: 2 }}>
        {(inProc ? procNav : portfolioNav).map((it) => {
          const active = inProc ? route.tab === it.tab : it.tab === "processos" && route.screen === "processos";
          return (
            <button
              key={it.tab}
              onClick={() => ("onClick" in it && it.onClick ? it.onClick() : go(inProc ? "processo" : "processos", inProc ? route.processId : null, it.tab as Tab))}
              className="row gap2"
              style={{ width: "100%", textAlign: "left", border: "none", borderRadius: 10, padding: "9px 11px", background: active ? "var(--accent-soft)" : "transparent", color: active ? "var(--accent-deep)" : "var(--text)", fontWeight: active ? 700 : 500, fontSize: 14, transition: "background .15s" }}
              onMouseEnter={(e) => { if (!active) (e.currentTarget as HTMLElement).style.background = "var(--line-2)"; }}
              onMouseLeave={(e) => { if (!active) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
            >
              <Icon name={it.icon} size={18} strokeWidth={active ? 2.2 : 1.9} color={active ? "var(--accent)" : "var(--muted)"} />
              <span className="grow truncate">{it.label}</span>
              {"badge" in it && (it.badge ?? 0) > 0 && (
                <span className="tnum" style={{ minWidth: 20, height: 19, padding: "0 6px", borderRadius: 99, fontSize: 11, fontWeight: 700, background: active ? "var(--accent)" : "var(--p-100)", color: active ? "#fff" : "var(--accent-deep)", display: "grid", placeItems: "center" }}>
                  {it.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {!inProc && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "var(--faint)", padding: "0 8px 8px" }}>Meus processos</div>
          <div className="col" style={{ gap: 1 }}>
            {processos.map((p) => (
              <button
                key={p.id}
                onClick={() => go("processo", p.id, "dashboard")}
                className="row gap2"
                style={{ width: "100%", textAlign: "left", border: "none", background: "transparent", borderRadius: 9, padding: "7px 9px", fontSize: 13, color: "var(--text)" }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--line-2)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}
              >
                <MaturityMeter pct={p.maturidade} size={26} compact />
                <span className="grow truncate">{p.nome}</span>
                {p.pendencias > 0 && <span style={{ width: 7, height: 7, borderRadius: 99, background: "var(--p-300)" }} title={`${p.pendencias} pendências`} />}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="grow" />

      {(() => {
        const ativo = route.screen === "ajuda";
        return (
          <button
            onClick={() => go("ajuda")}
            className="row gap2"
            style={{ width: "100%", textAlign: "left", border: "none", borderRadius: 10, padding: "9px 11px", marginBottom: 6, background: ativo ? "var(--accent-soft)" : "transparent", color: ativo ? "var(--accent-deep)" : "var(--text)", fontWeight: ativo ? 700 : 500, fontSize: 14, transition: "background .15s" }}
            onMouseEnter={(e) => { if (!ativo) (e.currentTarget as HTMLElement).style.background = "var(--line-2)"; }}
            onMouseLeave={(e) => { if (!ativo) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
          >
            <Icon name="help-circle" size={18} strokeWidth={ativo ? 2.2 : 1.9} color={ativo ? "var(--accent)" : "var(--muted)"} />
            <span className="grow truncate">Como funciona</span>
          </button>
        );
      })()}

      <button onClick={onOpenPrism} className="row gap2 click" style={{ width: "100%", textAlign: "left", border: "1px solid var(--line)", borderRadius: 12, padding: "10px 11px", background: "linear-gradient(135deg, var(--soft), #fff)", marginBottom: 10 }}>
        <Prism size={30} ring />
        <div className="grow col" style={{ gap: 1 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>Perguntar ao Prism</span>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>{inProc ? "sobre este processo" : "visão da operação"}</span>
        </div>
        <Icon name="arrow-up-right" size={15} color="var(--accent)" />
      </button>

      <div className="row gap2" style={{ padding: "8px 6px", borderTop: "1px solid var(--line-2)", paddingTop: 12 }}>
        <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--grad-cta)", color: "#fff", fontWeight: 700, fontSize: 13, flex: "none" }}>{usuario.iniciais}</span>
        <div className="grow col" style={{ gap: 0, lineHeight: 1.2 }}>
          <span className="truncate" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink)" }}>{usuario.nome}</span>
          <span className="truncate" style={{ fontSize: 11, color: "var(--muted)" }}>{usuario.cargo}</span>
        </div>
        <button title="Sair" onClick={onSignOut} className="center" style={{ width: 30, height: 30, borderRadius: 8, border: "none", background: "transparent", color: "var(--muted)" }}
          onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--line-2)")}
          onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}>
          <Icon name="log-out" size={16} />
        </button>
      </div>
      </aside>
    </>
  );
}

export function Topbar({ route, proc, go, onOpenPrism, action, isMobile = false, onMenu }: { route: Route; proc?: ProcHeaderMock | null; go: Go; onOpenPrism: () => void; action?: ReactNode; isMobile?: boolean; onMenu?: () => void }) {
  const inProc = route.screen === "processo";
  const tabLabels: Record<string, string> = { dashboard: "Dashboard", validacao: "Validação", eventos: "Eventos", padroes: "Padrões", upload: "Novo vídeo", descricao: "Descrição", configuracoes: "Configurações" };
  return (
    <header className="row" style={{ height: isMobile ? 54 : 60, padding: isMobile ? "0 12px" : "0 26px", borderBottom: "1px solid var(--line)", background: "rgba(255,255,255,.82)", backdropFilter: "blur(10px)", position: "sticky", top: 0, zIndex: 30, justifyContent: "space-between", gap: isMobile ? 8 : 16 }}>
      <div className="row gap2" style={{ fontSize: 13.5, color: "var(--muted)", minWidth: 0 }}>
        {isMobile && (
          <button onClick={onMenu} title="Menu" className="center" style={{ width: 36, height: 36, borderRadius: 9, border: "1px solid var(--line)", background: "#fff", color: "var(--ink)", flex: "none" }}>
            <Icon name="menu" size={18} />
          </button>
        )}
        {isMobile ? (
          <span className="truncate" style={{ color: "var(--ink)", fontWeight: 700, fontSize: 14, minWidth: 0 }}>
            {inProc && proc ? tabLabels[route.tab] : route.screen === "ajuda" ? "Como funciona" : "Operação"}
          </span>
        ) : (
          <>
            <button onClick={() => go("processos")} className="click" style={{ border: "none", background: "none", color: inProc ? "var(--muted)" : "var(--ink)", fontWeight: inProc ? 500 : 700, fontSize: 13.5 }}>
              Operação
            </button>
            {inProc && proc && (
              <>
                <Icon name="chevron-right" size={14} color="var(--faint)" />
                <button onClick={() => go("processo", proc.id, "dashboard")} className="truncate click" style={{ border: "none", background: "none", color: "var(--text)", fontWeight: 600, fontSize: 13.5, maxWidth: 200 }}>{proc.nome}</button>
                <Icon name="chevron-right" size={14} color="var(--faint)" />
                <span style={{ color: "var(--ink)", fontWeight: 700 }}>{tabLabels[route.tab]}</span>
              </>
            )}
          </>
        )}
      </div>
      <div className="row gap2" style={{ flex: "none" }}>
        {action}
        <button onClick={onOpenPrism} className="btn btn-secondary btn-sm row gap2" title="Perguntar ao Prism">
          <Prism size={20} />
          {!isMobile && <span>Prism</span>}
        </button>
      </div>
    </header>
  );
}
