import React, { CSSProperties, useEffect, useState } from "react";
import * as L from "lucide-react";

// ════════════════════════════════════════════════════════════════════════
// Ícones — wrapper sobre lucide-react. Aceita nome kebab-case do design.
// ════════════════════════════════════════════════════════════════════════
function toPascal(name: string): string {
  return name
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
}

export function Icon({
  name,
  size = 16,
  color,
  className = "",
  strokeWidth = 1.7,
}: {
  name: string;
  size?: number;
  color?: string;
  className?: string;
  strokeWidth?: number;
}) {
  const Comp = (L as unknown as Record<string, React.ComponentType<{ size?: number; color?: string; className?: string; strokeWidth?: number }>>)[toPascal(name)];
  if (!Comp) {
    // fallback discreto pra não derrubar a UI
    return (
      <span
        className={className}
        style={{ display: "inline-block", width: size, height: size, background: "var(--line-2)", borderRadius: 3 }}
        title={`(ícone "${name}" não encontrado)`}
      />
    );
  }
  return <Comp size={size} color={color} className={className} strokeWidth={strokeWidth} />;
}

// ════════════════════════════════════════════════════════════════════════
// Botão
// ════════════════════════════════════════════════════════════════════════
type BtnVariant = "primary" | "secondary" | "ghost" | "ok" | "danger";
export function Btn({
  variant = "primary",
  size,
  icon,
  iconRight,
  children,
  className = "",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant;
  size?: "sm" | "lg";
  icon?: string;
  iconRight?: string;
}) {
  const sizeClass = size === "sm" ? "btn-sm" : size === "lg" ? "btn-lg" : "";
  return (
    <button className={`btn btn-${variant} ${sizeClass} ${className}`} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 14 : 16} />}
      {children}
      {iconRight && <Icon name={iconRight} size={size === "sm" ? 14 : 16} />}
    </button>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Cartão
