import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, formatSeg } from "../lib/api";
import { Badge, Button, Card, EmptyState, HelpBox, Input, Spinner } from "../components/UI";
import type { EventoPendente, PerguntaProcesso } from "../lib/types";

export default function Validacao() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const perguntas = useQuery({
    queryKey: ["perguntas-pendentes", id],
    queryFn: () => api.perguntas.listar(id!, "pendente"),
    enabled: !!id,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["eventos-pendentes", id],
    queryFn: () => api.processos.eventosPendentes(id!),
    enabled: !!id,
  });

  const dashboard = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api.processos.dashboard(id!),
    enabled: !!id,
  });

  const [filtroLabel, setFiltroLabel] = useState<string>("(todos)");
  const labels = useMemo(() => {
    if (!data) return [];
    return Array.from(new Set(data.map((e) => e.comportamento_label))).sort();
  }, [data]);

  const filtrados = useMemo(
    () =>
      (data || []).filter(
        (e) => filtroLabel === "(todos)" || e.comportamento_label === filtroLabel
      ),
    [data, filtroLabel]
  );

  return (
    <div>
      {perguntas.data && perguntas.data.length > 0 && (
        <PerguntasSecao
          perguntas={perguntas.data}
          onResolved={() => {
            qc.invalidateQueries({ queryKey: ["perguntas-pendentes", id] });
            qc.invalidateQueries({ queryKey: ["dashboard", id] });
          }}
        />
      )}

      <div className="mb-4">
        <HelpBox title="Por que validar?">
          A IA descobre os comportamentos automaticamente, mas pode errar nos
          primeiros vídeos. Quando você <b>confirma</b>, ela aprende que o label
          está correto. Quando você <b>corrige</b>, ela aprende o mapeamento
          certo. Quando você <b>descarta</b>, ela aprende que aquilo é falso
          positivo. Depois de 2 confirmações do mesmo label, o sistema passa a
          confirmar sozinho os eventos seguintes — sua carga de trabalho cai.
        </HelpBox>
      </div>

      {dashboard.data && (
        <Card className="p-4 mb-6 bg-gradient-to-r from-emerald-50 to-emerald-100/60 border-emerald-200">
          <div className="flex items-baseline gap-2 text-sm text-emerald-900">
            <span className="font-semibold">
              {dashboard.data.eventos_pendentes} eventos aguardando você
            </span>
            <span className="text-emerald-700">·</span>
            <span className="text-emerald-700">
              o sistema já confirmou sozinho{" "}
              <b>
                {dashboard.data.snapshot.eventos_considerados -
                  dashboard.data.eventos_pendentes}
              </b>{" "}
              de {dashboard.data.snapshot.eventos_considerados} eventos.
            </span>
          </div>
        </Card>
      )}

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

      {data && data.length === 0 && (
        <Card className="p-2">
          <EmptyState
            title="Nada a validar manualmente"
            description="Todos os eventos detectados já foram confirmados pelo conhecimento acumulado, ou ainda não há eventos. Envie mais vídeos para continuar treinando o sistema."
          />
        </Card>
      )}

      {data && data.length > 0 && (
        <>
          <div className="flex items-center gap-4 mb-4 flex-wrap">
            <select
              value={filtroLabel}
              onChange={(e) => setFiltroLabel(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-kv-purple focus:ring-2 focus:ring-kv-purple/20 outline-none"
            >
              <option>(todos)</option>
              {labels.map((l) => (
                <option key={l}>{l}</option>
              ))}
            </select>
            <span className="text-sm text-slate-500">
              {filtrados.length} evento(s)
            </span>
          </div>

          <div className="space-y-4">
            {filtrados.slice(0, 50).map((e) => (
              <EventoCard key={e.id} evento={e} onResolved={() => qc.invalidateQueries({ queryKey: ["eventos-pendentes", id] })} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function EventoCard({
  evento,
  onResolved,
}: {
  evento: EventoPendente;
  onResolved: () => void;
}) {
  const [labelEdit, setLabelEdit] = useState(evento.comportamento_label);
  const [resolved, setResolved] = useState<string | null>(null);
  const frames = useQuery({
    queryKey: ["frames", evento.id],
    queryFn: () => api.eventos.frames(evento.id),
    staleTime: 5 * 60 * 1000,
  });

  const mut = useMutation({
    mutationFn: (args: { acao: "confirmar" | "corrigir" | "descartar"; label?: string }) =>
      api.eventos.validar(evento.id, args.acao, args.label),
    onSuccess: (_d, vars) => {
      setResolved(
        vars.acao === "descartar"
          ? "descartado"
          : vars.label && vars.label !== evento.comportamento_label
            ? `corrigido para "${vars.label}"`
            : "confirmado"
      );
      setTimeout(onResolved, 800);
    },
  });

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
        <div className="text-xs text-slate-500 uppercase tracking-wide">
          Pessoa-{String(evento.pessoa_track_id).padStart(3, "0")} ·{" "}
          {evento.tempo_inicio_s.toFixed(1)}s → {evento.tempo_fim_s.toFixed(1)}s ·{" "}
          {formatSeg(evento.tempo_fim_s - evento.tempo_inicio_s)}
        </div>
        <Badge tone="info">{(evento.confianca * 100).toFixed(0)}% confiança</Badge>
      </div>

      <div className="mb-3">
        <div className="text-sm">
          Label proposto:{" "}
          <code className="bg-slate-100 text-kv-purple-dark px-1.5 py-0.5 rounded">
            {evento.comportamento_label}
          </code>
        </div>
        <div className="text-xs text-slate-500 mt-1">{evento.descricao_bruta}</div>
      </div>

      <div className="flex gap-2 mb-4 overflow-x-auto">
        {frames.isLoading && (
          <div className="h-32 w-48 bg-slate-100 rounded-lg flex items-center justify-center">
            <Spinner />
          </div>
        )}
        {frames.data?.frames.map((src, i) => (
          // eslint-disable-next-line jsx-a11y/img-redundant-alt
          <img
            key={i}
            src={src}
            alt={`frame ${i}`}
            className="h-44 rounded-lg border border-slate-200"
          />
        ))}
        {frames.error && (
          <div className="text-xs text-red-600">Falha ao carregar frames.</div>
        )}
      </div>

      {resolved ? (
        <div className="text-sm text-emerald-700 font-medium">✓ {resolved}</div>
      ) : (
        <div className="flex items-center gap-2 flex-wrap">
          <Input
            value={labelEdit}
            onChange={(e) => setLabelEdit(e.target.value)}
            className="max-w-xs"
          />
          <Button
            variant="success"
            onClick={() => mut.mutate({ acao: "confirmar" })}
            disabled={mut.isPending}
          >
            ✓ Confirmar
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              mut.mutate({ acao: "corrigir", label: labelEdit.trim() })
            }
            disabled={mut.isPending || !labelEdit.trim()}
          >
            ✎ Corrigir
          </Button>
          <Button
            variant="danger"
            onClick={() => mut.mutate({ acao: "descartar" })}
            disabled={mut.isPending}
          >
            ✕ Descartar
          </Button>
        </div>
      )}
    </Card>
  );
}


function PerguntasSecao({
  perguntas,
  onResolved,
}: {
  perguntas: PerguntaProcesso[];
  onResolved: () => void;
}) {
  return (
    <Card className="mb-6 p-5 bg-gradient-to-br from-kv-purple-50 to-kv-indigo-bg border-kv-purple-200">
      <div className="flex items-start gap-3 mb-4">
        <div className="h-9 w-9 rounded-full bg-kv-purple text-white flex items-center justify-center font-bold flex-shrink-0">
          ?
        </div>
        <div>
          <h2 className="font-semibold text-slate-900">
            O sistema tem {perguntas.length === 1 ? "uma pergunta" : `${perguntas.length} perguntas`}
          </h2>
          <p className="text-sm text-slate-600 mt-1 max-w-2xl">
            Conforme analisa seus vídeos, a IA percebe pontos que ainda não
            entende bem. Responder é <b>opcional</b>, mas <b>cada resposta vira
            verdade do domínio</b> e é injetada nos próximos prompts — as
            análises seguintes ficam mais precisas e suas sugestões mais
            assertivas.
          </p>
        </div>
      </div>
      <div className="space-y-3">
        {perguntas.map((p) => (
          <PerguntaCard key={p.id} pergunta={p} onResolved={onResolved} />
        ))}
      </div>
    </Card>
  );
}

function PerguntaCard({
  pergunta,
  onResolved,
}: {
  pergunta: PerguntaProcesso;
  onResolved: () => void;
}) {
  const [resposta, setResposta] = useState("");
  const [mostrarMotivo, setMostrarMotivo] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const responder = useMutation({
    mutationFn: () => api.perguntas.responder(pergunta.id, resposta.trim()),
    onSuccess: () => {
      setFeedback("Obrigado! Isso ajuda o sistema a aprender seu processo.");
      setTimeout(onResolved, 1100);
    },
  });

  const dispensar = useMutation({
    mutationFn: () => api.perguntas.dispensar(pergunta.id),
    onSuccess: () => {
      setFeedback("Pergunta dispensada — não vai aparecer de novo.");
      setTimeout(onResolved, 800);
    },
  });

  if (feedback) {
    return (
      <div className="bg-white rounded-xl p-4 border border-emerald-200 text-sm text-emerald-700">
        ✓ {feedback}
      </div>
    );
  }

  const ocupado = responder.isPending || dispensar.isPending;

  return (
    <div className="bg-white rounded-xl p-4 border border-slate-200">
      <p className="text-sm text-slate-900 font-medium">{pergunta.pergunta}</p>
      {pergunta.comportamentos_relacionados && pergunta.comportamentos_relacionados.length > 0 && (
        <div className="flex gap-1 flex-wrap mt-2">
          {pergunta.comportamentos_relacionados.slice(0, 6).map((c) => (
            <code
              key={c}
              className="text-[10px] bg-kv-purple-50 text-kv-purple-dark px-1.5 py-0.5 rounded border border-kv-purple-200"
            >
              {c}
            </code>
          ))}
        </div>
      )}
      {pergunta.motivo && (
        <button
          type="button"
          onClick={() => setMostrarMotivo((v) => !v)}
          className="block text-xs text-slate-500 hover:text-kv-purple-dark mt-2"
        >
          {mostrarMotivo ? "ocultar" : "por que essa pergunta?"}
        </button>
      )}
      {mostrarMotivo && pergunta.motivo && (
        <p className="text-xs text-slate-500 mt-1 italic">{pergunta.motivo}</p>
      )}
      <div className="mt-3 flex flex-col sm:flex-row gap-2">
        <textarea
          value={resposta}
          onChange={(e) => setResposta(e.target.value)}
          placeholder="Responda em 1-2 frases..."
          rows={2}
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-kv-purple focus:ring-2 focus:ring-kv-purple/20 outline-none"
        />
        <div className="flex sm:flex-col gap-2">
          <Button
            onClick={() => responder.mutate()}
            disabled={ocupado || !resposta.trim()}
          >
            Responder
          </Button>
          <Button variant="ghost" onClick={() => dispensar.mutate()} disabled={ocupado}>
            Dispensar
          </Button>
        </div>
      </div>
    </div>
  );
}
