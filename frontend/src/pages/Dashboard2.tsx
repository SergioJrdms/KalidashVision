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
import { nomeHumano } from "../design/rotulos";
import { leanCor, leanLabel, type LeanShort } from "../design/helpers";
import { pedirAuditoriaDoDia } from "./Auditoria";
import type { ProcHeaderMock } from "../lib/adapt";
import type { Go } from "../design/Shell";
import type { AnaliseDiaria, DiaAnalise, JanelaAgregada } from "../lib/types";

export default function Dashboard2({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const q = useQuery({ queryKey: ["diaadia", proc.id], queryFn: () => api.diaadia.analise(proc.id, 30) });
  const dados = q.data || null;
  const dias = useMemo(() => dados?.dias || [], [dados]);
  const trabalhados = useMemo(() => dias.filter((d) => !d.sem_trabalho && d.tempo_obs_s > 0), [dias]);
  // null = NENHUM dia selecionado → os cards de detalhe mostram o AGREGADO de
  // todos os dias trabalhados. Clicar num dia já selecionado deseleciona (volta
  // pro agregado). O padrão é o agregado.
  const [diaSel, setDiaSel] = useState<string | null>(null);
  const toggleDia = (d: DiaAnalise) => setDiaSel((prev) => (prev === d.dia ? null : d.dia));
  const selecionado = useMemo(
    () => (diaSel ? dias.find((d) => d.dia === diaSel) || null : null),
    [dias, diaSel],
  );
  const agregado = useMemo(() => construirAgregado(trabalhados), [trabalhados]);
  const alvo = selecionado ?? agregado;   // o que os cards de detalhe exibem
  const ehAgregado = !selecionado;

  if (q.isLoading) return <Empty icon="loader" title="Montando o dia a dia…" />;
  if (!dados || dias.length === 0 || trabalhados.length === 0) {
    return <Empty icon="calendar-days" title="Ainda não há dias analisados" desc="Assim que as primeiras capturas com data real forem processadas, o dia a dia do operador aparece aqui." />;
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <VereditoHero dados={dados} />
      <EvolucaoPorDia dias={dias} selecionado={selecionado} alvo={alvo} ehAgregado={ehAgregado} onSelecionar={toggleDia} trabalhados={trabalhados} onAuditar={(d) => { pedirAuditoriaDoDia(d); go("processo", proc.id, "auditoria"); }} />
      {alvo && !alvo.sem_trabalho && (alvo.linha_tempo.length > 0 || alvo.top_acoes.length > 0) && (
        <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
          {alvo.linha_tempo.length > 0 && <div style={{ flex: "1.4 1 420px" }}><JornadaDoDia d={alvo} agregado={ehAgregado} proc={proc} /></div>}
          <div style={{ flex: "1 1 300px" }}><TopAcoesDia d={alvo} agregado={ehAgregado} /></div>
        </div>
      )}
      <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
        <div style={{ flex: "1.2 1 380px" }}><TendenciaCard dias={trabalhados} tendencia={dados.tendencia} /></div>
        <div style={{ flex: "1 1 340px" }}><DuvidaCard dias={trabalhados} /></div>
      </div>
      <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
        <div style={{ flex: "1 1 340px" }}><HeatmapQuinzena dias={dias} onSelecionar={toggleDia} /></div>
      </div>
      <div className="row gap4 wrap" style={{ alignItems: "stretch" }}>
        <div style={{ flex: "1 1 360px" }}><JanelasComparador dados={dados} /></div>
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
            <ChipStat icon="calendar-check" texto={`${sem.atual.dias_trabalhados} dia(s) trabalhados`} />
            <ChipStat icon="calendar-x" texto={`${sem.atual.dias_sem_trabalho} sem trabalho`} alerta={sem.atual.dias_sem_trabalho > 0} />
            {sem.atual.vazio_pct > 0 && <ChipStat icon="user-x" texto={`posto vazio ${sem.atual.vazio_pct.toFixed(0)}%`} alerta />}
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
// Fase 63: produtivo × não-produtivo fecham 100%. `vazio_pct` é desenhado
// DENTRO do não-produtivo (mesma barra, cor própria), porque a causa de
// "operador ausente" é outra — mas não é uma terceira categoria.
const CATS: Array<{ k: keyof Pick<DiaAnalise, "va_pct" | "desp_pct" | "vazio_pct">; cat: LeanShort }> = [
  { k: "va_pct", cat: "va" }, { k: "desp_pct", cat: "desp" },
  { k: "vazio_pct", cat: "vazio" },
];

// "Dia típico": para cada faixa de 15 min do relógio, a PROPORÇÃO de cada
// categoria somando todos os dias (por tempo, não por contagem). Fase 50: antes
// pegava só a categoria dominante do slot, o que escondia minorias (ex.: um
// desperdício recorrente mas curto sumia). Agora cada slot é fatiado na
// proporção real. Slots sem cobertura viram buraco; faixas vizinhas de mesma
// categoria são fundidas.
const ORDEM_CAT_TIPICO = ["va", "desp", "vazio"] as const;
function agregarLinhaTempo(dias: DiaAnalise[]): DiaAnalise["linha_tempo"] {
  const SLOT = 15, N = Math.ceil(1440 / SLOT);
  // minutos de cada categoria por slot, somando a SOBREPOSIÇÃO real de todas as faixas.
  const tally: Record<string, number>[] = Array.from({ length: N }, () => ({}));
  for (const d of dias) for (const f of d.linha_tempo || []) {
    const s0 = Math.max(0, Math.floor(f.ini_m / SLOT));
    const s1 = Math.min(N, Math.ceil(f.fim_m / SLOT));
    for (let s = s0; s < s1; s++) {
      const ov = Math.min(f.fim_m, (s + 1) * SLOT) - Math.max(f.ini_m, s * SLOT);
      if (ov > 0) tally[s][f.cat] = (tally[s][f.cat] || 0) + ov;
    }
  }
  const faixas: DiaAnalise["linha_tempo"] = [];
  for (let s = 0; s < N; s++) {
    const t = tally[s];
    const totalSlot = Object.values(t).reduce((a, b) => a + b, 0);
    if (totalSlot <= 0) continue;   // horário sem filmagem em nenhum dia = buraco
    let cursor = s * SLOT;
    for (const cat of ORDEM_CAT_TIPICO) {
      const m = t[cat] || 0;
      if (m <= 0) continue;
      const ini_m = cursor, fim_m = cursor + SLOT * (m / totalSlot);
      cursor = fim_m;
      const last = faixas[faixas.length - 1];
      if (last && last.cat === cat && Math.abs(last.fim_m - ini_m) < 0.02) last.fim_m = fim_m;
      else faixas.push({ ini_m, fim_m, cat: cat as DiaAnalise["linha_tempo"][number]["cat"] });
    }
  }
  return faixas;
}

// Agrega TODOS os dias trabalhados num único "dia sintético" (dia="__agg__"),
// usado quando nenhum dia está selecionado. Percentuais são média ponderada
// pelo tempo observado; por_hora e top_acoes somam por hora/ação; a jornada
// vira o "dia típico" (agregarLinhaTempo). primeira/ultima hora = min/max.
function construirAgregado(dias: DiaAnalise[]): DiaAnalise | null {
  if (!dias.length) return null;
  const tot = dias.reduce((s, d) => s + d.tempo_obs_s, 0) || 1;
  const wavg = (f: (d: DiaAnalise) => number) => dias.reduce((s, d) => s + f(d) * d.tempo_obs_s, 0) / tot;

  const horaMap = new Map<number, { seg: number; va: number; de: number; vz: number }>();
  for (const d of dias) for (const h of d.por_hora || []) {
    const c = horaMap.get(h.hora) || { seg: 0, va: 0, de: 0, vz: 0 };
    c.seg += h.seg; c.va += h.va_pct * h.seg; c.de += h.desp_pct * h.seg;
    c.vz += (h.vazio_pct || 0) * h.seg;
    horaMap.set(h.hora, c);
  }
  const por_hora = [...horaMap.entries()].sort((a, b) => a[0] - b[0]).map(([hora, v]) => ({
    hora, seg: v.seg,
    va_pct: v.seg ? v.va / v.seg : 0, desp_pct: v.seg ? v.de / v.seg : 0,
    vazio_pct: v.seg ? v.vz / v.seg : 0,
  }));

  const acaoMap = new Map<string, number>();
  for (const d of dias) for (const a of d.top_acoes || []) acaoMap.set(a.label, (acaoMap.get(a.label) || 0) + a.seg);
  const top_acoes = [...acaoMap.entries()].map(([label, seg]) => ({ label, seg })).sort((a, b) => b.seg - a.seg).slice(0, 8);

  const primeiras = dias.map((d) => d.primeira_h).filter((x): x is string => !!x);
  const ultimas = dias.map((d) => d.ultima_h).filter((x): x is string => !!x);
  return {
    dia: "__agg__", rot: `${dias.length} dias`, dow: "",
    tempo_obs_s: dias.reduce((s, d) => s + d.tempo_obs_s, 0),
    va_pct: wavg((d) => d.va_pct), vazio_pct: wavg((d) => d.vazio_pct || 0),
    duvida_pct: wavg((d) => d.duvida_pct || 0),
    sem_evidencia_pct: wavg((d) => d.sem_evidencia_pct || 0),
    nao_observado_pct: wavg((d) => d.nao_observado_pct || 0),
    nao_observado_gate_pct: wavg((d) => d.nao_observado_gate_pct || 0),
    desp_pct: wavg((d) => d.desp_pct),
    posto_vazio_s: dias.reduce((s, d) => s + d.posto_vazio_s, 0),
    posto_vazio_pct: wavg((d) => d.posto_vazio_pct),
    n_videos: dias.reduce((s, d) => s + d.n_videos, 0),
    visitas: dias.reduce((s, d) => s + d.visitas, 0),
    primeira_h: primeiras.length ? primeiras.reduce((a, b) => (a < b ? a : b)) : null,
    ultima_h: ultimas.length ? ultimas.reduce((a, b) => (a > b ? a : b)) : null,
    top_acao: top_acoes[0] || null,
    top_acoes,
    linha_tempo: agregarLinhaTempo(dias),
    por_hora,
    sem_trabalho: null,
  };
}

function EvolucaoPorDia({ dias, selecionado, alvo, ehAgregado, onSelecionar, trabalhados, onAuditar }: {
  dias: DiaAnalise[]; selecionado: DiaAnalise | null; alvo: DiaAnalise | null; ehAgregado: boolean; onSelecionar: (d: DiaAnalise) => void; trabalhados: DiaAnalise[]; onAuditar: (dia: string) => void;
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
        ajuda="Cada coluna é UM DIA do calendário, dividida em produtivo e não-produtivo (com o posto vazio destacado dentro do não-produtivo). Dias sem trabalho aparecem marcados (cinza = sem captura, vermelho = máquina vazia o dia todo). Clique num dia para ver o ritmo e o resumo dele logo abaixo."
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
              ? `${d.dow} ${d.rot} — máquina vazia o dia todo (posto vazio) · clique e audite: nenhum destes trechos passou por gente`
              : `${d.dow} ${d.rot} — sem captura neste dia`;
            return (
              <g key={d.dia} onClick={() => onSelecionar(d)} style={{ cursor: "pointer" }}>
                {sel && <rect x={x - 3} y={padT - 3} width={bw + 6} height={plotH + 6} rx="5" fill="none" stroke="var(--accent)" strokeWidth={1.6} />}
                <rect x={x} y={padT} width={bw} height={plotH} rx="3" fill={cor} opacity={0.16}><title>{tip}</title></rect>
                <text x={x + bw / 2} y={padT + plotH / 2} fontSize="11" textAnchor="middle" fill={d.sem_trabalho === "posto_vazio" ? leanCor("desp") : "var(--faint)"}>✕</text>
                {d.atipico_vazio && <MarcaAtipico x={x + bw / 2} y={padT + 5} tip={tip} />}
              {(d.versoes_instrumento || []).length > 1 && (
                // Fase 85: o dia em que o instrumento mudou. A linha existe
                // para que ninguém compare os dois lados sem saber.
                <g style={{ pointerEvents: "none" }}>
                  <line x1={x - 2} x2={x - 2} y1={padT} y2={H - padB}
                        stroke="var(--accent)" strokeWidth={1.6} strokeDasharray="3 3">
                    <title>{`${d.dow} ${d.rot} — a MEDIÇÃO mudou neste dia (instrumento ${(d.versoes_instrumento || []).join(" → ")}). O número antes e depois não é comparável.`}</title>
                  </line>
                </g>
              )}
                {mostraRotulo && <text x={x + bw / 2} y={H - padB + 14} fontSize="9" textAnchor="middle" fill="var(--muted)" fontFamily="var(--mono)">{d.rot}</text>}
              </g>
            );
          }
          // `desp_pct` é o NÃO-PRODUTIVO INTEIRO e já contém `vazio_pct`.
          // Empilhar os três como vêm somava 100 + vazio e a coluna passava do
          // topo do gráfico. O posto vazio é desenhado DENTRO do não-produtivo.
          const vazioDia = Math.min(Math.max(0, d.vazio_pct || 0), Math.max(0, d.desp_pct));
          const vals: Record<string, number> = {
            va_pct: Math.max(0, d.va_pct),
            desp_pct: Math.max(0, d.desp_pct) - vazioDia,
            vazio_pct: vazioDia,
          };
          let yTopo = H - padB;
          const tip = `${d.dow} ${d.rot} — produtivo ${Math.round(d.va_pct)}% · desperdício ${Math.round(d.desp_pct)}%`
            + (d.atipico_vazio ? ` · ${Math.round(d.posto_vazio_pct)}% de posto vazio — dia atípico, vale auditar` : "");
          return (
            <g key={d.dia} onClick={() => onSelecionar(d)} style={{ cursor: "pointer" }}>
              {sel && <rect x={x - 3} y={padT - 3} width={bw + 6} height={plotH + 6} rx="5" fill="none" stroke="var(--accent)" strokeWidth={1.6} />}
              {CATS.map(({ k, cat }) => {
                const h = (vals[k] / 100) * plotH;
                if (h <= 0.5) return null;
                yTopo -= h;
                return <rect key={k} x={x} y={yTopo + 1} width={bw} height={Math.max(1, h - 2)} rx="2.5" fill={leanCor(cat)} opacity={0.92}><title>{tip}</title></rect>;
              })}
              {d.atipico_vazio && <MarcaAtipico x={x + bw / 2} y={padT + 5} tip={tip} />}
              {(d.versoes_instrumento || []).length > 1 && (
                // Fase 85: o dia em que o instrumento mudou. A linha existe
                // para que ninguém compare os dois lados sem saber.
                <g style={{ pointerEvents: "none" }}>
                  <line x1={x - 2} x2={x - 2} y1={padT} y2={H - padB}
                        stroke="var(--accent)" strokeWidth={1.6} strokeDasharray="3 3">
                    <title>{`${d.dow} ${d.rot} — a MEDIÇÃO mudou neste dia (instrumento ${(d.versoes_instrumento || []).join(" → ")}). O número antes e depois não é comparável.`}</title>
                  </line>
                </g>
              )}
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
        {dias.some((d) => (d.versoes_instrumento || []).length > 1) && (
          <span className="row" style={{ gap: 5 }} title="Neste dia o sistema passou a medir de outro jeito. Comparar os dois lados da linha é comparar instrumentos diferentes, não desempenhos diferentes.">
            <span style={{ color: "var(--accent)", fontWeight: 800 }}>┆</span> a medição mudou aqui
          </span>
        )}
        {dias.some((d) => d.atipico_vazio) && (
          <span className="row" style={{ gap: 5 }} title="Dia quase todo posto vazio: ou é falta real, ou é falha de detecção. Clique no dia para auditá-lo.">
            <span style={{ color: leanCor("desp"), fontWeight: 800 }}>!</span> dia atípico (audite)
          </span>
        )}
        {selecionado ? (
          <button
            onClick={() => onSelecionar(selecionado)}
            title="Voltar a mostrar o agregado de todos os dias"
            style={{ marginLeft: "auto", cursor: "pointer", border: "1px solid var(--line)", background: "#fff", borderRadius: 99, padding: "3px 11px", fontSize: 11, fontWeight: 600, color: "var(--text)" }}
          >
            ✕ ver agregado (todos os dias)
          </button>
        ) : (
          <span style={{ marginLeft: "auto" }}>mostrando o agregado — clique num dia para ver só ele</span>
        )}
      </div>

      {/* ── Ritmo + resumão — do dia selecionado OU do agregado de tudo ── */}
      {alvo && (
        <div style={{ marginTop: 18, borderTop: "1px dashed var(--line)", paddingTop: 16 }}>
          <RitmoDoDiaSelecionado d={alvo} mediaJanela={mediaJanela} agregado={ehAgregado} onAuditar={onAuditar} />
        </div>
      )}
    </Card>
  );
}

// Fase 80: a marca do dia atípico. Um dia quase todo posto vazio é INVISÍVEL
// por construção — os eventos saem da fila por mecanismo, ninguém os julga, e
// o dia vira só um número. Este "!" é o convite para abrir e conferir.
function MarcaAtipico({ x, y, tip }: { x: number; y: number; tip: string }) {
  return (
    <g style={{ pointerEvents: "none" }}>
      <circle cx={x} cy={y} r={5.5} fill={leanCor("desp")} opacity={0.92}><title>{tip}</title></circle>
      <text x={x} y={y + 3.2} fontSize="8" fontWeight="800" textAnchor="middle" fill="#fff">!</text>
    </g>
  );
}

// O convite explícito, no dia selecionado. Sai no MESMO lugar nos dois ramos
// (dia trabalhado e dia sem trabalho) porque o caso do dia 29 caiu no segundo.
function ConviteAuditar({ d, onAuditar }: { d: DiaAnalise; onAuditar: (dia: string) => void }) {
  return (
    <div className="row gap2 wrap" style={{ alignItems: "center", background: "var(--desp-bg)", border: `1px solid ${leanCor("desp")}33`, borderRadius: 10, padding: "10px 12px" }}>
      <Icon name="alert-triangle" size={16} color={leanCor("desp")} />
      <span className="grow" style={{ fontSize: 12.5, color: "var(--text)", minWidth: 200 }}>
        <b>{Math.round(d.posto_vazio_pct)}% do dia como posto vazio.</b>{" "}
        Nenhum destes trechos passou por uma pessoa — eles saem da fila por
        mecanismo. Ou é falta real, ou é falha de detecção.
      </span>
      <button
        onClick={() => onAuditar(d.dia)}
        className="row gap1 click"
        style={{ border: `1px solid ${leanCor("desp")}`, background: "#fff", color: leanCor("desp"), borderRadius: 99, padding: "6px 14px", fontSize: 12, fontWeight: 700, cursor: "pointer", flex: "none" }}
      >
        <Icon name="search" size={13} /> Auditar este dia
      </button>
    </div>
  );
}

function RitmoDoDiaSelecionado({ d, mediaJanela, agregado, onAuditar }: { d: DiaAnalise; mediaJanela: number; agregado?: boolean; onAuditar: (dia: string) => void }) {
  // O convite só faz sentido num dia REAL: o agregado é um dia sintético
  // (dia="__agg__") e não existe para auditar.
  const convite = !agregado && d.atipico_vazio && d.dia !== "__agg__";
  if (d.sem_trabalho) {
    return (
      <div className="col" style={{ gap: 10 }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
          {d.dow} {d.rot} — <span style={{ color: d.sem_trabalho === "posto_vazio" ? leanCor("desp") : "var(--muted)" }}>
            {d.sem_trabalho === "posto_vazio" ? "máquina vazia o dia todo" : "sem captura neste dia"}
          </span>
        </span>
        <p style={{ fontSize: 12.5, color: "var(--muted)", margin: 0 }}>
          {d.sem_trabalho === "posto_vazio"
            ? "As câmeras filmaram, mas o operador não trabalhou no posto — o dia inteiro ficou como posto vazio."
            : "Nenhuma captura chegou deste dia — câmera desligada, feriado ou falha no Pi."}
        </p>
        {convite && <ConviteAuditar d={d} onAuditar={onAuditar} />}
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
        <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
          {agregado ? "Ritmo médio — todos os dias trabalhados" : `Ritmo de ${d.dow} ${d.rot}`}
        </span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          {agregado ? "o dia típico do operador, hora a hora (agregado do período)" : "o dia do operador, hora a hora"}
        </span>
      </div>
      {/* Resumão do dia (ou do agregado) */}
      <div className="row gap2 wrap" style={{ fontSize: 12 }}>
        {agregado ? (
          <ChipStat icon="gauge" texto={`${d.va_pct.toFixed(0)}% produtivo no período`} alerta={d.va_pct < 40} />
        ) : (
          <ChipStat icon="gauge" texto={`${d.va_pct.toFixed(0)}% produtivo (${delta >= 0 ? "+" : ""}${delta.toFixed(0)} pts vs média do período)`} alerta={delta < -5} />
        )}
        {d.posto_vazio_s > 0 && <ChipStat icon="user-x" texto={`posto vazio ${d.posto_vazio_pct.toFixed(0)}%`} alerta={d.posto_vazio_pct >= 20} />}
        {d.visitas > 0 && <ChipStat icon="users" texto={`${d.visitas} visita(s) ao posto`} />}
        {d.primeira_h && d.ultima_h && <ChipStat icon="sunrise" texto={`atividade de ${d.primeira_h} às ${d.ultima_h}`} />}
        {d.top_acao && <ChipStat icon="star" texto={`mais tempo em "${nomeHumano(d.top_acao.label)}"`} />}
        {pico && vale && pico.hora !== vale.hora && (
          <ChipStat icon="trending-up" texto={`pico ${String(pico.hora).padStart(2, "0")}h (${pico.va_pct.toFixed(0)}%) · vale ${String(vale.hora).padStart(2, "0")}h (${vale.va_pct.toFixed(0)}%)`} />
        )}
      </div>
      {convite && <ConviteAuditar d={d} onAuditar={onAuditar} />}
      {/* Ritmo hora a hora */}
      {horas.length >= 2 ? (
        <ul className="col" style={{ gap: 8, listStyle: "none", padding: 0, margin: 0 }}>
          {horas.map((h) => {
            return (
              <li key={h.hora} className="row gap2" title={`${h.hora}h — ${Math.round(h.va_pct)}% produtivo · ${Math.round(h.desp_pct)}% desperdício`}>
                <span className="tnum" style={{ width: 34, fontSize: 12, fontWeight: 700, color: "var(--text)", flex: "none" }}>{String(h.hora).padStart(2, "0")}h</span>
                <div className="grow" style={{ opacity: 0.45 + 0.55 * (h.seg / maxSeg) }}>
                  <LeanBar va={h.va_pct} desp={h.desp_pct} vazio={h.vazio_pct || 0} height={10} />
                </div>
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

// ═══ B5 — O KPI que responde à pergunta do negócio ═══
// "% do tempo observado em DÚVIDA", por dia. Esta curva é o veredito do
// produto: se cai semana a semana, o sistema aprende; se estabiliza em 20-30%,
// a tese está errada. Por isso é visível e permanente, não um número escondido.
function DuvidaCard({ dias }: { dias: DiaAnalise[] }) {
  const pts = dias.filter((d) => d.duvida_pct != null);
  if (pts.length === 0) return null;
  const W = 460, H = 150, padT = 10, padB = 24, padL = 30, padR = 10;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const n = pts.length;
  const x = (i: number) => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => padT + (1 - Math.min(100, Math.max(0, v)) / 100) * plotH;
  const ultimo = pts[pts.length - 1];
  const atual = ultimo.duvida_pct;
  const semEvid = ultimo.sem_evidencia_pct || 0;
  const naoObs = ultimo.nao_observado_pct || 0;
  const naoObsGate = ultimo.nao_observado_gate_pct || 0;
  // Fase 66: a curva é HISTÓRICA — validar um trecho não o apaga do dia em que
  // aconteceu. O que muda é quanto dela já foi julgado, e isso vira a área
  // preenchida sob a linha: o trabalho feito fica visível em vez de sumir.
  const resolvidoAgora = ultimo.duvida_resolvida_pct || 0;
  const totalLevantado = pts.reduce((t, d) => t + d.duvida_pct, 0);
  const totalResolvido = pts.reduce((t, d) => t + (d.duvida_resolvida_pct || 0), 0);
  const pctJulgado = totalLevantado > 0 ? (totalResolvido / totalLevantado) * 100 : 0;
  // Tendência simples: média da 1ª metade contra a 2ª.
  const meio = Math.floor(n / 2);
  const m = (a: DiaAnalise[]) => (a.length ? a.reduce((s, d) => s + d.duvida_pct, 0) / a.length : 0);
  const delta = n >= 4 ? m(pts.slice(meio)) - m(pts.slice(0, meio)) : null;
  const cor = atual >= 30 ? leanCor("desp") : atual >= 20 ? "#c98a00" : leanCor("va");
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo="Quanto o sistema não sabe"
        ajuda="Parte do tempo observado em que a leitura ficou em dúvida naquele dia — as amostras do minuto discordaram, uma verificação da cena contradisse o rótulo, ou ninguém decidiu se o comportamento agrega valor. A curva é HISTÓRICA: validar um trecho não o apaga do dia em que aconteceu, só o marca como julgado (a área preenchida). Tempo que o sistema NÃO OLHOU (herdado por economia) NUNCA entra nesta curva — aparece separado, em âmbar."
        leitura="Esta curva é o veredito: caindo semana a semana, o sistema está aprendendo."
      />
      <div className="row gap2" style={{ alignItems: "baseline", marginBottom: 6 }}>
        <span className="font-display tnum" style={{ fontSize: 26, fontWeight: 700, color: cor }}>
          {atual.toFixed(0)}%
        </span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>do tempo observado, no último dia</span>
      </div>
      {totalLevantado > 0 && (
        <p style={{ fontSize: 11.5, color: "var(--muted)", margin: "0 0 6px" }}>
          <b className="tnum" style={{ color: leanCor("va") }}>{pctJulgado.toFixed(0)}%</b> da
          dúvida do período já foi julgada por você
          {resolvidoAgora > 0 && ` · ${resolvidoAgora.toFixed(0)} pts do último dia`}
          {" "}— o julgado continua no gráfico, preenchido.
        </p>
      )}
      {semEvid > 0 && (
        // Caso DIFERENTE: trecho curto demais para afirmar ou duvidar. Resolve-se
        // com mais amostragem, não com melhor decisão — por isso fica separado.
        <p style={{ fontSize: 11.5, color: "var(--muted)", margin: "0 0 6px" }}>
          + <b className="tnum">{semEvid.toFixed(0)}%</b> sem evidência suficiente
          (trechos curtos demais para julgar) — isso se resolve gravando mais denso.
        </p>
      )}
      {naoObs > 0 && (
        // Fase 90 — A TERCEIRA COISA, e ela NÃO é dúvida.
        // "Olhei e não sei" é o sistema sendo honesto sobre uma cena difícil.
        // "NÃO OLHEI" é o sistema tendo economizado. Se as duas entrassem na
        // mesma curva, um corte de custo apareceria como perda de confiança do
        // modelo — exatamente o gráfico errado para mostrar a um sócio.
        // Caixa própria, cor própria (âmbar de alerta, não o vermelho da dúvida).
        <div style={{ fontSize: 11.5, margin: "0 0 6px", padding: "7px 10px",
                      background: "var(--apoio-bg)", border: "1px solid var(--apoio)",
                      borderRadius: 8, color: "var(--ink)" }}>
          <b className="tnum">{naoObs.toFixed(0)}%</b> do tempo <b>não foi olhado</b> —
          o minuto está coberto por herança da observação anterior, mas nenhum
          quadro novo foi analisado. Isso <b>não é dúvida do modelo</b>: é economia.
          {naoObsGate > 0 && (
            <>
              {" "}Desses, <b className="tnum">{naoObsGate.toFixed(0)}%</b> vêm da
              supressão do gate (<code className="font-mono">KV_GATE_MAX_REPETICOES</code>)
              {naoObsGate >= 15
                ? " — o teto está agressivo demais, baixe-o."
                : " — dentro do esperado."}
            </>
          )}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }} role="img"
           aria-label="Evolução do percentual de tempo em dúvida">
        {[0, 25, 50].map((g) => (
          <g key={g}>
            <line x1={padL} x2={W - padR} y1={y(g)} y2={y(g)} stroke="var(--line-2)" />
            <text x={padL - 6} y={y(g) + 3} fontSize="8.5" textAnchor="end" fill="var(--faint)" fontFamily="var(--mono)">{g}%</text>
          </g>
        ))}
        {/* faixa 20-30%: acima disso, o dono do processo disse que não há produto */}
        <rect x={padL} y={y(30)} width={plotW} height={Math.max(0, y(20) - y(30))}
              fill="var(--desp)" opacity={0.07} />
        {/* Área do que JÁ foi julgado, sob a linha do total levantado. É o
            trabalho de validação ficando visível — antes ele sumia do gráfico
            justamente por ter sido feito. */}
        <polygon fill={leanCor("va")} opacity={0.18}
                 points={[
                   `${x(0)},${y(0)}`,
                   ...pts.map((d, i) => `${x(i)},${y(d.duvida_resolvida_pct || 0)}`),
                   `${x(n - 1)},${y(0)}`,
                 ].join(" ")} />
        <polyline fill="none" stroke={leanCor("va")} strokeWidth={1.4} strokeLinejoin="round"
                  strokeDasharray="3 3" opacity={0.85}
                  points={pts.map((d, i) => `${x(i)},${y(d.duvida_resolvida_pct || 0)}`).join(" ")} />
        <polyline fill="none" stroke={cor} strokeWidth={2.2} strokeLinejoin="round"
                  points={pts.map((d, i) => `${x(i)},${y(d.duvida_pct)}`).join(" ")} />
        {pts.map((d, i) => (
          <circle key={d.dia} cx={x(i)} cy={y(d.duvida_pct)} r={3} fill="#fff" stroke={cor} strokeWidth={1.6}>
            <title>{`${d.dow} ${d.rot} — ${d.duvida_pct.toFixed(0)}% em dúvida · ${(d.duvida_resolvida_pct || 0).toFixed(0)}% já julgado`}</title>
          </circle>
        ))}
        {pts.map((d, i) => {
          const passo = Math.ceil(n / 6);
          return i % passo === 0 ? (
            <text key={d.dia} x={x(i)} y={H - padB + 14} fontSize="8.5" textAnchor="middle"
                  fill="var(--muted)" fontFamily="var(--mono)">{d.rot}</text>
          ) : null;
        })}
      </svg>
      <div className="row wrap" style={{ gap: 12, fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
        <span className="row" style={{ gap: 5 }}>
          <i style={{ width: 14, height: 2.5, borderRadius: 2, background: cor }} /> dúvida levantada
        </span>
        <span className="row" style={{ gap: 5 }}>
          <i style={{ width: 14, height: 8, borderRadius: 2, background: leanCor("va"), opacity: 0.35 }} /> já julgada
        </span>
      </div>
      <p style={{ fontSize: 12, color: "var(--text)", margin: "6px 0 0" }}>
        {delta == null ? (
          <span style={{ color: "var(--muted)" }}>Poucos dias para dizer se está caindo.</span>
        ) : delta <= -2 ? (
          <><b style={{ color: leanCor("va") }}>▼ Caindo {Math.abs(delta).toFixed(0)} pts</b> — o sistema está aprendendo.</>
        ) : delta >= 2 ? (
          <><b style={{ color: leanCor("desp") }}>▲ Subindo {delta.toFixed(0)} pts</b> — vale olhar o que mudou na operação.</>
        ) : (
          <><b style={{ color: "#c98a00" }}>◆ Estável</b> — se ficar entre 20% e 30%, a leitura ainda não é confiável o bastante.</>
        )}
      </p>
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
  va: leanCor("va"), desp: leanCor("desp"), vazio: "#8a8598",
};
const CAT_NOMES: Record<string, string> = {
  va: "produtivo", desp: "desperdício", vazio: "posto vazio",
};

function fmtMin(m: number): string {
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(Math.round(m % 60)).padStart(2, "0")}`;
}

// Fase 87 — o bloco de 15 min é a MENOR janela sobre a qual a faixa afirma
// alguma coisa. Dentro dele as larguras são proporção, não horário: uma fatia
// vermelha desenhada às 09:07 não diz que o desperdício foi às 09:07, diz que
// aquele tanto do bloco 09:00–09:15 foi desperdício. Por isso o clique abre o
// BLOCO, nunca a fatia — e o painel repete isso em voz alta, porque a fatia
// parece um horário e é a leitura errada mais fácil de fazer.
const BIN_MIN = 15;

function JornadaDoDia({ d, agregado, proc }: { d: DiaAnalise; agregado?: boolean; proc: ProcHeaderMock }) {
  const faixas = d.linha_tempo || [];
  const [binSel, setBinSel] = useState<number | null>(null);
  // Trocar de dia (ou voltar pro agregado) tem de fechar o detalhe: senão o
  // painel fica mostrando os eventos de um dia que não está mais no gráfico.
  const chaveAlvo = agregado ? "@agregado" : d.dia;
  const [chaveAberta, setChaveAberta] = useState(chaveAlvo);
  if (chaveAberta !== chaveAlvo) { setChaveAberta(chaveAlvo); setBinSel(null); }
  if (faixas.length === 0) return null;
  const ini = Math.max(0, faixas[0].ini_m - 15);
  const fim = Math.min(1440, faixas[faixas.length - 1].fim_m + 15);
  const span = Math.max(1, fim - ini);
  const marcas: number[] = [];
  for (let h = Math.ceil(ini / 60); h * 60 <= fim; h++) marcas.push(h * 60);
  // Só os blocos que têm faixa desenhada viram alvo de clique. Clicar num
  // buraco abriria um painel vazio e pareceria bug.
  const binsCobertos = new Set<number>();
  for (const f of faixas) {
    for (let b = Math.floor(f.ini_m / BIN_MIN); b * BIN_MIN < f.fim_m; b++) binsCobertos.add(b);
  }
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo={agregado ? "A jornada típica — todos os dias" : `A jornada de ${d.dow} ${d.rot}`}
        ajuda={agregado
          ? "O dia TÍPICO do operador: em cada faixa de 15 min, a proporção de cada categoria somando todos os dias. Verde = produtivo, vermelho = desperdício, cinza = posto vazio. Buracos em branco = horário sem filmagem em nenhum dia."
          : "O dia inteiro numa faixa só, em blocos de 15 minutos: verde = produtivo, vermelho = desperdício, cinza = posto vazio. Buracos em branco = sem filmagem naquele horário. Clique num bloco para ver os eventos que o compõem — rótulo, descrição e hora."}
        leitura={agregado
          ? "O padrão do posto: onde o dia costuma render, o horário do almoço e as folgas típicas."
          : "O filme do dia: dá pra ver quando começou, o almoço, os buracos e onde o dia rendeu. Clique num bloco de 15 min para abrir o que há dentro dele."}
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
              background: CAT_CORES[f.cat], opacity: 0.92,
              borderRadius: 3,
            }} />
        ))}
        {!agregado && [...binsCobertos].sort((a, b) => a - b).map((b) => {
          const m0 = b * BIN_MIN;
          if (m0 + BIN_MIN <= ini || m0 >= fim) return null;
          const sel = binSel === b;
          return (
            <button key={`b${b}`} type="button"
              title={`${fmtMin(m0)}–${fmtMin(m0 + BIN_MIN)} · ver o que compõe este bloco`}
              aria-label={`Abrir o bloco de ${fmtMin(m0)} a ${fmtMin(m0 + BIN_MIN)}`}
              aria-pressed={sel}
              onClick={() => setBinSel(sel ? null : b)}
              style={{
                position: "absolute", top: 4, bottom: 12, padding: 0,
                left: `${((m0 - ini) / span) * 100}%`,
                width: `${(BIN_MIN / span) * 100}%`,
                cursor: "pointer", background: "transparent",
                border: sel ? "2px solid var(--ink)" : "1px solid transparent",
                borderRadius: 4,
              }} />
          );
        })}
        {marcas.map((m) => (
          <span key={m} style={{ position: "absolute", bottom: 0, left: `${((m - ini) / span) * 100}%`, transform: "translateX(-50%)", fontSize: 9, color: "var(--faint)", fontFamily: "var(--mono)" }}>
            {Math.floor(m / 60)}h
          </span>
        ))}
      </div>
      <div className="row wrap" style={{ gap: 10, fontSize: 11, color: "var(--muted)", marginTop: 10 }}>
        {(["va", "desp", "vazio"] as const).map((c) => (
          <span key={c} className="row" style={{ gap: 5 }}>
            <i style={{ width: 9, height: 9, borderRadius: 3, background: CAT_CORES[c] }} /> {CAT_NOMES[c]}
          </span>
        ))}
        {!agregado && (
          <span style={{ color: "var(--faint)" }}>· clique num bloco de 15 min para abrir</span>
        )}
      </div>
      {!agregado && binSel != null && (
        <DetalheDoBin proc={proc} dia={d.dia} bin={binSel} onFechar={() => setBinSel(null)} />
      )}
    </Card>
  );
}

// ═══ O que compõe um bloco de 15 min ═══
function DetalheDoBin({ proc, dia, bin, onFechar }: {
  proc: ProcHeaderMock; dia: string; bin: number; onFechar: () => void;
}) {
  const minuto = bin * BIN_MIN + BIN_MIN / 2;
  const q = useQuery({
    queryKey: ["jornada-bin", proc.id, dia, bin],
    queryFn: () => api.jornada.bin(proc.id, dia, minuto),
  });
  const b = q.data;
  const [modo, setModo] = useState<"eventos" | "acoes">("eventos");
  return (
    <div className="col" style={{ gap: 10, marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
      <div className="row gap2 wrap" style={{ alignItems: "baseline" }}>
        <span className="font-mono" style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>
          {fmtMin(bin * BIN_MIN)}–{fmtMin(bin * BIN_MIN + BIN_MIN)}
        </span>
        {b && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {b.n_eventos} evento(s)
            {b.truncado ? ` · mostrando ${b.itens.length}` : ""}
          </span>
        )}
        <span className="grow" />
        {b && b.n_eventos > 0 && (
          <div className="row gap1">
            <AbaBin ativo={modo === "eventos"} onClick={() => setModo("eventos")} texto="Por evento" />
            <AbaBin ativo={modo === "acoes"} onClick={() => setModo("acoes")} texto="Por rótulo" />
          </div>
        )}
        <button type="button" onClick={onFechar}
          style={{ border: "1px solid var(--line)", background: "#fff", borderRadius: 99, padding: "3px 11px", fontSize: 11.5, cursor: "pointer", color: "var(--muted)" }}>
          fechar
        </button>
      </div>

      {q.isLoading && <span style={{ fontSize: 12, color: "var(--muted)" }}>Lendo o bloco…</span>}
      {!q.isLoading && !b && (
        // Mostrar o motivo, não só o fato — a tela de rótulos já nos ensinou
        // que "não foi possível carregar" manda o gestor adivinhar.
        <span style={{ fontSize: 12, color: "var(--desp)" }}>
          Não deu para abrir o bloco{q.error ? `: ${String((q.error as Error).message || q.error)}` : "."}
        </span>
      )}

      {b && b.n_eventos === 0 && (
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          Sem eventos neste bloco{b.buraco ? " — é um buraco de filmagem, e por isso a faixa está vazia aqui." : "."}
        </span>
      )}

      {b && b.n_eventos > 0 && (
        <>
          <div className="row gap2 wrap" style={{ fontSize: 11.5 }}>
            {(["va", "desp", "vazio"] as const).map((c) => {
              const p = b.por_categoria[c];
              if (!p) return null;
              return (
                <span key={c} className="row" style={{ gap: 5, color: "var(--muted)" }}>
                  <i style={{ width: 9, height: 9, borderRadius: 3, background: CAT_CORES[c] }} />
                  {CAT_NOMES[c]} <b className="tnum" style={{ color: "var(--ink)" }}>{p.pct.toFixed(0)}%</b>
                </span>
              );
            })}
          </div>

          {modo === "acoes" && (
            <ul className="col" style={{ gap: 6, listStyle: "none", padding: 0, margin: 0 }}>
              {b.acoes.map((a) => (
                <li key={a.rotulo} className="row gap2" style={{ alignItems: "baseline" }}>
                  <i style={{ width: 8, height: 8, borderRadius: 2, background: CAT_CORES[a.cat], flex: "none" }} />
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink)" }}>{nomeHumano(a.rotulo)}</span>
                  <span className="tnum" style={{ fontSize: 11.5, color: "var(--ink)", fontWeight: 700 }}>{a.pct.toFixed(0)}%</span>
                  <span style={{ fontSize: 11, color: "var(--muted)" }}>do bloco · {a.n} trecho(s)</span>
                </li>
              ))}
            </ul>
          )}

          {modo === "eventos" && (
            <ul className="col" style={{ gap: 8, listStyle: "none", padding: 0, margin: 0, maxHeight: 340, overflowY: "auto" }}>
              {b.itens.map((it) => (
                <li key={it.id} className="col" style={{ gap: 3, borderLeft: `3px solid ${CAT_CORES[it.cat]}`, paddingLeft: 9 }}>
                  <div className="row gap2 wrap" style={{ alignItems: "baseline" }}>
                    <span className="font-mono tnum" style={{ fontSize: 11.5, color: "var(--muted)" }}>{it.hora}</span>
                    <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }} title={it.rotulo}>
                      {nomeHumano(it.rotulo)}
                    </span>
                    {/* A duração saiu da tela: com a sequência, todo trecho é o
                        minuto, e "60s" em cada linha era ruído. O evento que
                        atravessa a borda do bloco ainda precisa se anunciar —
                        senão o bloco parece conter um trecho que só encostou
                        nele — mas isso é uma MARCA, não um número. */}
                    {it.parcial && <TagBin texto="atravessa o bloco" cor="var(--faint)" />}
                    {it.corrigido && <TagBin texto="corrigido" cor="var(--va)" />}
                    {it.papel && it.papel !== "operador" && <TagBin texto={it.papel} cor="var(--apoio)" />}
                    {it.em_duvida && <TagBin texto="em dúvida" cor="var(--apoio)" />}
                    {it.versao_instrumento < 3 && <TagBin texto={`instr. v${it.versao_instrumento}`} cor="var(--faint)" />}
                  </div>
                  {it.descricao && (
                    // A descrição é o que o VLM VIU; o rótulo é o que o cluster
                    // DECIDIU. Ver as duas lado a lado é o único jeito de saber
                    // se o rótulo faz sentido — e é o motivo desta tela existir.
                    <span style={{ fontSize: 11.5, color: "var(--text)", lineHeight: 1.45 }}>{it.descricao}</span>
                  )}
                  <span style={{ fontSize: 10.5, color: "var(--faint)" }}>
                    {it.cam_id || "cam?"}{it.origem ? ` · origem ${it.origem}` : ""}
                    {it.confianca != null ? ` · confiança ${(it.confianca * 100).toFixed(0)}%` : ""}
                    {it.n_amostras != null ? ` · ${it.n_amostras} amostra(s)` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <span style={{ fontSize: 10.5, color: "var(--faint)", lineHeight: 1.5 }}>{b.nota}</span>
        </>
      )}
    </div>
  );
}

function AbaBin({ ativo, texto, onClick }: { ativo: boolean; texto: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      style={{
        border: `1px solid ${ativo ? "var(--ink)" : "var(--line)"}`,
        background: ativo ? "var(--ink)" : "#fff", color: ativo ? "#fff" : "var(--muted)",
        borderRadius: 99, padding: "3px 11px", fontSize: 11.5, cursor: "pointer", fontWeight: 600,
      }}>
      {texto}
    </button>
  );
}

function TagBin({ texto, cor }: { texto: string; cor: string }) {
  return (
    <span style={{ fontSize: 10, fontWeight: 700, color: cor, border: `1px solid ${cor}`, borderRadius: 99, padding: "0 6px" }}>
      {texto}
    </span>
  );
}

// ═══ Top ações do dia selecionado (mini-pareto do dia) ═══
function TopAcoesDia({ d, agregado }: { d: DiaAnalise; agregado?: boolean }) {
  const acoes = d.top_acoes || [];
  const maxSeg = Math.max(1, ...acoes.map((a) => a.seg));
  // Fase 53: a tela fala em PROPORÇÃO, nunca em minutos de vídeo. Com a
  // amostragem sistemática, a duração absoluta é a do recorte amostrado — a
  // fatia do tempo é a leitura correta e a única que não engana.
  const base = d.tempo_obs_s || acoes.reduce((t, a) => t + a.seg, 0) || 1;
  const pctDoTempo = (seg: number) => `${Math.round((seg / base) * 100)}%`;
  return (
    <Card style={{ padding: 20, height: "100%" }}>
      <PanelHead
        titulo={agregado ? "No que o tempo foi gasto — todos os dias" : `No que ${d.dow} ${d.rot} foi gasto`}
        ajuda={agregado ? "As ações que mais consumiram tempo somando todos os dias trabalhados do período." : "As 5 ações que mais consumiram o tempo do dia selecionado."}
        leitura="A ação nº 1 deveria ser a que agrega valor — se não for, contou outra história."
      />
      {acoes.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>{agregado ? "Sem ações registradas no período." : "Sem ações registradas neste dia."}</p>
      ) : (
        <ul className="col" style={{ gap: 9, listStyle: "none", padding: 0, margin: 0 }}>
          {acoes.map((a, i) => (
            <li key={a.label} className="col" style={{ gap: 3 }}>
              <div className="row" style={{ justifyContent: "space-between", fontSize: 12 }}>
                <span style={{ background: "var(--line-2)", padding: "1px 7px", borderRadius: 5, fontSize: 11.5, fontWeight: 600 }} title={a.label}>{i + 1}. {nomeHumano(a.label)}</span>
                <span className="tnum" style={{ color: "var(--muted)" }}>{pctDoTempo(a.seg)}</span>
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
                    title={`${d.dow} ${d.rot} ${h}h — ${Math.round(hd.va_pct)}% produtivo`}
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
