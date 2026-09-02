// ============================================================
// O QUE É TRABALHO — a árvore do vocabulário aprendido.
//
// Pedida na reunião de 12/08: "eu quero uma imagem do sistema: de tudo que eu
// já gerei para você, isso aqui é produtivo, isso aqui é improdutivo."
//
// E o propósito, que é o que define o desenho: é para o CLIENTE APONTAR O QUE
// FALTA — "é muito mais fácil do cara falar: ó, tem mais essa daqui que é
// improdutiva."
//
// ⚠️ AGORA É ÁRVORE DE VERDADE, não três listas empilhadas. Uma RAIZ por vez
// no topo, as atividades pendendo dela por uma espinha visível, e um seletor
// para trocar de raiz. Ver produtivo e improdutivo lado a lado convidava à
// comparação errada — o que importa é "o que compõe ESTE lado".
//
// ⚠️ NENHUM CÁLCULO NOVO. Os números vêm de `distribuicao_comportamentos`, o
// mesmo dado do Dashboard. Esta tela reagrupa e traduz — não mede nada.
//
// ⚠️ NENHUMA FOLHA COM 0%. Rótulo sem tempo medido NÃO É UM GALHO — é uma
// linha de catálogo que nunca foi observada, e desenhá-la como atividade do
// posto é a mesma "ausência de medida virando medida" que já mordeu este
// projeto cinco vezes. Tempo real porém minúsculo aparece como "<1%", que é
// honesto; zero não aparece.
//
// ⛔ E nenhuma duração absoluta: a captura amostra ~50% de cada hora, então
// percentual é estimativa correta do turno e minuto seria metade da verdade.
// ============================================================
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, PanelHead, Empty, Icon, toast } from "../design/ui";
import { leanCor } from "../design/helpers";
import { nomeHumano, familiaLabel } from "../design/rotulos";
import type { ProcHeaderMock } from "../lib/adapt";
import type { CategoriaLean, DistribuicaoComportamento } from "../lib/types";
import { EventEvidenceDrawer } from "../components/EventEvidenceDrawer";

type Ramo = "va" | "desp" | "sem";

const RAMOS: Record<Ramo, { titulo: string; cor: string; ajuda: string }> = {
  va: {
    titulo: "Produtivo",
    cor: leanCor("va"),
    ajuda: "O tempo que agrega valor à peça.",
  },
  desp: {
    titulo: "Improdutivo",
    cor: leanCor("desp"),
    ajuda: "O tempo que não agrega valor: espera, deslocamento, posto vazio.",
  },
  sem: {
    titulo: "Sem classificação",
    cor: "var(--apoio)",
    ajuda: "O que o sistema ainda não sabe julgar. Enquanto ninguém decide, conta como improdutivo.",
  },
};

/** Percentual da folha, nunca "0%" e nunca "<1%".
 *
 *  ⚠️ "<1%" era uma resposta ruim para uma pergunta boa. Ele nasceu certo —
 *  dizer "0%" para algo que aconteceu é errado —, mas apagava a única
 *  informação que a linha carregava: SETE folhas com "<1%" empilhadas viram um
 *  muro em que nada se distingue, e o gestor lê "o sistema não sabe medir isso"
 *  em vez de "isso é pouco". 0,4% e 0,04% são coisas MUITO diferentes, e ambas
 *  viravam o mesmo símbolo.
 *
 *  Abaixo de 1 vai uma casa decimal; abaixo de 0,1 vai duas. Nenhuma folha
 *  chega aqui com zero (o filtro é acima), então nunca sai "0%". */
function pctFolha(pct: number): string {
  if (pct >= 10) return `${Math.round(pct)}%`;
  if (pct >= 1) return `${pct.toFixed(1).replace(".", ",")}%`;
  if (pct >= 0.1) return `${pct.toFixed(1).replace(".", ",")}%`;
  return `${pct.toFixed(2).replace(".", ",")}%`;
}

