import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import {
  Badge,
  Btn,
  Card,
  Empty,
  Help,
  Icon,
  LeanBar,
  PanelHead,
  Spinner,
  Track,
  fmtSeg,
  leanCor,
  leanLabel,
  leanShort,
  toast,
} from "../components/UIKit";
import { PrismAvatar } from "../components/PrismAvatar";
import type {
  CategoriaLean,
  DashboardData,
  DistribuicaoComportamento,
  ParetoItem,
  Sugestao,
} from "../lib/types";

export default function Dashboard() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api.processos.dashboard(id!),
    enabled: !!id,
  });

  if (isLoading)
    return (
      <div className="center" style={{ padding: 60 }}>
        <Spinner size={28} />
      </div>
    );
  if (!data) return null;
  const s = data.snapshot;

  if (s.eventos_considerados === 0)
    return (
      <Card style={{ padding: 6 }}>
        <Empty
          icon="video"
          title="Nenhum vídeo processado ainda"
          desc="Envie seu primeiro vídeo. Em poucos minutos você verá comportamentos, distribuição do tempo e as primeiras sugestões."
          action={
            <Link to={`/processos/${id}/upload`}>
              <Btn icon="upload">Enviar vídeo</Btn>
            </Link>
          }
        />
      </Card>
    );

  const topComp =
    s.distribuicao_comportamentos[0] || { comportamento: "—", pct_tempo: 0 };
  const sugAlta = data.sugestoes.filter(
    (x) => (x.prioridade || "").toLowerCase() === "alta"
  ).length;

  return (
    <div className="col" style={{ gap: 18 }}>
      <LearningStrip data={data} processoId={id!} />

      {/* KPIs */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
          gap: 14,
        }}
      >
        <KpiVA cv={data.composicao_valor} />
        <Kpi
          icon="clock"
          label="Tempo observado"
          valor={`${s.tempo_total_observado_min} min`}
          sub={`${s.videos_analisados} vídeos`}
          ajuda="Soma da duração dos vídeos analisados. Quanto maior, mais robusta a base."
        />
        <Kpi
          icon="crosshair"
          label="Onde o tempo se concentra"
          valor={`${topComp.pct_tempo}%`}
          sub={topComp.comportamento}
          ajuda="Comportamento que mais consome tempo — melhor candidato a otimização."
        />
        <Kpi
          icon="flame"
          label="Oportunidades alta prioridade"
          valor={String(sugAlta)}
          sub="sugestões"
          alert={sugAlta > 0}
          ajuda="Sugestões marcadas como ALTA pela IA. Resolva por aqui primeiro."
        />
        <Kpi
          icon="shield-check"
          label="Confiança nos dados"
          valor={`${s.pct_validado_por_humano}%`}
          sub="validado por humano"
          ajuda="Quanto da base já foi confirmado por uma pessoa."
        />
      </div>

      {/* Banners contextuais (pendências) */}
      {(data.perguntas_pendentes > 0 || data.eventos_pendentes > 0) && (
        <div
          style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 12 }}
        >
          {data.perguntas_pendentes > 0 && (
            <Banner
              tone="purple"
              icone="message-square-question"
              titulo={`O Prism tem ${data.perguntas_pendentes} ${data.perguntas_pendentes === 1 ? "pergunta" : "perguntas"}`}
              sub="Cada resposta vira contexto do seu domínio."
              cta={
                <Link to={`/processos/${id}/validacao`}>
                  <Btn size="sm" variant="secondary">Responder</Btn>
                </Link>
              }
            />
          )}
          {data.eventos_pendentes > 0 && (
            <Banner
              tone="info"
              icone="git-pull-request-arrow"
              titulo={`${data.eventos_pendentes} eventos esperando você`}
              sub="A cada label confirmado 2× o Prism passa a confirmar sozinho."
              cta={
                <Link to={`/processos/${id}/validacao`}>
                  <Btn size="sm" variant="secondary">Validar</Btn>
                </Link>
              }
            />
          )}
        </div>
      )}

      {/* Sugestões + lateral (Aprendizado + Resumo) */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.65fr 1fr",
          gap: 16,
          alignItems: "start",
        }}
      >
        <Sugestoes lista={data.sugestoes} />
        <div className="col" style={{ gap: 16 }}>
          <AprendizadoPanel origens={data.origens} processoId={id!} />
          <ResumoOportunidades sugestoes={data.sugestoes} />
          {data.padroes_resumo && data.padroes_resumo.length > 0 && (
            <PadroesResumoPanel padroes={data.padroes_resumo} processoId={id!} />
          )}
        </div>
      </div>

      {/* Várias óticas */}
      <div>
        <div className="row gap2" style={{ marginBottom: 8 }}>
          <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>
            Sua operação em várias óticas
          </h2>
          <Help text="Painéis para entender como o tempo é gasto e como as atividades se sequenciam — derivado dos vídeos deste processo." />
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(440px, 1fr))",
            gap: 16,
          }}
        >
          <ComposicaoValorPanel cv={data.composicao_valor} />
          <ParetoPanel pareto={data.pareto} />
          <TempoPorComportamentoPanel
            distribuicao={data.snapshot.distribuicao_comportamentos}
          />
          <FluxoPanel transicoes={data.transicoes} />
        </div>
      </div>

      {/* Vídeos */}
      <VideosPanel videos={data.videos} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Aprendizado strip (introduz o processo + maturidade fictícia desta tela)
