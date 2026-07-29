// ============================================================
// Validação — Foco Único com GERENCIAMENTO DE FILA POR LOTES:
//  • mostra apenas TAMANHO_LOTE itens por vez (não despeja 60+ de cara);
//  • o cliente pode PULAR (botão ou swipe horizontal) — o card volta pro
//    FINAL do lote (loop dentro do lote, sem crescer infinito);
//  • quando o lote termina, abre o próximo lote (mesma quantidade);
//  • header sempre deixa explícito "lote N de M" e "X ainda na fila".
// ============================================================
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { mapPendentes, mapPerguntas, type PendMock, type PendIrmaoMock, type PergMock, type ProcHeaderMock } from "../lib/adapt";
import { nivelDe, leanCor, leanLabel, fmtSeg } from "../design/helpers";
import { Btn, Card, Icon, Prism, Ring, toast } from "../design/ui";
import { FrameStripReal, FrameStripSegmento, FrameReal } from "../lib/frames";
import type { Go } from "../design/Shell";
import type { Tweaks } from "../App";

const TAMANHO_LOTE = 10;

// "cam1" → "Câmera 1"; fallback gracioso para ids fora do padrão.
function camLabel(camId: string | null | undefined): string {
  if (!camId) return "Câmera";
  const m = /cam(\d+)/i.exec(camId);
  return m ? `Câmera ${m[1]}` : camId;
}

