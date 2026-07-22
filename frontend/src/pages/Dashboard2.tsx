// ============================================================
// Dashboard 2 — "Dia a dia" (Fase 35). A pergunta do dono é uma só:
// "o operador está trabalhando?" — respondida por JANELAS de tempo
// (7/30 dias, nunca dia contra dia), evolução diária com o ritmo e o
// resumão do dia selecionado, tendência temporal e dias sem trabalho.
// ============================================================
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, PanelHead, Ring, Icon, Empty, LeanBar } from "../design/ui";
import { leanCor, leanLabel, fmtDur, type LeanShort } from "../design/helpers";
import type { ProcHeaderMock } from "../lib/adapt";
import type { Go } from "../design/Shell";
import type { AnaliseDiaria, DiaAnalise, JanelaAgregada } from "../lib/types";

export default function Dashboard2({ proc }: { proc: ProcHeaderMock; go: Go }) {
  const q = useQuery({ queryKey: ["diaadia", proc.id], queryFn: () => api.diaadia.analise(proc.id, 30) });
  const dados = q.data || null;
  const dias = useMemo(() => dados?.dias || [], [dados]);
  const trabalhados = useMemo(() => dias.filter((d) => !d.sem_trabalho && d.tempo_obs_s > 0), [dias]);
  const [diaSel, setDiaSel] = useState<string | null>(null);
  const selecionado = useMemo(() => {
    const alvo = diaSel && dias.find((d) => d.dia === diaSel);
    if (alvo) return alvo;
    return trabalhados.length ? trabalhados[trabalhados.length - 1] : null;
  }, [dias, trabalhados, diaSel]);

  if (q.isLoading) return <Empty icon="loader" title="Montando o dia a dia…" />;
  if (!dados || dias.length === 0 || trabalhados.length === 0) {
    return <Empty icon="calendar-days" title="Ainda não há dias analisados" desc="Assim que os primeiros vídeos com data real forem processados, o dia a dia do operador aparece aqui." />;
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <VereditoHero dados={dados} />
      <EvolucaoPorDia dias={dias} selecionado={selecionado} onSelecionar={(d) => setDiaSel(d.dia)} trabalhados={trabalhados} />
      {selecionado && !selecionado.sem_trabalho && (
        <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
          <div style={{ flex: "1.4 1 420px" }}><JornadaDoDia d={selecionado} /></div>
          <div style={{ flex: "1 1 300px" }}><TopAcoesDia d={selecionado} /></div>
        </div>
      )}
      <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
        <div style={{ flex: "1.2 1 380px" }}><TendenciaCard dias={trabalhados} tendencia={dados.tendencia} /></div>
        <div style={{ flex: "1 1 340px" }}><HeatmapQuinzena dias={dias} onSelecionar={(d) => setDiaSel(d.dia)} /></div>
      </div>
      <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
        <div style={{ flex: "1 1 360px" }}><JanelasComparador dados={dados} /></div>
        <div style={{ flex: "1 1 340px" }}><HorasUteisCard dias={trabalhados} /></div>
      </div>
      <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
        <div style={{ flex: "1 1 340px" }}><PresencaCard dias={dias} /></div>
        <div style={{ flex: "1 1 300px" }}><RecordesCard dias={dias} trabalhados={trabalhados} /></div>
      </div>
      <ComparadorFuturo />
    </div>
  );
}

// ═══ 1) Veredito: a resposta do dono, por JANELAS (nunca dia vs dia) ═══
function VereditoHero({ dados }: { dados: AnaliseDiaria }) {
  const j = dados.janelas;
  const t = dados.tendencia;
  if (!j) return null;
  const sem = j.semana;
  const mes = j.mes;
  const va = sem.atual.dias_trabalhados > 0 ? sem.atual.va_pct : mes.atual.va_pct;
  const delta = sem.delta_va_pp;
  const cor = va >= 60 ? leanCor("va") : va >= 40 ? "#c98a00" : leanCor("desp");
  const dirTxt = t?.direcao === "ascendente" ? "em ASCENSÃO" : t?.direcao === "descendente" ? "em QUEDA" : "estável";
  const dirCor = t?.direcao === "ascendente" ? leanCor("va") : t?.direcao === "descendente" ? leanCor("desp") : "var(--muted)";
  return (
    <Card style={{ padding: "20px 22px", borderLeft: `3px solid ${cor}`, background: "linear-gradient(120deg, var(--soft), #fff 60%)" }}>
      <div className="row gap4 wrap" style={{ alignItems: "center" }}>
        <Ring pct={va} size={96} stroke={9} color={cor}>
          <div className="col" style={{ alignItems: "center", lineHeight: 1 }}>
            <span className="font-display tnum" style={{ fontSize: 26, fontWeight: 700, color: "var(--ink)" }}>{va.toFixed(0)}%</span>
            <span style={{ fontSize: 9, color: "var(--muted)", fontWeight: 700 }}>produtivo</span>
          </div>
        </Ring>
        <div className="col grow" style={{ gap: 7, minWidth: 260 }}>
          <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
            Últimos 7 dias — o posto está rendendo?
          </div>
          <p style={{ fontSize: 15.5, fontWeight: 700, color: "var(--ink)", margin: 0 }}>
            {delta != null ? (
              <>Produtividade <span style={{ color: delta >= 0 ? leanCor("va") : leanCor("desp") }}>{delta >= 0 ? "▲ subiu" : "▼ caiu"} {Math.abs(delta).toFixed(0)} pts</span> vs os 7 dias anteriores{t ? <> · tendência <span style={{ color: dirCor }}>{dirTxt}</span></> : null}.</>
            ) : (
              <>Primeira semana medida — esta é a linha de base{t ? <> · tendência <span style={{ color: dirCor }}>{dirTxt}</span></> : null}.</>
            )}
          </p>
          <div className="row gap2 wrap" style={{ fontSize: 12, color: "var(--muted)" }}>
            <ChipStat icon="clock" texto={`${sem.atual.horas_produtivas_dia.toFixed(1)}h produtivas/dia`} />
            <ChipStat icon="calendar-check" texto={`${sem.atual.dias_trabalhados} dia(s) trabalhados`} />
            <ChipStat icon="calendar-x" texto={`${sem.atual.dias_sem_trabalho} sem trabalho`} alerta={sem.atual.dias_sem_trabalho > 0} />
            {sem.atual.posto_vazio_s > 0 && <ChipStat icon="user-x" texto={`posto vazio ${fmtDur(sem.atual.posto_vazio_s)}`} alerta />}
          </div>
        </div>
        <JanelaMini titulo="Últimos 30 dias" j={mes.atual} delta={mes.delta_va_pp} />
      </div>
    </Card>
  );
}

