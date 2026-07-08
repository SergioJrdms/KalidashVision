export interface ComposicaoValorLite {
  valor_agregado_pct: number;
  apoio_pct: number;
  desperdicio_pct: number;
  nao_classificado_pct: number;
}

export interface Processo {
  id: string;
  processo: string;
  descricao: string | null;
  area?: string | null;
  atualizado_em: string;
  // enriquecimento (GET /processos)
  n_videos?: number;
  eventos_pendentes?: number;
  pct_validado?: number;
  n_sugestoes?: number;
  n_sugestoes_alta?: number;
  tempo_total_min?: number;
  ultimo_video_em?: string | null;
  maturidade?: number;
  composicao_valor?: ComposicaoValorLite | null;
}

export interface InsightGlobal {
  id: string;
  prioridade: "alta" | "media" | "info" | string;
  titulo: string;
  descricao: string;
  processos_relacionados: string[] | null;
  criado_em: string;
}

export type ConfiancaPadrao = "alta" | "media" | "baixa";

export interface PadraoProcesso {
  id: string;
  tipo: string; // tendencia|recorrencia|desvio|volatilidade|fluxo|desperdicio|valor
  camada: "temporal" | "estrutural" | string;
  titulo: string;
  descricao: string;
  comportamentos_relacionados: string[] | null;
  categoria_relacionada: string | null;
  confianca: ConfiancaPadrao | string;
  relevancia: "alta" | "media" | "info" | string;
  recomendacao: string | null;
  n_videos_analisados: number | null;
  criado_em: string;
}

export interface PadraoGlobal {
  id: string;
  tipo: string; // compartilhado|benchmarking|sistemico
  titulo: string;
  descricao: string;
  processos_relacionados: string[] | null;
  confianca: ConfiancaPadrao | string;
  relevancia: string;
  recomendacao: string | null;
  criado_em: string;
}

export interface SerieTemporalPonto {
  video_id: string;
  nome: string | null;
  processado_em: string | null;
  n_eventos: number;
  n_pessoas: number;
  share_comportamento: Record<string, number>;
  share_categoria: Record<string, number>;
}

export interface SerieTemporal {
  pontos: SerieTemporalPonto[];
  labels: string[];
  categorias: string[];
  n_videos: number;
}

export interface ProcessoDetalhe extends Processo {
  videos: Array<{
    id: string;
    nome: string;
    duracao_s: number;
    total_eventos: number;
    processado_em: string;
  }>;
  n_videos: number;
  pendencias?: number;
}

export interface JobStatus {
  id: string;
  processo_id: string;
  status: "pendente" | "processando" | "concluido" | "erro";
  etapa_atual: string;
  progresso_pct: number;
  mensagem: string;
  video_id?: string | null;
  erro?: string | null;
  resultado?: { n_eventos?: number; n_auto_validados?: number; n_sugestoes?: number } | null;
}

export type CategoriaLean = "valor_agregado" | "apoio" | "desperdicio";

export interface DistribuicaoComportamento {
  comportamento: string;
  descricao: string;
  ocorrencias: number;
  tempo_total_s: number;
  pct_tempo: number;
  em_n_videos: number;
  categoria_lean?: CategoriaLean | null;
  categoria_lean_origem?: "ia" | "humano" | "aprendido" | null;
  comportamento_id?: string | null;
  pct_acumulado?: number;
}

export interface ComposicaoValor {
  valor_agregado_pct: number;
  apoio_pct: number;
  desperdicio_pct: number;
  nao_classificado_pct: number;
  tempo_total_s: number;
  por_categoria_s: Record<string, number>;
}

export interface ParetoItem {
  comportamento: string;
  descricao: string;
  categoria_lean?: CategoriaLean | null;
  pct_tempo: number;
  tempo_total_s: number;
  pct_acumulado: number;
}

export type StatusSugestao = "pendente" | "realizada" | "dispensada";
export type AcaoSugestao = "realizada" | "dispensada" | "reabrir";

