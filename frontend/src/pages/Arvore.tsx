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
import { nomeHumano } from "../design/rotulos";
import type { ProcHeaderMock } from "../lib/adapt";
import type { CategoriaLean, DistribuicaoComportamento } from "../lib/types";

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

/** Percentual da folha, nunca "0%".
 *
 *  A folha só chega aqui se tiver tempo medido (o filtro é acima). Tempo real
 *  que arredonda para zero vira "<1%" — dizer "0%" para algo que aconteceu é
 *  errado, e some com a única informação que a linha carrega. */
function pctFolha(pct: number): string {
  if (pct >= 1) return `${Math.round(pct)}%`;
  return "<1%";
}

export default function Arvore({ proc }: { proc: ProcHeaderMock }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["dashboard", proc.id],
    queryFn: () => api.processos.dashboard(proc.id),
  });
  const [salvando, setSalvando] = useState<string | null>(null);
  const [ramo, setRamo] = useState<Ramo>("va");

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

  const dist = q.data?.snapshot.distribuicao_comportamentos || [];

  const porRamo = useMemo(() => {
    const g: Record<Ramo, DistribuicaoComportamento[]> = { va: [], desp: [], sem: [] };
    for (const d of dist) {
      // ⚠️ O FILTRO DO 0%. Rótulo sem tempo medido não é galho da árvore: é
      // linha de catálogo que nunca foi observada neste posto. Aparecia como
      // "Lendo o desenho técnico 0%" ao lado de atividades reais, dando ao
      // cliente a impressão de que o sistema mediu e deu zero — quando o que
      // houve foi não ter medido nada.
      if (!(d.tempo_total_s > 0)) continue;
      const r: Ramo = d.categoria_lean === "valor_agregado" ? "va"
        : d.categoria_lean === "desperdicio" ? "desp" : "sem";
      g[r].push(d);
    }
    for (const k of Object.keys(g) as Ramo[]) g[k].sort((a, b) => b.tempo_total_s - a.tempo_total_s);
    return g;
  }, [dist]);

  const totalS = useMemo(
    () => dist.reduce((t, d) => t + (d.tempo_total_s > 0 ? d.tempo_total_s : 0), 0),
    [dist]
  );

  const pctDoRamo = (r: Ramo) => {
    const s = porRamo[r].reduce((t, d) => t + d.tempo_total_s, 0);
    return totalS > 0 ? (100 * s) / totalS : 0;
  };

  if (q.isLoading) return <Empty icon="loader" title="Montando a árvore…" />;
  if (!q.data) {
    return <Empty icon="alert-triangle" title="Não foi possível carregar"
                  desc={q.error ? String((q.error as Error).message || q.error) : undefined} />;
  }

  const nGalhos = porRamo.va.length + porRamo.desp.length + porRamo.sem.length;
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
  const opcoes: Ramo[] = porRamo.sem.length > 0 ? ["va", "desp", "sem"] : ["va", "desp"];
  const atual = RAMOS[ramo];
  const itens = porRamo[ramo];

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
                key={d.comportamento}
                d={d}
                cor={atual.cor}
                ramo={ramo}
                ultima={i === itens.length - 1}
                salvando={salvando === d.comportamento}
                onMover={(cat) => classificar.mutate({ label: d.comportamento, cat })}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Folha({
  d, cor, ramo, ultima, salvando, onMover,
}: {
  d: DistribuicaoComportamento;
  cor: string;
  ramo: Ramo;
  ultima: boolean;
  salvando: boolean;
  onMover: (cat: CategoriaLean) => void;
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
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
          {nomeHumano(d.comportamento)}
        </span>
        <span className="tnum" style={{ fontSize: 14, fontWeight: 700, color: cor }}>
          {pctFolha(d.pct_tempo)}
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
            onClick={() => onMover(t.cat)}
            disabled={salvando}
            title={`Mover "${nomeHumano(d.comportamento)}" para ${t.rot.toLowerCase()}`}
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
