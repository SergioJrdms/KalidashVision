export interface ComposicaoValorLite {
  valor_agregado_pct: number;
  desperdicio_pct: number;
  sem_evidencia_pct?: number;
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

export type CategoriaLean = "valor_agregado" | "desperdicio"; // Fase 49: binário

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
  // Inclui posto_vazio (o card não muda de número). `posto_vazio_pct` abaixo é
  // a parcela dele, para a tela mostrar a fatia e a decomposição.
  desperdicio_pct: number;
  posto_vazio_pct?: number;
  posto_vazio_s?: number;
  sem_evidencia_pct?: number;
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

/** Fase 101 — O NÚMERO PRINCIPAL. Vem de presença na zona, contado direto:
 *  sem VLM, sem rótulo, sem categoria. Só percentual — a captura amostra ~50%
 *  de cada hora, então duração absoluta seria metade da verdade. */
export interface Permanencia {
  no_posto_pct: number;
  fora_pct: number;
  /** null enquanto a orientação não foi verificada com dado. */
  no_posto_torno_pct: number | null;
  no_posto_outro_lado_pct: number | null;
  orientacao_verificada: boolean;
  /** false = a tela mostra SÓ permanência (os dois estados colapsados). */
  detalhado: boolean;
  sem_dado: boolean;
  frase: string;
}

export interface MetricasProdutividadePosto {
  produtividade_pct: number | null;
  improdutividade_pct: number | null;
  presenca_pct: number | null;
  posto_vazio_pct: number | null;
  outra_pessoa_no_posto_pct: number | null;
  cobertura_produtividade_pct: number | null;
  cobertura_presenca_pct: number | null;
  cobertura_identificacao_pct: number | null;
  inconclusivo_pct: number | null;
  publicavel: boolean;
  sem_dado: boolean;
}

export interface ProdutividadePosto extends MetricasProdutividadePosto {
  janela_dias: number;
  captura_atrasada: boolean;
  estado_atual: {
    presenca: "no_posto" | "fora_do_posto" | "posto_vazio" | "sem_leitura";
    posto: "ocupado" | "vazio" | "indeterminado";
    produtividade: "produtivo" | "improdutivo" | null;
    motivo: string;
    leitura_em: string | null;
  };
  serie_diaria: Array<MetricasProdutividadePosto & { dia: string }>;
  regra: {
    produtivo: string;
    improdutivo: string;
    presenca: string;
  };
}

export interface DashboardData {
  permanencia?: Permanencia;
  produtividade_posto?: ProdutividadePosto;
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
  // Fase 21 — ritmo por hora do relógio real (junta todos os dias)
  por_hora?: Array<{ hora: number; seg: number; va_pct: number; desp_pct: number }>;
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
  papel_pessoa?: string | null; // Fase 28: 'operador' | 'visitante' | 'posto_vazio'
  categoria_lean_prevista?: string | null;
  cam_id?: string | null;
  gravado_em?: string | null;
  /** Hora de PAREDE da fábrica, já formatada pelo servidor. O navegador não
   *  reinterpreta: formatar no cliente relia o carimbo como UTC e subtraía o
   *  fuso — 07h virava 04h. */
  instante_fabrica?: string | null;
  faixa_hora_fabrica?: string | null;
  instante_iso?: string | null;
  /** 'segmento' | 'video' — de onde saiu o relógio. */
  hora_de?: string | null;
  irmaos?: EventoIrmaoPendente[];
  // Fase 6 (dual-angle): 2º ângulo (segmento da cam2) p/ mostrar no card de
  // validação quando o evento foi processado com os 2 ângulos juntos (sem irmão).
  // Fase 30: offset_s = início do vídeo cam1 − início do segmento cam2 (os
  // dois NÃO começam no mesmo segundo) — somar em ini/fim ao pedir frames.
  segundo_angulo?: { segmento_id: string; cam_id: string | null; offset_s?: number } | null;
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
  papel_pessoa?: string | null; // Fase 28: 'operador' | 'visitante' | 'posto_vazio'
  // Fase 29: segmento par (cam2) p/ mostrar as 2 câmeras juntas.
  // Fase 30: offset_s corrige o relógio (ver EventoPendente.segundo_angulo).
  segundo_angulo?: { segmento_id: string; cam_id: string | null; offset_s?: number } | null;
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

// Fase 70: `descricao_invalida` é estado PRÓPRIO, não um descarte.
//   descartar          → não havia ação aqui (falso positivo da detecção);
//   descricao_invalida → HAVIA uma cena e o modelo mentiu sobre ela.
// Os dois saem das métricas; só o segundo queima a frase e mede alucinação.
export type AcaoEvento =
  | "confirmar" | "corrigir" | "descartar" | "descricao_invalida" | "reabrir";
/** As ações que a tela de validação oferece (sem 'reabrir'). */
export type AcaoValidacao = Exclude<AcaoEvento, "reabrir">;

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

// ── Zonas por câmera (Fase 28) ──────────────────────────────
export type PapelZona = "posto_operador" | "maquina" | "interacao";

export interface ZonaCamera {
  id: string;
  cam_id: string;
  nome: string;
  papel: PapelZona;
  pts_rel: [number, number][]; // normalizado [0-1] no espaço do vídeo enviado
  descricao_contexto: string | null;
  frame_ref_w: number | null;
  frame_ref_h: number | null;
  ativo: boolean;
  // Fase 86: só na zona 'maquina'. Onde a máquina está em relação à CÂMERA —
  // é o que traduz "de costas para a câmera" em "de frente para o torno".
  frente_maquina: FrenteMaquina | null;
  criado_em: string;
  atualizado_em: string;
}
export type FrenteMaquina = "camera" | "oposta" | "perfil";

export interface ZonaBody {
  cam_id: string;
  nome: string;
  papel: PapelZona;
  pts_rel: [number, number][];
  descricao_contexto?: string | null;
  frame_ref_w?: number | null;
  frame_ref_h?: number | null;
  ativo: boolean;
  frente_maquina?: FrenteMaquina | null;
}

// ── Análise diária "Dia a dia" (Fase 35) ────────────────────
// ── Fase 52: saúde da borda (heartbeat do Pi) ────────────────────────────────
// O estado vem JÁ INTERPRETADO do backend (observado × esperado pelo turno).
// A tela só pinta — nenhum cálculo de "está online?" no frontend.
// `sem_captura` = o Pi está VIVO (mandando pulso) mas não está gravando dentro
// do turno — ffmpeg morto, timer que não disparou. Só existe porque o Pi manda
// pulso 24/7; sem isso, esse caso se disfarçaria de "capturando".
export type EstadoSaude =
  | "capturando" | "em_repouso" | "sem_sinal" | "sem_captura" | "sem_dados";

export interface SaudeCamera {
  cam_id: string;
  nome: string;
  estado: EstadoSaude;
  gravando: boolean;
  ultimo_segmento_em: string | null;
  falhas: number;
  visto_em: string | null;
}

export interface SaudeBlocoCobertura {
  inicio: string;      // ISO
  esperado: boolean;   // dentro de uma janela do turno
  houve: boolean;      // chegou heartbeat neste bloco
}

export interface SaudeEdge {
  estado: EstadoSaude;
  desde: string | null;
  ultimo_heartbeat_em: string | null;
  idade_s: number | null;
  device_id: string | null;
  runner_versao: string | null;
  estado_runner: string | null;
  cpu_temp_c: number | null;
  uptime_s: number | null;
  cameras: SaudeCamera[];
  disco: { livre_gb: number; uso_pct: number | null; dias_restantes: number | null } | null;
  turno: {
    janelas: { inicio: string; fim: string; nome: string; ativa: boolean }[];
    ativa: { inicio: string; fim: string; nome: string } | null;
    proxima: { inicio: string; fim: string; em_min: number } | null;
    configurado: boolean;
  };
  cobertura_24h: SaudeBlocoCobertura[];
  intervalo_min: number;
  // Fase 65: fuso da FÁBRICA usado para tudo acima. Vem no payload porque
  // fuso errado é erro silencioso — o painel continua bonito e mente o dia
  // inteiro. Na tela, alguém percebe no primeiro olhar.
  fuso?: string;
  agora_local?: string;
}

export interface FusoProcesso {
  configurado: string | null;
  efetivo: string;
  padrao_ambiente: string;
  agora_local: string;
  sugestoes: string[];
}

export interface DiaHora {
  hora: number;
  seg: number;
  va_pct: number;
  desp_pct: number;
  // Fase 56: posto vazio é CATEGORIA própria — antes inflava o denominador da
  // hora sem entrar em fatia nenhuma e aparecia como "não classificado".
  vazio_pct: number;
}

export interface DiaAnalise {
  dia: string; // ISO
  rot: string; // "18/07"
  dow: string; // "sex"
  tempo_obs_s: number;
  va_pct: number;
  desp_pct: number;
  vazio_pct: number;
  // B5: % do tempo observado em DÚVIDA (concordância abaixo do limiar ou
  // camada ativa). É o veredito do produto — cai = o sistema aprende.
  // Fase 66: `duvida_pct` é a dúvida LEVANTADA naquele dia — histórica, não
  // se reescreve quando alguém valida. `duvida_resolvida_pct` é a parte dela
  // já julgada por gente. Antes validar apagava o dia do histórico, e a curva
  // que existe para provar aprendizado era zerada pelo ato de aprender.
  duvida_pct: number;
  atipico_vazio?: boolean;
  // Fase 85: versões do instrumento presentes no dia. Mais de uma = dia do
  // deploy; o número dos dois lados não é comparável.
  versoes_instrumento?: number[];
  duvida_resolvida_pct?: number;
  sem_evidencia_resolvida_pct?: number;
  // Trecho curto demais para afirmar OU duvidar — resolve-se com mais
  // amostragem, não com melhor decisão. Caso DIFERENTE de duvida_pct.
  sem_evidencia_pct: number;
  // Fase 90 — "NÃO OLHEI", que é diferente de "olhei e não sei". Sobe quando o
  // gate suprime minutos inteiros: o tempo continua coberto (a descrição é
  // herdada da âncora) mas nenhum quadro novo foi visto. Nunca soma na dúvida —
  // misturar os dois faria um corte de orçamento parecer perda de confiança do
  // modelo, e a dúvida é a única métrica que responde se o produto funciona.
  nao_observado_pct?: number;
  // A fatia disso causada pelo TETO DO GATE. Se cresce, o teto está agressivo
  // demais — e isso tem de aparecer, não ser descoberto por acaso.
  nao_observado_gate_pct?: number;
  posto_vazio_s: number;
  posto_vazio_pct: number;
  n_videos: number;
  visitas: number;
  primeira_h: string | null;
  ultima_h: string | null;
  top_acao: { label: string; seg: number } | null;
  top_acoes: { label: string; seg: number }[];
  // Fase 35.2: o "filme" do dia — faixas de 15 min com a categoria dominante
  linha_tempo: { ini_m: number; fim_m: number; cat: "va" | "desp" | "vazio" }[];
  por_hora: DiaHora[];
  sem_trabalho: "sem_captura" | "posto_vazio" | null;
}

export interface JanelaAgregada {
  dias: number;
  dias_trabalhados: number;
  dias_sem_trabalho: number;
  tempo_obs_s: number;
  va_pct: number;
  desp_pct: number;
  vazio_pct: number;
  posto_vazio_s: number;
  visitas: number;
  horas_produtivas_dia: number;
}

export interface AnaliseDiaria {
  dias: DiaAnalise[];
  janelas: {
    semana: { atual: JanelaAgregada; anterior: JanelaAgregada; delta_va_pp: number | null };
    mes: { atual: JanelaAgregada; anterior: JanelaAgregada; delta_va_pp: number | null };
  } | null;
  tendencia: { slope_pts_dia: number; direcao: string; dias_considerados: number } | null;
}

export interface FrameReferencia {
  img: string; // data:image/jpeg;base64,...
  largura: number | null;
  altura: number | null;
  video_nome: string | null;
  gravado_em: string | null;
}


// ── Fase 58/59: fila da dúvida ───────────────────────────────────────────────
export type TipoDuvida = "sem_evidencia" | "discordancia" | "camada" | "categoria_assumida";

export interface ItemDuvida {
  id: string;
  video_id: string | null;
  rotulo: string;
  descricao: string | null;
  ini: number;
  fim: number;
  minutos: number;
  confianca: number | null;
  motivo: string;
  tipo: TipoDuvida | "";
  n_amostras: number | null;
  camadas: string[];
  rotulos_competindo: string[];
  cam_id: string | null;
  // Fase 63: 2º ângulo do MESMO instante. `offset_s` é a diferença real de
  // relógio entre as duas câmeras — somá-lo em ini/fim é o que sincroniza.
  segundo_angulo: { segmento_id: string; cam_id: string | null; offset_s: number } | null;
  pessoa: number;
  papel: string | null;
}

export interface FilaDuvidas {
  limiar: number;
  total: number;
  minutos_totais: number;
  por_rotulo: { rotulo: string; eventos: number; minutos: number }[];
  por_tipo: { tipo: TipoDuvida; eventos: number; minutos: number }[];
  itens: ItemDuvida[];
  filtrado_por: string | null;
}

// Fase 62 — chave da generalização automática por processo.
// `configurado` null = herdando o default do ambiente; `efetivo` é o que o
// pipeline vai realmente fazer no próximo vídeo.
export interface MecanismoAprendizado {
  nome: string;
  coberto: boolean;
  efeito: string;
}
export interface EstadoAprendizado {
  processo: string;
  configurado: boolean | null;
  efetivo: boolean;
  padrao_ambiente: boolean;
  mecanismos: MecanismoAprendizado[];
}


// Fase 77 — quanto custa QUEIMAR uma descrição (marcá-la como inválida).
// A queima tira a frase do APRENDIZADO em todos os eventos que a usam, não só
// no card aberto. Reversível: reabrir o evento limpa a marca.
export interface UsoDescricao {
  descricao: string;
  eventos: number;
  minutos: number;
  rotulos: { rotulo: string; eventos: number }[];
  ja_queimada: boolean;
  reversivel: boolean;
}


// Fase 79 — auditoria de um dia. SÓ LEITURA: nada aqui entra na fila.
export interface AmostraAuditoria {
  id: string; video_id: string; video_nome: string | null; cam_id: string | null;
  rotulo: string; descricao: string | null;
  ini: number; fim: number; hora: string;
  papel: string | null; origem: string | null;
  posicao: "inicio" | "meio" | "fim" | "unico";
  bloco_eventos: number; bloco_minutos: number;
}
export interface AuditoriaDia {
  dia: string;
  eventos: number;
  minutos: number;
  posto_vazio_pct: number;
  atipico: boolean;
  limiar_atipico: number;
  contradicoes_c1: number;
  videos: { id: string; nome: string | null; cam_id: string | null; duracao_s: number | null }[];
  blocos: { rotulo: string; eventos: number; minutos: number; de: string; ate: string }[];
  amostras: AmostraAuditoria[];
  nota: string;
}


// Fase 91 — o TITULAR do posto, em SOMBRA.
// Identidade ANÔNIMA por papel: `g1`/`g2` valem para UM dia e UMA câmera —
// não são pessoas, não há cadastro, não há re-identificação persistente.
export interface GrupoTitular {
  grupo: string;
  n_tracks: number;
  tempo_posto_s: number;
  minutos_posto: number;
  pct_do_posto: number;
  eh_titular: boolean;
  altura_rel: number | null;
  recorte?: string | null;
  // Fase 93: POR QUE não há recorte. "frame não aquecido" era diagnóstico
  // errado na cam2 — a lateral simplesmente não tem frame por evento.
  recorte_motivo?: string | null;
  tracks: { video_id: string; pessoa_track_id: number; tempo_posto_s: number }[];
}
export interface CameraTitular {
  cam_id: string;
  n_tracks: number;
  n_grupos: number;
  minutos_posto_total: number;
  // null = DIA SEM TITULAR. Não é falha: é a guarda de piso dizendo que
  // ninguém dominou o posto. Coroar um intruso seria pior.
  titular: string | null;
  motivo: string;
  grupos: GrupoTitular[];
}
export interface TitularDia {
  dia: string;
  n_descritores: number;
  cameras: CameraTitular[];
  modo: string;
  limiares: Record<string, number>;
  continuidade: { cam_id: string; similaridade: number; ontem: string; alerta: string }[];
  nota: string;
}


// Fase 87 — o que compõe UM bloco de 15 min da faixa "A jornada de …".
// A largura da cor dentro do bloco é PROPORÇÃO, não horário; a hora de
// verdade de cada trecho está aqui.
export type CatJornada = "va" | "desp" | "vazio";
export interface ItemBinJornada {
  id: string;
  video_id: string;
  video_nome: string | null;
  cam_id: string | null;
  rotulo: string;
  descricao: string | null;
  cat: CatJornada;
  corrigido: boolean;
  hora: string;
  hora_fim: string;
  ini: number | null;
  fim: number | null;
  segundos: number;
  // Evento que atravessa a borda do bloco entra só com a fatia dele.
  segundos_no_bin: number;
  parcial: boolean;
  papel: string | null;
  origem: string | null;
  validado: boolean;
  em_duvida: boolean;
  confianca: number | null;
  n_amostras: number | null;
  track: number | null;
  versao_instrumento: number;
}
export interface BinJornada {
  dia: string;
  bin: number;
  de: string;
  ate: string;
  minutos_bin: number;
  n_eventos: number;
  segundos: number;
  por_categoria: Partial<Record<CatJornada, { segundos: number; pct: number }>>;
  acoes: { rotulo: string; cat: CatJornada; segundos: number; n: number; pct: number }[];
  itens: ItemBinJornada[];
  truncado: boolean;
  buraco: boolean;
  nota: string;
}


// Fase 85 — rótulos sem categoria Lean, do mais CARO para o mais barato.
// Sem categoria, o tempo conta como NÃO-PRODUTIVO: quando nascem vários
// rótulos de uma vez, a produtividade cai por CONTABILIDADE antes de cair por
// medição, e no gráfico de um dia as duas quedas são iguais.
export interface VarianteFamilia {
  label: string;
  minutos: number;
  categoria: CategoriaLean | null;
  origem: string | null;
  versoes: number[];
}
export interface RotuloSemCategoria {
  label: string;
  // Fase 86: raiz da família (`monitorar_maquina_ciclo` → `monitorar_maquina`).
  // A SOMA da família é comparável entre semanas; a decomposição, não.
  familia: string;
  familia_minutos: number;
  familia_variantes: VarianteFamilia[];
  comportamento_id: string | null;
  descricao: string | null;
  categoria_atual: CategoriaLean | null;
  origem_atual: string | null;
  n_eventos: number;
  minutos: number;
  pct_do_tempo: number;
  exemplos: string[];
  versoes: number[];
}
export interface RotulosSemCategoria {
  itens: RotuloSemCategoria[];
  n_rotulos: number;
  minutos_sem_categoria: number;
  pct_sem_categoria: number;
  minutos_observados: number;
  nota: string;
}