// ════════════════════════════════════════════════════════════════════════
function LearningStrip({ data, processoId }: { data: DashboardData; processoId: string }) {
  return (
    <Card
      style={{
        padding: 16,
        background: "linear-gradient(135deg, var(--accent-soft), #fff 70%)",
        border: "1px solid var(--p-200)",
      }}
    >
      <div className="row gap3" style={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="row gap3">
          <PrismAvatar size={42} ring />
          <div>
            <div style={{ fontSize: 11, color: "var(--accent-deep)", fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase" }}>
              O Prism aprendeu
            </div>
            <div style={{ fontSize: 14, color: "var(--text)", marginTop: 4 }}>
              {data.snapshot.eventos_considerados.toLocaleString("pt-BR")} eventos · {data.snapshot.tempo_total_observado_min} min de vídeo · {data.snapshot.pct_validado_por_humano}% validado
            </div>
          </div>
        </div>
        <Link to={`/processos/${processoId}/upload`}>
          <Btn size="sm" icon="upload">Novo vídeo</Btn>
        </Link>
      </div>
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// KPIs
// ════════════════════════════════════════════════════════════════════════
function KpiVA({ cv }: { cv: DashboardData["composicao_valor"] }) {
  return (
    <Card style={{ padding: 16, borderLeft: "4px solid var(--accent)" }}>
      <div className="row gap2" style={{ color: "var(--muted)", fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".08em" }}>
        <Icon name="award" size={14} color="var(--accent)" />
        Índice de valor agregado
        <Help text="% do tempo em comportamentos classificados como 'valor agregado' (Lean). É a métrica-rainha da análise de valor: quanto da operação realmente entrega o que o cliente paga." />
      </div>
      <div className="font-display tnum" style={{ fontSize: 28, fontWeight: 800, color: "var(--ink)", marginTop: 8 }}>
        {cv.valor_agregado_pct}%
      </div>
      <div style={{ marginTop: 10 }}>
        <LeanBar
          va={cv.valor_agregado_pct}
          apoio={cv.apoio_pct}
          desp={cv.desperdicio_pct}
          none={cv.nao_classificado_pct}
        />
        <div className="row wrap" style={{ gap: 10, marginTop: 6, fontSize: 10.5, color: "var(--muted)" }}>
          <LegItem cor="var(--va)" t={`VA ${cv.valor_agregado_pct}%`} />
          <LegItem cor="var(--apoio)" t={`Apoio ${cv.apoio_pct}%`} />
          <LegItem cor="var(--desp)" t={`Desp ${cv.desperdicio_pct}%`} />
          {cv.nao_classificado_pct > 0 && <LegItem cor="var(--none)" t={`? ${cv.nao_classificado_pct}%`} />}
        </div>
      </div>
    </Card>
  );
}
function LegItem({ cor, t }: { cor: string; t: string }) {
  return (
    <span className="row gap1">
      <span style={{ width: 8, height: 8, borderRadius: 2, background: cor, display: "inline-block" }} /> {t}
    </span>
  );
}
function Kpi({
  icon, label, valor, sub, ajuda, alert,
}: {
  icon: string;
  label: string;
  valor: string;
  sub?: string;
  ajuda: string;
  alert?: boolean;
}) {
  return (
    <Card style={{ padding: 16, borderLeft: alert ? "4px solid var(--apoio)" : undefined }}>
      <div className="row gap2" style={{ color: "var(--muted)", fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".08em" }}>
        <Icon name={icon} size={14} color={alert ? "var(--apoio)" : "var(--accent)"} />
        <span className="truncate">{label}</span>
        <Help text={ajuda} />
      </div>
      <div className="font-display tnum truncate" style={{ fontSize: 24, fontWeight: 800, color: "var(--ink)", marginTop: 6 }}>
        {valor}
      </div>
      {sub && (
        <div className="truncate" style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
          {sub}
        </div>
      )}
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Banner pequeno
// ════════════════════════════════════════════════════════════════════════
function Banner({
  tone, icone, titulo, sub, cta,
}: {
  tone: "purple" | "info";
  icone: string;
  titulo: string;
  sub: string;
  cta: React.ReactNode;
}) {
  const bg = tone === "purple" ? "var(--accent-soft)" : "var(--info-bg)";
  const fg = tone === "purple" ? "var(--accent-deep)" : "var(--info)";
  return (
    <Card style={{ padding: 14, background: bg, border: `1px solid ${tone === "purple" ? "var(--p-200)" : "rgba(47,107,216,.2)"}` }}>
      <div className="row gap3" style={{ alignItems: "center" }}>
        <span style={{ width: 34, height: 34, borderRadius: "50%", background: "#fff", display: "grid", placeItems: "center", flex: "none" }}>
          <Icon name={icone} size={17} color={fg} />
        </span>
        <div className="grow col" style={{ gap: 2 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: fg }}>{titulo}</div>
          <div style={{ fontSize: 11.5, color: "var(--muted)" }}>{sub}</div>
        </div>
        {cta}
      </div>
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Sugestões + filtros
// ════════════════════════════════════════════════════════════════════════
type Prio = "todas" | "alta" | "media" | "info";
function Sugestoes({ lista }: { lista: Sugestao[] }) {
  const [prio, setPrio] = useState<Prio>("todas");
  const cont = { alta: 0, media: 0, info: 0 } as Record<"alta" | "media" | "info", number>;
  lista.forEach((s) => {
    const p = (s.prioridade || "").toLowerCase() as "alta" | "media" | "info";
    if (p in cont) cont[p]++;
  });
  const ordem = { alta: 0, media: 1, info: 2 } as Record<string, number>;
  const filtradas = lista
    .filter((s) => prio === "todas" || (s.prioridade || "").toLowerCase() === prio)
    .sort((a, b) => (ordem[(a.prioridade || "").toLowerCase()] ?? 3) - (ordem[(b.prioridade || "").toLowerCase()] ?? 3));

  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Sugestões de produtividade"
        ajuda="Geradas pela IA combinando seus dados agregados com os 7 desperdícios do Lean. Mais vídeos e validações = mais precisão."
        leitura="As de ALTA são as que mais consomem tempo sem agregar valor."
      />
      <div className="row gap1 wrap" style={{ marginBottom: 14, fontSize: 12 }}>
        {(["todas", "alta", "media", "info"] as Prio[]).map((p) => (
          <button
            key={p}
            onClick={() => setPrio(p)}
            style={{
              padding: "4px 11px",
              borderRadius: 999,
              border: `1px solid ${prio === p ? "var(--accent)" : "var(--line)"}`,
              background: prio === p ? "var(--accent)" : "#fff",
              color: prio === p ? "#fff" : "var(--text)",
              fontWeight: 600,
            }}
          >
            {p === "todas"
              ? `Todas · ${lista.length}`
              : `${p[0].toUpperCase()}${p.slice(1)} · ${cont[p]}`}
          </button>
        ))}
      </div>
      {filtradas.length === 0 && (
        <p style={{ fontSize: 13, color: "var(--muted)", padding: 14, textAlign: "center" }}>
          Nenhuma sugestão para o filtro.
        </p>
      )}
      <div className="col gap2">
        {filtradas.map((s) => (
          <SugestaoCard key={s.id} s={s} />
        ))}
      </div>
    </Card>
  );
}

function SugestaoCard({ s }: { s: Sugestao }) {
  const [aberto, setAberto] = useState(false);
  const tone =
    (s.prioridade || "").toLowerCase() === "alta"
      ? "high"
      : (s.prioridade || "").toLowerCase() === "media"
        ? "warn"
        : "info";
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        borderRadius: 12,
        padding: 14,
        background: "#fff",
      }}
    >
      <div className="row gap2 wrap" style={{ marginBottom: 6 }}>
        <Badge tone={tone as "high" | "warn" | "info"}>{(s.prioridade || "INFO").toUpperCase()}</Badge>
        {s.area && <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text)" }}>{s.area}</span>}
      </div>
      <p style={{ fontSize: 14, color: "var(--ink)", lineHeight: 1.55 }}>{s.sugestao}</p>
      <div className="row" style={{ justifyContent: "space-between", marginTop: 8, fontSize: 12, color: "var(--muted)" }}>
        <span>
          <b style={{ color: "var(--text)" }}>Impacto:</b> {s.impacto_estimado || "—"}
        </span>
        {(s.situacao || s.causa_provavel) && (
          <button
            onClick={() => setAberto((v) => !v)}
            style={{ background: "none", border: 0, color: "var(--accent-deep)", fontWeight: 600 }}
          >
            {aberto ? "▲ ocultar" : "▸ situação/causa"}
          </button>
        )}
      </div>
      {aberto && (
        <div className="col" style={{ gap: 6, marginTop: 8, borderTop: "1px solid var(--line-2)", paddingTop: 8, fontSize: 12.5, color: "var(--text)" }}>
          {s.situacao && (
            <p>
              <b>Situação. </b>
              {s.situacao}
            </p>
          )}
          {s.causa_provavel && (
            <p>
              <b>Causa. </b>
              {s.causa_provavel}
            </p>
          )}
          {s.eventos_relacionados?.comportamentos && s.eventos_relacionados.comportamentos.length > 0 && (
            <div className="row gap1 wrap">
              {s.eventos_relacionados.comportamentos.slice(0, 6).map((c) => (
                <code
                  key={c}
                  style={{ fontSize: 10.5, background: "var(--line-2)", color: "var(--muted)", padding: "1px 5px", borderRadius: 4 }}
                >
                  {c}
                </code>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Aprendizado (donut origens)
// ════════════════════════════════════════════════════════════════════════
function AprendizadoPanel({ origens, processoId }: { origens: DashboardData["origens"]; processoId: string }) {
  const total = origens.auto + origens.humano + origens.pendente;
  const dados = [
    { name: "Auto-validados", v: origens.auto, c: "#A78BFA" },
    { name: "Validados por você", v: origens.humano, c: "var(--accent-deep)" },
    { name: "Pendentes", v: origens.pendente, c: "var(--none)" },
  ];
  const autoPct = total > 0 ? Math.round((origens.auto / total) * 100) : 0;
  return (
    <Card style={{ padding: 18 }}>
      <PanelHead
        titulo="Estado do aprendizado"
        ajuda="Como cada evento foi confirmado. O Prism confirma sozinho quando reconhece um label já validado 2× ou mais."
        leitura={total === 0 ? "Sem dados ainda." : `${autoPct}% já validado sozinho.`}
      />
      {total === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Sem dados ainda.</p>
      ) : (
        <div className="row gap3 wrap" style={{ alignItems: "center" }}>
          <div style={{ width: 130, height: 130 }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie data={dados} dataKey="v" innerRadius={36} outerRadius={58} paddingAngle={2}>
                  {dados.map((d, i) => <Cell key={i} fill={d.c} />)}
                </Pie>
                <RTooltip formatter={(v: number, n: string) => [`${v} eventos`, n]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="col gap1" style={{ flex: 1, minWidth: 140, fontSize: 12, listStyle: "none", padding: 0 }}>
            {dados.map((d) => (
              <li key={d.name} className="row gap2">
                <span style={{ width: 9, height: 9, borderRadius: "50%", background: d.c, display: "inline-block" }} />
                <span className="grow" style={{ color: "var(--muted)" }}>{d.name}</span>
                <span className="font-mono tnum" style={{ fontWeight: 700, color: "var(--ink)" }}>{d.v}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {origens.pendente > 0 && (
        <Link
          to={`/processos/${processoId}/validacao`}
          style={{ display: "block", textAlign: "center", marginTop: 10, fontSize: 13, color: "var(--accent-deep)", fontWeight: 600 }}
        >
          Validar pendentes →
        </Link>
      )}
    </Card>
  );
}

function ResumoOportunidades({ sugestoes }: { sugestoes: Sugestao[] }) {
  const cont = { alta: 0, media: 0, info: 0 } as Record<string, number>;
  const areas = new Map<string, number>();
  sugestoes.forEach((s) => {
    const p = (s.prioridade || "").toLowerCase();
    if (p in cont) cont[p]++;
    const a = (s.area || "—").trim();
    areas.set(a, (areas.get(a) || 0) + 1);
  });
  const maxP = Math.max(1, ...Object.values(cont));
  const topAreas = Array.from(areas.entries()).sort((a, b) => b[1] - a[1]).slice(0, 6);

  return (
    <Card style={{ padding: 18 }}>
      <PanelHead
        titulo="Resumo das oportunidades"
        ajuda="A dimensão do todo num relance, sem rolar a lista de sugestões."
      />
      <div className="col gap2">
        {(["alta", "media", "info"] as const).map((p) => (
          <div key={p} className="row gap2" style={{ fontSize: 12 }}>
            <span style={{ width: 48, textTransform: "capitalize", color: "var(--muted)" }}>{p}</span>
            <Track pct={(cont[p] / maxP) * 100} color={p === "alta" ? "var(--desp)" : p === "media" ? "var(--apoio)" : "var(--info)"} />
            <span className="font-mono tnum" style={{ width: 26, textAlign: "right", color: "var(--ink)", fontWeight: 700 }}>
              {cont[p]}
            </span>
          </div>
        ))}
      </div>
      {topAreas.length > 0 && (
        <>
          <div
            style={{
              fontSize: 10.5, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".06em",
              marginTop: 14, marginBottom: 4, fontWeight: 700,
            }}
          >
            Top áreas
          </div>
          <ul className="col" style={{ gap: 0, listStyle: "none", padding: 0, fontSize: 13 }}>
            {topAreas.map(([a, n]) => (
              <li
                key={a}
                className="row"
                style={{ justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid var(--line-2)" }}
              >
                <span className="truncate" style={{ color: "var(--text)" }}>{a}</span>
                <span className="font-mono tnum" style={{ color: "var(--muted)", fontWeight: 600 }}>{n}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function PadroesResumoPanel({ padroes, processoId }: { padroes: DashboardData["padroes_resumo"]; processoId: string }) {
  return (
    <Card style={{ padding: 18 }}>
      <PanelHead
        titulo="Padrões detectados"
        ajuda="Padrões de recorrência e evolução no tempo. Veja a aba Padrões para detalhes."
      />
      <ul className="col gap2" style={{ listStyle: "none", padding: 0 }}>
        {padroes.slice(0, 4).map((p) => (
          <li
            key={p.id}
            style={{
              border: "1px solid var(--line)",
              borderRadius: 10,
              padding: "8px 10px",
              fontSize: 12.5,
              color: "var(--text)",
            }}
          >
            <span style={{ fontWeight: 600, color: "var(--ink)" }}>{p.titulo}</span>
            <div className="row gap1" style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2 }}>
              <span className="badge badge-purple" style={{ fontSize: 10 }}>{p.camada}</span>
              <span>confiança {p.confianca}</span>
            </div>
          </li>
        ))}
      </ul>
      <Link
        to={`/processos/${processoId}/padroes`}
        style={{ display: "block", textAlign: "center", marginTop: 10, fontSize: 13, color: "var(--accent-deep)", fontWeight: 600 }}
      >
        Ver todos os padrões →
      </Link>
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Composição donut
// ════════════════════════════════════════════════════════════════════════
function ComposicaoValorPanel({ cv }: { cv: DashboardData["composicao_valor"] }) {
  const fatias = [
    { name: "Valor agregado", v: cv.valor_agregado_pct, c: "var(--va)" },
    { name: "Apoio", v: cv.apoio_pct, c: "var(--apoio)" },
    { name: "Desperdício", v: cv.desperdicio_pct, c: "var(--desp)" },
    { name: "Não classificado", v: cv.nao_classificado_pct, c: "var(--none)" },
  ].filter((f) => f.v > 0);
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Composição de valor (Lean)"
        ajuda="Como o tempo total se distribui entre atividades que agregam valor, que servem de apoio, ou que são desperdício."
        leitura={cv.valor_agregado_pct > 30 ? "Bom índice de VA para a média industrial." : "Há espaço claro pra mover tempo de Apoio/Desperdício para Valor Agregado."}
      />
      {fatias.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Sem dados ainda.</p>
      ) : (
        <>
          <div style={{ width: "100%", height: 220 }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie data={fatias} dataKey="v" nameKey="name" innerRadius={62} outerRadius={92} paddingAngle={2}>
                  {fatias.map((f, i) => <Cell key={i} fill={f.c} />)}
                </Pie>
                <RTooltip formatter={(v: number, n: string) => [`${v}%`, n]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="row wrap" style={{ gap: 10, listStyle: "none", padding: 0, marginTop: 4, fontSize: 12 }}>
            {fatias.map((f) => (
              <li key={f.name} className="row gap1">
                <span style={{ width: 9, height: 9, borderRadius: 2, background: f.c, display: "inline-block" }} />
                <span style={{ color: "var(--muted)" }}>{f.name}</span>
                <span className="font-mono tnum" style={{ fontWeight: 700, color: "var(--ink)" }}>{f.v}%</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Pareto
// ════════════════════════════════════════════════════════════════════════
function ParetoPanel({ pareto }: { pareto: ParetoItem[] }) {
  const dados = pareto.slice(0, 10).map((p) => ({
    nome: p.comportamento.length > 18 ? p.comportamento.slice(0, 16) + "…" : p.comportamento,
    pct_tempo: p.pct_tempo,
    pct_acumulado: p.pct_acumulado,
    cat: leanShort(p.categoria_lean),
  }));
  const i80 = dados.findIndex((d) => d.pct_acumulado >= 80);
  const leitura =
    i80 >= 0
      ? `80% do tempo está em ${i80 + 1} comportamento${i80 === 0 ? "" : "s"}.`
      : "Tempo bem distribuído entre vários comportamentos.";
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Pareto do tempo"
        ajuda="Comportamentos por tempo, com a curva de % acumulado. Mostra a regra 80/20: poucos comportamentos concentram a maior parte do tempo."
        leitura={leitura}
      />
      {dados.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Sem dados ainda.</p>
      ) : (
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <ComposedChart data={dados} margin={{ left: 0, right: 16, top: 8, bottom: 0 }}>
              <CartesianGrid stroke="var(--line-2)" />
              <XAxis dataKey="nome" fontSize={10} interval={0} angle={-15} dy={6} height={50} />
              <YAxis yAxisId="left" tickFormatter={(v) => `${v}%`} fontSize={11} />
              <YAxis yAxisId="right" orientation="right" tickFormatter={(v) => `${v}%`} fontSize={11} domain={[0, 100]} />
              <RTooltip formatter={(v: number, n: string) => (n === "pct_acumulado" ? [`${v}%`, "Acumulado"] : [`${v}%`, "Tempo"])} />
              <Bar yAxisId="left" dataKey="pct_tempo" radius={[6, 6, 0, 0]}>
                {dados.map((d, i) => <Cell key={i} fill={leanCor(d.cat)} />)}
              </Bar>
              <Line yAxisId="right" dataKey="pct_acumulado" stroke="var(--accent-deep)" strokeWidth={2} dot={{ r: 3, fill: "var(--accent-deep)" }} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Tempo por comportamento (com chip de categoria Lean clicável)
// ════════════════════════════════════════════════════════════════════════
function TempoPorComportamentoPanel({ distribuicao }: { distribuicao: DistribuicaoComportamento[] }) {
  const qc = useQueryClient();
  const [editId, setEditId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const setCat = useMutation({
    mutationFn: ({ id, cat }: { id: string; cat: CategoriaLean | null }) =>
      api.comportamentos.setCategoria(id, cat),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["processos"] });
      qc.invalidateQueries({ queryKey: ["insights-globais"] });
      setEditId(null);
      const extra =
        (resp as { propagados?: number }).propagados
          ? ` Aplicado em mais ${(resp as { propagados: number }).propagados} processo(s).`
          : "";
      setFeedback(`Anotado. O Prism vai usar isso para classificar comportamentos parecidos.${extra}`);
      toast("Categoria atualizada", { icon: "check", color: "#3EE6AE" });
      window.setTimeout(() => setFeedback(null), 4500);
    },
  });
  const dados = distribuicao.slice(0, 10);
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Tempo por comportamento"
        ajuda="Top comportamentos por tempo. As barras refletem a categoria Lean. Clique no chip pra reclassificar — sua decisão vira aprendizado e vale em outros processos. Marcas: ✓ definida por você · ↺ aprendida de você · ~ sugestão da IA."
        leitura="A cor diz se aquele tempo está agregando valor ou não."
      />
      {feedback && (
        <div
          style={{
            fontSize: 12.5, color: "var(--ok)", background: "var(--ok-bg)",
            border: "1px solid rgba(21,168,107,.2)", borderRadius: 10, padding: "8px 11px", marginBottom: 10,
          }}
        >
          {feedback}
        </div>
      )}
      {dados.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Sem dados ainda.</p>
      ) : (
        <ul className="col gap2" style={{ listStyle: "none", padding: 0 }}>
          {dados.map((d) => {
            const short = leanShort(d.categoria_lean);
            const cor = leanCor(short);
            const editando = editId === d.comportamento_id;
            return (
              <li key={d.comportamento} style={{ fontSize: 12 }}>
                <div className="row gap2" style={{ marginBottom: 4 }}>
                  <span className="truncate" style={{ fontWeight: 600, color: "var(--text)" }} title={d.comportamento}>
                    {d.comportamento}
                  </span>
                  <CategoriaChip
                    cat={short}
                    origem={d.categoria_lean_origem || null}
                    editando={editando}
                    onClick={() => setEditId(editando ? null : (d.comportamento_id ?? null))}
                    onSet={(novo) =>
                      d.comportamento_id && setCat.mutate({ id: d.comportamento_id, cat: novo })
                    }
                    pending={setCat.isPending}
                  />
                  <span className="grow" />
                  <span className="font-mono tnum" style={{ color: "var(--muted)" }}>
                    {d.pct_tempo}% · {fmtSeg(d.tempo_total_s)}
                  </span>
                </div>
                <Track pct={d.pct_tempo} color={cor} />
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function CategoriaChip({
  cat,
  origem,
  editando,
  onClick,
  onSet,
  pending,
}: {
  cat: "va" | "apoio" | "desp" | "none";
  origem: "ia" | "humano" | "aprendido" | null;
  editando: boolean;
  onClick: () => void;
  onSet: (c: CategoriaLean | null) => void;
  pending: boolean;
}) {
  const cor = leanCor(cat);
  if (editando) {
    return (
      <div
        className="row gap1"
        style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 8, padding: "2px 4px" }}
      >
        {([
          { c: "valor_agregado", color: "var(--va)" },
          { c: "apoio", color: "var(--apoio)" },
          { c: "desperdicio", color: "var(--desp)" },
        ] as { c: CategoriaLean; color: string }[]).map((b) => (
          <button
            key={b.c}
            disabled={pending}
            onClick={() => onSet(b.c)}
            title={leanLabel(leanShort(b.c))}
            style={{ width: 16, height: 16, borderRadius: 4, background: b.color, border: 0 }}
          />
        ))}
        <button
          disabled={pending}
          onClick={() => onSet(null)}
          title="Limpar"
          style={{ width: 16, height: 16, borderRadius: 4, background: "var(--line-2)", color: "var(--muted)", border: 0, fontSize: 10 }}
        >
          ×
        </button>
        <button onClick={onClick} style={{ background: 0, border: 0, color: "var(--faint)", fontSize: 11 }}>
          ✕
        </button>
      </div>
    );
  }
  const marca =
    origem === "humano" ? "✓" : origem === "aprendido" ? "↺" : origem === "ia" ? "~" : "";
  const marcaCor =
    origem === "humano" ? "var(--accent-deep)" : origem === "aprendido" ? "var(--va)" : "var(--faint)";
  return (
    <button
      onClick={onClick}
      title={
        cat === "none"
          ? "Não classificado — clique para definir"
          : `${leanLabel(cat)} (clique para mudar)`
      }
      className="row gap1"
      style={{
        fontSize: 10.5,
        padding: "2px 7px",
        borderRadius: 999,
        border: "1px solid var(--line)",
        background: "#fff",
        color: "var(--text)",
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: 2, background: cor }} />
      <span>{leanLabel(cat)}</span>
      {marca && <span style={{ color: marcaCor }}>{marca}</span>}
    </button>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Fluxo (transições)
// ════════════════════════════════════════════════════════════════════════
function FluxoPanel({ transicoes }: { transicoes: DashboardData["transicoes"] }) {
  const total = transicoes.reduce((s, t) => s + t.vezes, 0) || 1;
  const max = Math.max(1, ...transicoes.map((t) => t.vezes));
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Fluxo de atividades"
        ajuda="Sequências A→B mais frequentes observadas (por pessoa, no mesmo vídeo)."
        leitura="O fluxo real que o Prism observou no chão de fábrica."
      />
      {transicoes.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>
          Ainda não há sequências suficientes (envie mais vídeos).
        </p>
      ) : (
        <div className="col gap2">
          {transicoes.slice(0, 8).map((t, i) => {
            const pct = ((t.vezes / total) * 100).toFixed(0);
            return (
              <div key={i} className="row gap2" style={{ fontSize: 12 }}>
                <code
                  title={t.de}
                  style={{ background: "var(--line-2)", color: "var(--text)", padding: "2px 7px", borderRadius: 6, flex: 1, minWidth: 0, textAlign: "right" }}
                  className="truncate"
                >
                  {t.de}
                </code>
                <Icon name="arrow-right" size={12} color="var(--faint)" />
                <code
                  title={t.para}
                  style={{ background: "var(--accent-soft)", color: "var(--accent-deep)", padding: "2px 7px", borderRadius: 6, flex: 1, minWidth: 0 }}
                  className="truncate"
                >
                  {t.para}
                </code>
                <div style={{ width: 80 }}>
                  <Track pct={(t.vezes / max) * 100} color="var(--accent)" />
                </div>
                <span className="font-mono tnum" style={{ color: "var(--muted)", width: 36, textAlign: "right" }}>
                  {pct}%
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Vídeos processados (rodapé)
// ════════════════════════════════════════════════════════════════════════
function VideosPanel({ videos }: { videos: DashboardData["videos"] }) {
  if (videos.length === 0) return null;
  return (
    <Card style={{ padding: 20 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h3 className="font-display row gap2" style={{ fontSize: 15, fontWeight: 700 }}>
          Vídeos processados
          <Help text="Cada novo vídeo enriquece a base e melhora as sugestões." />
        </h3>
        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{videos.length} no total</span>
      </div>
      <ul
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))",
          gap: 10,
          listStyle: "none",
          padding: 0,
          fontSize: 12,
        }}
      >
        {videos.slice(0, 12).map((v) => (
          <li
            key={v.id}
            style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "8px 10px" }}
          >
            <div className="truncate" title={v.nome} style={{ fontWeight: 600, color: "var(--ink)", fontSize: 12 }}>
              {v.nome}
            </div>
            <div className="row" style={{ justifyContent: "space-between", marginTop: 4 }}>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                {new Date(v.processado_em).toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
              </span>
              <span className="font-mono tnum" style={{ color: "var(--faint)", fontSize: 11 }}>
                {v.total_eventos} ev · {fmtSeg(v.duracao_s)}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
