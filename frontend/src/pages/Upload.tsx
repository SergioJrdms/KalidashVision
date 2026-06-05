import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { Btn, Card, Icon, Spinner } from "../components/UIKit";
import type { JobStatus } from "../lib/types";

const ETAPAS: { key: string; label: string }[] = [
  { key: "setup", label: "Preparando vídeo" },
  { key: "deteccao", label: "Detectando e rastreando pessoas" },
  { key: "vlm", label: "Descrevendo ações com IA" },
  { key: "cluster", label: "Agrupando em comportamentos" },
  { key: "segmentar", label: "Formando eventos" },
  { key: "persistir", label: "Salvando na memória do Prism" },
  { key: "sugestoes", label: "Gerando sugestões Lean" },
  { key: "lean", label: "Atualizando categorias Lean" },
  { key: "perguntas", label: "Formulando perguntas" },
  { key: "concluido", label: "Concluído" },
];

export default function Upload() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
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
        if (s.status === "concluido") {
          clearInterval(t);
          setTimeout(() => nav(`/processos/${id}/dashboard`), 800);
        }
        if (s.status === "erro") clearInterval(t);
      } catch (e) {
        if ((e as Error).message.startsWith("404")) {
          m404 += 1;
          if (m404 >= 10) {
            clearInterval(t);
            setJob({
              ...job,
              status: "erro",
              erro:
                "O backend perdeu este job — provavelmente o uvicorn reiniciou. Rode sem --reload e envie de novo.",
              mensagem: "Job perdido",
            });
          }
        }
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job, id, nav]);

  async function enviar() {
    if (!file || !id) return;
    setErro(null);
    setUploading(true);
    try {
      const r = await api.videos.upload(id, file);
      setJob({
        id: r.job_id,
        processo_id: id,
        status: "pendente",
        etapa_atual: "setup",
        progresso_pct: 0,
        mensagem: "Em fila",
      });
    } catch (e) {
      setErro((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  }

  if (job) return <Progresso job={job} />;

  return (
    <div className="col" style={{ gap: 18, maxWidth: 780, margin: "0 auto" }}>
      <Card style={{ padding: 26 }}>
        <h1 className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>
          Enviar vídeo da operação
        </h1>
        <p style={{ fontSize: 13.5, color: "var(--muted)", marginTop: 6 }}>
          O processamento leva alguns minutos. Você acompanha cada etapa abaixo.
        </p>

        <div
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          className="click center"
          style={{
            marginTop: 18,
            padding: 32,
            border: `2px dashed ${drag ? "var(--accent)" : "var(--p-200)"}`,
            background: drag ? "var(--accent-soft)" : "var(--soft)",
            borderRadius: 16,
            transition: "all .15s",
            flexDirection: "column",
            gap: 10,
          }}
        >
          <span
            style={{
              width: 54,
              height: 54,
              borderRadius: "50%",
              background: "#fff",
              border: "1px solid var(--line)",
              display: "grid",
              placeItems: "center",
            }}
          >
            <Icon name="upload-cloud" size={26} color="var(--accent)" />
          </span>
          <div style={{ fontSize: 14, color: "var(--text)" }}>
            {file ? (
              <>
                <b style={{ color: "var(--ink)" }}>{file.name}</b> ·{" "}
                <span style={{ color: "var(--muted)" }}>
                  {(file.size / 1024 / 1024).toFixed(1)} MB
                </span>
              </>
            ) : (
              <>
                Clique para selecionar ou{" "}
                <b style={{ color: "var(--accent-deep)" }}>arraste o vídeo aqui</b>
              </>
            )}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--faint)" }}>MP4, MOV ou AVI</div>
          <input
            ref={inputRef}
            type="file"
            accept="video/*"
            style={{ display: "none" }}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </div>

        {erro && (
          <div
            style={{
              marginTop: 14,
              fontSize: 13,
              color: "var(--desp)",
              background: "var(--desp-bg)",
              border: "1px solid rgba(229,72,77,.2)",
              borderRadius: 10,
              padding: "8px 11px",
            }}
          >
            {erro}
          </div>
        )}

        <div className="row gap2" style={{ marginTop: 18, justifyContent: "flex-end" }}>
          {file && (
            <Btn variant="ghost" onClick={() => setFile(null)}>
              Trocar
            </Btn>
          )}
          <Btn disabled={!file || uploading} onClick={enviar} icon="play">
            {uploading ? "Enviando..." : "Iniciar análise"}
          </Btn>
        </div>
      </Card>
    </div>
  );
}

function Progresso({ job }: { job: JobStatus }) {
  const erro = job.status === "erro";
  const concluido = job.status === "concluido";

  function statusEtapa(key: string): "done" | "active" | "pending" {
    if (concluido) return "done";
    const order = ETAPAS.map((e) => e.key);
    const i = order.indexOf(key);
    const cur = order.indexOf(job.etapa_atual || "setup");
    if (i < cur) return "done";
    if (i === cur) return "active";
    return "pending";
  }

  return (
    <div className="col" style={{ gap: 18, maxWidth: 780, margin: "0 auto" }}>
      <Card style={{ padding: 26 }}>
        <h2 className="font-display" style={{ fontSize: 20, fontWeight: 700 }}>
          {erro
            ? "Erro no processamento"
            : concluido
              ? "Processamento concluído"
              : "Processando seu vídeo"}
        </h2>
        <p style={{ fontSize: 13.5, color: "var(--muted)", marginTop: 6 }}>
          {erro
            ? "Algo deu errado. Veja o detalhe abaixo."
            : concluido
              ? "Redirecionando para o dashboard..."
              : "Pode levar alguns minutos — deixe a aba aberta."}
        </p>

        <div style={{ marginTop: 20 }}>
          <div
            className="row"
            style={{ justifyContent: "space-between", fontSize: 12, color: "var(--muted)", marginBottom: 6 }}
          >
            <span>{job.mensagem || "Trabalhando..."}</span>
            <span className="font-mono tnum">{job.progresso_pct}%</span>
          </div>
          <div className="track" style={{ height: 10 }}>
            <i
              style={{
                width: `${Math.max(2, job.progresso_pct)}%`,
                background: erro ? "var(--desp)" : "var(--grad-cta)",
              }}
            />
          </div>
        </div>

        <ul className="col gap2" style={{ marginTop: 24, listStyle: "none", padding: 0 }}>
          {ETAPAS.map((e) => {
            const s = statusEtapa(e.key);
            return (
              <li key={e.key} className="row gap2" style={{ fontSize: 13.5 }}>
                {s === "done" && (
                  <span
                    style={{
                      width: 20,
                      height: 20,
                      borderRadius: "50%",
                      background: "var(--ok)",
                      color: "#fff",
                      display: "grid",
                      placeItems: "center",
                      fontSize: 11,
                    }}
                  >
                    ✓
                  </span>
                )}
                {s === "active" && <Spinner size={16} />}
                {s === "pending" && (
                  <span
                    style={{
                      width: 20,
                      height: 20,
                      borderRadius: "50%",
                      border: "2px solid var(--line)",
                    }}
                  />
                )}
                <span
                  style={{
                    color:
                      s === "active"
                        ? "var(--accent-deep)"
                        : s === "done"
                          ? "var(--text)"
                          : "var(--faint)",
                    fontWeight: s === "active" ? 700 : 500,
                  }}
                >
                  {e.label}
                </span>
              </li>
            );
          })}
        </ul>

        {erro && (
          <div
            style={{
              marginTop: 18,
              fontSize: 13,
              color: "var(--desp)",
              background: "var(--desp-bg)",
              border: "1px solid rgba(229,72,77,.2)",
              borderRadius: 10,
              padding: "8px 11px",
            }}
          >
            {job.erro}
          </div>
        )}
      </Card>
    </div>
  );
}
