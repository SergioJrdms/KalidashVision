import { supabase } from "./supabase";
import type {
  CategoriaLean,
  DashboardData,
  EventoPendente,
  JobStatus,
  PerguntaProcesso,
  Processo,
  ProcessoDetalhe,
  Sugestao,
} from "./types";

const API = import.meta.env.VITE_API_URL as string;

async function authHeader(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("Sem sessão. Faça login.");
  return { Authorization: `Bearer ${token}` };
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(await authHeader()),
    ...((init.headers as Record<string, string>) || {}),
  };
  const r = await fetch(`${API}${path}`, { ...init, headers });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`${r.status}: ${txt || r.statusText}`);
  }
  if (r.status === 204) return undefined as T;
  return r.json();
}

export const api = {
  processos: {
    list: () => req<Processo[]>("/processos"),
    create: (nome: string, descricao?: string) =>
      req<Processo>("/processos", {
        method: "POST",
        body: JSON.stringify({ nome, descricao }),
      }),
    detalhe: (id: string) => req<ProcessoDetalhe>(`/processos/${id}`),
    setDescricao: (id: string, descricao: string) =>
      req<{ ok: boolean }>(`/processos/${id}/descricao`, {
        method: "PUT",
        body: JSON.stringify({ descricao }),
      }),
    dashboard: (id: string) => req<DashboardData>(`/processos/${id}/dashboard`),
    sugestoes: (id: string) => req<Sugestao[]>(`/processos/${id}/sugestoes`),
    eventosPendentes: (id: string) =>
      req<EventoPendente[]>(`/processos/${id}/eventos?status=pendente`),
    chat: (id: string, pergunta: string, historico?: { role: string; content: string }[]) =>
      req<{ resposta: string }>(`/processos/${id}/chat`, {
        method: "POST",
        body: JSON.stringify({ pergunta, historico }),
      }),
  },
  videos: {
    upload: async (processoId: string, file: File): Promise<{ job_id: string }> => {
      const fd = new FormData();
      fd.append("file", file);
      const headers = await authHeader();
      const r = await fetch(`${API}/processos/${processoId}/videos`, {
        method: "POST",
        body: fd,
        headers,
      });
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      return r.json();
    },
  },
  jobs: {
    status: (id: string) => req<JobStatus>(`/jobs/${id}`),
  },
  eventos: {
    frames: (id: string) => req<{ frames: string[] }>(`/eventos/${id}/frames`),
    validar: (
      id: string,
      acao: "confirmar" | "corrigir" | "descartar",
      label_corrigido?: string
    ) =>
      req<{ ok: boolean }>(`/eventos/${id}/validar`, {
        method: "POST",
        body: JSON.stringify({ acao, label_corrigido }),
      }),
  },
  perguntas: {
    listar: (processoId: string, status: "pendente" | "respondida" | "dispensada" | "todas" = "pendente") =>
      req<PerguntaProcesso[]>(`/processos/${processoId}/perguntas?status=${status}`),
    contagem: (processoId: string) =>
      req<{ pendentes: number }>(`/processos/${processoId}/perguntas/contagem`),
    responder: (id: string, resposta: string) =>
      req<{ ok: boolean }>(`/perguntas/${id}/responder`, {
        method: "POST",
        body: JSON.stringify({ resposta }),
      }),
    dispensar: (id: string) =>
      req<{ ok: boolean }>(`/perguntas/${id}/dispensar`, { method: "POST" }),
  },
  comportamentos: {
    setCategoria: (comportamentoId: string, categoria_lean: CategoriaLean | null) =>
      req<{ ok: boolean; categoria_lean: CategoriaLean | null; origem: string | null }>(
        `/comportamentos/${comportamentoId}/categoria`,
        { method: "PUT", body: JSON.stringify({ categoria_lean }) }
      ),
  },
};

export function formatSeg(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return `${m}m ${r}s`;
}
