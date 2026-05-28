import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
  CartesianGrid,
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
import type { DashboardData, Sugestao } from "../lib/types";

const PALETA = [
  "#7c3aed",
  "#a855f7",
  "#c084fc",
  "#d8b4fe",
  "#e9d5ff",
  "#a5b4fc",
  "#818cf8",
  "#6366f1",
  "#a78bfa",
  "#c4b5fd",
];

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
      <KPIs data={data} />

      {data.perguntas_pendentes > 0 && (
        <Card className="p-4 bg-gradient-to-r from-kv-purple-50 to-kv-purple-100 border-kv-purple-300">
          <div className="flex items-start gap-3 flex-wrap">
            <div className="h-9 w-9 rounded-full bg-kv-purple text-white flex items-center justify-center font-bold flex-shrink-0">
              ?
            </div>
            <div className="flex-1 min-w-[14rem]">
              <p className="text-sm font-medium text-kv-purple-dark">
                A IA tem {data.perguntas_pendentes === 1
                  ? "1 pergunta sobre o seu processo"
                  : `${data.perguntas_pendentes} perguntas sobre o seu processo`}
              </p>
              <p className="text-xs text-slate-600 mt-1">
                Cada resposta vira contexto de domínio e deixa as próximas
                análises mais precisas. Responder é opcional.
              </p>
            </div>
            <Link to={`/processos/${id}/validacao`}>
              <Button variant="secondary">Responder agora</Button>
            </Link>
          </div>
        </Card>
      )}

      {data.eventos_pendentes > 0 && (
        <Card className="p-4 bg-gradient-to-r from-kv-purple-50 to-kv-indigo-bg border-kv-purple-200">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div>
              <p className="text-sm font-medium text-kv-purple-dark">
                {data.eventos_pendentes} eventos aguardando sua validação
              </p>
              <p className="text-xs text-slate-600 mt-1">
                Quanto mais você valida, mais o sistema aprende seu contexto. A
                cada label confirmado 2 vezes, ele passa a confirmar sozinho.
              </p>
            </div>
            <Link to={`/processos/${id}/validacao`}>
              <Button variant="secondary">Validar agora</Button>
            </Link>
          </div>
        </Card>
      )}

      {/* Sugestões + sidebar (estado da operação) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2 p-6">
          <SectionTitle
            titulo="Sugestões de produtividade"
            ajuda="Geradas pela IA combinando seus dados agregados com os 7 desperdícios do Lean. Quanto mais vídeos e validações, mais precisas elas ficam."
          />
          <p className="text-xs text-slate-500 mb-4">
            Estes são os pontos de maior impacto detectados em toda a base do
            processo. As de prioridade <b>ALTA</b> são as que mais consomem
            tempo sem agregar valor.
          </p>
          <div className="space-y-3">
            {data.sugestoes.length === 0 && (
              <p className="text-sm text-slate-500">
                Ainda não há sugestões disponíveis.
              </p>
            )}
            {data.sugestoes.map((s) => (
              <SugestaoCard key={s.id} s={s} />
            ))}
          </div>
        </Card>

        <div className="space-y-6">
          <ValidacaoPanel
            auto={data.origens.auto}
            humano={data.origens.humano}
            pendente={data.origens.pendente}
            processoId={id!}
          />
          <VideosPanel videos={data.videos} />
        </div>
      </div>

      {/* Sua operação em números */}
      <div>
        <h2 className="text-lg font-semibold text-slate-900 mb-1 flex items-center gap-2">
          Sua operação em números
          <Tooltip text="Painéis para você entender como o tempo está sendo gasto na sua operação e como as atividades se sequenciam." />
        </h2>
        <p className="text-sm text-slate-500 mb-4">
          Visão consolidada de <b>todos os vídeos</b> deste processo. As
          sugestões acima nascem dessa base.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="p-6">
          <SectionTitle
            titulo="Onde o tempo é gasto"
            ajuda="% do tempo observado por comportamento. Comportamentos que consomem grande % do tempo são os candidatos óbvios a otimização."
          />
          <p className="text-xs text-slate-500 mb-4">
            Comportamentos ordenados do que mais consome tempo para o que menos
            consome.
          </p>
          <DistribuicaoChart
            data={data.snapshot.distribuicao_comportamentos.slice(0, 10)}
          />
        </Card>

        <Card className="p-6">
          <SectionTitle
            titulo="Mix de atividades"
            ajuda="Como o tempo total observado se divide entre os comportamentos detectados. Útil pra enxergar a composição global da operação."
          />
          <p className="text-xs text-slate-500 mb-4">
            Composição percentual do tempo observado (top 6).
          </p>
          <MixDonut data={data.snapshot.distribuicao_comportamentos.slice(0, 6)} />
        </Card>
      </div>

      <Card className="p-6">
        <SectionTitle
          titulo="Sequências mais frequentes"
          ajuda="Pares 'comportamento A → comportamento B' que aparecem com mais frequência por pessoa, no mesmo vídeo. Bom pra ver o fluxo real da operação vs o fluxo desejado."
        />
        <p className="text-xs text-slate-500 mb-4">
          O fluxo real que a IA observou no seu chão de fábrica.
        </p>
        <TransicoesPanel data={data.transicoes} />
      </Card>
    </div>
  );
}

