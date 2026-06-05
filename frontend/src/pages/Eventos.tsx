import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import {
  Badge,
  Btn,
  Card,
  Empty,
  Icon,
  Spinner,
  fmtSeg,
  toast,
} from "../components/UIKit";
import type {
  AcaoEvento,
  EventosTabelaParams,
  EventoTabela,
  StatusEfetivo,
} from "../lib/types";

const STATUS_META: Record<StatusEfetivo, { tone: "neutral" | "ok" | "info" | "warn" | "high"; label: string; icon: string }> = {
  pendente: { tone: "neutral", label: "Pendente", icon: "clock" },
  confirmado: { tone: "ok", label: "Confirmado", icon: "check" },
  corrigido: { tone: "info", label: "Corrigido", icon: "pencil" },
  descartado: { tone: "high", label: "Descartado", icon: "x" },
  auto: { tone: "warn", label: "Auto-validado", icon: "zap" },
};

const STATUS_OPCOES: { v: "todos" | StatusEfetivo; label: string }[] = [
  { v: "todos", label: "Todos" },
  { v: "pendente", label: "Pendentes" },
  { v: "confirmado", label: "Confirmados" },
  { v: "corrigido", label: "Corrigidos" },
  { v: "descartado", label: "Descartados" },
  { v: "auto", label: "Auto-validados" },
];

