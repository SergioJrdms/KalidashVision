import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import {
  Badge,
  Btn,
  Card,
  Empty,
  Icon,
  Help,
  Spinner,
} from "../components/UIKit";
import { PrismAvatar } from "../components/PrismAvatar";
import type { PadraoProcesso, SerieTemporal } from "../lib/types";

const TIPO_INFO: Record<string, { icon: string; rotulo: string }> = {
  tendencia: { icon: "trending-up", rotulo: "Tendência" },
  recorrencia: { icon: "repeat", rotulo: "Recorrência" },
  desvio: { icon: "alert-triangle", rotulo: "Desvio" },
  volatilidade: { icon: "activity", rotulo: "Volatilidade" },
  fluxo: { icon: "git-branch", rotulo: "Fluxo" },
  desperdicio: { icon: "arrow-down", rotulo: "Desperdício recorrente" },
  valor: { icon: "arrow-up", rotulo: "Valor recorrente" },
};

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
      <div className="center" style={{ padding: 60 }}>
        <Spinner size={26} />
      </div>
    );

  const nVid = serie.data?.n_videos ?? 0;
  if (nVid < 3) {
    return (
      <Card style={{ padding: 6 }}>
        <Empty
          icon="activity"
          title="Ainda não há série suficiente para detectar padrões"
          desc={`Padrões precisam de pelo menos 3 vídeos processados (você tem ${nVid}). Envie mais alguns turnos para o Prism identificar tendências, recorrências e desvios.`}
          action={
            <Link to={`/processos/${id}/upload`}>
              <Btn icon="upload">Enviar vídeo</Btn>
            </Link>
          }
        />
      </Card>
    );
  }

  const lista = padroes.data || [];

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="row gap3">
        <PrismAvatar size={36} ring />
        <div>
          <h1 className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>
            Padrões da operação
          </h1>
          <p style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>
            Recorrência e evolução ao longo dos turnos — diferente das sugestões pontuais, que olham o estado atual.
          </p>
        </div>
      </div>

      {serie.data && <GraficoEvolucao serie={serie.data} />}

      {lista.length === 0 ? (
        <Card style={{ padding: 18 }}>
          <p style={{ fontSize: 13.5, color: "var(--muted)" }}>
            Nenhum padrão forte detectado nesta série ainda. Conforme você envia mais turnos, as recorrências e tendências ficam mais nítidas.
          </p>
        </Card>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))",
            gap: 14,
          }}
        >
          {lista.map((p) => (
            <PadraoCard key={p.id} p={p} />
          ))}
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Gráfico de evolução (SVG nativo — stacked area por categoria/comportamento)
// ════════════════════════════════════════════════════════════════════════
const CORES_CHAVE: Record<string, string> = {
  valor_agregado: "var(--va)",
  apoio: "var(--apoio)",
  desperdicio: "var(--desp)",
  nao_classificado: "var(--none)",
};
const PALETA = [
  "#5330C0", "#15A86B", "#E5950E", "#E5484D", "#683BED",
  "#A78BFA", "#0EA5E9", "#84CC16", "#EC4899", "#14B8A6",
];

