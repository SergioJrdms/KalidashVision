// ============================================================
// Adaptadores: resposta real da API → shapes que as telas do design
// (porte de data.jsx) esperam. Mantém os componentes ~verbatim.
// ============================================================
import { leanShort, type LeanShort } from "../design/helpers";
import type {
  DashboardData,
  EventoPendente,
  EventoTabela,
  InsightGlobal,
  InsightsQuantitativos,
  PadraoGlobal,
  Permanencia,
  PadraoProcesso,
  PerguntaProcesso,
  Processo,
  ProcessoDetalhe,
  SerieTemporal,
} from "./types";

export interface ProcMock {
  id: string;
  nome: string;
  descricao: string;
  area: string;
  videos: number;
  maturidade: number;
  validado: number;
  sugestoesAlta: number;
  pendencias: number;
  va: number;
  desp: number;
  ultimoVideo: string;
}

export interface ProcHeaderMock {
  id: string;
  nome: string;
  descricao: string;
  area: string;
  maturidade: number;
  pendencias: number;
}

// `id` só serve de chave de lista — pode ser sintético. Reclassificação usa
// SEMPRE `nome` (o rótulo), porque nem todo rótulo tem linha em `comportamentos`.
export interface CompMock { id: string; nome: string; pct: number; seg: number; cat: LeanShort; origem: string | null }
export interface SugMock { id: string; prioridade: string; area: string; sugestao: string; impacto: string; situacao: string; causa: string; comportamentos: string[]; voltou: boolean }
export interface DetMock {
  snapshot: { va: number; desp: number; vazio: number; semEvidencia: number; /** ⛔ limiar de cobertura, nunca exibido (Fase 101) */ coberturaMin: number; videos: number; validadoPct: number; topComportamento: { nome: string; pct: number } };
  comportamentos: CompMock[];
  pareto: { nome: string; pct: number; acc: number; cat: LeanShort }[];
  transicoes: { de: string; para: string; vezes: number }[];
  origens: { auto: number; humano: number; pendente: number };
  sugestoes: SugMock[];
  videos: { id: string; nome: string; quando: string; eventos: number; dur: number }[];
  perguntasPendentes: number;
  insights: InsightsQuantitativos | null;
  /** Fase 101 — o número principal, passado adiante sem transformação. */
  permanencia: Permanencia | null;
}

export interface PendIrmaoMock { id: string; camId: string | null; label: string; pessoa: number; ini: number; fim: number; conf: number; sugestao: LeanShort }
export interface PendMock { id: string; label: string; descricao: string; pessoa: number; papel: string | null; ini: number; fim: number; conf: number; sugestao: LeanShort; camId: string | null; irmaos: PendIrmaoMock[]; segundoAngulo: { segmentoId: string; camId: string | null; offsetS: number } | null }
export interface PergMock { id: string; pergunta: string; motivo: string; relacionados: string[]; chips: string[] }
export interface EvTabMock { id: string; label: string; corrigido: string | null; labelOrig: string; descricao: string; video: string; ini: number; fim: number; pessoa: number; conf: number; status: string; cat: LeanShort; comportamentoId: string | null; camId: string | null; papel: string | null; segundoAngulo: { segmentoId: string; camId: string | null; offsetS: number } | null }
export interface SerieMock { nVideos: number; pontos: { turno: string; va: number; desp: number }[] }
export interface PadProcMock { id: string; tipo: string; confianca: string; relevancia: string; titulo: string; descricao: string; recomendacao: string | null; comportamentos: string[] }
export interface InsightMock { id: string; prioridade: string; titulo: string; descricao: string; processos: string[] }
export interface PadGlobalMock { id: string; tipo: string; confianca: string; titulo: string; descricao: string; recomendacao: string | null; processos: string[] }

