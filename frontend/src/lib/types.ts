export interface Processo {
  id: string;
  processo: string;
  descricao: string | null;
  atualizado_em: string;
  // enriquecimento (GET /processos)
  n_videos?: number;
  eventos_pendentes?: number;
  pct_validado?: number;
  n_sugestoes?: number;
  n_sugestoes_alta?: number;
  tempo_total_min?: number;
  ultimo_video_em?: string | null;
  composicao_valor?: {
    valor_agregado_pct: number;
    apoio_pct: number;
    desperdicio_pct: number;
    nao_classificado_pct: number;
  } | null;
}

export interface InsightGlobal {
  id: string;
  prioridade: "alta" | "media" | "info" | string;
  titulo: string;
  descricao: string;
  processos_relacionados: string[] | null;
  criado_em: string;
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
  categoria_lean_origem?: "ia" | "humano" | null;
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

export interface Sugestao {
  id: string;
  prioridade: "alta" | "media" | "info" | string;
  area: string;
  situacao: string;
  causa_provavel: string;
  sugestao: string;
  impacto_estimado: string;
  eventos_relacionados?: { comportamentos?: string[] };
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
}

export interface EventosTabelaResposta {
  itens: EventoTabela[];
  total: number;
  page: number;
  page_size: number;
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
