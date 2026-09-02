import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { FrameStripReal, FrameStripSegmento, janelaCam2, RotuloSegundoAngulo } from "../lib/frames";
import { Card, Empty, Icon } from "../design/ui";
import { nomeHumano } from "../design/rotulos";

type Props = { processoId: string; labels?: string[]; titulo: string; categoria?: string; janelaPresenca?: number; onClose: () => void };

function duracao(s: number) { return s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`; }

/** Painel reutilizável: lista leve primeiro; os frames só são requisitados ao abrir um evento. */
export function EventEvidenceDrawer({ processoId, labels, titulo, categoria, janelaPresenca, onClose }: Props) {
  const [aberto, setAberto] = useState<string | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["evidencias", processoId, labels, janelaPresenca],
    initialPageParam: 1,
    queryFn: ({ pageParam }) => janelaPresenca
      ? api.eventos.evidenciasPresenca(processoId, janelaPresenca, pageParam)
      : api.eventos.evidencias(processoId, labels || [], pageParam),
    getNextPageParam: (last) => last.page * last.page_size < last.total ? last.page + 1 : undefined,
    staleTime: 60_000,
  });
  const dados = q.data?.pages;
  const itens = dados?.flatMap((p) => p.itens) || [];
  const total = dados?.[0]?.total || 0;
  const leituras = !!janelaPresenca;
  return <div role="dialog" aria-modal="true" className="col" style={{ position: "fixed", zIndex: 70, right: 0, top: 0, width: "min(560px,100vw)", height: "100vh", overflowY: "auto", padding: 18, background: "var(--card,#fff)", boxShadow: "-8px 0 28px rgba(0,0,0,.18)" }}>
    <div className="row gap2" style={{ alignItems: "flex-start" }}><div className="grow"><span style={{ fontSize: 11, color: "var(--muted)" }}>{categoria || "Evidência da análise"}</span><h2 className="font-display" style={{ margin: "2px 0", fontSize: 20 }}>{nomeHumano(titulo)}</h2><p style={{ margin: 0, fontSize: 12.5, color: "var(--muted)" }}>{leituras ? "Estas leituras compõem este indicador." : "Estes eventos formam este número."}</p></div><button onClick={onClose} aria-label="Fechar" className="center" style={{ border: "1px solid var(--line)", background: "#fff", borderRadius: 8, width: 32, height: 32 }}><Icon name="x" size={16} /></button></div>
    {q.isLoading ? <Empty icon="loader" title="Buscando evidências…" /> : q.isError ? <Empty icon="alert-triangle" title="Não foi possível carregar a evidência" /> : !total ? <Empty icon="search-x" title="Nenhuma leitura constituinte disponível" /> : <><p style={{ fontSize: 12.5, color: "var(--muted)", margin: "16px 0 8px" }}>{itens.length} de {total} {leituras ? "leituras" : "eventos"} · maiores impactos primeiro</p><div className="col" style={{ gap: 9 }}>{itens.map((e, i) => <Card key={`${e.id}-${e.tempo_inicio_s}-${i}`} style={{ padding: 12 }}><button onClick={() => setAberto(aberto === `${e.id}-${i}` ? null : `${e.id}-${i}`)} className="row gap2" style={{ width: "100%", textAlign: "left", border: 0, background: "transparent", padding: 0, cursor: "pointer" }}><div className="grow"><b style={{ fontSize: 13 }}>{leituras ? "Posto sem operador" : nomeHumano(e.label_efetivo)}</b><div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 3 }}>{e.video_nome} · {(e.cam_id || "Câmera").replace(/^cam/i, "Cam ")} · {e.tempo_inicio_s.toFixed(0)}–{e.tempo_fim_s.toFixed(0)}s · {duracao(e.duracao_s)}</div></div><Icon name={aberto === `${e.id}-${i}` ? "chevron-up" : "chevron-down"} size={15} /></button>{aberto === `${e.id}-${i}` && <div style={{ marginTop: 10 }}><p style={{ fontSize: 12.5, margin: "0 0 8px", color: "var(--text)" }}>{e.descricao_bruta || "Sem descrição adicional."}</p><EvidenceFrames id={e.id} pessoa={e.pessoa_track_id} label={e.label_efetivo} ini={e.tempo_inicio_s} fim={e.tempo_fim_s} segundoAngulo={e.segundo_angulo} /></div>}</Card>)}</div>{q.hasNextPage && <button disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()} style={{ marginTop: 12, border: "1px solid var(--line)", background: "#fff", borderRadius: 99, padding: "7px 14px", color: "var(--accent)", fontWeight: 700 }}>{q.isFetchingNextPage ? "Carregando…" : leituras ? "Ver mais leituras" : "Ver mais eventos"}</button>}</>}
  </div>;
}

function EvidenceFrames({ id, pessoa, label, ini, fim, segundoAngulo }: { id: string; pessoa: number; label: string; ini: number; fim: number; segundoAngulo?: { segmento_id: string; cam_id: string | null; offset_s?: number } | null }) {
  const cam1 = useQuery({ queryKey: ["frames", id], queryFn: () => api.eventos.frames(id), retry: false, staleTime: 5 * 60_000 });
  const j = segundoAngulo ? janelaCam2(ini, fim, segundoAngulo.offset_s || 0) : null;
  const cam2 = useQuery({ queryKey: ["frames-seg", segundoAngulo?.segmento_id, Math.round(j?.ini || 0), Math.round(j?.fim || 0)], queryFn: () => api.segmentos.frames(segundoAngulo!.segmento_id, j!.ini, j!.fim), enabled: !!segundoAngulo && !!j, retry: false, staleTime: 5 * 60_000 });
  const cam1Ok = !!cam1.data?.frames?.length;
  const cam2Ok = !!cam2.data?.frames?.length;
  const ambosIndisponiveis = !cam1.isLoading && !cam1Ok && (!segundoAngulo || (!cam2.isLoading && !cam2Ok));
  if (ambosIndisponiveis) return <span style={{ fontSize: 12, color: "var(--muted)" }}>Evidência visual indisponível</span>;
  return <div className="col" style={{ gap: 8 }}><span style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)" }}>Evidência</span>{cam1.isLoading ? <span style={{ fontSize: 12, color: "var(--muted)" }}>Carregando evidência CAM1…</span> : cam1Ok ? <FrameStripReal ativo={{ id, pessoa, label, ini, fim }} /> : <span style={{ fontSize: 12, color: "var(--muted)" }}>Evidência CAM1 indisponível</span>}{segundoAngulo && j && <div className="col" style={{ gap: 4 }}><RotuloSegundoAngulo camId={segundoAngulo.cam_id} offsetS={segundoAngulo.offset_s || 0} residual={j.residual} sincronizado={j.sincronizado} />{cam2.isLoading ? <span style={{ fontSize: 12, color: "var(--muted)" }}>Carregando 2º ângulo…</span> : cam2Ok ? <FrameStripSegmento segmentoId={segundoAngulo.segmento_id} ini={j.ini} fim={j.fim} /> : <span style={{ fontSize: 12, color: "var(--muted)" }}>2º ângulo indisponível</span>}</div>}</div>;
}
