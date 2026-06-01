import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, formatSeg } from "../lib/api";
import { Badge, Button, Card, EmptyState, HelpBox, Input, Spinner } from "../components/UI";
import type {
  AcaoEvento,
  EventosTabelaParams,
  EventoTabela,
  StatusEfetivo,
} from "../lib/types";

const STATUS_OPCOES: { v: "todos" | StatusEfetivo; label: string }[] = [
  { v: "todos", label: "Todos" },
  { v: "pendente", label: "Pendentes" },
  { v: "confirmado", label: "Confirmados" },
  { v: "corrigido", label: "Corrigidos" },
  { v: "descartado", label: "Descartados" },
  { v: "auto", label: "Auto-validados" },
];

const SORT_OPCOES: { v: NonNullable<EventosTabelaParams["sort"]>; label: string }[] = [
  { v: "criado_em", label: "Data de detecção" },
  { v: "tempo_inicio_s", label: "Tempo no vídeo" },
  { v: "duracao_s", label: "Duração" },
  { v: "comportamento_label", label: "Comportamento" },
  { v: "confianca", label: "Confiança" },
];

function statusBadge(s: StatusEfetivo) {
  const map: Record<StatusEfetivo, { tone: "neutral" | "success" | "info" | "alta" | "warning"; texto: string }> = {
    pendente: { tone: "neutral", texto: "Pendente" },
    confirmado: { tone: "success", texto: "Confirmado" },
    corrigido: { tone: "info", texto: "Corrigido" },
    descartado: { tone: "alta", texto: "Descartado" },
    auto: { tone: "warning", texto: "Auto-validado" },
  };
  const { tone, texto } = map[s];
  return <Badge tone={tone}>{texto}</Badge>;
}

