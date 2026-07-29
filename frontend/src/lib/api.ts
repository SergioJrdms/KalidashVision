import { supabase } from "./supabase";
import type {
  AcaoEvento,
  CategoriaLean,
  DashboardData,
  EventoPendente,
  EventosTabelaParams,
  EventosTabelaResposta,
  FilaResposta,
  FilaGlobalResposta,
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
  TurnoProcesso,
  TurnoBody,
  ZonaCamera,
  ZonaBody,
  FrameReferencia,
  AnaliseDiaria,
  SaudeEdge,
  FilaDuvidas,
  EstadoAprendizado,
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
    eventosPendentes: (id: string, agrupar = false) =>
      req<EventoPendente[]>(
        `/processos/${id}/eventos?status=pendente${agrupar ? "&agrupar=true" : ""}`,
      ),
    excluir: (id: string) =>
      req<{ ok: boolean }>(`/processos/${id}`, { method: "DELETE" }),
    // Fase 7 — painel da fila (inbox de segmentos)
    fila: (id: string, status?: string) =>
      req<FilaResposta>(
        `/processos/${id}/fila${status && status !== "todos" ? `?status=${status}` : ""}`,
      ),
    processarLote: (id: string) =>
      req<{ ok: boolean; pares: number; solo: number; itens: number }>(
        `/processos/${id}/lote/concluido`,
        { method: "POST" },
      ),
    reprocessarErros: (id: string) =>
      req<{ ok: boolean; reset: number; itens: number }>(
        `/processos/${id}/fila/reprocessar-erros`,
        { method: "POST" },
      ),
    filaGlobal: () => req<FilaGlobalResposta>(`/fila/global`),
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
    // Modo teste (sem o Pi), Fase 32: os BYTES vão DIRETO ao Supabase Storage
    // (URL assinada) — o backend/proxy, que corta uploads longos, só vê dois
    // JSONs de milissegundos (pedir URL + registrar na inbox `segmentos`).
    // gravado_em é lido do nome seg_YYYYMMDD_HHMMSS; depois
    // api.processos.processarLote pareia cam1+cam2 e dispara o dual-angle.
    uploadSegmento: async (
      processoId: string,
      file: File,
      camId: string,
    ): Promise<{ ok: boolean; status: string }> => {
      const r1 = await req<{
        ok: boolean; status: string; bucket?: string; storage_path?: string; token?: string;
      }>(`/processos/${processoId}/segmentos/upload-url`, {
        method: "POST",
        body: JSON.stringify({ nome: file.name, cam_id: camId }),
      });
      if (r1.status === "duplicado") return { ok: true, status: "duplicado" };
      if (!r1.bucket || !r1.storage_path || !r1.token) {
        throw new Error("Resposta inválida do upload-url.");
      }
      const { error } = await supabase.storage
        .from(r1.bucket)
        .uploadToSignedUrl(r1.storage_path, r1.token, file, {
          contentType: file.type || "video/mp4",
        });
      if (error) throw new Error(`Storage: ${error.message}`);
      return req<{ ok: boolean; status: string }>(
        `/processos/${processoId}/segmentos/registrar`,
        {
          method: "POST",
          body: JSON.stringify({
            nome: file.name,
            cam_id: camId,
            storage_path: r1.storage_path,
          }),
        },
      );
    },
  },
  jobs: {
    status: (id: string) => req<JobStatus>(`/jobs/${id}`),
  },
  segmentos: {
    // 2º ângulo (cam2) por janela de tempo — validação dual-câmera (Fase 6).
    frames: (id: string, ini: number, fim: number) =>
      req<{ frames: string[] }>(`/segmentos/${id}/frames?ini=${ini}&fim=${fim}`),
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
  turnos: {
    listar: (processoId: string) =>
      req<TurnoProcesso[]>(`/processos/${processoId}/turnos`),
    criar: (processoId: string, body: TurnoBody) =>
      req<TurnoProcesso>(`/processos/${processoId}/turnos`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    atualizar: (turnoId: string, body: TurnoBody) =>
      req<TurnoProcesso>(`/turnos/${turnoId}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    excluir: (turnoId: string) =>
      req<{ ok: boolean }>(`/turnos/${turnoId}`, { method: "DELETE" }),
  },
  zonas: {
    listar: (processoId: string, camId?: string) =>
      req<ZonaCamera[]>(`/processos/${processoId}/zonas${camId ? `?cam_id=${encodeURIComponent(camId)}` : ""}`),
    criar: (processoId: string, body: ZonaBody) =>
      req<ZonaCamera>(`/processos/${processoId}/zonas`, { method: "POST", body: JSON.stringify(body) }),
    atualizar: (zonaId: string, body: ZonaBody) =>
      req<ZonaCamera>(`/zonas/${zonaId}`, { method: "PUT", body: JSON.stringify(body) }),
    excluir: (zonaId: string) =>
      req<{ ok: boolean }>(`/zonas/${zonaId}`, { method: "DELETE" }),
  },
  diaadia: {
    // Fase 35: análise diária (evolução por dia, janelas 7/30, tendência).
    analise: (processoId: string, dias = 30) =>
      req<AnaliseDiaria>(`/processos/${processoId}/dias?dias=${dias}`),
  },
  duvidas: {
    // Fase 58: fila ordenada por MINUTOS EM JOGO, com filtro por rótulo/tipo.
    listar: (processoId: string, rotulo?: string | null, tipo?: string | null) =>
      req<FilaDuvidas>(`/processos/${processoId}/duvidas`
        + `?limite=200${rotulo ? `&rotulo=${encodeURIComponent(rotulo)}` : ""}`
        + `${tipo ? `&tipo=${encodeURIComponent(tipo)}` : ""}`),
  },
  saude: {
    // Fase 52: estado da borda JÁ INTERPRETADO (observado × turno esperado).
    obter: (processoId: string) =>
      req<SaudeEdge>(`/processos/${processoId}/saude`),
  },
  cameras: {
    listar: (processoId: string) =>
      req<{ cameras: string[] }>(`/processos/${processoId}/cameras`),
    frameReferencia: (processoId: string, camId: string) =>
      req<FrameReferencia>(`/processos/${processoId}/cameras/${encodeURIComponent(camId)}/frame-referencia`),
  },
  aprendizado: {
    ler: (processoId: string) =>
      req<EstadoAprendizado>(`/processos/${processoId}/aprendizado`),
    setar: (processoId: string, ativo: boolean | null) =>
      req<{ ok: boolean; configurado: boolean | null; efetivo: boolean }>(
        `/processos/${processoId}/aprendizado`,
        { method: "PUT", body: JSON.stringify({ ativo }) },
      ),
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
    // Pelo RÓTULO: funciona mesmo quando o rótulo ainda não tem linha em
    // `comportamentos` (o backend cria). É a rota que as telas usam, porque
    // é a única que não depende de um id que pode não existir.
    setCategoriaPorLabel: (
      processoId: string,
      label: string,
      categoria_lean: CategoriaLean | null,
    ) =>
      req<{
        ok: boolean;
        comportamento_id: string;
        categoria_lean: CategoriaLean | null;
        origem: string | null;
        propagados?: number;
        eventos_atualizados?: number;
      }>(`/processos/${processoId}/comportamentos/categoria`, {
        method: "PUT",
        body: JSON.stringify({ label, categoria_lean }),
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
