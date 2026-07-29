// ============================================================
// Padrões — porte fiel de padroes.jsx, com dados reais.
// ============================================================
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { mapSerie, mapPadroes, type SerieMock, type PadProcMock, type ProcHeaderMock } from "../lib/adapt";
import { leanCor, leanLabel } from "../design/helpers";
import { Card, Icon, Prism, Badge, PanelHead, Empty, Btn } from "../design/ui";
import type { Go } from "../design/Shell";

const TIPO_PADRAO: Record<string, { icon: string; label: string }> = {
  tendencia: { icon: "trending-up", label: "Tendência" },
  recorrencia: { icon: "repeat", label: "Recorrência" },
  desvio: { icon: "alert-triangle", label: "Desvio" },
  volatilidade: { icon: "activity", label: "Volatilidade" },
  fluxo: { icon: "git-branch", label: "Fluxo" },
  desperdicio: { icon: "arrow-down", label: "Desperdício recorrente" },
  valor: { icon: "arrow-up", label: "Valor recorrente" },
};

// Fase 24: a tela de Padrões está DESATIVADA ("em breve") p/ cortar o consumo
// de tokens da análise de padrões no backend. Nada foi removido — a
// implementação original segue INTACTA em `PadroesOriginal` abaixo; para
// reativar, basta o export default voltar a renderizá-la (e KV_PADROES_ENABLE=on).
export default function Padroes(_props: { proc: ProcHeaderMock; go: Go }) {
  return (
    <Card>
      <Empty
        icon="sparkles"
        title="Padrões — em breve"
        desc="Estamos aprimorando a detecção de padrões da operação (tendências, recorrências e desvios ao longo dos turnos). Em breve por aqui."
      />
    </Card>
  );
}

function PadroesOriginal({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const serieQ = useQuery({ queryKey: ["serie", proc.id], queryFn: () => api.padroes.serie(proc.id) });
  const padQ = useQuery({ queryKey: ["padroes", proc.id], queryFn: () => api.padroes.doProcesso(proc.id) });

  if (serieQ.isLoading || padQ.isLoading) return <Card><Empty icon="loader" title="Carregando padrões…" /></Card>;
  const serie = serieQ.data ? mapSerie(serieQ.data) : { nVideos: 0, pontos: [] };
  const padroes = mapPadroes(padQ.data || []);

  if (serie.nVideos < 3) {
    return (
      <Card>
        <Empty icon="activity" title="Ainda não há série suficiente para detectar padrões"
          desc={`Padrões precisam de pelo menos 3 vídeos processados (você tem ${serie.nVideos}). Envie mais turnos para o Prism identificar tendências, recorrências e desvios.`}
          action={<Btn icon="upload" onClick={() => go("processo", proc.id, "upload")}>Enviar vídeo</Btn>} />
      </Card>
    );
  }

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="row gap3">
        <Prism size={36} ring />
        <div>
          <h1 className="font-display" style={{ fontSize: 20, fontWeight: 700 }}>Padrões da operação</h1>
          <p style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>Recorrência e evolução ao longo dos turnos — diferente das sugestões pontuais, que olham o estado atual.</p>
        </div>
      </div>

      <GraficoEvolucao serie={serie} />

      {padroes.length === 0 ? (
        <Card style={{ padding: 18 }}><p style={{ fontSize: 13.5, color: "var(--muted)" }}>Nenhum padrão forte detectado ainda. Conforme você envia mais turnos, as recorrências e tendências ficam mais nítidas.</p></Card>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%, 380px),1fr))", gap: 14 }}>
          {padroes.map((p) => <PadraoCard key={p.id} p={p} />)}
        </div>
      )}
    </div>
  );
}

