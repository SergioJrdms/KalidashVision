// ============================================================
// Dashboard do processo — porte fiel de dashboard.jsx (dados reais).
// t.dashboard = "equilibrado" | "minimal" | "denso"
// ============================================================
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { mapDashboard, type DetMock, type CompMock, type ProcHeaderMock } from "../lib/adapt";
import { leanCor, leanLabel, leanLong, fmtSeg, type LeanShort } from "../design/helpers";
import { Btn, Card, Icon, Prism, Help, PrioBadge, MaturityMeter, LeanBar, Donut, PanelHead, Empty, toast } from "../design/ui";
import type { Go } from "../design/Shell";
import type { Tweaks } from "../App";

export default function Dashboard({ proc, go, t }: { proc: ProcHeaderMock; go: Go; t: Tweaks }) {
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
  const minimal = t.dashboard === "minimal";
  const denso = t.dashboard === "denso";
  const s = det.snapshot;
  return (
    <div className="col" style={{ gap: 18 }}>
      <DashHeader proc={proc} det={det} go={go} />
      <LearningStrip proc={proc} det={det} go={go} />

      <div style={{ display: "grid", gridTemplateColumns: minimal ? "repeat(auto-fit,minmax(200px,1fr))" : "repeat(auto-fit,minmax(190px,1fr))", gap: 14 }}>
        <KpiVA det={det} />
        <Kpi label="Tempo observado" valor={`${s.tempoObservadoMin} min`} sub={`${s.videos} vídeos`} icon="clock" ajuda="Soma da duração dos vídeos analisados. Quanto maior, mais robusta a base." />
        <Kpi label="Onde o tempo se concentra" valor={`${s.topComportamento.pct}%`} sub={s.topComportamento.nome} icon="crosshair" ajuda="Comportamento que mais consome tempo — melhor candidato a otimização." />
        {!minimal && <Kpi label="Oportunidades alta prioridade" valor={String(det.sugestoes.filter((x) => x.prioridade === "alta").length)} sub="sugestões" icon="flame" alert ajuda="Sugestões marcadas como ALTA pela IA. Resolva por aqui primeiro." />}
        <Kpi label="Confiança nos dados" valor={`${s.validadoPct}%`} sub="validado por humano" icon="shield-check" ajuda="Quanto da base já foi confirmado por uma pessoa." />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: minimal ? "1fr" : "1.65fr 1fr", gap: 16, alignItems: "start" }}>
        <Sugestoes det={det} minimal={minimal} />
        <div className="col" style={{ gap: 16 }}>
          <Aprendizado det={det} />
          {!minimal && <ResumoOportunidades det={det} />}
        </div>
      </div>

      {!minimal && (
        <div>
          <div className="row gap2" style={{ marginBottom: 4 }}>
            <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>Sua operação em várias óticas</h2>
            <Help text="Painéis para entender como o tempo é gasto e como as atividades se sequenciam — tudo derivado dos vídeos deste processo." />
          </div>
          <p style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 14 }}>Visão consolidada de todos os vídeos. As sugestões acima nascem dessa base.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(380px,1fr))", gap: 16 }}>
            <ComposicaoPanel det={det} />
            <ParetoPanel det={det} />
            <TempoPorComportamento det={det} denso={denso} processoId={proc.id} />
            <FluxoPanel det={det} />
          </div>
        </div>
      )}

      <VideosRodape det={det} />
    </div>
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

