// ============================================================
// Fase 79 — AUDITORIA DE UM DIA. Auditar não é validar.
//
// POR QUE ESTA TELA EXISTE
// O dia 29 tinha 463 eventos e sumiu da plataforma. Nenhum deles foi julgado
// por gente: 245 nasceram com origem `posto_vazio` e 163 com `auditoria`, e
// nos dois casos `validado_humano=true` é o MECANISMO que os mantém fora da
// fila. O dia estava certo — o operador faltou. Mas se estivesse errado, não
// haveria como perceber: um dia inteiramente classificado como posto vazio é
// invisível por construção e só sobrevive como número no dashboard.
//
// Aqui nada entra na fila e nada muda de validação. É leitura.
//
// AMOSTRA, NÃO LISTA: 245 trechos de posto vazio não se auditam um a um. De
// cada bloco contíguo saem início, meio e fim — o começo mostra a transição
// que o originou, o fim a que o encerrou, o meio o regime. Se o operador
// estivesse lá, apareceria em algum.
// ============================================================
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, Icon, Empty, PanelHead } from "../design/ui";
import { leanCor } from "../design/helpers";
import { FrameStripReal } from "../lib/frames";
import type { ProcHeaderMock } from "../lib/adapt";
import type { AmostraAuditoria } from "../lib/types";

// Handoff do "Dia a dia": lá o gestor vê o dia atípico no gráfico e clica em
// "auditar este dia". O roteador é estado em memória e só carrega
// (tela, processo, aba) — o dia viaja por aqui e é consumido UMA vez, para
// que voltar à aba depois não force sempre o mesmo dia.
export const AUDITORIA_DIA_KEY = "kv_auditoria_dia";
export function pedirAuditoriaDoDia(dia: string) {
  try { sessionStorage.setItem(AUDITORIA_DIA_KEY, dia); } catch { /* modo privado */ }
}
function diaPedido(): string | null {
  try {
    const v = sessionStorage.getItem(AUDITORIA_DIA_KEY);
    if (v) sessionStorage.removeItem(AUDITORIA_DIA_KEY);
    return v;
  } catch { return null; }
}