function ChipStat({ icon, texto, alerta }: { icon: string; texto: string; alerta?: boolean }) {
  return (
    <span className="row" style={{ gap: 5, padding: "3px 10px", borderRadius: 99, border: "1px solid var(--line)", background: "#fff", color: alerta ? leanCor("desp") : "var(--text)", fontWeight: 600 }}>
      <Icon name={icon} size={12} /> {texto}
    </span>
  );
}

function JanelaMini({ titulo, j, delta }: { titulo: string; j: JanelaAgregada; delta: number | null }) {
  if (!j || j.dias_trabalhados === 0) return null;
  return (
    <div className="col" style={{ gap: 3, padding: "10px 16px", borderRadius: 12, border: "1px solid var(--line)", background: "#fff", minWidth: 150 }}>
      <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: "var(--muted)" }}>{titulo}</span>
      <span className="font-display tnum" style={{ fontSize: 22, fontWeight: 700, color: "var(--ink)" }}>
        {j.va_pct.toFixed(0)}% <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>produtivo</span>
      </span>
      {delta != null && (
        <span style={{ fontSize: 11.5, fontWeight: 700, color: delta >= 0 ? leanCor("va") : leanCor("desp") }}>
          {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(0)} pts vs 30 anteriores
        </span>
      )}
      <span style={{ fontSize: 11, color: "var(--muted)" }}>{j.dias_trabalhados} dias trabalhados · {j.dias_sem_trabalho} sem trabalho</span>
    </div>
  );
}

// ═══ 2) OBRIGATÓRIA — Evolução por dia + ritmo/resumão do dia (mesmo card) ═══
const CATS: Array<{ k: keyof Pick<DiaAnalise, "va_pct" | "apoio_pct" | "desp_pct" | "none_pct">; cat: LeanShort }> = [
  { k: "va_pct", cat: "va" }, { k: "apoio_pct", cat: "apoio" },
  { k: "desp_pct", cat: "desp" }, { k: "none_pct", cat: "none" },
];