export default function Eventos() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [status, setStatus] = useState<"todos" | StatusEfetivo>("todos");
  const [label, setLabel] = useState("");
  const [videoF, setVideoF] = useState("");
  const [buscaInput, setBuscaInput] = useState("");
  const [busca, setBusca] = useState("");
  const [sort, setSort] = useState<NonNullable<EventosTabelaParams["sort"]>>("criado_em");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [expand, setExpand] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setBusca(buscaInput.trim()), 400);
    return () => clearTimeout(t);
  }, [buscaInput]);
  useEffect(() => {
    setPage(1);
    setSel(new Set());
  }, [status, label, videoF, busca, sort, order, pageSize]);

  const params: EventosTabelaParams = {
    page,
    page_size: pageSize,
    status,
    label: label || undefined,
    video_id: videoF || undefined,
    busca: busca || undefined,
    sort,
    order,
  };
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["eventos-tabela", id, params],
    queryFn: () => api.eventos.tabela(id!, params),
    enabled: !!id,
  });
  const dash = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api.processos.dashboard(id!),
    enabled: !!id,
    staleTime: 30_000,
  });
  const labels = useMemo(
    () => (dash.data?.snapshot.distribuicao_comportamentos || []).map((d) => d.comportamento).sort(),
    [dash.data]
  );
  const videos = dash.data?.videos || [];

  function invalidar() {
    qc.invalidateQueries({ queryKey: ["eventos-tabela", id] });
    qc.invalidateQueries({ queryKey: ["dashboard", id] });
    qc.invalidateQueries({ queryKey: ["eventos-pendentes", id] });
  }

  const lote = useMutation({
    mutationFn: ({ acao, lbl }: { acao: AcaoEvento; lbl?: string }) =>
      api.eventos.lote(Array.from(sel), acao, lbl),
    onSuccess: (_d, vars) => {
      setSel(new Set());
      invalidar();
      toast(`${vars.acao} aplicada em lote.`, { icon: "check", color: "#3EE6AE" });
    },
  });

  function acaoLote(acao: AcaoEvento) {
    if (sel.size === 0) return;
    let lbl: string | undefined;
    if (acao === "corrigir") {
      lbl = window
        .prompt(
          `Reclassificar ${sel.size} evento(s) para qual comportamento?\n(use um label existente quando possível)`
        )
        ?.trim();
      if (!lbl) return;
    } else {
      const verbo = { confirmar: "confirmar", descartar: "descartar", reabrir: "reabrir" }[acao];
      if (!window.confirm(`${verbo} ${sel.size} evento(s)?`)) return;
    }
    lote.mutate({ acao, lbl });
  }

  if (isLoading)
    return (
      <div className="center" style={{ padding: 60 }}>
        <Spinner size={26} />
      </div>
    );

  const itens = data?.itens || [];
  const total = data?.total || 0;
  const totalPag = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="col" style={{ gap: 14 }}>
      <ExplicacaoEventos />

      {/* Controles */}
      <Card style={{ padding: 14 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", gap: 10 }}>
          <Campo label="Buscar descrição" valor={buscaInput} onChange={setBuscaInput} placeholder="ex.: caixa…" />
          <Select label="Status" valor={status} onChange={(v) => setStatus(v as "todos" | StatusEfetivo)} opts={STATUS_OPCOES.map((o) => ({ value: o.v, label: o.label }))} />
          <Select label="Comportamento" valor={label} onChange={setLabel} opts={[{ value: "", label: "Todos" }, ...labels.map((l) => ({ value: l, label: l }))]} />
          <Select label="Vídeo" valor={videoF} onChange={setVideoF} opts={[{ value: "", label: "Todos" }, ...videos.map((v) => ({ value: v.id, label: v.nome }))]} />
        </div>
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", marginTop: 10 }}>
          <div className="row gap2">
            <Select label="Ordenar" valor={sort} onChange={(v) => setSort(v as NonNullable<EventosTabelaParams["sort"]>)} opts={[
              { value: "criado_em", label: "Data" },
              { value: "tempo_inicio_s", label: "Tempo no vídeo" },
              { value: "duracao_s", label: "Duração" },
              { value: "comportamento_label", label: "Comportamento" },
              { value: "confianca", label: "Confiança" },
            ]} compact />
            <button
              onClick={() => setOrder((o) => (o === "asc" ? "desc" : "asc"))}
              className="btn btn-secondary btn-sm"
              title="Inverter ordem"
            >
              <Icon name={order === "asc" ? "arrow-up" : "arrow-down"} size={14} />
              {order === "asc" ? "crescente" : "decrescente"}
            </button>
            <Select
              label="Por página"
              valor={String(pageSize)}
              onChange={(v) => setPageSize(Number(v))}
              opts={[25, 50, 100, 200].map((n) => ({ value: String(n), label: String(n) }))}
              compact
            />
          </div>
          <div className="row gap2" style={{ fontSize: 12.5, color: "var(--muted)" }}>
            {isFetching && <Spinner size={14} />}
            <span>
              {total.toLocaleString("pt-BR")} eventos · página {page} de {totalPag}
            </span>
          </div>
        </div>
      </Card>

      {sel.size > 0 && (
        <Card style={{ padding: 12, background: "var(--accent-soft)", border: "1px solid var(--p-200)", position: "sticky", top: 70, zIndex: 4 }}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent-deep)" }}>
              {sel.size} selecionado(s)
            </span>
            <div className="row gap2 wrap">
              <Btn size="sm" variant="ok" icon="check" onClick={() => acaoLote("confirmar")} disabled={lote.isPending}>
                Confirmar
              </Btn>
              <Btn size="sm" variant="secondary" icon="pencil" onClick={() => acaoLote("corrigir")} disabled={lote.isPending}>
                Corrigir
              </Btn>
              <Btn size="sm" variant="danger" icon="x" onClick={() => acaoLote("descartar")} disabled={lote.isPending}>
                Descartar
              </Btn>
              <Btn size="sm" variant="ghost" icon="rotate-ccw" onClick={() => acaoLote("reabrir")} disabled={lote.isPending}>
                Reabrir
              </Btn>
              <Btn size="sm" variant="ghost" onClick={() => setSel(new Set())}>
                Limpar
              </Btn>
            </div>
          </div>
        </Card>
      )}

      {itens.length === 0 ? (
        <Card style={{ padding: 6 }}>
          <Empty
            icon="table-2"
            title="Nenhum evento encontrado"
            desc={
              total === 0 && status === "todos" && !busca && !label && !videoF
                ? "Envie um vídeo para começar."
                : "Nenhum evento corresponde aos filtros."
            }
          />
        </Card>
      ) : (
        <Card style={{ overflow: "hidden" }}>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ background: "var(--soft)", color: "var(--muted)", textTransform: "uppercase", fontSize: 10.5, letterSpacing: ".07em" }}>
                  <Th style={{ width: 30 }}>
                    <input
                      type="checkbox"
                      checked={itens.length > 0 && itens.every((e) => sel.has(e.id))}
                      onChange={() =>
                        setSel((s) => {
                          const todos = itens.every((e) => s.has(e.id));
                          const n = new Set(s);
                          if (todos) itens.forEach((e) => n.delete(e.id));
                          else itens.forEach((e) => n.add(e.id));
                          return n;
                        })
                      }
                      style={{ accentColor: "var(--accent)" }}
                    />
                  </Th>
                  <Th>Comportamento</Th>
                  <Th>Descrição</Th>
                  <Th>Vídeo · tempo</Th>
                  <Th center>Pessoa</Th>
                  <Th center>Conf.</Th>
                  <Th center>Status</Th>
                  <Th style={{ width: 30 }} />
                </tr>
              </thead>
              <tbody>
                {itens.map((ev) => (
                  <Linha
                    key={ev.id}
                    ev={ev}
                    sel={sel.has(ev.id)}
                    onToggleSel={() => {
                      setSel((s) => {
                        const n = new Set(s);
                        n.has(ev.id) ? n.delete(ev.id) : n.add(ev.id);
                        return n;
                      });
                    }}
                    expandido={expand === ev.id}
                    onExpand={() => setExpand((e) => (e === ev.id ? null : ev.id))}
                    labels={labels}
                    onResolvido={invalidar}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {totalPag > 1 && (
        <div className="row gap2" style={{ justifyContent: "center", marginTop: 6 }}>
          <Btn size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(1)}>« Primeira</Btn>
          <Btn size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>‹</Btn>
          <span className="font-mono" style={{ fontSize: 13, color: "var(--text)", padding: "0 6px" }}>
            {page} / {totalPag}
          </span>
          <Btn size="sm" variant="ghost" disabled={page >= totalPag} onClick={() => setPage((p) => p + 1)}>›</Btn>
          <Btn size="sm" variant="ghost" disabled={page >= totalPag} onClick={() => setPage(totalPag)}>Última »</Btn>
        </div>
      )}
    </div>
  );
}

function Th({ children, center, style }: { children?: React.ReactNode; center?: boolean; style?: React.CSSProperties }) {
  return (
    <th
      style={{
        padding: "10px 12px",
        textAlign: center ? "center" : "left",
        fontWeight: 700,
        ...style,
      }}
    >
      {children}
    </th>
  );
}

function Campo({
  label,
  valor,
  onChange,
  placeholder,
}: {
  label: string;
  valor: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="col" style={{ gap: 5 }}>
      <span className="label">{label}</span>
      <input className="field" value={valor} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </label>
  );
}

function Select({
  label,
  valor,
  onChange,
  opts,
  compact,
}: {
  label: string;
  valor: string;
  onChange: (v: string) => void;
  opts: { value: string; label: string }[];
  compact?: boolean;
}) {
  return (
    <label className="col" style={{ gap: 5, minWidth: compact ? 140 : "auto" }}>
      <span className="label">{label}</span>
      <select className="field" value={valor} onChange={(e) => onChange(e.target.value)}>
        {opts.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Linha({
  ev,
  sel,
  onToggleSel,
  expandido,
  onExpand,
  labels,
  onResolvido,
}: {
  ev: EventoTabela;
  sel: boolean;
  onToggleSel: () => void;
  expandido: boolean;
  onExpand: () => void;
  labels: string[];
  onResolvido: () => void;
}) {
  const [labelEdit, setLabelEdit] = useState(ev.label_efetivo);
  const [msg, setMsg] = useState<string | null>(null);
  const acao = useMutation({
    mutationFn: ({ a, lbl }: { a: AcaoEvento; lbl?: string }) =>
      a === "reabrir" ? api.eventos.reabrir(ev.id) : api.eventos.validar(ev.id, a, lbl),
    onSuccess: (_d, vars) => {
      const txt: Record<AcaoEvento, string> = {
        confirmar: "confirmado",
        corrigir: `corrigido para "${vars.lbl}"`,
        descartar: "descartado",
        reabrir: "reaberto",
      };
      setMsg(`✓ ${txt[vars.a]}`);
      window.setTimeout(onResolvido, 700);
    },
  });
  const meta = STATUS_META[ev.status_efetivo] || STATUS_META.pendente;
  const houveCorrecao = ev.label_corrigido && ev.label_corrigido !== ev.comportamento_label;
  const frames = useQuery({
    queryKey: ["frames", ev.id],
    queryFn: () => api.eventos.frames(ev.id),
    enabled: expandido,
    staleTime: 5 * 60 * 1000,
  });

  return (
    <>
      <tr style={{ borderTop: "1px solid var(--line-2)" }}>
        <td style={{ padding: "10px 12px", verticalAlign: "top" }}>
          <input type="checkbox" checked={sel} onChange={onToggleSel} style={{ accentColor: "var(--accent)" }} />
        </td>
        <td style={{ padding: "10px 12px", verticalAlign: "top" }}>
          {houveCorrecao ? (
            <span className="font-mono" style={{ fontSize: 11.5 }}>
              <span style={{ textDecoration: "line-through", color: "var(--faint)" }}>{ev.comportamento_label}</span>
              <span style={{ color: "var(--faint)" }}> → </span>
              <code style={{ background: "var(--accent-soft)", color: "var(--accent-deep)", padding: "1px 6px", borderRadius: 5 }}>
                {ev.label_corrigido}
              </code>
            </span>
          ) : (
            <code style={{ background: "var(--line-2)", color: "var(--text)", padding: "1px 6px", borderRadius: 5, fontSize: 11.5, fontFamily: "var(--mono)" }}>
              {ev.comportamento_label}
            </code>
          )}
        </td>
        <td style={{ padding: "10px 12px", verticalAlign: "top", maxWidth: 240 }}>
          <span className="clamp2" style={{ fontSize: 12, color: "var(--text)" }} title={ev.descricao_bruta}>
            {ev.descricao_bruta || "—"}
          </span>
        </td>
        <td style={{ padding: "10px 12px", verticalAlign: "top", fontSize: 11.5, color: "var(--muted)", whiteSpace: "nowrap" }}>
          <div className="truncate" title={ev.video_nome} style={{ maxWidth: 160 }}>
            {ev.video_nome}
          </div>
          <div className="font-mono">
            {ev.tempo_inicio_s.toFixed(0)}→{ev.tempo_fim_s.toFixed(0)}s · {fmtSeg(ev.duracao_s)}
          </div>
        </td>
        <td style={{ padding: "10px 12px", verticalAlign: "top", textAlign: "center", fontSize: 12, color: "var(--muted)" }}>
          #{String(ev.pessoa_track_id).padStart(3, "0")}
        </td>
        <td style={{ padding: "10px 12px", verticalAlign: "top", textAlign: "center", fontSize: 12, color: "var(--muted)" }}>
          {(ev.confianca * 100).toFixed(0)}%
        </td>
        <td style={{ padding: "10px 12px", verticalAlign: "top", textAlign: "center" }}>
          <Badge tone={meta.tone}>{meta.label}</Badge>
        </td>
        <td style={{ padding: "10px 12px", verticalAlign: "top", textAlign: "center" }}>
          <button
            onClick={onExpand}
            style={{ background: 0, border: 0, color: "var(--muted)" }}
            title="Inspecionar e editar"
          >
            <Icon name={expandido ? "chevron-up" : "chevron-down"} size={14} />
          </button>
        </td>
      </tr>
      {expandido && (
        <tr style={{ background: "var(--soft)" }}>
          <td colSpan={8} style={{ padding: 14 }}>
            <div className="row gap2" style={{ overflowX: "auto", marginBottom: 10 }}>
              {frames.isLoading && <div className="dotbg" style={{ width: "100%", height: 140, borderRadius: 10 }} />}
              {frames.data?.frames.map((src, i) => (
                <img key={i} src={src} alt={`q${i}`} style={{ height: 140, borderRadius: 10, border: "1px solid var(--line)" }} />
              ))}
              {frames.error && (
                <div style={{ fontSize: 11.5, color: "var(--apoio)", background: "var(--apoio-bg)", border: "1px solid rgba(229,149,14,.2)", borderRadius: 8, padding: "6px 10px", maxWidth: 380 }}>
                  {(frames.error as Error).message}
                </div>
              )}
            </div>
            {msg ? (
              <div style={{ fontSize: 13, color: "var(--ok)" }}>{msg}</div>
            ) : (
              <div className="row gap2 wrap" style={{ alignItems: "center" }}>
                <input
                  list={`labs-tab-${ev.id}`}
                  className="field"
                  style={{ width: 320 }}
                  value={labelEdit}
                  onChange={(e) => setLabelEdit(e.target.value)}
                />
                <datalist id={`labs-tab-${ev.id}`}>
                  {labels.map((l) => (
                    <option key={l} value={l} />
                  ))}
                </datalist>
                <Btn variant="ok" size="sm" icon="check" disabled={acao.isPending} onClick={() => acao.mutate({ a: "confirmar" })}>
                  Confirmar
                </Btn>
                <Btn variant="secondary" size="sm" icon="pencil" disabled={acao.isPending || !labelEdit.trim()} onClick={() => acao.mutate({ a: "corrigir", lbl: labelEdit.trim() })}>
                  Corrigir
                </Btn>
                <Btn variant="danger" size="sm" icon="x" disabled={acao.isPending} onClick={() => acao.mutate({ a: "descartar" })}>
                  Descartar
                </Btn>
                {ev.status_efetivo !== "pendente" && (
                  <Btn variant="ghost" size="sm" icon="rotate-ccw" disabled={acao.isPending} onClick={() => acao.mutate({ a: "reabrir" })}>
                    Reabrir
                  </Btn>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Explicação retrátil
// ════════════════════════════════════════════════════════════════════════
function ExplicacaoEventos() {
  const [aberto, setAberto] = useState(false);
  return (
    <Card style={{ padding: 0 }}>
      <button
        onClick={() => setAberto((v) => !v)}
        className="row gap2 click"
        style={{
          width: "100%",
          background: "transparent",
          border: 0,
          padding: "12px 16px",
          textAlign: "left",
        }}
      >
        <span
          style={{
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: "var(--accent-soft)",
            color: "var(--accent-deep)",
            display: "grid",
            placeItems: "center",
            fontWeight: 800,
            fontSize: 13,
          }}
        >
          i
        </span>
        <div className="grow">
          <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
            Entenda os status e como o Prism aprende
          </div>
          <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
            Em 1 minuto: o que cada coluna quer dizer e por que sua correção vale ouro.
          </div>
        </div>
        <Icon name={aberto ? "chevron-up" : "chevron-down"} size={14} color="var(--muted)" />
      </button>
      {aberto && (
        <div style={{ padding: "0 20px 20px", borderTop: "1px solid var(--line)" }}>
          <h4 style={{ fontSize: 13, fontWeight: 700, marginTop: 16, marginBottom: 10, color: "var(--ink)" }}>
            O que cada status significa
          </h4>
          <ul className="col gap2" style={{ listStyle: "none", padding: 0, fontSize: 13 }}>
            <ExplicaStatus tone="neutral" label="Pendente">
              O Prism detectou e está aguardando sua revisão.
            </ExplicaStatus>
            <ExplicaStatus tone="ok" label="Confirmado">
              Você disse que ele acertou — cada confirmação reforça o aprendizado.
            </ExplicaStatus>
            <ExplicaStatus tone="info" label="Corrigido">
              Você ajustou o nome para o termo certo do seu chão de fábrica.
            </ExplicaStatus>
            <ExplicaStatus tone="high" label="Descartado">
              Falso alarme — o Prism para de marcar coisas parecidas.
            </ExplicaStatus>
            <ExplicaStatus tone="warn" label="Auto-validado">
              Confirmado sozinho (label já validado por você 2× ou mais).
            </ExplicaStatus>
          </ul>
          <h4 style={{ fontSize: 13, fontWeight: 700, marginTop: 18, marginBottom: 10, color: "var(--ink)" }}>
            Como o Prism aprende com você
          </h4>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: 8 }}>
            {[
              ["1", "O Prism observa o vídeo", "Identifica pessoas e descreve, em linguagem natural, o que cada uma faz."],
              ["2", "Você ensina", "Confirma, corrige ou descarta. Pode aqui mesmo, a qualquer hora."],
              ["3", "Ele aprende", "Cada confirmação reforça o nome certo. Após 2 iguais, ele aprova sozinho."],
              ["4", "Próximo vídeo vem melhor", "Análises seguintes partem do que você ensinou."],
            ].map(([n, t, d]) => (
              <div
                key={n}
                style={{
                  background: "linear-gradient(135deg, var(--accent-soft), #fff)",
                  border: "1px solid var(--p-100)",
                  borderRadius: 12,
                  padding: 10,
                }}
              >
                <div className="row gap2" style={{ marginBottom: 4 }}>
                  <span style={{ width: 22, height: 22, borderRadius: "50%", background: "var(--accent)", color: "#fff", display: "grid", placeItems: "center", fontSize: 11, fontWeight: 800 }}>
                    {n}
                  </span>
                  <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>{t}</span>
                </div>
                <p style={{ fontSize: 11.5, color: "var(--muted)", lineHeight: 1.45 }}>{d}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function ExplicaStatus({
  tone,
  label,
  children,
}: {
  tone: "neutral" | "ok" | "info" | "warn" | "high";
  label: string;
  children: React.ReactNode;
}) {
  return (
    <li className="row gap2">
      <span style={{ minWidth: 110 }}>
        <Badge tone={tone}>{label}</Badge>
      </span>
      <span style={{ color: "var(--text)" }}>{children}</span>
    </li>
  );
}