export interface Sugestao {
  id: string;
  prioridade: "alta" | "media" | "info" | string;
  area: string;
  situacao: string;
  causa_provavel: string;
  sugestao: string;
  impacto_estimado: string;
  eventos_relacionados?: { comportamentos?: string[] };
  status?: StatusSugestao;
  voltou_apos_realizada?: boolean;
  marcada_em?: string | null;
  criado_em: string;
}

export interface Transicao {
  de: string;
  para: string;
  vezes: number;
}

export interface VideoResumo {
  id: string;
  nome: string;
  duracao_s: number;
  total_eventos: number;
  total_pessoas: number;
  processado_em: string;
}

export interface PerguntaProcesso {
  id: string;
  pergunta: string;
  motivo: string | null;
  comportamentos_relacionados: string[] | null;
  respostas_rapidas: string[] | null;
  status: "pendente" | "respondida" | "dispensada";
  resposta: string | null;
  respondida_em: string | null;
  criada_em: string;
}

export interface DashboardData {
  snapshot: {
    videos_analisados: number;
    tempo_total_observado_min: number;
    eventos_considerados: number;
    pct_validado_por_humano: number;
    distribuicao_comportamentos: DistribuicaoComportamento[];
    sugestoes_recentes: Array<Pick<Sugestao, "prioridade" | "area" | "situacao" | "sugestao" | "impacto_estimado">>;
  };
  sugestoes: Sugestao[];
  eventos_pendentes: number;
  perguntas_pendentes: number;
  transicoes: Transicao[];
  origens: { auto: number; humano: number; pendente: number };
  videos: VideoResumo[];
  composicao_valor: ComposicaoValor;
  pareto: ParetoItem[];
  insights_quantitativos?: InsightsQuantitativos;
  padroes_resumo: Array<{
    id: string;
    tipo: string;
    camada: string;
    titulo: string;
    relevancia: string;
    confianca: string;
  }>;
}

export interface InsightFrase {
  texto: string;
  tom: string; // ok | warn | high | info
}

export interface PlacarDia {
  dia: string; // "27/06"
  va_pct: number;
  desp_pct: number;
  seg: number;
}

export interface PlacarProcesso {
  modo: "comparativo" | "referencia";
  unidade: "dia" | "sessão";
  score: number; // comparativo: % da melhor unidade · referência: % produtivo (linha de base)
  eh_melhor: boolean;
  dia_atual: PlacarDia;
  dia_melhor: PlacarDia;
  puxou: string[]; // o que puxou pra baixo vs melhor unidade
  vs_anterior: Record<string, { antes: number; atual: number; delta_pp: number }> | null;
  n_unidades: number;
  // Fase 20 — quanto vale fechar o gap vs o melhor dia (horas produtivas)
  ganho?: { gap_pp: number; turno_h: number; por_turno_s: number; por_mes_s: number } | null;
}

export interface PerguntaGestor {
  texto: string;
  contexto?: string;
}

export interface InsightsQuantitativos {
  frases: InsightFrase[];
  tempo_por_acao: Array<{ acao: string; seg: number; pct: number; categoria: string | null }>;
  por_categoria: Record<string, { seg: number; pct: number }>;
  por_roi: Array<{ zona: string; seg: number; pct: number; va_pct: number; desp_pct: number }>;
  periodo: { texto: string; tendencia_desp_pp: number } | null;
  // Fase 19 — placar vs melhor dia + perguntas prontas pro chão de fábrica
  placar?: PlacarProcesso | null;
  perguntas?: PerguntaGestor[];
}

export interface EventoIrmaoPendente {
  id: string;
  cam_id?: string | null;
  comportamento_label: string;
  label_corrigido: string | null;
  confianca: number;
  pessoa_track_id: number;
  tempo_inicio_s: number;
  tempo_fim_s: number;
  categoria_lean_prevista?: string | null;
}

