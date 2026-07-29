// ============================================================
// A FILA DA DÚVIDA (Fase 59, reescrita na 63).
//
// PARA QUE ESTA TELA EXISTE
// O sistema nunca deixa tempo sem classificar: todo minuto é produtivo ou
// não-produtivo. Mas "sempre responder" não é o mesmo que "sempre saber" —
// e a diferença entre as duas coisas mora aqui. Esta é a lista dos trechos
// em que a resposta que está valendo no dashboard foi dada com pouca ou
// nenhuma evidência. Cada item resolvido tira um pedaço do placar do campo
// da suposição e o põe no campo do fato.
//
// Ordenada por MINUTOS EM JOGO, nunca por ordem de chegada: valida-se
// primeiro o que mais move o placar.
//
// QUATRO TIPOS, QUATRO AÇÕES DIFERENTES — por isso não se misturam:
//   • sem evidência        → trecho curto demais para afirmar OU duvidar.
//                            NÃO se resolve validando: resolve-se gravando
//                            mais denso.
//   • amostras discordaram → as leituras do mesmo minuto brigaram entre si.
//                            Você desempata dizendo se o rótulo está certo.
//   • a cena contradiz     → uma verificação determinística achou o rótulo
//                            incompatível com o que se vê.
//   • categoria assumida   → o rótulo pode estar certo; ninguém decidiu se
//                            aquilo AGREGA VALOR. Está contando como
//                            não-produtivo por convenção. Resolve-se
//                            classificando o comportamento, não o evento.
// ============================================================
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Btn, Card, Icon, Empty, PanelHead, Segmented, toast } from "../design/ui";
import { FrameStripReal, FrameStripSegmento } from "../lib/frames";
import { leanCor, leanLabel, leanLong, type LeanShort } from "../design/helpers";
import type { ProcHeaderMock } from "../lib/adapt";
import type { Go } from "../design/Shell";
import type { ItemDuvida } from "../lib/types";

const TIPO_ROTULO: Record<string, {
  nome: string; cor: string; dica: string; comoResolver: string;
}> = {
  sem_evidencia: {
    nome: "Sem evidência", cor: "var(--muted)",
    dica: "Trecho curto demais para julgar.",
    comoResolver:
      "Este caso não sai da fila validando — não há o que julgar em um trecho " +
      "com uma amostra só. Ele some sozinho quando a gravação daquele posto " +
      "ficar mais densa.",
  },
  discordancia: {
    nome: "Amostras discordaram", cor: "#c98a00",
    dica: "As leituras do mesmo minuto brigaram entre si.",
    comoResolver:
      "Olhe os dois ângulos e diga se o rótulo está certo. Você está " +
      "desempatando uma disputa entre leituras do mesmo minuto.",
  },
  camada: {
    nome: "A cena contradiz", cor: "var(--desp)",
    dica: "Uma verificação determinística achou o rótulo incompatível com a cena.",
    comoResolver:
      "Uma regra da cena (posição, zona, mãos, deslocamento) diz que este " +
      "rótulo não cabe aqui. Confirme se a regra tem razão — se não tiver, é " +
      "a regra que precisa mudar, não o evento.",
  },
  categoria_assumida: {
    nome: "Ninguém decidiu se agrega valor", cor: "var(--accent-deep)",
    dica: "O rótulo pode estar certo; a categoria Lean foi assumida.",
    comoResolver:
      "Este tempo já está contando como NÃO-produtivo, por convenção e não " +
      "por evidência. Se ele agrega valor, seu placar está pior do que a " +
      "realidade. Responda aqui embaixo o que este comportamento é — vale " +
      "para todos os trechos com o mesmo nome.",
  },
};

