// ============================================================
// Fase 96 — O QUE É TRABALHO. A árvore do vocabulário aprendido.
//
// Pedida na reunião de 12/08: "eu quero uma imagem do sistema: de tudo que eu
// já gerei para você, isso aqui é produtivo, isso aqui é improdutivo."
//
// E o propósito, que é o que define o desenho: é para o CLIENTE APONTAR O QUE
// FALTA — "é muito mais fácil do cara falar: ó, tem mais essa daqui que é
// improdutiva." Por isso a árvore não é só um gráfico: mover uma folha
// classifica o rótulo. É a mesma decisão da tela "Classificar rótulos", com
// uma interface que não precisa de treinamento.
//
// ⚠️ NENHUM CÁLCULO NOVO. Os números vêm de `distribuicao_comportamentos`, o
// mesmo dado do Dashboard. Esta tela reagrupa e traduz — não mede nada.
//
// ⚠️ O RAMO "SEM CLASSIFICAÇÃO" APARECE SEMPRE, mesmo vazio. É onde o cliente
// vê o que o sistema ainda não sabe julgar, e é o convite que o sócio pediu.
// ============================================================
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, PanelHead, Empty, Icon, toast } from "../design/ui";
import { leanCor } from "../design/helpers";
import { nomeHumano, duracaoHumana } from "../design/rotulos";
import type { ProcHeaderMock } from "../lib/adapt";
import type { CategoriaLean, DistribuicaoComportamento } from "../lib/types";

type Ramo = "va" | "desp" | "sem";

const RAMOS: { id: Ramo; titulo: string; cor: string; ajuda: string }[] = [
  { id: "va", titulo: "Produtivo", cor: leanCor("va"),
    ajuda: "O tempo que agrega valor à peça." },
  { id: "desp", titulo: "Improdutivo", cor: leanCor("desp"),
    ajuda: "O tempo que não agrega valor: espera, deslocamento, posto vazio." },
  { id: "sem", titulo: "Sem classificação", cor: "var(--apoio)",
    ajuda: "O que o sistema ainda não sabe julgar. Enquanto ninguém decide, conta como improdutivo." },
];

export default function Arvore({ proc }: { proc: ProcHeaderMock }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["dashboard", proc.id],
    queryFn: () => api.processos.dashboard(proc.id),
  });
  const [salvando, setSalvando] = useState<string | null>(null);
  const [arrastando, setArrastando] = useState<string | null>(null);

  const classificar = useMutation({
    mutationFn: ({ label, cat }: { label: string; cat: CategoriaLean }) =>
      api.comportamentos.setCategoriaPorLabel(proc.id, label, cat),
    onMutate: ({ label }) => setSalvando(label),
    onSettled: () => { setSalvando(null); setArrastando(null); },
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
      const r: Ramo = d.categoria_lean === "valor_agregado" ? "va"
        : d.categoria_lean === "desperdicio" ? "desp" : "sem";
      g[r].push(d);
    }
    for (const k of Object.keys(g) as Ramo[]) g[k].sort((a, b) => b.tempo_total_s - a.tempo_total_s);
    return g;
  }, [dist]);

  const totalS = dist.reduce((t, d) => t + d.tempo_total_s, 0);
  const maiorS = Math.max(1, ...dist.map((d) => d.tempo_total_s));

  const soltarEm = (ramo: Ramo) => {
    if (!arrastando || ramo === "sem") { setArrastando(null); return; }
    classificar.mutate({
      label: arrastando,
      cat: ramo === "va" ? "valor_agregado" : "desperdicio",
    });
  };

  if (q.isLoading) return <Empty icon="loader" title="Montando a árvore…" />;
  if (!q.data) {
    return <Empty icon="alert-triangle" title="Não foi possível carregar"
                  desc={q.error ? String((q.error as Error).message || q.error) : undefined} />;
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="O que é trabalho"
          ajuda="Tudo o que o sistema já viu neste posto, agrupado pelo que conta como produtivo e pelo que não conta. O tamanho de cada barra é o tempo que aquilo ocupou."
          leitura="Achou algo no lugar errado? Arraste para o outro lado — ou use os botões. Sua decisão vale mais que a do sistema e não é desfeita depois."
        />
        <div className="row gap2 wrap" style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>
          <span><b style={{ color: "var(--ink)" }}>{duracaoHumana(totalS)}</b> observadas</span>
          <span>·</span>
          <span><b style={{ color: "var(--ink)" }}>{dist.length}</b> tipos de atividade reconhecidos</span>
        </div>
      </Card>

      <div className="col" style={{ gap: 12 }}>
        {RAMOS.map((r) => (
          <BlocoRamo
            key={r.id}
            ramo={r}
            itens={porRamo[r.id]}
            totalS={totalS}
            maiorS={maiorS}
            salvando={salvando}
            arrastando={arrastando}
            onArrastar={setArrastando}
            onSoltar={() => soltarEm(r.id)}
            onClassificar={(label, cat) => classificar.mutate({ label, cat })}
          />
        ))}
      </div>
    </div>
  );
}

