import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import {
  Badge,
  Btn,
  Card,
  Empty,
  Help,
  Icon,
  PanelHead,
  RingMaturidade,
  Spinner,
  Track,
  fmtSeg,
  leanCor,
  leanLabel,
  leanShort,
  toast,
} from "../components/UIKit";
import { PrismAvatar } from "../components/PrismAvatar";
import type { EventoPendente, PerguntaProcesso } from "../lib/types";

export default function Validacao() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const pendentes = useQuery({
    queryKey: ["eventos-pendentes", id],
    queryFn: () => api.processos.eventosPendentes(id!),
    enabled: !!id,
  });
  const perguntas = useQuery({
    queryKey: ["perguntas-pendentes", id],
    queryFn: () => api.perguntas.listar(id!, "pendente"),
    enabled: !!id,
  });
  const proc = useQuery({
    queryKey: ["processo", id],
    queryFn: () => api.processos.detalhe(id!),
    enabled: !!id,
  });

  if (pendentes.isLoading || proc.isLoading)
    return (
      <div className="center" style={{ padding: 60 }}>
        <Spinner size={26} />
      </div>
    );

  const fila = pendentes.data || [];
  const totalInicial = fila.length;
  const matBase = (proc.data as { maturidade?: number } | undefined)?.maturidade ?? 0;
  // boost local visual conforme o usuário valida (rodada atual)
  const [boost, setBoost] = useState(0);
  const maturidade = Math.min(99, Math.round(matBase + Math.min(16, boost * 1.4)));

  function resolvido() {
    setBoost((b) => b + 1);
    qc.invalidateQueries({ queryKey: ["eventos-pendentes", id] });
    qc.invalidateQueries({ queryKey: ["dashboard", id] });
  }

  return (
    <div className="col" style={{ gap: 18, maxWidth: 1080, margin: "0 auto" }}>
      <SessionHeader
        nome={proc.data?.processo || ""}
        maturidade={maturidade}
        restantes={fila.length}
        total={totalInicial}
        feitos={boost}
      />

      {perguntas.data && perguntas.data.length > 0 && (
        <PerguntasConversa
          perguntas={perguntas.data}
          processoId={id!}
          onResolvido={() => {
            qc.invalidateQueries({ queryKey: ["perguntas-pendentes", id] });
            qc.invalidateQueries({ queryKey: ["dashboard", id] });
          }}
        />
      )}

      {fila.length === 0 ? (
        <Card style={{ padding: 6 }}>
          <Empty
            icon="check-circle-2"
            title={boost > 0 ? `Você zerou a fila — ${boost} validações nesta sessão` : "Nada a validar manualmente"}
            desc={
              boost > 0
                ? "O Prism está mais inteligente agora. Veja o impacto nas próximas análises."
                : "Todos os eventos já foram confirmados (auto ou por você). Envie mais vídeos para continuar treinando."
            }
          />
        </Card>
      ) : (
        <FilaFoco
          evento={fila[0]}
          processoId={id!}
          restantes={fila.length}
          total={totalInicial}
          onResolvido={resolvido}
        />
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Cabeçalho da sessão (maturidade + progresso)
// ════════════════════════════════════════════════════════════════════════
function SessionHeader({
  nome,
  maturidade,
  restantes,
  total,
  feitos,
}: {
  nome: string;
  maturidade: number;
  restantes: number;
  total: number;
  feitos: number;
}) {
  const progresso = total === 0 ? 100 : Math.round(((total - restantes) / total) * 100);
  return (
    <Card style={{ padding: 18 }}>
      <div className="row gap3" style={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="row gap3">
          <RingMaturidade pct={maturidade} size={64} />
          <div>
            <div style={{ fontSize: 11, color: "var(--accent-deep)", fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase" }}>
              Maturidade do Prism
            </div>
            <div className="font-display" style={{ fontSize: 16, fontWeight: 700, color: "var(--ink)" }}>
              {nome}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>
              {feitos > 0 ? `+${feitos} validações nesta sessão` : "cada validação aumenta a confiança"}
            </div>
          </div>
        </div>
        <div className="col" style={{ alignItems: "flex-end", minWidth: 200, gap: 6 }}>
          <div className="row gap2" style={{ fontSize: 11.5, color: "var(--muted)" }}>
            <Icon name="git-pull-request-arrow" size={14} />
            <span>
              <b style={{ color: "var(--ink)" }}>{restantes}</b> restantes · {total} total
            </span>
          </div>
          <div style={{ width: 200 }}>
            <Track pct={progresso} color="var(--accent)" />
          </div>
        </div>
      </div>
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Fila Foco Único — um evento por vez, click-único, animação satisfatória
// ════════════════════════════════════════════════════════════════════════
function FilaFoco({
  evento,
  processoId,
  restantes,
  total,
  onResolvido,
}: {
  evento: EventoPendente;
  processoId: string;
  restantes: number;
  total: number;
  onResolvido: () => void;
}) {
  const [editandoLabel, setEditandoLabel] = useState(false);
  const [labelNovo, setLabelNovo] = useState(evento.comportamento_label);
  const [deixando, setDeixando] = useState<null | "ok" | "ko">(null);
  // labels canônicos (autocomplete) — vem do dashboard cacheado
  const dash = useQuery({
    queryKey: ["dashboard", processoId],
    queryFn: () => api.processos.dashboard(processoId),
    enabled: !!processoId,
    staleTime: 30_000,
  });
  const labels = useMemo(
    () =>
      (dash.data?.snapshot.distribuicao_comportamentos || [])
        .map((d) => d.comportamento)
        .sort(),
    [dash.data]
  );

  const frames = useQuery({
    queryKey: ["frames", evento.id],
    queryFn: () => api.eventos.frames(evento.id),
    staleTime: 5 * 60 * 1000,
  });

  const validar = useMutation({
    mutationFn: ({ acao, label }: { acao: "confirmar" | "corrigir" | "descartar"; label?: string }) =>
      api.eventos.validar(evento.id, acao, label),
    onSuccess: (_d, vars) => {
      setDeixando(vars.acao === "descartar" ? "ko" : "ok");
      toast(
        vars.acao === "confirmar"
          ? "Confirmado — o Prism aprendeu com você."
          : vars.acao === "corrigir"
            ? `Corrigido para "${vars.label}".`
            : "Descartado — falso alarme registrado.",
        { icon: vars.acao === "descartar" ? "x" : "check", color: vars.acao === "descartar" ? "#F8B4B6" : "#3EE6AE" }
      );
      window.setTimeout(() => {
        setDeixando(null);
        onResolvido();
      }, 360);
    },
  });

  // categoria Lean prevista (derivada do label)
  const catPrev = leanShort(evento.categoria_lean_prevista);
  const confPct = Math.round((evento.confianca || 0) * 100);

  const animClass =
    deixando === "ok" ? "leave-r" : deixando === "ko" ? "leave-l" : "anim-pop";

  return (
    <Card key={evento.id} className={animClass} style={{ padding: 20 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <span
          className="font-mono"
          style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".08em" }}
        >
          Evento {total - restantes + 1} de {total}
        </span>
        <span className="row gap2 font-mono" style={{ fontSize: 11, color: "var(--muted)" }}>
          <span className="live-dot on" /> ao vivo
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 22, alignItems: "flex-start" }}>
        <div>
          {/* Frames */}
          <div className="row gap2" style={{ overflowX: "auto", marginBottom: 14 }}>
            {frames.isLoading && (
              <div className="dotbg" style={{ height: 200, width: "100%", borderRadius: 12 }} />
            )}
            {frames.data?.frames.map((src, i) => (
              <img
                key={i}
                src={src}
                alt={`quadro ${i + 1}`}
                style={{
                  height: 200,
                  borderRadius: 12,
                  border: "1px solid var(--line)",
                  background: "#000",
                }}
              />
            ))}
            {frames.error && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--apoio)",
                  background: "var(--apoio-bg)",
                  border: "1px solid rgba(229,149,14,.22)",
                  borderRadius: 10,
                  padding: "8px 11px",
                  maxWidth: 360,
                }}
              >
                {(frames.error as Error).message}
              </div>
            )}
          </div>

          <div className="row gap2 wrap" style={{ marginBottom: 6 }}>
            <Badge tone="purple">
              Pessoa #{String(evento.pessoa_track_id).padStart(3, "0")}
            </Badge>
            <Badge tone="neutral">
              {evento.tempo_inicio_s.toFixed(0)}s → {evento.tempo_fim_s.toFixed(0)}s · {fmtSeg(evento.tempo_fim_s - evento.tempo_inicio_s)}
            </Badge>
            <Badge tone={confPct >= 80 ? "ok" : confPct >= 60 ? "warn" : "neutral"}>
              {confPct}% confiança
            </Badge>
            {catPrev !== "none" && (
              <span
                className="badge"
                style={{ background: "#fff", color: leanCor(catPrev), borderColor: leanCor(catPrev) }}
              >
                provável {leanLabel(catPrev)}
              </span>
            )}
          </div>

          {!editandoLabel ? (
            <div className="row gap2" style={{ marginTop: 8 }}>
              <h3 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--ink)" }}>
                {evento.comportamento_label}
              </h3>
              <button
                onClick={() => setEditandoLabel(true)}
                title="Editar label"
                className="row gap1"
                style={{ fontSize: 11, color: "var(--accent-deep)", background: "var(--accent-soft)", border: "1px solid var(--line)", borderRadius: 6, padding: "3px 8px", fontWeight: 600 }}
              >
                <Icon name="pencil" size={12} />
                editar
              </button>
            </div>
          ) : (
            <div className="row gap2" style={{ marginTop: 8 }}>
              <input
                className="field"
                style={{ maxWidth: 360 }}
                autoFocus
                value={labelNovo}
                onChange={(e) => setLabelNovo(e.target.value)}
                list={`labs-${evento.id}`}
              />
              <datalist id={`labs-${evento.id}`}>
                {labels.map((l) => (
                  <option key={l} value={l} />
                ))}
              </datalist>
              <Btn
                variant="secondary"
                size="sm"
                onClick={() => {
                  setEditandoLabel(false);
                  setLabelNovo(evento.comportamento_label);
                }}
              >
                cancelar
              </Btn>
            </div>
          )}

          <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 8 }}>
            {evento.descricao_bruta}
          </p>
        </div>

        <div className="col gap3">
          <PanelHead titulo="O que o Prism propõe está certo?" />
          <div className="col gap2">
            <button
              onClick={() => validar.mutate({ acao: "confirmar" })}
              disabled={validar.isPending}
              className="btn btn-ok btn-lg"
              style={{ justifyContent: "flex-start" }}
            >
              <Icon name="check" size={18} />
              Sim, está certo
              <span className="grow" />
              <span style={{ fontSize: 11, color: "var(--muted)" }}>confirmar</span>
            </button>
            <button
              onClick={() => {
                const lbl = labelNovo.trim();
                if (!lbl) return toast("Edite o label primeiro.", { icon: "alert-triangle" });
                validar.mutate({ acao: "corrigir", label: lbl });
              }}
              disabled={validar.isPending || !labelNovo.trim()}
              className="btn btn-secondary btn-lg"
              style={{ justifyContent: "flex-start" }}
            >
              <Icon name="pencil" size={16} />
              {editandoLabel && labelNovo !== evento.comportamento_label
                ? `Corrigir para "${labelNovo}"`
                : "Corrigir label"}
              <span className="grow" />
              <span style={{ fontSize: 11, color: "var(--muted)" }}>corrigir</span>
            </button>
            <button
              onClick={() => validar.mutate({ acao: "descartar" })}
              disabled={validar.isPending}
              className="btn btn-danger btn-lg"
              style={{ justifyContent: "flex-start" }}
            >
              <Icon name="x" size={18} />
              Falso alarme
              <span className="grow" />
              <span style={{ fontSize: 11, color: "var(--muted)" }}>descartar</span>
            </button>
          </div>
          <p style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 4 }}>
            Cada validação reduz seu trabalho futuro: depois de 2 confirmações iguais, o Prism passa a confirmar sozinho.
          </p>
        </div>
      </div>
    </Card>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Conversa guiada com o Prism (perguntas proativas)