export default function Duvidas({ proc }: { proc: ProcHeaderMock; go: Go }) {
  const qc = useQueryClient();
  const [rotulo, setRotulo] = useState<string | null>(null);
  const [tipo, setTipo] = useState<string>("todos");

  const q = useQuery({
    queryKey: ["duvidas", proc.id, rotulo, tipo],
    queryFn: () => api.duvidas.listar(proc.id, rotulo, tipo === "todos" ? null : tipo),
  });

  function invalidar() {
    qc.invalidateQueries({ queryKey: ["duvidas", proc.id] });
    qc.invalidateQueries({ queryKey: ["diaadia", proc.id] });
    qc.invalidateQueries({ queryKey: ["dashboard", proc.id] });
  }

  const validar = useMutation({
    mutationFn: ({ id, acao }: { id: string; acao: "confirmar" | "descartar" }) =>
      api.eventos.validar(id, acao),
    onSuccess: () => {
      invalidar();
      toast("Registrado — a curva de dúvida se atualiza no próximo cálculo.");
    },
    onError: (e: Error) => toast(`Não deu: ${e.message}`, { color: "var(--desp)" }),
  });

  // Dúvida de CATEGORIA se resolve no comportamento, não no evento: a
  // pergunta "isso agrega valor?" tem a mesma resposta para todos os trechos
  // com o mesmo rótulo. Responder evento a evento seria trabalho repetido.
  const classificar = useMutation({
    mutationFn: ({ label, cat }: { label: string; cat: LeanShort }) =>
      api.comportamentos.setCategoriaPorLabel(proc.id, label, leanLong(cat)),
    onSuccess: (_r, v) => {
      invalidar();
      toast(`“${v.label}” agora é ${leanLabel(v.cat)} — vale para todos os trechos com esse nome.`, { icon: "check" });
    },
    onError: (e: Error) => toast(`Não deu: ${e.message}`, { color: "var(--desp)" }),
  });

  if (q.isLoading) return <Empty icon="loader" title="Montando a fila…" />;
  const d = q.data;
  if (!d) return <Empty icon="alert-triangle" title="Não foi possível carregar a fila" />;

  if (d.total === 0) {
    return (
      <Empty
        icon="check-circle"
        title="Nada em dúvida agora"
        desc="Todo o tempo observado foi lido com evidência suficiente. Quando o sistema tiver de responder sem saber, os trechos aparecem aqui — do que mais pesa para o que menos pesa."
      />
    );
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="O que o sistema não sabe"
          ajuda="O sistema nunca deixa tempo sem classificar: todo minuto é produtivo ou não-produtivo. Esta tela mostra os trechos em que a resposta que ESTÁ VALENDO no dashboard foi dada com pouca ou nenhuma evidência. Cada item resolvido move um pedaço do placar da suposição para o fato."
          leitura="A fila é ordenada por MINUTOS EM JOGO — validar de cima para baixo é o que mais move o placar."
          right={
            <span className="font-mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
              limiar {d.limiar.toFixed(2)}
            </span>
          }
        />

        <div className="row gap2 wrap" style={{ alignItems: "baseline", marginBottom: 10 }}>
          <span className="font-display tnum" style={{ fontSize: 24, fontWeight: 700, color: "var(--ink)" }}>
            {d.minutos_totais.toFixed(0)} min
          </span>
          <span style={{ fontSize: 13, color: "var(--muted)" }}>
            em {d.total} trecho(s) esperando julgamento
          </span>
        </div>

        {/* O que fazer aqui — era a pergunta que a tela não respondia. */}
        <div style={{ background: "var(--soft)", border: "1px solid var(--line-2)", borderRadius: 10, padding: "11px 14px", marginBottom: 14 }}>
          <div className="row gap1" style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)", marginBottom: 6 }}>
            <Icon name="help-circle" size={12} /> Como tirar a dúvida
          </div>
          <ol className="col" style={{ gap: 4, margin: 0, paddingLeft: 18, fontSize: 12.5, color: "var(--text)", lineHeight: 1.5 }}>
            <li>Comece pelo topo — é o trecho com mais minutos em jogo.</li>
            <li>Veja os dois ângulos: as câmeras mostram o <b>mesmo instante</b>.</li>
            <li>Leia o <b>motivo</b> — ele diz o que exatamente está em dúvida.</li>
            <li>Responda o que o card pedir: cada tipo pede uma coisa diferente.</li>
          </ol>
        </div>

        {/* Tipos — separados de propósito: exigem ações diferentes */}
        <div className="row gap2 wrap" style={{ marginBottom: 12 }}>
          <Segmented
            size="sm"
            value={tipo}
            onChange={setTipo}
            options={[
              { value: "todos", label: "Todos" },
              ...d.por_tipo.map((t) => ({
                value: t.tipo,
                label: `${TIPO_ROTULO[t.tipo]?.nome || t.tipo} (${t.minutos.toFixed(0)}min)`,
              })),
            ]}
          />
        </div>
        {tipo !== "todos" && TIPO_ROTULO[tipo] && (
          <p style={{ fontSize: 12.5, color: "var(--muted)", margin: "0 0 12px", lineHeight: 1.5 }}>
            <b style={{ color: TIPO_ROTULO[tipo].cor }}>{TIPO_ROTULO[tipo].nome}:</b>{" "}
            {TIPO_ROTULO[tipo].comoResolver}
          </p>
        )}

        {/* Por rótulo — é aqui que se enxerga um rótulo virar depósito da dúvida */}
        <div className="col" style={{ gap: 6 }}>
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
            Onde a dúvida se concentra
          </span>
          <div className="row gap1 wrap">
            {rotulo && (
              <button onClick={() => setRotulo(null)}
                style={{ cursor: "pointer", border: "1px solid var(--line)", background: "#fff", borderRadius: 99, padding: "3px 11px", fontSize: 11.5, fontWeight: 600 }}>
                ✕ todos os rótulos
              </button>
            )}
            {d.por_rotulo.map((r) => {
              const sel = r.rotulo === rotulo;
              return (
                <button
                  key={r.rotulo}
                  onClick={() => setRotulo(sel ? null : r.rotulo)}
                  title={`${r.eventos} trecho(s) · ${r.minutos} min em dúvida`}
                  style={{
                    cursor: "pointer", borderRadius: 99, padding: "3px 11px", fontSize: 11.5,
                    fontWeight: 600, fontFamily: "var(--mono)",
                    border: sel ? "1px solid var(--accent)" : "1px solid var(--line)",
                    background: sel ? "var(--accent-soft)" : "#fff",
                    color: sel ? "var(--accent)" : "var(--text)",
                  }}
                >
                  {r.rotulo} · {r.minutos.toFixed(0)}min
                </button>
              );
            })}
          </div>
        </div>
      </Card>

      {d.itens.length === 0 ? (
        <Empty icon="filter" title="Nenhum trecho com esse filtro"
               desc="Ajuste o rótulo ou o tipo acima para ver os demais." />
      ) : (
        d.itens.map((it) => (
          <ItemDaFila
            key={it.id}
            it={it}
            ocupado={validar.isPending || classificar.isPending}
            onValidar={(acao) => validar.mutate({ id: it.id, acao })}
            onClassificar={(cat) => classificar.mutate({ label: it.rotulo, cat })}
          />
        ))
      )}
    </div>
  );
}