// ═══════════════════════════════════════════════════════════════════════
// ⭐ POR QUE A CAUDA ERA TODA "<1%" — e a maior parte disso era ARTEFATO.
//
// Duas coisas somavam, e nenhuma delas era "essa atividade é rara":
//
//  1. A ÁRVORE LISTAVA RÓTULO CRU, NÃO FAMÍLIA. `nomeHumano` já colapsa a
//     família na hora de escrever o nome (`operar_torno_ciclo` e
//     `operar_torno` viram "Operando o torno"), mas as LINHAS continuavam
//     separadas. Resultado: duas linhas com o MESMO nome na tela, cada uma
//     com metade do tempo — e as duas metades pequenas o bastante para
//     virarem "<1%". O tempo estava certo; o que estava errado era desenhar
//     como duas atividades o que o próprio sistema já sabe ser uma.
//
//  2. O DENOMINADOR ERA O TOTAL, NÃO O RAMO. A tela existe para responder "o
//     que compõe ESTE lado" — está escrito no cabeçalho dela — mas mostrava a
//     fatia do turno inteiro. Uma atividade que é 2% do trabalho produtivo
//     aparecia como 1% e parecia irrelevante, quando na composição do lado
//     produtivo ela é um vigésimo.
//
// ⚠️ NADA AQUI INFLA NÚMERO. Somar variantes da mesma família é a mesma conta
// que o backend já faz para leitura de tendência, e a fatia do ramo continua
// vindo acompanhada da fatia do turno. O que muda é a família aparecer inteira
// e a pergunta certa ser respondida primeiro.
//
// A cauda que SOBRA depois disso é real, e vai para um "outras N ações" com a
// soma delas — visível, somada e expansível. Esconder seria mentir; empilhar
// sete linhas indistinguíveis também era.
// ═══════════════════════════════════════════════════════════════════════
type Galho = {
  familia: string;
  labels: string[];          // rótulos crus somados nesta folha
  tempo_total_s: number;
  ocorrencias: number;
  pct_tempo: number;         // do turno observado (o número antigo)
  pct_ramo: number;          // deste lado da árvore
  categoria_lean: CategoriaLean | null;
  categoria_lean_origem: string | null;
  descricao: string;
};

// Abaixo disto a folha vai para o agrupador da cauda. 3% do RAMO é o ponto em
// que a linha deixa de ser legível como fatia — não é um número mágico, é o
// limite em que a barra mental do leitor para de distinguir.
const CAUDA_PCT_RAMO = 3;

export default function Arvore({ proc }: { proc: ProcHeaderMock }) {
  const q = useQuery({
    queryKey: ["dashboard", proc.id],
    queryFn: () => api.processos.dashboard(proc.id),
  });
  if (q.isLoading) return <Empty icon="loader" title="Montando a árvore…" />;
  if (!q.data) {
    return <Empty icon="alert-triangle" title="Não foi possível carregar"
                  desc={q.error ? String((q.error as Error).message || q.error) : undefined} />;
  }
  return <ArvoreProdutividade proc={proc} distribuicao={q.data.snapshot.distribuicao_comportamentos} />;
}