export default function Validacao({ proc, go, t }: { proc: ProcHeaderMock; go: Go; t: Tweaks }) {
  const qc = useQueryClient();
  const fila = t.validacao !== "cards";
  const pendQ = useQuery({ queryKey: ["pendentes", proc.id], queryFn: () => api.processos.eventosPendentes(proc.id, true) });
  const pergQ = useQuery({ queryKey: ["perguntas", proc.id], queryFn: () => api.perguntas.listar(proc.id, "pendente") });
  const dashQ = useQuery({ queryKey: ["dashboard", proc.id], queryFn: () => api.processos.dashboard(proc.id), staleTime: 30_000 });

  const labels = useMemo(
    () => (dashQ.data?.snapshot.distribuicao_comportamentos || []).map((d) => d.comportamento).sort(),
    [dashQ.data]
  );

  const [queue, setQueue] = useState<PendMock[]>([]);
  const [blocoIds, setBlocoIds] = useState<string[]>([]);   // ids do lote atual, na ordem (suporta rotação)
  const [loteIdx, setLoteIdx] = useState(0);                // 1, 2, 3... (0 antes de abrir o 1º)
  const [perguntas, setPerguntas] = useState<(PergMock & { status: "open" | "answered" | "dismissed"; answer?: string | null })[]>([]);
  const [done, setDone] = useState(0);
  const [learned, setLearned] = useState(0);
  const [streak, setStreak] = useState(0);
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    if (!seeded && pendQ.data) {
      const q = mapPendentes(pendQ.data);
      setQueue(q);
      setBlocoIds(q.slice(0, TAMANHO_LOTE).map((x) => x.id));
      setLoteIdx(q.length > 0 ? 1 : 0);
      setSeeded(true);
    }
  }, [pendQ.data, seeded]);
  useEffect(() => {
    if (pergQ.data) setPerguntas((cur) => (cur.length ? cur : mapPerguntas(pergQ.data).map((q) => ({ ...q, status: "open" as const, answer: null }))));
  }, [pergQ.data]);

  const totalInicial = pendQ.data?.length ?? 0;
  const totalLotes = Math.max(1, Math.ceil(totalInicial / TAMANHO_LOTE));
  const matBoost = Math.min(16, done * 1.4);
  const maturidade = Math.min(99, Math.round((proc.maturidade || 0) + matBoost));
  const respondidas = perguntas.filter((q) => q.status !== "open").length;

  const validar = useMutation({
    // Resolve o grupo inteiro num lote atômico: o primário + todos os irmãos
    // (câmeras diferentes da mesma ação). Com 1 id só, o lote age como validar.
    mutationFn: ({ ids, acao, label }: { ids: string[]; acao: "confirmar" | "corrigir" | "descartar"; label?: string }) =>
      api.eventos.lote(ids, acao, label),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dashboard", proc.id] }); qc.invalidateQueries({ queryKey: ["processos"] }); },
  });
  const respMut = useMutation({ mutationFn: ({ id, resposta }: { id: string; resposta: string }) => api.perguntas.responder(id, resposta) });
  const dispMut = useMutation({ mutationFn: (id: string) => api.perguntas.dispensar(id) });

  function resolver(ev: PendMock, acao: "confirmar" | "corrigir" | "descartar", novoLabel?: string) {
    const ids = [ev.id, ...(ev.irmaos?.map((s) => s.id) ?? [])];
    validar.mutate({ ids, acao, label: novoLabel });
    setQueue((q) => q.filter((x) => x.id !== ev.id));
    setBlocoIds((ids) => ids.filter((id) => id !== ev.id));
    setDone((d) => d + 1);
    if (acao !== "descartar") setLearned((l) => l + 1);
    setStreak((s) => (acao === "descartar" ? 0 : s + 1));
    const msg = acao === "confirmar" ? "Confirmado · o Prism aprendeu com você" : acao === "corrigir" ? `Corrigido para “${novoLabel}”` : "Descartado · falso alarme registrado";
    toast(msg, { icon: acao === "descartar" ? "x" : "check", color: acao === "descartar" ? "#F8B4B6" : "#3EE6AE" });
  }

  function pular() {
    // Empurra o item da frente pro fim do lote (loop dentro do lote).
    setBlocoIds((ids) => (ids.length > 1 ? [...ids.slice(1), ids[0]] : ids));
  }

  function proximoLote() {
    // Pega os próximos TAMANHO_LOTE da fila (excluindo o que já estava no bloco).
    const idsNoBloco = new Set(blocoIds);
    const restantes = queue.filter((q) => !idsNoBloco.has(q.id));
    setBlocoIds(restantes.slice(0, TAMANHO_LOTE).map((x) => x.id));
    setLoteIdx((l) => l + 1);
  }

  function responder(q: PergMock, answer: string) {
    respMut.mutate({ id: q.id, resposta: answer });
    setPerguntas((arr) => arr.map((x) => (x.id === q.id ? { ...x, status: "answered", answer } : x)));
    setLearned((l) => l + 1);
    toast("Resposta virou conhecimento do Prism.", { icon: "sparkles" });
  }
  function dispensar(q: PergMock) {
    dispMut.mutate(q.id);
    setPerguntas((arr) => arr.map((x) => (x.id === q.id ? { ...x, status: "dismissed" } : x)));
  }

  if (pendQ.isLoading) return <Card style={{ padding: 0 }}><div className="center" style={{ padding: 60 }}><span className="spin" style={{ width: 22, height: 22, border: "3px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} /></div></Card>;

  // Bloco visível na ordem dos ids (suporta rotação do pular).
  const mapById = new Map(queue.map((q) => [q.id, q]));
  const bloco = blocoIds.map((id) => mapById.get(id)).filter((x): x is PendMock => !!x);
  const evento = bloco[0];
  const restantesBloco = bloco.length;
  const restantesFila = queue.length;
  const naFilaAposBloco = Math.max(0, restantesFila - restantesBloco);

  return (
    <div className="col" style={{ gap: 18, maxWidth: 1080, margin: "0 auto" }}>
      <SessionHeader maturidade={maturidade} done={done} total={totalInicial} learned={learned} streak={streak} />

      {perguntas.some((q) => q.status === "open") && (
        <PerguntasConversa perguntas={perguntas} onResponder={responder} onDispensar={dispensar} respondidas={respondidas} />
      )}

      {restantesFila === 0 ? (
        <FilaLimpa done={done} learned={learned} go={go} proc={proc} />
      ) : restantesBloco === 0 ? (
        // Acabou o lote, mas ainda tem mais — transição amigável.
        <LoteConcluido loteIdx={loteIdx} totalLotes={totalLotes} restantesFila={restantesFila} tamanhoLote={TAMANHO_LOTE} onProximo={proximoLote} />
      ) : fila ? (
        <FilaFoco
          evento={evento!}
          restantesBloco={restantesBloco}
          loteIdx={loteIdx}
          totalLotes={totalLotes}
          naFilaAposBloco={naFilaAposBloco}
          onResolver={resolver}
          onPular={pular}
          labels={labels}
        />
      ) : (
        <CardsGrid queue={bloco} onResolver={resolver} labels={labels} />
      )}
    </div>
  );
}

function SessionHeader({ maturidade, done, total, learned, streak }: { maturidade: number; done: number; total: number; learned: number; streak: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <Card style={{ padding: "16px 20px", background: "linear-gradient(120deg, var(--soft), #fff 72%)" }}>
      <div className="row gap3 wrap" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div className="row gap3" style={{ minWidth: 0 }}>
          <Ring pct={maturidade} size={58} stroke={6} color={nivelDe(maturidade).cor}><Prism size={30} /></Ring>
          <div style={{ minWidth: 0 }}>
            <div className="row gap2">
              <h1 className="font-display" style={{ fontSize: 19, fontWeight: 700 }}>Ensinando o Prism</h1>
              {streak >= 3 && <span className="chip anim-pop" style={{ background: "var(--apoio-bg)", color: "#b8740b", border: "none" }}><Icon name="flame" size={12} /> {streak} seguidos</span>}
            </div>
            <p style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>
              {nivelDe(maturidade).rotulo} · conhece <b className="tnum" style={{ color: "var(--ink)" }}>{maturidade}%</b> desta linha
              {done > 0 && <> · <span style={{ color: "var(--va)", fontWeight: 600 }}>+{learned} padrões hoje</span></>}
            </p>
          </div>
        </div>
        <div className="row gap4" style={{ alignItems: "center" }}>
          <div className="col" style={{ alignItems: "flex-end", gap: 4, minWidth: 130 }}>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>{done} de {total} revisados</span>
            <div className="track" style={{ width: 160, height: 8 }}><i style={{ width: `${pct}%`, background: "var(--grad-cta)", transition: "width .5s cubic-bezier(.2,.8,.2,1)" }} /></div>
          </div>
        </div>
      </div>
    </Card>
  );
}

type PQ = PergMock & { status: "open" | "answered" | "dismissed"; answer?: string | null };
function PerguntasConversa({ perguntas, onResponder, onDispensar }: { perguntas: PQ[]; onResponder: (q: PergMock, a: string) => void; onDispensar: (q: PergMock) => void; respondidas: number }) {
  const abertas = perguntas.filter((q) => q.status === "open");
  const atual = abertas[0];
  const respondidasArr = perguntas.filter((q) => q.status === "answered");
  return (
    <Card style={{ padding: 0, overflow: "hidden", border: "1px solid var(--p-200)" }}>
      <div className="row gap2" style={{ padding: "12px 18px", background: "var(--accent-soft)", borderBottom: "1px solid var(--line)" }}>
        <Prism size={28} ring />
        <div className="grow">
          <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>O Prism quer te perguntar</div>
          <div style={{ fontSize: 11.5, color: "var(--accent-deep)" }}>Conversa rápida · cada resposta deixa as próximas análises mais certeiras</div>
        </div>
        <span className="badge badge-purple">{abertas.length} aberta{abertas.length > 1 ? "s" : ""}</span>
      </div>
      <div className="col" style={{ padding: 18, gap: 14 }}>
        {respondidasArr.slice(-1).map((q) => (
          <div key={q.id} className="col" style={{ gap: 8, opacity: 0.85 }}>
            <Bubble who="prism" text={q.pergunta} />
            <Bubble who="me" text={q.answer || ""} />
            <div className="row gap1" style={{ fontSize: 11.5, color: "var(--va)", paddingLeft: 38 }}><Icon name="check" size={13} /> Anotado — virou contexto permanente do Prism.</div>
          </div>
        ))}
        {atual && <PerguntaAtiva key={atual.id} q={atual} onResponder={onResponder} onDispensar={onDispensar} />}
      </div>
    </Card>
  );
}

function Bubble({ who, text }: { who: "me" | "prism"; text: string }) {
  if (who === "me") {
    return <div className="row" style={{ justifyContent: "flex-end" }}><div style={{ maxWidth: "76%", background: "var(--accent)", color: "#fff", padding: "8px 13px", borderRadius: "14px 14px 4px 14px", fontSize: 13.5, lineHeight: 1.45 }}>{text}</div></div>;
  }
  return (
    <div className="row gap2" style={{ alignItems: "flex-start" }}>
      <Prism size={26} ring />
      <div style={{ maxWidth: "82%", background: "var(--soft)", border: "1px solid var(--line)", padding: "9px 13px", borderRadius: "14px 14px 14px 4px", fontSize: 13.5, color: "var(--ink)", lineHeight: 1.5 }}>{text}</div>
    </div>
  );
}

function PerguntaAtiva({ q, onResponder, onDispensar }: { q: PQ; onResponder: (q: PergMock, a: string) => void; onDispensar: (q: PergMock) => void }) {
  const [texto, setTexto] = useState("");
  const [motivo, setMotivo] = useState(false);
  return (
    <div className="col anim-fadeup" style={{ gap: 12 }}>
      <Bubble who="prism" text={q.pergunta} />
      <div style={{ paddingLeft: 38 }}>
        {q.relacionados?.length > 0 && (
          <div className="row wrap gap1" style={{ marginBottom: 8 }}>
            {q.relacionados.map((c) => <code key={c} className="font-mono" style={{ fontSize: 10, background: "var(--line-2)", color: "var(--text)", padding: "2px 7px", borderRadius: 6 }}>{c}</code>)}
          </div>
        )}
        <div className="row wrap gap2" style={{ marginBottom: 10 }}>
          {q.chips.map((c) => (
            <button key={c} onClick={() => onResponder(q, c)} className="row gap1" style={{ padding: "7px 14px", borderRadius: 99, border: "1px solid var(--p-200)", background: "#fff", color: "var(--accent-deep)", fontSize: 13, fontWeight: 600, transition: "all .15s" }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--accent-soft)")} onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "#fff")}>
              {c}
            </button>
          ))}
        </div>
        <div className="row gap2" style={{ alignItems: "flex-end" }}>
          <input className="field" value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="…ou responda com suas palavras" onKeyDown={(e) => { if (e.key === "Enter" && texto.trim()) onResponder(q, texto.trim()); }} />
          <Btn variant="secondary" disabled={!texto.trim()} onClick={() => onResponder(q, texto.trim())} icon="send">Enviar</Btn>
        </div>
        <div className="row gap2" style={{ marginTop: 8 }}>
          <button onClick={() => setMotivo((v) => !v)} className="row gap1" style={{ border: "none", background: "none", color: "var(--muted)", fontSize: 11.5, padding: 0 }}>
            <Icon name="help-circle" size={12} /> por que essa pergunta?
          </button>
          <span style={{ color: "var(--line)" }}>·</span>
          <button onClick={() => onDispensar(q)} style={{ border: "none", background: "none", color: "var(--muted)", fontSize: 11.5, padding: 0 }}>dispensar</button>
        </div>
        {motivo && q.motivo && <p className="anim-fadeup" style={{ fontSize: 12, color: "var(--muted)", fontStyle: "italic", marginTop: 6, maxWidth: 560 }}>{q.motivo}</p>}
      </div>
    </div>
  );
}

// Faixa rotulada de UMA câmera (usada dentro do empilhamento multi-câmera).
function FaixaCamera({ camId, ativo, labelDivergente }: { camId: string | null; ativo: { id: string; pessoa: number; label: string; ini: number; fim: number }; labelDivergente?: string }) {
  return (
    <div className="col" style={{ gap: 0 }}>
      <div className="row gap2" style={{ alignItems: "center", padding: "6px 10px", background: "#0d0820", borderBottom: "1px solid rgba(255,255,255,.08)" }}>
        <span className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, color: "#fff", background: "rgba(167,139,250,.25)", border: "1px solid rgba(167,139,250,.45)", borderRadius: 99, padding: "2px 9px" }}>
          <Icon name="video" size={11} /> {camLabel(camId)}
        </span>
        {labelDivergente && (
          <span style={{ fontSize: 11, color: "rgba(255,255,255,.62)" }}>
            viu como <b style={{ color: "rgba(255,255,255,.85)" }}>“{labelDivergente}”</b>
          </span>
        )}
      </div>
      <FrameStripReal ativo={ativo} />
    </div>
  );
}

