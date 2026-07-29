// ============================================================
// Helpers do design (porte fiel de data.jsx — só os utilitários).
// Os dados mock foram substituídos pela camada de API real.
// ============================================================

// Fase 63: NÃO existe "não classificado". Todo tempo é produtivo (va) ou
// não-produtivo (desp); `vazio` (posto sem operador) é um DETALHE do
// não-produtivo, mostrado à parte porque a causa e a ação são outras — não é
// uma terceira fatia e não entra na soma de 100%.
export type LeanShort = "va" | "desp" | "vazio";

// Níveis de maturidade do Prism por processo
export const NIVEIS = [
  { min: 0, rotulo: "Conhecendo", cor: "#A78BFA" },
  { min: 35, rotulo: "Aprendendo", cor: "#683BED" },
  { min: 60, rotulo: "Confiante", cor: "#5330C0" },
  { min: 82, rotulo: "Especialista", cor: "#44279C" },
] as const;

export function nivelDe(pct: number) {
  let n: { min: number; rotulo: string; cor: string } = NIVEIS[0];
  for (const x of NIVEIS) if (pct >= x.min) n = x;
  return n;
}

// Lean meta
export const LEAN: Record<LeanShort, { label: string; cor: string; bg: string }> = {
  va: { label: "Produtivo", cor: "var(--va)", bg: "var(--va-bg)" },
  desp: { label: "Desperdício", cor: "var(--desp)", bg: "var(--desp-bg)" },
  vazio: { label: "Posto vazio", cor: "#8a8598", bg: "var(--none-bg)" },
};

export function leanCor(c: string) {
  return (LEAN[c as LeanShort] || LEAN.desp).cor;
}
export function leanLabel(c: string) {
  return (LEAN[c as LeanShort] || LEAN.desp).label;
}

/** Mapa categoria do banco → short do design.
 * Fase 63: espelha `categoria_efetiva` do backend — nulo/desconhecido cai em
 * NÃO-PRODUTIVO, nunca em cinza. A convenção Lean é que o ônus da prova é de
 * quem afirma que a atividade agrega valor; sem prova, não agrega. Errar para
 * o outro lado inflaria a produtividade que o cliente leva para a diretoria. */
export function leanShort(cat: string | null | undefined): LeanShort {
  if (cat === "valor_agregado") return "va";
  return "desp";
}
/** Inverso: short do design → categoria do banco. */
export function leanLong(c: LeanShort): "valor_agregado" | "desperdicio" {
  return c === "va" ? "valor_agregado" : "desperdicio";
}

export function fmtSeg(s: number) {
  s = Math.round(s || 0);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m}m${String(r).padStart(2, "0")}s` : `${r}s`;
}

/** Duração legível p/ gestor (Fase 17): 3h20 / 45min / 12s. */
export function fmtDur(s: number) {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h${String(m).padStart(2, "0")}` : `${h}h`;
  if (m > 0) return `${m}min`;
  return `${s}s`;
}

export function tempoRelativo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const min = Math.round((Date.now() - d) / 60000);
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
  const base = txt.includes("@") ? txt.split("@")[0] : txt;
  const partes = base.replace(/[._-]+/g, " ").split(/\s+/).filter(Boolean);
  if (!partes.length) return "U";
  if (partes.length === 1) return partes[0].slice(0, 2).toUpperCase();
  return (partes[0][0] + partes[partes.length - 1][0]).toUpperCase();
}