function rel(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const min = Math.round((Date.now() - d) / 60000);
  if (min < 60) return min < 1 ? "agora" : `há ${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `há ${h}h`;
  const dias = Math.round(h / 24);
  if (dias <= 1) return "há 1 dia";
  if (dias < 30) return `há ${dias} dias`;
  const m = Math.round(dias / 30);
  return m <= 1 ? "há 1 mês" : `há ${m} meses`;
}

export function mapProcessos(rows: Processo[]): ProcMock[] {
  return rows.map((p) => {
    const cv = p.composicao_valor;
    return {
      id: p.id,
      nome: p.processo,
      descricao: p.descricao || "",
      area: p.area || "Processo",
      videos: p.n_videos || 0,
      maturidade: p.maturidade || 0,
      validado: Math.round(p.pct_validado || 0),
      sugestoesAlta: p.n_sugestoes_alta || 0,
      pendencias: p.eventos_pendentes || 0,
      va: cv?.valor_agregado_pct || 0,
      desp: cv?.desperdicio_pct || 0,
      ultimoVideo: rel(p.ultimo_video_em),
    };
  });
}

export function mapHeader(p: ProcessoDetalhe): ProcHeaderMock {
  return {
    id: p.id,
    nome: p.processo,
    descricao: p.descricao || "",
    area: p.area || "Processo",
    maturidade: p.maturidade || 0,
    pendencias: (p as { pendencias?: number }).pendencias ?? p.eventos_pendentes ?? 0,
  };
}

export function mapDashboard(d: DashboardData): DetMock {
  const s = d.snapshot;
  const permanencia = d.permanencia ?? null;
  const cv = d.composicao_valor;
  const top = s.distribuicao_comportamentos[0];
  const comportamentos: CompMock[] = s.distribuicao_comportamentos.map((c) => ({
    // Chave estável mesmo sem linha no catálogo — o índice mudava de posição a
    // cada refresh e embaralhava o estado de edição da lista.
    id: (c.comportamento_id as string) || `lbl:${c.comportamento}`,
    nome: c.comportamento,
    pct: Math.round(c.pct_tempo),
    seg: Math.round(c.tempo_total_s),
    cat: leanShort(c.categoria_lean),
    origem: c.categoria_lean_origem || null,
  }));
  const pareto = (d.pareto || []).map((p) => ({
    nome: p.comportamento,
    pct: Math.round(p.pct_tempo),
    acc: Math.round(p.pct_acumulado),
    cat: leanShort(p.categoria_lean),
  }));
  const sugestoes: SugMock[] = (d.sugestoes || []).map((x) => ({
    id: x.id,
    prioridade: (x.prioridade || "info").toLowerCase(),
    area: x.area || "—",
    sugestao: x.sugestao || "",
    impacto: x.impacto_estimado || "—",
    situacao: x.situacao || "",
    causa: x.causa_provavel || "",
    comportamentos: x.eventos_relacionados?.comportamentos || [],
    voltou: !!x.voltou_apos_realizada,
  }));
  return {
    snapshot: {
      va: Math.round(cv.valor_agregado_pct),
      desp: Math.round(cv.desperdicio_pct),
      vazio: Math.round(cv.posto_vazio_pct || 0),
      // Não é uma fatia — é quanto do tempo já classificado foi ASSUMIDO em
      // vez de decidido. Vira o "próximo passo" e vira fila de dúvidas.
      semEvidencia: Math.round(cv.sem_evidencia_pct || 0),
      coberturaMin: s.tempo_total_observado_min,
      videos: s.videos_analisados,
      validadoPct: Math.round(s.pct_validado_por_humano),
      topComportamento: { nome: top?.comportamento || "—", pct: Math.round(top?.pct_tempo || 0) },
    },
    comportamentos,
    pareto,
    transicoes: (d.transicoes || []).map((t) => ({ de: t.de, para: t.para, vezes: t.vezes })),
    origens: d.origens,
    sugestoes,
    videos: (d.videos || []).map((v) => ({ id: v.id, nome: v.nome, quando: rel(v.processado_em), eventos: v.total_eventos, dur: v.duracao_s })),
    perguntasPendentes: d.perguntas_pendentes || 0,
    insights: d.insights_quantitativos || null,
    permanencia,
  };
}

export function mapPendentes(rows: EventoPendente[]): PendMock[] {
  return rows.map((e) => ({
    id: e.id,
    label: e.label_corrigido || e.comportamento_label,
    descricao: e.descricao_bruta || "",
    pessoa: e.pessoa_track_id,
    papel: e.papel_pessoa ?? null,
    ini: e.tempo_inicio_s,
    fim: e.tempo_fim_s,
    conf: e.confianca || 0,
    sugestao: leanShort(e.categoria_lean_prevista),
    camId: e.cam_id ?? null,
    irmaos: (e.irmaos || []).map((s) => ({
      id: s.id,
      camId: s.cam_id ?? null,
      label: s.label_corrigido || s.comportamento_label,
      pessoa: s.pessoa_track_id,
      ini: s.tempo_inicio_s,
      fim: s.tempo_fim_s,
      conf: s.confianca || 0,
      sugestao: leanShort(s.categoria_lean_prevista),
    })),
    segundoAngulo: e.segundo_angulo
      ? { segmentoId: e.segundo_angulo.segmento_id, camId: e.segundo_angulo.cam_id ?? null, offsetS: e.segundo_angulo.offset_s ?? 0 }
      : null,
  }));
}

const CHIPS_PADRAO = ["Sim", "Não", "Às vezes"];
export function mapPerguntas(rows: PerguntaProcesso[]): PergMock[] {
  return rows.map((q) => {
    const rapidas = Array.isArray(q.respostas_rapidas)
      ? q.respostas_rapidas.map((s) => (typeof s === "string" ? s.trim() : "")).filter(Boolean).slice(0, 3)
      : [];
    return {
      id: q.id,
      pergunta: q.pergunta,
      motivo: q.motivo || "",
      relacionados: q.comportamentos_relacionados || [],
      chips: rapidas.length >= 2 ? rapidas : CHIPS_PADRAO,
    };
  });
}

export function mapEventosTabela(rows: EventoTabela[]): EvTabMock[] {
  return rows.map((e) => ({
    id: e.id,
    label: e.label_efetivo,
    corrigido: e.label_corrigido,
    labelOrig: e.comportamento_label,
    descricao: e.descricao_bruta || "",
    video: e.video_nome || "—",
    ini: e.tempo_inicio_s,
    fim: e.tempo_fim_s,
    pessoa: e.pessoa_track_id,
    conf: e.confianca || 0,
    status: e.status_efetivo,
    cat: leanShort(e.categoria_lean),
    comportamentoId: e.comportamento_id ?? null,
    camId: e.cam_id ?? null,
    papel: e.papel_pessoa ?? null,
    segundoAngulo: e.segundo_angulo
      ? { segmentoId: e.segundo_angulo.segmento_id, camId: e.segundo_angulo.cam_id ?? null, offsetS: e.segundo_angulo.offset_s ?? 0 }
      : null,
  }));
}

export function mapSerie(s: SerieTemporal): SerieMock {
  return {
    nVideos: s.n_videos,
    pontos: s.pontos.map((p, i) => {
      const sc = p.share_categoria || {};
      const va = Math.round(sc["valor_agregado"] || 0);
      const desp = Math.round(sc["desperdicio"] || 0);
      const none = Math.max(0, 100 - va - desp);
      return { turno: `T${i + 1}`, va, desp, none };
    }),
  };
}

export function mapPadroes(rows: PadraoProcesso[]): PadProcMock[] {
  return rows.map((p) => ({
    id: p.id,
    tipo: p.tipo,
    confianca: p.confianca,
    relevancia: p.relevancia,
    titulo: p.titulo,
    descricao: p.descricao,
    recomendacao: p.recomendacao,
    comportamentos: p.comportamentos_relacionados || [],
  }));
}

export function mapInsights(rows: InsightGlobal[]): InsightMock[] {
  return rows.map((it) => ({
    id: it.id,
    prioridade: (it.prioridade || "info").toLowerCase(),
    titulo: it.titulo,
    descricao: it.descricao,
    processos: it.processos_relacionados || [],
  }));
}

export function mapPadroesGlobais(rows: PadraoGlobal[]): PadGlobalMock[] {
  return rows.map((p) => ({
    id: p.id,
    tipo: p.tipo,
    confianca: p.confianca,
    titulo: p.titulo,
    descricao: p.descricao,
    recomendacao: p.recomendacao,
    processos: p.processos_relacionados || [],
  }));
}
