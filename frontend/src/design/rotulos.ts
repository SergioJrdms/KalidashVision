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

// SEMENTE, não contrato: só os rótulos em que a conversão automática ficaria
// pobre ou ambígua. Tudo o que não estiver aqui é convertido sozinho.
const SEMENTE: Record<string, string> = {
  operar_torno: "Operando o torno",
  posto_vazio: "Posto vazio",
  monitorar_maquina: "Acompanhando a máquina",
  conversando_colega: "Conversando com colega",
  acao_indefinida: "Ação não identificada",
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
 *  Ninguém na fábrica pensa em "130 minutos" nem em "0,25 do turno". */
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
// "não classificado" ou "concordância". E unidade que a pessoa usa: 2h10, não
// 130 minutos e muito menos 0,25.
// ============================================================
export interface LeituraDoPosto {
  frase: string;
  ressalva: string | null;
  tom: "ok" | "atencao" | "fraco";
}

export function leituraDoPosto(d: {
  vaPct: number;
  vazioPct: number;
  tempoObservadoMin: number;
  semEvidenciaPct?: number;
  naoObservadoPct?: number;
}): LeituraDoPosto {
  const obsSeg = Math.max(0, d.tempoObservadoMin) * 60;
  const vazioSeg = obsSeg * (Math.max(0, d.vazioPct) / 100);
  const duvida = Math.max(d.semEvidenciaPct || 0, d.naoObservadoPct || 0);

  // COBERTURA INSUFICIENTE — não arredonda para uma frase confiante.
  if (d.tempoObservadoMin < 30) {
    return {
      frase: `Ainda há pouco material para concluir: ${duracaoHumana(obsSeg)} de gravação analisada.`,
      ressalva: "Com menos de meia hora observada, qualquer percentual oscila demais para valer como leitura.",
      tom: "fraco",
    };
  }

  const partes: string[] = [
    `O posto rendeu ${Math.round(d.vaPct)}% do tempo observado.`,
  ];
  if (vazioSeg >= 60) {
    partes.push(
      `O operador esteve ausente ${duracaoHumana(vazioSeg)}` +
      ` — o equivalente a ${Math.round(d.vazioPct)}% do que foi filmado.`,
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