function GraficoEvolucao({ serie }: { serie: SerieMock }) {
  const pts = serie.pontos;
  const W = 920, H = 280, padL = 34, padR = 16, padT = 16, padB = 28;
  const iw = W - padL - padR, ih = H - padT - padB;
  const x = (i: number) => padL + (pts.length <= 1 ? iw / 2 : (i / (pts.length - 1)) * iw);
  const y = (v: number) => padT + (1 - v / 100) * ih;
  const order: (keyof SerieMock["pontos"][number])[] = ["va", "desp"];
  const bands = order.map((cat, ci) => {
    const lowKeys = order.slice(0, ci);
    const top = pts.map((p, i) => ({ i, v: lowKeys.reduce((s, k) => s + (p[k] as number), 0) + (p[cat] as number) }));
    const bot = pts.map((p, i) => ({ i, v: lowKeys.reduce((s, k) => s + (p[k] as number), 0) }));
    const path = "M" + top.map((t) => `${x(t.i).toFixed(1)},${y(t.v).toFixed(1)}`).join(" L ") + " L " + bot.slice().reverse().map((b) => `${x(b.i).toFixed(1)},${y(b.v).toFixed(1)}`).join(" L ") + " Z";
    return { cat: cat as string, path };
  });
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Evolução ao longo dos turnos" ajuda="Share de % do tempo de cada categoria, turno a turno (cada vídeo é um turno, em ordem cronológica). Permite VER a tendência." leitura={`${serie.nVideos} turnos analisados.`} />
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}>
        {[0, 25, 50, 75, 100].map((g) => (
          <g key={g}><line x1={padL} x2={W - padR} y1={y(g)} y2={y(g)} stroke="var(--line-2)" /><text x={padL - 6} y={y(g) + 3} fontSize="9" textAnchor="end" fill="var(--faint)" className="font-mono">{g}%</text></g>
        ))}
        {bands.map((b) => <path key={b.cat} d={b.path} fill={leanCor(b.cat)} fillOpacity={0.82} stroke={leanCor(b.cat)} strokeOpacity={0.3} strokeWidth=".5" />)}
        {pts.map((p, i) => i % 2 === 0 && <text key={i} x={x(i)} y={H - padB + 16} fontSize="9" textAnchor="middle" fill="var(--muted)" className="font-mono">{p.turno}</text>)}
      </svg>
      <div className="row wrap" style={{ gap: 14, marginTop: 8, justifyContent: "center" }}>
        {["va", "desp"].map((k) => (
          <span key={k} className="row gap1" style={{ fontSize: 12, color: "var(--text)" }}><i style={{ width: 11, height: 11, borderRadius: 3, background: leanCor(k) }} /> {leanLabel(k)}</span>
        ))}
      </div>
    </Card>
  );
}

function PadraoCard({ p }: { p: PadProcMock }) {
  const info = TIPO_PADRAO[p.tipo] || { icon: "circle", label: p.tipo };
  return (
    <Card style={{ padding: 18 }}>
      <div className="row gap2 wrap" style={{ marginBottom: 8 }}>
        <span className="chip"><Icon name={info.icon} size={12} /> {info.label}</span>
        <Badge tone={p.confianca === "alta" ? "ok" : "warn"}>confiança {p.confianca}</Badge>
        {p.relevancia === "alta" && <Badge tone="high">ALTA</Badge>}
      </div>
      <h4 style={{ fontSize: 15.5, fontWeight: 700 }}>{p.titulo}</h4>
      <p className="pretty" style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, marginTop: 6 }}>{p.descricao}</p>
      {p.recomendacao && (
        <div className="soft" style={{ borderRadius: 10, padding: "10px 12px", marginTop: 10, fontSize: 12.5, color: "var(--text)", border: "1px solid var(--line)" }}>
          <b style={{ color: "var(--ink)" }}>Recomendação. </b>{p.recomendacao}
        </div>
      )}
      {p.comportamentos?.length > 0 && (
        <div className="row wrap gap1" style={{ marginTop: 10 }}>
          {p.comportamentos.map((c) => <code key={c} className="font-mono" style={{ fontSize: 10, background: "var(--line-2)", color: "var(--text)", padding: "2px 7px", borderRadius: 6 }}>{c}</code>)}
        </div>
      )}
    </Card>
  );
}