function ItemDaFila({ it, ocupado, onValidar, onClassificar }: {
  it: ItemDuvida;
  ocupado: boolean;
  onValidar: (a: "confirmar" | "descartar") => void;
  onClassificar: (c: LeanShort) => void;
}) {
  const tom = TIPO_ROTULO[it.tipo] || TIPO_ROTULO.discordancia;
  const sa = it.segundo_angulo;
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div className="row gap2 wrap" style={{ alignItems: "center", padding: "12px 16px", borderBottom: "1px solid var(--line-2)" }}>
        <code className="font-mono" style={{ background: "var(--line-2)", padding: "2px 9px", borderRadius: 6, fontSize: 12 }}>
          {it.rotulo}
        </code>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: tom.cor }} title={tom.dica}>
          <Icon name="help-circle" size={12} /> {tom.nome}
        </span>
        <span className="font-mono" style={{ fontSize: 11, color: "var(--faint)" }}>
          {Math.round(it.ini)}→{Math.round(it.fim)}s
          {it.confianca != null && ` · concordância ${(it.confianca * 100).toFixed(0)}%`}
          {it.n_amostras != null && ` · ${it.n_amostras} amostra(s)`}
        </span>
        <span className="font-display tnum" style={{ marginLeft: "auto", fontSize: 16, fontWeight: 700, color: "var(--ink)" }}>
          {it.minutos.toFixed(1)} min
        </span>
      </div>

      {/* Os DOIS ângulos do mesmo instante. É aqui que a dúvida costuma morrer:
          o que a cam1 não mostra (oclusão, operador de costas, mão fora de
          quadro) quase sempre está visível na cam2. */}
      <div style={{ display: "grid", gridTemplateColumns: sa ? "1fr 1fr" : "1fr", gap: 2, background: "#0d0820" }}>
        <div className="col" style={{ gap: 0 }}>
          <span style={{ fontSize: 10.5, fontWeight: 700, color: "rgba(255,255,255,.72)", padding: "6px 10px 2px", fontFamily: "var(--mono)" }}>
            {(it.cam_id || "cam1").replace(/^cam/i, "Cam ")}
          </span>
          <FrameStripReal ativo={{ id: it.id, pessoa: it.pessoa, label: it.rotulo, ini: it.ini, fim: it.fim }} />
        </div>
        {sa && (
          <div className="col" style={{ gap: 0 }}>
            <span style={{ fontSize: 10.5, fontWeight: 700, color: "rgba(255,255,255,.72)", padding: "6px 10px 2px", fontFamily: "var(--mono)" }}>
              {(sa.cam_id || "cam2").replace(/^cam/i, "Cam ")} · mesmo instante
            </span>
            {/* offset_s = diferença REAL de relógio entre as duas câmeras. Sem
                somá-lo, os dois lados mostrariam momentos distintos — pior que
                não mostrar o segundo ângulo, porque parece sincronizado. */}
            <FrameStripSegmento
              segmentoId={sa.segmento_id}
              ini={Math.max(0, it.ini + sa.offset_s)}
              fim={Math.max(0, it.fim + sa.offset_s)}
            />
          </div>
        )}
      </div>

      <div className="col" style={{ gap: 10, padding: "12px 16px" }}>
        <p style={{ fontSize: 12.5, color: "var(--text)", margin: 0, lineHeight: 1.5 }}>
          <b style={{ color: tom.cor }}>Por que está aqui:</b> {it.motivo}
        </p>
        {it.rotulos_competindo?.length > 1 && (
          <p style={{ fontSize: 11.5, color: "var(--muted)", margin: 0 }}>
            Disputaram o minuto:{" "}
            {it.rotulos_competindo.map((r) => (
              <code key={r} className="font-mono" style={{ fontSize: 11, background: "var(--line-2)", padding: "1px 6px", borderRadius: 4, marginRight: 4 }}>{r}</code>
            ))}
          </p>
        )}

        <p style={{ fontSize: 12, color: "var(--muted)", margin: 0, lineHeight: 1.5 }}>
          <b>Como resolver:</b> {tom.comoResolver}
        </p>

        {it.tipo === "sem_evidencia" ? null : it.tipo === "categoria_assumida" ? (
          // A pergunta aqui NÃO é sobre o rótulo — é sobre valor.
          <div className="row gap2 wrap" style={{ alignItems: "center" }}>
            <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text)" }}>
              “{it.rotulo}” agrega valor ao produto?
            </span>
            {(["va", "desp"] as LeanShort[]).map((c) => (
              <Btn
                key={c}
                size="sm"
                variant={c === "va" ? "primary" : "ghost"}
                disabled={ocupado}
                onClick={() => onClassificar(c)}
              >
                <i style={{ width: 9, height: 9, borderRadius: 2, background: leanCor(c), display: "inline-block", marginRight: 6 }} />
                {c === "va" ? "Sim — produtivo" : "Não — não-produtivo"}
              </Btn>
            ))}
          </div>
        ) : (
          <div className="row gap2 wrap">
            <Btn size="sm" icon="check" disabled={ocupado} onClick={() => onValidar("confirmar")}>
              O rótulo está certo
            </Btn>
            <Btn size="sm" variant="ghost" icon="x" disabled={ocupado} onClick={() => onValidar("descartar")}>
              Está errado
            </Btn>
          </div>
        )}
      </div>
    </Card>
  );
}
