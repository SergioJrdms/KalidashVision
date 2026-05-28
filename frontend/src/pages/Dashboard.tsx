import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, formatSeg } from "../lib/api";
import { Badge, Button, Card, EmptyState, Spinner } from "../components/UI";
import { ProcessoTabs } from "../components/Layout";

export default function Dashboard() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api.processos.dashboard(id!),
    enabled: !!id,
    refetchOnWindowFocus: false,
  });

  return (
    <div>
      <ProcessoTabs />

      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Spinner className="h-8 w-8" />
        </div>
      )}

      {error && (
        <div className="text-red-700 bg-red-50 border border-red-200 rounded-lg p-4">
          {(error as Error).message}
        </div>
      )}

      {data && data.snapshot.eventos_considerados === 0 && (
        <Card className="p-2">
          <EmptyState
            title="Nenhum vídeo processado ainda"
            description="Envie seu primeiro vídeo para gerar análises de produtividade."
            action={
              <Link to={`/processos/${id}/upload`}>
                <Button>Enviar vídeo</Button>
              </Link>
            }
          />
        </Card>
      )}

      {data && data.snapshot.eventos_considerados > 0 && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <Metric label="Vídeos analisados" valor={data.snapshot.videos_analisados.toString()} />
            <Metric
              label="Tempo observado"
              valor={`${data.snapshot.tempo_total_observado_min} min`}
            />
            <Metric
              label="Eventos detectados"
              valor={data.snapshot.eventos_considerados.toLocaleString("pt-BR")}
            />
            <Metric
              label="% validado por humano"
              valor={`${data.snapshot.pct_validado_por_humano}%`}
            />
          </div>

          {data.eventos_pendentes > 0 && (
            <Card className="p-4 mb-6 bg-gradient-to-r from-kv-purple-50 to-kv-indigo-bg border-kv-purple-200">
              <div className="flex items-center justify-between gap-4 flex-wrap">
                <div>
                  <p className="text-sm font-medium text-kv-purple-dark">
                    {data.eventos_pendentes} eventos aguardando sua validação
                  </p>
                  <p className="text-xs text-slate-600 mt-1">
                    Quanto mais você valida, mais o sistema aprende seu contexto.
                  </p>
                </div>
                <Link to={`/processos/${id}/validacao`}>
                  <Button variant="secondary">Validar agora</Button>
                </Link>
              </div>
            </Card>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <Card className="lg:col-span-2 p-6">
              <h2 className="font-semibold text-slate-900 mb-1">
                Sugestões de produtividade
              </h2>
              <p className="text-xs text-slate-500 mb-4">
                Geradas pela IA a partir de toda a base de vídeos deste processo.
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

            <Card className="p-6">
              <h2 className="font-semibold text-slate-900 mb-1">
                Onde o tempo é gasto
              </h2>
              <p className="text-xs text-slate-500 mb-4">
                Distribuição agregada por comportamento.
              </p>
              <DistribuicaoChart
                data={data.snapshot.distribuicao_comportamentos.slice(0, 10)}
              />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function Metric({ label, valor }: { label: string; valor: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500 font-medium">
        {label}
      </div>
      <div className="text-2xl font-semibold text-slate-900 mt-1">{valor}</div>
    </Card>
  );
}

function SugestaoCard({
  s,
}: {
  s: {
    prioridade: string;
    area: string;
    situacao: string;
    causa_provavel: string;
    sugestao: string;
    impacto_estimado: string;
    eventos_relacionados?: { comportamentos?: string[] };
  };
}) {
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
          <b className="text-slate-700">Impacto estimado:</b> {s.impacto_estimado || "—"}
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
    <div className="space-y-3">
      <div style={{ width: "100%", height: Math.max(220, data.length * 32) }}>
        <ResponsiveContainer>
          <BarChart data={data} layout="vertical" margin={{ left: 0, right: 16 }}>
            <CartesianGrid stroke="#f1f5f9" />
            <XAxis type="number" tickFormatter={(v) => `${v}%`} fontSize={11} />
            <YAxis
              type="category"
              dataKey="comportamento"
              fontSize={11}
              width={120}
              tickFormatter={(v: string) => (v.length > 18 ? v.slice(0, 16) + "…" : v)}
            />
            <Tooltip
              formatter={(v: number, _name, p: { payload?: { tempo_total_s?: number } }) => [
                `${v}% · ${formatSeg(p?.payload?.tempo_total_s ?? 0)}`,
                "Tempo",
              ]}
            />
            <Bar dataKey="pct_tempo" fill="#7c3aed" radius={[0, 6, 6, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