export default function Eventos() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [statusF, setStatusF] = useState<"todos" | StatusEfetivo>("todos");
  const [labelF, setLabelF] = useState("");
  const [videoF, setVideoF] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  const [busca, setBusca] = useState("");
  const [sort, setSort] = useState<NonNullable<EventosTabelaParams["sort"]>>("criado_em");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [selecionados, setSelecionados] = useState<Set<string>>(new Set());
  const [expandido, setExpandido] = useState<string | null>(null);

  // debounce da busca
  useEffect(() => {
    const t = setTimeout(() => setBusca(buscaInput.trim()), 400);
    return () => clearTimeout(t);
  }, [buscaInput]);

  // qualquer mudança de filtro volta pra página 1
  useEffect(() => {
    setPage(1);
    setSelecionados(new Set());
  }, [statusF, labelF, videoF, busca, sort, order, pageSize]);

  const params: EventosTabelaParams = {
    page,
    page_size: pageSize,
    status: statusF,
    label: labelF || undefined,
    video_id: videoF || undefined,
    busca: busca || undefined,
    sort,
    order,
  };

  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ["eventos-tabela", id, params],
    queryFn: () => api.eventos.tabela(id!, params),
    enabled: !!id,
  });

  // opções de filtro (labels e vídeos) vêm do dashboard já cacheado
  const dashboard = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api.processos.dashboard(id!),
    enabled: !!id,
    staleTime: 30_000,
  });
  const labelsCanonicos = useMemo(
    () =>
      (dashboard.data?.snapshot.distribuicao_comportamentos || [])
        .map((d) => d.comportamento)
        .sort(),
    [dashboard.data]
  );
  const videos = dashboard.data?.videos || [];

  function invalidarTudo() {
    qc.invalidateQueries({ queryKey: ["eventos-tabela", id] });
    qc.invalidateQueries({ queryKey: ["dashboard", id] });
    qc.invalidateQueries({ queryKey: ["eventos-pendentes", id] });
  }

  const lote = useMutation({
    mutationFn: ({ acao, label }: { acao: AcaoEvento; label?: string }) =>
      api.eventos.lote(Array.from(selecionados), acao, label),
    onSuccess: () => {
      setSelecionados(new Set());
      invalidarTudo();
    },
  });

  const itens = data?.itens || [];
  const total = data?.total || 0;
  const totalPaginas = Math.max(1, Math.ceil(total / pageSize));

  function toggleSel(eid: string) {
    setSelecionados((prev) => {
      const n = new Set(prev);
      if (n.has(eid)) n.delete(eid);
      else n.add(eid);
      return n;
    });
  }
  function toggleTodosPagina() {
    setSelecionados((prev) => {
      const todosNaPagina = itens.every((e) => prev.has(e.id));
      const n = new Set(prev);
      if (todosNaPagina) itens.forEach((e) => n.delete(e.id));
      else itens.forEach((e) => n.add(e.id));
      return n;
    });
  }

  function acaoLote(acao: AcaoEvento) {
    if (selecionados.size === 0) return;
    let label: string | undefined;
    if (acao === "corrigir") {
      label = window.prompt(
        `Reclassificar ${selecionados.size} evento(s) para qual comportamento?\n(use um label existente quando possível, ex.: ${labelsCanonicos[0] || "operar_computador"})`
      )?.trim();
      if (!label) return;
    } else {
      const verbo = { confirmar: "confirmar", descartar: "descartar", reabrir: "reabrir" }[acao];
      if (!window.confirm(`Tem certeza que deseja ${verbo} ${selecionados.size} evento(s)?`)) return;
    }
    lote.mutate({ acao, label });
  }

  return (
    <div>
      <div className="mb-4">
        <HelpBox title="Planilha mestre de eventos">
          Aqui está <b>tudo</b> que o sistema já detectou neste processo — inclusive o
          que já foi validado. Você pode <b>consultar, reclassificar, descartar ou
          reabrir</b> qualquer evento a qualquer momento. Toda correção feita aqui{" "}
          <b>re-treina o sistema</b> do mesmo jeito que na tela de Validação.
        </HelpBox>
      </div>

      {/* Controles */}
      <Card className="p-4 mb-4">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Buscar descrição</label>
            <Input
              value={buscaInput}
              onChange={(e) => setBuscaInput(e.target.value)}
              placeholder="ex.: caixa, computador..."
            />
          </div>
          <SelectFiltro
            label="Status"
            value={statusF}
            onChange={(v) => setStatusF(v as "todos" | StatusEfetivo)}
            options={STATUS_OPCOES.map((o) => ({ value: o.v, label: o.label }))}
          />
          <SelectFiltro
            label="Comportamento"
            value={labelF}
            onChange={setLabelF}
            options={[
              { value: "", label: "Todos" },
              ...labelsCanonicos.map((l) => ({ value: l, label: l })),
            ]}
          />
          <SelectFiltro
            label="Vídeo"
            value={videoF}
            onChange={setVideoF}
            options={[
              { value: "", label: "Todos" },
              ...videos.map((v) => ({ value: v.id, label: v.nome })),
            ]}
          />
        </div>
        <div className="flex items-center justify-between gap-3 flex-wrap mt-3">
          <div className="flex items-end gap-3 flex-wrap">
            <SelectFiltro
              label="Ordenar por"
              value={sort}
              onChange={(v) => setSort(v as NonNullable<EventosTabelaParams["sort"]>)}
              options={SORT_OPCOES.map((o) => ({ value: o.v, label: o.label }))}
            />
            <button
              onClick={() => setOrder((o) => (o === "asc" ? "desc" : "asc"))}
              className="px-3 py-2 rounded-lg border border-slate-300 text-sm text-slate-600 hover:bg-slate-50"
              title="Inverter ordem"
            >
              {order === "asc" ? "↑ crescente" : "↓ decrescente"}
            </button>
            <SelectFiltro
              label="Por página"
              value={String(pageSize)}
              onChange={(v) => setPageSize(Number(v))}
              options={[25, 50, 100, 200].map((n) => ({ value: String(n), label: String(n) }))}
            />
          </div>
          <div className="text-sm text-slate-500 flex items-center gap-2">
            {isFetching && <Spinner className="h-4 w-4" />}
            <span>
              {total.toLocaleString("pt-BR")} evento(s) · página {page} de {totalPaginas}
            </span>
          </div>
        </div>
      </Card>

      {/* Barra de seleção em lote */}
      {selecionados.size > 0 && (
        <Card className="p-3 mb-4 bg-kv-purple-50 border-kv-purple-200 sticky top-16 z-10">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <span className="text-sm font-medium text-kv-purple-dark">
              {selecionados.size} selecionado(s)
            </span>
            <div className="flex gap-2 flex-wrap">
              <Button variant="success" onClick={() => acaoLote("confirmar")} disabled={lote.isPending}>
                ✓ Confirmar
              </Button>
              <Button variant="secondary" onClick={() => acaoLote("corrigir")} disabled={lote.isPending}>
                ✎ Corrigir
              </Button>
              <Button variant="danger" onClick={() => acaoLote("descartar")} disabled={lote.isPending}>
                ✕ Descartar
              </Button>
              <Button variant="ghost" onClick={() => acaoLote("reabrir")} disabled={lote.isPending}>
                ↺ Reabrir
              </Button>
              <Button variant="ghost" onClick={() => setSelecionados(new Set())}>
                Limpar seleção
              </Button>
            </div>
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

      {data && itens.length === 0 && (
        <Card className="p-2">
          <EmptyState
            title="Nenhum evento encontrado"
            description={
              total === 0 && statusF === "todos" && !busca && !labelF && !videoF
                ? "Este processo ainda não tem eventos. Envie um vídeo para começar."
                : "Nenhum evento corresponde aos filtros selecionados."
            }
          />
        </Card>
      )}

      {itens.length > 0 && (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 text-slate-500 text-xs uppercase tracking-wide">
                  <th className="p-3 w-8">
                    <input
                      type="checkbox"
                      checked={itens.length > 0 && itens.every((e) => selecionados.has(e.id))}
                      onChange={toggleTodosPagina}
                      className="accent-kv-purple"
                    />
                  </th>
                  <th className="p-3 text-left">Comportamento</th>
                  <th className="p-3 text-left">Descrição</th>
                  <th className="p-3 text-left">Vídeo · tempo</th>
                  <th className="p-3 text-center">Pessoa</th>
                  <th className="p-3 text-center">Conf.</th>
                  <th className="p-3 text-center">Status</th>
                  <th className="p-3 w-8"></th>
                </tr>
              </thead>
              <tbody>
                {itens.map((ev) => (
                  <LinhaEvento
                    key={ev.id}
                    ev={ev}
                    selecionado={selecionados.has(ev.id)}
                    onToggleSel={() => toggleSel(ev.id)}
                    expandido={expandido === ev.id}
                    onToggleExpandir={() =>
                      setExpandido((cur) => (cur === ev.id ? null : ev.id))
                    }
                    labelsCanonicos={labelsCanonicos}
                    onResolved={invalidarTudo}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Paginação */}
      {totalPaginas > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <Button variant="ghost" disabled={page <= 1} onClick={() => setPage(1)}>
            « Primeira
          </Button>
          <Button variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            ‹ Anterior
          </Button>
          <span className="text-sm text-slate-600 px-2">
            {page} / {totalPaginas}
          </span>
          <Button variant="ghost" disabled={page >= totalPaginas} onClick={() => setPage((p) => p + 1)}>
            Próxima ›
          </Button>
          <Button variant="ghost" disabled={page >= totalPaginas} onClick={() => setPage(totalPaginas)}>
            Última »
          </Button>
        </div>
      )}
    </div>
  );
}

function SelectFiltro({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-600 mb-1">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-kv-purple focus:ring-2 focus:ring-kv-purple/20 outline-none bg-white"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function LinhaEvento({
  ev,
  selecionado,
  onToggleSel,
  expandido,
  onToggleExpandir,
  labelsCanonicos,
  onResolved,
}: {
  ev: EventoTabela;
  selecionado: boolean;
  onToggleSel: () => void;
  expandido: boolean;
  onToggleExpandir: () => void;
  labelsCanonicos: string[];
  onResolved: () => void;
}) {
  const [labelEdit, setLabelEdit] = useState(ev.label_efetivo);
  const [msg, setMsg] = useState<string | null>(null);

  const acao = useMutation({
    mutationFn: ({ a, label }: { a: AcaoEvento; label?: string }) =>
      a === "reabrir" ? api.eventos.reabrir(ev.id) : api.eventos.validar(ev.id, a, label),
    onSuccess: (_d, vars) => {
      const txt: Record<AcaoEvento, string> = {
        confirmar: "confirmado",
        corrigir: `corrigido para "${vars.label}"`,
        descartar: "descartado",
        reabrir: "reaberto (voltou para a fila)",
      };
      setMsg(`✓ ${txt[vars.a]}`);
      setTimeout(onResolved, 700);
    },
  });

  const houveCorrecao = ev.label_corrigido && ev.label_corrigido !== ev.comportamento_label;

  return (
    <>
      <tr className="border-t border-slate-100 hover:bg-slate-50/60">
        <td className="p-3 align-top">
          <input
            type="checkbox"
            checked={selecionado}
            onChange={onToggleSel}
            className="accent-kv-purple"
          />
        </td>
        <td className="p-3 align-top">
          {houveCorrecao ? (
            <span className="text-xs">
              <span className="line-through text-slate-400">{ev.comportamento_label}</span>
              <span className="text-slate-400"> → </span>
              <code className="bg-kv-purple-50 text-kv-purple-dark px-1.5 py-0.5 rounded">
                {ev.label_corrigido}
              </code>
            </span>
          ) : (
            <code className="bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded text-xs">
              {ev.comportamento_label}
            </code>
          )}
        </td>
        <td className="p-3 align-top max-w-[14rem]">
          <span className="text-slate-600 text-xs line-clamp-2" title={ev.descricao_bruta}>
            {ev.descricao_bruta || "—"}
          </span>
        </td>
        <td className="p-3 align-top whitespace-nowrap text-xs text-slate-500">
          <div className="truncate max-w-[10rem]" title={ev.video_nome}>{ev.video_nome}</div>
          <div>
            {ev.tempo_inicio_s.toFixed(0)}→{ev.tempo_fim_s.toFixed(0)}s ·{" "}
            {formatSeg(ev.duracao_s)}
          </div>
        </td>
        <td className="p-3 align-top text-center text-xs text-slate-500">
          {String(ev.pessoa_track_id).padStart(3, "0")}
        </td>
        <td className="p-3 align-top text-center text-xs text-slate-500">
          {(ev.confianca * 100).toFixed(0)}%
        </td>
        <td className="p-3 align-top text-center">{statusBadge(ev.status_efetivo)}</td>
        <td className="p-3 align-top text-center">
          <button
            onClick={onToggleExpandir}
            className="text-slate-400 hover:text-kv-purple-dark"
            title="Inspecionar e editar"
          >
            {expandido ? "▲" : "▸"}
          </button>
        </td>
      </tr>
      {expandido && (
        <tr className="bg-slate-50/80">
          <td colSpan={8} className="p-4">
            <PainelEdicao
              ev={ev}
              labelEdit={labelEdit}
              setLabelEdit={setLabelEdit}
              labelsCanonicos={labelsCanonicos}
              onAcao={(a, label) => acao.mutate({ a, label })}
              pending={acao.isPending}
              msg={msg}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function PainelEdicao({
  ev,
  labelEdit,
  setLabelEdit,
  labelsCanonicos,
  onAcao,
  pending,
  msg,
}: {
  ev: EventoTabela;
  labelEdit: string;
  setLabelEdit: (v: string) => void;
  labelsCanonicos: string[];
  onAcao: (a: AcaoEvento, label?: string) => void;
  pending: boolean;
  msg: string | null;
}) {
  const frames = useQuery({
    queryKey: ["frames", ev.id],
    queryFn: () => api.eventos.frames(ev.id),
    staleTime: 5 * 60 * 1000,
  });
  const listaId = `labels-${ev.id}`;

  return (
    <div className="space-y-3">
      <div className="flex gap-2 overflow-x-auto">
        {frames.isLoading && (
          <div className="h-40 w-56 bg-slate-100 rounded-lg flex items-center justify-center">
            <Spinner />
          </div>
        )}
        {frames.data?.frames.map((src, i) => (
          <img
            key={i}
            src={src}
            alt={`quadro ${i + 1}`}
            className="h-40 rounded-lg border border-slate-200"
          />
        ))}
        {frames.error && (
          <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3 max-w-md">
            {(frames.error as Error).message}
          </div>
        )}
      </div>

      {msg ? (
        <div className="text-sm text-emerald-700 font-medium">{msg}</div>
      ) : (
        <div className="flex items-end gap-2 flex-wrap">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              Comportamento (use um existente quando possível)
            </label>
            <input
              list={listaId}
              value={labelEdit}
              onChange={(e) => setLabelEdit(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-kv-purple focus:ring-2 focus:ring-kv-purple/20 outline-none w-72"
            />
            <datalist id={listaId}>
              {labelsCanonicos.map((l) => (
                <option key={l} value={l} />
              ))}
            </datalist>
          </div>
          <Button variant="success" onClick={() => onAcao("confirmar")} disabled={pending}>
            ✓ Confirmar
          </Button>
          <Button
            variant="secondary"
            onClick={() => onAcao("corrigir", labelEdit.trim())}
            disabled={pending || !labelEdit.trim()}
          >
            ✎ Corrigir
          </Button>
          <Button variant="danger" onClick={() => onAcao("descartar")} disabled={pending}>
            ✕ Descartar
          </Button>
          {ev.status_efetivo !== "pendente" && (
            <Button variant="ghost" onClick={() => onAcao("reabrir")} disabled={pending}>
              ↺ Reabrir
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