export interface EventoPendente {
  id: string;
  video_id: string;
  comportamento_label: string;
  descricao_bruta: string;
  tempo_inicio_s: number;
  tempo_fim_s: number;
  confianca: number;
  validado_humano: boolean | null;
  validacao_correto: boolean | null;
  label_corrigido: string | null;
  origem_validacao: string | null;
  pessoa_track_id: number;
  categoria_lean_prevista?: string | null;
  cam_id?: string | null;
  gravado_em?: string | null;
  irmaos?: EventoIrmaoPendente[];
  // Fase 6 (dual-angle): 2º ângulo (segmento da cam2) p/ mostrar no card de
  // validação quando o evento foi processado com os 2 ângulos juntos (sem irmão).
  segundo_angulo?: { segmento_id: string; cam_id: string | null } | null;
}

export type StatusEfetivo =
  | "pendente"
  | "confirmado"
  | "corrigido"
  | "descartado"
  | "auto";

export interface EventoTabela {
  id: string;
  video_id: string;
  video_nome: string;
  pessoa_track_id: number;
  comportamento_label: string;
  label_corrigido: string | null;
  label_efetivo: string;
  descricao_bruta: string;
  tempo_inicio_s: number;
  tempo_fim_s: number;
  duracao_s: number;
  confianca: number;
  validado_humano: boolean | null;
  validacao_correto: boolean | null;
  origem_validacao: string | null;
  status_efetivo: StatusEfetivo;
  criado_em: string;
  validado_em: string | null;
  categoria_lean?: CategoriaLean | null;
  comportamento_id?: string | null;
  cam_id?: string | null;
}

export interface EventosTabelaResposta {
  itens: EventoTabela[];
  total: number;
  page: number;
  page_size: number;
}

export type StatusSegmento =
  | "pendente"
  | "enfileirado"
  | "processando"
  | "concluido"
  | "erro";

export interface SegmentoFila {
  id: string;
  nome: string | null;
  seg_token: string | null;
  cam_id: string | null;
  gravado_em: string | null;
  status: StatusSegmento;
  erro: string | null;
  recebido_em: string | null;
  processado_em: string | null;
  video_id: string | null;
}

export interface FilaResposta {
  contagens: Record<StatusSegmento, number>;
  total: number;
  itens: SegmentoFila[];
}

export interface FilaProcessoResumo {
  processo: string;
  processo_id: string | null;
  contagens: Record<StatusSegmento, number>;
  total: number;
}

export interface FilaGlobalResposta {
  contagens: Record<StatusSegmento, number>;
  total: number;
  processos: FilaProcessoResumo[];
}

export interface EventosTabelaParams {
  page?: number;
  page_size?: number;
  status?: "todos" | StatusEfetivo;
  label?: string;
  video_id?: string;
  busca?: string;
  sort?: "criado_em" | "tempo_inicio_s" | "duracao_s" | "comportamento_label" | "confianca";
  order?: "asc" | "desc";
}

export type AcaoEvento = "confirmar" | "corrigir" | "descartar" | "reabrir";

export interface PrismConversa {
  id: string;
  titulo: string;
  titulo_auto: boolean;
  criada_em: string;
  atualizada_em: string;
}

export interface PrismMensagem {
  id?: string;
  papel: "user" | "assistant";
  conteudo: string;
  criada_em?: string;
}

export interface PrismConversaDetalhe extends PrismConversa {
  mensagens: PrismMensagem[];
}

export interface PrismEnvioResposta {
  resposta: string;
  titulo_auto: string | null;
  fora_de_escopo: boolean;
}

export interface IntervaloTurno {
  inicio: string; // "HH:MM"
  fim: string;    // "HH:MM"
}

export interface TurnoProcesso {
  id: string;
  nome: string;
  intervalos: IntervaloTurno[];
  dias_semana: number[]; // ISO: 1=seg .. 7=dom
  ativo: boolean;
  criado_em: string;
  atualizado_em: string;
}

export interface TurnoBody {
  nome: string;
  intervalos: IntervaloTurno[];
  dias_semana: number[];
  ativo: boolean;
}
