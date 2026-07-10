// ============================================================
// Prism — painel slide-over (porte fiel de prism.jsx) com chat real.
// ============================================================
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import { Icon, Prism } from "../design/ui";
import type { ProcHeaderMock } from "../lib/adapt";

type Msg = { who: "me" | "prism"; text: string };

// Fase 25: o Chat do Prism está DESATIVADO ("em breve") p/ cortar tokens. A
// implementação real segue INTACTA em `PrismPanelOriginal` abaixo (nada foi
// removido); para reativar, o export volta a renderizá-la (e
// KV_PRISM_CHAT_ENABLE=on no backend).
export function PrismPanel({ open, onClose, scope, proc }: { open: boolean; onClose: () => void; scope: "global" | "processo"; proc?: ProcHeaderMock | null }) {
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(26,16,49,.3)", backdropFilter: "blur(2px)", zIndex: 90, opacity: open ? 1 : 0, pointerEvents: open ? "auto" : "none", transition: "opacity .25s" }} />
      <aside style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: "min(440px, 94vw)", background: "#fff", zIndex: 100, boxShadow: "-20px 0 60px -20px rgba(26,16,49,.4)", borderLeft: "1px solid var(--line)", transform: open ? "translateX(0)" : "translateX(100%)", transition: "transform .3s cubic-bezier(.3,.8,.3,1)", display: "flex", flexDirection: "column" }}>
        <header className="row gap2" style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
          <Prism size={34} ring />
          <div className="grow">
            <div className="font-display" style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>Prism</div>
            <div style={{ fontSize: 11.5, color: "var(--muted)" }}>{scope === "global" ? "visão geral da operação" : `inteligência · ${proc?.nome || ""}`}</div>
          </div>
          <button onClick={onClose} title="Fechar" className="center" style={{ width: 32, height: 32, borderRadius: 8, border: "none", background: "transparent", color: "var(--muted)" }}
            onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--line-2)")} onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}><Icon name="x" size={18} /></button>
        </header>
        <div className="grow center col gap2" style={{ padding: 28, textAlign: "center" }}>
          <Prism size={54} ring />
          <div className="font-display" style={{ fontSize: 18, fontWeight: 700, color: "var(--ink)", marginTop: 6 }}>Prism — em breve</div>
          <div className="pretty" style={{ fontSize: 13.5, color: "var(--muted)", lineHeight: 1.55, maxWidth: 320 }}>
            Estamos aprimorando o assistente do Prism. Em breve você poderá conversar sobre sua operação por aqui.
          </div>
        </div>
      </aside>
    </>
  );
}

