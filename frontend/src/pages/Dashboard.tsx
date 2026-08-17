// ============================================================
// Dashboard do processo — layout executivo (Fase 18/19): placar do processo,
// KPIs "1 olhada", leitura rápida, onde o tempo vai e perguntas pro gestor.
// É o dashboard PADRÃO — sem variantes (minimal/denso) por tweak.
// ============================================================
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { mapDashboard, type DetMock, type CompMock, type ProcHeaderMock, type SugMock } from "../lib/adapt";
import { leanCor, leanLabel, leanLong, leanShort, type LeanShort } from "../design/helpers";
import { Btn, Card, Icon, Prism, Help, PrioBadge, MaturityMeter, LeanBar, Donut, PanelHead, Empty, Ring, toast } from "../design/ui";
import { leituraDoPosto, nomeHumano } from "../design/rotulos";
import type { Go } from "../design/Shell";
import type { AcaoSugestao, InsightsQuantitativos, Permanencia, PerguntaGestor, PlacarProcesso } from "../lib/types";
import { useIsMobile } from "../hooks/useIsMobile";

const SUG_VISIVEL_PADRAO = 3;
const COMP_VISIVEL_PADRAO = 5;

export default function Dashboard({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const isMobile = useIsMobile();
  const [janela, setJanela] = useState<1 | 7 | 30>(7);
  const q = useQuery({
    queryKey: ["dashboard", proc.id, janela],
    queryFn: () => api.processos.dashboard(proc.id, janela),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  if (q.isLoading) return <Card><Empty icon="loader" title="Carregando dashboard…" /></Card>;
  if (!q.data) return <Card><Empty icon="alert-triangle" title="Não foi possível carregar" /></Card>;
  const det = mapDashboard(q.data);
  if (det.snapshot.videos === 0) {
    return (
      <Card>
        <Empty icon="video" title="Aguardando a primeira captura do posto" desc="Assim que a captura automática for processada, esta tela mostrará presença, posto vazio e produtividade." />
      </Card>
    );
  }
  const p = det.produtividade;
  if (!p) {
    return (
      <Card style={{ padding: 24 }}>
        <Empty
          icon="alert-triangle"
          title="Leitura do posto indisponível"
          desc="A plataforma ainda não recebeu uma leitura compatível de presença e produtividade. Nenhum indicador antigo será exibido como substituto."
        />
      </Card>
    );
  }

  const atual = p.sem_dado
    ? { titulo: "Sem leitura válida", detalhe: "Ainda não há uma leitura compatível neste período.", cor: "#6f5e87", fundo: "var(--soft)", icone: "help-circle" as IconeNome }
    : p.captura_atrasada
      ? { titulo: "Captura desatualizada", detalhe: "A última leitura foi preservada, mas não representa o estado atual.", cor: "#a46c00", fundo: "#fff6df", icone: "clock-alert" as IconeNome }
      : leituraAtual(p.estado_atual);
  const dataLeitura = formatarLeitura(p.estado_atual.leitura_em);
  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row wrap" style={{ gap: 14, alignItems: "flex-end", justifyContent: "space-between" }}>
        <div className="col" style={{ gap: 4 }}>
          <span style={{ color: "var(--accent)", fontSize: 11, fontWeight: 800, letterSpacing: ".08em", textTransform: "uppercase" }}>Visão do posto</span>
          <h1 className="font-display" style={{ margin: 0, color: "var(--ink)", fontSize: isMobile ? 25 : 31, lineHeight: 1.1 }}>Produtividade do operador</h1>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13.5 }}>{proc.nome} · torno mecânico</p>
        </div>
        <div className="row" style={{ gap: 5, padding: 4, border: "1px solid var(--line)", borderRadius: 11, background: "#fff" }} aria-label="Período analisado">
          {([1, 7, 30] as const).map((dias) => (
            <button
              key={dias}
              onClick={() => setJanela(dias)}
              aria-pressed={janela === dias}
              style={{
                border: 0,
                borderRadius: 8,
                padding: "7px 11px",
                background: janela === dias ? "var(--grad-cta)" : "transparent",
                color: janela === dias ? "#fff" : "var(--muted)",
                fontSize: 12,
                fontWeight: 750,
                cursor: "pointer",
              }}
            >
              {dias === 1 ? "1 dia" : `${dias} dias`}
            </button>
          ))}
        </div>
      </div>

      <Card style={{ padding: isMobile ? 18 : "20px 22px", borderLeft: `4px solid ${atual.cor}` }}>
        <div className="row wrap" style={{ gap: 14, justifyContent: "space-between", alignItems: "center" }}>
          <div className="row" style={{ gap: 12, minWidth: 0 }}>
            <span className="center" style={{ width: 42, height: 42, flex: "none", borderRadius: 12, background: atual.fundo, color: atual.cor }}>
              <Icon name={atual.icone} size={21} />
            </span>
            <div className="col" style={{ gap: 2, minWidth: 0 }}>
              <span style={{ color: "var(--muted)", fontSize: 10.5, fontWeight: 800, textTransform: "uppercase", letterSpacing: ".07em" }}>Última leitura</span>
              <strong style={{ color: "var(--ink)", fontSize: isMobile ? 16 : 19 }}>{atual.titulo}</strong>
              <span style={{ color: "var(--muted)", fontSize: 12 }}>{atual.detalhe}</span>
            </div>
          </div>
          <div className="col" style={{ gap: 5, alignItems: isMobile ? "flex-start" : "flex-end" }}>
            <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{dataLeitura}</span>
            <span style={{ padding: "5px 9px", borderRadius: 999, background: p.publicavel ? "#eaf7ef" : "#fff6df", color: p.publicavel ? "#187a43" : "#8a5a00", fontSize: 10.5, fontWeight: 800 }}>
              {p.sem_dado ? "Sem dados válidos no período" : p.captura_atrasada ? "Aguardando nova captura" : p.publicavel ? "Leitura pronta para apresentação" : "Leitura em calibração"}
            </span>
          </div>
        </div>
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "repeat(3,minmax(0,1fr))", gap: 14 }}>
        <KpiComercial titulo="Produtividade" valor={p.produtividade_pct} detalhe="entre leituras com operador identificado e decisão válida" cor="#187a43" icone="gauge" />
        <KpiComercial titulo="Operador no posto" valor={p.presenca_pct} detalhe="entre as leituras de presença válidas" cor="var(--accent)" icone="user-check" />
        <KpiComercial titulo="Posto vazio" valor={p.posto_vazio_pct} detalhe="entre as leituras de presença válidas" cor="#b74a3a" icone="user-x" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.15fr .85fr", gap: 14, alignItems: "stretch" }}>
        <Card style={{ padding: 20 }}>
          <div className="row" style={{ justifyContent: "space-between", gap: 12, marginBottom: 18 }}>
            <div>
              <h2 className="font-display" style={{ margin: 0, fontSize: 17 }}>Leitura do período</h2>
              <p style={{ margin: "4px 0 0", color: "var(--muted)", fontSize: 12 }}>Percentuais calculados somente sobre leituras válidas do posto.</p>
            </div>
          </div>
          <BarraComercial
            titulo="Produtividade com operador presente"
            partes={[
              { nome: "Produtivo", valor: p.produtividade_pct, cor: "#2e9d62" },
              { nome: "Improdutivo", valor: p.improdutividade_pct, cor: "#d66755" },
            ]}
          />
          <div style={{ height: 18 }} />
          <BarraComercial
            titulo="Ocupação do posto"
            partes={[
              { nome: "Operador", valor: p.presenca_pct, cor: "var(--accent)" },
              { nome: "Outra pessoa", valor: p.outra_pessoa_no_posto_pct, cor: "#c79232" },
              { nome: "Vazio", valor: p.posto_vazio_pct, cor: "#d9dde6" },
            ]}
          />
        </Card>

        <Card style={{ padding: 20 }}>
          <h2 className="font-display" style={{ margin: 0, fontSize: 17 }}>Qualidade da leitura</h2>
          <p style={{ margin: "4px 0 16px", color: "var(--muted)", fontSize: 12 }}>Incerteza fica visível; não vira improdutividade.</p>
          <QualidadeItem titulo="Cobertura da identificação" valor={p.cobertura_identificacao_pct} />
          <QualidadeItem titulo="Decisão de produtividade" valor={p.cobertura_produtividade_pct} />
          <div style={{ marginTop: 16, padding: "11px 12px", borderRadius: 10, background: "var(--soft)", color: "var(--text)", fontSize: 12.5, lineHeight: 1.45 }}>
            {p.sem_dado
              ? "Ainda não há leituras válidas neste período. A plataforma não exibe zero como se fosse um resultado."
              : p.inconclusivo_pct != null && p.inconclusivo_pct > 0
              ? `${p.inconclusivo_pct.toFixed(1)}% das leituras ficaram inconclusivas e foram excluídas da decisão.`
              : "Nenhuma leitura inconclusiva neste período."}
          </div>
        </Card>
      </div>

      <SerieComercial pontos={p.serie_diaria} />
    </div>
  );
}

