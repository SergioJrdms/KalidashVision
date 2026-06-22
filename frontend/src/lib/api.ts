import { supabase } from "./supabase";
import type {
  AcaoEvento,
  CategoriaLean,
  DashboardData,
  EventoPendente,
  EventosTabelaParams,
  EventosTabelaResposta,
  JobStatus,
  InsightGlobal,
  PadraoGlobal,
  PadraoProcesso,
  PerguntaProcesso,
  SerieTemporal,
  PrismConversa,
  PrismConversaDetalhe,
  PrismEnvioResposta,
  Processo,
  ProcessoDetalhe,
  Sugestao,
  AcaoSugestao,
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
    create: (nome: string, descricao?: string, area?: string) =>
      req<Processo>("/processos", {
        method: "POST",
        body: JSON.stringify({ nome, descricao, area }),
      }),
    detalhe: (id: string) => req<ProcessoDetalhe>(`/processos/${id}`),
    setDescricao: (id: string, descricao: string) =>
      req<{ ok: boolean }>(`/processos/${id}/descricao`, {
        method: "PUT",
        body: JSON.stringify({ descricao }),
      }),
    setArea: (id: string, area: string | null) =>
      req<{ ok: boolean; area: string | null }>(`/processos/${id}/area`, {
        method: "PUT",
        body: JSON.stringify({ area }),
      }),
    extrairDescricaoArquivo: async (id: string, file: File): Promise<{ texto: string }> => {
      const fd = new FormData();
      fd.append("file", file);
      const { data: sess } = await supabase.auth.getSession();
      const headers: Record<string, string> = {};
      if (sess.session?.access_token) headers["Authorization"] = `Bearer ${sess.session.access_token}`;
      const r = await fetch(`${API}/processos/${id}/descricao/extrair`, { method: "POST", body: fd, headers });
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      return r.json();
    },
    onboardingProxima: (
      id: string,
      historico: { pergunta: string; resposta: string }[],
      area_inicial: string | null,
    ) =>
      req<
        | { completo: false; pergunta: string; motivo: string; respostas_rapidas: string[] | null }
        | { completo: true; descricao_consolidada: string }
      >(`/processos/${id}/onboarding/proxima-pergunta`, {
        method: "POST",
        body: JSON.stringify({ historico, area_inicial }),
      }),
    dashboard: (id: string) => req<DashboardData>(`/processos/${id}/dashboard`),
    sugestoes: (id: string) => req<Sugestao[]>(`/processos/${id}/sugestoes`),
    eventosPendentes: (id: string) =>
      req<EventoPendente[]>(`/processos/${id}/eventos?status=pendente`),
    excluir: (id: string) =>
      req<{ ok: boolean }>(`/processos/${id}`, { method: "DELETE" }),
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
    validar: (id: string, acao: AcaoEvento, label_corrigido?: string) =>
      req<{ ok: boolean }>(`/eventos/${id}/validar`, {
        method: "POST",
        body: JSON.stringify({ acao, label_corrigido }),
      }),
    reabrir: (id: string) =>
      req<{ ok: boolean }>(`/eventos/${id}/reabrir`, { method: "POST" }),
    lote: (ids: string[], acao: AcaoEvento, label_corrigido?: string) =>
      req<{ ok: boolean; aplicados: number }>(`/eventos/lote`, {
        method: "POST",
        body: JSON.stringify({ ids, acao, label_corrigido }),
      }),
    tabela: (processoId: string, params: EventosTabelaParams = {}) => {
      const qs = new URLSearchParams();
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
      });
      const sufixo = qs.toString() ? `?${qs.toString()}` : "";
      return req<EventosTabelaResposta>(`/processos/${processoId}/eventos/tabela${sufixo}`);
    },
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
  sugestoes: {
    marcar: (id: string, acao: AcaoSugestao) =>
      req<{ ok: boolean; status: string }>(`/sugestoes/${id}/marcar`, {
        method: "POST",
        body: JSON.stringify({ acao }),
      }),
  },
  comportamentos: {
    setCategoria: (comportamentoId: string, categoria_lean: CategoriaLean | null) =>
      req<{
        ok: boolean;
        categoria_lean: CategoriaLean | null;
        origem: string | null;
        propagados?: number;
      }>(`/comportamentos/${comportamentoId}/categoria`, {
        method: "PUT",
        body: JSON.stringify({ categoria_lean }),
      }),
  },
  // Prism com escopo: processoId definido → modo processo; null → modo global.
  prism: (processoId: string | null) => {
    const base = processoId ? `/processos/${processoId}/prism` : `/prism`;
    return {
      listarConversas: () => req<PrismConversa[]>(`${base}/conversas`),
      criarConversa: () => req<PrismConversa>(`${base}/conversas`, { method: "POST" }),
      getConversa: (conversaId: string) =>
        req<PrismConversaDetalhe>(`${base}/conversas/${conversaId}`),
      renomear: (conversaId: string, titulo: string) =>
        req<{ ok: boolean }>(`${base}/conversas/${conversaId}`, {
          method: "PATCH",
          body: JSON.stringify({ titulo }),
        }),
      excluir: (conversaId: string) =>
        req<{ ok: boolean }>(`${base}/conversas/${conversaId}`, { method: "DELETE" }),
      enviarMensagem: (conversaId: string, pergunta: string) =>
        req<PrismEnvioResposta>(`${base}/conversas/${conversaId}/mensagens`, {
          method: "POST",
          body: JSON.stringify({ pergunta }),
        }),
      sugestoes: (excluir: string[] = []) => {
        const qs = excluir.length > 0 ? `?excluir=${encodeURIComponent(excluir.join("|"))}` : "";
        return req<{ sugestoes: string[] }>(`${base}/sugestoes${qs}`);
      },
    };
  },
  insightsGlobais: () => req<InsightGlobal[]>(`/prism/insights-globais`),
  padroes: {
    doProcesso: (processoId: string) =>
      req<PadraoProcesso[]>(`/processos/${processoId}/padroes`),
    serie: (processoId: string) =>
      req<SerieTemporal>(`/processos/${processoId}/serie-temporal`),
    globais: () => req<PadraoGlobal[]>(`/prism/padroes-globais`),
  },
};

export function formatSeg(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return `${m}m ${r}s`;
}