function Sugestoes({ det, minimal }: { det: DetMock; minimal: boolean }) {
  const [prio, setPrio] = useState("todas");
  const lista = useMemo(() => det.sugestoes.filter((x) => prio === "todas" || x.prioridade === prio), [prio, det]);
  const visiveis = minimal ? lista.slice(0, 3) : lista;
  const counts: Record<string, number> = { todas: det.sugestoes.length, alta: 0, media: 0, info: 0 };
  det.sugestoes.forEach((x) => { counts[x.prioridade] = (counts[x.prioridade] || 0) + 1; });
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Sugestões de produtividade" ajuda="Geradas pela IA combinando seus dados agregados com os 7 desperdícios do Lean. Mais vídeos e validações = mais precisas." right={<span style={{ fontSize: 12, color: "var(--muted)" }}>{visiveis.length} de {det.sugestoes.length}</span>} />
      <div className="row gap1 wrap" style={{ marginBottom: 14 }}>
        {["todas", "alta", "media", "info"].map((p) => (
          <button key={p} onClick={() => setPrio(p)} style={{ padding: "4px 11px", borderRadius: 99, fontSize: 12, fontWeight: 600, border: "1px solid", borderColor: prio === p ? "var(--accent)" : "var(--line)", background: prio === p ? "var(--accent)" : "#fff", color: prio === p ? "#fff" : "var(--muted)" }}>
            {p === "todas" ? "Todas" : p[0].toUpperCase() + p.slice(1)} · {counts[p] || 0}
          </button>
        ))}
      </div>
      <div className="col" style={{ gap: 10 }}>
        {visiveis.length === 0 ? <p style={{ fontSize: 13, color: "var(--muted)" }}>Nenhuma sugestão para o filtro.</p> : visiveis.map((sug) => <SugestaoCard key={sug.id} s={sug} />)}
      </div>
    </Card>
  );
}
function SugestaoCard({ s }: { s: DetMock["sugestoes"][number] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card-flat hoverlift" style={{ padding: 14, borderColor: "var(--line)" }}>
      <div className="row gap2 wrap" style={{ marginBottom: 6 }}>
        <PrioBadge p={s.prioridade} />
        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text)" }}>{s.area}</span>
        <span className="grow" />
        <span className="row gap1" style={{ fontSize: 11.5, color: "var(--muted)" }}><Icon name="zap" size={12} color="var(--accent)" /> impacto {s.impacto}</span>
      </div>
      <p className="pretty" style={{ fontSize: 13.5, color: "var(--ink)", lineHeight: 1.5 }}>{s.sugestao}</p>
      {(s.situacao || s.causa) && (
        <button onClick={() => setOpen((v) => !v)} className="row gap1" style={{ border: "none", background: "none", color: "var(--accent)", fontSize: 12, fontWeight: 600, marginTop: 8, padding: 0 }}>
          <Icon name={open ? "chevron-up" : "chevron-down"} size={13} /> {open ? "ocultar" : "ver situação e causa"}
        </button>
      )}
      {open && (
        <div className="col" style={{ gap: 6, marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--line-2)", fontSize: 12.5, color: "var(--text)" }}>
          {s.situacao && <p><b style={{ color: "var(--ink)" }}>Situação. </b>{s.situacao}</p>}
          {s.causa && <p><b style={{ color: "var(--ink)" }}>Causa provável. </b>{s.causa}</p>}
          {s.comportamentos.length > 0 && (
            <div className="row wrap" style={{ gap: 5, marginTop: 2 }}>
              {s.comportamentos.map((c) => <code key={c} style={{ fontSize: 10.5, background: "var(--line-2)", color: "var(--text)", padding: "2px 7px", borderRadius: 6 }} className="font-mono">{c}</code>)}
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

function TempoPorComportamento({ det, denso, processoId }: { det: DetMock; denso: boolean; processoId: string }) {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<string | null>(null);
  const setCat = useMutation({
    mutationFn: ({ id, cat }: { id: string; cat: LeanShort }) => api.comportamentos.setCategoria(id, leanLong(cat)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dashboard", processoId] }); qc.invalidateQueries({ queryKey: ["processos"] }); setEdit(null); toast("Anotado. O Prism vai classificar parecidos sozinho.", { icon: "check" }); },
  });
  const lista: CompMock[] = denso ? det.comportamentos : det.comportamentos.slice(0, 7);
  const marca: Record<string, { m: string; c: string }> = { humano: { m: "check", c: "var(--accent-deep)" }, aprendido: { m: "rotate-ccw", c: "var(--va)" }, ia: { m: "sparkles", c: "var(--faint)" } };
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Tempo por comportamento" ajuda="Os comportamentos que mais consomem tempo. Clique no chip de categoria para reclassificar — sua decisão vale para comportamentos de mesmo nome em outros processos." leitura="A cor diz se aquele tempo está agregando valor ou não." />
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