type IconeNome = Parameters<typeof Icon>[0]["name"];

function formatarLeitura(iso: string | null): string {
  if (!iso) return "Sem horário de captura disponível";
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return "Horário de captura indisponível";
  return `Capturado em ${new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(data)}`;
}

function leituraAtual(estado: NonNullable<DetMock["produtividade"]>["estado_atual"]): { titulo: string; detalhe: string; cor: string; fundo: string; icone: IconeNome } {
  if (estado.presenca === "posto_vazio") return { titulo: "Posto vazio", detalhe: "Nenhum operador identificado no posto.", cor: "#b74a3a", fundo: "#fff0ed", icone: "user-x" };
  if (estado.presenca === "fora_do_posto") return { titulo: "Operador fora do posto", detalhe: "Há outra pessoa na área, mas não o operador.", cor: "#a46c00", fundo: "#fff6df", icone: "users" };
  if (estado.presenca === "no_posto" && estado.produtividade === "produtivo") return { titulo: "Operador no posto · produtivo", detalhe: "Mão no torno ou atenção voltada para a operação.", cor: "#187a43", fundo: "#eaf7ef", icone: "circle-check" };
  if (estado.presenca === "no_posto" && estado.produtividade === "improdutivo") return { titulo: "Operador no posto · improdutivo", detalhe: "Sem interação ou atenção ao torno nesta leitura.", cor: "#b74a3a", fundo: "#fff0ed", icone: "circle-alert" };
  if (estado.presenca === "no_posto") return { titulo: "Operador no posto", detalhe: "A produtividade desta leitura ficou inconclusiva.", cor: "#6f5e87", fundo: "var(--soft)", icone: "help-circle" };
  return { titulo: "Leitura inconclusiva", detalhe: "Não foi possível identificar o operador com segurança.", cor: "#6f5e87", fundo: "var(--soft)", icone: "help-circle" };
}