// 2º ângulo (Fase 6 dual-angle): faixa rotulada que busca os frames do SEGMENTO
// par (cam2) por janela de tempo — não há evento-irmão, só o segmento.
function FaixaSegundoAngulo({ camId, segmentoId, ini, fim }: { camId: string | null; segmentoId: string; ini: number; fim: number }) {
  return (
    <div className="col" style={{ gap: 0 }}>
      <div className="row gap2" style={{ alignItems: "center", padding: "6px 10px", background: "#0d0820", borderBottom: "1px solid rgba(255,255,255,.08)" }}>
        <span className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, color: "#fff", background: "rgba(167,139,250,.25)", border: "1px solid rgba(167,139,250,.45)", borderRadius: 99, padding: "2px 9px" }}>
          <Icon name="video" size={11} /> {camLabel(camId)}
        </span>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,.62)" }}>2º ângulo (mesma ação)</span>
      </div>
      <FrameStripSegmento segmentoId={segmentoId} ini={ini} fim={fim} />
    </div>
  );
}

// Empilha as faixas das 2+ câmeras do grupo (primário no topo, irmãos abaixo).
function FaixasMultiCamera({ evento }: { evento: PendMock }) {
  const irmaos = evento.irmaos ?? [];
  return (
    <div className="col" style={{ gap: 6, padding: 6, background: "#0d0820" }}>
      <FaixaCamera camId={evento.camId} ativo={evento} />
      {irmaos.map((s: PendIrmaoMock) => (
        <FaixaCamera
          key={s.id}
          camId={s.camId}
          ativo={{ id: s.id, pessoa: s.pessoa, label: s.label, ini: s.ini, fim: s.fim }}
          labelDivergente={s.label !== evento.label ? s.label : undefined}
        />
      ))}
      {/* Dual-angle (Fase 6): sem irmão-evento, mas há o segmento da cam2. */}
      {irmaos.length === 0 && evento.segundoAngulo && (
        <FaixaSegundoAngulo
          camId={evento.segundoAngulo.camId}
          segmentoId={evento.segundoAngulo.segmentoId}
          /* Fase 30: offset de relógio cam1→cam2 (segmentos não começam juntos) */
          ini={Math.max(0, evento.ini + evento.segundoAngulo.offsetS)}
          fim={Math.max(0, evento.fim + evento.segundoAngulo.offsetS)}
        />
      )}
    </div>
  );
}