// Data de hoje no relógio de quem olha — `toISOString()` é UTC e às 21h de
// Brasília já teria virado o dia seguinte, abrindo um dia vazio.
function hoje(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function Auditoria({ proc, dia: diaInicial }: { proc: ProcHeaderMock; dia?: string }) {
  const [dia, setDia] = useState(() => diaInicial || diaPedido() || hoje());
  const [porBloco, setPorBloco] = useState(3);
  const q = useQuery({
    queryKey: ["auditoria", proc.id, dia, porBloco],
    queryFn: () => api.auditoria.dia(proc.id, dia, porBloco),
  });
  const d = q.data;

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="Auditar um dia"
          ajuda="Abre um dia inteiro para conferência — inclusive os trechos que NÃO precisam de validação. Eventos de posto vazio e de auditoria saem da fila por mecanismo, não porque alguém os julgou; sem esta tela, um dia inteiro classificado como posto vazio ficaria invisível. Nada aqui entra na fila nem altera validação."
          leitura="Auditar é olhar. Validar é decidir. Esta tela só faz a primeira."
        />
        <div className="row gap2 wrap" style={{ alignItems: "flex-end" }}>
          <div className="col" style={{ gap: 4 }}>
            <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--muted)" }}>Dia</span>
            <input className="field" type="date" value={dia} style={{ maxWidth: 190 }}
                   onChange={(e) => setDia(e.target.value)} />
          </div>
          <div className="col" style={{ gap: 4 }}>
            <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--muted)" }}>
              Trechos por bloco
            </span>
            <div className="row gap1">
              {[1, 3, 5].map((n) => (
                <button key={n} onClick={() => setPorBloco(n)}
                        style={{ cursor: "pointer", borderRadius: 99, padding: "5px 13px",
                                 fontSize: 12, fontWeight: 600,
                                 border: porBloco === n ? "1px solid var(--accent)" : "1px solid var(--line)",
                                 background: porBloco === n ? "var(--accent-soft)" : "#fff",
                                 color: porBloco === n ? "var(--accent)" : "var(--muted)" }}>
                  {n}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Card>

      {q.isLoading && <Empty icon="loader" title="Abrindo o dia…" />}
      {!q.isLoading && !d && <Empty icon="alert-triangle" title="Não foi possível carregar" />}

      {d && d.eventos === 0 && (
        <Empty icon="calendar" title="Nenhum trecho neste dia"
               desc="Não há vídeo processado com gravação nesta data." />
      )}

      {d && d.eventos > 0 && (
        <>
          {/* O ALERTA — item que faltava. Dia assim ou é falta real, ou é
              falha grave de detecção; as duas merecem olhada. */}
          {d.atipico && (
            <Card style={{ padding: 16, borderLeft: "3px solid var(--desp)", background: "var(--desp-bg)" }}>
              <div className="row gap2" style={{ alignItems: "flex-start" }}>
                <Icon name="alert-triangle" size={18} color="var(--desp)" />
                <div className="col" style={{ gap: 4 }}>
                  <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
                    Dia atípico — {d.posto_vazio_pct.toFixed(0)}% do tempo como posto vazio
                  </span>
                  <p style={{ fontSize: 12.5, color: "var(--text)", margin: 0, lineHeight: 1.5 }}>
                    Acima de {d.limiar_atipico.toFixed(0)}%, um dia assim ou é <b>falta real</b> do
                    operador, ou é <b>falha grave de detecção</b> — e as duas merecem conferência.
                    Confira as amostras abaixo: se o operador estivesse no posto,
                    ele apareceria em alguma delas.
                  </p>
                </div>
              </div>
            </Card>
          )}

          {d.contradicoes_c1 > 0 && (
            // Não depende de reprocessar nem de reavaliar camada: papel_pessoa
            // já está gravado no evento, então a contradição é legível direto.
            <Card style={{ padding: 14, borderLeft: "3px solid var(--desp)" }}>
              <span style={{ fontSize: 12.5, color: "var(--text)" }}>
                <b style={{ color: "var(--desp)" }}>
                  <Icon name="alert-octagon" size={13} /> {d.contradicoes_c1} contradição(ões) lógica(s)
                </b>{" "}
                neste dia: o rastreamento identificou o <b>operador no posto</b> em trechos
                rotulados como <code className="font-mono">posto_vazio</code>. Os dois não
                podem estar certos.
              </span>
            </Card>
          )}

          <Card style={{ padding: 20 }}>
            <PanelHead titulo="O dia em blocos"
                       ajuda="Trechos contíguos com o mesmo rótulo, na ordem do relógio da fábrica."
                       leitura="Um bloco muito longo é onde vale olhar primeiro." />
            <div className="row gap2 wrap" style={{ marginBottom: 12, fontSize: 12.5, color: "var(--muted)" }}>
              <span><b className="tnum" style={{ color: "var(--ink)" }}>{d.eventos}</b> trechos</span>
              <span>·</span>
              <span><b className="tnum" style={{ color: "var(--ink)" }}>{d.minutos.toFixed(0)}</b> min observados</span>
              <span>·</span>
              <span><b className="tnum" style={{ color: "var(--ink)" }}>{d.videos.length}</b> vídeo(s)</span>
            </div>
            <div className="col" style={{ gap: 6 }}>
              {d.blocos.map((b, i) => (
                <div key={i} className="row gap2" style={{ alignItems: "center" }}>
                  <span className="font-mono" style={{ fontSize: 11, color: "var(--faint)", width: 92, flex: "none" }}>
                    {b.de}–{b.ate}
                  </span>
                  <code className="font-mono" style={{ fontSize: 11.5, background: "var(--line-2)", padding: "1px 8px", borderRadius: 5 }}>
                    {b.rotulo}
                  </code>
                  <div className="grow track" style={{ height: 8 }}>
                    <i style={{ width: `${Math.max(2, (b.minutos / Math.max(1, d.minutos)) * 100)}%`,
                                background: b.rotulo === "posto_vazio" ? "#8a8598" : leanCor("va") }} />
                  </div>
                  <span className="tnum" style={{ fontSize: 11.5, color: "var(--muted)", width: 74, textAlign: "right", flex: "none" }}>
                    {b.minutos.toFixed(0)}min · {b.eventos}
                  </span>
                </div>
              ))}
            </div>
          </Card>

          <Card style={{ padding: 20 }}>
            <PanelHead
              titulo={`Amostras — ${d.amostras.length} trecho(s) de ${d.eventos}`}
              ajuda="De cada bloco saem início, meio e fim. Não é a lista completa de propósito: 245 trechos de posto vazio não se auditam um a um, e se o operador estivesse lá apareceria em algum destes."
              leitura="Olhe as imagens. Se o posto não estiver vazio em alguma, a leitura do dia está errada."
            />
            <p style={{ fontSize: 11.5, color: "var(--faint)", margin: "0 0 12px", lineHeight: 1.5 }}>
              {d.nota}
            </p>
            <div className="col" style={{ gap: 14 }}>
              {d.amostras.map((a) => <CardAmostra key={a.id} a={a} />)}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function CardAmostra({ a }: { a: AmostraAuditoria }) {
  const POS: Record<string, string> = {
    inicio: "início do bloco", meio: "meio do bloco",
    fim: "fim do bloco", unico: "trecho único",
  };
  // A contradição no próprio card: o rastreamento viu o operador e o rótulo
  // diz que o posto estava vazio.
  const contradiz = a.rotulo === "posto_vazio" && a.papel === "operador";
  return (
    <div style={{ border: contradiz ? "1px solid var(--desp)" : "1px solid var(--line-2)",
                  borderRadius: 10, overflow: "hidden" }}>
      <div className="row gap2 wrap" style={{ alignItems: "center", padding: "9px 12px",
                                              borderBottom: "1px solid var(--line-2)" }}>
        <span className="font-mono tnum" style={{ fontSize: 12, fontWeight: 700, color: "var(--ink)" }}>
          {a.hora}
        </span>
        <code className="font-mono" style={{ fontSize: 11.5, background: "var(--line-2)", padding: "1px 8px", borderRadius: 5 }}>
          {a.rotulo}
        </code>
        <span style={{ fontSize: 11, color: "var(--faint)" }}>
          {POS[a.posicao]} · {a.bloco_eventos} trechos / {a.bloco_minutos.toFixed(0)}min
        </span>
        {contradiz && (
          <span style={{ fontSize: 11, fontWeight: 700, color: "var(--desp)" }}>
            <Icon name="alert-octagon" size={11} /> operador rastreado no posto
          </span>
        )}
        <span className="grow" />
        <span className="font-mono" style={{ fontSize: 10.5, color: "var(--faint)" }}>
          {a.cam_id || "cam1"} · {Math.round(a.ini)}→{Math.round(a.fim)}s
        </span>
      </div>
      <FrameStripReal ativo={{ id: a.id, pessoa: 1, label: a.rotulo, ini: a.ini, fim: a.fim }} />
      {a.descricao && (
        <p style={{ fontSize: 12, color: "var(--muted)", margin: 0, padding: "8px 12px", lineHeight: 1.5 }}>
          “{a.descricao}”
        </p>
      )}
    </div>
  );
}