function GraficoEvolucao({ serie }: { serie: SerieTemporal }) {
  const [modo, setModo] = useState<"categoria" | "comportamento">("categoria");
  const pts = serie.pontos;

  const { dados, chaves } = useMemo(() => {
    const campo = modo === "categoria" ? "share_categoria" : "share_comportamento";
    const somas: Record<string, number> = {};
    pts.forEach((pt) => {
      const obj = (pt as unknown as Record<string, Record<string, number>>)[campo];
      Object.entries(obj).forEach(([k, v]) => {
        somas[k] = (somas[k] || 0) + v;
      });
    });
    const chaves = Object.entries(somas).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k]) => k);
    const dados = pts.map((pt, i) => {
      const obj = (pt as unknown as Record<string, Record<string, number>>)[campo];
      const valores: Record<string, number> = {};
      chaves.forEach((k) => {
        valores[k] = obj[k] ?? 0;
      });
      return { turno: `T${i + 1}`, valores };
    });
    return { dados, chaves };
  }, [pts, modo]);

  // SVG layout
  const W = 920, H = 280, padL = 36, padR = 16, padT = 14, padB = 30;
  const iw = W - padL - padR;
  const ih = H - padT - padB;
  const n = dados.length;
  const x = (i: number) => padL + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v: number) => padT + (1 - v / 100) * ih;
  // cumulativo (de baixo pra cima)
  const stacked = dados.map((d) => {
    let acc = 0;
    const out: Record<string, [number, number]> = {};
    for (const k of chaves) {
      const v = d.valores[k] || 0;
      out[k] = [acc, acc + v];
      acc += v;
    }
    return out;
  });
  const cor = (k: string, idx: number) => CORES_CHAVE[k] || PALETA[idx % PALETA.length];

  return (
    <Card style={{ padding: 20 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10, gap: 8, flexWrap: "wrap" }}>
        <div>
          <h3 className="font-display row gap2" style={{ fontSize: 15, fontWeight: 700 }}>
            Evolução ao longo dos turnos
            <Help text="Share de % do tempo turno a turno (cada vídeo = 1 turno, em ordem cronológica). Permite VER a tendência." />
          </h3>
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>{n} turnos analisados.</p>
        </div>
        <div className="row gap1" style={{ fontSize: 12 }}>
          {(["categoria", "comportamento"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setModo(m)}
              style={{
                padding: "4px 11px",
                borderRadius: 999,
                border: `1px solid ${modo === m ? "var(--accent)" : "var(--line)"}`,
                background: modo === m ? "var(--accent)" : "#fff",
                color: modo === m ? "#fff" : "var(--text)",
                fontWeight: 600,
              }}
            >
              {m === "categoria" ? "Por categoria" : "Por comportamento"}
            </button>
          ))}
        </div>
      </div>

      <div style={{ width: "100%", overflowX: "auto" }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, minWidth: 480 }}>
          {/* grid horizontal a cada 25% */}
          {[0, 25, 50, 75, 100].map((v) => (
            <g key={v}>
              <line x1={padL} y1={y(v)} x2={W - padR} y2={y(v)} stroke="var(--line-2)" />
              <text x={padL - 6} y={y(v) + 4} fontSize={10} textAnchor="end" fill="var(--muted)">
                {v}%
              </text>
            </g>
          ))}
          {/* stack areas (de baixo pra cima) */}
          {chaves.map((k, idx) => {
            const top: [number, number][] = stacked.map((st, i) => [x(i), y(st[k][1])]);
            const bot: [number, number][] = stacked.map((st, i) => [x(i), y(st[k][0])]).reverse();
            const path =
              "M " +
              top.map(([px, py]) => `${px.toFixed(1)} ${py.toFixed(1)}`).join(" L ") +
              " L " +
              bot.map(([px, py]) => `${px.toFixed(1)} ${py.toFixed(1)}`).join(" L ") +
              " Z";
            return (
              <path
                key={k}
                d={path}
                fill={cor(k, idx)}
                fillOpacity={0.78}
                stroke={cor(k, idx)}
                strokeWidth={1}
              />
            );
          })}
          {/* eixo X (turnos) */}
          {dados.map((d, i) => (
            <text key={i} x={x(i)} y={H - padB + 16} fontSize={10} textAnchor="middle" fill="var(--muted)">
              {d.turno}
            </text>
          ))}
        </svg>
      </div>

      {/* Legenda */}
      <div className="row wrap" style={{ gap: 10, marginTop: 8, fontSize: 11.5 }}>
        {chaves.map((k, idx) => (
          <span key={k} className="row gap1">
            <span style={{ width: 10, height: 10, borderRadius: 2, background: cor(k, idx) }} />
            <span style={{ color: "var(--muted)" }}>{nomeFamiliar(k)}</span>
          </span>
        ))}
      </div>
    </Card>
  );
}

function nomeFamiliar(k: string): string {
  if (k === "valor_agregado") return "Valor agregado";
  if (k === "apoio") return "Apoio";
  if (k === "desperdicio") return "Desperdício";
  if (k === "nao_classificado") return "Não classificado";
  return k;
}

// ════════════════════════════════════════════════════════════════════════
// Card de padrão
// ════════════════════════════════════════════════════════════════════════
function tomConf(c: string): "ok" | "warn" | "neutral" {
  return c === "alta" ? "ok" : c === "media" ? "warn" : "neutral";
}
function PadraoCard({ p }: { p: PadraoProcesso }) {
  const info = TIPO_INFO[p.tipo] || { icon: "circle", rotulo: p.tipo };
  return (
    <Card style={{ padding: 18 }}>
      <div className="row gap2 wrap" style={{ marginBottom: 8 }}>
        <span className="badge badge-purple row gap1">
          <Icon name={info.icon} size={12} color="var(--accent-deep)" />
          {info.rotulo}
        </span>
        <Badge tone={tomConf(p.confianca)}>confiança {p.confianca}</Badge>
        {p.relevancia === "alta" && <Badge tone="high">ALTA</Badge>}
      </div>
      <h4 style={{ fontSize: 14.5, fontWeight: 700, color: "var(--ink)" }}>{p.titulo}</h4>
      <p style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.55, marginTop: 6 }}>
        {p.descricao}
      </p>
      {p.recomendacao && (
        <div
          className="soft"
          style={{ borderRadius: 10, padding: "10px 12px", marginTop: 10, fontSize: 12.5, color: "var(--text)", border: "1px solid var(--line)" }}
        >
          <b style={{ color: "var(--ink)" }}>Recomendação. </b>
          {p.recomendacao}
        </div>
      )}
      {p.comportamentos_relacionados && p.comportamentos_relacionados.length > 0 && (
        <div className="row gap1 wrap" style={{ marginTop: 10 }}>
          {p.comportamentos_relacionados.slice(0, 6).map((c) => (
            <code
              key={c}
              style={{ fontSize: 10.5, background: "var(--line-2)", color: "var(--muted)", padding: "1px 6px", borderRadius: 5, fontFamily: "var(--mono)" }}
            >
              {c}
            </code>
          ))}
        </div>
      )}
    </Card>
  );
}