function FilaFoco({
  evento,
  restantesBloco,
  loteIdx,
  totalLotes,
  naFilaAposBloco,
  onResolver,
  onPular,
  labels,
}: {
  evento: PendMock;
  restantesBloco: number;
  loteIdx: number;
  totalLotes: number;
  naFilaAposBloco: number;
  onResolver: (ev: PendMock, k: "confirmar" | "corrigir" | "descartar", l?: string) => void;
  onPular: () => void;
  labels: string[];
}) {
  const [phase, setPhase] = useState<"idle" | "confirm" | "leaving">("idle");
  const [corrigir, setCorrigir] = useState(false);
  const [label, setLabel] = useState(evento.label);
  const [conf, setConf] = useState(Math.round(evento.conf * 100));
  const [resolvedKind, setResolvedKind] = useState<string | null>(null);
  // Swipe horizontal (mouse + touch) — arraste pra qualquer lado pra pular.
  const [drag, setDrag] = useState<{ startX: number; dx: number } | null>(null);

  useEffect(() => {
    setPhase("idle"); setCorrigir(false); setLabel(evento.label);
    setConf(Math.round(evento.conf * 100)); setResolvedKind(null); setDrag(null);
  }, [evento.id]);

  function act(kind: "confirmar" | "corrigir" | "descartar", lbl?: string) {
    setResolvedKind(kind);
    if (kind !== "descartar") setConf(Math.min(98, Math.round(evento.conf * 100) + 23));
    setPhase("confirm");
    setTimeout(() => setPhase("leaving"), 620);
    setTimeout(() => onResolver(evento, kind, lbl), 980);
  }

  // ── Swipe ────────────────────────────────────────────────
  const SWIPE_TH = 110; // px de deslocamento para confirmar o "pular"
  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (phase !== "idle" || corrigir) return;
    // não inicia drag em controles interativos
    const t = e.target as HTMLElement;
    if (t.closest("button, input, textarea, a, label, [data-no-drag]")) return;
    setDrag({ startX: e.clientX, dx: 0 });
    try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); } catch { /* ignore */ }
  }
  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!drag) return;
    setDrag({ ...drag, dx: e.clientX - drag.startX });
  }
  function endDrag() {
    if (!drag) return;
    const dx = drag.dx;
    setDrag(null);
    if (Math.abs(dx) >= SWIPE_TH) {
      // anima saída e troca evento (o useEffect do próximo evento reseta o estado)
      setResolvedKind("pular");
      setPhase("leaving");
      setTimeout(() => onPular(), 320);
    }
  }

  const swipeStyle: React.CSSProperties = drag
    ? { transform: `translateX(${drag.dx}px) rotate(${drag.dx * 0.02}deg)`, transition: "none" }
    : { transition: "transform .25s cubic-bezier(.2,.8,.2,1)" };

  const animClass = phase === "leaving"
    ? (resolvedKind === "descartar" || resolvedKind === "pular" ? "leave-r" : "leave-l")
    : "anim-pop";

  return (
    <div className="col" style={{ gap: 10 }}>
      {/* Barra de lote — diz claramente "lote N de M, X ainda na fila" */}
      <BarraLote
        loteIdx={loteIdx}
        totalLotes={totalLotes}
        restantesBloco={restantesBloco}
        naFilaAposBloco={naFilaAposBloco}
      />

      <div style={{ position: "relative" }}>
        {restantesBloco > 1 && <div className="card" style={{ position: "absolute", inset: "8px -8px -8px -8px", zIndex: 0, opacity: 0.5 }} />}
        {restantesBloco > 2 && <div className="card" style={{ position: "absolute", inset: "16px -16px -16px -16px", zIndex: -1, opacity: 0.25 }} />}

        <div
          key={evento.id}
          className={`card ${animClass}`}
          style={{ position: "relative", zIndex: 1, padding: 0, overflow: "hidden", touchAction: "pan-y", userSelect: "none", ...swipeStyle }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          <div className="row gap2" style={{ justifyContent: "space-between", padding: "12px 18px", borderBottom: "1px solid var(--line-2)" }}>
            <span className="row gap2" style={{ fontSize: 11.5, color: "var(--muted)", fontFamily: "var(--mono)", flexWrap: "wrap" }}>
              <Icon name="user" size={13} /> {evento.papel === "operador" ? "OPERADOR" : evento.papel === "visitante" ? "VISITANTE NO POSTO" : `PESSOA-${String(evento.pessoa).padStart(3, "0")}`} · {evento.ini.toFixed(1)}s→{evento.fim.toFixed(1)}s · {fmtSeg(evento.fim - evento.ini)}
              {((evento.irmaos && evento.irmaos.length > 0) || evento.segundoAngulo) && (
                <span className="badge badge-purple" style={{ fontFamily: "var(--sans)" }}>
                  <Icon name="video" size={11} /> {(evento.irmaos?.length ?? 0) + (evento.segundoAngulo ? 1 : 0) + 1} ângulos
                </span>
              )}
            </span>
            <button
              data-no-drag
              onClick={() => { setResolvedKind("pular"); setPhase("leaving"); setTimeout(() => onPular(), 320); }}
              title="Pular este por enquanto — volta no fim do lote"
              disabled={phase !== "idle" || restantesBloco <= 1}
              className="row gap1"
              style={{ fontSize: 11.5, fontWeight: 700, border: "1px solid var(--line)", background: "#fff", color: "var(--muted)", borderRadius: 99, padding: "3px 10px", cursor: restantesBloco > 1 ? "pointer" : "not-allowed", opacity: restantesBloco > 1 ? 1 : 0.5 }}
            >
              <Icon name="rotate-cw" size={12} /> Pular
            </button>
          </div>

          {(evento.irmaos && evento.irmaos.length > 0) || evento.segundoAngulo ? (
            <FaixasMultiCamera evento={evento} />
          ) : (
            <FrameStripReal ativo={evento} />
          )}

          <div style={{ padding: "16px 20px 20px" }}>
            <div className="row gap2" style={{ marginBottom: 4 }}>
              <Prism size={24} ring />
              <span style={{ fontSize: 12.5, color: "var(--muted)" }}>O Prism interpretou como</span>
            </div>
            <div className="row gap2 wrap" style={{ alignItems: "center", marginBottom: 10 }}>
              {!corrigir ? (
                <span className="font-display" style={{ fontSize: 22, fontWeight: 700, color: "var(--ink)" }}>“{label}”</span>
              ) : (
                <div className="row gap2" style={{ width: "100%" }}>
                  <input className="field" list="labels-fila" autoFocus value={label} onChange={(e) => setLabel(e.target.value)} style={{ maxWidth: 360, fontSize: 16 }} />
                  <datalist id="labels-fila">{labels.map((l) => <option key={l} value={l} />)}</datalist>
                </div>
              )}
              {(
                <span className="badge badge-purple"><i style={{ width: 8, height: 8, borderRadius: 2, background: leanCor(evento.sugestao) }} /> {leanLabel(evento.sugestao)}</span>
              )}
            </div>
            <p className="pretty" style={{ fontSize: 13.5, color: "var(--text)", lineHeight: 1.5, marginBottom: 14, maxWidth: 620 }}>{evento.descricao}</p>

            <div className="row gap2" style={{ marginBottom: 18, maxWidth: 420 }}>
              <span style={{ fontSize: 11.5, color: "var(--muted)", width: 84 }}>confiança</span>
              <div className="grow track" style={{ height: 8 }}>
                <i style={{ width: `${conf}%`, background: resolvedKind && resolvedKind !== "descartar" && resolvedKind !== "pular" ? "linear-gradient(90deg,#34D399,#10B981)" : "var(--grad-cta)", transition: "width .6s cubic-bezier(.2,.8,.2,1)" }} />
              </div>
              <span className="tnum font-mono" style={{ fontSize: 12, color: resolvedKind && resolvedKind !== "descartar" && resolvedKind !== "pular" ? "var(--va)" : "var(--accent)", width: 38, textAlign: "right" }}>{conf}%</span>
            </div>

            {phase === "idle" && !corrigir && (
              <div className="row gap2 wrap">
                <button onClick={() => act("confirmar")} className="btn btn-ok btn-lg" style={{ flex: "1 1 200px" }}><Icon name="check" size={19} strokeWidth={2.6} /> Confirmar</button>
                <button onClick={() => setCorrigir(true)} className="btn btn-secondary btn-lg" style={{ flex: "1 1 140px" }}><Icon name="pencil" size={17} /> Corrigir</button>
                <button onClick={() => act("descartar")} className="btn btn-danger btn-lg" style={{ flex: "1 1 140px" }}><Icon name="x" size={18} strokeWidth={2.4} /> Descartar</button>
              </div>
            )}
            {phase === "idle" && corrigir && (
              <div className="row gap2 wrap">
                <button onClick={() => act("corrigir", label.trim())} disabled={!label.trim()} className="btn btn-primary btn-lg" style={{ flex: "1 1 200px" }}><Icon name="check" size={18} strokeWidth={2.5} /> Salvar correção</button>
                <button onClick={() => { setCorrigir(false); setLabel(evento.label); }} className="btn btn-ghost btn-lg">Cancelar</button>
              </div>
            )}
            {phase !== "idle" && (
              <div className="row gap2 anim-pop" style={{ fontSize: 15, fontWeight: 700, color: resolvedKind === "descartar" ? "var(--desp)" : resolvedKind === "pular" ? "var(--muted)" : "var(--va)", padding: "10px 0" }}>
                <Icon
                  name={resolvedKind === "descartar" ? "x-circle" : resolvedKind === "pular" ? "rotate-cw" : "check-circle-2"}
                  size={20}
                  strokeWidth={2.4}
                />
                {resolvedKind === "confirmar"
                  ? "Confirmado!"
                  : resolvedKind === "corrigir"
                  ? "Corrigido!"
                  : resolvedKind === "pular"
                  ? "Pulado — volta no fim do lote"
                  : "Descartado"}
                {resolvedKind === "confirmar" || resolvedKind === "corrigir" ? (
                  <span style={{ color: "var(--accent)" }}>+1 padrão aprendido</span>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </div>
      <p className="row gap2" style={{ justifyContent: "center", fontSize: 11.5, color: "var(--faint)", marginTop: 4, flexWrap: "wrap", textAlign: "center" }}>
        <Icon name="mouse-pointer-click" size={13} /> Um clique resolve. Arraste o card pro lado, ou toque <b style={{ color: "var(--muted)" }}>Pular</b>, pra adiar e voltar ao fim do lote.
      </p>
    </div>
  );
}

function BarraLote({ loteIdx, totalLotes, restantesBloco, naFilaAposBloco }: { loteIdx: number; totalLotes: number; restantesBloco: number; naFilaAposBloco: number }) {
  return (
    <Card style={{ padding: "10px 14px", background: "var(--soft)" }}>
      <div className="row gap2" style={{ alignItems: "center", flexWrap: "wrap" }}>
        <span className="row gap1" style={{ fontSize: 11.5, fontWeight: 700, color: "var(--accent-deep)", background: "var(--accent-soft)", border: "1px solid var(--p-200)", borderRadius: 99, padding: "3px 10px" }}>
          <Icon name="layers" size={12} /> Lote {loteIdx} de {totalLotes}
        </span>
        <span style={{ fontSize: 12.5, color: "var(--text)" }}>
          <b className="tnum" style={{ color: "var(--ink)" }}>{restantesBloco}</b> {restantesBloco === 1 ? "item neste lote" : "itens neste lote"}
        </span>
        <span className="grow" />
        <span className="row gap1" style={{ fontSize: 12, color: "var(--muted)" }}>
          <Icon name="inbox" size={12} />
          {naFilaAposBloco > 0
            ? <>+{naFilaAposBloco} aguardando próximo lote</>
            : <>último lote</>}
        </span>
      </div>
    </Card>
  );
}

function LoteConcluido({ loteIdx, totalLotes, restantesFila, tamanhoLote, onProximo }: { loteIdx: number; totalLotes: number; restantesFila: number; tamanhoLote: number; onProximo: () => void }) {
  const prox = Math.min(tamanhoLote, restantesFila);
  return (
    <Card style={{ padding: 0, overflow: "hidden" }} className="anim-fadeup">
      <div style={{ background: "linear-gradient(120deg, var(--accent-soft), #fff 80%)", padding: "26px 24px", position: "relative" }}>
        <div className="row gap3" style={{ alignItems: "center" }}>
          <span className="center" style={{ width: 48, height: 48, borderRadius: 14, background: "#fff", color: "var(--accent)", boxShadow: "var(--glow)" }}><Icon name="check-check" size={24} strokeWidth={2.4} /></span>
          <div className="grow">
            <h2 className="font-display" style={{ fontSize: 20, fontWeight: 700, color: "var(--ink)" }}>Lote {loteIdx} concluído</h2>
            <p style={{ fontSize: 13.5, color: "var(--muted)", marginTop: 3 }}>
              Faltam <b className="tnum" style={{ color: "var(--ink)" }}>{restantesFila}</b> {restantesFila === 1 ? "item" : "itens"} na fila — mais {loteIdx >= totalLotes - 1 ? "este último lote" : `${totalLotes - loteIdx} ${totalLotes - loteIdx === 1 ? "lote" : "lotes"}`} pra terminar.
            </p>
          </div>
          <Btn icon="arrow-right" onClick={onProximo}>
            Continuar com mais {prox}
          </Btn>
        </div>
      </div>
    </Card>
  );
}

function CardsGrid({ queue, onResolver, labels }: { queue: PendMock[]; onResolver: (ev: PendMock, k: "confirmar" | "corrigir" | "descartar", l?: string) => void; labels: string[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(min(100%, 320px),1fr))", gap: 14 }}>
      {queue.map((ev) => <CardEvento key={ev.id} ev={ev} onResolver={onResolver} labels={labels} />)}
    </div>
  );
}
function CardEvento({ ev, onResolver, labels }: { ev: PendMock; onResolver: (ev: PendMock, k: "confirmar" | "corrigir" | "descartar", l?: string) => void; labels: string[] }) {
  const [leaving, setLeaving] = useState<string | null>(null);
  const [corrigir, setCorrigir] = useState(false);
  const [label, setLabel] = useState(ev.label);
  function act(kind: "confirmar" | "corrigir" | "descartar", lbl?: string) { setLeaving(kind); setTimeout(() => onResolver(ev, kind, lbl), 360); }
  return (
    <div className={`card ${leaving ? (leaving === "descartar" ? "leave-r" : "leave-l") : "anim-fadeup"}`} style={{ padding: 0, overflow: "hidden" }}>
      <FrameReal id={ev.id} pessoa={ev.pessoa} height={130} />
      <div style={{ padding: 14 }}>
        <div className="row gap2" style={{ marginBottom: 6, justifyContent: "space-between" }}>
          <span className="font-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>P-{String(ev.pessoa).padStart(2, "0")} · {fmtSeg(ev.fim - ev.ini)}</span>
          <span className="badge badge-purple" style={{ fontSize: 10 }}>{Math.round(ev.conf * 100)}% conf.</span>
        </div>
        {!corrigir ? (
          <div className="font-display" style={{ fontSize: 15.5, fontWeight: 700, color: "var(--ink)", marginBottom: 4 }}>“{label}”</div>
        ) : (
          <input className="field" list="labels-cards" autoFocus value={label} onChange={(e) => setLabel(e.target.value)} style={{ marginBottom: 6, fontSize: 14 }} />
        )}
        <datalist id="labels-cards">{labels.map((l) => <option key={l} value={l} />)}</datalist>
        <p className="clamp2" style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.4, minHeight: 32, marginBottom: 10 }}>{ev.descricao}</p>
        {!corrigir ? (
          <div className="row gap1">
            <button onClick={() => act("confirmar")} className="btn btn-ok btn-sm grow"><Icon name="check" size={15} strokeWidth={2.6} /> Confirmar</button>
            <button onClick={() => setCorrigir(true)} className="btn btn-secondary btn-sm" title="Corrigir"><Icon name="pencil" size={14} /></button>
            <button onClick={() => act("descartar")} className="btn btn-danger btn-sm" title="Descartar"><Icon name="x" size={15} strokeWidth={2.4} /></button>
          </div>
        ) : (
          <div className="row gap1">
            <button onClick={() => act("corrigir", label.trim())} disabled={!label.trim()} className="btn btn-primary btn-sm grow"><Icon name="check" size={14} /> Salvar</button>
            <button onClick={() => { setCorrigir(false); setLabel(ev.label); }} className="btn btn-ghost btn-sm">Cancelar</button>
          </div>
        )}
      </div>
    </div>
  );
}

function FilaLimpa({ done, learned, go, proc }: { done: number; learned: number; go: Go; proc: ProcHeaderMock }) {
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ background: "var(--grad-brand)", padding: "32px 28px", color: "#fff", position: "relative", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: -80, right: -60, width: 280, height: 280, borderRadius: "50%", background: "radial-gradient(closest-side, rgba(167,139,250,.4), transparent)" }} />
        <div className="row gap3" style={{ position: "relative" }}>
          <Prism size={56} ring />
          <div>
            <h2 className="font-display" style={{ fontSize: 24, fontWeight: 700, color: "#fff" }}>Fila limpa! 🎉</h2>
            <p style={{ fontSize: 14, color: "rgba(255,255,255,.8)", marginTop: 4 }}>Você revisou tudo que precisava de olho humano. O resto, o Prism cuida sozinho.</p>
          </div>
        </div>
      </div>
      <div className="row" style={{ padding: 20, gap: 16, flexWrap: "wrap" }}>
        <StatBig valor={done} label="eventos revisados" icon="check-check" />
        <StatBig valor={learned} label="padrões aprendidos hoje" icon="sparkles" color="var(--accent)" />
        <StatBig valor={`+${Math.min(16, Math.round(done * 1.4))}%`} label="maturidade do Prism" icon="trending-up" color="var(--va)" />
        <div className="grow" />
        <div className="row gap2" style={{ alignSelf: "center" }}>
          <Btn variant="secondary" icon="layout-dashboard" onClick={() => go("processo", proc.id, "dashboard")}>Ver dashboard</Btn>
          <Btn icon="upload" onClick={() => go("processo", proc.id, "upload")}>Enviar novo vídeo</Btn>
        </div>
      </div>
    </Card>
  );
}
function StatBig({ valor, label, icon, color = "var(--ink)" }: { valor: number | string; label: string; icon: string; color?: string }) {
  return (
    <div className="row gap2" style={{ alignItems: "center" }}>
      <span className="center" style={{ width: 42, height: 42, borderRadius: 12, background: "var(--soft)", color }}><Icon name={icon} size={20} /></span>
      <div className="col" style={{ gap: 0 }}>
        <span className="font-display tnum" style={{ fontSize: 24, fontWeight: 700, color }}>{valor}</span>
        <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{label}</span>
      </div>
    </div>
  );
}
