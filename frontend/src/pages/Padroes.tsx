import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import { Badge, Button, Card, EmptyState, Spinner, Tooltip } from "../components/UI";
import { PrismAvatar } from "../components/PrismAvatar";
import type { PadraoProcesso, SerieTemporal } from "../lib/types";

const CORES = [
  "#7c3aed", "#10b981", "#f59e0b", "#ef4444", "#6366f1",
  "#a855f7", "#0ea5e9", "#84cc16", "#ec4899", "#14b8a6",
];

const TIPO_INFO: Record<string, { icone: string; rotulo: string }> = {
  tendencia: { icone: "↗", rotulo: "Tendência" },
  recorrencia: { icone: "↻", rotulo: "Recorrência" },
  desvio: { icone: "⚠", rotulo: "Desvio" },
  volatilidade: { icone: "∿", rotulo: "Volatilidade" },
  fluxo: { icone: "⇄", rotulo: "Fluxo" },
  desperdicio: { icone: "▼", rotulo: "Desperdício recorrente" },
  valor: { icone: "▲", rotulo: "Valor recorrente" },
};

function tomConfianca(c: string): "success" | "warning" | "neutral" {
  if (c === "alta") return "success";
  if (c === "media") return "warning";
  return "neutral";
}

export default function Padroes() {
  const { id } = useParams<{ id: string }>();
  const padroes = useQuery({
    queryKey: ["padroes", id],
    queryFn: () => api.padroes.doProcesso(id!),
    enabled: !!id,
  });
  const serie = useQuery({
    queryKey: ["serie-temporal", id],
    queryFn: () => api.padroes.serie(id!),
    enabled: !!id,
  });

  if (padroes.isLoading || serie.isLoading)
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner className="h-8 w-8" />
      </div>
    );

  const nVideos = serie.data?.n_videos ?? 0;
  const lista = padroes.data || [];

  // série insuficiente
  if (nVideos < 3) {
    return (
      <Card className="p-2">
        <EmptyState
          title="Ainda não há série suficiente para detectar padrões"
          description={`Padrões precisam de pelo menos 3 vídeos processados (você tem ${nVideos}). Processe mais alguns turnos para o Prism identificar tendências, recorrências e desvios ao longo do tempo.`}
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
      <div className="flex items-center gap-2.5">
        <PrismAvatar size={32} />
        <div>
          <h2 className="font-semibold text-slate-900 leading-tight">
            Padrões da operação
          </h2>
          <p className="text-xs text-slate-500 leading-tight">
            Recorrência e evolução ao longo dos turnos — diferente das sugestões
            pontuais, que olham o estado atual.
          </p>
        </div>
      </div>

      {serie.data && <GraficoEvolucao serie={serie.data} />}

      {lista.length === 0 ? (
        <Card className="p-6">
          <p className="text-sm text-slate-500">
            Nenhum padrão forte detectado nesta série ainda. Conforme você
            processa mais turnos, tendências e recorrências ficam mais nítidas.
          </p>
        </Card>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {lista.map((p) => (
            <PadraoCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}

function GraficoEvolucao({ serie }: { serie: SerieTemporal }) {
  const [modo, setModo] = useState<"comportamento" | "categoria">("categoria");

  const { dados, chaves } = useMemo(() => {
    const campo = modo === "categoria" ? "share_categoria" : "share_comportamento";
    // chaves mais relevantes (maior média) — limita a 6 para não poluir
    const somas: Record<string, number> = {};
    serie.pontos.forEach((pt) => {
      const obj = pt[campo] as Record<string, number>;
      Object.entries(obj).forEach(([k, v]) => {
        somas[k] = (somas[k] || 0) + v;
      });
    });
    const chaves = Object.entries(somas)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([k]) => k);
    const dados = serie.pontos.map((pt, i) => {
      const obj = pt[campo] as Record<string, number>;
      const linha: Record<string, number | string> = {
        turno: `T${i + 1}`,
      };
      chaves.forEach((k) => {
        linha[k] = obj[k] ?? 0;
      });
      return linha;
    });
    return { dados, chaves };
  }, [serie, modo]);

  return (
    <Card className="p-6">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-1">
        <h3 className="font-semibold text-slate-900 flex items-center gap-2">
          Evolução ao longo dos turnos
          <Tooltip text="Share de % do tempo (eixo Y) de cada comportamento/categoria, turno a turno (cada vídeo é um turno, em ordem cronológica). Permite VER a tendência." />
        </h3>
        <div className="flex gap-1 text-xs">
          {(["categoria", "comportamento"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setModo(m)}
              className={`px-2.5 py-1 rounded-full border transition ${
                modo === m
                  ? "bg-kv-purple text-white border-kv-purple"
                  : "bg-white text-slate-600 border-slate-200 hover:border-kv-purple-200"
              }`}
            >
              {m === "categoria" ? "Por categoria" : "Por comportamento"}
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs text-slate-500 mb-3">
        {serie.n_videos} turnos analisados.
      </p>
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <AreaChart data={dados} margin={{ left: 0, right: 12, top: 8 }}>
            <CartesianGrid stroke="#f1f5f9" />
            <XAxis dataKey="turno" fontSize={11} />
            <YAxis tickFormatter={(v) => `${v}%`} fontSize={11} />
            <RTooltip formatter={(v: number, n: string) => [`${v}%`, n]} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {chaves.map((k, i) => (
              <Area
                key={k}
                type="monotone"
                dataKey={k}
                stackId="1"
                stroke={CORES[i % CORES.length]}
                fill={CORES[i % CORES.length]}
                fillOpacity={0.18}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function PadraoCard({ p }: { p: PadraoProcesso }) {
  const info = TIPO_INFO[p.tipo] || { icone: "•", rotulo: p.tipo };
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        <span className="inline-flex items-center gap-1 text-xs font-medium text-kv-purple-dark bg-kv-purple-50 border border-kv-purple-200 rounded-full px-2 py-0.5">
          <span aria-hidden>{info.icone}</span> {info.rotulo}
        </span>
        <Badge tone={tomConfianca(p.confianca)}>confiança {p.confianca}</Badge>
        {p.relevancia === "alta" && <Badge tone="alta">ALTA</Badge>}
      </div>
      <h4 className="font-semibold text-slate-900 text-sm">{p.titulo}</h4>
      <p className="text-sm text-slate-600 mt-1 leading-relaxed">{p.descricao}</p>
      {p.recomendacao && (
        <div className="mt-2 text-sm text-slate-700 bg-slate-50 rounded-lg p-2.5 border border-slate-100">
          <span className="font-semibold text-slate-800">Recomendação. </span>
          {p.recomendacao}
        </div>
      )}
      {p.comportamentos_relacionados && p.comportamentos_relacionados.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {p.comportamentos_relacionados.slice(0, 6).map((c) => (
            <code key={c} className="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
              {c}
            </code>
          ))}
        </div>
      )}
    </Card>
  );
}
