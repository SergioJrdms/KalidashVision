import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
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
import { api, formatSeg } from "../lib/api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Spinner,
  Tooltip,
} from "../components/UI";
import type {
  CategoriaLean,
  DashboardData,
  DistribuicaoComportamento,
  ParetoItem,
  Sugestao,
} from "../lib/types";

// ════════════════════════════════════════════════════════════════════════
// Constantes visuais
// ════════════════════════════════════════════════════════════════════════
const COR_VA = "#10b981"; // emerald
const COR_APOIO = "#f59e0b"; // amber
const COR_DESP = "#ef4444"; // red
const COR_NAO_CLASS = "#cbd5e1"; // slate-300

const COR_CAT: Record<string, string> = {
  valor_agregado: COR_VA,
  apoio: COR_APOIO,
  desperdicio: COR_DESP,
};

const LABEL_CAT: Record<string, string> = {
  valor_agregado: "Valor agregado",
  apoio: "Apoio",
  desperdicio: "Desperdício",
};

function corCategoria(cat: CategoriaLean | null | undefined): string {
  if (!cat) return COR_NAO_CLASS;
  return COR_CAT[cat] || COR_NAO_CLASS;
}

function labelCategoria(cat: CategoriaLean | null | undefined): string {
  if (!cat) return "Não classificado";
  return LABEL_CAT[cat] || cat;
}

// ════════════════════════════════════════════════════════════════════════
// Página
// ════════════════════════════════════════════════════════════════════════
export default function Dashboard() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api.processos.dashboard(id!),
    enabled: !!id,
    refetchOnWindowFocus: false,
  });

  if (isLoading)
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner className="h-8 w-8" />
      </div>
    );

  if (error)
    return (
      <div className="text-red-700 bg-red-50 border border-red-200 rounded-lg p-4">
        {(error as Error).message}
      </div>
    );

  if (!data) return null;

  if (data.snapshot.eventos_considerados === 0) {
    return (
      <Card className="p-2">
        <EmptyState
          title="Nenhum vídeo processado ainda"
          description="Envie seu primeiro vídeo para gerar análises de produtividade. Em poucos minutos você verá comportamentos, distribuição do tempo e sugestões prontas."
          action={
            <Link to={`/processos/${id}/upload`}>
              <Button>Enviar vídeo</Button>
            </Link>
          }
        />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* A · KPIs */}
      <KPIRow data={data} />

      {/* Banners de ação */}
      <BannersContextuais data={data} processoId={id!} />

      {data.padroes_resumo && data.padroes_resumo.length > 0 && (
        <Card className="p-4 bg-gradient-to-r from-kv-purple-50 to-white border-kv-purple-200">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-[14rem] flex-1">
              <p className="text-sm font-medium text-kv-purple-dark mb-1">
                O Prism identificou padrões na sua operação
              </p>
              <div className="flex flex-wrap gap-1.5">
                {data.padroes_resumo.slice(0, 4).map((p) => (
                  <span
                    key={p.id}
                    className="text-xs bg-white border border-kv-purple-200 text-slate-700 rounded-full px-2.5 py-0.5"
                    title={`${p.camada} · confiança ${p.confianca}`}
                  >
                    {p.titulo}
                  </span>
                ))}
              </div>
            </div>
            <Link to={`/processos/${id}/padroes`}>
              <Button variant="secondary">Ver padrões</Button>
            </Link>
          </div>
        </Card>
      )}

      {/* B · Sugestões + Resumo das oportunidades */}
      <SugestoesBloco sugestoes={data.sugestoes} origens={data.origens} />

      {/* C · Várias óticas */}
      <div>
        <h2 className="text-lg font-semibold text-slate-900 mb-1 flex items-center gap-2">
          Sua operação em várias óticas
          <Tooltip text="Painéis para você entender como o tempo está sendo gasto e como as atividades se sequenciam. Tudo derivado dos vídeos processados deste processo." />
        </h2>
        <p className="text-sm text-slate-500 mb-4">
          Visão consolidada de <b>todos os vídeos</b> deste processo. As
          sugestões acima nascem dessa base.
        </p>
        <GraficosGrid data={data} />
      </div>

      {/* D · Rodapé */}
      <VideosRodape videos={data.videos} tempoTotalMin={data.snapshot.tempo_total_observado_min} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// BLOCO A · KPIs
// ════════════════════════════════════════════════════════════════════════
function KPIRow({ data }: { data: DashboardData }) {
  const s = data.snapshot;
  const cv = data.composicao_valor;
  const topComp = s.distribuicao_comportamentos[0];
  const sugAlta = data.sugestoes.filter((x) => (x.prioridade || "").toLowerCase() === "alta").length;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
      <KPIValorAgregado pct={cv.valor_agregado_pct} composicao={cv} />
      <KPICard
        label="Tempo observado"
        valor={`${s.tempo_total_observado_min} min`}
        sub={`${s.videos_analisados} vídeo(s)`}
        ajuda="Soma da duração dos vídeos analisados. Quanto maior, mais robusta a base para as sugestões."
      />
      <KPICard
        label="Onde o tempo se concentra"
        valor={topComp ? `${topComp.pct_tempo}%` : "—"}
        sub={topComp ? topComp.comportamento : "sem dados"}
        ajuda="Comportamento que mais consome tempo no processo. Costuma ser o melhor candidato a otimização."
      />
      <KPICard
        label="Oportunidades de alta prioridade"
        valor={sugAlta.toString()}
        sub={sugAlta === 0 ? "nenhuma — boa!" : "sugestões"}
        ajuda="Número de sugestões marcadas como ALTA prioridade pela IA. Resolva por aqui primeiro."
        destaque={sugAlta > 0}
      />
      <KPICard
        label="Confiança nos dados"
        valor={`${s.pct_validado_por_humano}%`}
        sub="validado por humano"
        ajuda="Quanto da base já foi confirmado por uma pessoa. Quanto mais alto, mais o sistema confia no que aprendeu."
      />
    </div>
  );
}

