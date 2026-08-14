// ============================================================
// Fase 85 — CLASSIFICAR RÓTULOS. Do mais caro para o mais barato.
//
// POR QUE ESTA TELA EXISTE
// Rótulo novo nasce sem categoria Lean. Desde a Fase 63 não há mais fatia
// cinza: sem categoria, o tempo conta como NÃO-PRODUTIVO. A convenção é a
// certa — sem prova de que agrega valor, não agrega — mas ela cria um efeito
// perverso no dia em que o vocabulário cresce: parte dos rótulos novos é
// trabalho produtivo de verdade, e a produtividade cai por CONTABILIDADE antes
// de cair por MEDIÇÃO.
//
// No gráfico de um dia as duas quedas são idênticas. Esta tela é o que permite
// separá-las: classificar rápido, começando pelo rótulo que representa mais
// TEMPO — porque é ele que move o número.
//
// Ordenado por FATIA DO TEMPO, não por número de eventos: 4 eventos longos pesam
// mais que 300 de 8 segundos.
// ============================================================
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, Icon, Empty, PanelHead, toast } from "../design/ui";
import { nomeHumano } from "../design/rotulos";
import { leanCor, leanLabel } from "../design/helpers";
import type { ProcHeaderMock } from "../lib/adapt";
import type { CategoriaLean, RotuloSemCategoria } from "../lib/types";

