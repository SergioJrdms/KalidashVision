// ============================================================
// Fase 59 (B4) — A FILA DA DÚVIDA.
//
// Ordenada por MINUTOS EM JOGO, nunca por ordem de chegada: valida-se primeiro
// o que mais move o placar. Cada item mostra os FRAMES do trecho (quadro a
// quadro, como pedido) e o MOTIVO — sem o motivo, o validador está adivinhando
// junto com a máquina.
//
// Dois tipos que NÃO se misturam:
//   • sem evidência   → trecho curto demais para afirmar OU duvidar.
//                       Resolve-se gravando mais denso.
//   • discordância    → as amostras do minuto brigaram entre si.
//   • camada          → uma verificação da cena contradiz o rótulo.
// ============================================================
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Btn, Card, Icon, Empty, PanelHead, Segmented, toast } from "../design/ui";
import { FrameStripReal } from "../lib/frames";
import type { ProcHeaderMock } from "../lib/adapt";
import type { Go } from "../design/Shell";
import type { ItemDuvida, TipoDuvida } from "../lib/types";

const TIPO_ROTULO: Record<string, { nome: string; cor: string; dica: string }> = {
  sem_evidencia: {
    nome: "Sem evidência", cor: "var(--muted)",
    dica: "Trecho curto demais para julgar — some gravando mais denso, não validando.",
  },
  discordancia: {
    nome: "Amostras discordaram", cor: "#c98a00",
    dica: "As leituras do mesmo minuto brigaram entre si.",
  },
  camada: {
    nome: "A cena contradiz", cor: "var(--desp)",
    dica: "Uma verificação determinística achou o rótulo incompatível com a cena.",
  },
};

export default function Duvidas({ proc }: { proc: ProcHeaderMock; go: Go }) {
  const qc = useQueryClient();
  const [rotulo, setRotulo] = useState<string | null>(null);
  const [tipo, setTipo] = useState<string>("todos");

  const q = useQuery({
    queryKey: ["duvidas", proc.id, rotulo, tipo],
    queryFn: () => api.duvidas.listar(proc.id, rotulo, tipo === "todos" ? null : tipo),
  });

  const validar = useMutation({
    mutationFn: ({ id, acao, label }: { id: string; acao: "confirmar" | "descartar"; label?: string }) =>
      api.eventos.validar(id, acao, label),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["duvidas", proc.id] });
      qc.invalidateQueries({ queryKey: ["diaadia", proc.id] });
      toast("Registrado — a curva de dúvida se atualiza no próximo cálculo.");
    },
    onError: (e: Error) => toast(`Não deu: ${e.message}`, { color: "var(--desp)" }),
  });

  if (q.isLoading) return <Empty icon="loader" title="Montando a fila…" />;
  const d = q.data;
  if (!d) return <Empty icon="alert-triangle" title="Não foi possível carregar a fila" />;

  if (d.total === 0) {
    return (
      <Empty
        icon="check-circle"
        title="Nada em dúvida agora"
        desc="Todo o tempo observado foi lido com concordância suficiente entre as amostras. Quando o sistema não souber, os trechos aparecem aqui — do que mais pesa para o que menos pesa."
      />
    );
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="O que o sistema não sabe"
          ajuda="Trechos em que a leitura ficou em dúvida. A fila é ordenada por MINUTOS EM JOGO — validar de cima para baixo é o que mais move o placar. O limiar de dúvida é configurável por processo."
          leitura="Cada trecho validado aqui ensina o sistema e derruba a curva de dúvida."
          right={
            <span className="font-mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
              limiar {d.limiar.toFixed(2)}
            </span>
          }
        />
        <div className="row gap2 wrap" style={{ alignItems: "baseline", marginBottom: 12 }}>
          <span className="font-display tnum" style={{ fontSize: 24, fontWeight: 700, color: "var(--ink)" }}>
            {d.minutos_totais.toFixed(0)} min
          </span>
          <span style={{ fontSize: 13, color: "var(--muted)" }}>
            em {d.total} trecho(s) esperando julgamento
          </span>
        </div>

        {/* Tipos — separados de propósito: exigem ações diferentes */}
        <div className="row gap2 wrap" style={{ marginBottom: 12 }}>
          <Segmented
            size="sm"
            value={tipo}
            onChange={setTipo}
            options={[
              { value: "todos", label: "Todos" },
              ...d.por_tipo.map((t) => ({
                value: t.tipo,
                label: `${TIPO_ROTULO[t.tipo]?.nome || t.tipo} (${t.minutos.toFixed(0)}min)`,
              })),
            ]}
          />
        </div>

        {/* Por rótulo — é aqui que se enxerga um rótulo virar depósito da dúvida */}
        <div className="col" style={{ gap: 6 }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
            Onde a dúvida se concentra
          </span>
          <div className="row gap1 wrap">
            {rotulo && (
              <button onClick={() => setRotulo(null)}
                style={{ cursor: "pointer", border: "1px solid var(--line)", background: "#fff", borderRadius: 99, padding: "3px 11px", fontSize: 11.5, fontWeight: 600 }}>
                ✕ todos os rótulos
              </button>
            )}
            {d.por_rotulo.map((r) => {
              const sel = r.rotulo === rotulo;
              return (
                <button
                  key={r.rotulo}
                  onClick={() => setRotulo(sel ? null : r.rotulo)}
                  title={`${r.eventos} trecho(s) · ${r.minutos} min em dúvida`}
                  style={{
                    cursor: "pointer", borderRadius: 99, padding: "3px 11px", fontSize: 11.5,
                    fontWeight: 600, fontFamily: "var(--mono)",
                    border: sel ? "1px solid var(--accent)" : "1px solid var(--line)",
                    background: sel ? "var(--accent-soft)" : "#fff",
                    color: sel ? "var(--accent)" : "var(--text)",
                  }}
                >
                  {r.rotulo} · {r.minutos.toFixed(0)}min
                </button>
              );
            })}
          </div>
        </div>
      </Card>

      {d.itens.length === 0 ? (
        <Empty icon="filter" title="Nenhum trecho com esse filtro"
               desc="Ajuste o rótulo ou o tipo acima para ver os demais." />
      ) : (
        d.itens.map((it) => (
          <ItemDaFila key={it.id} it={it} onValidar={(acao) => validar.mutate({ id: it.id, acao })} />
        ))
      )}
    </div>
  );
}