/** Árvore funcional, reutilizável com a distribuição já carregada pelo Dashboard. */
export function ArvoreProdutividade({
  proc,
  distribuicao,
}: {
  proc: ProcHeaderMock;
  distribuicao: DistribuicaoComportamento[];
}) {
  const qc = useQueryClient();
  const [salvando, setSalvando] = useState<string | null>(null);
  const [ramo, setRamo] = useState<Ramo>("va");
  const [evidencia, setEvidencia] = useState<Galho | null>(null);

  const classificar = useMutation({
    mutationFn: ({ label, cat }: { label: string; cat: CategoriaLean }) =>
      api.comportamentos.setCategoriaPorLabel(proc.id, label, cat),
    onMutate: ({ label }) => setSalvando(label),
    onSettled: () => setSalvando(null),
    onSuccess: (_r, { label, cat }) => {
      toast(`"${nomeHumano(label)}" agora é ${cat === "valor_agregado" ? "produtivo" : "improdutivo"}`);
      // A decisão muda o número em todas as telas que o mostram.
      qc.invalidateQueries({ queryKey: ["dashboard", proc.id] });
      qc.invalidateQueries({ queryKey: ["diaadia", proc.id] });
      qc.invalidateQueries({ queryKey: ["rotulos-sem-categoria", proc.id] });
    },
    onError: (e: unknown) => toast(`Não deu para classificar: ${String(e)}`),
  });

  const dist = distribuicao;

  const porRamo = useMemo(() => {
    // Agrupa por (ramo, FAMÍLIA). A chave inclui o ramo porque duas variantes
    // da mesma família podem ter classificação diferente — e nesse caso elas
    // são, de fato, duas folhas: uma de cada lado.
    const acc = new Map<string, Galho>();
    let total = 0;
    for (const d of dist) {
      // ⚠️ O FILTRO DO 0%. Rótulo sem tempo medido não é galho da árvore: é
      // linha de catálogo que nunca foi observada neste posto. Aparecia como
      // "Lendo o desenho técnico 0%" ao lado de atividades reais, dando ao
      // cliente a impressão de que o sistema mediu e deu zero — quando o que
      // houve foi não ter medido nada.
      if (!(d.tempo_total_s > 0)) continue;
      total += d.tempo_total_s;
      const r: Ramo = d.categoria_lean === "valor_agregado" ? "va"
        : d.categoria_lean === "desperdicio" ? "desp" : "sem";
      const fam = familiaLabel(d.comportamento);
      const chave = `${r}|${fam}`;
      const atual = acc.get(chave);
      if (atual) {
        atual.tempo_total_s += d.tempo_total_s;
        atual.ocorrencias += d.ocorrencias || 0;
        atual.labels.push(d.comportamento);
        // A descrição mais longa costuma ser a mais informativa; a curta
        // ("Limpando cavaco") é eco do próprio rótulo.
        if ((d.descricao || "").length > atual.descricao.length) atual.descricao = d.descricao || "";
        // Decisão HUMANA de qualquer variante manda na família inteira: ela
        // vale mais que a automática, e foi tomada olhando a mesma atividade.
        if (d.categoria_lean_origem === "humano") {
          atual.categoria_lean_origem = "humano";
          atual.categoria_lean = d.categoria_lean ?? null;
        }
      } else {
        acc.set(chave, {
          familia: fam, labels: [d.comportamento],
          tempo_total_s: d.tempo_total_s, ocorrencias: d.ocorrencias || 0,
          pct_tempo: 0, pct_ramo: 0,
          categoria_lean: d.categoria_lean ?? null,
          categoria_lean_origem: d.categoria_lean_origem ?? null,
          descricao: d.descricao || "",
        });
      }
    }

    const g: Record<Ramo, Galho[]> = { va: [], desp: [], sem: [] };
    for (const [chave, galho] of acc) g[chave.split("|")[0] as Ramo].push(galho);
    for (const k of Object.keys(g) as Ramo[]) {
      const soma = g[k].reduce((t, x) => t + x.tempo_total_s, 0);
      for (const x of g[k]) {
        x.pct_tempo = total > 0 ? (100 * x.tempo_total_s) / total : 0;
        // ⭐ A fatia DESTE LADO — a pergunta que a tela diz responder.
        x.pct_ramo = soma > 0 ? (100 * x.tempo_total_s) / soma : 0;
      }
      g[k].sort((a, b) => b.tempo_total_s - a.tempo_total_s);
    }
    return { g, total };
  }, [dist]);

  const totalS = porRamo.total;

  const pctDoRamo = (r: Ramo) => {
    const s = porRamo.g[r].reduce((t, d) => t + d.tempo_total_s, 0);
    return totalS > 0 ? (100 * s) / totalS : 0;
  };

  const nGalhos = porRamo.g.va.length + porRamo.g.desp.length + porRamo.g.sem.length;
  if (nGalhos === 0) {
    return (
      <Card style={{ padding: 20 }}>
        <Empty icon="git-branch" title="A árvore ainda não tem galhos"
               desc="Assim que houver atividade observada no posto, ela aparece aqui agrupada por produtivo e improdutivo." />
      </Card>
    );
  }

  // "Sem classificação" só vira opção quando existe tempo lá. Um seletor com
  // uma aba permanentemente vazia treina o olho a ignorá-la.
  const opcoes: Ramo[] = porRamo.g.sem.length > 0 ? ["va", "desp", "sem"] : ["va", "desp"];
  const atual = RAMOS[ramo];
  const todos = porRamo.g[ramo];
  // A cauda só existe quando sobra mais de uma folha pequena: uma folha só,
  // agrupada em "outras 1 ação", seria burocracia sem ganho de leitura.
  const grandes = todos.filter((d) => d.pct_ramo >= CAUDA_PCT_RAMO);
  const cauda = todos.filter((d) => d.pct_ramo < CAUDA_PCT_RAMO);
  const itens = cauda.length > 1 ? grandes : todos;
  const naCauda = cauda.length > 1 ? cauda : [];

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="O que é trabalho"
          ajuda="Tudo o que o sistema já viu neste posto, pendurado no lado a que pertence. A fatia é do tempo observado."
          leitura="Achou algo no lado errado? Use o botão da folha para mandá-la para o outro lado. Sua decisão vale mais que a do sistema e não é desfeita depois."
        />
        <div className="row gap2 wrap" style={{ marginTop: 12 }}>
          {opcoes.map((r) => (
            <button
              key={r}
              onClick={() => setRamo(r)}
              className="row gap1"
              style={{
                border: `1px solid ${ramo === r ? RAMOS[r].cor : "var(--line)"}`,
                background: ramo === r ? RAMOS[r].cor : "transparent",
                color: ramo === r ? "#fff" : "var(--text)",
                borderRadius: 999, padding: "7px 16px", cursor: "pointer",
                fontSize: 13, fontWeight: 700, alignItems: "center",
                transition: "background .15s, border-color .15s",
              }}
            >
              {RAMOS[r].titulo}
              <span className="tnum" style={{ opacity: 0.85, fontWeight: 600 }}>
                {Math.round(pctDoRamo(r))}%
              </span>
            </button>
          ))}
        </div>
      </Card>

      <Card style={{ padding: "24px 22px 20px" }}>
        {/* ── A RAIZ ─────────────────────────────────────────────── */}
        <div className="col" style={{ alignItems: "flex-start", gap: 0 }}>
          <div
            className="row gap2"
            style={{
              alignItems: "baseline", padding: "12px 20px", borderRadius: 12,
              background: atual.cor, color: "#fff", boxShadow: "0 3px 14px rgba(0,0,0,.12)",
            }}
          >
            <span style={{ fontSize: 17, fontWeight: 700, letterSpacing: ".01em" }}>
              {atual.titulo}
            </span>
            <span className="tnum font-display" style={{ fontSize: 22, fontWeight: 700 }}>
              {Math.round(pctDoRamo(ramo))}%
            </span>
          </div>
          <span style={{ fontSize: 12, color: "var(--muted)", marginTop: 7 }}>
            {atual.ajuda}
          </span>
        </div>

        {/* ── OS GALHOS ──────────────────────────────────────────────
            A espinha é uma borda contínua; cada folha ganha um cotovelo por
            pseudo-elemento. Árvore de verdade, sem SVG e sem quebrar no
            celular. */}
        {itens.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--faint)", margin: "22px 0 0 22px" }}>
            Nenhuma atividade deste lado ainda.
          </p>
        ) : (
          <div
            style={{
              marginLeft: 26, marginTop: 4, paddingTop: 14,
              borderLeft: `2px solid ${atual.cor}`, opacity: 1,
            }}
          >
            {itens.map((d, i) => (
              <Folha
                key={d.familia}
                d={d}
                cor={atual.cor}
                ramo={ramo}
                ultima={i === itens.length - 1 && naCauda.length === 0}
                salvando={d.labels.includes(salvando ?? "")}
                // A folha é a FAMÍLIA: a decisão do gestor vale para todas as
                // variantes dela. Aplicar só ao rótulo "principal" deixaria as
                // outras do lado errado, e ele nem saberia que existem.
                onMover={(cat) => d.labels.forEach(
                  (l) => classificar.mutate({ label: l, cat }))}
                onEvidencia={() => setEvidencia(d)}
              />
            ))}
            {naCauda.length > 0 && (
              <Cauda itens={naCauda} cor={atual.cor} ramo={ramo}
                     salvando={salvando}
                     onEvidencia={(d) => setEvidencia(d)}
                     onMover={(labels, cat) => labels.forEach(
                       (l) => classificar.mutate({ label: l, cat }))} />
            )}
          </div>
        )}
      </Card>
      {evidencia && <EventEvidenceDrawer processoId={proc.id} labels={evidencia.labels} titulo={evidencia.familia}
        categoria={RAMOS[ramo].titulo} onClose={() => setEvidencia(null)} />}
    </div>
  );
}

