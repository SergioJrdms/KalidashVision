// ============================================================
// Upload (pipeline real) + Descrição — porte fiel de extras.jsx.
// ============================================================
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Btn, Card, Icon, Prism, Ring, toast } from "../design/ui";
import type { Go } from "../design/Shell";
import type { ProcHeaderMock } from "../lib/adapt";
import type { JobStatus } from "../lib/types";

const ETAPAS = [
  { key: "setup", label: "Preparando vídeo" },
  { key: "deteccao", label: "Detectando e rastreando pessoas" },
  { key: "vlm", label: "Descrevendo ações com IA" },
  { key: "cluster", label: "Agrupando em comportamentos" },
  { key: "segmentar", label: "Formando eventos" },
  { key: "persistir", label: "Salvando na memória do Prism" },
  { key: "sugestoes", label: "Gerando sugestões Lean" },
  { key: "concluido", label: "Concluído" },
];
// etapas reais do backend que mapeiam para os passos visuais acima
const MAP_ETAPA: Record<string, number> = {
  setup: 0, deteccao: 1, vlm: 2, cluster: 3, segmentar: 4, persistir: 5,
  sugestoes: 6, lean: 6, perguntas: 6, concluido: 7,
};

export function Upload({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [job, setJob] = useState<JobStatus | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!job || job.status === "concluido" || job.status === "erro") return;
    let m404 = 0;
    const t = setInterval(async () => {
      try {
        const s = await api.jobs.status(job.id);
        m404 = 0;
        setJob(s);
        if (s.status === "concluido") { clearInterval(t); qc.invalidateQueries({ queryKey: ["dashboard", proc.id] }); qc.invalidateQueries({ queryKey: ["processos"] }); }
        if (s.status === "erro") clearInterval(t);
      } catch (e) {
        if ((e as Error).message.startsWith("404")) { m404 += 1; if (m404 >= 10) { clearInterval(t); setJob((j) => (j ? { ...j, status: "erro", erro: "O backend perdeu o job (uvicorn reiniciou?). Envie novamente." } : j)); } }
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job, proc.id, qc]);

  useEffect(() => { if (job?.status === "concluido") toast("Vídeo processado — o Prism aprendeu mais um turno.", { icon: "sparkles" }); }, [job?.status]);

  async function iniciar() {
    if (!file) return;
    setErro(null);
    setEnviando(true);
    try {
      const r = await api.videos.upload(proc.id, file);
      setJob({ id: r.job_id, processo_id: proc.id, status: "pendente", etapa_atual: "setup", progresso_pct: 3, mensagem: "Em fila" });
    } catch (e) {
      setErro((e as Error).message);
    } finally {
      setEnviando(false);
    }
  }

  if (job) return <ProgressoJob job={job} proc={proc} go={go} fileName={file?.name} />;

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <Card style={{ padding: 28 }}>
        <h1 className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>Enviar vídeo da operação</h1>
        <p style={{ fontSize: 13.5, color: "var(--muted)", marginTop: 6 }}>O Prism processa em alguns minutos. Você acompanha cada etapa por aqui.</p>

        <div className="soft" style={{ border: "1px solid var(--line)", borderRadius: 12, padding: "14px 16px", marginTop: 16, display: "flex", gap: 12 }}>
          <Prism size={30} ring />
          <p style={{ fontSize: 12.5, color: "var(--text)", lineHeight: 1.55 }}>
            <b style={{ color: "var(--ink)" }}>O que acontece:</b> a IA detecta e rastreia cada pessoa, descreve as ações em linguagem natural, agrupa em comportamentos e gera sugestões com base em <b>todos</b> os vídeos do processo. Quanto mais você envia, mais inteligente fica.
          </p>
        </div>

        <div
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) setFile(f); }}
          className="center click"
          style={{ marginTop: 18, border: `2px dashed ${drag ? "var(--accent)" : "var(--p-200)"}`, borderRadius: 16, padding: "44px 20px", background: drag ? "var(--accent-soft)" : "var(--soft)", transition: "all .15s", textAlign: "center" }}
        >
          <div>
            <div className="center" style={{ width: 52, height: 52, borderRadius: 14, background: "#fff", color: "var(--accent)", margin: "0 auto 12px", boxShadow: "var(--glow)" }}><Icon name="upload-cloud" size={24} /></div>
            {file ? (
              <p style={{ fontSize: 14, color: "var(--ink)" }}><b>{file.name}</b> <span style={{ color: "var(--muted)" }}>· {(file.size / 1048576).toFixed(1)} MB</span></p>
            ) : (
              <p style={{ fontSize: 14, color: "var(--text)" }}>Clique para selecionar ou <b>arraste o vídeo</b> aqui</p>
            )}
            <p style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 4 }}>MP4, MOV ou AVI · ideal 3–15 min de operação contínua</p>
          </div>
          <input ref={inputRef} type="file" accept="video/*" style={{ display: "none" }} onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </div>

        {erro && <p style={{ fontSize: 12.5, color: "var(--desp)", marginTop: 10 }}>{erro}</p>}

        <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 20 }}>
          {file && <Btn variant="ghost" onClick={() => setFile(null)}>Trocar</Btn>}
          <Btn disabled={!file || enviando} icon="play" onClick={iniciar}>{enviando ? "Enviando…" : "Iniciar análise"}</Btn>
        </div>
      </Card>

      <UploadParTeste proc={proc} go={go} />
    </div>
  );
}