function KpiComercial({ titulo, valor, detalhe, cor, icone }: { titulo: string; valor: number | null; detalhe: string; cor: string; icone: IconeNome }) {
  return (
    <Card style={{ padding: 20 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 12 }}>
        <span style={{ color: "var(--muted)", fontSize: 11, fontWeight: 800, textTransform: "uppercase", letterSpacing: ".06em" }}>{titulo}</span>
        <Icon name={icone} size={18} color={cor} />
      </div>
      <div className="font-display tnum" style={{ marginTop: 12, color: valor == null ? "var(--faint)" : cor, fontSize: 38, lineHeight: 1, fontWeight: 750 }}>
        {valor == null ? "—" : `${valor.toFixed(1)}%`}
      </div>
      <p style={{ margin: "9px 0 0", color: "var(--muted)", fontSize: 11.5, lineHeight: 1.4 }}>{detalhe}</p>
    </Card>
  );
}

function BarraComercial({ titulo, partes }: { titulo: string; partes: Array<{ nome: string; valor: number | null; cor: string }> }) {
  const validas = partes.filter((p) => p.valor != null && p.valor > 0);
  return (
    <div>
      <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--text)", marginBottom: 8 }}>{titulo}</div>
      <div className="row" style={{ height: 12, overflow: "hidden", borderRadius: 999, background: "var(--line-2)", gap: 0 }}>
        {validas.map((p) => <span key={p.nome} style={{ height: "100%", width: `${p.valor}%`, background: p.cor }} title={`${p.nome}: ${p.valor?.toFixed(1)}%`} />)}
      </div>
      <div className="row wrap" style={{ gap: 12, marginTop: 9 }}>
        {partes.map((p) => (
          <span key={p.nome} className="row" style={{ gap: 5, color: "var(--muted)", fontSize: 11 }}>
            <i style={{ width: 8, height: 8, borderRadius: 999, background: p.cor }} />
            {p.nome} <strong className="tnum" style={{ color: "var(--text)" }}>{p.valor == null ? "—" : `${p.valor.toFixed(1)}%`}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

function QualidadeItem({ titulo, valor }: { titulo: string; valor: number | null }) {
  const pct = Math.max(0, Math.min(100, valor ?? 0));
  const cor = pct >= 80 ? "#2e9d62" : pct >= 60 ? "#c79232" : "#d66755";
  return (
    <div style={{ marginTop: 13 }}>
      <div className="row" style={{ justifyContent: "space-between", fontSize: 12, marginBottom: 6 }}>
        <span style={{ color: "var(--text)", fontWeight: 650 }}>{titulo}</span>
        <strong className="tnum" style={{ color: cor }}>{valor == null ? "—" : `${valor.toFixed(1)}%`}</strong>
      </div>
      <div style={{ height: 7, borderRadius: 999, background: "var(--line-2)", overflow: "hidden" }}><div style={{ width: `${pct}%`, height: "100%", background: cor, borderRadius: 999 }} /></div>
    </div>
  );
}

function SerieComercial({ pontos }: { pontos: NonNullable<DetMock["produtividade"]>["serie_diaria"] }) {
  if (pontos.length < 2) return null;
  return (
    <Card style={{ padding: 20 }}>
      <h2 className="font-display" style={{ margin: 0, fontSize: 17 }}>Evolução diária</h2>
      <p style={{ margin: "4px 0 17px", color: "var(--muted)", fontSize: 12 }}>Produtividade e presença no mesmo instrumento.</p>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${pontos.length}, minmax(34px,1fr))`, gap: 8, minHeight: 150, alignItems: "end", overflowX: "auto" }}>
        {pontos.map((ponto) => (
          <div key={ponto.dia} className="col" role="group" aria-label={`${ponto.dia}: produtividade ${ponto.produtividade_pct ?? "sem dado"}; presença ${ponto.presenca_pct ?? "sem dado"}`} style={{ gap: 7, alignItems: "center", minWidth: 42 }}>
            <div className="row" style={{ height: 108, width: "100%", maxWidth: 42, alignItems: "end", gap: 3 }}>
              {ponto.produtividade_pct != null && <span aria-hidden="true" style={{ width: "50%", height: `${ponto.produtividade_pct}%`, borderRadius: "5px 5px 2px 2px", background: "#2e9d62" }} />}
              {ponto.presenca_pct != null && <span aria-hidden="true" style={{ width: "50%", height: `${ponto.presenca_pct}%`, borderRadius: "5px 5px 2px 2px", background: "var(--accent)" }} />}
            </div>
            <span style={{ fontSize: 9.5, color: "var(--muted)", whiteSpace: "nowrap" }}>{ponto.dia.slice(8, 10)}/{ponto.dia.slice(5, 7)}</span>
            <span className="tnum" style={{ fontSize: 8.5, color: "var(--muted)", whiteSpace: "nowrap" }}>P {ponto.produtividade_pct == null ? "—" : `${ponto.produtividade_pct.toFixed(0)}%`}</span>
            <span className="tnum" style={{ fontSize: 8.5, color: "var(--muted)", whiteSpace: "nowrap" }}>O {ponto.presenca_pct == null ? "—" : `${ponto.presenca_pct.toFixed(0)}%`}</span>
          </div>
        ))}
      </div>
      <div className="row" style={{ gap: 14, marginTop: 10, fontSize: 11, color: "var(--muted)" }}><span>● <b style={{ color: "#2e9d62" }}>Produtividade</b></span><span>● <b style={{ color: "var(--accent)" }}>Presença</b></span></div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Fase 21 — Seção visual: gráficos e óticas complementares, abaixo das
// sugestões. O topo responde em 1 olhada; aqui é onde o gestor se aprofunda.
// ═══════════════════════════════════════════════════════════════════════
function OperacaoEmGraficos({ det, iq, processoId }: { det: DetMock; iq: InsightsQuantitativos | null; processoId: string }) {
  return (
    <div>
      <div className="row gap2" style={{ marginBottom: 4 }}>
        <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>A operação em gráficos</h2>
        <Help text="Visões complementares calculadas dos mesmos vídeos: como o processo evolui, em que horas rende, onde o tempo se concentra e como as atividades se sequenciam." />
      </div>
      <p style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 14 }}>Os números do topo, agora em imagem — para investigar padrões e contar a história da operação.</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%, 380px),1fr))", gap: 16 }}>
        <EvolucaoPanel processoId={processoId} />
        <RitmoDoDia iq={iq} />
        <ParetoPanel det={det} />
        <ComposicaoPanel det={det} />
        <FluxoPanel det={det} />
        <Aprendizado det={det} />
      </div>
    </div>
  );
}

// Evolução por vídeo: colunas 100% empilhadas (produtivo × não-produtivo),
// em ordem cronológica — a história do processo.
function EvolucaoPanel({ processoId }: { processoId: string }) {
  const q = useQuery({ queryKey: ["serie", processoId], queryFn: () => api.padroes.serie(processoId) });
  const pontos = (q.data?.pontos || []).slice(-16); // últimos 16 vídeos
  const rotulo = (p: { processado_em: string | null }, i: number): string => {
    if (p.processado_em) {
      const d = new Date(p.processado_em);
      if (!Number.isNaN(d.getTime())) return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
    }
    return `V${i + 1}`;
  };
  const W = 460, H = 210, padT = 8, padB = 26, padL = 8, padR = 8;
  const n = Math.max(1, pontos.length);
  const slot = (W - padL - padR) / n;
  const bw = Math.min(30, slot - 6);
  const plotH = H - padT - padB;
  // Fase 63: duas fatias, e elas fecham 100%.
  const CATS: Array<{ k: string; cat: LeanShort }> = [
    { k: "valor_agregado", cat: "va" },
    { k: "desperdicio", cat: "desp" },
  ];
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Evolução por vídeo"
        ajuda="Cada coluna é um vídeo (em ordem cronológica), dividida em produtivo e não-produtivo. Mostra se a operação está melhorando de um vídeo para o outro."
        leitura="Verde crescendo = o processo está aprendendo a render."
      />
      {pontos.length < 2 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Precisa de pelo menos 2 vídeos para mostrar a evolução.</p>
      ) : (
        <>
          <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} role="img" aria-label="Evolução da composição do tempo por vídeo">
            {[0, 25, 50, 75, 100].map((g) => (
              <line key={g} x1={padL} x2={W - padR} y1={padT + (1 - g / 100) * plotH} y2={padT + (1 - g / 100) * plotH} stroke="var(--line-2)" />
            ))}
            {/* Rótulos esparsos: no máx. ~6, senão colidem com muitas colunas. */}
            {pontos.map((p, i) => {
              const sc = p.share_categoria || {};
              const va = Math.max(0, sc["valor_agregado"] || 0);
              // O resto do tempo é não-produtivo: não existe fatia sem dono.
              const vals: Record<string, number> = { valor_agregado: va, desperdicio: Math.max(0, 100 - va) };
              const x = padL + i * slot + (slot - bw) / 2;
              let yTopo = H - padB; // empilha de baixo (produtivo) pra cima
              const tip = `${rotulo(p, i)} — produtivo ${Math.round(va)}% · não-produtivo ${Math.round(100 - va)}%`;
              const passoRotulo = Math.ceil(pontos.length / 6);
              const mostraRotulo = i % passoRotulo === 0;
              return (
                <g key={p.video_id || i}>
                  {CATS.map(({ k, cat }) => {
                    const h = (vals[k] / 100) * plotH;
                    if (h <= 0.5) return null;
                    yTopo -= h;
                    return <rect key={k} x={x} y={yTopo + 1} width={bw} height={Math.max(1, h - 2)} rx="2.5" fill={leanCor(cat)} opacity={0.92}><title>{tip}</title></rect>;
                  })}
                  {mostraRotulo && <text x={x + bw / 2} y={H - padB + 14} fontSize="9" textAnchor="middle" fill="var(--muted)" fontFamily="var(--mono)">{rotulo(p, i)}</text>}
                </g>
              );
            })}
          </svg>
          <div className="row wrap" style={{ gap: 10, fontSize: 11, color: "var(--muted)", marginTop: 8 }}>
            {CATS.map(({ cat }) => (
              <span key={cat} className="row" style={{ gap: 5 }}>
                <i style={{ width: 9, height: 9, borderRadius: 3, background: leanCor(cat) }} /> {leanLabel(cat)}
              </span>
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

// Ritmo do dia: em que horas o processo rende e em que horas trava —
// agregado pelo relógio REAL dos vídeos (todos os dias juntos).
function RitmoDoDia({ iq }: { iq: InsightsQuantitativos | null }) {
  const horas = iq?.por_hora || [];
  const maxSeg = Math.max(1, ...horas.map((h) => h.seg));
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Ritmo do dia"
        ajuda="O tempo de cada hora do relógio (somando todos os dias filmados), dividido em produtivo e não-produtivo. Revela padrões estruturais: começo de turno lento, queda pós-almoço, fim de dia disperso."
        leitura="Procure a hora mais vermelha — é onde a rotina trava todo dia."
      />
      {horas.length < 2 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Ainda não há horas suficientes com atividade para desenhar o ritmo.</p>
      ) : (
        <>
          <ul className="col" style={{ gap: 10, listStyle: "none", padding: 0, margin: 0 }}>
            {horas.map((h) => {
              return (
                <li key={h.hora} className="row gap2" title={`${h.hora}h — ${Math.round(h.va_pct)}% produtivo · ${Math.round(h.desp_pct)}% desperdício`}>
                  <span className="tnum" style={{ width: 34, fontSize: 12, fontWeight: 700, color: "var(--text)", flex: "none" }}>{String(h.hora).padStart(2, "0")}h</span>
                  <div className="grow" style={{ opacity: 0.45 + 0.55 * (h.seg / maxSeg) }}>
                    <LeanBar va={h.va_pct} desp={h.desp_pct} height={10} />
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="row wrap" style={{ gap: 10, fontSize: 11, color: "var(--muted)", marginTop: 10 }}>
            {(["va", "desp"] as LeanShort[]).map((c) => (
              <span key={c} className="row" style={{ gap: 5 }}>
                <i style={{ width: 9, height: 9, borderRadius: 3, background: leanCor(c) }} /> {leanLabel(c)}
              </span>
            ))}
            <span style={{ marginLeft: "auto" }}>barra mais forte = mais tempo filmado naquela hora</span>
          </div>
        </>
      )}
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Fase 101 — PERMANÊNCIA: o número principal.
//
// "a única coisa que a gente conseguiu medir é tempo de permanência"
// (Fernando, 12/08). Frames → detecção → está no polígono do posto? → acumula.
//
// ⛔ SÓ PERCENTUAL, NUNCA DURAÇÃO. A captura é uma amostra sistemática de ~50%
// de cada hora: o percentual é estimativa não-enviesada do turno, o minuto
// absoluto seria METADE da verdade. Mostrar minuto seria ERRADO, não só feio.
// ═══════════════════════════════════════════════════════════════════════
function PermanenciaHero({ p }: { p: Permanencia | null }) {
  if (!p || p.sem_dado) {
    return (
      <Card style={{ padding: "20px 22px" }}>
        <div className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
          Permanência no posto
        </div>
        <p style={{ fontSize: 14, color: "var(--muted)", margin: "6px 0 0" }}>
          Ainda não há observação suficiente para medir o turno.
        </p>
      </Card>
    );
  }
  const cor = p.no_posto_pct >= 75 ? leanCor("va") : p.no_posto_pct >= 55 ? "#c98a00" : leanCor("desp");
  return (
    <Card style={{ padding: "22px 24px", borderLeft: `3px solid ${cor}`, background: "linear-gradient(120deg, var(--soft), #fff 60%)" }}>
      <div className="row gap4 wrap" style={{ alignItems: "center" }}>
        <Ring pct={p.no_posto_pct} size={104} stroke={10} color={cor}>
          <div className="col" style={{ alignItems: "center", lineHeight: 1 }}>
            <span className="font-display tnum" style={{ fontSize: 30, fontWeight: 700, color: "var(--ink)" }}>
              {p.no_posto_pct.toFixed(0)}%
            </span>
            <span style={{ fontSize: 9, color: "var(--muted)", fontWeight: 700 }}>NO POSTO</span>
          </div>
        </Ring>
        <div className="col grow" style={{ gap: 8, minWidth: 240 }}>
          <div className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
            Permanência no posto
            <Help width={280} text="Contado direto: para cada instante observado, o sistema verifica se há alguém dentro do polígono do posto. Não depende do que a câmera achou que a pessoa estava fazendo — só de onde ela estava." />
          </div>
          <p style={{ fontSize: 17, fontWeight: 700, color: "var(--ink)", margin: 0, lineHeight: 1.35 }}>
            {p.frase}
          </p>
          {/* A ÁRVORE — três folhas (duas enquanto a orientação não é
              verificada). Somam 100% do tempo observado, sem sobra. */}
          <ArvorePermanencia p={p} />
          {!p.orientacao_verificada && (
            <span style={{ fontSize: 11.5, color: "var(--faint)", lineHeight: 1.5 }}>
              O detalhe de para onde o operador estava voltado ainda não é
              mostrado: a referência da câmera não foi confirmada com dado. Melhor
              um número simples e certo do que um detalhe que pode estar invertido.
            </span>
          )}
        </div>
      </div>
    </Card>
  );
}

function ArvorePermanencia({ p }: { p: Permanencia }) {
  const folhas = p.detalhado && p.no_posto_torno_pct !== null
    ? [
        { rot: "No posto, voltado para o torno", pct: p.no_posto_torno_pct as number, cor: leanCor("va") },
        { rot: "No posto, voltado para outro lado", pct: p.no_posto_outro_lado_pct as number, cor: "#c98a00" },
        { rot: "Fora do posto", pct: p.fora_pct, cor: leanCor("desp") },
      ]
    : [
        { rot: "No posto", pct: p.no_posto_pct, cor: leanCor("va") },
        { rot: "Fora do posto", pct: p.fora_pct, cor: leanCor("desp") },
      ];
  return (
    <div className="col" style={{ gap: 4, marginTop: 2 }}>
      {folhas.map((f) => (
        <div key={f.rot} className="row gap2" style={{ alignItems: "center", fontSize: 12.5 }}>
          <span style={{ width: 9, height: 9, borderRadius: 3, background: f.cor, flexShrink: 0 }} />
          <span style={{ color: "var(--text)" }}>{f.rot}</span>
          <span className="grow" />
          <span className="tnum" style={{ fontWeight: 700, color: "var(--ink)" }}>{f.pct.toFixed(0)}%</span>
        </div>
      ))}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════
// Fase 19 — Placar do processo: "rodou a X% do seu melhor dia".
// O processo comparado com ELE MESMO — máximo julgamento, zero micro-gestão.
// Acende com 2+ dias; 1 dia → compara por sessão; 1 sessão → linha de base.
// ═══════════════════════════════════════════════════════════════════════
function PlacarHero({ placar }: { placar: PlacarProcesso | null }) {
  if (!placar) return null;
  const p = placar;
  const ref = p.modo === "referencia";
  const uni = p.unidade === "sessão" ? "sessão" : "dia";      // singular
  const unis = uni === "dia" ? "dias" : "sessões";            // plural

  // Modo REFERÊNCIA: uma unidade só — é a linha de base do processo.
  if (ref) {
    const va = p.dia_atual.va_pct;
    const cor = va >= 60 ? leanCor("va") : va >= 40 ? "#c98a00" : leanCor("desp");
    return (
      <Card style={{ padding: "20px 22px", borderLeft: `3px solid ${cor}`, background: "linear-gradient(120deg, var(--soft), #fff 60%)" }}>
        <div className="row gap4 wrap" style={{ alignItems: "center" }}>
          <Ring pct={va} size={92} stroke={9} color={cor}>
            <div className="col" style={{ alignItems: "center", lineHeight: 1 }}>
              <span className="font-display tnum" style={{ fontSize: 26, fontWeight: 700, color: "var(--ink)" }}>{va.toFixed(0)}%</span>
              <span style={{ fontSize: 9, color: "var(--muted)", fontWeight: 700 }}>produtivo</span>
            </div>
          </Ring>
          <div className="col grow" style={{ gap: 6, minWidth: 220 }}>
            <div className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
              Placar do processo <Help text={`Ainda não há histórico suficiente para comparar ${unis}. Esta primeira medição vira a linha de base — os próximos ${unis} serão pontuados contra ela.`} width={250} />
            </div>
            <p style={{ fontSize: 15.5, fontWeight: 700, color: "var(--ink)", margin: 0 }}>
              Linha de base: <span style={{ color: cor }}>{va.toFixed(0)}% produtivo</span>.
            </p>
            <p style={{ fontSize: 12.5, color: "var(--muted)", margin: 0 }}>
              Primeira medição do processo. A partir dela, cada {uni} novo é comparado com o melhor já observado.
            </p>
          </div>
        </div>
      </Card>
    );
  }

  // Modo COMPARATIVO.
  const bom = p.eh_melhor || p.score >= 85;
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
            Placar do processo <Help text={`Compara ${uni === "dia" ? "o dia mais recente" : "a sessão mais recente"} com ${uni === "dia" ? "o melhor dia" : "a melhor sessão"} já observado deste processo (maior % de tempo produtivo). Base: ${p.n_unidades} ${unis} com observação suficiente.`} width={250} />
          </div>
          {p.eh_melhor ? (
            <p style={{ fontSize: 15.5, fontWeight: 700, color: "var(--ink)", margin: 0 }}>
              {p.dia_atual.dia} é {uni === "dia" ? "o melhor dia" : "a melhor sessão"} <span style={{ color: leanCor("va") }}>já observado</span> — {p.dia_atual.va_pct.toFixed(0)}% produtivo.
            </p>
          ) : (
            <p style={{ fontSize: 15.5, fontWeight: 700, color: "var(--ink)", margin: 0 }}>
              O processo rodou a <span style={{ color: cor }}>{p.score}% do seu melhor {uni}</span>.
            </p>
          )}
          {!p.eh_melhor && (
            <p style={{ fontSize: 12.5, color: "var(--muted)", margin: 0 }}>
              {p.dia_atual.dia}: <b style={{ color: "var(--text)" }}>{p.dia_atual.va_pct.toFixed(0)}% produtivo</b> · melhor {uni} {p.dia_melhor.dia}: <b style={{ color: "var(--text)" }}>{p.dia_melhor.va_pct.toFixed(0)}% produtivo</b>
            </p>
          )}
          {p.ganho && (
            <div className="row gap1" style={{ marginTop: 2, padding: "6px 10px", borderRadius: 8, background: "var(--accent-soft)", width: "fit-content", maxWidth: "100%" }}>
              <Icon name="gem" size={14} color="var(--accent-deep)" />
              <span style={{ fontSize: 12.5, color: "var(--accent-deep)", fontWeight: 600 }}>
                {/* ⛔ Fase 101 — o ganho vira PONTOS PERCENTUAIS. Ele era
                    exibido em horas por turno e por mês; com captura amostrada
                    a ~50% de cada hora, essas horas eram metade da verdade e a
                    projeção mensal multiplicava o erro por 22. */}
                Rodando como o melhor {uni}, o turno ganha <b>+{(p.dia_melhor.va_pct - p.dia_atual.va_pct).toFixed(0)} pontos percentuais</b>.
              </span>
            </div>
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
              Vs {unis} anteriores: desperdício {vs.antes.toFixed(0)}% → {vs.atual.toFixed(0)}% ({vs.delta_pp > 0 ? "+" : "−"}{Math.abs(vs.delta_pp).toFixed(0)} pts)
            </span>
          )}
        </div>
      </div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════════
// Fase 20/63 — Próximo passo: a ÚNICA ação de maior alavancagem agora.
// Prioridade: (1) tempo classificado por SUPOSIÇÃO ≥15% — hoje ele conta como
// não-produtivo e pode estar subestimando o placar → (2) fila de validação
// grande → (3) desperdício relevante. Nada disparou → some.
// ═══════════════════════════════════════════════════════════════════════
function ProximoPasso({ det, proc, go }: { det: DetMock; proc: ProcHeaderMock; go: Go }) {
  const semEvidPct = det.snapshot.semEvidencia;
  const desp = det.insights?.por_categoria?.["desperdicio"];

  let passo: { titulo: string; desc: string; cta: string; onClick: () => void } | null = null;
  if (semEvidPct >= 15) {
    passo = {
      titulo: "Resolva o tempo que o sistema assumiu",
      desc: `${Math.round(semEvidPct)}% do tempo está contando como NÃO-produtivo porque ninguém decidiu — o sistema teve de escolher sem evidência. Se parte disso agrega valor, seu placar está pior do que a realidade.`,
      cta: "Abrir dúvidas",
      onClick: () => go("processo", proc.id, "duvidas"),
    };
  } else if (proc.pendencias >= 30) {
    passo = {
      titulo: `Valide ${proc.pendencias} eventos e ensine o Prism`,
      desc: "Cada validação vira um padrão que o Prism aplica sozinho nos próximos vídeos — a fila encolhe de forma permanente e os números ficam mais confiáveis.",
      cta: "Ir para a validação",
      onClick: () => go("processo", proc.id, "validacao"),
    };
  } else if ((desp?.pct ?? 0) >= 15) {
    passo = {
      titulo: "Ataque o desperdício",
      desc: `${Math.round(desp!.pct)}% do tempo é desperdício. As perguntas e sugestões abaixo mostram por onde começar.`,
      cta: "Ver o que fazer",
      onClick: () => document.getElementById("painel-sugestoes")?.scrollIntoView({ behavior: "smooth", block: "start" }),
    };
  }
  if (!passo) return null;
  return (
    <Card style={{ padding: "14px 18px", borderLeft: "3px solid var(--accent)" }}>
      <div className="row gap3 wrap" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div className="row gap3" style={{ minWidth: 0, flex: 1 }}>
          <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--accent-soft)", color: "var(--accent-deep)", flex: "none" }}>
            <Icon name="crosshair" size={17} />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="row gap2" style={{ alignItems: "baseline" }}>
              <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--accent-deep)" }}>Próximo passo</span>
              <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>{passo.titulo}</span>
            </div>
            <p className="pretty" style={{ fontSize: 12.5, color: "var(--muted)", margin: "2px 0 0" }}>{passo.desc}</p>
          </div>
        </div>
        <Btn icon="arrow-right" onClick={passo.onClick} style={{ flex: "none" }}>{passo.cta}</Btn>
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
              
            </div>
            <LeanBar va={r.va_pct} desp={Math.max(0, 100 - r.va_pct)} />
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
  const vazioPct = Math.round(s.vazio || 0);
  const vaPct = va ? Math.round(va.pct) : s.va;
  const trend = iq?.periodo?.tendencia_desp_pp ?? null;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 14 }}>
      <KpiHero
        label="Tempo desperdiçado"
        valor={`${despPct}%`}
        // Fase 56: o TOTAL não muda — posto vazio segue somando aqui. O
        // subtítulo separa "operador ausente" de "operador presente sem agregar
        // valor": são problemas com causas e ações diferentes.
        sub={vazioPct > 0 ? `${vazioPct} pts são posto vazio` : undefined}
        icon="alert-triangle"
        cor={leanCor("desp")}
        ajuda="Parte do tempo observado classificada como desperdício (Lean). Inclui o posto vazio (operador ausente), destacado à parte porque a ação para resolver é outra."
      />
      <KpiHero
        label="Tempo produtivo"
        valor={`${vaPct}%`}
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

// Fase 96: a frase que o dono de fábrica lê sem precisar de alguém ao lado.
function LeituraEmPortugues({ det }: { det: DetMock }) {
  const s = det.snapshot;
  const l = leituraDoPosto({
    vaPct: s.va, vazioPct: s.vazio,
    limiarCoberturaMin: s.coberturaMin,   // limiar interno, não exibido
    semEvidenciaPct: s.semEvidencia,
  });
  const cor = l.tom === "ok" ? leanCor("va") : l.tom === "atencao" ? "var(--apoio)" : "var(--muted)";
  return (
    <Card style={{ padding: "16px 20px", borderLeft: `4px solid ${cor}` }}>
      <p style={{ margin: 0, fontSize: 17, lineHeight: 1.5, color: "var(--ink)", fontWeight: 600 }}>
        {l.frase}
      </p>
      {l.ressalva && (
        <p style={{ margin: "7px 0 0", fontSize: 13, lineHeight: 1.5, color: "var(--muted)" }}>
          {l.ressalva}
        </p>
      )}
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
      <div style={{ marginTop: 10 }}><LeanBar va={s.va} desp={s.desp} vazio={s.vazio || 0} showLegend /></div>
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
    <Card id="painel-sugestoes" style={{ padding: 18 }}>
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
              {s.comportamentos.map((c) => <span key={c} style={{ fontSize: 10.5, background: "var(--line-2)", color: "var(--text)", padding: "1px 6px", borderRadius: 5 }}>{nomeHumano(c)}</span>)}
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
  const data = [
    { v: s.va, c: "var(--va)", n: "Produtivo" },
    { v: Math.max(0, s.desp - (s.vazio || 0)), c: "var(--desp)", n: "Desperdício" },
    { v: s.vazio || 0, c: "#8a8598", n: "Posto vazio" },
  ];
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead titulo="Produtivo × não-produtivo" ajuda="Como o tempo observado se divide entre produtivo (agrega valor ao produto) e não-produtivo. São as duas únicas categorias: todo minuto cai em uma delas. 'Posto vazio' é um pedaço do não-produtivo, separado porque a causa é outra. Você pode reclassificar qualquer comportamento." leitura="Há espaço claro para mover tempo de Desperdício para Produtivo." />
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
            return <rect key={i} x={x(i) + 3} y={top} width={bw - 6} height={Math.max(2, h)} rx="4" fill={leanCor(d.cat)} opacity={0.92} />;
          })}
          <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="2.2" />
          {data.map((d, i) => <circle key={i} cx={x(i) + bw / 2} cy={yT(d.acc)} r="3" fill="var(--accent)" />)}
          {data.map((d, i) => <text key={i} x={x(i) + bw / 2} y={H - padB + 14} fontSize="8.5" textAnchor="end" transform={`rotate(-32 ${x(i) + bw / 2} ${H - padB + 14})`} fill="var(--muted)">{nomeHumano(d.nome)}</text>)}
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

function TempoPorComportamento({ det, processoId }: { det: DetMock; processoId: string }) {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<string | null>(null);
  const [verTodos, setVerTodos] = useState(false);
  // Reclassifica pelo RÓTULO, não pelo id do catálogo: rótulo que ainda não
  // entrou em `comportamentos` vinha sem id, e o clique morria em silêncio —
  // justamente nos rótulos sem linha no catálogo, que era onde o cinza caía.
  const setCat = useMutation({
    mutationFn: ({ label, cat }: { label: string; cat: LeanShort }) =>
      api.comportamentos.setCategoriaPorLabel(processoId, label, leanLong(cat)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dashboard", processoId] }); qc.invalidateQueries({ queryKey: ["processos"] }); setEdit(null); toast("Anotado. O Prism vai classificar parecidos sozinho.", { icon: "check" }); },
    onError: (e: Error) => toast(`Não deu para reclassificar: ${e.message}`, { color: "var(--desp)" }),
  });
  const total = det.comportamentos.length;
  const lista: CompMock[] = verTodos ? det.comportamentos : det.comportamentos.slice(0, COMP_VISIVEL_PADRAO);
  const marca: Record<string, { m: string; c: string }> = { humano: { m: "check", c: "var(--accent-deep)" }, aprendido: { m: "rotate-ccw", c: "var(--va)" }, ia: { m: "sparkles", c: "var(--faint)" } };
  return (
    <Card id="painel-tempo-comportamento" style={{ padding: 20 }}>
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
                <span className="truncate" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)", maxWidth: 180 }} title={d.nome}>{nomeHumano(d.nome)}</span>
                {editing ? (
                  <span className="row gap1" style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 8, padding: "2px 4px", boxShadow: "var(--glow)" }}>
                    {(["va", "desp"] as LeanShort[]).map((c) => <button key={c} disabled={setCat.isPending} onClick={() => setCat.mutate({ label: d.nome, cat: c })} title={leanLabel(c)} style={{ width: 18, height: 18, borderRadius: 5, border: d.cat === c ? "2px solid var(--ink)" : "none", background: leanCor(c), opacity: setCat.isPending ? 0.5 : 1 }} />)}
                    <button onClick={() => setEdit(null)} className="center" style={{ width: 18, height: 18, border: "none", background: "none", color: "var(--faint)" }}><Icon name="x" size={12} /></button>
                  </span>
                ) : (
                  <button onClick={() => setEdit(d.id)} className="row gap1" style={{ fontSize: 10.5, padding: "2px 7px", borderRadius: 7, border: "1px solid var(--line)", background: "#fff", color: "var(--text)" }}>
                    <i style={{ width: 8, height: 8, borderRadius: 2, background: leanCor(d.cat) }} />{leanLabel(d.cat)}
                    {d.origem && marca[d.origem] && <Icon name={marca[d.origem].m} size={10} color={marca[d.origem].c} />}
                  </button>
                )}
                <span className="grow" />
                <span className="tnum" style={{ fontSize: 12, color: "var(--muted)", fontWeight: 600 }}>{d.pct}%</span>
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
              <span className="truncate" style={{ flex: 1, textAlign: "right", background: "var(--line-2)", color: "var(--text)", padding: "3px 8px", borderRadius: 6 }} title={tr.de}>{nomeHumano(tr.de)}</span>
              <Icon name="arrow-right" size={13} color="var(--faint)" />
              <span className="truncate" style={{ flex: 1, background: "var(--accent-soft)", color: "var(--accent-deep)", padding: "3px 8px", borderRadius: 6 }} title={tr.para}>{nomeHumano(tr.para)}</span>
              <div className="track" style={{ width: 56, height: 6, flex: "none" }}><i style={{ width: `${(tr.vezes / max) * 100}%`, background: "var(--accent)" }} /></div>
              <span className="tnum" style={{ width: 26, textAlign: "right", color: "var(--muted)", fontWeight: 600 }}>{tr.vezes}×</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