function PrismPanelOriginal({ open, onClose, scope, proc }: { open: boolean; onClose: () => void; scope: "global" | "processo"; proc?: ProcHeaderMock | null }) {
  const processoId = scope === "processo" ? proc?.id ?? null : null;
  const prism = useMemo(() => api.prism(processoId), [processoId]);
  const [convId, setConvId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [texto, setTexto] = useState("");
  const [pensando, setPensando] = useState(false);
  const [sugestoes, setSugestoes] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Ao abrir / trocar escopo: garante conversa + carrega sugestões.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setMsgs([]);
    setTexto("");
    setConvId(null);
    (async () => {
      try {
        const lista = await prism.listarConversas();
        let cid = lista[0]?.id;
        if (!cid) cid = (await prism.criarConversa()).id;
        if (!alive) return;
        setConvId(cid);
        const conv = await prism.getConversa(cid);
        if (!alive) return;
        setMsgs(conv.mensagens.map((m) => ({ who: m.papel === "user" ? "me" : "prism", text: m.conteudo })));
      } catch { /* silencioso */ }
      try {
        const s = await prism.sugestoes([]);
        if (alive) setSugestoes(s.sugestoes || []);
      } catch { /* fallback vazio */ }
    })();
    return () => { alive = false; };
  }, [open, scope, processoId, prism]);

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [msgs, pensando]);

  async function novaConversa() {
    try {
      const c = await prism.criarConversa();
      setConvId(c.id);
      setMsgs([]);
      const s = await prism.sugestoes([]);
      setSugestoes(s.sugestoes || []);
    } catch { /* ignore */ }
  }

  async function enviar(t?: string) {
    const txt = (t || texto).trim();
    if (!txt || pensando) return;
    let cid = convId;
    if (!cid) { try { cid = (await prism.criarConversa()).id; setConvId(cid); } catch { return; } }
    setMsgs((m) => [...m, { who: "me", text: txt }]);
    setTexto("");
    setPensando(true);
    try {
      const r = await prism.enviarMensagem(cid!, txt);
      setMsgs((m) => [...m, { who: "prism", text: r.resposta }]);
    } catch (e) {
      setMsgs((m) => [...m, { who: "prism", text: `⚠️ ${(e as Error).message}` }]);
    } finally {
      setPensando(false);
    }
  }

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(26,16,49,.3)", backdropFilter: "blur(2px)", zIndex: 90, opacity: open ? 1 : 0, pointerEvents: open ? "auto" : "none", transition: "opacity .25s" }} />
      <aside style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: "min(440px, 94vw)", background: "#fff", zIndex: 100, boxShadow: "-20px 0 60px -20px rgba(26,16,49,.4)", borderLeft: "1px solid var(--line)", transform: open ? "translateX(0)" : "translateX(100%)", transition: "transform .3s cubic-bezier(.3,.8,.3,1)", display: "flex", flexDirection: "column" }}>
        <header className="row gap2" style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
          <Prism size={34} ring />
          <div className="grow">
            <div className="font-display" style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>Prism</div>
            <div style={{ fontSize: 11.5, color: "var(--muted)" }}>{scope === "global" ? "visão geral da operação" : `inteligência · ${proc?.nome || ""}`}</div>
          </div>
          <button onClick={novaConversa} title="Nova conversa" className="center" style={{ width: 32, height: 32, borderRadius: 8, border: "none", background: "transparent", color: "var(--muted)" }}
            onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--line-2)")} onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}><Icon name="plus" size={17} /></button>
          <button onClick={onClose} title="Fechar" className="center" style={{ width: 32, height: 32, borderRadius: 8, border: "none", background: "transparent", color: "var(--muted)" }}
            onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--line-2)")} onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}><Icon name="x" size={18} /></button>
        </header>

        <div ref={scrollRef} className="grow" style={{ overflowY: "auto", padding: 16 }}>
          {msgs.length === 0 && (
            <div className="row gap2" style={{ alignItems: "flex-start", marginBottom: 18 }}>
              <Prism size={28} ring />
              <div style={{ background: "var(--accent-soft)", border: "1px solid var(--line)", borderRadius: "14px 14px 14px 4px", padding: "11px 14px", fontSize: 13.5, color: "var(--ink)", lineHeight: 1.5 }}>
                {scope === "global"
                  ? <>Sou o <b>Prism</b>. Estou vendo <b>todos os seus processos</b> — posso comparar, priorizar e achar padrões entre eles.</>
                  : <>Sou o <b>Prism</b>. Posso te ajudar a entender onde o tempo da <b>{proc?.nome}</b> está indo, achar gargalos e ler seus indicadores.</>}
                <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>Sobre o que quer falar?</div>
              </div>
            </div>
          )}
          {msgs.map((m, i) =>
            m.who === "me" ? (
              <div key={i} className="row" style={{ justifyContent: "flex-end", marginBottom: 12 }}>
                <div style={{ maxWidth: "84%", background: "var(--accent)", color: "#fff", padding: "9px 13px", borderRadius: "14px 14px 4px 14px", fontSize: 13.5, lineHeight: 1.45 }}>{m.text}</div>
              </div>
            ) : (
              <div key={i} className="row gap2" style={{ alignItems: "flex-start", marginBottom: 14 }}>
                <Prism size={26} ring />
                <div className="prose-chat" style={{ maxWidth: "84%", background: "#fff", border: "1px solid var(--line)", borderRadius: "14px 14px 14px 4px", padding: "11px 14px", fontSize: 13.5, color: "var(--text)", lineHeight: 1.55 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                </div>
              </div>
            )
          )}
          {pensando && (
            <div className="row gap2" style={{ alignItems: "center" }}>
              <Prism size={26} ring />
              <div className="row gap2" style={{ background: "#fff", border: "1px solid var(--line)", borderRadius: 12, padding: "9px 13px", fontSize: 13, color: "var(--muted)" }}>
                <span className="spin" style={{ width: 14, height: 14, border: "2px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} /> Prism está pensando…
              </div>
            </div>
          )}

          {msgs.length === 0 && sugestoes.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--faint)", marginBottom: 8 }}>Sugestões</div>
              <div className="col" style={{ gap: 7 }}>
                {sugestoes.map((s) => (
                  <button key={s} onClick={() => enviar(s)} className="row gap2" style={{ textAlign: "left", border: "1px solid var(--line)", background: "var(--soft)", borderRadius: 10, padding: "9px 12px", fontSize: 13, color: "var(--text)" }}
                    onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "var(--p-200)")} onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "var(--line)")}>
                    <Icon name="sparkle" size={14} color="var(--accent)" /> <span className="grow">{s}</span> <Icon name="arrow-up-right" size={14} color="var(--faint)" />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div style={{ padding: 14, borderTop: "1px solid var(--line)" }}>
          <div className="row gap2" style={{ alignItems: "flex-end" }}>
            <textarea className="field" rows={1} value={texto} onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviar(); } }}
              placeholder={scope === "global" ? "Pergunte sobre o conjunto dos processos…" : "Pergunte ao Prism sobre esta operação…"} style={{ resize: "none", maxHeight: 120 }} />
            <button onClick={() => enviar()} disabled={!texto.trim() || pensando} className="btn btn-primary center" style={{ width: 42, height: 42, padding: 0, flex: "none" }}><Icon name="arrow-up" size={18} strokeWidth={2.5} /></button>
          </div>
        </div>
      </aside>
    </>
  );
}