// ── MODO TESTE (temporário): subir um PAR cam1+cam2 do PC, sem o Pi ─────────
// Usa exatamente o caminho do edge: upload com cam_id → inbox `segmentos`
// (gravado_em lido do nome seg_YYYYMMDD_HHMMSS) → "processar lote" pareia e
// dispara o dual-angle com zonas/operador. Para retirar depois: apagar este
// componente e a linha <UploadParTeste/> acima (+ api.videos.uploadSegmento).
const RE_SEG_TOKEN = /seg_(\d{8})_(\d{6})/;

function UploadParTeste({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const [f1, setF1] = useState<File | null>(null);
  const [f2, setF2] = useState<File | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const ref1 = useRef<HTMLInputElement>(null);
  const ref2 = useRef<HTMLInputElement>(null);

  function tokenDe(f: File | null): string | null {
    const m = f ? RE_SEG_TOKEN.exec(f.name) : null;
    return m ? `${m[1]}_${m[2]}` : null;
  }
  const t1 = tokenDe(f1);
  const t2 = tokenDe(f2);
  const nomesOk = !!(t1 && t2);
  // Pareamento tolera ~6 min entre os relógios dos nomes.
  const paream = (() => {
    if (!t1 || !t2) return false;
    const p = (t: string) => {
      const d = t.split("_");
      return new Date(+d[0].slice(0, 4), +d[0].slice(4, 6) - 1, +d[0].slice(6, 8),
        +d[1].slice(0, 2), +d[1].slice(2, 4), +d[1].slice(4, 6)).getTime();
    };
    return Math.abs(p(t1) - p(t2)) <= 360_000;
  })();

  async function enviarPar() {
    if (!f1 || !f2) return;
    setErro(null);
    setEnviando(true);
    try {
      await api.videos.uploadSegmento(proc.id, f1, "cam1");
      await api.videos.uploadSegmento(proc.id, f2, "cam2");
      const r = await api.processos.processarLote(proc.id);
      toast(`Par enviado — ${r.itens} item(ns) na fila de processamento.`, { icon: "check" });
      setF1(null); setF2(null);
      go("processo", proc.id, "fila");
    } catch (e) {
      setErro((e as Error).message);
    } finally {
      setEnviando(false);
    }
  }

  const Slot = ({ f, cam, onPick, onClear, inputRef }: { f: File | null; cam: string; onPick: () => void; onClear: () => void; inputRef: React.RefObject<HTMLInputElement | null> }) => (
    <div onClick={() => !f && onPick()} className={f ? "" : "click"}
      style={{ flex: "1 1 220px", border: "1.5px dashed var(--line)", borderRadius: 12, padding: "14px 14px", background: "var(--soft)", textAlign: "center" }}>
      <span className="badge badge-purple" style={{ fontSize: 10 }}>{cam.replace(/^cam/i, "Cam ")}</span>
      {f ? (
        <div style={{ marginTop: 6 }}>
          <p style={{ fontSize: 12.5, color: "var(--ink)", wordBreak: "break-all" }}><b>{f.name}</b></p>
          <p style={{ fontSize: 11, color: "var(--muted)" }}>{(f.size / 1048576).toFixed(1)} MB · <button onClick={(e) => { e.stopPropagation(); onClear(); }} style={{ border: "none", background: "none", color: "var(--accent)", cursor: "pointer", fontSize: 11 }}>trocar</button></p>
        </div>
      ) : (
        <p style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 6 }}>Selecionar segmento da {cam.replace(/^cam/i, "câmera ")}</p>
      )}
    </div>
  );

  return (
    <Card style={{ padding: 22, marginTop: 16, border: "1px dashed var(--p-200)" }}>
      <div className="row gap2" style={{ alignItems: "center" }}>
        <Icon name="flask-conical" size={16} color="var(--accent)" />
        <h2 className="font-display" style={{ fontSize: 15.5, fontWeight: 700 }}>Modo teste — par de câmeras (sem o Pi)</h2>
      </div>
      <p style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 4, lineHeight: 1.5 }}>
        Sobe um segmento da <b>cam1</b> + um da <b>cam2</b> direto do seu PC, pelo mesmo caminho do
        Raspberry (inbox → pareamento → análise dual-angle com zonas). Os nomes precisam ter o
        carimbo <code className="font-mono">seg_AAAAMMDD_HHMMSS</code> com relógios até 6 min de distância — é
        por ele que o par se encontra e se alinha.
      </p>
      <div className="row gap2 wrap" style={{ marginTop: 12 }}>
        <Slot f={f1} cam="cam1" onPick={() => ref1.current?.click()} onClear={() => setF1(null)} inputRef={ref1} />
        <Slot f={f2} cam="cam2" onPick={() => ref2.current?.click()} onClear={() => setF2(null)} inputRef={ref2} />
      </div>
      <input ref={ref1} type="file" accept="video/*" style={{ display: "none" }} onChange={(e) => setF1(e.target.files?.[0] || null)} />
      <input ref={ref2} type="file" accept="video/*" style={{ display: "none" }} onChange={(e) => setF2(e.target.files?.[0] || null)} />

      {f1 && f2 && !nomesOk && (
        <p style={{ fontSize: 12, color: "var(--desp)", marginTop: 10 }}>
          Os nomes precisam conter <code className="font-mono">seg_AAAAMMDD_HHMMSS</code> (ex.:
          <code className="font-mono"> seg_20260715_080001_roi.mp4</code>) — sem isso o par não se forma. Renomeie os arquivos e selecione de novo.
        </p>
      )}
      {nomesOk && !paream && (
        <p style={{ fontSize: 12, color: "var(--desp)", marginTop: 10 }}>
          Os relógios dos nomes estão a mais de 6 minutos um do outro — o pareamento não vai juntá-los. Use o par gravado do mesmo instante.
        </p>
      )}
      {erro && <p style={{ fontSize: 12, color: "var(--desp)", marginTop: 10 }}>{erro}</p>}

      <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
        <Btn size="sm" disabled={!f1 || !f2 || !nomesOk || !paream || enviando} icon="play" onClick={enviarPar}>
          {enviando ? "Enviando par…" : "Enviar par e processar"}
        </Btn>
      </div>
      <p style={{ fontSize: 11, color: "var(--faint)", marginTop: 8 }}>
        O progresso aparece na aba <b>Fila</b> (você será levado pra lá). Ferramenta temporária de teste.
      </p>
    </Card>
  );
}