// ════════════════════════════════════════════════════════════════════════
function PerguntasConversa({
  perguntas,
  processoId,
  onResolvido,
}: {
  perguntas: PerguntaProcesso[];
  processoId: string;
  onResolvido: () => void;
}) {
  return (
    <Card style={{ padding: 20, background: "linear-gradient(135deg, var(--accent-soft), #fff 70%)", border: "1px solid var(--p-200)" }}>
      <div className="row gap2" style={{ marginBottom: 12 }}>
        <PrismAvatar size={28} ring />
        <h3 className="font-display" style={{ fontSize: 16, fontWeight: 700 }}>
          O Prism quer entender melhor
        </h3>
        <Help text="Respostas viram contexto de domínio nos próximos prompts. Responder é opcional, mas torna análises futuras muito mais precisas." />
        <span className="grow" />
        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{perguntas.length} pergunta(s)</span>
      </div>
      <div className="col gap3">
        {perguntas.map((q) => (
          <PerguntaItem key={q.id} q={q} processoId={processoId} onResolvido={onResolvido} />
        ))}
      </div>
    </Card>
  );
}

function PerguntaItem({
  q,
  onResolvido,
}: {
  q: PerguntaProcesso;
  processoId: string;
  onResolvido: () => void;
}) {
  const [aberto, setAberto] = useState(false);
  const [texto, setTexto] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const responder = useMutation({
    mutationFn: () => api.perguntas.responder(q.id, texto.trim()),
    onSuccess: () => {
      setFeedback("Anotado · virou conhecimento do Prism.");
      toast("Resposta virou conhecimento do Prism.", { icon: "sparkles" });
      window.setTimeout(onResolvido, 700);
    },
  });
  const dispensar = useMutation({
    mutationFn: () => api.perguntas.dispensar(q.id),
    onSuccess: () => {
      setFeedback("Pergunta dispensada.");
      window.setTimeout(onResolvido, 500);
    },
  });
  if (feedback)
    return (
      <div style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 12, padding: 12, fontSize: 13, color: "var(--ok)" }}>
        ✓ {feedback}
      </div>
    );

  function enviar(e?: FormEvent) {
    e?.preventDefault();
    if (!texto.trim()) return;
    responder.mutate();
  }
  const ocupado = responder.isPending || dispensar.isPending;

  return (
    <div style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 12, padding: 14 }}>
      <p style={{ fontSize: 13.5, color: "var(--ink)", fontWeight: 600 }}>{q.pergunta}</p>
      {q.comportamentos_relacionados && q.comportamentos_relacionados.length > 0 && (
        <div className="row gap1 wrap" style={{ marginTop: 6 }}>
          {q.comportamentos_relacionados.slice(0, 6).map((c) => (
            <code key={c} style={{ fontSize: 10.5, background: "var(--accent-soft)", color: "var(--accent-deep)", padding: "1px 6px", borderRadius: 5, border: "1px solid var(--p-100)" }}>
              {c}
            </code>
          ))}
        </div>
      )}
      {q.motivo && (
        <button
          onClick={() => setAberto((v) => !v)}
          style={{ marginTop: 8, background: 0, border: 0, color: "var(--muted)", fontSize: 11.5 }}
        >
          {aberto ? "ocultar" : "por que essa pergunta?"}
        </button>
      )}
      {aberto && q.motivo && (
        <p style={{ marginTop: 4, fontSize: 12, color: "var(--muted)", fontStyle: "italic" }}>
          {q.motivo}
        </p>
      )}
      <form onSubmit={enviar} className="row gap2" style={{ marginTop: 10, alignItems: "stretch" }}>
        <textarea
          className="field"
          rows={2}
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          placeholder="Responda em 1-2 frases…"
          style={{ flex: 1, resize: "vertical" }}
        />
        <div className="col gap2" style={{ width: 120 }}>
          <Btn type="submit" disabled={ocupado || !texto.trim()} size="sm">
            Responder
          </Btn>
          <Btn type="button" variant="ghost" size="sm" disabled={ocupado} onClick={() => dispensar.mutate()}>
            Dispensar
          </Btn>
        </div>
      </form>
    </div>
  );
}
