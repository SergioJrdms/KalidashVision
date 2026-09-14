import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ProcHeaderMock } from "../lib/adapt";
import type { Go } from "../design/Shell";
import type { ReplayDecision, ReplayExample, ReplayMetrics } from "../lib/types";
import { Badge, Btn, Card, Empty, Eyebrow, Icon } from "../design/ui";
import { nomeHumano } from "../design/rotulos";

const pct = (valor: number | null) => valor == null ? "—" : `${valor.toFixed(2).replace(".", ",")}%`;
const curto = (valor: string) => valor === "local" ? "local" : valor.slice(0, 8);

function MetricCard({ titulo, valor, meta, minimum }: { titulo: string; valor: number | null; meta: string; minimum: number }) {
  const passou = valor != null && valor >= minimum;
  return <Card style={{ padding: 18, flex: "1 1 210px", borderColor: passou ? "#b9dfce" : "var(--line)" }}>
    <div className="row gap2" style={{ justifyContent: "space-between" }}>
      <span style={{ fontSize: 12, fontWeight: 700, color: "var(--muted)" }}>{titulo}</span>
      <Badge tone={passou ? "ok" : "warn"}>{meta}</Badge>
    </div>
    <div className="font-display tnum" style={{ fontSize: 34, fontWeight: 800, color: "var(--ink)", marginTop: 10 }}>{pct(valor)}</div>
  </Card>;
}

function EvolutionCard({ item, current }: { item: ReplayMetrics; current?: boolean }) {
  return <Card style={{ padding: 16, flex: "1 1 240px", border: current ? "1.5px solid var(--accent)" : "1px solid var(--line)", background: current ? "var(--accent-soft)" : "#fff" }}>
    <div className="row gap2" style={{ justifyContent: "space-between" }}>
      <b style={{ fontSize: 13 }}>{item.label}</b>
      {current && <Badge tone="purple">EM PRODUÇÃO</Badge>}
    </div>
    <div style={{ fontSize: 11.5, color: "var(--muted)", margin: "5px 0 13px" }}>{item.n.toLocaleString("pt-BR")} eventos · {item.days} {item.days === 1 ? "dia" : "dias"}</div>
    <div className="col" style={{ gap: 7 }}>
      <Linha label="Precisão improdutiva" value={pct(item.precision_improductive_pct)} />
      <Linha label="Precisão produtiva" value={pct(item.precision_productive_pct)} />
      <Linha label="Coverage" value={pct(item.coverage_pct)} />
    </div>
    {item.note && <p style={{ fontSize: 11.5, lineHeight: 1.45, color: "var(--muted)", margin: "12px 0 0" }}>{item.note}</p>}
  </Card>;
}

function Linha({ label, value }: { label: string; value: string }) {
  return <div className="row" style={{ justifyContent: "space-between", fontSize: 12.5 }}><span style={{ color: "var(--muted)" }}>{label}</span><b className="tnum">{value}</b></div>;
}

function Decisao({ valor }: { valor: ReplayDecision }) {
  const tom = valor === "PRODUTIVO" ? "ok" : valor === "IMPRODUTIVO" ? "warn" : "neutral";
  return <Badge tone={tom}>{valor === "ABSTEM" ? "VALIDAÇÃO" : valor}</Badge>;
}

function EvidenceModal({ item, onClose }: { item: ReplayExample; onClose: () => void }) {
  const q = useQuery({ queryKey: ["replay-frame", item.event_id], queryFn: () => api.eventos.frames(item.event_id), retry: false });
  const frames = q.data?.frames || [];
  return <div style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(15,23,42,.45)", display: "grid", placeItems: "center", padding: 16 }}>
    <Card role="dialog" aria-modal="true" style={{ width: "min(860px, 96vw)", maxHeight: "92vh", overflowY: "auto", padding: 20 }}>
      <div className="row gap2" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div><Eyebrow icon="camera">Evidência original</Eyebrow><h2 className="font-display" style={{ fontSize: 20, margin: "6px 0 3px" }}>{item.group}</h2><div style={{ fontSize: 12, color: "var(--muted)" }}>{item.video_name} · {item.start_s.toFixed(0)}–{item.end_s.toFixed(0)}s</div></div>
        <button onClick={onClose} aria-label="Fechar" className="center" style={{ width: 34, height: 34, borderRadius: 9, border: "1px solid var(--line)", background: "#fff" }}><Icon name="x" size={16} /></button>
      </div>
      <div className="row gap2" style={{ margin: "15px 0 12px", flexWrap: "wrap" }}><span style={{ fontSize: 12, color: "var(--muted)" }}>Humano:</span><Decisao valor={item.truth} /><span style={{ fontSize: 12, color: "var(--muted)" }}>Sistema atual:</span><Decisao valor={item.current} /></div>
      {q.isLoading ? <Empty icon="loader" title="Abrindo os frames reais…" /> : q.isError || !frames.length ? <Empty icon="image-off" title="Evidência visual não está mais disponível no Storage" /> : <div className="row" style={{ gap: 3, background: "#0d0820", padding: 3 }}>{frames.slice(0, 3).map((src: string, i: number) => <img key={i} src={src} alt={`Frame ${i + 1}`} style={{ width: "33.33%", minWidth: 0, objectFit: "contain", maxHeight: 330 }} />)}</div>}
      <p style={{ fontSize: 12.5, color: "var(--text)", margin: "12px 0 0" }}>{item.reason}</p>
    </Card>
  </div>;
}