function ProgressoJob({ job, proc, go, fileName }: { job: JobStatus; proc: ProcHeaderMock; go: Go; fileName?: string }) {
  const done = job.status === "concluido";
  const erro = job.status === "erro";
  const etapaIdx = done ? ETAPAS.length - 1 : MAP_ETAPA[job.etapa_atual] ?? 0;
  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <Card style={{ padding: 28 }}>
        <div className="row gap3" style={{ marginBottom: 4 }}>
          <Ring pct={job.progresso_pct} size={52} stroke={6} animate={false}>
            {done ? <Icon name="check" size={22} color="var(--va)" strokeWidth={2.6} /> : <Prism size={28} />}
          </Ring>
          <div>
            <h1 className="font-display" style={{ fontSize: 21, fontWeight: 700 }}>{erro ? "Falha no processamento" : done ? "Processamento concluído" : "O Prism está analisando seu vídeo"}</h1>
            <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 2 }}>{erro ? job.erro : done ? "Tudo pronto — as análises já estão no dashboard." : `${fileName || "vídeo"} · ${job.mensagem || "pode deixar a aba aberta"}`}</p>
          </div>
        </div>

        <div className="track" style={{ height: 9, marginTop: 18 }}><i style={{ width: `${job.progresso_pct}%`, background: erro ? "var(--desp)" : done ? "linear-gradient(90deg,#34D399,#10B981)" : "var(--grad-cta)", transition: "width .4s" }} /></div>

        <ul className="col" style={{ gap: 2, listStyle: "none", padding: 0, margin: "22px 0 0" }}>
          {ETAPAS.map((e, i) => {
            const st = done || i < etapaIdx ? "done" : i === etapaIdx ? "active" : "pending";
            return (
              <li key={e.key} className="row gap3" style={{ padding: "9px 10px", borderRadius: 10, background: st === "active" ? "var(--accent-soft)" : "transparent" }}>
                <span className="center" style={{ width: 22, height: 22, flex: "none" }}>
                  {st === "done" && <span className="center" style={{ width: 20, height: 20, borderRadius: 99, background: "var(--va)", color: "#fff" }}><Icon name="check" size={13} strokeWidth={3} /></span>}
                  {st === "active" && <span className="spin" style={{ width: 18, height: 18, border: "2.5px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} />}
                  {st === "pending" && <span style={{ width: 16, height: 16, borderRadius: 99, border: "2px solid var(--line)" }} />}
                </span>
                <span style={{ fontSize: 13.5, fontWeight: st === "active" ? 700 : 500, color: st === "pending" ? "var(--faint)" : st === "active" ? "var(--accent-deep)" : "var(--text)" }}>{e.label}</span>
              </li>
            );
          })}
        </ul>

        {done && (
          <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 20 }}>
            <Btn variant="secondary" icon="git-pull-request-arrow" onClick={() => go("processo", proc.id, "validacao")}>Validar eventos novos</Btn>
            <Btn icon="layout-dashboard" onClick={() => go("processo", proc.id, "dashboard")}>Ver dashboard</Btn>
          </div>
        )}
      </Card>
    </div>
  );
}

