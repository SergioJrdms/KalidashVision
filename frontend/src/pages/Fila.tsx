// ============================================================
// Fila de Processamento (Fase 7) — visibilidade da inbox `segmentos`.
// Mostra o estado do lote: pendente/na fila/processando/concluído/erro,
// com polling que para sozinho quando a fila zera. Permite "Processar
// agora" (dispara o lote) e "Reprocessar erros". Só LÊ a tabela segmentos.
// ============================================================
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ProcHeaderMock } from "../lib/adapt";
import type { FilaResposta, SegmentoFila, StatusSegmento } from "../lib/types";
import { Badge, Btn, Card, Empty, Icon, PanelHead, Ring, toast } from "../design/ui";

const STATUS_META: Record<StatusSegmento, { tone: string; label: string }> = {
  pendente: { tone: "neutral", label: "Pendente" },
  enfileirado: { tone: "info", label: "Na fila" },
  processando: { tone: "warn", label: "Processando" },
  concluido: { tone: "ok", label: "Concluído" },
  erro: { tone: "high", label: "Erro" },
};
const ORDEM: StatusSegmento[] = ["pendente", "enfileirado", "processando", "concluido", "erro"];

// token "20260626_155058" → "26/06 15:50:58"
function fmtToken(tok: string | null): string {
  if (!tok) return "—";
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(tok);
  if (!m) return tok;
  const [, , mo, d, h, mi, s] = m;
  return `${d}/${mo} ${h}:${mi}:${s}`;
}

function rel(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const min = Math.round((Date.now() - t) / 60000);
  if (min < 1) return "agora";
  if (min < 60) return `há ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `há ${h}h`;
  return `há ${Math.round(h / 24)} d`;
}

function camLabel(camId: string | null): string {
  if (!camId) return "—";
  const m = /cam(\d+)/i.exec(camId);
  return m ? `Cam ${m[1]}` : camId;
}

export default function Fila({ proc }: { proc: ProcHeaderMock }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<string>("todos");

  const q = useQuery({
    queryKey: ["fila", proc.id, status],
    queryFn: () => api.processos.fila(proc.id, status),
    // Polling enquanto há trabalho ativo; para sozinho quando a fila zera.
    refetchInterval: (query) => {
      const d = query.state.data as FilaResposta | undefined;
      if (!d) return 4000;
      const ativo = d.contagens.pendente + d.contagens.enfileirado + d.contagens.processando;
      return ativo > 0 ? 4000 : false;
    },
  });

  const processar = useMutation({
    mutationFn: () => api.processos.processarLote(proc.id),
    onSuccess: (r) => {
      toast(`Lote disparado · ${r.itens} item(ns) na fila`, { icon: "play" });
      qc.invalidateQueries({ queryKey: ["fila", proc.id] });
    },
    onError: (e) => toast(`Falha ao disparar: ${(e as Error).message}`, { icon: "alert-circle", color: "#F8B4B6" }),
  });
  const reprocessar = useMutation({
    mutationFn: () => api.processos.reprocessarErros(proc.id),
    onSuccess: (r) => {
      toast(`${r.reset} erro(s) re-enfileirado(s)`, { icon: "rotate-cw" });
      qc.invalidateQueries({ queryKey: ["fila", proc.id] });
    },
    onError: (e) => toast(`Falha: ${(e as Error).message}`, { icon: "alert-circle", color: "#F8B4B6" }),
  });

  const data = q.data;
  const c = data?.contagens;
  const total = data?.total ?? 0;
  const concl = c?.concluido ?? 0;
  const naFila = (c?.pendente ?? 0) + (c?.enfileirado ?? 0);
  const proc_ = c?.processando ?? 0;
  const erros = c?.erro ?? 0;
  const pct = total > 0 ? Math.round((concl / total) * 100) : 0;
  const itens = data?.itens ?? [];

  return (
    <div className="col" style={{ gap: 18, maxWidth: 1080, margin: "0 auto" }}>
      <PanelHead
        titulo="Fila de processamento"
        leitura="Os segmentos enviados pelo edge entram aqui e são processados 1 por 1 (cam1+cam2 do mesmo instante viram um só). O processamento começa quando o lote termina — ou dispare manualmente."
        right={
          <div className="row gap2">
            {erros > 0 && (
              <Btn variant="secondary" size="sm" icon="rotate-cw" disabled={reprocessar.isPending} onClick={() => reprocessar.mutate()}>
                Reprocessar erros
              </Btn>
            )}
            <Btn variant="primary" size="sm" icon="play" disabled={processar.isPending || (c?.pendente ?? 0) === 0} onClick={() => processar.mutate()}>
              Processar agora
            </Btn>
          </div>
        }
      />

      {/* Resumo */}
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

      {/* Filtros por status */}
      <div className="row gap1 wrap">
        {["todos", ...ORDEM].map((s) => {
          const count = s === "todos" ? total : (c?.[s as StatusSegmento] ?? 0);
          const label = s === "todos" ? "Todos" : STATUS_META[s as StatusSegmento].label;
          const active = status === s;
          return (
            <button
              key={s}
              onClick={() => setStatus(s)}
              style={{ padding: "5px 12px", borderRadius: 99, fontSize: 12, fontWeight: 600, border: "1px solid", borderColor: active ? "var(--accent)" : "var(--line)", background: active ? "var(--accent)" : "#fff", color: active ? "#fff" : "var(--muted)" }}
            >
              {label} · {count}
            </button>
          );
        })}
      </div>

      {/* Tabela */}
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--soft)", color: "var(--muted)", fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".05em" }}>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Segmento</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Câmera</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Status</th>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Recebido</th>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Processado</th>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Detalhe</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((s: SegmentoFila) => {
                const m = STATUS_META[s.status] || STATUS_META.pendente;
                return (
                  <tr key={s.id} style={{ borderTop: "1px solid var(--line-2)" }}>
                    <td style={{ padding: "10px 12px", fontFamily: "var(--mono)", fontSize: 12 }}>{fmtToken(s.seg_token)}</td>
                    <td style={{ padding: "10px 12px", textAlign: "center" }}>
                      {s.cam_id ? <span className="badge badge-purple" style={{ fontSize: 10 }}>{camLabel(s.cam_id)}</span> : <span style={{ color: "var(--faint)" }}>—</span>}
                    </td>
                    <td style={{ padding: "10px 12px", textAlign: "center" }}>
                      <span className="row gap1" style={{ justifyContent: "center" }}>
                        {s.status === "processando" && <span className="spin" style={{ width: 12, height: 12, border: "2px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} />}
                        <Badge tone={m.tone}>{m.label}</Badge>
                      </span>
                    </td>
                    <td style={{ padding: "10px 12px", color: "var(--muted)", fontSize: 12 }}>{rel(s.recebido_em)}</td>
                    <td style={{ padding: "10px 12px", color: "var(--muted)", fontSize: 12 }}>{rel(s.processado_em)}</td>
                    <td style={{ padding: "10px 12px", color: s.erro ? "var(--desp)" : "var(--faint)", fontSize: 12, maxWidth: 280 }}>
                      <div className="truncate" title={s.erro || undefined}>{s.erro || "—"}</div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {q.isLoading ? (
          <Empty icon="loader" title="Carregando fila…" />
        ) : itens.length === 0 ? (
          <Empty icon="inbox" title="Nada na fila" desc="Quando o edge enviar segmentos, eles aparecem aqui para processamento." />
        ) : null}
      </Card>
    </div>
  );
}
