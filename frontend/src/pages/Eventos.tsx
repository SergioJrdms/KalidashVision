// ============================================================
// Eventos — porte fiel de eventos.jsx, com dados reais.
// ============================================================
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { mapEventosTabela, type EvTabMock, type ProcHeaderMock } from "../lib/adapt";
import { fmtSeg, leanCor, leanLabel, leanLong, type LeanShort } from "../design/helpers";
import { Btn, Card, Icon, Badge, Empty, Modal, toast } from "../design/ui";
import { FrameReal } from "../lib/frames";
import type { AcaoEvento } from "../lib/types";

const AVISO_VOCAB_KEY = "spectra_corrigir_vocab_aviso";

const STATUS_LIST = ["pendente", "confirmado", "corrigido", "descartado", "auto"];
const STATUS_META: Record<string, { tone: string; label: string }> = {
  pendente: { tone: "neutral", label: "Pendente" },
  confirmado: { tone: "ok", label: "Confirmado" },
  corrigido: { tone: "info", label: "Corrigido" },
  descartado: { tone: "high", label: "Descartado" },
  auto: { tone: "warn", label: "Auto-validado" },
};

export default function Eventos({ proc }: { proc: ProcHeaderMock }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("todos");
  const [busca, setBusca] = useState("");
  const [comp, setComp] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [expand, setExpand] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [aviso, setAviso] = useState<EvTabMock | null>(null);
  const [naoMostrar, setNaoMostrar] = useState(false);

  const q = useQuery({ queryKey: ["eventos-tabela", proc.id], queryFn: () => api.eventos.tabela(proc.id, { page: 1, page_size: 200, sort: "criado_em", order: "desc" }) });
  const rows: EvTabMock[] = useMemo(() => mapEventosTabela(q.data?.itens || []), [q.data]);

  const comps = useMemo(() => Array.from(new Set(rows.map((e) => e.label))), [rows]);
  const filtrados = useMemo(
    () => rows.filter((e) => (status === "todos" || e.status === status) && (!comp || e.label === comp) && (!busca || (e.label + e.descricao).toLowerCase().includes(busca.toLowerCase()))),
    [rows, status, comp, busca]
  );
  const contagem = useMemo(() => { const c: Record<string, number> = { todos: rows.length }; STATUS_LIST.forEach((s) => (c[s] = rows.filter((e) => e.status === s).length)); return c; }, [rows]);

  function invalidar() { qc.invalidateQueries({ queryKey: ["eventos-tabela", proc.id] }); qc.invalidateQueries({ queryKey: ["dashboard", proc.id] }); qc.invalidateQueries({ queryKey: ["pendentes", proc.id] }); }
  const loteMut = useMutation({ mutationFn: ({ ids, acao, label }: { ids: string[]; acao: AcaoEvento; label?: string }) => api.eventos.lote(ids, acao, label), onSuccess: invalidar });
  const acaoMut = useMutation({
    mutationFn: ({ id, acao, label }: { id: string; acao: AcaoEvento; label?: string }) => (acao === "reabrir" ? api.eventos.reabrir(id) : api.eventos.validar(id, acao, label)),
    onSuccess: invalidar,
  });
  const setCatMut = useMutation({
    mutationFn: ({ id, cat }: { id: string; cat: LeanShort }) => api.comportamentos.setCategoria(id, leanLong(cat)),
    onSuccess: () => { invalidar(); qc.invalidateQueries({ queryKey: ["processos"] }); },
  });

  function pedirCorrigir(e: EvTabMock) {
    setExpand(e.id);
    if (localStorage.getItem(AVISO_VOCAB_KEY) === "1") setEditId(e.id);
    else { setNaoMostrar(false); setAviso(e); }
  }
  function confirmarAviso() {
    if (naoMostrar) localStorage.setItem(AVISO_VOCAB_KEY, "1");
    if (aviso) setEditId(aviso.id);
    setAviso(null);
  }
  function salvarCorrecao(e: EvTabMock, novoLabel: string, novaCat: LeanShort) {
    const label = novoLabel.trim();
    const labelMudou = !!label && label !== e.label;
    const catMudou = novaCat !== e.cat;
    if (labelMudou) acaoMut.mutate({ id: e.id, acao: "corrigir", label });
    if (catMudou && e.comportamentoId) setCatMut.mutate({ id: e.comportamentoId, cat: novaCat });
    if (labelMudou || catMudou) toast("Correção salva. O Prism aprendeu com você.", { icon: "check" });
    setEditId(null);
  }

  function toggle(id: string) { setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  function toggleAll() { setSel((s) => (s.size === filtrados.length ? new Set() : new Set(filtrados.map((e) => e.id)))); }
  function aplicarLote(acao: AcaoEvento) {
    let label: string | undefined;
    if (acao === "corrigir") { label = window.prompt(`Reclassificar ${sel.size} evento(s) para qual comportamento?`)?.trim(); if (!label) return; }
    loteMut.mutate({ ids: Array.from(sel), acao, label });
    toast(`${sel.size} evento(s) — ação aplicada.`, { icon: "check" });
    setSel(new Set());
  }
  function resolver(id: string, acao: AcaoEvento) { acaoMut.mutate({ id, acao }); toast(`Evento ${acao === "reabrir" ? "reaberto" : acao + "do"}.`, { icon: "check" }); }

  return (
    <div className="col" style={{ gap: 16 }}>
      <ExplicaStatus />

      <Card style={{ padding: 14 }}>
        <div className="row gap2 wrap">
          <div style={{ position: "relative", flex: "1 1 220px" }}>
            <Icon name="search" size={15} color="var(--faint)" style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)" }} />
            <input className="field" style={{ paddingLeft: 34 }} value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar comportamento ou descrição…" />
          </div>
          <select className="field" style={{ flex: "0 1 180px" }} value={comp} onChange={(e) => setComp(e.target.value)}>
            <option value="">Todos os comportamentos</option>
            {comps.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <span className="grow" />
          <span style={{ fontSize: 12.5, color: "var(--muted)", alignSelf: "center" }}>{filtrados.length} de {rows.length} eventos</span>
        </div>
        <div className="row gap1 wrap" style={{ marginTop: 12 }}>
          {["todos", ...STATUS_LIST].map((s) => (
            <button key={s} onClick={() => setStatus(s)} style={{ padding: "5px 12px", borderRadius: 99, fontSize: 12, fontWeight: 600, border: "1px solid", borderColor: status === s ? "var(--accent)" : "var(--line)", background: status === s ? "var(--accent)" : "#fff", color: status === s ? "#fff" : "var(--muted)" }}>
              {s === "todos" ? "Todos" : STATUS_META[s].label} · {contagem[s] || 0}
            </button>
          ))}
        </div>
      </Card>

      {sel.size > 0 && (
        <Card className="anim-pop" style={{ padding: "10px 16px", background: "var(--accent-soft)", border: "1px solid var(--p-200)", position: "sticky", top: 70, zIndex: 20 }}>
          <div className="row gap2 wrap" style={{ justifyContent: "space-between" }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--accent-deep)" }}>{sel.size} selecionado(s)</span>
            <div className="row gap1 wrap">
              <Btn variant="ok" size="sm" icon="check" onClick={() => aplicarLote("confirmar")}>Confirmar</Btn>
              <Btn variant="secondary" size="sm" icon="pencil" onClick={() => aplicarLote("corrigir")}>Corrigir</Btn>
              <Btn variant="danger" size="sm" icon="x" onClick={() => aplicarLote("descartar")}>Descartar</Btn>
              <Btn variant="ghost" size="sm" onClick={() => setSel(new Set())}>Limpar</Btn>
            </div>
          </div>
        </Card>
      )}

      <Card style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--soft)", color: "var(--muted)", fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".05em" }}>
                <th style={{ padding: "11px 12px", width: 34 }}><input type="checkbox" checked={filtrados.length > 0 && sel.size === filtrados.length} onChange={toggleAll} style={{ accentColor: "var(--accent)" }} /></th>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Comportamento</th>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Classificação</th>
                <th style={{ padding: "11px 12px", textAlign: "left" }}>Vídeo · tempo</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Pessoa</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Conf.</th>
                <th style={{ padding: "11px 12px", textAlign: "center" }}>Status</th>
                <th style={{ padding: "11px 12px", width: 110, textAlign: "right" }}>Ações</th>
              </tr>
            </thead>
            <tbody>
              {filtrados.map((e) => (
                <LinhaEvento key={e.id} e={e} sel={sel.has(e.id)} onToggle={() => toggle(e.id)} expand={expand === e.id} onExpand={() => setExpand((c) => (c === e.id ? null : e.id))} onResolver={resolver} labels={comps} editing={editId === e.id} onPedirCorrigir={() => pedirCorrigir(e)} onSalvar={(label, cat) => salvarCorrecao(e, label, cat)} onCancelarEdicao={() => setEditId(null)} />
              ))}
            </tbody>
          </table>
        </div>
        {q.isLoading ? <Empty icon="loader" title="Carregando…" /> : filtrados.length === 0 && <Empty icon="search-x" title="Nenhum evento encontrado" desc="Ajuste os filtros para ver outros eventos." />}
      </Card>

      {aviso && (
        <Modal onClose={() => setAviso(null)} width={440}>
          <div className="row gap2" style={{ marginBottom: 12 }}>
            <span className="center" style={{ width: 38, height: 38, borderRadius: 11, background: "var(--accent-soft)", color: "var(--accent)", flex: "none" }}><Icon name="book-open" size={19} /></span>
            <h3 className="font-display" style={{ fontSize: 17, fontWeight: 700, color: "var(--ink)" }}>Mantenha o vocabulário consistente</h3>
          </div>
          <p style={{ fontSize: 13.5, color: "var(--text)", lineHeight: 1.55, marginBottom: 14 }}>
            Antes de corrigir, prefira <b>reaproveitar um nome que já existe</b> em vez de criar variações (ex.: "empurrar carrinho" vs. "empurra o carrinho"). Um vocabulário enxuto deixa os relatórios e os padrões do Prism muito mais precisos — cada nome novo fragmenta a análise.
          </p>
          <label className="row gap2" style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 16, cursor: "pointer" }}>
            <input type="checkbox" checked={naoMostrar} onChange={(e) => setNaoMostrar(e.target.checked)} style={{ accentColor: "var(--accent)" }} />
            Não mostrar este aviso novamente
          </label>
          <div className="row gap2" style={{ justifyContent: "flex-end" }}>
            <Btn variant="ghost" size="sm" onClick={() => setAviso(null)}>Cancelar</Btn>
            <Btn variant="primary" size="sm" icon="check" onClick={confirmarAviso}>Entendi, corrigir</Btn>
          </div>
        </Modal>
      )}
    </div>
  );
}