function KPIs({ data }: { data: DashboardData }) {
  const s = data.snapshot;
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <Metric
        label="Vídeos analisados"
        valor={s.videos_analisados.toString()}
        ajuda="Quantos vídeos diferentes você já processou neste processo."
      />
      <Metric
        label="Tempo observado"
        valor={`${s.tempo_total_observado_min} min`}
        ajuda="Soma da duração de todos os vídeos analisados. Quanto maior, mais robusta a base para as sugestões."
      />
      <Metric
        label="Eventos detectados"
        valor={s.eventos_considerados.toLocaleString("pt-BR")}
        ajuda="Cada evento é um trecho contínuo de uma pessoa fazendo um comportamento. Falsos positivos descartados não contam."
      />
      <Metric
        label="% validado por humano"
        valor={`${s.pct_validado_por_humano}%`}
        ajuda="Quanto da base você (ou alguém da sua equipe) já revisou. Quanto mais alto, mais o sistema confia no que aprendeu."
      />
    </div>
  );
}

function SectionTitle({ titulo, ajuda }: { titulo: string; ajuda: string }) {
  return (
    <h2 className="font-semibold text-slate-900 flex items-center gap-2">
      {titulo}
      <Tooltip text={ajuda} />
    </h2>
  );
}

function Metric({
  label,
  valor,
  ajuda,
}: {
  label: string;
  valor: string;
  ajuda: string;
}) {
  return (
    <Card className="p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500 font-medium flex items-center gap-1.5">
        {label}
        <Tooltip text={ajuda} />
      </div>
      <div className="text-2xl font-semibold text-slate-900 mt-1">{valor}</div>
    </Card>
  );
}

function SugestaoCard({ s }: { s: Sugestao }) {
  const tone = (s.prioridade as "alta" | "media" | "info") || "info";
  return (
    <div className="border border-slate-200 rounded-xl p-4 hover:border-kv-purple-200 transition">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <Badge tone={tone}>{s.prioridade?.toUpperCase()}</Badge>
        <span className="text-sm font-medium text-slate-900">{s.area}</span>
      </div>
      <p className="text-sm text-slate-700 mb-2">
        <span className="font-semibold text-slate-900">Situação. </span>
        {s.situacao}
      </p>
      <p className="text-sm text-slate-600 mb-2">
        <span className="font-semibold text-slate-800">Causa provável. </span>
        {s.causa_provavel}
      </p>
      <p className="text-sm text-slate-800 mb-2">
        <span className="font-semibold text-slate-900">Sugestão. </span>
        {s.sugestao}
      </p>
      <div className="flex items-center justify-between text-xs text-slate-500 mt-3 flex-wrap gap-2">
        <span>
          <b className="text-slate-700">Impacto estimado:</b>{" "}
          {s.impacto_estimado || "—"}
        </span>
        {s.eventos_relacionados?.comportamentos &&
          s.eventos_relacionados.comportamentos.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              {s.eventos_relacionados.comportamentos.slice(0, 4).map((c) => (
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
    </div>
  );
}

function ValidacaoPanel({
  auto,
  humano,
  pendente,
  processoId,
}: {
  auto: number;
  humano: number;
  pendente: number;
  processoId: string;
}) {
  const total = auto + humano + pendente;
  const dados = [
    { name: "Auto-validados", v: auto, color: "#a78bfa" },
    { name: "Validados por você", v: humano, color: "#7c3aed" },
    { name: "Pendentes", v: pendente, color: "#e2e8f0" },
  ];
  return (
    <Card className="p-6">
      <SectionTitle
        titulo="Estado da validação"
        ajuda="Como cada evento foi confirmado. O sistema confirma sozinho quando reconhece um label já validado 2× ou mais por humanos."
      />
      <p className="text-xs text-slate-500 mb-3">
        Quanto o sistema já aprendeu sozinho.
      </p>
      {total === 0 ? (
        <p className="text-sm text-slate-500">Sem dados ainda.</p>
      ) : (
        <>
          <div style={{ width: "100%", height: 140 }}>
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
                    <Cell key={i} fill={d.color} />
                  ))}
                </Pie>
                <RTooltip
                  formatter={(v: number, name: string) => [`${v} eventos`, name]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <ul className="space-y-1.5 text-xs mt-1">
            {dados.map((d) => (
              <li key={d.name} className="flex items-center gap-2">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ background: d.color }}
                />
                <span className="text-slate-600 flex-1">{d.name}</span>
                <span className="font-semibold text-slate-900">{d.v}</span>
                <span className="text-slate-400">
                  ({total > 0 ? Math.round((d.v / total) * 100) : 0}%)
                </span>
              </li>
            ))}
          </ul>
          {pendente > 0 && (
            <Link
              to={`/processos/${processoId}/validacao`}
              className="block text-center mt-3 text-sm text-kv-purple-dark hover:underline"
            >
              Validar agora →
            </Link>
          )}
        </>
      )}
    </Card>
  );
}

