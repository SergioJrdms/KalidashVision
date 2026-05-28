import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { Button, Card, Spinner } from "../components/UI";
import { ProcessoTabs } from "../components/Layout";
import type { JobStatus } from "../lib/types";

const ETAPAS: { key: string; label: string }[] = [
  { key: "setup", label: "Preparando" },
  { key: "deteccao", label: "Detectando pessoas" },
  { key: "vlm", label: "Analisando ações com IA" },
  { key: "cluster", label: "Agrupando comportamentos" },
  { key: "segmentar", label: "Formando eventos" },
  { key: "persistir", label: "Salvando resultados" },
  { key: "sugestoes", label: "Gerando sugestões" },
  { key: "concluido", label: "Concluído" },
];

export default function Upload() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);

  useEffect(() => {
    if (!job || job.status === "concluido" || job.status === "erro") return;
    let consecutivos404 = 0;
    const t = setInterval(async () => {
      try {
        const s = await api.jobs.status(job.id);
        consecutivos404 = 0;
        setJob(s);
        if (s.status === "concluido") {
          clearInterval(t);
          setTimeout(() => nav(`/processos/${id}/dashboard`), 800);
        }
        if (s.status === "erro") clearInterval(t);
      } catch (e: unknown) {
        const msg = (e as Error).message || "";
        if (msg.startsWith("404")) {
          consecutivos404 += 1;
          // Job sumiu (provavelmente o backend reiniciou no meio do
          // processamento). Após ~15s sem encontrá-lo, paramos.
          if (consecutivos404 >= 10) {
            clearInterval(t);
            setJob({
              ...job,
              status: "erro",
              erro:
                "O backend perdeu este job. Provavelmente o uvicorn reiniciou (--reload) ou foi encerrado durante o processamento. Rode o backend SEM --reload e envie o vídeo de novo.",
              mensagem: "Job perdido",
            });
          }
        }
        /* outros erros: continua tentando no próximo tick */
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job, id, nav]);

  async function enviar() {
    if (!file || !id) return;
    setErro(null);
    setUploading(true);
    try {
      const { job_id } = await api.videos.upload(id, file);
      setJob({
        id: job_id,
        processo_id: id,
        status: "pendente",
        etapa_atual: "setup",
        progresso_pct: 0,
        mensagem: "Em fila",
      });
    } catch (e: unknown) {
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

  return (
    <div>
      <ProcessoTabs />

      {!job && (
        <Card className="p-8 max-w-3xl">
          <h2 className="text-xl font-semibold text-slate-900 mb-1">
            Enviar vídeo da operação
          </h2>
          <p className="text-sm text-slate-500 mb-6">
            O processamento pode levar alguns minutos. Você pode acompanhar o
            progresso por aqui.
          </p>

          <div
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            className={`border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition ${
              drag
                ? "border-kv-purple bg-kv-purple-50"
                : "border-slate-300 bg-slate-50 hover:bg-kv-purple-50 hover:border-kv-purple-300"
            }`}
          >
            <div className="mx-auto h-12 w-12 rounded-full bg-kv-purple-100 flex items-center justify-center mb-3">
              <svg className="h-6 w-6 text-kv-purple" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <p className="text-sm text-slate-700">
              {file ? (
                <>
                  <span className="font-medium">{file.name}</span>{" "}
                  <span className="text-slate-500">
                    · {(file.size / 1024 / 1024).toFixed(1)} MB
                  </span>
                </>
              ) : (
                <>
                  Clique para selecionar ou{" "}
                  <span className="font-medium">arraste o vídeo</span> aqui
                </>
              )}
            </p>
            <p className="text-xs text-slate-400 mt-1">MP4, MOV ou AVI</p>
            <input
              ref={inputRef}
              type="file"
              accept="video/*"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </div>

          {erro && (
            <div className="mt-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
              {erro}
            </div>
          )}

          <div className="mt-6 flex justify-end gap-2">
            {file && (
              <Button variant="ghost" onClick={() => setFile(null)}>
                Trocar
              </Button>
            )}
            <Button disabled={!file || uploading} onClick={enviar}>
              {uploading ? "Enviando..." : "Iniciar análise"}
            </Button>
          </div>
        </Card>
      )}

      {job && <ProgressoJob job={job} />}
    </div>
  );
}

function ProgressoJob({ job }: { job: JobStatus }) {
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
    <Card className="p-8 max-w-3xl">
      <h2 className="text-xl font-semibold text-slate-900">
        {erro ? "Erro no processamento" : concluido ? "Processamento concluído" : "Processando seu vídeo"}
      </h2>
      <p className="text-sm text-slate-500 mt-1">
        {erro
          ? "Algo deu errado. Veja o detalhe abaixo."
          : concluido
            ? "Te redirecionando para o dashboard..."
            : "Isso pode levar alguns minutos. Você pode deixar a aba aberta."}
      </p>

      <div className="mt-6">
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>{job.mensagem || "Trabalhando..."}</span>
          <span>{job.progresso_pct}%</span>
        </div>
        <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
          <div
            className={`h-full transition-all ${erro ? "bg-red-500" : "bg-kv-purple"}`}
            style={{ width: `${Math.max(2, job.progresso_pct)}%` }}
          />
        </div>
      </div>

      <ul className="mt-8 space-y-3">
        {ETAPAS.map((e) => {
          const s = statusEtapa(e.key);
          return (
            <li key={e.key} className="flex items-center gap-3 text-sm">
              {s === "done" && (
                <span className="h-5 w-5 rounded-full bg-emerald-500 text-white flex items-center justify-center text-xs">
                  ✓
                </span>
              )}
              {s === "active" && <Spinner />}
              {s === "pending" && (
                <span className="h-5 w-5 rounded-full border-2 border-slate-200" />
              )}
              <span
                className={
                  s === "active"
                    ? "text-kv-purple-dark font-medium"
                    : s === "done"
                      ? "text-slate-600"
                      : "text-slate-400"
                }
              >
                {e.label}
              </span>
            </li>
          );
        })}
      </ul>

      {erro && (
        <div className="mt-6 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
          {job.erro}
        </div>
      )}
    </Card>
  );
}