function LinhaEvento({ e, sel, onToggle, expand, onExpand, onResolver, labels, editing, onPedirCorrigir, onSalvar, onCancelarEdicao }: { e: EvTabMock; sel: boolean; onToggle: () => void; expand: boolean; onExpand: () => void; onResolver: (id: string, acao: AcaoEvento) => void; labels: string[]; editing: boolean; onPedirCorrigir: () => void; onSalvar: (label: string, cat: LeanShort) => void; onCancelarEdicao: () => void }) {
  const m = STATUS_META[e.status] || STATUS_META.pendente;
  return (
    <>
      <tr style={{ borderTop: "1px solid var(--line-2)", background: sel ? "var(--accent-soft)" : expand ? "var(--soft)" : "#fff" }}>
        <td style={{ padding: "10px 12px" }}><input type="checkbox" checked={sel} onChange={onToggle} style={{ accentColor: "var(--accent)" }} /></td>
        <td style={{ padding: "10px 12px" }}>
          {e.status === "corrigido" && e.corrigido ? (
            <span style={{ fontSize: 12 }}><span style={{ textDecoration: "line-through", color: "var(--faint)" }}>{e.labelOrig}</span> <span style={{ color: "var(--faint)" }}>→</span> <code className="font-mono" style={{ background: "var(--accent-soft)", color: "var(--accent-deep)", padding: "2px 6px", borderRadius: 5 }}>{e.corrigido}</code></span>
          ) : (
            <code className="font-mono" style={{ background: "var(--line-2)", color: "var(--text)", padding: "2px 7px", borderRadius: 5, fontSize: 11.5 }}>{e.label}</code>
          )}
        </td>
        <td style={{ padding: "10px 12px" }}>
          <span className="row gap1" style={{ fontSize: 11, fontWeight: 600, color: "var(--text)", padding: "2px 8px", borderRadius: 7, border: "1px solid var(--line)", background: "#fff", display: "inline-flex", whiteSpace: "nowrap" }}>
            <i style={{ width: 8, height: 8, borderRadius: 2, background: leanCor(e.cat), flex: "none" }} /> {leanLabel(e.cat)}
          </span>
        </td>
        <td style={{ padding: "10px 12px", color: "var(--muted)", fontSize: 11.5 }}>
          <div className="truncate" style={{ maxWidth: 150 }}>{e.video}</div>
          <div className="font-mono">{e.ini.toFixed(0)}→{e.fim.toFixed(0)}s · {fmtSeg(e.fim - e.ini)}</div>
        </td>
        <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--muted)", fontSize: 12 }} className="font-mono">{String(e.pessoa).padStart(3, "0")}</td>
        <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--muted)", fontSize: 12 }} className="tnum">{Math.round(e.conf * 100)}%</td>
        <td style={{ padding: "10px 12px", textAlign: "center" }}><Badge tone={m.tone}>{m.label}</Badge></td>
        <td style={{ padding: "10px 12px", textAlign: "right" }}>
          <button onClick={onExpand} className="center" style={{ width: 28, height: 28, borderRadius: 7, border: "1px solid var(--line)", background: "#fff", color: "var(--muted)", display: "inline-grid" }}>
            <Icon name={expand ? "chevron-up" : "chevron-down"} size={15} />
          </button>
        </td>
      </tr>
      {expand && (
        <tr style={{ background: "var(--soft)" }}>
          <td colSpan={8} style={{ padding: 16 }}>
            <div className="row gap3 wrap" style={{ alignItems: "flex-start" }}>
              <div style={{ width: 240, flex: "none" }}><FrameReal id={e.id} pessoa={e.pessoa} height={140} /></div>
              <div className="grow" style={{ minWidth: 220 }}>
                <p style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, marginBottom: 12 }}>{e.descricao}</p>
                {editing ? (
                  <CorrigirForm e={e} labels={labels} onSalvar={onSalvar} onCancelar={onCancelarEdicao} />
                ) : (
                  <div className="row gap1 wrap">
                    <Btn variant="ok" size="sm" icon="check" onClick={() => onResolver(e.id, "confirmar")}>Confirmar</Btn>
                    <Btn variant="secondary" size="sm" icon="pencil" onClick={onPedirCorrigir}>Corrigir</Btn>
                    <Btn variant="danger" size="sm" icon="x" onClick={() => onResolver(e.id, "descartar")}>Descartar</Btn>
                    {e.status !== "pendente" && <Btn variant="ghost" size="sm" icon="rotate-ccw" onClick={() => onResolver(e.id, "reabrir")}>Reabrir</Btn>}
                  </div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function CorrigirForm({ e, labels, onSalvar, onCancelar }: { e: EvTabMock; labels: string[]; onSalvar: (label: string, cat: LeanShort) => void; onCancelar: () => void }) {
  const [label, setLabel] = useState(e.label);
  const [cat, setCat] = useState<LeanShort>(e.cat);
  const listId = `labels-ev-${e.id}`;
  return (
    <div className="col anim-fadeup" style={{ gap: 12, maxWidth: 460 }}>
      <div className="col" style={{ gap: 5 }}>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--muted)" }}>Comportamento</span>
        <input className="field" list={listId} autoFocus value={label} onChange={(ev) => setLabel(ev.target.value)} placeholder="nome do comportamento" style={{ maxWidth: 320 }}
          onKeyDown={(ev) => { if (ev.key === "Enter" && label.trim()) onSalvar(label, cat); }} />
        <datalist id={listId}>{labels.map((l) => <option key={l} value={l} />)}</datalist>
        <span style={{ fontSize: 11, color: "var(--faint)" }}>Reaproveite um nome já existente para manter o vocabulário consistente.</span>
      </div>
      <div className="col" style={{ gap: 5 }}>
        <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--muted)" }}>Classificação Lean</span>
        <div className="row gap1 wrap">
          {(["va", "apoio", "desp"] as LeanShort[]).map((c) => (
            <button key={c} onClick={() => setCat(c)} className="row gap1" style={{ padding: "5px 11px", borderRadius: 8, fontSize: 12, fontWeight: 600, color: "var(--text)", background: "#fff", border: cat === c ? "2px solid var(--ink)" : "1px solid var(--line)" }}>
              <i style={{ width: 9, height: 9, borderRadius: 2, background: leanCor(c) }} /> {leanLabel(c)}
            </button>
          ))}
        </div>
        {!e.comportamentoId && <span style={{ fontSize: 11, color: "var(--faint)" }}>A reclassificação será aplicada quando o comportamento existir no catálogo.</span>}
      </div>
      <div className="row gap1">
        <Btn variant="primary" size="sm" icon="check" disabled={!label.trim()} onClick={() => onSalvar(label, cat)}>Salvar correção</Btn>
        <Btn variant="ghost" size="sm" onClick={onCancelar}>Cancelar</Btn>
      </div>
    </div>
  );
}

