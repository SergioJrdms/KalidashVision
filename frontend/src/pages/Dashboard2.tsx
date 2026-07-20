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
      <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
        <div style={{ flex: "1.2 1 380px" }}><TendenciaCard dias={trabalhados} tendencia={dados.tendencia} /></div>
        <div style={{ flex: "1 1 320px" }}><PresencaCard dias={dias} /></div>
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
  const media7 = dias.map((_, i) => {
    const ini = Math.max(0, i - 6);
    const fatia = dias.slice(ini, i + 1);
    return fatia.reduce((s, d) => s + d.va_pct, 0) / fatia.length;
  });
  const dir = tendencia?.direcao || "estável";
  const cor = dir === "ascendente" ? leanCor("va") : dir === "descendente" ? leanCor("desp") : "#c98a00";
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Tendência de produtividade"
        ajuda="A % produtiva de cada dia trabalhado (pontos) e a média móvel de 7 dias (linha). A inclinação diz se o posto está subindo ou caindo — sempre por janela de tempo, nunca um dia isolado."
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
            <polyline fill="none" stroke={cor} strokeWidth={2.2} strokeLinejoin="round"
              points={media7.map((v, i) => `${x(i)},${y(v)}`).join(" ")} opacity={0.95} />
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

// ═══ 5) O que vem: comparar operadores/postos (pedido do Fernando) ═══
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