export default function ReplayPrecisao({ proc, go }: { proc: ProcHeaderMock; go: Go }) {
  const [evidencia, setEvidencia] = useState<ReplayExample | null>(null);
  const replay = useMutation({ mutationFn: () => api.replayProdutividade.executar(proc.id) });
  const dados = replay.data;
  const atual = dados?.results.find((x) => x.id === "current");

  return <div className="col" style={{ gap: 18 }}>
    <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 14 }}>
      <div><Eyebrow icon="badge-check">Ferramenta interna · somente leitura</Eyebrow><h1 className="font-display" style={{ fontSize: 28, margin: "7px 0 5px" }}>Replay de precisão</h1><p style={{ margin: 0, color: "var(--muted)", maxWidth: 720, lineHeight: 1.55 }}>Reaplica as regras R1 e R95 aos eventos reais já avaliados e calcula os percentuais neste momento. Nenhum evento ou indicador da operação é alterado.</p></div>
      <Btn icon={replay.isPending ? "loader" : "play"} size="lg" onClick={() => replay.mutate()} disabled={replay.isPending}>{replay.isPending ? "Executando…" : dados ? "Executar novamente" : "Executar replay completo"}</Btn>
    </div>

    {!dados && !replay.isPending && !replay.isError && <Card style={{ padding: 22 }}>
      <div className="row gap3" style={{ alignItems: "flex-start" }}><Icon name="database" size={22} color="var(--accent)" /><div><b>O que será executado</b><p style={{ color: "var(--muted)", fontSize: 13, lineHeight: 1.55, margin: "5px 0 0" }}>A plataforma lerá o conjunto fixado de agosto, cruzará cada decisão com a avaliação humana e recalculará precisão improdutiva, precisão produtiva e coverage. Os resultados só aparecem depois da execução.</p></div></div>
    </Card>}
    {replay.isPending && <Card style={{ padding: 34 }}><Empty icon="loader" title="Lendo os eventos e reaplicando as regras…" /></Card>}
    {replay.isError && <Card style={{ padding: 24, borderColor: "#efc4c4" }}><Empty icon="alert-triangle" title="Não foi possível executar o replay" /><p style={{ textAlign: "center", fontSize: 12, color: "var(--muted)" }}>{replay.error instanceof Error ? replay.error.message : "Erro inesperado"}</p></Card>}

    {dados && atual && <>
      <Card style={{ padding: 14, background: dados.dataset.matches_frozen_manifest ? "#f1faf6" : "#fff8e8", borderColor: dados.dataset.matches_frozen_manifest ? "#b9dfce" : "#ecd49d" }}>
        <div className="row gap2" style={{ flexWrap: "wrap" }}><Icon name={dados.dataset.matches_frozen_manifest ? "shield-check" : "triangle-alert"} size={18} color={dados.dataset.matches_frozen_manifest ? "#25815b" : "#a46a13"} /><b style={{ fontSize: 13 }}>{dados.dataset.matches_frozen_manifest ? "Conjunto conferido com o manifesto congelado" : "O conjunto mudou desde a validação original"}</b><span style={{ fontSize: 12, color: "var(--muted)" }}>· {dados.dataset.actual_events.toLocaleString("pt-BR")} eventos · {dados.dataset.days.length} dias · executado em {dados.elapsed_ms.toFixed(1).replace(".", ",")} ms</span></div>
      </Card>

      <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
        <MetricCard titulo="Precisão de improdutividade" valor={atual.precision_improductive_pct} meta="≥ 85%" minimum={85} />
        <MetricCard titulo="Precisão de produtividade" valor={atual.precision_productive_pct} meta="≥ 85%" minimum={85} />
        <MetricCard titulo="Coverage" valor={atual.coverage_pct} meta="≥ 65%" minimum={65} />
      </div>

      <div><h2 className="font-display" style={{ fontSize: 19, margin: "2px 0 10px" }}>Evolução medida</h2><div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "stretch" }}>{dados.results.map((item) => <EvolutionCard key={item.id} item={item} current={item.id === "current"} />)}</div></div>

      <Card style={{ padding: 18 }}>
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}><div><h2 className="font-display" style={{ fontSize: 18, margin: 0 }}>Casos que explicam o resultado</h2><p style={{ margin: "4px 0 0", fontSize: 12.5, color: "var(--muted)" }}>Abra um caso para conferir os frames reais usados como evidência.</p></div><Badge tone="info">{dados.examples.length} exemplos auditáveis</Badge></div>
        <div className="col" style={{ gap: 8, marginTop: 14 }}>{dados.examples.map((item) => <button key={item.event_id} onClick={() => setEvidencia(item)} className="row gap2" style={{ width: "100%", textAlign: "left", border: "1px solid var(--line)", background: "#fff", borderRadius: 10, padding: "11px 12px", flexWrap: "wrap" }}><div className="grow" style={{ minWidth: 220 }}><div className="row gap2" style={{ flexWrap: "wrap" }}><b style={{ fontSize: 12.5 }}>{item.group}</b>{item.evidence_available && <Badge tone="info">Frames disponíveis</Badge>}</div><div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 3 }}>{item.day.split("-").reverse().join("/")} · {nomeHumano(item.label)} · {item.start_s.toFixed(0)}–{item.end_s.toFixed(0)}s</div></div><span style={{ fontSize: 11.5, color: "var(--muted)" }}>R1</span><Decisao valor={item.r1} /><Icon name="arrow-right" size={14} color="var(--muted)" /><Decisao valor={item.current} /><Icon name="images" size={16} color={item.evidence_available ? "var(--accent)" : "var(--muted)"} /></button>)}</div>
      </Card>

      <Card style={{ padding: 18 }}><h2 className="font-display" style={{ fontSize: 18, margin: "0 0 11px" }}>De onde vieram os números</h2><div className="col" style={{ gap: 9, fontSize: 12.5, color: "var(--text)", lineHeight: 1.5 }}><div className="row gap2" style={{ alignItems: "flex-start" }}><Badge tone="neutral">1</Badge><span>Uma pessoa confirmou ou corrigiu a atividade observada nos eventos.</span></div><div className="row gap2" style={{ alignItems: "flex-start" }}><Badge tone="neutral">2</Badge><span>O replay aplicou novamente as regras atuais sobre {dados.dataset.actual_events.toLocaleString("pt-BR")} eventos de {dados.dataset.days.length} dias.</span></div><div className="row gap2" style={{ alignItems: "flex-start" }}><Badge tone="neutral">3</Badge><span>O sistema comparou decisão por decisão e contou quantas afirmações produtivas e improdutivas estavam corretas e em quantas conseguiu decidir.</span></div></div><div style={{ borderTop: "1px solid var(--line)", marginTop: 14, paddingTop: 10, fontSize: 11, color: "var(--muted)" }}>Código {curto(dados.code_version)} · manifesto {curto(dados.manifest.sha256)} · conjunto {curto(dados.dataset.event_set_sha256)}</div></Card>

      <Card style={{ padding: 17, background: "var(--soft)" }}><div className="row gap3" style={{ flexWrap: "wrap" }}><Icon name="flask-conical" color="var(--accent)" /><div className="grow"><b style={{ fontSize: 13 }}>Quer demonstrar também o caminho completo do vídeo?</b><p style={{ margin: "3px 0 0", fontSize: 12, color: "var(--muted)" }}>Este replay prova as regras sobre evidências já extraídas. O Teste do pipeline envia um vídeo e executa a cadeia completa.</p></div><Btn variant="secondary" onClick={() => go("processo", proc.id, "teste-pipeline")}>Abrir Teste do pipeline</Btn></div></Card>
    </>}
    {evidencia && <EvidenceModal item={evidencia} onClose={() => setEvidencia(null)} />}
  </div>;
}