function ItemDaFila({ it, onValidar }: { it: ItemDuvida; onValidar: (a: "confirmar" | "descartar") => void }) {
  const tom = TIPO_ROTULO[it.tipo] || TIPO_ROTULO.discordancia;
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div className="row gap2 wrap" style={{ alignItems: "center", padding: "12px 16px", borderBottom: "1px solid var(--line-2)" }}>
        <code className="font-mono" style={{ background: "var(--line-2)", padding: "2px 9px", borderRadius: 6, fontSize: 12 }}>
          {it.rotulo}
        </code>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: tom.cor }} title={tom.dica}>
          <Icon name="help-circle" size={12} /> {tom.nome}
        </span>
        <span className="font-mono" style={{ fontSize: 11, color: "var(--faint)" }}>
          {it.cam_id || "cam"} · {Math.round(it.ini)}→{Math.round(it.fim)}s
          {it.confianca != null && ` · concordância ${(it.confianca * 100).toFixed(0)}%`}
          {it.n_amostras != null && ` · ${it.n_amostras} amostra(s)`}
        </span>
        <span className="font-display tnum" style={{ marginLeft: "auto", fontSize: 16, fontWeight: 700, color: "var(--ink)" }}>
          {it.minutos.toFixed(1)} min
        </span>
      </div>

      {/* Os frames do trecho — quadro a quadro, servidos do cache já aquecido */}
      <FrameStripReal ativo={{ id: it.id, pessoa: it.pessoa, label: it.rotulo, ini: it.ini, fim: it.fim }} />

      <div className="col" style={{ gap: 10, padding: "12px 16px" }}>
        <p style={{ fontSize: 12.5, color: "var(--text)", margin: 0, lineHeight: 1.5 }}>
          <b style={{ color: tom.cor }}>Por que está aqui:</b> {it.motivo}
        </p>
        {it.rotulos_competindo?.length > 1 && (
          <p style={{ fontSize: 11.5, color: "var(--muted)", margin: 0 }}>
            Disputaram o minuto:{" "}
            {it.rotulos_competindo.map((r) => (
              <code key={r} className="font-mono" style={{ fontSize: 11, background: "var(--line-2)", padding: "1px 6px", borderRadius: 4, marginRight: 4 }}>{r}</code>
            ))}
          </p>
        )}
        {it.tipo === "sem_evidencia" ? (
          <p style={{ fontSize: 11.5, color: "var(--muted)", margin: 0, fontStyle: "italic" }}>
            Este caso não se resolve validando — o trecho é curto demais para
            julgar. Some quando a gravação ficar mais densa.
          </p>
        ) : (
          <div className="row gap2 wrap">
            <Btn size="sm" icon="check" onClick={() => onValidar("confirmar")}>
              O rótulo está certo
            </Btn>
            <Btn size="sm" variant="ghost" icon="x" onClick={() => onValidar("descartar")}>
              Está errado
            </Btn>
          </div>
        )}
      </div>
    </Card>
  );
}