function EvolucaoPorDia({ dias, selecionado, onSelecionar, trabalhados }: {
  dias: DiaAnalise[]; selecionado: DiaAnalise | null; onSelecionar: (d: DiaAnalise) => void; trabalhados: DiaAnalise[];
}) {
  const W = 720, H = 220, padT = 8, padB = 26, padL = 8, padR = 8;
  const n = Math.max(1, dias.length);
  const slot = (W - padL - padR) / n;
  const bw = Math.min(26, Math.max(6, slot - 5));
  const plotH = H - padT - padB;
  const mediaJanela = trabalhados.length
    ? trabalhados.reduce((s, d) => s + d.va_pct, 0) / trabalhados.length
    : 0;
  return (
    <Card style={{ padding: 20 }}>
      <PanelHead
        titulo="Evolução por dia"
        ajuda="Cada coluna é UM DIA do calendário, dividida em produtivo, apoio, desperdício e não classificado. Dias sem trabalho aparecem marcados (cinza = sem captura, vermelho = máquina vazia o dia todo). Clique num dia para ver o ritmo e o resumo dele logo abaixo."
        leitura="Verde crescendo dia após dia = o posto está rendendo mais."
        right={<span style={{ fontSize: 12, color: "var(--muted)" }}>últimos {dias.length} dias</span>}
      />
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} role="img" aria-label="Evolução da composição do tempo por dia">
        {[0, 25, 50, 75, 100].map((g) => (
          <line key={g} x1={padL} x2={W - padR} y1={padT + (1 - g / 100) * plotH} y2={padT + (1 - g / 100) * plotH} stroke="var(--line-2)" />
        ))}
        {dias.map((d, i) => {
          const x = padL + i * slot + (slot - bw) / 2;
          const passoRotulo = Math.ceil(dias.length / 8);
          const mostraRotulo = i % passoRotulo === 0 || d.dia === selecionado?.dia;
          const sel = d.dia === selecionado?.dia;
          if (d.sem_trabalho) {
            const cor = d.sem_trabalho === "posto_vazio" ? leanCor("desp") : "var(--line)";
            const tip = d.sem_trabalho === "posto_vazio"
              ? `${d.dow} ${d.rot} — máquina vazia o dia todo (posto vazio)`
              : `${d.dow} ${d.rot} — sem captura neste dia`;
            return (
              <g key={d.dia} onClick={() => onSelecionar(d)} style={{ cursor: "pointer" }}>
                <rect x={x} y={padT} width={bw} height={plotH} rx="3" fill={cor} opacity={0.16}><title>{tip}</title></rect>
                <text x={x + bw / 2} y={padT + plotH / 2} fontSize="11" textAnchor="middle" fill={d.sem_trabalho === "posto_vazio" ? leanCor("desp") : "var(--faint)"}>✕</text>
                {mostraRotulo && <text x={x + bw / 2} y={H - padB + 14} fontSize="9" textAnchor="middle" fill="var(--muted)" fontFamily="var(--mono)">{d.rot}</text>}
              </g>
            );
          }
          const vals: Record<string, number> = {
            va_pct: Math.max(0, d.va_pct), apoio_pct: Math.max(0, d.apoio_pct),
            desp_pct: Math.max(0, d.desp_pct),
            none_pct: Math.max(0, 100 - d.va_pct - d.apoio_pct - d.desp_pct),
          };
          let yTopo = H - padB;
          const tip = `${d.dow} ${d.rot} — produtivo ${Math.round(d.va_pct)}% · apoio ${Math.round(d.apoio_pct)}% · desperdício ${Math.round(d.desp_pct)}% · ${fmtDur(d.tempo_obs_s)} observadas`;
          return (
            <g key={d.dia} onClick={() => onSelecionar(d)} style={{ cursor: "pointer" }}>
              {sel && <rect x={x - 3} y={padT - 3} width={bw + 6} height={plotH + 6} rx="5" fill="none" stroke="var(--accent)" strokeWidth={1.6} />}
              {CATS.map(({ k, cat }) => {
                const h = (vals[k] / 100) * plotH;
                if (h <= 0.5) return null;
                yTopo -= h;
                return <rect key={k} x={x} y={yTopo + 1} width={bw} height={Math.max(1, h - 2)} rx="2.5" fill={leanCor(cat)} opacity={cat === "none" ? 0.55 : 0.92}><title>{tip}</title></rect>;
              })}
              {mostraRotulo && <text x={x + bw / 2} y={H - padB + 14} fontSize="9" textAnchor="middle" fill={sel ? "var(--accent)" : "var(--muted)"} fontFamily="var(--mono)" fontWeight={sel ? 700 : 400}>{d.rot}</text>}
            </g>
          );
        })}
      </svg>
      <div className="row wrap" style={{ gap: 10, fontSize: 11, color: "var(--muted)", marginTop: 8 }}>
        {CATS.map(({ cat }) => (
          <span key={cat} className="row" style={{ gap: 5 }}>
            <i style={{ width: 9, height: 9, borderRadius: 3, background: leanCor(cat) }} /> {leanLabel(cat)}
          </span>
        ))}
        <span className="row" style={{ gap: 5 }}><span style={{ color: "var(--faint)" }}>✕</span> sem trabalho</span>
        <span style={{ marginLeft: "auto" }}>clique num dia para abrir o ritmo dele</span>
      </div>

      {/* ── Ritmo + resumão do dia selecionado — DENTRO do mesmo card ── */}
      {selecionado && (
        <div style={{ marginTop: 18, borderTop: "1px dashed var(--line)", paddingTop: 16 }}>
          <RitmoDoDiaSelecionado d={selecionado} mediaJanela={mediaJanela} />
        </div>
      )}
    </Card>
  );
}

