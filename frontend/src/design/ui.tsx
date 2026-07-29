// ============================================================
// Primitivos de UI + componentes compartilhados (porte fiel de ui.jsx).
// ============================================================
import React, { CSSProperties, useEffect, useRef, useState } from "react";
import * as Lucide from "lucide-react";
import { LEAN, leanCor, leanLabel, nivelDe } from "./helpers";

// ---- Ícone (wrapper lucide-react; aceita nome kebab-case) ----
function pascal(name: string) {
  return name
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
}
export function Icon({
  name,
  size = 18,
  strokeWidth = 1.9,
  className = "",
  color,
  style,
}: {
  name: string;
  size?: number;
  strokeWidth?: number;
  className?: string;
  color?: string;
  style?: CSSProperties;
}) {
  const Cmp = (Lucide as unknown as Record<string, React.ComponentType<{ size?: number; strokeWidth?: number; color?: string; style?: CSSProperties }>>)[pascal(name)];
  const base: CSSProperties = { display: "inline-flex", alignItems: "center", justifyContent: "center", color: color || "currentColor", lineHeight: 0, ...style };
  if (!Cmp) return <span className={className} style={{ ...base, width: size, height: size }} aria-hidden />;
  return (
    <span className={className} style={base} aria-hidden>
      <Cmp size={size} strokeWidth={strokeWidth} color={color || "currentColor"} />
    </span>
  );
}

// ---- Logo Prism (sempre fundo branco) ----
export function Prism({ size = 36, ring = false, className = "", style }: { size?: number; ring?: boolean; className?: string; style?: CSSProperties }) {
  return (
    <span className={`prism-badge ${ring ? "ring" : ""} ${className}`} style={{ width: size, height: size, ...style }}>
      <img src="/prism.png" alt="Prism" />
    </span>
  );
}

// ---- Wordmark ----
export function Wordmark({ size = 18, sub = true, empresa = "" }: { size?: number; sub?: boolean; empresa?: string }) {
  return (
    <div className="row gap2" style={{ alignItems: "center" }}>
      <Prism size={size + 16} ring />
      <div className="col" style={{ lineHeight: 1.05 }}>
        <span className="font-display" style={{ fontWeight: 800, fontSize: size, color: "var(--ink)", letterSpacing: "-.02em" }}>
          Spectra<span style={{ color: "var(--accent)" }}>AI</span>
        </span>
        {sub && empresa && <span style={{ fontSize: 10.5, color: "var(--muted)", fontWeight: 600, letterSpacing: ".02em" }}>{empresa}</span>}
      </div>
    </div>
  );
}

// ---- Botão ----
export function Btn({
  variant = "primary",
  size,
  icon,
  iconRight,
  children,
  className = "",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string; size?: "sm" | "lg"; icon?: string; iconRight?: string }) {
  const cls = `btn btn-${variant} ${size ? "btn-" + size : ""} ${className}`;
  return (
    <button className={cls} {...rest}>
      {icon && <Icon name={icon} size={size === "lg" ? 18 : 15} strokeWidth={2.2} />}
      {children}
      {iconRight && <Icon name={iconRight} size={size === "lg" ? 18 : 15} strokeWidth={2.2} />}
    </button>
  );
}

// ---- Cartão ----
export function Card({ className = "", children, style, flat, ...rest }: React.HTMLAttributes<HTMLDivElement> & { flat?: boolean }) {
  return (
    <div className={`${flat ? "card-flat" : "card"} ${className}`} style={style} {...rest}>
      {children}
    </div>
  );
}

// ---- Badge ----
const BADGE_MAP: Record<string, string> = { neutral: "neutral", info: "info", ok: "ok", success: "ok", warn: "warn", warning: "warn", alta: "high", high: "high", purple: "purple" };
export function Badge({ tone = "neutral", children, icon }: { tone?: string; children: React.ReactNode; icon?: string }) {
  return (
    <span className={`badge badge-${BADGE_MAP[tone] || "neutral"}`}>
      {icon && <Icon name={icon} size={11} strokeWidth={2.4} />}
      {children}
    </span>
  );
}
export function PrioBadge({ p }: { p: string }) {
  const map: Record<string, string> = { alta: "high", media: "warn", info: "info" };
  return <Badge tone={map[p] || "info"}>{(p || "info").toUpperCase()}</Badge>;
}

// ---- Eyebrow ----
export function Eyebrow({ icon, children, style }: { icon?: string; children: React.ReactNode; style?: CSSProperties }) {
  return (
    <span className="eyebrow" style={style}>
      {icon && <Icon name={icon} size={12} strokeWidth={2.4} />}
      {children}
    </span>
  );
}

