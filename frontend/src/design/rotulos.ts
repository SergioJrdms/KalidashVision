// ============================================================
// Fase 96 — NOME HUMANO. Camada de TRADUÇÃO, só na exibição.
//
// A interface mostrava identificadores internos em fonte monoespaçada —
// `operar_torno`, `monitorar_maquina_parada` — e isso denuncia o banco de
// dados. Num criativo de marketing ou numa demo para cliente, é a diferença
// entre um produto e um protótipo.
//
// ⚠️ O IDENTIFICADOR CONTINUA SENDO A CHAVE. Banco, API, validação,
// comparação entre dias: tudo continua em `operar_torno`. Só o TEXTO na tela
// muda. Qualquer coisa que compare, agrupe ou grave usa a chave — traduzir na
// camada errada quebraria a série histórica sem ninguém perceber.
//
// ⚠️ E NÃO É UM DICIONÁRIO QUE EXIGE DEPLOY. O vocabulário cresce sozinho: o
// dicionário abaixo é SEMENTE, não contrato. Rótulo novo cai no conversor
// automático e aparece legível no mesmo instante em que nasce — nunca cru.
// ============================================================

// Sufixos de cena da Fase 86, REVOGADA na Fase 88 (o discriminador media
// ruído). Os rótulos com sufixo continuam no histórico e não são renomeados,
// porque renomear reescreve o passado — mas o sufixo é resíduo de uma decisão
// desfeita e não tem por que aparecer para ninguém.
const SUFIXOS_CENA = ["_ciclo", "_parada", "_imovel"];

/** Raiz da família: `monitorar_maquina_parada_ciclo` → `monitorar_maquina`.
 *  Descasca em laço porque o histórico tem sufixo duplo (o LLM batizava o
 *  rótulo já com o estado dentro e o sufixo mecânico era colado por cima). */
export function familiaLabel(label: string): string {
  let base = (label || "").trim();
  let mudou = true;
  while (mudou) {
    mudou = false;
    for (const s of SUFIXOS_CENA) {
      if (base.endsWith(s) && base.length > s.length) {
        base = base.slice(0, -s.length);
        mudou = true;
        break;
      }
    }
  }
  return base;
}

/** True se o rótulo afirma estado da máquina — coisa que nenhum sinal mede.
 *
 *  Fase 99 — usar SÓ para filtrar ESCOLHA (o que o gestor pode atribuir a um
 *  evento), nunca LEITURA. O histórico tem 896 eventos assim e continua sendo
 *  mostrado; o que não pode é nascer o 897º.
 *
 *  O backend limpa o sufixo em qualquer correção que chegue. Sem este filtro,
 *  a lista de opções ofereceria `monitorar_maquina_parada`, o gestor escolheria
 *  de boa-fé e o banco gravaria outra coisa — reescrita silenciosa, que é pior
 *  que recusar. Aqui a opção simplesmente não existe. */
export function afirmaEstado(label: string | null | undefined): boolean {
  const cru = String(label ?? "").trim();
  return !!cru && familiaLabel(cru) !== cru;
}

// ⚠️ Fase 100 — OS DOIS CARIMBOS DE AUSÊNCIA DE RÓTULO.
// `acao_indefinida` é o histórico (o modelo escolhia um balde com cara de
// atividade); `nao_nomeado` é o regime novo (o cluster não nomeou e o evento
// foi para a fila). Nenhum dos dois é atividade — e por isso nenhum dos dois
// pode ser oferecido como escolha nem exibido como se fosse uma.
const SEM_ROTULO = new Set(["acao_indefinida", "nao_nomeado"]);

/** True quando o "rótulo" é, na verdade, a ausência de um. */
export function semRotulo(label: string | null | undefined): boolean {
  return SEM_ROTULO.has(String(label ?? "").trim());
}

/** Opções de correção: sem duplicata de família, sem afirmação de estado e
 *  sem os carimbos de ausência — ninguém "corrige" um evento PARA sem-nome. */
export function rotulosAtribuiveis(labels: (string | null | undefined)[]): string[] {
  const vistos = new Set<string>();
  for (const l of labels) {
    const cru = String(l ?? "").trim();
    if (!cru || afirmaEstado(cru) || semRotulo(cru)) continue;
    vistos.add(cru);
  }
  return [...vistos].sort();
}

// SEMENTE, não contrato: só os rótulos em que a conversão automática ficaria
// pobre ou ambígua. Tudo o que não estiver aqui é convertido sozinho.
const SEMENTE: Record<string, string> = {
  operar_torno: "Operando o torno",
  posto_vazio: "Posto vazio",
  monitorar_maquina: "Acompanhando a máquina",
  conversando_colega: "Conversando com colega",
  // Fase 100: os dois carimbos de ausência não recebem nome de atividade. O
  // texto diz o que É — um item de trabalho para o gestor —, não finge que o
  // sistema observou uma ação chamada "indefinida".
  acao_indefinida: "Sem nome — aguardando você",
  nao_nomeado: "Sem nome — aguardando você",
  ajustar_maquina: "Ajustando a máquina",
  preparar_maquina: "Preparando a máquina",
  medir_peca: "Medindo a peça",
  limpando_cavaco: "Limpando cavaco",
  lendo_desenho_tecnico: "Lendo o desenho técnico",
  interagir_com_colega_ou_lider: "Conversando com colega ou líder",
  deslocar_buscar_material_ferramenta: "Buscando material ou ferramenta",
};