function RitmoDoDiaSelecionado({ d, mediaJanela }: { d: DiaAnalise; mediaJanela: number }) {
  if (d.sem_trabalho) {
    return (
      <div className="col" style={{ gap: 6 }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
          {d.dow} {d.rot} — <span style={{ color: d.sem_trabalho === "posto_vazio" ? leanCor("desp") : "var(--muted)" }}>
            {d.sem_trabalho === "posto_vazio" ? "máquina vazia o dia todo" : "sem captura neste dia"}
          </span>
        </span>
        <p style={{ fontSize: 12.5, color: "var(--muted)", margin: 0 }}>
          {d.sem_trabalho === "posto_vazio"
            ? `As câmeras filmaram (${d.n_videos} vídeo(s), ${fmtDur(d.posto_vazio_s)} de posto vazio), mas o operador não trabalhou no posto.`
            : "Nenhum vídeo chegou deste dia — captura desligada, feriado ou falha do Pi."}
        </p>
      </div>
    );
  }
  const delta = d.va_pct - mediaJanela;
  const horas = d.por_hora || [];
  const maxSeg = Math.max(1, ...horas.map((h) => h.seg));
  const pico = horas.length ? horas.reduce((a, b) => (b.va_pct > a.va_pct ? b : a)) : null;
  const vale = horas.length ? horas.reduce((a, b) => (b.va_pct < a.va_pct ? b : a)) : null;
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row gap2 wrap" style={{ alignItems: "baseline" }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>Ritmo de {d.dow} {d.rot}</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>o dia do operador, hora a hora</span>
      </div>
      {/* Resumão do dia */}
      <div className="row gap2 wrap" style={{ fontSize: 12 }}>
        <ChipStat icon="clock" texto={`${fmtDur(d.tempo_obs_s)} observadas em ${d.n_videos} vídeo(s)`} />
        <ChipStat icon="gauge" texto={`${d.va_pct.toFixed(0)}% produtivo (${delta >= 0 ? "+" : ""}${delta.toFixed(0)} pts vs média do período)`} alerta={delta < -5} />
        {d.posto_vazio_s > 0 && <ChipStat icon="user-x" texto={`posto vazio ${fmtDur(d.posto_vazio_s)}`} alerta={d.posto_vazio_pct >= 20} />}
        {d.visitas > 0 && <ChipStat icon="users" texto={`${d.visitas} visita(s) ao posto`} />}
        {d.primeira_h && d.ultima_h && <ChipStat icon="sunrise" texto={`atividade de ${d.primeira_h} às ${d.ultima_h}`} />}
        {d.top_acao && <ChipStat icon="star" texto={`mais tempo em "${d.top_acao.label}" (${fmtDur(d.top_acao.seg)})`} />}
        {pico && vale && pico.hora !== vale.hora && (
          <ChipStat icon="trending-up" texto={`pico ${String(pico.hora).padStart(2, "0")}h (${pico.va_pct.toFixed(0)}%) · vale ${String(vale.hora).padStart(2, "0")}h (${vale.va_pct.toFixed(0)}%)`} />
        )}
      </div>
      {/* Ritmo hora a hora */}
      {horas.length >= 2 ? (
        <ul className="col" style={{ gap: 8, listStyle: "none", padding: 0, margin: 0 }}>
          {horas.map((h) => {
            const none = Math.max(0, 100 - h.va_pct - h.apoio_pct - h.desp_pct);
            return (
              <li key={h.hora} className="row gap2" title={`${h.hora}h — ${fmtDur(h.seg)} · ${Math.round(h.va_pct)}% produtivo · ${Math.round(h.desp_pct)}% desperdício`}>
                <span className="tnum" style={{ width: 34, fontSize: 12, fontWeight: 700, color: "var(--text)", flex: "none" }}>{String(h.hora).padStart(2, "0")}h</span>
                <div className="grow" style={{ opacity: 0.45 + 0.55 * (h.seg / maxSeg) }}>
                  <LeanBar va={h.va_pct} apoio={h.apoio_pct} desp={h.desp_pct} none={none} height={10} />
                </div>
                <span className="tnum" style={{ width: 52, textAlign: "right", fontSize: 11, color: "var(--muted)", flex: "none" }}>{fmtDur(h.seg)}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p style={{ fontSize: 12.5, color: "var(--muted)", margin: 0 }}>Poucas horas com atividade neste dia para desenhar o ritmo.</p>
      )}
    </div>
  );
}

// ═══ 3) Tendência temporal: sempre queremos ver o ascendente (ou o descendente) ═══
function TendenciaCard({ dias, tendencia }: { dias: DiaAnalise[]; tendencia: AnaliseDiaria["tendencia"] }) {
  const W = 460, H = 170, padT = 10, padB = 24, padL = 30, padR = 10;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const n = dias.length;
  const x = (i: number) => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => padT + (1 - Math.min(100, Math.max(0, v)) / 100) * plotH;
  // Reta de TENDÊNCIA = regressão linear da % produtiva pelos dias trabalhados
  // (a MESMA que a API usa p/ o slope do rodapé). Antes a linha era uma "média
  // móvel de 7 dias" que, com poucos dias não consecutivos, virava uma média
  // acumulada presa ao 1º dia (100%) — não passava pelos pontos e enganava.
  // Agora: pontos = dia real; linha fina liga os pontos; reta tracejada = tendência.
  const va = dias.map((d) => d.va_pct);
  const xm = (n - 1) / 2;
  const ym = va.reduce((s, v) => s + v, 0) / n;
  const slope = tendencia?.slope_pts_dia ??
    va.reduce((s, v, i) => s + (i - xm) * (v - ym), 0) /
      (va.reduce((s, _v, i) => s + (i - xm) ** 2, 0) || 1);
  const intercept = ym - slope * xm;
  const yReta = (i: number) => intercept + slope * i;
  const dir = tendencia?.direcao || "estável";
  const cor = dir === "ascendente" ? leanCor("va") : dir === "descendente" ? leanCor("desp") : "#c98a00";
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Tendência de produtividade"
        ajuda="A % produtiva de cada dia trabalhado (pontos, ligados pela linha fina) e a reta de tendência (tracejada = regressão linear). A inclinação da reta é o slope do rodapé — sempre por janela de dias trabalhados, nunca um dia isolado."
        leitura={dir === "ascendente" ? "Subindo — é isso que queremos ver." : dir === "descendente" ? "Caindo — vale conversar com o posto." : "Estável."}
      />
      {n < 3 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Precisa de pelo menos 3 dias trabalhados para medir a tendência.</p>
      ) : (
        <>
          <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} role="img" aria-label="Tendência da produtividade por dia">
            {[0, 25, 50, 75, 100].map((g) => (
              <g key={g}>
                <line x1={padL} x2={W - padR} y1={y(g)} y2={y(g)} stroke="var(--line-2)" />
                <text x={padL - 6} y={y(g) + 3} fontSize="8.5" textAnchor="end" fill="var(--faint)" fontFamily="var(--mono)">{g}%</text>
              </g>
            ))}
            {/* caminho real dia a dia — linha fina que LIGA os pontos */}
            <polyline fill="none" stroke={cor} strokeWidth={1.4} strokeLinejoin="round" opacity={0.3}
              points={dias.map((d, i) => `${x(i)},${y(d.va_pct)}`).join(" ")} />
            {/* reta de TENDÊNCIA (regressão) — é a inclinação descrita no rodapé */}
            <line x1={x(0)} y1={y(yReta(0))} x2={x(n - 1)} y2={y(yReta(n - 1))}
              stroke={cor} strokeWidth={2.4} strokeDasharray="6 4" opacity={0.95} />
            {dias.map((d, i) => (
              <circle key={d.dia} cx={x(i)} cy={y(d.va_pct)} r={3} fill="#fff" stroke={cor} strokeWidth={1.6}>
                <title>{`${d.dow} ${d.rot} — ${d.va_pct.toFixed(0)}% produtivo`}</title>
              </circle>
            ))}
            {dias.map((d, i) => {
              const passo = Math.ceil(n / 6);
              return i % passo === 0 ? (
                <text key={d.dia} x={x(i)} y={H - padB + 14} fontSize="8.5" textAnchor="middle" fill="var(--muted)" fontFamily="var(--mono)">{d.rot}</text>
              ) : null;
            })}
          </svg>
          {tendencia && (
            <p style={{ fontSize: 12.5, color: "var(--text)", marginTop: 8 }}>
              <b style={{ color: cor }}>{dir === "ascendente" ? "▲ Ascendente" : dir === "descendente" ? "▼ Descendente" : "◆ Estável"}</b>
              {" — "}{tendencia.slope_pts_dia >= 0 ? "+" : ""}{tendencia.slope_pts_dia.toFixed(1)} pts de produtividade por dia trabalhado ({tendencia.dias_considerados} dias medidos).
            </p>
          )}
        </>
      )}
    </Card>
  );
}

// ═══ 4) Presença no posto: o cara está lá? Dias sem trabalho na cara. ═══
function PresencaCard({ dias }: { dias: DiaAnalise[] }) {
  const semTrabalho = dias.filter((d) => d.sem_trabalho);
  const trabalhados = dias.filter((d) => !d.sem_trabalho && d.tempo_obs_s > 0);
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Presença no posto"
        ajuda="Quanto do tempo observado de cada dia teve o operador de fato no posto (100% menos o posto vazio). Dias sem trabalho são listados — máquina vazia o dia todo ou sem captura."
        leitura="A pergunta central: o operador está lá, trabalhando?"
      />
      <ul className="col" style={{ gap: 7, listStyle: "none", padding: 0, margin: 0 }}>
        {trabalhados.slice(-10).map((d) => {
          const presenca = Math.max(0, 100 - d.posto_vazio_pct);
          const cor = presenca >= 85 ? leanCor("va") : presenca >= 60 ? "#c98a00" : leanCor("desp");
          return (
            <li key={d.dia} className="row gap2" title={`${d.dow} ${d.rot} — ${presenca.toFixed(0)}% do tempo com o operador no posto`}>
              <span className="tnum" style={{ width: 44, fontSize: 11.5, fontWeight: 700, color: "var(--text)", flex: "none" }}>{d.rot}</span>
              <div className="grow track" style={{ height: 9 }}>
                <i style={{ width: `${presenca}%`, background: cor, display: "block", height: "100%", borderRadius: 99 }} />
              </div>
              <span className="tnum" style={{ width: 40, textAlign: "right", fontSize: 11, color: "var(--muted)", flex: "none" }}>{presenca.toFixed(0)}%</span>
            </li>
          );
        })}
      </ul>
      {semTrabalho.length > 0 && (
        <div className="col" style={{ gap: 6, marginTop: 14, paddingTop: 12, borderTop: "1px dashed var(--line)" }}>
          <span style={{ fontSize: 11.5, fontWeight: 700, color: leanCor("desp") }}>
            <Icon name="calendar-x" size={12} /> {semTrabalho.length} dia(s) sem trabalho no período
          </span>
          <div className="row gap1 wrap">
            {semTrabalho.map((d) => (
              <span key={d.dia} title={d.sem_trabalho === "posto_vazio" ? "máquina vazia o dia todo" : "sem captura"}
                style={{ fontSize: 11, fontWeight: 600, padding: "2px 9px", borderRadius: 99, border: `1px solid ${d.sem_trabalho === "posto_vazio" ? leanCor("desp") : "var(--line)"}`, color: d.sem_trabalho === "posto_vazio" ? leanCor("desp") : "var(--muted)", background: "#fff" }}>
                {d.dow} {d.rot} · {d.sem_trabalho === "posto_vazio" ? "máquina vazia" : "sem captura"}
              </span>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

// ═══ Jornada do dia: o FILME do dia selecionado numa faixa só ═══
const CAT_CORES: Record<string, string> = {
  va: leanCor("va"), apoio: leanCor("apoio"), desp: leanCor("desp"),
  none: leanCor("none"), vazio: "#8a8598",
};
const CAT_NOMES: Record<string, string> = {
  va: "produtivo", apoio: "apoio", desp: "desperdício", none: "não classificado", vazio: "posto vazio",
};

function fmtMin(m: number): string {
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.round(m % 60)).padStart(2, "0")}`;
}

function JornadaDoDia({ d }: { d: DiaAnalise }) {
  const faixas = d.linha_tempo || [];
  if (faixas.length === 0) return null;
  const ini = Math.max(0, faixas[0].ini_m - 15);
  const fim = Math.min(1440, faixas[faixas.length - 1].fim_m + 15);
  const span = Math.max(1, fim - ini);
  const marcas: number[] = [];
  for (let h = Math.ceil(ini / 60); h * 60 <= fim; h++) marcas.push(h * 60);
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo={`A jornada de ${d.dow} ${d.rot}`}
        ajuda="O dia inteiro numa faixa só, em blocos de 15 minutos: verde = produzindo, azul = apoio, vermelho = desperdício, cinza escuro = posto vazio. Buracos em branco = sem filmagem naquele horário."
        leitura="O filme do dia: dá pra ver quando começou, o almoço, os buracos e onde o dia rendeu."
      />
      <div style={{ position: "relative", height: 46, marginTop: 6 }}>
        <div style={{ position: "absolute", inset: "8px 0 14px", background: "var(--soft)", borderRadius: 8, border: "1px solid var(--line-2)" }} />
        {faixas.map((f, i) => (
          <div key={i}
            title={`${fmtMin(f.ini_m)}–${fmtMin(f.fim_m)} · ${CAT_NOMES[f.cat]}`}
            style={{
              position: "absolute", top: 8, bottom: 14,
              left: `${((f.ini_m - ini) / span) * 100}%`,
              width: `${((f.fim_m - f.ini_m) / span) * 100}%`,
              background: CAT_CORES[f.cat], opacity: f.cat === "none" ? 0.55 : 0.92,
              borderRadius: 3,
            }} />
        ))}
        {marcas.map((m) => (
          <span key={m} style={{ position: "absolute", bottom: 0, left: `${((m - ini) / span) * 100}%`, transform: "translateX(-50%)", fontSize: 9, color: "var(--faint)", fontFamily: "var(--mono)" }}>
            {Math.floor(m / 60)}h
          </span>
        ))}
      </div>
      <div className="row wrap" style={{ gap: 10, fontSize: 11, color: "var(--muted)", marginTop: 10 }}>
        {(["va", "apoio", "desp", "vazio", "none"] as const).map((c) => (
          <span key={c} className="row" style={{ gap: 5 }}>
            <i style={{ width: 9, height: 9, borderRadius: 3, background: CAT_CORES[c] }} /> {CAT_NOMES[c]}
          </span>
        ))}
      </div>
    </Card>
  );
}

// ═══ Top ações do dia selecionado (mini-pareto do dia) ═══
function TopAcoesDia({ d }: { d: DiaAnalise }) {
  const acoes = d.top_acoes || [];
  const maxSeg = Math.max(1, ...acoes.map((a) => a.seg));
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo={`No que ${d.dow} ${d.rot} foi gasto`}
        ajuda="As 5 ações que mais consumiram o tempo do dia selecionado."
        leitura="A ação nº 1 deveria ser a que agrega valor — se não for, o dia contou outra história."
      />
      {acoes.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Sem ações registradas neste dia.</p>
      ) : (
        <ul className="col" style={{ gap: 9, listStyle: "none", padding: 0, margin: 0 }}>
          {acoes.map((a, i) => (
            <li key={a.label} className="col" style={{ gap: 3 }}>
              <div className="row" style={{ justifyContent: "space-between", fontSize: 12 }}>
                <code className="font-mono" style={{ background: "var(--line-2)", padding: "1px 7px", borderRadius: 5, fontSize: 11 }}>{i + 1}. {a.label}</code>
                <span className="tnum" style={{ color: "var(--muted)" }}>{fmtDur(a.seg)}</span>
              </div>
              <div className="track" style={{ height: 7 }}>
                <i style={{ width: `${(a.seg / maxSeg) * 100}%`, background: "var(--grad-cta)", display: "block", height: "100%", borderRadius: 99 }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ═══ Mapa da quinzena: dia × hora — o padrão da semana pula aos olhos ═══
function HeatmapQuinzena({ dias, onSelecionar }: { dias: DiaAnalise[]; onSelecionar: (d: DiaAnalise) => void }) {
  const ultimos = dias.slice(-14);
  const horasSet = new Set<number>();
  ultimos.forEach((d) => (d.por_hora || []).forEach((h) => horasSet.add(h.hora)));
  const horas = Array.from(horasSet).sort((a, b) => a - b);
  if (horas.length === 0) {
    return (
      <Card style={{ padding: 20, height: "100%" }}>
        <PanelHead titulo="Mapa da quinzena" ajuda="Cada linha é um dia, cada célula uma hora — a cor diz o quanto rendeu." leitura="" />
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Ainda não há horas suficientes para o mapa.</p>
      </Card>
    );
  }
  const corCelula = (va: number) => {
    if (va >= 60) return leanCor("va");
    if (va >= 35) return "#c98a00";
    return leanCor("desp");
  };
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Mapa da quinzena"
        ajuda="Cada linha é um dia (últimos 14), cada célula é uma hora do relógio. Verde = hora produtiva, âmbar = mediana, vermelho = hora perdida, ✕ = dia sem trabalho. A opacidade acompanha o tempo filmado na hora."
        leitura="Padrões saltam aos olhos: toda tarde caindo? Toda segunda fraca?"
      />
      <div className="col" style={{ gap: 4 }}>
        <div className="row" style={{ gap: 3, paddingLeft: 52 }}>
          {horas.map((h) => (
            <span key={h} style={{ flex: 1, textAlign: "center", fontSize: 8.5, color: "var(--faint)", fontFamily: "var(--mono)" }}>{h}h</span>
          ))}
        </div>
        {ultimos.map((d) => {
          const porHora = new Map((d.por_hora || []).map((h) => [h.hora, h]));
          const maxSeg = Math.max(1, ...(d.por_hora || []).map((h) => h.seg));
          return (
            <div key={d.dia} className="row" style={{ gap: 3, alignItems: "center", cursor: "pointer" }} onClick={() => onSelecionar(d)}>
              <span className="tnum" style={{ width: 48, fontSize: 10.5, fontWeight: 600, color: "var(--muted)", flex: "none" }}>{d.dow} {d.rot}</span>
              {horas.map((h) => {
                if (d.sem_trabalho) {
                  return <span key={h} title={`${d.dow} ${d.rot} — ${d.sem_trabalho === "posto_vazio" ? "máquina vazia" : "sem captura"}`}
                    style={{ flex: 1, height: 16, borderRadius: 3, background: "var(--line-2)", display: "grid", placeItems: "center", fontSize: 8, color: "var(--faint)" }}>✕</span>;
                }
                const hd = porHora.get(h);
                if (!hd) return <span key={h} style={{ flex: 1, height: 16, borderRadius: 3, background: "var(--soft)", border: "1px solid var(--line-2)" }} />;
                return (
                  <span key={h}
                    title={`${d.dow} ${d.rot} ${h}h — ${Math.round(hd.va_pct)}% produtivo · ${fmtDur(hd.seg)}`}
                    style={{ flex: 1, height: 16, borderRadius: 3, background: corCelula(hd.va_pct), opacity: 0.35 + 0.65 * (hd.seg / maxSeg) }} />
                );
              })}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ═══ Semana vs semana: a comparação de JANELAS em barras (não em frase) ═══
function JanelasComparador({ dados }: { dados: AnaliseDiaria }) {
  const j = dados.janelas;
  if (!j || !j.semana.anterior.dias_trabalhados) {
    return (
      <Card style={{ padding: 20, height: "100%" }}>
        <PanelHead titulo="Semana vs semana" ajuda="Compara os últimos 7 dias com os 7 anteriores — sempre janela contra janela." leitura="" />
        <p style={{ fontSize: 13, color: "var(--muted)" }}>Quando houver duas semanas medidas, a comparação aparece aqui.</p>
      </Card>
    );
  }
  const linhas: Array<{ nome: string; cat: LeanShort | "vazio"; a: number; b: number }> = [
    { nome: "Produtivo", cat: "va", a: j.semana.atual.va_pct, b: j.semana.anterior.va_pct },
    { nome: "Apoio", cat: "apoio", a: j.semana.atual.apoio_pct, b: j.semana.anterior.apoio_pct },
    { nome: "Desperdício", cat: "desp", a: j.semana.atual.desp_pct, b: j.semana.anterior.desp_pct },
    { nome: "Posto vazio", cat: "vazio", a: j.semana.atual.vazio_pct, b: j.semana.anterior.vazio_pct },
  ];
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Semana vs semana"
        ajuda="Os últimos 7 dias (barra forte) contra os 7 anteriores (barra clara), categoria a categoria. É a comparação certa: janela contra janela, nunca um dia isolado."
        leitura="Produtivo subindo + desperdício caindo = a semana foi melhor de verdade."
      />
      <ul className="col" style={{ gap: 12, listStyle: "none", padding: 0, margin: 0 }}>
        {linhas.map((l) => {
          const delta = l.a - l.b;
          const bom = l.cat === "va" ? delta >= 0 : delta <= 0;
          const cor = l.cat === "vazio" ? CAT_CORES.vazio : leanCor(l.cat as LeanShort);
          return (
            <li key={l.nome} className="col" style={{ gap: 4 }}>
              <div className="row" style={{ justifyContent: "space-between", fontSize: 12 }}>
                <span style={{ fontWeight: 600, color: "var(--text)" }}>{l.nome}</span>
                <span className="tnum" style={{ fontWeight: 700, color: bom ? leanCor("va") : leanCor("desp") }}>
                  {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(0)} pts
                </span>
              </div>
              <div className="track" style={{ height: 9 }} title={`Últimos 7 dias: ${l.a.toFixed(0)}%`}>
                <i style={{ width: `${Math.min(100, l.a)}%`, background: cor, display: "block", height: "100%", borderRadius: 99 }} />
              </div>
              <div className="track" style={{ height: 9, opacity: 0.45 }} title={`7 dias anteriores: ${l.b.toFixed(0)}%`}>
                <i style={{ width: `${Math.min(100, l.b)}%`, background: cor, display: "block", height: "100%", borderRadius: 99 }} />
              </div>
            </li>
          );
        })}
      </ul>
      <p style={{ fontSize: 11, color: "var(--faint)", marginTop: 10 }}>barra forte = últimos 7 dias · barra clara = 7 dias anteriores</p>
    </Card>
  );
}

// ═══ Horas úteis por dia: o dono pensa em HORAS, não em % ═══
function HorasUteisCard({ dias }: { dias: DiaAnalise[] }) {
  const ultimos = dias.slice(-10);
  const horasDia = ultimos.map((d) => ({ d, h: (d.tempo_obs_s * d.va_pct) / 100 / 3600 }));
  const media = horasDia.length ? horasDia.reduce((s, x) => s + x.h, 0) / horasDia.length : 0;
  const maxH = Math.max(0.5, ...horasDia.map((x) => x.h));
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Horas úteis por dia"
        ajuda="Horas de trabalho PRODUTIVO entregues em cada dia (tempo observado × % produtivo). A linha pontilhada é a média do período."
        leitura="A pergunta em horas: quantas horas de valor o posto entrega por dia?"
      />
      <div className="row" style={{ gap: 6, alignItems: "flex-end", height: 120, position: "relative", marginTop: 6 }}>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: `${(media / maxH) * 100}%`, borderTop: "2px dashed var(--accent)", opacity: 0.6 }} title={`média ${media.toFixed(1)}h/dia`} />
        {horasDia.map(({ d, h }) => (
          <div key={d.dia} className="col" style={{ flex: 1, alignItems: "center", gap: 3, height: "100%", justifyContent: "flex-end" }}
            title={`${d.dow} ${d.rot} — ${h.toFixed(1)}h produtivas de ${fmtDur(d.tempo_obs_s)} observadas`}>
            <span className="tnum" style={{ fontSize: 9.5, color: "var(--muted)" }}>{h.toFixed(1)}</span>
            <div style={{ width: "70%", height: `${(h / maxH) * 82}%`, minHeight: 2, background: leanCor("va"), borderRadius: 4, opacity: 0.9 }} />
            <span style={{ fontSize: 8.5, color: "var(--faint)", fontFamily: "var(--mono)" }}>{d.rot}</span>
          </div>
        ))}
      </div>
      <p style={{ fontSize: 12, color: "var(--text)", marginTop: 10 }}>
        Média do período: <b>{media.toFixed(1)}h produtivas/dia</b>.
      </p>
    </Card>
  );
}

// ═══ Recordes & constância: marcos que o dono cobra na segunda-feira ═══
function RecordesCard({ dias, trabalhados }: { dias: DiaAnalise[]; trabalhados: DiaAnalise[] }) {
  if (trabalhados.length === 0) return null;
  const melhor = trabalhados.reduce((a, b) => (b.va_pct > a.va_pct ? b : a));
  const pior = trabalhados.reduce((a, b) => (b.va_pct < a.va_pct ? b : a));
  // Sequência atual de dias trabalhados (do fim pra trás) + recorde do período.
  let streakAtual = 0;
  for (let i = dias.length - 1; i >= 0; i--) {
    if (dias[i].sem_trabalho) break;
    if (dias[i].tempo_obs_s > 0) streakAtual++;
  }
  let recorde = 0, atual = 0;
  dias.forEach((d) => {
    if (!d.sem_trabalho && d.tempo_obs_s > 0) { atual++; recorde = Math.max(recorde, atual); }
    else atual = 0;
  });
  const media = trabalhados.reduce((s, d) => s + d.va_pct, 0) / trabalhados.length;
  const desvio = Math.sqrt(trabalhados.reduce((s, d) => s + (d.va_pct - media) ** 2, 0) / trabalhados.length);
  const constancia = desvio <= 8 ? "constante" : desvio <= 16 ? "variável" : "irregular";
  const itens = [
    { icon: "trophy", titulo: "Melhor dia", valor: `${melhor.dow} ${melhor.rot} · ${melhor.va_pct.toFixed(0)}%`, cor: leanCor("va") },
    { icon: "trending-down", titulo: "Pior dia", valor: `${pior.dow} ${pior.rot} · ${pior.va_pct.toFixed(0)}%`, cor: leanCor("desp") },
    { icon: "flame", titulo: "Sequência trabalhando", valor: `${streakAtual} dia(s) · recorde ${recorde}`, cor: "var(--accent)" },
    { icon: "activity", titulo: "Constância", valor: `${constancia} (±${desvio.toFixed(0)} pts)`, cor: desvio <= 8 ? leanCor("va") : desvio <= 16 ? "#c98a00" : leanCor("desp") },
  ];
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Recordes & constância"
        ajuda="Melhor e pior dia do período, a sequência atual de dias trabalhados sem falha e a regularidade da produtividade (desvio entre os dias)."
        leitura="Melhor dia é a meta; constância é o que sustenta o mês."
      />
      <ul className="col" style={{ gap: 10, listStyle: "none", padding: 0, margin: 0 }}>
        {itens.map((it) => (
          <li key={it.titulo} className="row gap2" style={{ alignItems: "center" }}>
            <span className="center" style={{ width: 30, height: 30, borderRadius: 9, background: "var(--soft)", color: it.cor, flex: "none" }}>
              <Icon name={it.icon} size={15} />
            </span>
            <div className="col" style={{ gap: 1 }}>
              <span style={{ fontSize: 10.5, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".04em", color: "var(--muted)" }}>{it.titulo}</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{it.valor}</span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ═══ O que vem: comparar operadores/postos (pedido do Fernando) ═══
function ComparadorFuturo() {
  return (
    <Card style={{ padding: "14px 20px", border: "1px dashed var(--p-200)", background: "var(--soft)" }}>
      <div className="row gap2" style={{ alignItems: "center" }}>
        <Icon name="users" size={16} color="var(--accent)" />
        <p style={{ fontSize: 12.5, color: "var(--muted)", margin: 0 }}>
          <b style={{ color: "var(--ink)" }}>Em breve:</b> comparar o dia a dia entre operadores e postos — quem rende mais, em que horário, e por quê.
        </p>
      </div>
    </Card>
  );
}