// ════════════════════════════════════════════════════════════════════════
export function Card({
  className = "",
  children,
  style,
  flat,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { flat?: boolean }) {
  return (
    <div className={`${flat ? "card-flat" : "card"} ${className}`} style={style} {...rest}>
      {children}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Tooltip discreto (?)
// ════════════════════════════════════════════════════════════════════════
export function Help({
  text,
  width = 240,
  children,
}: {
  text: string;
  width?: number;
  children?: React.ReactNode;
}) {
  return (
    <span className="tip">
      {children ?? <span className="tip-q">?</span>}
      <span className="tip-body" style={{ width }}>
        {text}
      </span>
    </span>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Badge
// ════════════════════════════════════════════════════════════════════════
type BadgeTone = "neutral" | "ok" | "info" | "warn" | "high" | "purple";
export function Badge({
  tone = "neutral",
  children,
  className = "",
}: {
  tone?: BadgeTone;
  children: React.ReactNode;
  className?: string;
}) {
  return <span className={`badge badge-${tone} ${className}`}>{children}</span>;
}

// ════════════════════════════════════════════════════════════════════════
// Barras: split (Lean stack) e track simples
// ════════════════════════════════════════════════════════════════════════
export function LeanBar({
  va,
  apoio,
  desp,
  none,
  height = 8,
  className = "",
}: {
  va: number;
  apoio: number;
  desp: number;
  none: number;
  height?: number;
  className?: string;
}) {
  const total = Math.max(1, va + apoio + desp + none);
  return (
    <div className={`bar-split ${className}`} style={{ height }}>
      {va > 0 && <span style={{ width: `${(va / total) * 100}%`, background: "var(--va)" }} title={`VA ${va}%`} />}
      {apoio > 0 && <span style={{ width: `${(apoio / total) * 100}%`, background: "var(--apoio)" }} title={`Apoio ${apoio}%`} />}
      {desp > 0 && <span style={{ width: `${(desp / total) * 100}%`, background: "var(--desp)" }} title={`Desperdício ${desp}%`} />}
      {none > 0 && <span style={{ width: `${(none / total) * 100}%`, background: "var(--none)" }} title={`Não class. ${none}%`} />}
    </div>
  );
}

export function Track({ pct, color = "var(--accent)" }: { pct: number; color?: string }) {
  const v = Math.max(0, Math.min(100, pct));
  return (
    <div className="track">
      <i style={{ width: `${v}%`, background: color }} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Anel de maturidade (SVG simples) — usado nos cards de processo
// ════════════════════════════════════════════════════════════════════════
const NIVEIS = [
  { min: 0, rotulo: "Conhecendo", cor: "#A78BFA" },
  { min: 35, rotulo: "Aprendendo", cor: "#683BED" },
  { min: 60, rotulo: "Confiante", cor: "#5330C0" },
  { min: 82, rotulo: "Especialista", cor: "#44279C" },
] as const;
export function nivelDe(pct: number) {
  let n = NIVEIS[0] as { min: number; rotulo: string; cor: string };
  for (const x of NIVEIS) if (pct >= x.min) n = x;
  return n;
}

export function RingMaturidade({
  pct,
  size = 56,
  stroke = 6,
}: {
  pct: number;
  size?: number;
  stroke?: number;
}) {
  const v = Math.max(0, Math.min(100, pct));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const off = c - (v / 100) * c;
  const nivel = nivelDe(v);
  return (
    <span style={{ position: "relative", width: size, height: size, display: "inline-block" }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--line-2)" strokeWidth={stroke} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={nivel.cor}
          strokeWidth={stroke}
          fill="none"
          strokeDasharray={c}
          strokeDashoffset={off}
          strokeLinecap="round"
          style={{ transition: "stroke-dashoffset .6s cubic-bezier(.2,.7,.2,1)" }}
        />
      </svg>
      <span
        style={{
          position: "absolute",
          inset: 0,
          display: "grid",
          placeItems: "center",
          fontSize: size / 4,
          fontWeight: 700,
          color: "var(--ink)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {Math.round(v)}
      </span>
    </span>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Empty state
// ════════════════════════════════════════════════════════════════════════
export function Empty({
  icon = "sparkles",
  title,
  desc,
  action,
}: {
  icon?: string;
  title: string;
  desc?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="center" style={{ padding: "44px 24px", textAlign: "center" }}>
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: "50%",
          background: "var(--accent-soft)",
          color: "var(--accent)",
          display: "grid",
          placeItems: "center",
          marginBottom: 14,
        }}
      >
        <Icon name={icon} size={24} />
      </div>
      <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--ink)" }}>{title}</h3>
      {desc && (
        <p className="pretty" style={{ fontSize: 13.5, color: "var(--muted)", maxWidth: 460, marginTop: 6 }}>
          {desc}
        </p>
      )}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Cabeçalho de painel: título + ajuda
// ════════════════════════════════════════════════════════════════════════
export function PanelHead({
  titulo,
  ajuda,
  leitura,
  acao,
}: {
  titulo: string;
  ajuda?: string;
  leitura?: string;
  acao?: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
        <div className="row gap2">
          <h3 className="font-display" style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>
            {titulo}
          </h3>
          {ajuda && <Help text={ajuda} />}
        </div>
        {acao}
      </div>
      {leitura && <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>{leitura}</p>}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Wordmark — marca real (Kalidash Vision + chip "by Prism")
// ════════════════════════════════════════════════════════════════════════
export function Wordmark({ size = 18, sub = true }: { size?: number; sub?: boolean }) {
  return (
    <div className="row gap2" style={{ alignItems: "center" }}>
      <span className="prism-badge" style={{ width: 28, height: 28 }}>
        <img src="/prism.png" alt="Prism" />
      </span>
      <div className="col" style={{ gap: 0, lineHeight: 1.1 }}>
        <span
          className="font-display"
          style={{ fontSize: size, fontWeight: 800, color: "var(--ink)", letterSpacing: "-0.01em" }}
        >
          Kalidash Vision
        </span>
        {sub && (
          <span style={{ fontSize: 10, color: "var(--muted)", letterSpacing: ".08em", textTransform: "uppercase", fontWeight: 600 }}>
            inteligência Prism
          </span>
        )}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Spinner compacto
// ════════════════════════════════════════════════════════════════════════
export function Spinner({ size = 18, color = "var(--accent)" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className="spin" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke={color} strokeOpacity="0.25" strokeWidth="3" />
      <path d="M4 12a8 8 0 018-8" stroke={color} strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Toast bus simples
// ════════════════════════════════════════════════════════════════════════
type ToastItem = { id: number; msg: string; icon?: string; color?: string };
export function ToastHost() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  useEffect(() => {
    const h = (e: Event) => {
      const { msg, icon, color } = (e as CustomEvent).detail || {};
      const id = Date.now() + Math.random();
      setToasts((arr) => [...arr, { id, msg, icon, color }]);
      window.setTimeout(() => setToasts((arr) => arr.filter((t) => t.id !== id)), 3300);
    };
    window.addEventListener("toast", h);
    return () => window.removeEventListener("toast", h);
  }, []);
  return (
    <div style={{ position: "fixed", left: "50%", bottom: 26, transform: "translateX(-50%)", display: "grid", gap: 8, zIndex: 100 }}>
      {toasts.map((t) => (
        <div
          key={t.id}
          className="anim-pop"
          style={{
            background: "var(--ink)",
            color: "#fff",
            padding: "10px 14px",
            borderRadius: 12,
            display: "flex",
            alignItems: "center",
            gap: 9,
            fontSize: 13,
            fontWeight: 500,
            boxShadow: "0 18px 40px -16px rgba(26,16,49,.6)",
            border: "1px solid rgba(255,255,255,.08)",
          }}
        >
          {t.icon && <Icon name={t.icon} size={16} color={t.color || "#A78BFA"} />}
          <span>{t.msg}</span>
        </div>
      ))}
    </div>
  );
}
export function toast(
  msg: string,
  opts: { icon?: string; color?: string } = {}
): void {
  window.dispatchEvent(new CustomEvent("toast", { detail: { msg, ...opts } }));
}

// ════════════════════════════════════════════════════════════════════════
// Helpers de formatação (compatíveis com mock)
// ════════════════════════════════════════════════════════════════════════
export const LEAN = {
  va:    { label: "Valor agregado",    cor: "var(--va)",    bg: "var(--va-bg)" },
  apoio: { label: "Apoio",             cor: "var(--apoio)", bg: "var(--apoio-bg)" },
  desp:  { label: "Desperdício",       cor: "var(--desp)",  bg: "var(--desp-bg)" },
  none:  { label: "Não classificado",  cor: "var(--none)",  bg: "var(--none-bg)" },
} as const;
export type LeanShort = keyof typeof LEAN;

/** Mapa da categoria do banco (`valor_agregado`/`apoio`/`desperdicio`/null) para a short do mock. */
export function leanShort(cat: string | null | undefined): LeanShort {
  if (cat === "valor_agregado") return "va";
  if (cat === "apoio") return "apoio";
  if (cat === "desperdicio") return "desp";
  return "none";
}
export function leanCor(c: LeanShort) {
  return LEAN[c].cor;
}
export function leanLabel(c: LeanShort) {
  return LEAN[c].label;
}
export function fmtSeg(s: number): string {
  s = Math.round(s);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}m${String(r).padStart(2, "0")}s` : `${r}s`;
}
export function tempoRelativo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const diffMs = Date.now() - d;
  const min = Math.round(diffMs / 60000);
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `há ${h}h`;
  const dias = Math.round(h / 24);
  if (dias === 1) return "há 1 dia";
  if (dias < 30) return `há ${dias} dias`;
  const meses = Math.round(dias / 30);
  return meses === 1 ? "há 1 mês" : `há ${meses} meses`;
}
export function iniciaisDe(nomeOuEmail: string): string {
  const txt = (nomeOuEmail || "").trim();
  if (!txt) return "U";
  const limpo = txt.includes("@") ? txt.split("@")[0] : txt;
  const partes = limpo.replace(/[._-]+/g, " ").split(/\s+/).filter(Boolean);
  if (partes.length === 0) return "U";
  if (partes.length === 1) return partes[0].slice(0, 2).toUpperCase();
  return (partes[0][0] + partes[partes.length - 1][0]).toUpperCase();
}

// ════════════════════════════════════════════════════════════════════════
// Modal genérico
// ════════════════════════════════════════════════════════════════════════
export function Modal({
  open,
  onClose,
  children,
  width = 480,
  style,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
  style?: CSSProperties;
}) {
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(26,16,49,.42)",
        backdropFilter: "blur(2px)",
        zIndex: 90,
        display: "grid",
        placeItems: "center",
        padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card anim-pop"
        style={{ width: "100%", maxWidth: width, padding: 22, ...style }}
      >
        {children}
      </div>
    </div>
  );
}