function VideosPanel({
  videos,
}: {
  videos: DashboardData["videos"];
}) {
  return (
    <Card className="p-6">
      <SectionTitle
        titulo="Vídeos processados"
        ajuda="Histórico de uploads neste processo. Cada novo vídeo enriquece a base e melhora as sugestões."
      />
      <p className="text-xs text-slate-500 mb-3">Histórico recente.</p>
      {videos.length === 0 ? (
        <p className="text-sm text-slate-500">Nenhum vídeo ainda.</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {videos.slice(0, 6).map((v) => (
            <li
              key={v.id}
              className="flex items-center justify-between gap-2 py-1.5 border-b border-slate-100 last:border-0"
            >
              <div className="min-w-0 flex-1">
                <div
                  className="text-slate-800 truncate"
                  title={v.nome}
                >
                  {v.nome}
                </div>
                <div className="text-xs text-slate-400">
                  {new Date(v.processado_em).toLocaleString("pt-BR", {
                    day: "2-digit",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </div>
              </div>
              <div className="text-right text-xs text-slate-500 flex-shrink-0">
                <div>{v.total_eventos} eventos</div>
                <div>{formatSeg(v.duracao_s)}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DistribuicaoChart({
  data,
}: {
  data: {
    comportamento: string;
    pct_tempo: number;
    tempo_total_s: number;
    ocorrencias: number;
  }[];
}) {
  if (data.length === 0)
    return <p className="text-sm text-slate-500">Sem dados ainda.</p>;
  return (
    <div style={{ width: "100%", height: Math.max(240, data.length * 36) }}>
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 0, right: 16 }}>
          <CartesianGrid stroke="#f1f5f9" />
          <XAxis type="number" tickFormatter={(v) => `${v}%`} fontSize={11} />
          <YAxis
            type="category"
            dataKey="comportamento"
            fontSize={11}
            width={130}
            tickFormatter={(v: string) => (v.length > 20 ? v.slice(0, 18) + "…" : v)}
          />
          <RTooltip
            formatter={(v: number, _name, p: { payload?: { tempo_total_s?: number } }) => [
              `${v}% · ${formatSeg(p?.payload?.tempo_total_s ?? 0)}`,
              "Tempo",
            ]}
          />
          <Bar dataKey="pct_tempo" fill="#7c3aed" radius={[0, 6, 6, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function MixDonut({
  data,
}: {
  data: { comportamento: string; tempo_total_s: number; pct_tempo: number }[];
}) {
  if (data.length === 0)
    return <p className="text-sm text-slate-500">Sem dados ainda.</p>;
  const total = data.reduce((s, d) => s + d.tempo_total_s, 0);
  return (
    <div>
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={data}
              dataKey="tempo_total_s"
              nameKey="comportamento"
              innerRadius={55}
              outerRadius={90}
              paddingAngle={2}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={PALETA[i % PALETA.length]} />
              ))}
            </Pie>
            <RTooltip
              formatter={(v: number, name: string) => [
                `${formatSeg(v)} (${((v / total) * 100).toFixed(1)}%)`,
                name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="mt-2 grid grid-cols-2 gap-y-1 gap-x-3 text-xs">
        {data.map((d, i) => (
          <li key={d.comportamento} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm flex-shrink-0"
              style={{ background: PALETA[i % PALETA.length] }}
            />
            <span className="text-slate-700 truncate" title={d.comportamento}>
              {d.comportamento}
            </span>
            <span className="text-slate-400 ml-auto">
              {d.pct_tempo.toFixed(0)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TransicoesPanel({
  data,
}: {
  data: { de: string; para: string; vezes: number }[];
}) {
  if (data.length === 0)
    return (
      <p className="text-sm text-slate-500">
        Ainda não há sequências suficientes (faça upload de mais vídeos).
      </p>
    );
  const max = Math.max(...data.map((d) => d.vezes));
  return (
    <div className="space-y-2.5">
      {data.map((t, i) => (
        <div key={i} className="flex items-center gap-3">
          <code className="bg-slate-100 text-slate-700 text-xs px-2 py-1 rounded min-w-[160px] truncate text-right">
            {t.de}
          </code>
          <span className="text-slate-400">→</span>
          <code className="bg-kv-purple-50 text-kv-purple-dark text-xs px-2 py-1 rounded min-w-[160px] truncate">
            {t.para}
          </code>
          <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-kv-purple"
              style={{ width: `${(t.vezes / max) * 100}%` }}
            />
          </div>
          <span className="text-xs text-slate-500 font-medium w-12 text-right">
            {t.vezes}×
          </span>
        </div>
      ))}
    </div>
  );
}