function KPIValorAgregado({
  pct,
  composicao,
}: {
  pct: number;
  composicao: DashboardData["composicao_valor"];
}) {
  const partes = [
    { v: composicao.valor_agregado_pct, c: COR_VA, n: "VA" },
    { v: composicao.apoio_pct, c: COR_APOIO, n: "Apoio" },
    { v: composicao.desperdicio_pct, c: COR_DESP, n: "Desp" },
    { v: composicao.nao_classificado_pct, c: COR_NAO_CLASS, n: "?" },
  ];
  return (
    <Card className="p-4 col-span-2 lg:col-span-1 border-l-4 border-l-kv-purple">
      <div className="text-xs uppercase tracking-wide text-slate-500 font-medium flex items-center gap-1.5">
        Índice de Valor Agregado
        <Tooltip text="% do tempo observado em comportamentos classificados como 'valor agregado' (Lean). É a métrica-rainha da análise de valor: comunica quanto da operação realmente entrega o que o cliente paga." />
      </div>
      <div className="text-3xl font-semibold text-slate-900 mt-1">
        {pct}%
      </div>
      <div className="flex h-2 mt-3 rounded-full overflow-hidden bg-slate-100">
        {partes.map((p, i) =>
          p.v > 0 ? (
            <div key={i} style={{ width: `${p.v}%`, background: p.c }} title={`${p.n}: ${p.v}%`} />
          ) : null
        )}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5 text-[10px] text-slate-500">
        <span><span className="inline-block h-2 w-2 rounded-sm align-middle mr-1" style={{ background: COR_VA }} /> VA</span>
        <span><span className="inline-block h-2 w-2 rounded-sm align-middle mr-1" style={{ background: COR_APOIO }} /> Apoio</span>
        <span><span className="inline-block h-2 w-2 rounded-sm align-middle mr-1" style={{ background: COR_DESP }} /> Desp</span>
        {composicao.nao_classificado_pct > 0 && (
          <span><span className="inline-block h-2 w-2 rounded-sm align-middle mr-1" style={{ background: COR_NAO_CLASS }} /> ?</span>
        )}
      </div>
    </Card>
  );
}