function BlocoRamo({ ramo, itens, totalS, maiorS, salvando, arrastando,
                    onArrastar, onSoltar, onClassificar }: {
  ramo: { id: Ramo; titulo: string; cor: string; ajuda: string };
  itens: DistribuicaoComportamento[];
  totalS: number; maiorS: number;
  salvando: string | null; arrastando: string | null;
  onArrastar: (l: string | null) => void;
  onSoltar: () => void;
  onClassificar: (label: string, cat: CategoriaLean) => void;
}) {
  const somaS = itens.reduce((t, d) => t + d.tempo_total_s, 0);
  const pct = totalS > 0 ? (somaS / totalS) * 100 : 0;
  // Só recebe solta quem é um destino válido: "sem classificação" é de onde as
  // coisas SAEM, não para onde vão — despromover para "não sei" seria apagar
  // uma decisão humana, e decisão humana não se desfaz sozinha.
  const aceita = ramo.id !== "sem" && !!arrastando;
  return (
    <Card
      onDragOver={(e: React.DragEvent) => { if (aceita) e.preventDefault(); }}
      onDrop={(e: React.DragEvent) => { if (aceita) { e.preventDefault(); onSoltar(); } }}
      style={{
        padding: 16,
        borderLeft: `4px solid ${ramo.cor}`,
        outline: aceita ? `2px dashed ${ramo.cor}` : "none",
        outlineOffset: -6,
        transition: "outline .15s",
      }}
    >
      <div className="row gap2 wrap" style={{ alignItems: "baseline", marginBottom: 10 }}>
        <span style={{ fontSize: 15, fontWeight: 700, color: ramo.cor }}>{ramo.titulo}</span>
        <span className="tnum" style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)" }}>
          {pct.toFixed(0)}%
        </span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          {duracaoHumana(somaS)} · {itens.length} atividade(s)
        </span>
        <span className="grow" />
        <span style={{ fontSize: 11.5, color: "var(--faint)", textAlign: "right" }}>{ramo.ajuda}</span>
      </div>

      {itens.length === 0 ? (
        <div style={{ fontSize: 12.5, color: "var(--faint)", padding: "10px 4px" }}>
          {ramo.id === "sem"
            // Ramo vazio continua na tela: é a pergunta aberta ao cliente.
            ? "Nada pendente — toda atividade que o sistema viu já tem um lado."
            : aceita ? "Solte aqui para classificar." : "Nenhuma atividade deste lado ainda."}
        </div>
      ) : (
        <div className="col" style={{ gap: 6 }}>
          {itens.map((d) => (
            <Folha
              key={d.comportamento}
              d={d}
              maiorS={maiorS}
              cor={ramo.cor}
              ramo={ramo.id}
              salvando={salvando === d.comportamento}
              onArrastar={onArrastar}
              onClassificar={onClassificar}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

function Folha({ d, maiorS, cor, ramo, salvando, onArrastar, onClassificar }: {
  d: DistribuicaoComportamento; maiorS: number; cor: string; ramo: Ramo;
  salvando: boolean;
  onArrastar: (l: string | null) => void;
  onClassificar: (label: string, cat: CategoriaLean) => void;
}) {
  // Peso visual proporcional ao tempo: o que mais pesa tem que saltar.
  const larg = Math.max(4, (d.tempo_total_s / maiorS) * 100);
  const humano = ramo !== "sem" && d.categoria_lean_origem === "humano";
  return (
    <div
      draggable={!salvando}
      onDragStart={() => onArrastar(d.comportamento)}
      onDragEnd={() => onArrastar(null)}
      className="col"
      style={{ gap: 3, padding: "7px 9px", borderRadius: 9, background: "var(--soft)",
               cursor: salvando ? "wait" : "grab", opacity: salvando ? 0.5 : 1 }}
    >
      <div className="row gap2 wrap" style={{ alignItems: "baseline" }}>
        <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>
          {nomeHumano(d.comportamento)}
        </span>
        <span className="tnum" style={{ fontSize: 13, fontWeight: 700, color: cor }}>
          {d.pct_tempo.toFixed(0)}%
        </span>
        <span className="tnum" style={{ fontSize: 11.5, color: "var(--muted)" }}>
          {duracaoHumana(d.tempo_total_s)}
        </span>
        {humano && (
          // A decisão humana fica visível e é a que manda: nada automático a
          // sobrescreve depois.
          <span style={{ fontSize: 10, fontWeight: 700, color: leanCor("va") }}>
            <Icon name="check" size={10} /> você decidiu
          </span>
        )}
        <span className="grow" />
        {/* Os botões existem porque arrastar não funciona bem no celular — e
            os criativos podem ser feitos em tela de celular. */}
        {ramo !== "va" && (
          <BotaoMover cor={leanCor("va")} texto="É produtivo" disabled={salvando}
                      onClick={() => onClassificar(d.comportamento, "valor_agregado")} />
        )}
        {ramo !== "desp" && (
          <BotaoMover cor={leanCor("desp")} texto="É improdutivo" disabled={salvando}
                      onClick={() => onClassificar(d.comportamento, "desperdicio")} />
        )}
      </div>
      <div style={{ height: 6, borderRadius: 4, background: "var(--line-2)", overflow: "hidden" }}>
        <div style={{ width: `${larg}%`, height: "100%", background: cor, opacity: 0.85 }} />
      </div>
      {/* A chave técnica pode aparecer — discreta, nunca como título. Ela é o
          que a validação e a auditoria usam para conversar com o banco. */}
      <span className="font-mono" style={{ fontSize: 9.5, color: "var(--faint)" }}
            title="identificador interno usado pelo sistema">
        {d.comportamento}
      </span>
    </div>
  );
}

function BotaoMover({ cor, texto, disabled, onClick }: {
  cor: string; texto: string; disabled: boolean; onClick: () => void;
}) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{ cursor: disabled ? "wait" : "pointer", border: `1px solid ${cor}`,
               background: "#fff", color: cor, borderRadius: 99, padding: "3px 10px",
               fontSize: 11, fontWeight: 700, opacity: disabled ? 0.5 : 1, flex: "none" }}>
      {texto}
    </button>
  );
}