// ---- Tooltip ----
export function Help({ text, width = 240, children }: { text: string; width?: number; children?: React.ReactNode }) {
  return (
    <span className="tip">
      {children || <span className="tip-q">?</span>}
      <span className="tip-body" style={{ width }}>
        {text}
      </span>
    </span>
  );
}

// ---- Anel de progresso ----
export function Ring({
  pct,
  size = 56,
  stroke = 6,
  color = "var(--accent)",
  track = "var(--p-50)",
  children,
  animate = true,
}: {
  pct: number;
  size?: number;
  stroke?: number;
  color?: string;
  track?: string;
  children?: React.ReactNode;
  animate?: boolean;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const [shown, setShown] = useState(animate ? 0 : pct);
  useEffect(() => {
    if (!animate) {
      setShown(pct);
      return;
    }
    let raf = 0;
    let t0 = 0;
    const step = (t: number) => {
      if (!t0) t0 = t;
      const p = Math.min(1, (t - t0) / 800);
      setShown(pct * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [pct, animate]);
  return (
    <span style={{ position: "relative", width: size, height: size, display: "inline-grid", placeItems: "center" }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c - (c * shown) / 100} />
      </svg>
      <span style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>{children}</span>
    </span>
  );
}

// ---- Medidor de maturidade ----
export function MaturityMeter({ pct, size = 56, compact = false }: { pct: number; size?: number; compact?: boolean }) {
  const nivel = nivelDe(pct);
  if (compact) {
    return (
      <span className="row gap1" title={`Prism: ${nivel.rotulo} · ${pct}%`} style={{ alignItems: "center", gap: 6 }}>
        <Ring pct={pct} size={size} stroke={5} color={nivel.cor}>
          <span className="font-mono tnum" style={{ fontSize: size * 0.26, fontWeight: 700, color: "var(--ink)" }}>
            {pct}
          </span>
        </Ring>
      </span>
    );
  }
  return (
    <div className="row gap3" style={{ alignItems: "center" }}>
      <Ring pct={pct} size={size} stroke={6} color={nivel.cor}>
        <Prism size={size * 0.52} />
      </Ring>
      <div className="col" style={{ gap: 2 }}>
        <span className="eyebrow" style={{ color: nivel.cor }}>
          Prism · {nivel.rotulo}
        </span>
        <span className="font-display" style={{ fontWeight: 700, fontSize: 15, color: "var(--ink)" }}>
          conhece <span className="tnum">{pct}%</span> deste processo
        </span>
      </div>
    </div>
  );
}

// ---- Barra Lean ----
// Fase 63: duas fatias fecham 100% (produtivo × não-produtivo). `vazio` é um
// pedaço DO não-produtivo, desenhado com cor própria dentro dele.
export function LeanBar({ va, desp, vazio = 0, height = 8, showLegend = false }: { va: number; desp: number; vazio?: number; height?: number; showLegend?: boolean }) {
  // Fase 56: `vazio` (posto sem operador) é fatia PRÓPRIA. Sem ela, esse tempo
  // é desenhado dentro do não-produtivo, com cor própria.
  const parts: [string, number][] = [["va", va], ["desp", desp], ["vazio", vazio]];
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="bar-split" style={{ height }}>
        {parts.map(([k, v]) => (v > 0 ? <span key={k} style={{ width: `${v}%`, background: leanCor(k) }} title={`${leanLabel(k)}: ${v}%`} /> : null))}
      </div>
      {showLegend && (
        <div className="row wrap" style={{ gap: 10, fontSize: 11, color: "var(--muted)" }}>
          {parts.map(([k, v]) =>
            v > 0 ? (
              <span key={k} className="row" style={{ gap: 5 }}>
                <i style={{ width: 9, height: 9, borderRadius: 3, background: leanCor(k) }} /> {leanLabel(k)} <b className="tnum" style={{ color: "var(--text)" }}>{v}%</b>
              </span>
            ) : null
          )}
        </div>
      )}
    </div>
  );
}

// ---- Donut ----
export function Donut({
  data,
  size = 168,
  thickness = 26,
  centerLabel,
  centerSub,
}: {
  data: { v: number; c: string; n?: string }[];
  size?: number;
  thickness?: number;
  centerLabel?: React.ReactNode;
  centerSub?: string;
}) {
  const total = data.reduce((s, d) => s + d.v, 0) || 1;
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  let acc = 0;
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        {data.map((d, i) => {
          const frac = d.v / total;
          const dash = c * frac;
          const off = c * (acc / total);
          acc += d.v;
          return <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={d.c} strokeWidth={thickness} strokeDasharray={`${dash} ${c - dash}`} strokeDashoffset={-off} />;
        })}
      </svg>
      {centerLabel != null && (
        <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", textAlign: "center" }}>
          <div>
            <div className="font-display tnum" style={{ fontSize: 28, fontWeight: 700, color: "var(--ink)", lineHeight: 1 }}>
              {centerLabel}
            </div>
            {centerSub && <div style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 3 }}>{centerSub}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Panel header ----
export function PanelHead({ titulo, ajuda, leitura, right }: { titulo: React.ReactNode; ajuda?: string; leitura?: string; right?: React.ReactNode }) {
  return (
    <div style={{ marginBottom: leitura ? 12 : 14 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 10 }}>
        <h3 className="row gap2" style={{ fontSize: 15.5, fontWeight: 700 }}>
          {titulo}
          {ajuda && <Help text={ajuda} />}
        </h3>
        {right}
      </div>
      {leitura && <p style={{ fontSize: 12.5, color: "var(--muted)", margin: "5px 0 0" }}>{leitura}</p>}
    </div>
  );
}

// ---- Empty ----
export function Empty({ icon = "inbox", title, desc, action }: { icon?: string; title: string; desc?: string; action?: React.ReactNode }) {
  return (
    <div className="center" style={{ textAlign: "center", padding: "56px 24px" }}>
      <div>
        <div className="center" style={{ width: 52, height: 52, borderRadius: 16, background: "var(--accent-soft)", color: "var(--accent)", margin: "0 auto 14px" }}>
          <Icon name={icon} size={24} />
        </div>
        <h3 style={{ fontSize: 17 }}>{title}</h3>
        {desc && (
          <p style={{ fontSize: 13.5, color: "var(--muted)", maxWidth: 380, margin: "8px auto 0" }} className="pretty">
            {desc}
          </p>
        )}
        {action && <div style={{ marginTop: 20 }}>{action}</div>}
      </div>
    </div>
  );
}

// ---- Segmented ----
export function Segmented({ value, onChange, options, size = "md" }: { value: string; onChange: (v: string) => void; options: { value: string; label: string; icon?: string }[]; size?: "sm" | "md" }) {
  return (
    <div className="row" style={{ gap: 3, padding: 3, background: "var(--line-2)", borderRadius: 10 }}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            style={{
              padding: size === "sm" ? "5px 11px" : "7px 14px",
              borderRadius: 7,
              fontSize: size === "sm" ? 12.5 : 13.5,
              fontWeight: 600,
              border: "none",
              background: active ? "#fff" : "transparent",
              color: active ? "var(--ink)" : "var(--muted)",
              boxShadow: active ? "0 1px 3px rgba(68,39,156,.16)" : "none",
              transition: "all .15s",
            }}
          >
            {o.icon && <Icon name={o.icon} size={14} style={{ marginRight: 6, verticalAlign: "-2px" }} />}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// ---- Modal ----
export function Modal({ onClose, children, width = 460 }: { onClose: () => void; children: React.ReactNode; width?: number }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      style={{ position: "fixed", inset: 0, background: "rgba(26,16,49,.42)", backdropFilter: "blur(3px)", zIndex: 200, display: "grid", placeItems: "center", padding: 20, animation: "fadeIn .15s" }}
    >
      <div className="card anim-pop" style={{ width: "100%", maxWidth: width, padding: 26 }}>
        {children}
      </div>
    </div>
  );
}

// ---- Toast ----
export function ToastHost() {
  const [toasts, setToasts] = useState<{ id: string; msg: string; icon?: string; color?: string }[]>([]);
  useEffect(() => {
    const h = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      const id = Math.random().toString(36).slice(2);
      setToasts((t) => [...t, { id, ...detail }]);
      setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), detail.duration || 2600);
    };
    window.addEventListener("toast", h);
    return () => window.removeEventListener("toast", h);
  }, []);
  return (
    <div style={{ position: "fixed", bottom: 22, left: "50%", transform: "translateX(-50%)", zIndex: 300, display: "flex", flexDirection: "column", gap: 8, alignItems: "center", pointerEvents: "none" }}>
      {toasts.map((t) => (
        <div key={t.id} className="anim-pop row gap2" style={{ background: "var(--ink)", color: "#fff", padding: "10px 16px", borderRadius: 12, fontSize: 13.5, fontWeight: 600, boxShadow: "0 16px 40px -10px rgba(26,16,49,.5)", maxWidth: 460 }}>
          {t.icon && <Icon name={t.icon} size={16} color={t.color || "#3EE6AE"} strokeWidth={2.4} />}
          <span>{t.msg}</span>
        </div>
      ))}
    </div>
  );
}
export function toast(msg: string, opts: { icon?: string; color?: string; duration?: number } = {}) {
  window.dispatchEvent(new CustomEvent("toast", { detail: { msg, ...opts } }));
}

// ---- Cena de câmera (bounding boxes) ----
export function CameraScene({ height = 200, hud = true, boxes }: { height?: number; hud?: boolean; boxes?: { id: string; x: number; y: number; w: number; h: number; act: string }[] }) {
  const ops = boxes || [
    { id: "P-01", x: 12, y: 34, w: 17, h: 40, act: "posiciona blank" },
    { id: "P-02", x: 44, y: 28, w: 15, h: 44, act: "aguarda ciclo" },
    { id: "P-03", x: 70, y: 42, w: 14, h: 32, act: "transporta bobina" },
  ];
  return (
    <div className="video-skin" style={{ height, width: "100%" }}>
      <svg viewBox="0 0 320 200" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <defs>
          <linearGradient id="vfloor" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#1a0f30" stopOpacity="0" />
            <stop offset="1" stopColor="#2a1a52" stopOpacity=".55" />
          </linearGradient>
          <linearGradient id="vmach" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#3b2470" />
            <stop offset="1" stopColor="#1a0f30" />
          </linearGradient>
        </defs>
        <rect x="0" y="138" width="320" height="62" fill="url(#vfloor)" />
        <g stroke="rgba(83,48,192,0.18)" strokeWidth=".5">
          <line x1="0" y1="138" x2="320" y2="138" />
          <line x1="40" y1="200" x2="120" y2="138" />
          <line x1="280" y1="200" x2="200" y2="138" />
          <line x1="160" y1="200" x2="160" y2="138" />
        </g>
        <g fill="url(#vmach)" stroke="rgba(83,48,192,0.28)" strokeWidth=".6">
          <rect x="20" y="96" width="62" height="52" rx="3" />
          <rect x="126" y="84" width="70" height="64" rx="3" />
          <rect x="232" y="98" width="60" height="50" rx="3" />
        </g>
        <g fill="#0a0614">
          <g transform="translate(60,126)">
            <ellipse cx="0" cy="-2" rx="4" ry="4" />
            <rect x="-5" y="2" width="10" height="22" rx="3" />
          </g>
          <g transform="translate(165,116)">
            <ellipse cx="0" cy="-2" rx="4" ry="4" />
            <rect x="-5" y="2" width="10" height="26" rx="3" />
          </g>
          <g transform="translate(254,126)">
            <ellipse cx="0" cy="-2" rx="3.5" ry="3.5" />
            <rect x="-5" y="2" width="10" height="22" rx="3" />
          </g>
        </g>
      </svg>
      <div className="scanlines" />
      <div className="scanbeam" />
      {ops.map((o, i) => (
        <div key={o.id} className="bbox anim-pop" style={{ left: `${o.x}%`, top: `${o.y}%`, width: `${o.w}%`, height: `${o.h}%`, animationDelay: `${i * 0.15}s` }}>
          <span className="bbox-tag">{o.id}</span>
          {o.act && <span className="bbox-act">{o.act}</span>}
        </div>
      ))}
      {hud && (
        <>
          <div className="row gap2" style={{ position: "absolute", top: 12, left: 12, fontSize: 10, fontFamily: "var(--mono)" }}>
            <span className="row gap1" style={{ background: "rgba(0,0,0,.6)", color: "rgba(255,255,255,.9)", padding: "4px 8px", borderRadius: 7 }}>
              <span className="live-dot on" style={{ background: "#A78BFA" }} /> INTERPRETANDO
            </span>
            <span style={{ background: "rgba(0,0,0,.4)", color: "rgba(255,255,255,.7)", padding: "4px 8px", borderRadius: 7 }}>CAM-04 · captação automática</span>
          </div>
          <div className="row" style={{ position: "absolute", bottom: 0, left: 0, right: 0, justifyContent: "space-between", padding: "8px 12px", fontSize: 10, fontFamily: "var(--mono)", background: "linear-gradient(0deg, rgba(0,0,0,.65), transparent)", color: "rgba(255,255,255,.7)" }}>
            <span>{ops.length} pessoas rastreadas · {ops.length} ações descritas</span>
            <span style={{ color: "rgba(255,255,255,.5)" }}>detect · track · descrever</span>
          </div>
        </>
      )}
    </div>
  );
}

export { LEAN, leanCor, leanLabel };