type OnbTurn = { pergunta: string; resposta: string };

export function Descricao({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const qc = useQueryClient();
  const det = useQuery({ queryKey: ["processo", proc.id], queryFn: () => api.processos.detalhe(proc.id) });
  const [modoEdicao, setModoEdicao] = useState<"wizard" | "manual" | null>(null);

  // Modo inicial é decidido pelo estado atual do processo: sem descrição → wizard.
  const modo = modoEdicao ?? (det.data && (det.data.descricao || "").trim() ? "manual" : "wizard");

  if (det.isLoading) {
    return <div className="center" style={{ padding: 60 }}><span className="spin" style={{ width: 22, height: 22, border: "3px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} /></div>;
  }

  if (modo === "wizard") {
    return <OnboardingWizard proc={proc} go={go} areaInicial={det.data?.area || ""} onCancelar={() => setModoEdicao("manual")} />;
  }
  return <DescricaoManual proc={proc} go={go} descricaoAtual={det.data?.descricao || ""} areaAtual={det.data?.area || ""} onRefazerOnboarding={() => setModoEdicao("wizard")} onSalvar={() => {
    qc.invalidateQueries({ queryKey: ["processo", proc.id] });
    qc.invalidateQueries({ queryKey: ["processos"] });
  }} />;
}

// ── Wizard adaptativo ──────────────────────────────────────
function OnboardingWizard({ proc, go, areaInicial, onCancelar }: { proc: ProcHeaderMock; go: Go; areaInicial: string; onCancelar: () => void }) {
  const qc = useQueryClient();
  const [historico, setHistorico] = useState<OnbTurn[]>([]);
  const [perguntaAtual, setPerguntaAtual] = useState<{ pergunta: string; motivo: string; chips: string[] } | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [texto, setTexto] = useState("");
  const [consolidada, setConsolidada] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function pedirProxima(novoHistorico: OnbTurn[]) {
    setCarregando(true);
    setErro(null);
    try {
      const r = await api.processos.onboardingProxima(proc.id, novoHistorico, areaInicial || null);
      if (r.completo) {
        setConsolidada(r.descricao_consolidada);
        setPerguntaAtual(null);
      } else {
        setPerguntaAtual({
          pergunta: r.pergunta,
          motivo: r.motivo || "",
          chips: r.respostas_rapidas && r.respostas_rapidas.length >= 2 ? r.respostas_rapidas : [],
        });
      }
    } catch (e) {
      setErro((e as Error).message);
    } finally {
      setCarregando(false);
    }
  }

  // Dispara a 1ª pergunta na montagem.
  useEffect(() => { void pedirProxima([]); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [historico, perguntaAtual, consolidada]);

  function responder(resposta: string) {
    if (!perguntaAtual || !resposta.trim()) return;
    const novo: OnbTurn[] = [...historico, { pergunta: perguntaAtual.pergunta, resposta: resposta.trim() }];
    setHistorico(novo);
    setPerguntaAtual(null);
    setTexto("");
    void pedirProxima(novo);
  }

  async function salvarConsolidada(textoFinal: string) {
    if (!textoFinal.trim()) return;
    setSalvando(true);
    try {
      await api.processos.setDescricao(proc.id, textoFinal.trim());
      qc.invalidateQueries({ queryKey: ["processo", proc.id] });
      qc.invalidateQueries({ queryKey: ["processos"] });
      toast("Descrição salva — o Prism vai usar no contexto.", { icon: "check" });
      go("processo", proc.id, "upload");
    } catch (e) {
      setErro((e as Error).message);
      setSalvando(false);
    }
  }

  return (
    <div style={{ maxWidth: 760, margin: "0 auto" }}>
      <Card style={{ padding: 0, overflow: "hidden" }}>
        {/* cabeçalho */}
        <div className="row gap2" style={{ padding: "16px 22px", borderBottom: "1px solid var(--line)", background: "var(--accent-soft)" }}>
          <Prism size={32} ring />
          <div className="grow">
            <h1 className="font-display" style={{ fontSize: 17, fontWeight: 700, color: "var(--ink)" }}>Conhecendo o seu processo</h1>
            <p style={{ fontSize: 12.5, color: "var(--accent-deep)", marginTop: 1 }}>
              Conversa rápida — vou te perguntar o essencial para entender essa linha.
            </p>
          </div>
          <span className="badge badge-purple">{historico.length} resposta{historico.length === 1 ? "" : "s"}</span>
        </div>

        {/* conversa */}
        <div ref={scrollRef} className="col" style={{ padding: 20, gap: 14, maxHeight: 560, overflowY: "auto" }}>
          {historico.map((h, i) => (
            <div key={i} className="col" style={{ gap: 8 }}>
              <Bolha who="prism" text={h.pergunta} />
              <Bolha who="me" text={h.resposta} />
            </div>
          ))}

          {erro && (
            <div className="row gap2" style={{ background: "var(--desp-bg)", border: "1px solid var(--desp)", borderRadius: 10, padding: "9px 12px", fontSize: 12.5, color: "var(--desp)" }}>
              <Icon name="alert-triangle" size={14} /> {erro}
              <span className="grow" />
              <button onClick={() => pedirProxima(historico)} className="row gap1" style={{ border: "none", background: "transparent", color: "var(--desp)", fontWeight: 700, fontSize: 12 }}>
                <Icon name="rotate-ccw" size={13} /> Tentar de novo
              </button>
            </div>
          )}

          {carregando && (
            <div className="row gap2" style={{ alignItems: "center", fontSize: 12.5, color: "var(--muted)" }}>
              <Prism size={24} ring />
              <span className="spin" style={{ width: 12, height: 12, border: "2px solid var(--p-100)", borderTopColor: "var(--accent)", borderRadius: "50%" }} />
              {consolidada === null && historico.length === 0 ? "Pensando na primeira pergunta…" : carregando && perguntaAtual === null && consolidada === null ? "Pensando na próxima pergunta…" : "Consolidando o que você contou…"}
            </div>
          )}

          {!carregando && perguntaAtual && (
            <div className="col anim-fadeup" style={{ gap: 10 }}>
              <Bolha who="prism" text={perguntaAtual.pergunta} />
              {perguntaAtual.motivo && (
                <span style={{ fontSize: 11.5, color: "var(--faint)", paddingLeft: 38, fontStyle: "italic" }}>{perguntaAtual.motivo}</span>
              )}
              {perguntaAtual.chips.length > 0 && (
                <div className="row wrap gap2" style={{ paddingLeft: 38 }}>
                  {perguntaAtual.chips.map((c) => (
                    <button key={c} onClick={() => responder(c)} className="row gap1" style={{ padding: "7px 14px", borderRadius: 99, border: "1px solid var(--p-200)", background: "#fff", color: "var(--accent-deep)", fontSize: 13, fontWeight: 600, transition: "all .15s" }}
                      onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--accent-soft)")} onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "#fff")}>
                      {c}
                    </button>
                  ))}
                </div>
              )}
              <div className="row gap2" style={{ paddingLeft: 38, alignItems: "flex-end" }}>
                <textarea className="field" rows={1} value={texto} onChange={(e) => setTexto(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && texto.trim()) { e.preventDefault(); responder(texto); } }}
                  placeholder="…ou responda com suas palavras" style={{ resize: "none", maxHeight: 120, fontSize: 13.5 }} />
                <Btn variant="primary" disabled={!texto.trim()} onClick={() => responder(texto)} icon="arrow-up">Enviar</Btn>
              </div>
            </div>
          )}

          {!carregando && consolidada !== null && (
            <ConsolidacaoBox texto={consolidada} salvando={salvando} onSalvar={salvarConsolidada} />
          )}
        </div>

        {/* rodapé */}
        <div className="row" style={{ justifyContent: "space-between", padding: "10px 18px", borderTop: "1px solid var(--line-2)", background: "var(--soft)", fontSize: 11.5, color: "var(--muted)" }}>
          <span className="row gap1"><Icon name="info" size={12} /> As respostas viram contexto permanente do Prism para essa linha.</span>
          <button onClick={onCancelar} style={{ border: "none", background: "none", color: "var(--muted)", fontSize: 11.5, fontWeight: 600, padding: 0 }}>
            prefiro escrever do zero
          </button>
        </div>
      </Card>
    </div>
  );
}

function Bolha({ who, text }: { who: "me" | "prism"; text: string }) {
  if (who === "me") {
    return <div className="row" style={{ justifyContent: "flex-end" }}><div style={{ maxWidth: "78%", background: "var(--accent)", color: "#fff", padding: "8px 13px", borderRadius: "14px 14px 4px 14px", fontSize: 13.5, lineHeight: 1.45 }}>{text}</div></div>;
  }
  return (
    <div className="row gap2" style={{ alignItems: "flex-start" }}>
      <Prism size={26} ring />
      <div style={{ maxWidth: "82%", background: "var(--soft)", border: "1px solid var(--line)", padding: "9px 13px", borderRadius: "14px 14px 14px 4px", fontSize: 13.5, color: "var(--ink)", lineHeight: 1.5 }}>{text}</div>
    </div>
  );
}

function ConsolidacaoBox({ texto, salvando, onSalvar }: { texto: string; salvando: boolean; onSalvar: (t: string) => void }) {
  const [editado, setEditado] = useState(texto);
  return (
    <div className="col anim-fadeup" style={{ gap: 12 }}>
      <div className="row gap2" style={{ alignItems: "flex-start" }}>
        <span className="center" style={{ width: 28, height: 28, borderRadius: 9, background: "var(--va-bg)", color: "var(--va)", flex: "none" }}><Icon name="check" size={15} strokeWidth={2.6} /></span>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>Acho que entendi sua linha.</div>
          <p style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>Veja se a descrição abaixo bate com a realidade. Você pode refinar antes de salvar.</p>
        </div>
      </div>
      <textarea className="field" rows={10} value={editado} onChange={(e) => setEditado(e.target.value)} style={{ resize: "vertical", lineHeight: 1.55, fontSize: 13.5 }} />
      <div className="row gap2" style={{ justifyContent: "flex-end" }}>
        <Btn variant="primary" icon="save" disabled={!editado.trim() || salvando} onClick={() => onSalvar(editado)}>
          {salvando ? "Salvando…" : "Salvar e ir para o upload"}
        </Btn>
      </div>
    </div>
  );
}

// ── Edição manual (estado anterior preservado) ─────────────
function DescricaoManual({ proc, go, descricaoAtual, areaAtual, onRefazerOnboarding, onSalvar }: { proc: ProcHeaderMock; go: Go; descricaoAtual: string; areaAtual: string; onRefazerOnboarding: () => void; onSalvar: () => void }) {
  const [texto, setTexto] = useState(descricaoAtual);
  const [area, setArea] = useState(areaAtual);
  const [saved, setSaved] = useState(false);
  const [importando, setImportando] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function salvar() {
    await Promise.all([api.processos.setDescricao(proc.id, texto.trim()), api.processos.setArea(proc.id, area.trim() || null)]);
    onSalvar();
    setSaved(true);
    toast("Descrição salva — o Prism vai usar no contexto.", { icon: "check" });
  }

  async function importarArquivo(file: File) {
    setImportando(file.name);
    try {
      const r = await api.processos.extrairDescricaoArquivo(proc.id, file);
      const extraido = (r.texto || "").trim();
      if (!extraido) {
        toast("Não consegui ler texto desse arquivo.", { icon: "alert-triangle", color: "#F8B4B6" });
        return;
      }
      // Se já há conteúdo digitado, anexa preservando o que o gestor escreveu.
      // Se está vazio, simplesmente preenche.
      setTexto((prev) => {
        const base = prev.trim();
        return base ? `${base}\n\n${extraido}` : extraido;
      });
      setSaved(false);
      toast(`Importado de "${file.name}".`, { icon: "check" });
    } catch (e) {
      toast(`Falha ao importar: ${(e as Error).message}`, { icon: "alert-triangle", color: "#F8B4B6", duration: 4000 });
    } finally {
      setImportando(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div style={{ maxWidth: 760, margin: "0 auto" }}>
      <Card style={{ padding: 28 }}>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 4, gap: 8, flexWrap: "wrap" }}>
          <h1 className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>Descrição do processo</h1>
          <button onClick={onRefazerOnboarding} className="row gap1" style={{ border: "1px solid var(--p-200)", background: "var(--accent-soft)", color: "var(--accent-deep)", borderRadius: 99, padding: "5px 12px", fontSize: 12, fontWeight: 600 }}>
            <Icon name="sparkles" size={13} /> Refazer com o Prism
          </button>
        </div>
        <p className="pretty" style={{ fontSize: 13.5, color: "var(--muted)", marginTop: 6 }}>
          Conte como o processo funciona: o que os operadores fazem, em que ordem, em quais estações. O Prism usa esse texto para reconhecer melhor os comportamentos esperados e sinalizar o que é incomum.
        </p>
        <div className="soft" style={{ border: "1px solid var(--line)", borderRadius: 12, padding: "12px 14px", margin: "16px 0", display: "flex", gap: 10 }}>
          <Icon name="lightbulb" size={18} color="var(--accent)" style={{ flex: "none", marginTop: 1 }} />
          <p style={{ fontSize: 12.5, color: "var(--text)", lineHeight: 1.5 }}>
            Esse texto entra nos prompts da IA. Ele ajuda a (1) reconhecer os comportamentos esperados, (2) usar o vocabulário do seu domínio nos labels e (3) sinalizar o que está fora do fluxo.
          </p>
        </div>
        <label className="label">Área (opcional)</label>
        <input className="field" value={area} onChange={(e) => { setArea(e.target.value); setSaved(false); }} placeholder="Ex.: Estamparia" list="areas-desc" style={{ maxWidth: 300, marginBottom: 14 }} />
        <datalist id="areas-desc">{["Estamparia", "Montagem", "Logística", "Soldagem", "Usinagem", "Embalagem", "Picking", "Pintura", "Qualidade"].map((a) => <option key={a} value={a} />)}</datalist>

        {/* Barra de ações da descrição: importar de arquivo + dica de "sem limite" */}
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
          <label className="label" style={{ margin: 0 }}>Descrição (sem limite de tamanho — pode escrever à vontade)</label>
          <div className="row gap2">
            <input ref={inputRef} type="file" accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown" style={{ display: "none" }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void importarArquivo(f); }} />
            <Btn variant="secondary" size="sm" icon="upload" disabled={!!importando} onClick={() => inputRef.current?.click()}>
              {importando ? "Importando…" : "Importar arquivo"}
            </Btn>
          </div>
        </div>
        <textarea className="field" rows={14} value={texto} onChange={(e) => { setTexto(e.target.value); setSaved(false); }} style={{ resize: "vertical", lineHeight: 1.55, minHeight: 280 }}
          placeholder="Ex.: Os operadores retiram a bobina do estoque, levam até a prensa, posicionam o blank e acionam o ciclo. Depois conferem a peça e registram no terminal… (ou importe um PDF/DOCX/TXT acima — o texto entra aqui pra você revisar.)" />
        <div className="row" style={{ justifyContent: "space-between", marginTop: 6, alignItems: "center", flexWrap: "wrap", gap: 8 }}>
          <span style={{ fontSize: 11.5, color: "var(--faint)" }}>
            {texto.length.toLocaleString("pt-BR")} caracteres · sem limite
          </span>
          <span className="row gap1" style={{ fontSize: 11.5, color: "var(--faint)" }}>
            <Icon name="file-text" size={12} /> Aceita PDF, DOCX, TXT, MD (até 20 MB).
          </span>
        </div>
        <div className="row" style={{ justifyContent: "space-between", marginTop: 16, alignItems: "center" }}>
          <span className="row gap1" style={{ fontSize: 12.5, color: saved ? "var(--va)" : "var(--faint)" }}>{saved && <><Icon name="check" size={14} /> salvo</>}</span>
          <div className="row gap2">
            <Btn variant="ghost" onClick={() => go("processo", proc.id, "upload")}>Pular para o upload</Btn>
            <Btn icon="save" onClick={salvar}>Salvar descrição</Btn>
          </div>
        </div>
      </Card>
    </div>
  );
}
