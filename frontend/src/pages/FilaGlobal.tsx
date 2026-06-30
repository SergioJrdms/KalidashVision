// ============================================================
// Fila de Processamento — visão GLOBAL (Fase 8). Agrega a inbox
// `segmentos` de TODOS os processos da empresa. Polling que para
// sozinho quando nada está ativo. Abre a fila de cada processo.
// ============================================================
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { FilaGlobalResposta, StatusSegmento } from "../lib/types";
import type { Go } from "../design/Shell";
import { Badge, Card, Empty, Icon, PanelHead, Ring } from "../design/ui";

const STATUS_META: Record<StatusSegmento, { tone: string; label: string }> = {
  pendente: { tone: "neutral", label: "Pendente" },
  enfileirado: { tone: "info", label: "Na fila" },
  processando: { tone: "warn", label: "Processando" },
  concluido: { tone: "ok", label: "Concluído" },
  erro: { tone: "high", label: "Erro" },
};

function ativo(c: Record<StatusSegmento, number>): number {
  return (c.pendente ?? 0) + (c.enfileirado ?? 0) + (c.processando ?? 0);
}

export default function FilaGlobal({ go }: { go: Go }) {
  const q = useQuery({
    queryKey: ["fila-global"],
    queryFn: () => api.processos.filaGlobal(),
    refetchInterval: (query) => {
      const d = query.state.data as FilaGlobalResposta | undefined;
      return d && ativo(d.contagens) > 0 ? 4000 : false;
    },
  });

  const d = q.data;
  const c = d?.contagens;
  const total = d?.total ?? 0;
  const concl = c?.concluido ?? 0;
  const naFila = (c?.pendente ?? 0) + (c?.enfileirado ?? 0);
  const proc_ = c?.processando ?? 0;
  const erros = c?.erro ?? 0;
  const pct = total > 0 ? Math.round((concl / total) * 100) : 0;
  const processos = (d?.processos ?? []).filter((p) => p.total > 0);

  return (
    <div className="col" style={{ gap: 18, maxWidth: 1080, margin: "0 auto" }}>
      <PanelHead
        titulo="Fila de processamento · todos os processos"
        leitura="Os segmentos enviados pelo edge entram numa inbox e são processados 1 por 1 (cam1+cam2 do mesmo instante viram um só). Aqui você vê o estado de TODOS os processos; clique para abrir e agir em cada um."
      />

      <Card style={{ padding: "18px 20px" }}>
        <div className="row gap4 wrap" style={{ alignItems: "center" }}>
          <Ring pct={pct} size={66} stroke={7} color="var(--va)">
            <span className="tnum font-display" style={{ fontSize: 16, fontWeight: 700 }}>{pct}%</span>
          </Ring>
          <div className="grow">
            <div className="font-display" style={{ fontSize: 17, fontWeight: 700, color: "var(--ink)" }}>
              {total === 0 ? "Nenhum segmento na inbox" : `${concl} de ${total} segmentos processados`}
            </div>
            <div className="row gap3 wrap" style={{ marginTop: 6, fontSize: 12.5, color: "var(--muted)" }}>
              <span><b className="tnum" style={{ color: "var(--ink)" }}>{naFila}</b> na fila</span>
              <span><b className="tnum" style={{ color: proc_ > 0 ? "var(--accent)" : "var(--ink)" }}>{proc_}</b> processando</span>
              <span><b className="tnum" style={{ color: erros > 0 ? "var(--desp)" : "var(--ink)" }}>{erros}</b> com erro</span>
            </div>
          </div>
        </div>
      </Card>

      <Card style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--soft)", color: "var(--muted)", fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".05em" }}>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Processo</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Na fila</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Processando</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Concluídos</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Erros</th>
                <th style={{ padding: "11px 12px", textAlign: "right" }}>Ação</th>
              </tr>
            </thead>
            <tbody>
              {processos.map((p) => {
                const pc = p.contagens;
                const naf = (pc.pendente ?? 0) + (pc.enfileirado ?? 0);
                return (
                  <tr key={p.processo} style={{ borderTop: "1px solid var(--line-2)" }}>
                    <td style={{ padding: "10px 12px", fontWeight: 600, color: "var(--ink)" }}>{p.processo}</td>
                    <td style={{ padding: "10px 12px", textAlign: "center" }} className="tnum">{naf || "—"}</td>
                    <td style={{ padding: "10px 12px", textAlign: "center" }}>
                      {pc.processando > 0 ? <Badge tone={STATUS_META.processando.tone}>{pc.processando}</Badge> : <span style={{ color: "var(--faint)" }}>—</span>}
                    </td>
                    <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--va)" }} className="tnum">{pc.concluido || "—"}</td>
                    <td style={{ padding: "10px 12px", textAlign: "center" }}>
                      {pc.erro > 0 ? <Badge tone={STATUS_META.erro.tone}>{pc.erro}</Badge> : <span style={{ color: "var(--faint)" }}>—</span>}
                    </td>
                    <td style={{ padding: "10px 12px", textAlign: "right" }}>
                      {p.processo_id ? (
                        <button
                          onClick={() => go("processo", p.processo_id!, "fila")}
                          className="row gap1"
                          style={{ display: "inline-flex", fontSize: 12, fontWeight: 600, border: "1px solid var(--line)", background: "#fff", color: "var(--accent-deep)", borderRadius: 8, padding: "5px 10px" }}
                        >
                          Abrir fila <Icon name="arrow-right" size={13} />
                        </button>
                      ) : (
                        <span style={{ color: "var(--faint)", fontSize: 12 }}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {q.isLoading ? (
          <Empty icon="loader" title="Carregando…" />
        ) : processos.length === 0 ? (
          <Empty icon="inbox" title="Nada na fila" desc="Quando o edge enviar segmentos, eles aparecem aqui por processo." />
        ) : null}
      </Card>
    </div>
  );
}