export default function Rotulos({ proc }: { proc: ProcHeaderMock }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["rotulos-sem-categoria", proc.id],
    queryFn: () => api.rotulos.semCategoria(proc.id),
  });
  const [salvando, setSalvando] = useState<string | null>(null);

  const classificar = useMutation({
    mutationFn: ({ label, cat }: { label: string; cat: CategoriaLean }) =>
      api.comportamentos.setCategoriaPorLabel(proc.id, label, cat),
    onMutate: ({ label }) => setSalvando(label),
    onSettled: () => setSalvando(null),
    onSuccess: (r, { label, cat }) => {
      toast(
        `"${label}" agora é ${cat === "valor_agregado" ? "produtivo" : "não-produtivo"}`
        + (r.eventos_atualizados ? ` · ${r.eventos_atualizados} evento(s) atualizados` : ""),
      );
      qc.invalidateQueries({ queryKey: ["rotulos-sem-categoria", proc.id] });
      // O número do dashboard muda com esta decisão — refaz as duas telas que
      // o mostram, senão o gestor classifica e continua vendo o valor velho.
      qc.invalidateQueries({ queryKey: ["dashboard", proc.id] });
      qc.invalidateQueries({ queryKey: ["diaadia", proc.id] });
    },
    onError: (e: unknown) => toast(`Não deu para classificar: ${String(e)}`),
  });

  const d = q.data;

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="Classificar rótulos"
          ajuda="Rótulo sem categoria conta como NÃO-PRODUTIVO. Isso é a convenção certa (sem prova de que agrega valor, não agrega), mas significa que todo rótulo novo entra do lado improdutivo até você decidir. Se vários nascerem juntos, a produtividade cai por contabilidade antes de cair por medição."
          leitura="Classifique de cima para baixo: o topo da lista é o que mais move o número."
        />
        {d && (
          <div className="row gap2 wrap" style={{ fontSize: 12.5, color: "var(--muted)" }}>
            <span>
              <b className="tnum" style={{ color: "var(--ink)" }}>{d.n_rotulos}</b> rótulo(s) sem categoria
            </span>
            <span>·</span>
            <span>
              <b className="tnum" style={{ color: d.pct_sem_categoria >= 10 ? "var(--desp)" : "var(--ink)" }}>
                {d.pct_sem_categoria.toFixed(0)}%
              </b>{" "}
              ({d.pct_sem_categoria.toFixed(1)}% do tempo observado)
            </span>
            <span>·</span>
            <span className="tnum">{d.n_rotulos} rótulo(s) sem categoria</span>
          </div>
        )}
      </Card>

      {q.isLoading && <Empty icon="loader" title="Lendo os rótulos…" />}
      {!q.isLoading && !d && (
        // Mostrar o motivo, não só o fato. "Não foi possível carregar" manda
        // o gestor adivinhar entre SQL não rodado, deploy velho e bug — e a
        // primeira vez que esta tela falhou foi exatamente assim.
        <Empty
          icon="alert-triangle"
          title="Não foi possível carregar os rótulos"
          desc={q.error ? String((q.error as Error).message || q.error) : undefined}
        />
      )}

      {d && d.itens.length === 0 && (
        <Empty
          icon="check-circle"
          title="Todo rótulo com tempo já tem categoria"
          desc="Nada aqui está contando como não-produtivo por falta de decisão. Quando o vocabulário crescer, os rótulos novos aparecem nesta tela."
        />
      )}

      {d && d.itens.length > 0 && (
        <div className="col" style={{ gap: 10 }}>
          {d.itens.map((r) => (
            <LinhaRotulo
              key={r.label}
              r={r}
              salvando={salvando === r.label}
              onClassificar={(cat) => classificar.mutate({ label: r.label, cat })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function LinhaRotulo({ r, salvando, onClassificar }: {
  r: RotuloSemCategoria;
  salvando: boolean;
  onClassificar: (c: CategoriaLean) => void;
}) {
  // Dois estados diferentes, e a diferença importa: NUNCA classificado espera
  // uma decisão; ASSUMIDO já tem uma — tomada pela máquina, sem evidência.
  const assumido = !!r.categoria_atual;
  return (
    <Card style={{ padding: 14, borderLeft: `3px solid ${assumido ? "var(--apoio)" : "var(--desp)"}` }}>
      <div className="row gap2 wrap" style={{ alignItems: "flex-start" }}>
        <div className="grow col" style={{ gap: 6, minWidth: 240 }}>
          <div className="row gap2 wrap" style={{ alignItems: "baseline" }}>
            <code className="font-mono" style={{ fontSize: 13, fontWeight: 700, background: "var(--line-2)", padding: "2px 9px", borderRadius: 6 }}>
              {nomeHumano(r.label)}
            </code>
            <span className="tnum" style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
              {r.pct_do_tempo.toFixed(1)}%
            </span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              do tempo observado · {r.n_eventos} evento(s)
            </span>
            {assumido && (
              <span style={{ fontSize: 11, fontWeight: 700, color: "var(--apoio)" }}>
                <Icon name="alert-triangle" size={11} /> categoria ASSUMIDA
                ({leanLabel(r.categoria_atual === "valor_agregado" ? "va" : "desp")}), não decidida
              </span>
            )}
          </div>
          {r.descricao && (
            <span style={{ fontSize: 12.5, color: "var(--text)" }}>{r.descricao}</span>
          )}
          {r.exemplos.length > 0 && (
            <span style={{ fontSize: 11.5, color: "var(--faint)", lineHeight: 1.5 }}>
              {r.exemplos.map((e) => `“${e}”`).join(" · ")}
            </span>
          )}
          {r.familia_variantes.length > 1 && (
            // A resposta ao problema do histórico: o rótulo antigo NÃO vira uma
            // quarta categoria nem é renomeado. Ele aparece como o que é — a
            // mesma família, medida com menos resolução. A soma da família é a
            // série comparável; a decomposição é o detalhe que ganhamos agora.
            <div className="col" style={{ gap: 3, borderLeft: "2px solid var(--line)", paddingLeft: 9, marginTop: 2 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)" }}>
                família <b>{nomeHumano(r.familia)}</b>
              </span>
              {r.familia_variantes.map((v) => (
                <span key={v.label} style={{ fontSize: 11, color: "var(--faint)" }}>
                  <b>{nomeHumano(v.label)}</b> ·{" "}
                  {v.categoria
                    ? leanLabel(v.categoria === "valor_agregado" ? "va" : "desp")
                    : "sem categoria"}
                  {v.label === r.familia
                    ? (v.versoes.some((n) => n < 3)
                        ? " — histórico: o instrumento não coletava o estado da máquina"
                        : " — o VLM não conseguiu ver o estado da máquina")
                    : ""}
                </span>
              ))}
            </div>
          )}
          {r.versoes.length > 0 && (
            // Fase 85: rótulo que só existe na versão 2 nasceu com o
            // instrumento novo — é vocabulário que antes não tinha como nascer.
            <span style={{ fontSize: 11, color: "var(--faint)" }}>
              instrumento {r.versoes.join(" e ")}
              {r.versoes.length === 1 && r.versoes[0] === 2 ? " (rótulo novo, só existe depois da mudança)" : ""}
            </span>
          )}
        </div>
        <div className="row gap1" style={{ flex: "none" }}>
          <BotaoCat cor={leanCor("va")} texto="Produtivo" disabled={salvando}
                    onClick={() => onClassificar("valor_agregado")} />
          <BotaoCat cor={leanCor("desp")} texto="Não-produtivo" disabled={salvando}
                    onClick={() => onClassificar("desperdicio")} />
        </div>
      </div>
    </Card>
  );
}

function BotaoCat({ cor, texto, disabled, onClick }: {
  cor: string; texto: string; disabled: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        cursor: disabled ? "wait" : "pointer", border: `1px solid ${cor}`,
        background: "#fff", color: cor, borderRadius: 99, padding: "6px 14px",
        fontSize: 12, fontWeight: 700, opacity: disabled ? 0.5 : 1,
      }}
    >
      {texto}
    </button>
  );
}