function ExplicaStatus() {
  const [open, setOpen] = useState(false);
  return (
    <Card style={{ overflow: "hidden" }}>
      <button onClick={() => setOpen((v) => !v)} className="row gap3" style={{ width: "100%", textAlign: "left", border: "none", background: "transparent", padding: "14px 18px" }}>
        <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--accent-soft)", color: "var(--accent)", flex: "none" }}><Icon name="info" size={17} /></span>
        <div className="grow">
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>Entenda os status e como o Prism aprende com você</div>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>Em 1 minuto: o que cada status quer dizer e por que sua correção vale ouro.</div>
        </div>
        <Icon name={open ? "chevron-up" : "chevron-down"} size={18} color="var(--muted)" />
      </button>
      {open && (
        <div style={{ padding: "0 18px 18px", borderTop: "1px solid var(--line-2)" }}>
          <div className="row wrap" style={{ gap: 14, marginTop: 16 }}>
            {[
              ["pendente", "A IA detectou mas não tem certeza. Aguarda sua revisão."],
              ["confirmado", "Você disse que a IA acertou. Reforça o aprendizado."],
              ["corrigido", "Você ajustou para o nome certo da sua operação."],
              ["descartado", "Falso alarme — a IA para de marcar coisas parecidas."],
              ["auto", "A IA aprovou sozinha (você já confirmou 2× antes)."],
            ].map(([s, d]) => (
              <div key={s} className="row gap2" style={{ flex: "1 1 300px", alignItems: "flex-start" }}>
                <span style={{ flex: "none", width: 104 }}><Badge tone={STATUS_META[s].tone}>{STATUS_META[s].label}</Badge></span>
                <p style={{ fontSize: 12.5, color: "var(--text)", lineHeight: 1.45 }}>{d}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