/** Nome legível de um rótulo. NUNCA devolve o identificador cru.
 *
 *  Ordem: semente → conversão automática. O sufixo de cena é removido antes
 *  das duas, então `operar_torno_ciclo` e `operar_torno` mostram o mesmo nome
 *  — que é o correto, porque a distinção que o sufixo carregava foi revogada. */
export function nomeHumano(label: string | null | undefined): string {
  const raiz = familiaLabel(String(label ?? "").trim());
  if (!raiz) return "Sem rótulo";
  const semente = SEMENTE[raiz];
  if (semente) return semente;
  // Conversão automática: underscore vira espaço e a primeira letra sobe.
  // É o que garante que rótulo novo nasce legível sem deploy nenhum.
  const texto = raiz.replace(/_/g, " ").trim();
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

/** True quando o nome exibido veio da conversão automática — útil para a tela
 *  de classificação, onde saber que o rótulo é novo importa. */
export function nomeEhAutomatico(label: string | null | undefined): boolean {
  return !SEMENTE[familiaLabel(String(label ?? "").trim())];
}

/** Duração em unidade de chão de fábrica: 2h10, 45min, 30s.
 *
 *  ⛔ Fase 101 — PROIBIDA EM SUPERFÍCIE DO CLIENTE. Continua existindo para
 *  ferramenta interna (a validação usa o instante do trecho como localizador
 *  no vídeo). Em dashboard, árvore, Pareto, evolução por dia, ritmo por hora,
 *  relatório ou exportação, use percentual: a captura amostra ~50% de cada
 *  hora, então a duração absoluta é metade da verdade.
 *  `tests_permanencia_numero.py` bloco [6] varre os arquivos e reprova. */
export function duracaoHumana(segundos: number): string {
  const s = Math.max(0, Math.round(segundos));
  if (s < 60) return `${s}s`;
  const min = Math.round(s / 60);
  if (min < 60) return `${min}min`;
  const h = Math.floor(min / 60);
  const resto = min % 60;
  return resto ? `${h}h${String(resto).padStart(2, "0")}` : `${h}h`;
}

// ============================================================
// Fase 96 — A CONCLUSÃO EM PORTUGUÊS
//
// O Dashboard tem números e gráficos, e faltava a frase que o dono de fábrica
// lê sem precisar de alguém explicando ao lado.
//
// ⚠️ GERADA POR REGRA, não por LLM: precisa ser previsível e não pode custar
// token. E só afirma o que foi MEDIDO — com pouca cobertura ou muita dúvida,
// a frase diz isso em vez de fingir precisão.
//
// Vocabulário de chão de fábrica: nada de "valor agregado", "categoria Lean",
// "não classificado" ou "concordância".
//
// ⛔ Fase 101 — E NENHUMA DURAÇÃO. Só percentual. Ver `leituraDoPosto`.
// ============================================================
export interface LeituraDoPosto {
  frase: string;
  ressalva: string | null;
  tom: "ok" | "atencao" | "fraco";
}

export function leituraDoPosto(d: {
  vaPct: number;
  vazioPct: number;
  /** ⛔ LIMIAR, nunca exibido. Nome explícito de propósito: o nome antigo
   *  parecia um número para mostrar, e a varredura da Fase 101 (que bane
   *  duração em superfície do cliente) o pegava com razão. Ele decide se há
   *  material suficiente para concluir — não vai para a tela. */
  limiarCoberturaMin: number;
  semEvidenciaPct?: number;
  naoObservadoPct?: number;
}): LeituraDoPosto {
  const duvida = Math.max(d.semEvidenciaPct || 0, d.naoObservadoPct || 0);

  // ⛔ Fase 101 — A DURAÇÃO SAIU DAQUI. Esta frase dizia "o operador esteve
  // ausente 2h10". A captura é uma amostra sistemática de ~50% de cada hora:
  // esse "2h10" era METADE do tempo real de ausência, apresentado como se
  // fosse o total. Não era feio — era errado. O percentual, sobre o mesmo
  // denominador amostrado, é estimativa correta do turno.
  //
  // A cobertura continua entrando como LIMIAR (`limiarCoberturaMin`), que é
  // decisão interna, não número exibido.

  // COBERTURA INSUFICIENTE — não arredonda para uma frase confiante.
  if (d.limiarCoberturaMin < 30) {
    return {
      frase: "Ainda há pouco material para concluir.",
      ressalva: "Com pouco tempo observado, qualquer percentual oscila demais para valer como leitura.",
      tom: "fraco",
    };
  }

  const partes: string[] = [
    `O posto rendeu ${Math.round(d.vaPct)}% do tempo observado.`,
  ];
  if (d.vazioPct >= 1) {
    partes.push(
      `O operador esteve ausente em ${Math.round(d.vazioPct)}% do que foi filmado.`,
    );
  } else {
    partes.push("O operador esteve no posto praticamente o tempo todo filmado.");
  }

  // DÚVIDA ALTA — a frase continua, mas com a ressalva colada. O número não é
  // escondido nem apresentado como se fosse firme.
  let ressalva: string | null = null;
  let tom: LeituraDoPosto["tom"] = d.vaPct >= 70 ? "ok" : "atencao";
  if (duvida >= 20) {
    ressalva = `Em ${Math.round(duvida)}% do tempo o sistema não teve como afirmar o que estava acontecendo — esse pedaço entra como improdutivo até alguém decidir.`;
    tom = "fraco";
  } else if (duvida >= 8) {
    ressalva = `Em ${Math.round(duvida)}% do tempo a leitura ficou incerta.`;
  }
  return { frase: partes.join(" "), ressalva, tom };
}