function KPICard({
  label,
  valor,
  sub,
  ajuda,
  destaque,
}: {
  label: string;
  valor: string;
  sub?: string;
  ajuda: string;
  destaque?: boolean;
}) {
  return (
    <Card className={`p-4 ${destaque ? "border-l-4 border-l-amber-400" : ""}`}>
      <div className="text-xs uppercase tracking-wide text-slate-500 font-medium flex items-center gap-1.5">
        {label}
        <Tooltip text={ajuda} />
      </div>
      <div className="text-2xl font-semibold text-slate-900 mt-1 truncate">{valor}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5 truncate">{sub}</div>}
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Banners
// ════════════════════════════════════════════════════════════════════════
function BannersContextuais({
  data,
  processoId,
}: {
  data: DashboardData;
  processoId: string;
}) {
  if (data.perguntas_pendentes === 0 && data.eventos_pendentes === 0) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {data.perguntas_pendentes > 0 && (
        <Card className="p-4 bg-gradient-to-r from-kv-purple-50 to-kv-purple-100 border-kv-purple-300">
          <div className="flex items-start gap-3 flex-wrap">
            <div className="h-9 w-9 rounded-full bg-kv-purple text-white flex items-center justify-center font-bold flex-shrink-0">
              ?
            </div>
            <div className="flex-1 min-w-[10rem]">
              <p className="text-sm font-medium text-kv-purple-dark">
                A IA tem {data.perguntas_pendentes === 1 ? "1 pergunta" : `${data.perguntas_pendentes} perguntas`} sobre o seu processo
              </p>
              <p className="text-xs text-slate-600 mt-1">
                Cada resposta vira contexto de domínio nos próximos prompts.
              </p>
            </div>
            <Link to={`/processos/${processoId}/validacao`}>
              <Button variant="secondary">Responder</Button>
            </Link>
          </div>
        </Card>
      )}
      {data.eventos_pendentes > 0 && (
        <Card className="p-4 bg-gradient-to-r from-kv-purple-50 to-kv-indigo-bg border-kv-purple-200">
          <div className="flex items-start gap-3 flex-wrap">
            <div className="h-9 w-9 rounded-full bg-kv-purple-200 text-kv-purple-dark flex items-center justify-center font-bold flex-shrink-0">
              ✓
            </div>
            <div className="flex-1 min-w-[10rem]">
              <p className="text-sm font-medium text-kv-purple-dark">
                {data.eventos_pendentes} eventos aguardando sua validação
              </p>
              <p className="text-xs text-slate-600 mt-1">
                A cada label confirmado 2× o sistema passa a confirmar sozinho.
              </p>
            </div>
            <Link to={`/processos/${processoId}/validacao`}>
              <Button variant="secondary">Validar</Button>
            </Link>
          </div>
        </Card>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// BLOCO B · Sugestões + Resumo
// ════════════════════════════════════════════════════════════════════════
type PrioFiltro = "todas" | "alta" | "media" | "info";

function SugestoesBloco({
  sugestoes,
  origens,
}: {
  sugestoes: Sugestao[];
  origens: DashboardData["origens"];
}) {
  const [prio, setPrio] = useState<PrioFiltro>("todas");
  const [areaFiltro, setAreaFiltro] = useState<string | null>(null);
  const [mostrarOutras, setMostrarOutras] = useState(false);

  const contagemPrio = useMemo(() => {
    const c = { alta: 0, media: 0, info: 0 };
    for (const s of sugestoes) {
      const p = (s.prioridade || "").toLowerCase() as keyof typeof c;
      if (p in c) c[p] += 1;
    }
    return c;
  }, [sugestoes]);

  const contagemArea = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of sugestoes) {
      const a = (s.area || "—").trim();
      m.set(a, (m.get(a) || 0) + 1);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [sugestoes]);

  const filtradas = useMemo(() => {
    return sugestoes.filter((s) => {
      const p = (s.prioridade || "").toLowerCase();
      if (prio !== "todas" && p !== prio) return false;
      if (areaFiltro && (s.area || "—").trim() !== areaFiltro) return false;
      return true;
    });
  }, [sugestoes, prio, areaFiltro]);

  const altasPrimeiro = useMemo(() => {
    const ordem: Record<string, number> = { alta: 0, media: 1, info: 2 };
    return [...filtradas].sort(
      (a, b) =>
        (ordem[(a.prioridade || "").toLowerCase()] ?? 3) -
        (ordem[(b.prioridade || "").toLowerCase()] ?? 3)
    );
  }, [filtradas]);

  const podem_colapsar = altasPrimeiro.length > 5;
  const visiveis = mostrarOutras ? altasPrimeiro : altasPrimeiro.slice(0, 5);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <Card className="lg:col-span-2 p-6">
        <div className="flex items-baseline justify-between gap-3 flex-wrap mb-2">
          <h2 className="font-semibold text-slate-900 flex items-center gap-2">
            Sugestões de produtividade
            <Tooltip text="Geradas pela IA combinando seus dados agregados com os 7 desperdícios do Lean. Quanto mais vídeos e validações, mais precisas elas ficam." />
          </h2>
          <span className="text-xs text-slate-500">
            {altasPrimeiro.length} de {sugestoes.length}
          </span>
        </div>
        <p className="text-xs text-slate-500 mb-3">
          Estes são os pontos de maior impacto detectados em toda a base. As de{" "}
          <b>ALTA</b> são as que mais consomem tempo sem agregar valor.
        </p>

        {/* Filtros */}
        <div className="flex items-center gap-1.5 flex-wrap mb-2 text-xs">
          {(["todas", "alta", "media", "info"] as PrioFiltro[]).map((p) => (
            <button
              key={p}
              onClick={() => setPrio(p)}
              className={`px-2.5 py-1 rounded-full border transition ${
                prio === p
                  ? "bg-kv-purple text-white border-kv-purple"
                  : "bg-white text-slate-600 border-slate-200 hover:border-kv-purple-200"
              }`}
            >
              {p === "todas"
                ? `Todas · ${sugestoes.length}`
                : `${p[0].toUpperCase()}${p.slice(1)} · ${contagemPrio[p as keyof typeof contagemPrio]}`}
            </button>
          ))}
        </div>
        {contagemArea.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap mb-4 text-xs">
            <span className="text-slate-400 mr-1">Áreas:</span>
            <button
              onClick={() => setAreaFiltro(null)}
              className={`px-2 py-0.5 rounded-full border ${
                !areaFiltro
                  ? "bg-slate-700 text-white border-slate-700"
                  : "bg-white text-slate-500 border-slate-200 hover:border-slate-400"
              }`}
            >
              Todas
            </button>
            {contagemArea.slice(0, 5).map(([a, n]) => (
              <button
                key={a}
                onClick={() => setAreaFiltro(a === areaFiltro ? null : a)}
                className={`px-2 py-0.5 rounded-full border ${
                  areaFiltro === a
                    ? "bg-slate-700 text-white border-slate-700"
                    : "bg-white text-slate-500 border-slate-200 hover:border-slate-400"
                }`}
              >
                {a} · {n}
              </button>
            ))}
          </div>
        )}

        {visiveis.length === 0 && (
          <p className="text-sm text-slate-500 py-6 text-center">
            Nenhuma sugestão para os filtros selecionados.
          </p>
        )}

        <div className="space-y-2.5">
          {visiveis.map((s) => (
            <SugestaoCard key={s.id} s={s} />
          ))}
        </div>

        {podem_colapsar && (
          <button
            onClick={() => setMostrarOutras((v) => !v)}
            className="mt-3 w-full text-sm text-kv-purple-dark hover:underline"
          >
            {mostrarOutras
              ? "▲ Mostrar só as 5 primeiras"
              : `▼ Ver outras ${altasPrimeiro.length - 5} sugestões`}
          </button>
        )}
      </Card>

      {/* Coluna direita: Estado do aprendizado em cima, Resumo embaixo */}
      <div className="space-y-6">
      <AprendizadoPanel origens={origens} />
      <Card className="p-6">
        <h2 className="font-semibold text-slate-900 flex items-center gap-2">
          Resumo das oportunidades
          <Tooltip text="A dimensão do todo num relance, sem precisar rolar a lista de sugestões." />
        </h2>
        <p className="text-xs text-slate-500 mb-4">Por prioridade e por área.</p>

        <div className="space-y-2">
          {(["alta", "media", "info"] as const).map((p) => {
            const n = contagemPrio[p];
            const max = Math.max(1, ...Object.values(contagemPrio));
            const cor =
              p === "alta" ? "bg-red-400" : p === "media" ? "bg-amber-400" : "bg-sky-400";
            return (
              <div key={p} className="flex items-center gap-2 text-xs">
                <span className="w-12 capitalize text-slate-600">{p}</span>
                <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div className={`h-full ${cor}`} style={{ width: `${(n / max) * 100}%` }} />
                </div>
                <span className="font-semibold text-slate-800 w-6 text-right">{n}</span>
              </div>
            );
          })}
        </div>

        {contagemArea.length > 0 && (
          <>
            <div className="text-xs uppercase tracking-wide text-slate-400 mt-5 mb-2 font-medium">
              Top áreas
            </div>
            <ul className="space-y-1.5 text-sm">
              {contagemArea.slice(0, 6).map(([a, n]) => (
                <li
                  key={a}
                  className="flex items-center justify-between py-1 border-b border-slate-100 last:border-0"
                >
                  <span className="text-slate-700 truncate" title={a}>
                    {a}
                  </span>
                  <span className="text-xs text-slate-500 font-medium">{n}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
      </div>
    </div>
  );
}

function SugestaoCard({ s }: { s: Sugestao }) {
  const [aberto, setAberto] = useState(false);
  const tone = (s.prioridade as "alta" | "media" | "info") || "info";
  return (
    <div className="border border-slate-200 rounded-xl p-3.5 hover:border-kv-purple-200 transition">
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        <Badge tone={tone}>{s.prioridade?.toUpperCase()}</Badge>
        <span className="text-xs font-semibold text-slate-700">{s.area}</span>
      </div>
      <p className="text-sm text-slate-800 leading-snug">{s.sugestao}</p>
      <div className="flex items-center justify-between text-xs text-slate-500 mt-2 flex-wrap gap-2">
        <span>
          <b className="text-slate-700">Impacto:</b> {s.impacto_estimado || "—"}
        </span>
        <button
          onClick={() => setAberto((v) => !v)}
          className="text-kv-purple-dark hover:underline"
        >
          {aberto ? "▲ ocultar detalhes" : "▸ ver situação/causa"}
        </button>
      </div>
      {aberto && (
        <div className="mt-2 pt-2 border-t border-slate-100 space-y-1.5 text-xs text-slate-600">
          <p>
            <b className="text-slate-700">Situação. </b>
            {s.situacao}
          </p>
          <p>
            <b className="text-slate-700">Causa provável. </b>
            {s.causa_provavel}
          </p>
          {s.eventos_relacionados?.comportamentos && s.eventos_relacionados.comportamentos.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-1">
              {s.eventos_relacionados.comportamentos.slice(0, 6).map((c) => (
                <code
                  key={c}
                  className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded"
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
// BLOCO C · Várias óticas
// ════════════════════════════════════════════════════════════════════════
function GraficosGrid({ data }: { data: DashboardData }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <ComposicaoValorPanel cv={data.composicao_valor} />
      <ParetoPanel pareto={data.pareto} />
      <TempoPorComportamentoPanel
        distribuicao={data.snapshot.distribuicao_comportamentos}
      />
      <FluxoPanel transicoes={data.transicoes} />
    </div>
  );
}

function PanelHeader({ titulo, ajuda, leitura }: { titulo: string; ajuda: string; leitura?: string }) {
  return (
    <>
      <h3 className="font-semibold text-slate-900 flex items-center gap-2">
        {titulo}
        <Tooltip text={ajuda} />
      </h3>
      {leitura && <p className="text-xs text-slate-500 mb-3">{leitura}</p>}
    </>
  );
}

// ─── Composição de valor (donut) ─────────────────────────────────────────
function ComposicaoValorPanel({ cv }: { cv: DashboardData["composicao_valor"] }) {
  const fatias = [
    { name: "Valor agregado", v: cv.valor_agregado_pct, c: COR_VA },
    { name: "Apoio", v: cv.apoio_pct, c: COR_APOIO },
    { name: "Desperdício", v: cv.desperdicio_pct, c: COR_DESP },
    { name: "Não classificado", v: cv.nao_classificado_pct, c: COR_NAO_CLASS },
  ].filter((f) => f.v > 0);

  return (
    <Card className="p-6">
      <PanelHeader
        titulo="Composição de valor (Lean)"
        ajuda="Como o tempo total se distribui entre atividades que agregam valor, que servem de apoio, ou que são desperdício. A categoria de cada comportamento vem da IA (você pode reclassificar no painel ao lado)."
        leitura={
          cv.valor_agregado_pct > 30
            ? "Bom índice de valor agregado para a média industrial."
            : "Há espaço claro pra mover tempo de Apoio/Desperdício para Valor Agregado."
        }
      />
      {fatias.length === 0 ? (
        <p className="text-sm text-slate-500">Sem dados ainda.</p>
      ) : (
        <>
          <div style={{ width: "100%", height: 220 }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie
                  data={fatias}
                  dataKey="v"
                  nameKey="name"
                  innerRadius={60}
                  outerRadius={92}
                  paddingAngle={2}
                >
                  {fatias.map((f, i) => (
                    <Cell key={i} fill={f.c} />
                  ))}
                </Pie>
                <RTooltip formatter={(v: number, n: string) => [`${v}%`, n]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="grid grid-cols-2 gap-y-1 mt-2 text-xs">
            {fatias.map((f) => (
              <li key={f.name} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-sm"
                  style={{ background: f.c }}
                />
                <span className="text-slate-600 truncate">{f.name}</span>
                <span className="ml-auto font-semibold text-slate-800">{f.v}%</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

// ─── Pareto do tempo ─────────────────────────────────────────────────────
function ParetoPanel({ pareto }: { pareto: ParetoItem[] }) {
  const dados = pareto.slice(0, 10).map((p) => ({
    nome: p.comportamento.length > 18 ? p.comportamento.slice(0, 16) + "…" : p.comportamento,
    pct_tempo: p.pct_tempo,
    pct_acumulado: p.pct_acumulado,
    categoria_lean: p.categoria_lean ?? null,
  }));
  const indexA80 = dados.findIndex((d) => d.pct_acumulado >= 80);
  const leitura =
    indexA80 >= 0
      ? `80% do tempo está em ${indexA80 + 1} comportamento${indexA80 === 0 ? "" : "s"} (regra 80/20).`
      : "Tempo bem distribuído entre vários comportamentos.";

  return (
    <Card className="p-6">
      <PanelHeader
        titulo="Pareto do tempo"
        ajuda="Comportamentos ordenados por tempo, com a curva de % acumulado. Mostra a regra 80/20: poucos comportamentos costumam concentrar a maior parte do tempo."
        leitura={leitura}
      />
      {dados.length === 0 ? (
        <p className="text-sm text-slate-500">Sem dados ainda.</p>
      ) : (
        <div style={{ width: "100%", height: 260 }}>
          <ResponsiveContainer>
            <ComposedChart data={dados} margin={{ left: 0, right: 16, top: 8, bottom: 0 }}>
              <CartesianGrid stroke="#f1f5f9" />
              <XAxis dataKey="nome" fontSize={10} interval={0} angle={-15} dy={6} height={50} />
              <YAxis
                yAxisId="left"
                tickFormatter={(v) => `${v}%`}
                fontSize={11}
                label={{ value: "% tempo", angle: -90, position: "insideLeft", style: { fontSize: 10, fill: "#94a3b8" } }}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tickFormatter={(v) => `${v}%`}
                fontSize={11}
                domain={[0, 100]}
              />
              <RTooltip
                formatter={(v: number, name: string) =>
                  name === "pct_acumulado" ? [`${v}%`, "Acumulado"] : [`${v}%`, "Tempo"]
                }
              />
              <Bar yAxisId="left" dataKey="pct_tempo" radius={[6, 6, 0, 0]}>
                {dados.map((d, i) => (
                  <Cell key={i} fill={corCategoria(d.categoria_lean as CategoriaLean | null)} />
                ))}
              </Bar>
              <Line
                yAxisId="right"
                dataKey="pct_acumulado"
                stroke="#7c3aed"
                strokeWidth={2}
                dot={{ r: 3, fill: "#7c3aed" }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
}

// ─── Tempo por comportamento (barra h, color = cat Lean) ─────────────────
function TempoPorComportamentoPanel({
  distribuicao,
}: {
  distribuicao: DistribuicaoComportamento[];
}) {
  const qc = useQueryClient();
  const [editId, setEditId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const setCat = useMutation({
    mutationFn: ({ id, cat }: { id: string; cat: CategoriaLean | null }) =>
      api.comportamentos.setCategoria(id, cat),
    onSuccess: (resp) => {
      // Garante refresh dos KPIs, Pareto e composição em todas as telas.
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["processos"] });
      qc.invalidateQueries({ queryKey: ["insights-globais"] });
      setEditId(null);
      const extra =
        resp && (resp as { propagados?: number }).propagados
          ? ` Aplicado também em ${(resp as { propagados: number }).propagados} comportamento(s) com o mesmo nome em outros processos.`
          : "";
      setFeedback(`Anotado. O Prism vai usar isso para classificar comportamentos parecidos.${extra}`);
      window.setTimeout(() => setFeedback(null), 4500);
    },
  });

  const dados = distribuicao.slice(0, 10);

  return (
    <Card className="p-6">
      <PanelHeader
        titulo="Tempo por comportamento"
        ajuda="Os comportamentos que mais consomem tempo. Cores: verde = valor agregado, âmbar = apoio, vermelho = desperdício, cinza = não classificado. Clique no chip para reclassificar — sua decisão vale para comportamentos com o mesmo nome em outros processos. Marca ✓ = definida por você · ↺ = aprendida de você · ~ = sugestão da IA."
        leitura="A cor diz se aquele tempo está agregando valor ou não."
      />
      {feedback && (
        <div className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2 mb-3">
          {feedback}
        </div>
      )}
      {dados.length === 0 ? (
        <p className="text-sm text-slate-500">Sem dados ainda.</p>
      ) : (
        <ul className="space-y-2.5">
          {dados.map((d) => {
            const cor = corCategoria(d.categoria_lean);
            const editando = editId === d.comportamento_id;
            return (
              <li key={d.comportamento} className="text-xs">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-slate-700 font-medium truncate" title={d.comportamento}>
                    {d.comportamento}
                  </span>
                  <CategoriaChip
                    cat={d.categoria_lean}
                    origem={d.categoria_lean_origem || null}
                    editando={editando}
                    onClick={() =>
                      setEditId(editando ? null : d.comportamento_id ?? null)
                    }
                    onSet={(novoCat) =>
                      d.comportamento_id &&
                      setCat.mutate({ id: d.comportamento_id, cat: novoCat })
                    }
                    pending={setCat.isPending}
                  />
                  <span className="ml-auto text-slate-500 font-semibold">
                    {d.pct_tempo}% · {formatSeg(d.tempo_total_s)}
                  </span>
                </div>
                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${Math.max(2, d.pct_tempo)}%`, background: cor }}
                  />
                </div>
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
  cat: CategoriaLean | null | undefined;
  origem: "ia" | "humano" | "aprendido" | null;
  editando: boolean;
  onClick: () => void;
  onSet: (c: CategoriaLean | null) => void;
  pending: boolean;
}) {
  const cor = corCategoria(cat);
  if (editando) {
    return (
      <div className="flex items-center gap-1 bg-white border border-slate-300 rounded-md px-1 py-0.5 shadow-sm">
        {(["valor_agregado", "apoio", "desperdicio"] as CategoriaLean[]).map((c) => (
          <button
            key={c}
            onClick={() => onSet(c)}
            disabled={pending}
            title={LABEL_CAT[c]}
            className={`h-4 w-4 rounded ${cat === c ? "ring-2 ring-offset-1 ring-kv-purple" : ""}`}
            style={{ background: COR_CAT[c] }}
          />
        ))}
        <button
          onClick={() => onSet(null)}
          disabled={pending}
          title="Limpar (volta a ser candidato à IA)"
          className="h-4 w-4 rounded text-[10px] flex items-center justify-center bg-slate-100 text-slate-500 hover:bg-slate-200"
        >
          ×
        </button>
        <button
          onClick={onClick}
          className="h-4 px-1 text-[10px] text-slate-400 hover:text-slate-600"
          title="fechar"
        >
          ✕
        </button>
      </div>
    );
  }
  const origemTexto =
    origem === "humano"
      ? "definida por você"
      : origem === "aprendido"
        ? "aprendida de uma decisão sua anterior — mesmo nome em outro processo"
        : origem === "ia"
          ? "sugerida pela IA (sem precedente seu ainda)"
          : "ainda não classificada";
  const origemMarca =
    origem === "humano" ? "✓" : origem === "aprendido" ? "↺" : origem === "ia" ? "~" : "";
  const origemClasse =
    origem === "humano"
      ? "text-kv-purple-dark"
      : origem === "aprendido"
        ? "text-emerald-600"
        : origem === "ia"
          ? "text-slate-400"
          : "";

  return (
    <button
      onClick={onClick}
      title={cat ? `${labelCategoria(cat)} · ${origemTexto} (clique para mudar)` : "Não classificado — clique para definir"}
      className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-slate-200 hover:border-slate-400 transition"
    >
      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: cor }} />
      <span className="text-slate-600">{labelCategoria(cat)}</span>
      {origemMarca && (
        <span className={origemClasse} aria-label={origemTexto}>
          {origemMarca}
        </span>
      )}
    </button>
  );
}

// ─── Fluxo de atividades ─────────────────────────────────────────────────
function FluxoPanel({ transicoes }: { transicoes: DashboardData["transicoes"] }) {
  const total = transicoes.reduce((s, t) => s + t.vezes, 0) || 1;
  const max = Math.max(1, ...transicoes.map((t) => t.vezes));
  return (
    <Card className="p-6">
      <PanelHeader
        titulo="Fluxo de atividades"
        ajuda="Sequências A→B mais frequentes observadas (por pessoa, no mesmo vídeo). Permite ver o fluxo real e compará-lo com o esperado."
        leitura="O fluxo real que a IA observou no seu chão de fábrica."
      />
      {transicoes.length === 0 ? (
        <p className="text-sm text-slate-500">
          Ainda não há sequências suficientes (faça upload de mais vídeos).
        </p>
      ) : (
        <div className="space-y-2.5">
          {transicoes.slice(0, 8).map((t, i) => {
            const pct = ((t.vezes / total) * 100).toFixed(0);
            return (
              <div key={i} className="flex items-center gap-2 text-xs">
                <code
                  className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded truncate min-w-0 flex-1 text-right"
                  title={t.de}
                >
                  {t.de}
                </code>
                <span className="text-slate-400">→</span>
                <code
                  className="bg-kv-purple-50 text-kv-purple-dark px-2 py-0.5 rounded truncate min-w-0 flex-1"
                  title={t.para}
                >
                  {t.para}
                </code>
                <div className="w-24 h-1.5 bg-slate-100 rounded-full overflow-hidden hidden sm:block">
                  <div
                    className="h-full bg-kv-purple"
                    style={{ width: `${(t.vezes / max) * 100}%` }}
                  />
                </div>
                <span className="text-slate-500 font-medium w-12 text-right">
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

// ─── Estado do aprendizado ───────────────────────────────────────────────
function AprendizadoPanel({ origens }: { origens: DashboardData["origens"] }) {
  const total = origens.auto + origens.humano + origens.pendente;
  const dados = [
    { name: "Auto-validados", v: origens.auto, c: "#a78bfa" },
    { name: "Validados por você", v: origens.humano, c: "#7c3aed" },
    { name: "Pendentes", v: origens.pendente, c: "#e2e8f0" },
  ];
  const auto_pct = total > 0 ? Math.round((origens.auto / total) * 100) : 0;
  return (
    <Card className="p-6">
      <PanelHeader
        titulo="Estado do aprendizado"
        ajuda="Como cada evento foi confirmado. O sistema confirma sozinho quando reconhece um label já validado 2× ou mais por humanos."
        leitura={
          total === 0
            ? "Ainda sem dados."
            : `O sistema já está confirmando ${auto_pct}% dos eventos sozinho.`
        }
      />
      {total === 0 ? (
        <p className="text-sm text-slate-500">Sem dados ainda.</p>
      ) : (
        <div className="flex items-center gap-4 flex-wrap">
          <div style={{ width: 180, height: 160 }}>
            <ResponsiveContainer>
              <PieChart>
                <Pie
                  data={dados}
                  dataKey="v"
                  innerRadius={40}
                  outerRadius={60}
                  paddingAngle={2}
                >
                  {dados.map((d, i) => (
                    <Cell key={i} fill={d.c} />
                  ))}
                </Pie>
                <RTooltip formatter={(v: number, n: string) => [`${v} eventos`, n]} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="space-y-1.5 text-xs flex-1 min-w-[180px]">
            {dados.map((d) => (
              <li key={d.name} className="flex items-center gap-2">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ background: d.c }}
                />
                <span className="text-slate-600 flex-1">{d.name}</span>
                <span className="font-semibold text-slate-900">{d.v}</span>
                <span className="text-slate-400">
                  ({total > 0 ? Math.round((d.v / total) * 100) : 0}%)
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// BLOCO D · Rodapé
// ════════════════════════════════════════════════════════════════════════
function VideosRodape({
  videos,
  tempoTotalMin,
}: {
  videos: DashboardData["videos"];
  tempoTotalMin: number;
}) {
  if (videos.length === 0) return null;
  return (
    <Card className="p-6">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <h3 className="font-semibold text-slate-900 flex items-center gap-2">
          Vídeos processados
          <Tooltip text="Cada novo vídeo enriquece a base e melhora as sugestões." />
        </h3>
        <span className="text-xs text-slate-500">
          {videos.length} no total · {tempoTotalMin} min observados
        </span>
      </div>
      <ul className="mt-3 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1 text-sm">
        {videos.slice(0, 12).map((v) => (
          <li
            key={v.id}
            className="flex items-center justify-between gap-2 py-1.5 border-b border-slate-100 last:border-0"
          >
            <div className="min-w-0 flex-1">
              <div className="text-slate-800 truncate text-xs" title={v.nome}>
                {v.nome}
              </div>
              <div className="text-[10px] text-slate-400">
                {new Date(v.processado_em).toLocaleString("pt-BR", {
                  day: "2-digit",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>
            </div>
            <div className="text-right text-[10px] text-slate-500 flex-shrink-0">
              <div>{v.total_eventos} eventos</div>
              <div>{formatSeg(v.duracao_s)}</div>
            </div>
          </li>
        ))}
      </ul>
      {videos.length > 12 && (
        <p className="text-xs text-slate-400 mt-2 text-center">
          + {videos.length - 12} vídeo(s) mais antigo(s)
        </p>
      )}
    </Card>
  );
}
