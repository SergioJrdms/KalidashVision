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
import { nomeHumano } from "../design/rotulos";
import { FrameStripReal, FrameStripSegmento, janelaCam2, RotuloSegundoAngulo,
         useAspecto, colunasPorAspecto } from "../lib/frames";
import { leanCor, leanLabel, leanLong, type LeanShort } from "../design/helpers";
import type { ProcHeaderMock } from "../lib/adapt";
import type { Go } from "../design/Shell";
import type { AcaoValidacao, ItemDuvida } from "../lib/types";

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
    mutationFn: ({ id, acao }: { id: string; acao: AcaoValidacao }) =>
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
      toast(`“${nomeHumano(v.label)}” agora é ${leanLabel(v.cat)} — vale para todos os trechos com esse nome.`, { icon: "check" });
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
            procId={proc.id}
            ocupado={validar.isPending || classificar.isPending}
            onValidar={(acao) => validar.mutate({ id: it.id, acao })}
            onClassificar={(cat) => classificar.mutate({ label: it.rotulo, cat })}
          />
        ))
      )}
    </div>
  );
}

function ItemDaFila({ it, procId, ocupado, onValidar, onClassificar }: {
  it: ItemDuvida;
  procId: string;
  ocupado: boolean;
  onValidar: (a: AcaoValidacao) => void;
  onClassificar: (c: LeanShort) => void;
}) {
  const [confirmando, setConfirmando] = useState(false);
  const tom = TIPO_ROTULO[it.tipo] || TIPO_ROTULO.discordancia;
  const sa = it.segundo_angulo;
  const _j = janelaCam2(it.ini, it.fim, sa?.offset_s ?? 0);
  // Fase 78: a grade se adapta às proporções REAIS dos dois ROIs.
  const [a1, mede1] = useAspecto();
  const [a2, mede2] = useAspecto();
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div className="row gap2 wrap" style={{ alignItems: "center", padding: "12px 16px", borderBottom: "1px solid var(--line-2)" }}>
        <code className="font-mono" style={{ background: "var(--line-2)", padding: "2px 9px", borderRadius: 6, fontSize: 12 }}>
          {nomeHumano(it.rotulo)}
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
      <div style={{ display: "grid", gridTemplateColumns: sa ? colunasPorAspecto(a1, a2) : "1fr",
                    gap: 2, background: "#0d0820", alignItems: "start" }}>
        <div className="col" style={{ gap: 0 }}>
          <span style={{ fontSize: 10.5, fontWeight: 700, color: "rgba(255,255,255,.72)", padding: "6px 10px 2px", fontFamily: "var(--mono)" }}>
            {(it.cam_id || "cam1").replace(/^cam/i, "Cam ")}
          </span>
          <FrameStripReal ativo={{ id: it.id, pessoa: it.pessoa, label: it.rotulo, ini: it.ini, fim: it.fim }}
                          onAspecto={mede1} />
        </div>
        {sa && (
          <div className="col" style={{ gap: 0 }}>
            {/* Fase 78: o rótulo só diz "mesmo instante" quando é verdade. O
                `Math.max(0, …)` que estava aqui clampava em SILÊNCIO quando a
                janela caía fora do segmento da cam2 — e era isso que fazia
                julgar duas cenas distintas como se fossem a mesma. */}
            <div style={{ padding: "6px 10px 2px" }}>
              <RotuloSegundoAngulo camId={sa.cam_id} offsetS={sa.offset_s}
                                   residual={_j.residual} sincronizado={_j.sincronizado} />
            </div>
            <FrameStripSegmento segmentoId={sa.segmento_id} ini={_j.ini} fim={_j.fim}
                                onAspecto={mede2} />
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
              “{nomeHumano(it.rotulo)}” agrega valor ao produto?
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
          // Fase 70 — a MESMA ordem da tela de validação: a descrição primeiro.
          // Esta fila recebe as contradições lógicas (rótulo × presença), que
          // são exatamente onde o VLM costuma ter alucinado a cena. Oferecer só
          // "certo/errado" aqui empurraria de volta para o erro que criou o
          // contágio: corrigir o rótulo de uma frase que nunca descreveu nada.
          <div className="col" style={{ gap: 8 }}>
            {it.descricao && (
              <p style={{ fontSize: 12.5, color: "var(--text)", margin: 0, lineHeight: 1.5 }}>
                <b>O Prism disse que viu:</b> “{it.descricao}”
              </p>
            )}
            <div className="row gap2 wrap">
              <Btn size="sm" icon="check" disabled={ocupado} onClick={() => onValidar("confirmar")}>
                Bate, e o rótulo está certo
              </Btn>
              <Btn size="sm" variant="ghost" icon="x" disabled={ocupado} onClick={() => onValidar("descartar")}>
                Bate, mas o rótulo está errado
              </Btn>
              <Btn size="sm" variant="ghost" icon="eye-off" disabled={ocupado}
                   onClick={() => setConfirmando(true)}>
                Não é isso que se vê
              </Btn>
            </div>
            {confirmando && it.descricao && (
              <ConfirmaQueima
                processoId={procId}
                descricao={it.descricao}
                onCancelar={() => setConfirmando(false)}
                onConfirmar={() => { setConfirmando(false); onValidar("descricao_invalida"); }}
              />
            )}
            <span style={{ fontSize: 11, color: "var(--faint)", lineHeight: 1.5 }}>
              <b>Teste do apagamento:</b> se apagar este trecho das métricas
              custa tempo REAL de trabalho, a cena existiu — use “Bate” e
              corrija o rótulo, mesmo que a frase esteja imprecisa. “Não é isso
              que se vê” é só para cena que não aconteceu.
            </span>
          </div>
        )}
      </div>
    </Card>
  );
}

