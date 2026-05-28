import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, formatSeg } from "../lib/api";
import { Badge, Button, Card, EmptyState, HelpBox, Input, Spinner } from "../components/UI";
import type { EventoPendente } from "../lib/types";

export default function Validacao() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

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
