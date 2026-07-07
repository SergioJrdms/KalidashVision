// ============================================================
// Dashboard do processo — porte fiel de dashboard.jsx (dados reais).
// t.dashboard = "equilibrado" | "minimal" | "denso"
// ============================================================
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { mapDashboard, type DetMock, type CompMock, type ProcHeaderMock, type SugMock } from "../lib/adapt";
import { leanCor, leanLabel, leanLong, leanShort, fmtSeg, fmtDur, type LeanShort } from "../design/helpers";
import { Btn, Card, Icon, Prism, Help, PrioBadge, MaturityMeter, LeanBar, Donut, PanelHead, Empty, Ring, toast } from "../design/ui";
import type { Go } from "../design/Shell";
import type { Tweaks } from "../App";
import type { AcaoSugestao, InsightsQuantitativos, PerguntaGestor, PlacarProcesso } from "../lib/types";
import { useIsMobile } from "../hooks/useIsMobile";

const SUG_VISIVEL_PADRAO = 3;
const COMP_VISIVEL_PADRAO = 5;

export default function Dashboard({ proc, go, t }: { proc: ProcHeaderMock; go: Go; t: Tweaks }) {
  const isMobile = useIsMobile();
  const q = useQuery({ queryKey: ["dashboard", proc.id], queryFn: () => api.processos.dashboard(proc.id) });
  if (q.isLoading) return <Card><Empty icon="loader" title="Carregando dashboard…" /></Card>;
  if (!q.data) return <Card><Empty icon="alert-triangle" title="Não foi possível carregar" /></Card>;
  const det = mapDashboard(q.data);
  if (det.snapshot.videos === 0) {
    return (
      <Card>
        <Empty icon="video" title="Nenhum vídeo processado ainda" desc="Envie o primeiro vídeo. Em poucos minutos você verá comportamentos, distribuição do tempo e as primeiras sugestões." action={<Btn icon="upload" onClick={() => go("processo", proc.id, "upload")}>Enviar vídeo</Btn>} />
      </Card>
    );
  }
  const denso = t.dashboard === "denso";
  const temPosto = (det.insights?.por_roi?.length || 0) > 1;
  return (
    <div className="col" style={{ gap: 18 }}>
      <DashHeader proc={proc} det={det} go={go} />

      {/* Fase 19 — HERÓI: o processo comparado com o melhor dia dele mesmo. */}
      <PlacarHero placar={det.insights?.placar || null} />

      {/* A resposta em 1 olhada: os 3 números que importam. */}
      <KpisExecutivos det={det} />

      {/* Insights rápidos — leitura de 5s (frases da Fase 17). */}
      <InsightsNumericos iq={det.insights} />

      {/* Onde o tempo vai — por ação (com reclassificação Lean) e por posto. */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile || !temPosto ? "1fr" : "1.4fr 1fr", gap: 16, alignItems: "start" }}>
        <TempoPorComportamento det={det} denso={denso} processoId={proc.id} />
        {temPosto && <PorPosto iq={det.insights} />}
      </div>

      {/* Agir: perguntas prontas pro chão de fábrica + sugestões curadas. */}
      <PerguntasGestor perguntas={det.insights?.perguntas || []} />
      <Sugestoes det={det} processoId={proc.id} />

      <VideosRodape det={det} />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Fase 19 — Placar do processo: "rodou a X% do seu melhor dia".
// O processo comparado com ELE MESMO — máximo julgamento, zero micro-gestão.
// ═══════════════════════════════════════════════════════════════════════
function PlacarHero({ placar }: { placar: PlacarProcesso | null }) {
  if (!placar) return null;
  const p = placar;
  const bom = p.eh_melhor_dia || p.score >= 85;
  const cor = bom ? leanCor("va") : p.score >= 60 ? "#c98a00" : leanCor("desp");
  const vs = p.vs_anterior?.["desperdicio"];
  return (
    <Card style={{ padding: "20px 22px", borderLeft: `3px solid ${cor}`, background: "linear-gradient(120deg, var(--soft), #fff 60%)" }}>
      <div className="row gap4 wrap" style={{ alignItems: "center" }}>
        <Ring pct={p.score} size={92} stroke={9} color={cor}>
          <div className="col" style={{ alignItems: "center", lineHeight: 1 }}>
            <span className="font-display tnum" style={{ fontSize: 26, fontWeight: 700, color: "var(--ink)" }}>{p.score}</span>
            <span style={{ fontSize: 9, color: "var(--muted)", fontWeight: 700 }}>/100</span>
          </div>
        </Ring>
        <div className="col grow" style={{ gap: 6, minWidth: 220 }}>
          <div className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
            Placar do processo <Help text={`Compara o dia mais recente com o MELHOR dia já observado deste processo (maior % de tempo produtivo). Base: ${p.n_dias} dias com observação suficiente.`} width={250} />
          </div>
          {p.eh_melhor_dia ? (
            <p style={{ fontSize: 15.5, fontWeight: 700, color: "var(--ink)", margin: 0 }}>
              {p.dia_atual.dia} é o <span style={{ color: leanCor("va") }}>melhor dia já observado</span> — {p.dia_atual.va_pct.toFixed(0)}% produtivo em {fmtDur(p.dia_atual.seg)}.
            </p>
          ) : (
            <p style={{ fontSize: 15.5, fontWeight: 700, color: "var(--ink)", margin: 0 }}>
              O processo rodou a <span style={{ color: cor }}>{p.score}% do seu melhor dia</span>.
            </p>
          )}
          {!p.eh_melhor_dia && (
            <p style={{ fontSize: 12.5, color: "var(--muted)", margin: 0 }}>
              {p.dia_atual.dia}: <b style={{ color: "var(--text)" }}>{p.dia_atual.va_pct.toFixed(0)}% produtivo</b> ({fmtDur(p.dia_atual.seg)} observadas) · melhor dia {p.dia_melhor.dia}: <b style={{ color: "var(--text)" }}>{p.dia_melhor.va_pct.toFixed(0)}% produtivo</b>
            </p>
          )}
          {p.puxou.length > 0 && (
            <div className="col" style={{ gap: 3, marginTop: 2 }}>
              {p.puxou.map((tx, i) => (
                <span key={i} className="row gap1" style={{ fontSize: 12.5, color: "var(--text)" }}>
                  <Icon name="arrow-down-right" size={13} color={leanCor("desp")} /> {tx}
                </span>
              ))}
            </div>
          )}
          {vs && Math.abs(vs.delta_pp) >= 1 && (
            <span style={{ fontSize: 11.5, color: "var(--muted)" }}>
              Vs dias anteriores: desperdício {vs.antes.toFixed(0)}% → {vs.atual.toFixed(0)}% ({vs.delta_pp > 0 ? "+" : "−"}{Math.abs(vs.delta_pp).toFixed(0)} pts)
            </span>
          )}
        </div>
      </div>
    </Card>
  );
}

// Fase 19 — anomalias viram perguntas prontas pra levar ao chão de fábrica.
function PerguntasGestor({ perguntas }: { perguntas: PerguntaGestor[] }) {
  if (!perguntas.length) return null;
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Perguntas para o chão de fábrica"
        ajuda="O Prism transforma as anomalias dos números em perguntas prontas — com horário e tempo reais. Leve-as à conversa diária com a equipe: quem responde é o processo, não a câmera."
        leitura="A pauta da sua próxima conversa com a equipe, direto dos dados."
      />
      <div className="col" style={{ gap: 10 }}>
        {perguntas.map((q, i) => (
          <div key={i} className="card-flat" style={{ padding: "10px 12px", display: "flex", gap: 10, alignItems: "flex-start" }}>
            <span className="center" style={{ width: 26, height: 26, borderRadius: 8, background: "var(--accent-soft)", color: "var(--accent-deep)", flex: "none" }}>
              <Icon name="message-circle-question" size={14} />
            </span>
            <div className="col" style={{ gap: 2, minWidth: 0 }}>
              <p style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)", margin: 0, lineHeight: 1.45 }}>{q.texto}</p>
              {q.contexto && <span style={{ fontSize: 11, color: "var(--faint)" }}>{q.contexto}</span>}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// Fase 19 — recorte por POSTO (ROI da câmera) — o processo por estação, nunca
// por pessoa. Só aparece quando há mais de um posto mapeado.
function PorPosto({ iq }: { iq: InsightsQuantitativos | null }) {
  const rois = iq?.por_roi || [];
  if (rois.length < 2) return null;
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Por posto de trabalho"
        ajuda="Cada posto é um ROI das câmeras (bancada, esteira…). Mostra quanto do tempo daquele posto é produtivo vs desperdício — o gargalo aparece na cor."
        leitura="Onde o processo trava, por estação."
      />
      <ul className="col" style={{ gap: 12, listStyle: "none", padding: 0, margin: 0 }}>
        {rois.slice(0, 6).map((r) => (
          <li key={r.zona}>
            <div className="row gap2" style={{ marginBottom: 4 }}>
              <span className="truncate" style={{ fontSize: 12.5, fontWeight: 700, color: "var(--text)" }}>{r.zona}</span>
              <span className="grow" />
              <span className="tnum" style={{ fontSize: 11.5, color: "var(--muted)", fontWeight: 600 }}>{fmtDur(r.seg)} observadas</span>
            </div>
            <LeanBar va={r.va_pct} desp={r.desp_pct} apoio={Math.max(0, 100 - r.va_pct - r.desp_pct)} none={0} />
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
              <b style={{ color: leanCor("va") }}>{r.va_pct.toFixed(0)}% produtivo</b> · <b style={{ color: leanCor("desp") }}>{r.desp_pct.toFixed(0)}% desperdício</b>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Fase 18 — KPIs executivos: "1 olhada = a resposta".
// Desperdício (o número que dói) · Produtivo · Tendência do desperdício.
// Usa os segundos absolutos da Fase 17 (por_categoria) com fallback aos %.
// ═══════════════════════════════════════════════════════════════════════
function KpisExecutivos({ det }: { det: DetMock }) {
  const iq = det.insights;
  const s = det.snapshot;
  const cat = iq?.por_categoria || {};
  const desp = cat["desperdicio"];
  const va = cat["valor_agregado"];
  const despPct = desp ? Math.round(desp.pct) : s.desp;
  const vaPct = va ? Math.round(va.pct) : s.va;
  const despSub = desp ? fmtDur(desp.seg) : `${s.videos} vídeos`;
  const vaSub = va ? fmtDur(va.seg) : `${s.videos} vídeos`;
  const trend = iq?.periodo?.tendencia_desp_pp ?? null;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 14 }}>
      <KpiHero
        label="Tempo desperdiçado"
        valor={`${despPct}%`}
        sub={despSub}
        icon="alert-triangle"
        cor={leanCor("desp")}
        ajuda="Parte do tempo observado classificada como desperdício (Lean). É o número a atacar primeiro — cada ponto aqui é tempo que não vira valor."
      />
      <KpiHero
        label="Tempo produtivo"
        valor={`${vaPct}%`}
        sub={vaSub}
        icon="check-circle"
        cor={leanCor("va")}
        ajuda="Parte do tempo em comportamentos de valor agregado — o que o cliente efetivamente paga."
      />
      <KpiTendencia trend={trend} texto={iq?.periodo?.texto || null} />
    </div>
  );
}

function KpiHero({ label, valor, sub, icon, cor, ajuda }: { label: string; valor: string; sub?: string; icon: string; cor: string; ajuda: string }) {
  return (
    <Card style={{ padding: 18, borderLeft: `3px solid ${cor}` }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>{label} <Help text={ajuda} /></span>
        <Icon name={icon} size={17} color={cor} />
      </div>
      <div className="font-display tnum" style={{ fontSize: 34, fontWeight: 700, color: "var(--ink)", marginTop: 6, lineHeight: 1 }}>{valor}</div>
      {sub && <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 4 }}>{sub}</div>}
    </Card>
  );
}

function KpiTendencia({ trend, texto }: { trend: number | null; texto: string | null }) {
  // trend = variação em pontos do DESPERDÍCIO nos últimos vídeos.
  // > 0 → desperdício SUBIU (piorou, vermelho ↑). < 0 → melhorou (verde ↓).
  const temDado = trend !== null && Number.isFinite(trend) && Math.abs(trend) >= 0.5;
  const piorou = (trend ?? 0) > 0;
  const cor = !temDado ? "var(--faint)" : piorou ? leanCor("desp") : leanCor("va");
  const seta = !temDado ? "minus" : piorou ? "trending-up" : "trending-down";
  const valor = !temDado ? "estável" : `${piorou ? "+" : "−"}${Math.abs(Math.round(trend ?? 0))} pts`;
  const sub = !temDado ? "poucos vídeos p/ tendência" : piorou ? "desperdício subindo" : "desperdício caindo";
  return (
    <Card style={{ padding: 18, borderLeft: `3px solid ${cor}` }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>Tendência do desperdício <Help text={texto || "Comparação do desperdício nos vídeos mais recentes contra os anteriores. Precisa de alguns vídeos para ser confiável."} width={240} /></span>
        <Icon name={seta} size={17} color={cor} />
      </div>
      <div className="font-display tnum" style={{ fontSize: 34, fontWeight: 700, color: temDado ? cor : "var(--ink)", marginTop: 6, lineHeight: 1 }}>{valor}</div>
      <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 4 }}>{sub}</div>
    </Card>
  );
}

function DashHeader({ proc, det, go }: { proc: ProcHeaderMock; det: DetMock; go: Go }) {
  return (
    <div className="row" style={{ justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
      <div className="col" style={{ gap: 10 }}>
        <h1 className="font-display" style={{ fontSize: 24, fontWeight: 700 }}>{proc.nome}</h1>
        <MaturityMeter pct={proc.maturidade} size={50} />
      </div>
      <div className="row gap2">
        <Btn variant="secondary" icon="git-pull-request-arrow" onClick={() => go("processo", proc.id, "validacao")}>
          Validar <span style={{ background: "var(--p-100)", color: "var(--accent-deep)", borderRadius: 99, padding: "1px 7px", fontSize: 12, fontWeight: 700 }}>{proc.pendencias}</span>
        </Btn>
        <Btn icon="upload" onClick={() => go("processo", proc.id, "upload")}>Novo vídeo</Btn>
      </div>
    </div>
  );
}

function LearningStrip({ proc, det, go }: { proc: ProcHeaderMock; det: DetMock; go: Go }) {
  const auto = det.origens.auto, total = auto + det.origens.humano + det.origens.pendente;
  const autoPct = total > 0 ? Math.round((auto / total) * 100) : 0;
  return (
    <Card style={{ padding: "14px 18px", background: "linear-gradient(120deg, var(--soft), #fff 70%)" }}>
      <div className="row gap3 wrap" style={{ justifyContent: "space-between" }}>
        <div className="row gap3" style={{ minWidth: 0 }}>
          <Prism size={38} ring />
          <div style={{ minWidth: 0 }}>
            <p style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>O Prism já confirma <span style={{ color: "var(--accent)" }}>{autoPct}% dos eventos sozinho</span> nesta linha.</p>
            <p className="pretty" style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>
              Cada validação sua ensina um padrão novo — e some da sua fila para sempre. Faltam <b style={{ color: "var(--text)" }}>{proc.pendencias}</b> eventos e <b style={{ color: "var(--text)" }}>{det.perguntasPendentes}</b> perguntas.
            </p>
          </div>
        </div>
        <Btn icon="sparkles" onClick={() => go("processo", proc.id, "validacao")} style={{ flex: "none" }}>Ensinar o Prism</Btn>
      </div>
    </Card>
  );
}

function KpiVA({ det }: { det: DetMock }) {
  const s = det.snapshot;
  return (
    <Card style={{ padding: 16, borderLeft: "3px solid var(--accent)" }}>
      <div className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
        Índice de valor agregado <Help text="% do tempo em comportamentos de 'valor agregado' (Lean). A métrica-rainha: quanto da operação entrega o que o cliente paga." width={230} />
      </div>
      <div className="font-display tnum" style={{ fontSize: 30, fontWeight: 700, color: "var(--ink)", marginTop: 4 }}>{s.va}%</div>
      <div style={{ marginTop: 10 }}><LeanBar va={s.va} apoio={s.apoio} desp={s.desp} none={s.none} showLegend /></div>
    </Card>
  );
}
function Kpi({ label, valor, sub, icon, ajuda, alert }: { label: string; valor: string; sub?: string; icon: string; ajuda: string; alert?: boolean }) {
  return (
    <Card style={{ padding: 16, borderLeft: alert ? "3px solid var(--apoio)" : "none" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }} className="row gap1">{label} <Help text={ajuda} /></span>
        <Icon name={icon} size={16} color={alert ? "var(--apoio)" : "var(--faint)"} />
      </div>
      <div className="font-display tnum truncate" style={{ fontSize: 24, fontWeight: 700, color: alert ? "#b8740b" : "var(--ink)", marginTop: 6 }}>{valor}</div>
      {sub && <div className="truncate" style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>{sub}</div>}
    </Card>
  );
}

function Sugestoes({ det, processoId }: { det: DetMock; processoId: string }) {
  const qc = useQueryClient();
  const [prio, setPrio] = useState("todas");
  const [verTodas, setVerTodas] = useState(false);

  const lista = useMemo(() => det.sugestoes.filter((x) => prio === "todas" || x.prioridade === prio), [prio, det]);
  const visiveis = verTodas ? lista : lista.slice(0, SUG_VISIVEL_PADRAO);
  const counts: Record<string, number> = { todas: det.sugestoes.length, alta: 0, media: 0, info: 0 };
  det.sugestoes.forEach((x) => { counts[x.prioridade] = (counts[x.prioridade] || 0) + 1; });

  const marcar = useMutation({
    mutationFn: ({ id, acao }: { id: string; acao: AcaoSugestao }) => api.sugestoes.marcar(id, acao),
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: ["dashboard", processoId] });
      qc.invalidateQueries({ queryKey: ["processos"] });
      toast(vars.acao === "realizada" ? "Marcada como realizada." : "Sugestão dispensada.", { icon: "check" });
    },
  });

  return (
    <Card style={{ padding: 18 }}>
      <PanelHead titulo="Sugestões de produtividade" ajuda="Geradas pela IA combinando seus dados agregados com os 7 desperdícios do Lean. Marque como realizada quando aplicar a ação — se ela voltar depois, o Prism avisa que não foi cumprida." right={<span style={{ fontSize: 12, color: "var(--muted)" }}>{visiveis.length} de {lista.length}</span>} />
      <div className="row gap1 wrap" style={{ marginBottom: 12 }}>
        {["todas", "alta", "media", "info"].map((p) => (
          <button key={p} onClick={() => { setPrio(p); setVerTodas(false); }} style={{ padding: "3px 10px", borderRadius: 99, fontSize: 11.5, fontWeight: 600, border: "1px solid", borderColor: prio === p ? "var(--accent)" : "var(--line)", background: prio === p ? "var(--accent)" : "#fff", color: prio === p ? "#fff" : "var(--muted)" }}>
            {p === "todas" ? "Todas" : p[0].toUpperCase() + p.slice(1)} · {counts[p] || 0}
          </button>
        ))}
      </div>
      <div className="col" style={{ gap: 8 }}>
        {visiveis.length === 0
          ? <p style={{ fontSize: 13, color: "var(--muted)" }}>Nenhuma sugestão pendente.</p>
          : visiveis.map((sug) => (
              <SugestaoCard key={sug.id} s={sug} pendente={marcar.isPending} onMarcar={(acao) => marcar.mutate({ id: sug.id, acao })} />
            ))}
      </div>
      {lista.length > SUG_VISIVEL_PADRAO && (
        <div className="row" style={{ justifyContent: "center", marginTop: 10 }}>
          <button onClick={() => setVerTodas((v) => !v)} className="row gap1" style={{ border: "1px solid var(--line)", background: "#fff", color: "var(--accent)", borderRadius: 99, padding: "6px 14px", fontSize: 12, fontWeight: 600 }}>
            <Icon name={verTodas ? "chevron-up" : "chevron-down"} size={14} />
            {verTodas ? "Mostrar menos" : `Ver todas (${lista.length})`}
          </button>
        </div>
      )}
    </Card>
  );
}
function SugestaoCard({ s, pendente, onMarcar }: { s: SugMock; pendente: boolean; onMarcar: (acao: AcaoSugestao) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card-flat hoverlift" style={{ padding: "10px 12px", borderColor: s.voltou ? "var(--desp)" : "var(--line)" }}>
      <div className="row gap2 wrap" style={{ marginBottom: 4, alignItems: "center" }}>
        <PrioBadge p={s.prioridade} />
        <span style={{ fontSize: 11.5, fontWeight: 700, color: "var(--text)" }}>{s.area}</span>
        {s.voltou && (
          <span className="row gap1" title="Esta sugestão foi marcada como realizada antes — voltou a aparecer, então a ação não foi cumprida ou perdeu efeito." style={{ fontSize: 10.5, fontWeight: 700, padding: "2px 8px", borderRadius: 99, background: "var(--desp-bg)", color: "var(--desp)" }}>
            <Icon name="alert-triangle" size={11} /> Voltou — não foi cumprida
          </span>
        )}
        <span className="grow" />
        <span className="row gap1" style={{ fontSize: 11, color: "var(--muted)" }}><Icon name="zap" size={11} color="var(--accent)" /> {s.impacto}</span>
      </div>
      <p className="pretty" style={{ fontSize: 13, color: "var(--ink)", lineHeight: 1.45, margin: 0 }}>{s.sugestao}</p>
      <div className="row gap1 wrap" style={{ marginTop: 8, alignItems: "center" }}>
        <button onClick={() => onMarcar("realizada")} disabled={pendente} className="row gap1 btn-ok btn-sm" style={{ fontSize: 11.5, padding: "4px 10px", borderRadius: 7 }}>
          <Icon name="check" size={13} strokeWidth={2.6} /> Realizada
        </button>
        <button onClick={() => onMarcar("dispensada")} disabled={pendente} className="row gap1" style={{ fontSize: 11.5, padding: "4px 10px", borderRadius: 7, border: "1px solid var(--line)", background: "#fff", color: "var(--muted)" }}>
          <Icon name="x" size={13} /> Dispensar
        </button>
        {(s.situacao || s.causa || s.comportamentos.length > 0) && (
          <>
            <span className="grow" />
            <button onClick={() => setOpen((v) => !v)} className="row gap1" style={{ border: "none", background: "none", color: "var(--accent)", fontSize: 11.5, fontWeight: 600, padding: 0 }}>
              <Icon name={open ? "chevron-up" : "chevron-down"} size={12} /> {open ? "ocultar" : "detalhes"}
            </button>
          </>
        )}
      </div>
      {open && (
        <div className="col" style={{ gap: 5, marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--line-2)", fontSize: 12, color: "var(--text)" }}>
          {s.situacao && <p style={{ margin: 0 }}><b style={{ color: "var(--ink)" }}>Situação. </b>{s.situacao}</p>}
          {s.causa && <p style={{ margin: 0 }}><b style={{ color: "var(--ink)" }}>Causa provável. </b>{s.causa}</p>}
          {s.comportamentos.length > 0 && (
            <div className="row wrap" style={{ gap: 4, marginTop: 2 }}>
              {s.comportamentos.map((c) => <code key={c} style={{ fontSize: 10, background: "var(--line-2)", color: "var(--text)", padding: "1px 6px", borderRadius: 5 }} className="font-mono">{c}</code>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Aprendizado({ det }: { det: DetMock }) {
  const o = det.origens, total = o.auto + o.humano + o.pendente;
  const autoPct = total > 0 ? Math.round((o.auto / total) * 100) : 0;
  const data = [{ v: o.auto, c: "var(--p-300)", n: "Auto-validados" }, { v: o.humano, c: "var(--p-700)", n: "Validados por você" }, { v: o.pendente, c: "var(--line)", n: "Pendentes" }];
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Estado do aprendizado" ajuda="Como cada evento foi confirmado. O Prism confirma sozinho quando reconhece um label já validado 2× ou mais." leitura={`O Prism já confirma ${autoPct}% dos eventos sozinho.`} />
      <div className="row gap4" style={{ alignItems: "center" }}>
        <Donut data={data} size={130} thickness={20} centerLabel={`${autoPct}%`} centerSub="automático" />
        <ul className="col grow" style={{ gap: 9, listStyle: "none", padding: 0, margin: 0 }}>
          {data.map((d) => (
            <li key={d.n} className="row gap2" style={{ fontSize: 12.5 }}>
              <i style={{ width: 10, height: 10, borderRadius: 3, background: d.c }} />
              <span className="grow" style={{ color: "var(--text)" }}>{d.n}</span>
              <b className="tnum" style={{ color: "var(--ink)" }}>{d.v}</b>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

function ResumoOportunidades({ det }: { det: DetMock }) {
  const c: Record<string, number> = { alta: 0, media: 0, info: 0 };
  det.sugestoes.forEach((s) => { c[s.prioridade] = (c[s.prioridade] || 0) + 1; });
  const max = Math.max(1, ...Object.values(c));
  const cor: Record<string, string> = { alta: "var(--desp)", media: "var(--apoio)", info: "var(--info)" };
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Resumo das oportunidades" ajuda="A dimensão do todo num relance, por prioridade." />
      <div className="col" style={{ gap: 9 }}>
        {["alta", "media", "info"].map((p) => (
          <div key={p} className="row gap2" style={{ fontSize: 12.5 }}>
            <span style={{ width: 48, textTransform: "capitalize", color: "var(--muted)" }}>{p}</span>
            <div className="grow track" style={{ height: 8 }}><i style={{ width: `${(c[p] / max) * 100}%`, background: cor[p] }} /></div>
            <b className="tnum" style={{ width: 18, textAlign: "right", color: "var(--ink)" }}>{c[p]}</b>
          </div>
        ))}
      </div>
    </Card>
  );
}

function ComposicaoPanel({ det }: { det: DetMock }) {
  const s = det.snapshot;
  const data = [{ v: s.va, c: "var(--va)", n: "Valor agregado" }, { v: s.apoio, c: "var(--apoio)", n: "Apoio" }, { v: s.desp, c: "var(--desp)", n: "Desperdício" }, { v: s.none, c: "var(--none)", n: "Não classificado" }];
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Composição de valor (Lean)" ajuda="Como o tempo total se distribui entre valor agregado, apoio e desperdício. A categoria vem da IA — você pode reclassificar." leitura="Há espaço claro para mover tempo de Apoio/Desperdício para Valor Agregado." />
      <div className="row gap4" style={{ alignItems: "center", justifyContent: "center" }}>
        <Donut data={data} size={168} thickness={26} centerLabel={`${s.va}%`} centerSub="valor" />
        <ul className="col" style={{ gap: 10, listStyle: "none", padding: 0, margin: 0 }}>
          {data.map((d) => (
            <li key={d.n} className="row gap2" style={{ fontSize: 12.5 }}>
              <i style={{ width: 11, height: 11, borderRadius: 3, background: d.c }} />
              <span style={{ color: "var(--text)", minWidth: 116 }}>{d.n}</span>
              <b className="tnum" style={{ color: "var(--ink)" }}>{d.v}%</b>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

function ParetoPanel({ det }: { det: DetMock }) {
  const data = det.pareto;
  const W = 460, H = 220, padL = 8, padR = 8, padT = 14, padB = 44;
  const bw = (W - padL - padR) / Math.max(1, data.length);
  const x = (i: number) => padL + i * bw;
  const yT = (pct: number) => padT + (1 - pct / 100) * (H - padT - padB);
  const idx80 = data.findIndex((d) => d.acc >= 80);
  const pts = data.map((d, i) => `${x(i) + bw / 2},${yT(d.acc)}`).join(" ");
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Pareto do tempo" ajuda="Comportamentos ordenados por tempo, com a curva de % acumulado. Mostra a regra 80/20." leitura={idx80 >= 0 ? `80% do tempo está em ${idx80 + 1} comportamentos (regra 80/20).` : "Tempo distribuído entre vários comportamentos."} />
      {data.length === 0 ? <p style={{ fontSize: 13, color: "var(--muted)" }}>Sem dados ainda.</p> : (
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}>
          {[0, 25, 50, 75, 100].map((g) => <line key={g} x1={padL} x2={W - padR} y1={yT(g)} y2={yT(g)} stroke="var(--line-2)" />)}
          {data.map((d, i) => {
            const top = yT(d.pct), h = (H - padT - padB) - (top - padT);
            return <rect key={i} x={x(i) + 3} y={top} width={bw - 6} height={Math.max(2, h)} rx="4" fill={leanCor(d.cat)} opacity={d.cat === "none" ? 0.5 : 0.92} />;
          })}
          <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="2.2" />
          {data.map((d, i) => <circle key={i} cx={x(i) + bw / 2} cy={yT(d.acc)} r="3" fill="var(--accent)" />)}
          {data.map((d, i) => <text key={i} x={x(i) + bw / 2} y={H - padB + 14} fontSize="8.5" textAnchor="end" transform={`rotate(-32 ${x(i) + bw / 2} ${H - padB + 14})`} fill="var(--muted)" fontFamily="var(--mono)">{d.nome}</text>)}
        </svg>
      )}
    </Card>
  );
}

// Fase 17/18 — Insights rápidos: só as frases prontas com números (leitura de 5s).
// O "tempo por ação" saiu daqui — vive agora no painel "Onde o tempo vai" abaixo.
function InsightsNumericos({ iq }: { iq: InsightsQuantitativos | null }) {
  if (!iq || !iq.frases?.length) return null;
  const tomCor: Record<string, string> = {
    high: leanCor("desp"),
    warn: "#c98a00",
    ok: leanCor("va"),
    info: "var(--faint)",
  };
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Leitura rápida"
        ajuda="Resumo direto, calculado dos vídeos: onde o tempo foi, produtivo vs desperdício, por ROI e a tendência. Junta os 2 ângulos das câmeras."
        leitura="Sem achismo — só os números que importam pro chão de fábrica."
      />
      <div className="col" style={{ gap: 9 }}>
        {iq.frases.map((f, i) => (
          <div key={i} style={{ borderLeft: `3px solid ${tomCor[f.tom] || "var(--faint)"}`, paddingLeft: 11 }}>
            <span style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.45 }}>{f.texto}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function TempoPorComportamento({ det, denso, processoId }: { det: DetMock; denso: boolean; processoId: string }) {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<string | null>(null);
  const [verTodos, setVerTodos] = useState(false);
  const setCat = useMutation({
    mutationFn: ({ id, cat }: { id: string; cat: LeanShort }) => api.comportamentos.setCategoria(id, leanLong(cat)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dashboard", processoId] }); qc.invalidateQueries({ queryKey: ["processos"] }); setEdit(null); toast("Anotado. O Prism vai classificar parecidos sozinho.", { icon: "check" }); },
  });
  const total = det.comportamentos.length;
  const lista: CompMock[] = verTodos ? det.comportamentos : det.comportamentos.slice(0, COMP_VISIVEL_PADRAO);
  const marca: Record<string, { m: string; c: string }> = { humano: { m: "check", c: "var(--accent-deep)" }, aprendido: { m: "rotate-ccw", c: "var(--va)" }, ia: { m: "sparkles", c: "var(--faint)" } };
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Tempo por comportamento"
        ajuda="Os comportamentos que mais consomem tempo. Clique no chip de categoria para reclassificar — sua decisão vale para comportamentos de mesmo nome em outros processos."
        leitura="A cor diz se aquele tempo está agregando valor ou não."
        right={total > COMP_VISIVEL_PADRAO ? <span style={{ fontSize: 12, color: "var(--muted)" }}>{lista.length} de {total}</span> : undefined}
      />
      <ul className="col" style={{ gap: 11, listStyle: "none", padding: 0, margin: 0 }}>
        {lista.map((d) => {
          const editing = edit === d.id;
          return (
            <li key={d.id}>
              <div className="row gap2" style={{ marginBottom: 4 }}>
                <span className="truncate" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)", maxWidth: 180 }}>{d.nome}</span>
                {editing ? (
                  <span className="row gap1" style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 8, padding: "2px 4px", boxShadow: "var(--glow)" }}>
                    {(["va", "apoio", "desp"] as LeanShort[]).map((c) => <button key={c} onClick={() => d.id && setCat.mutate({ id: d.id, cat: c })} title={leanLabel(c)} style={{ width: 18, height: 18, borderRadius: 5, border: d.cat === c ? "2px solid var(--ink)" : "none", background: leanCor(c) }} />)}
                    <button onClick={() => setEdit(null)} className="center" style={{ width: 18, height: 18, border: "none", background: "none", color: "var(--faint)" }}><Icon name="x" size={12} /></button>
                  </span>
                ) : (
                  <button onClick={() => setEdit(d.id)} className="row gap1" style={{ fontSize: 10.5, padding: "2px 7px", borderRadius: 7, border: "1px solid var(--line)", background: "#fff", color: "var(--text)" }}>
                    <i style={{ width: 8, height: 8, borderRadius: 2, background: leanCor(d.cat) }} />{leanLabel(d.cat)}
                    {d.origem && marca[d.origem] && <Icon name={marca[d.origem].m} size={10} color={marca[d.origem].c} />}
                  </button>
                )}
                <span className="grow" />
                <span className="tnum" style={{ fontSize: 12, color: "var(--muted)", fontWeight: 600 }}>{d.pct}% · {fmtSeg(d.seg)}</span>
              </div>
              <div className="track" style={{ height: 7 }}><i style={{ width: `${Math.max(2, d.pct)}%`, background: leanCor(d.cat) }} /></div>
            </li>
          );
        })}
      </ul>
      {total > COMP_VISIVEL_PADRAO && (
        <div className="row" style={{ justifyContent: "center", marginTop: 10 }}>
          <button onClick={() => setVerTodos((v) => !v)} className="row gap1" style={{ border: "1px solid var(--line)", background: "#fff", color: "var(--accent)", borderRadius: 99, padding: "6px 14px", fontSize: 12, fontWeight: 600 }}>
            <Icon name={verTodos ? "chevron-up" : "chevron-down"} size={14} />
            {verTodos ? "Mostrar menos" : `Ver todos (${total})`}
          </button>
        </div>
      )}
    </Card>
  );
}

function FluxoPanel({ det }: { det: DetMock }) {
  const max = Math.max(1, ...det.transicoes.map((t) => t.vezes));
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Fluxo de atividades" ajuda="Sequências A→B mais frequentes observadas (por pessoa, no mesmo vídeo). O fluxo real vs. o esperado." leitura="O fluxo real que o Prism observou no seu chão de fábrica." />
      {det.transicoes.length === 0 ? <p style={{ fontSize: 13, color: "var(--muted)" }}>Sem sequências suficientes ainda.</p> : (
        <div className="col" style={{ gap: 9 }}>
          {det.transicoes.map((tr, i) => (
            <div key={i} className="row gap2" style={{ fontSize: 11.5 }}>
              <code className="truncate font-mono" style={{ flex: 1, textAlign: "right", background: "var(--line-2)", color: "var(--text)", padding: "3px 8px", borderRadius: 6 }}>{tr.de}</code>
              <Icon name="arrow-right" size={13} color="var(--faint)" />
              <code className="truncate font-mono" style={{ flex: 1, background: "var(--accent-soft)", color: "var(--accent-deep)", padding: "3px 8px", borderRadius: 6 }}>{tr.para}</code>
              <div className="track" style={{ width: 56, height: 6, flex: "none" }}><i style={{ width: `${(tr.vezes / max) * 100}%`, background: "var(--accent)" }} /></div>
              <span className="tnum" style={{ width: 26, textAlign: "right", color: "var(--muted)", fontWeight: 600 }}>{tr.vezes}×</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function VideosRodape({ det }: { det: DetMock }) {
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Vídeos processados" ajuda="Cada novo vídeo enriquece a base e melhora as sugestões." right={<span style={{ fontSize: 12, color: "var(--muted)" }}>{det.videos.length} no total · {det.snapshot.tempoObservadoMin} min</span>} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: "4px 24px" }}>
        {det.videos.map((v) => (
          <div key={v.id} className="row gap2" style={{ padding: "9px 0", borderBottom: "1px solid var(--line-2)" }}>
            <span className="center" style={{ width: 30, height: 30, borderRadius: 8, background: "var(--soft)", color: "var(--accent)", flex: "none" }}><Icon name="film" size={14} /></span>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="truncate font-mono" style={{ fontSize: 11.5, color: "var(--text)" }}>{v.nome}</div>
              <div style={{ fontSize: 10.5, color: "var(--faint)" }}>{v.quando} · {v.eventos} eventos · {fmtSeg(v.dur)}</div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