// ── Fase 77 · Confirmação antes de QUEIMAR uma frase ───────────────
// Marcar uma descrição como inválida tira do APRENDIZADO todos os eventos que
// a usam — não só o card aberto. Se a frase for a mais comum do dataset, o
// custo é grande, e a tela não o mostrava: o botão era apertado às cegas.
//
// Mostra o custo, não impede a ação. E diz que é reversível (as queimadas são
// derivadas de `descricao_invalida`; reabrir o evento limpa a marca) — a
// suposição de irreversibilidade estava fazendo o gestor hesitar à toa.
export function ConfirmaQueima({
  processoId, descricao, onConfirmar, onCancelar,
}: {
  processoId: string;
  descricao: string;
  onConfirmar: () => void;
  onCancelar: () => void;
}) {
  const q = useQuery({
    queryKey: ["uso-descricao", processoId, descricao],
    queryFn: () => api.descricoes.uso(processoId, descricao),
    staleTime: 60_000,
  });
  const u = q.data;
  const outros = Math.max(0, (u?.eventos ?? 1) - 1);
  const pesado = outros >= 10;
  return (
    <div className="col" style={{
      gap: 9, background: "var(--desp-bg)", border: "1px solid var(--desp)",
      borderRadius: 10, padding: "12px 14px",
    }}>
      <div className="row gap1" style={{ fontSize: 12.5, fontWeight: 700, color: "var(--desp)" }}>
        <Icon name="alert-triangle" size={14} /> Confirmar: esta frase deixa de ensinar o sistema
      </div>

      {q.isLoading ? (
        <span style={{ fontSize: 12, color: "var(--muted)" }}>Medindo o alcance da frase…</span>
      ) : (
        <>
          <p style={{ fontSize: 12.5, color: "var(--text)", margin: 0, lineHeight: 1.55 }}>
            Esta descrição aparece em <b className="tnum">{u?.eventos ?? "?"}</b> trecho(s)
            {u?.minutos ? <> · <b className="tnum">{u.minutos.toFixed(0)} min</b></> : null}
            {outros > 0 && (
              <> — <b>{outros}</b> além deste.</>
            )}
          </p>
          {u?.rotulos && u.rotulos.length > 1 && (
            // Frase que produz vários rótulos é POLISSÊMICA: o problema é ela,
            // não o card. Ver isto aqui muda a decisão.
            <p style={{ fontSize: 11.5, color: "var(--muted)", margin: 0 }}>
              Ela já virou{" "}
              {u.rotulos.map((r) => (
                <code key={r.rotulo} className="font-mono" style={{
                  fontSize: 11, background: "#fff", padding: "1px 6px",
                  borderRadius: 4, marginRight: 4,
                }}>{r.rotulo} ({r.eventos})</code>
              ))}
            </p>
          )}
          <p style={{ fontSize: 11.5, color: "var(--muted)", margin: 0, lineHeight: 1.55 }}>
            Os outros trechos <b>continuam contando nas métricas</b> — o que muda
            é que esta frase para de alimentar o vocabulário e as correções.
            {pesado && (
              <> Como ela é frequente, isso desliga uma parte grande do aprendizado.</>
            )}{" "}
            Dá para desfazer: reabra o evento e a marca some.
          </p>
        </>
      )}

      <div className="row gap2 wrap">
        <Btn size="sm" variant="ghost" icon="x" onClick={onCancelar}>Cancelar</Btn>
        <Btn size="sm" icon="eye-off" onClick={onConfirmar}>
          Confirmar — a cena não aconteceu
        </Btn>
      </div>
    </div>
  );
}
