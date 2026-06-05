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
    </div>
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

export function Descricao({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const qc = useQueryClient();
  const det = useQuery({ queryKey: ["processo", proc.id], queryFn: () => api.processos.detalhe(proc.id) });
  const [texto, setTexto] = useState("");
  const [area, setArea] = useState("");
  const [saved, setSaved] = useState(false);
  const [seed, setSeed] = useState(false);
  useEffect(() => { if (det.data && !seed) { setTexto(det.data.descricao || ""); setArea(det.data.area || ""); setSeed(true); } }, [det.data, seed]);

  async function salvar() {
    await Promise.all([api.processos.setDescricao(proc.id, texto.trim()), api.processos.setArea(proc.id, area.trim() || null)]);
    qc.invalidateQueries({ queryKey: ["processo", proc.id] });
    qc.invalidateQueries({ queryKey: ["processos"] });
    setSaved(true);
    toast("Descrição salva — o Prism vai usar no contexto.", { icon: "check" });
  }

  return (
    <div style={{ maxWidth: 760, margin: "0 auto" }}>
      <Card style={{ padding: 28 }}>
        <h1 className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>Descrição do processo</h1>
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
        <textarea className="field" rows={9} value={texto} onChange={(e) => { setTexto(e.target.value); setSaved(false); }} style={{ resize: "vertical", lineHeight: 1.55 }}
          placeholder="Ex.: Os operadores retiram a bobina do estoque, levam até a prensa, posicionam o blank e acionam o ciclo. Depois conferem a peça e registram no terminal…" />
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