function Folha({
  d, cor, ramo, ultima, salvando, onMover, onEvidencia,
}: {
  d: Galho;
  cor: string;
  ramo: Ramo;
  ultima: boolean;
  salvando: boolean;
  onMover: (cat: CategoriaLean) => void;
  onEvidencia: () => void;
}) {
  const humano = d.categoria_lean_origem === "humano";
  // O destino é sempre o OUTRO lado. Em "sem classificação" há dois destinos,
  // porque ali nada foi decidido ainda.
  const destinos: { cat: CategoriaLean; rot: string; cor: string }[] =
    ramo === "va" ? [{ cat: "desperdicio", rot: "É improdutivo", cor: leanCor("desp") }]
    : ramo === "desp" ? [{ cat: "valor_agregado", rot: "É produtivo", cor: leanCor("va") }]
    : [{ cat: "valor_agregado", rot: "É produtivo", cor: leanCor("va") },
       { cat: "desperdicio", rot: "É improdutivo", cor: leanCor("desp") }];

  return (
    <div style={{ position: "relative", paddingLeft: 26, paddingBottom: ultima ? 0 : 12 }}>
      {/* cotovelo do galho */}
      <span
        aria-hidden
        style={{
          position: "absolute", left: 0, top: 15, width: 20, height: 2,
          background: cor, borderRadius: 2,
        }}
      />
      {/* tampa a espinha depois da última folha, para o traço não sobrar */}
      {ultima && (
        <span
          aria-hidden
          style={{
            position: "absolute", left: -2, top: 17, bottom: -20, width: 2,
            background: "var(--card, #fff)",
          }}
        />
      )}
      <div
        className="row gap2 wrap"
        style={{
          alignItems: "center", padding: "9px 14px", borderRadius: 10,
          background: "var(--soft)", opacity: salvando ? 0.5 : 1,
          border: "1px solid var(--line)",
        }}
      >
        <button onClick={onEvidencia} title="Ver os eventos que compõem este número" style={{ border: 0, padding: 0, background: "transparent", cursor: "pointer", textAlign: "left", color: "var(--ink)", fontSize: 14, fontWeight: 600 }}>
          {nomeHumano(d.familia)}
        </button>
        {/* ⭐ A fatia DESTE LADO primeiro — é a pergunta da tela. A do turno
            continua ao lado, menor: sem ela o leitor perderia a noção de
            tamanho real e "45% do produtivo" pareceria 45% do dia. */}
        <span className="tnum" style={{ fontSize: 14, fontWeight: 700, color: cor }}>
          {pctFolha(d.pct_ramo)}
        </span>
        <span className="tnum" style={{ fontSize: 11, color: "var(--faint)" }}>
          {pctFolha(d.pct_tempo)} do turno
        </span>
        {humano && (
          // A decisão humana fica visível e é a que manda: nada automático a
          // sobrescreve depois.
          <span style={{ fontSize: 10.5, fontWeight: 700, color: leanCor("va") }}>
            <Icon name="check" size={10} /> você decidiu
          </span>
        )}
        <span className="grow" />
        {destinos.map((t) => (
          <button
            key={t.cat}
            onClick={(e) => { e.stopPropagation(); onMover(t.cat); }}
            disabled={salvando}
            title={d.labels.length > 1
              ? `Mover "${nomeHumano(d.familia)}" (${d.labels.length} variantes) para ${t.rot.toLowerCase()}`
              : `Mover "${nomeHumano(d.familia)}" para ${t.rot.toLowerCase()}`}
            style={{
              border: `1px solid ${t.cor}`, color: t.cor, background: "transparent",
              borderRadius: 999, padding: "4px 12px", fontSize: 11.5, fontWeight: 700,
              cursor: salvando ? "wait" : "pointer", whiteSpace: "nowrap",
            }}
          >
            {t.rot}
          </button>
        ))}
      </div>
      {d.descricao && (
        <span style={{ fontSize: 11.5, color: "var(--faint)", display: "block", marginTop: 4 }}>
          {d.descricao}
        </span>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════
// A CAUDA — as folhas pequenas juntas, somadas e visíveis.
//
// Sete linhas de "0,4%" empilhadas não são sete informações: são um muro que
// esconde as duas folhas que importam e faz a árvore parecer quebrada. Juntas,
// elas viram UM número que o gestor consegue usar ("as miudezas somam 6% do
// produtivo") e continuam abertas a um clique para quem quiser conferir.
//
// ⚠️ Nada é escondido nem descartado: a soma aparece fechada, e cada folha
// continua classificável dentro. Esconder a cauda seria trocar um problema de
// leitura por um problema de honestidade.
// ═══════════════════════════════════════════════════════════════════════
function Cauda({ itens, cor, ramo, salvando, onMover, onEvidencia }: {
  itens: Galho[]; cor: string; ramo: Ramo; salvando: string | null;
  onMover: (labels: string[], cat: CategoriaLean) => void;
  onEvidencia: (item: Galho) => void;
}) {
  const [aberta, setAberta] = useState(false);
  const somaRamo = itens.reduce((t, d) => t + d.pct_ramo, 0);
  const somaTurno = itens.reduce((t, d) => t + d.pct_tempo, 0);
  return (
    <div style={{ position: "relative", paddingLeft: 26 }}>
      <span aria-hidden style={{ position: "absolute", left: 0, top: 15, width: 20,
                                 height: 2, background: cor, borderRadius: 2 }} />
      <span aria-hidden style={{ position: "absolute", left: -2, top: 17, bottom: -20,
                                 width: 2, background: "var(--card, #fff)" }} />
      <button
        type="button"
        onClick={() => setAberta((v) => !v)}
        aria-expanded={aberta}
        className="row gap2 wrap"
        style={{
          width: "100%", textAlign: "left", alignItems: "center", cursor: "pointer",
          padding: "9px 14px", borderRadius: 10, background: "var(--soft)",
          border: "1px dashed var(--line-2)",
        }}
      >
        <Icon name={aberta ? "chevron-down" : "chevron-right"} size={15} />
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
          Outras {itens.length} atividades
        </span>
        <span className="tnum" style={{ fontSize: 14, fontWeight: 700, color: cor }}>
          {pctFolha(somaRamo)}
        </span>
        <span className="tnum" style={{ fontSize: 11, color: "var(--faint)" }}>
          {pctFolha(somaTurno)} do turno · juntas
        </span>
      </button>
      {aberta && (
        <div className="col" style={{ gap: 10, margin: "12px 0 0 14px",
                                      paddingLeft: 12, borderLeft: `2px solid var(--line-2)` }}>
          {itens.map((d, i) => (
            <Folha
              key={d.familia}
              d={d}
              cor={cor}
              ramo={ramo}
              ultima={i === itens.length - 1}
              salvando={d.labels.includes(salvando ?? "")}
              onMover={(cat) => onMover(d.labels, cat)}
              onEvidencia={() => onEvidencia(d)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
