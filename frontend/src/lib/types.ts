export interface Processo {
  id: string;
  processo: string;
  descricao: string | null;
  atualizado_em: string;
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

export interface DistribuicaoComportamento {
  comportamento: string;
  descricao: string;
  ocorrencias: number;
  tempo_total_s: number;
  pct_tempo: number;
  em_n_videos: number;
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
