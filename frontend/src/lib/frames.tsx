// ============================================================
// Frames reais do evento — busca /eventos/{id}/frames (3 JPEGs
// base64 com a bbox desenhada) e renderiza no lugar do
// CameraScene ilustrativo. Fallback gracioso ao placeholder
// enquanto carrega ou em vídeos legados (422).
// ============================================================
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { CameraScene } from "../design/ui";

function useEventFrames(id: string, enabled = true) {
  return useQuery({
    queryKey: ["frames", id],
    queryFn: () => api.eventos.frames(id),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

// objectFit "contain": mostra o ROI COMPLETO (sem corte/zoom). O fundo escuro
// preenche a letterbox quando o aspecto do frame difere do card.
const imgBase: React.CSSProperties = { display: "block", width: "100%", objectFit: "contain", background: "#0d0820", borderRadius: "var(--r-md)" };

// Faixa de 3 frames (Validação · Foco Único) — fundo escuro + selo de tempo.
export function FrameStripReal({ ativo, onAspecto }: { ativo: { id: string; pessoa: number; label: string; ini: number; fim: number }; onAspecto?: (e: React.SyntheticEvent<HTMLImageElement>) => void }) {
  const { data } = useEventFrames(ativo.id);
  const raw = data?.frames || [];
  // Se há pelo menos 1 frame real, completa 3 repetindo o último — nunca
  // mistura frame real com o placeholder ilustrativo na faixa.
  const frames = raw.length ? [0, 1, 2].map((i) => raw[i] ?? raw[raw.length - 1]) : [];
  const boxes = [{ id: `P-${String(ativo.pessoa).padStart(2, "0")}`, x: 30, y: 24, w: 24, h: 52, act: ativo.label.split(" ").slice(0, 2).join(" ") }];
  return (
    <div className="row" style={{ gap: 2, padding: 2, background: "#0d0820" }}>
      {[0, 1, 2].map((i) => (
        <div key={i} style={{ flex: 1, position: "relative" }}>
          {frames[i] ? (
            <img src={frames[i]} alt="" onLoad={i === 0 ? onAspecto : undefined} style={{ ...imgBase, height: 180 }} />
          ) : (
            <CameraScene height={180} hud={i === 1} boxes={boxes.map((b) => ({ ...b, x: b.x + i * 4 }))} />
          )}
          <span style={{ position: "absolute", bottom: 6, left: 6, fontSize: 9.5, fontFamily: "var(--mono)", color: "rgba(255,255,255,.7)", background: "rgba(0,0,0,.5)", padding: "1px 6px", borderRadius: 5 }}>
            {(ativo.ini + (i * (ativo.fim - ativo.ini)) / 2).toFixed(1)}s
          </span>
        </div>
      ))}
    </div>
  );
}

// Faixa de 3 frames do 2º ÂNGULO (segmento da cam2) por janela de tempo —
// validação dual-câmera (Fase 6). Sem bbox (a cam2 não é rastreada).
export function FrameStripSegmento({ segmentoId, ini, fim, onAspecto }: { segmentoId: string; ini: number; fim: number; onAspecto?: (e: React.SyntheticEvent<HTMLImageElement>) => void }) {
  const { data } = useQuery({
    queryKey: ["frames-seg", segmentoId, Math.round(ini), Math.round(fim)],
    queryFn: () => api.segmentos.frames(segmentoId, ini, fim),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const raw = data?.frames || [];
  const frames = raw.length ? [0, 1, 2].map((i) => raw[i] ?? raw[raw.length - 1]) : [];
  return (
    <div className="row" style={{ gap: 2, padding: 2, background: "#0d0820" }}>
      {[0, 1, 2].map((i) => (
        <div key={i} style={{ flex: 1, position: "relative" }}>
          {frames[i] ? (
            <img src={frames[i]} alt="" onLoad={i === 0 ? onAspecto : undefined} style={{ ...imgBase, height: 180 }} />
          ) : (
            <CameraScene height={180} hud={i === 1} boxes={[]} />
          )}
          <span style={{ position: "absolute", bottom: 6, left: 6, fontSize: 9.5, fontFamily: "var(--mono)", color: "rgba(255,255,255,.7)", background: "rgba(0,0,0,.5)", padding: "1px 6px", borderRadius: 5 }}>
            {(ini + (i * (fim - ini)) / 2).toFixed(1)}s
          </span>
        </div>
      ))}
    </div>
  );
}

// Frame único (cabeçalho de card / linha expandida). `enabled` permite carga lazy.
export function FrameReal({ id, pessoa, height, enabled = true, pos = 0 }: { id: string; pessoa: number; height: number; enabled?: boolean; pos?: number }) {
  const { data } = useEventFrames(id, enabled);
  // pos: qual dos 3 frames do evento (0=início, 1=meio, 2=fim). Default início.
  const frame = data?.frames?.[pos] ?? data?.frames?.[0];
  if (frame) return <img src={frame} alt="" style={{ ...imgBase, height }} />;
  return <CameraScene height={height} hud={false} boxes={[{ id: `P-${String(pessoa).padStart(2, "0")}`, x: 34, y: 26, w: 26, h: 50, act: "" }]} />;
}

// ════════════════════════════════════════════════════════════════════
// Fase 78 — DEFASAGEM ENTRE AS CÂMERAS.
//
// O watchdog do edge relança as câmeras separadamente: nos nomes aparece
// seg_..._070056 na cam1 contra seg_..._070106 na cam2. Medido no banco:
// 108 pares alinhados (0-1s), 24 com 10-12s, 13 com 54-67s e 2 acima de
// 135s. Nos últimos, dizer "mesma ação" é FALSO — são momentos distintos,
// e afirmar simultaneidade que não existe faz julgar duas cenas diferentes
// como se fossem uma.
//
// `offset_s` = t0(cam1) − t0(cam2). O instante `t` da cam1 corresponde a
// `t + offset` na cam2: cam2 que começou 57s DEPOIS tem offset −57, e o
// instante 180s da cam1 está em 123s dela.
//
// O RESIDUAL é o que sobra depois de compensar: quando a janela pedida cai
// FORA do segmento da cam2, ela é clampada e o frame mostrado não é o
// instante pedido. Antes esse clamp era silencioso (`Math.max(0, …)`) — é
// exatamente aí que a tela mentia.
export const RESIDUAL_MAX_S = 5;

export function janelaCam2(ini: number, fim: number, offsetS: number, durSec?: number) {
  const iniAlvo = ini + offsetS;
  const fimAlvo = fim + offsetS;
  // Quanto precisou ser empurrado para caber no segmento da cam2.
  let residual = 0;
  if (iniAlvo < 0) residual = -iniAlvo;
  else if (durSec && fimAlvo > durSec) residual = fimAlvo - durSec;
  return {
    ini: Math.max(0, iniAlvo),
    fim: Math.max(0, fimAlvo),
    residual,
    // Só afirma simultaneidade quando ela existe de fato.
    sincronizado: residual <= RESIDUAL_MAX_S,
  };
}

/** Rótulo honesto do 2º ângulo: "mesmo instante" só quando é verdade. */
export function RotuloSegundoAngulo({
  camId, offsetS, residual, sincronizado,
}: { camId: string | null; offsetS: number; residual: number; sincronizado: boolean }) {
  const nome = (camId || "cam2").replace(/^cam/i, "Cam ");
  const defas = Math.abs(Math.round(offsetS));
  return (
    <span className="row gap1 wrap" style={{ alignItems: "baseline" }}>
      <span style={{ fontSize: 10.5, fontWeight: 700, color: "rgba(255,255,255,.72)", fontFamily: "var(--mono)" }}>
        {nome}
      </span>
      {sincronizado ? (
        <span style={{ fontSize: 10.5, color: "rgba(255,255,255,.55)" }}>
          · mesmo instante{defas > 1 ? ` (compensados ${defas}s)` : ""}
        </span>
      ) : (
        <span title="A janela pedida cai fora deste segmento — o frame mostrado NÃO é o mesmo instante."
              style={{ fontSize: 10.5, fontWeight: 700, color: "#F8B4B6" }}>
          · ⚠ momento DIFERENTE — {Math.round(residual)}s fora de sincronia
        </span>
      )}
    </span>
  );
}


// ── Fase 78 · Grade que respeita proporções diferentes ──────────────
// CAM1_ROI é 0.300x0.200 (deitado) e CAM2_ROI é 0.190x0.360 (em pé). Numa
// grade 1fr/1fr, renderizados na mesma altura, a cam1 fica ~3x mais larga e
// a cam2 vira uma tira espremida entre margens vazias. Os ROIs estão certos
// — quem precisa se adaptar é a grade.
//
// A proporção é MEDIDA do frame carregado (naturalWidth/naturalHeight), não
// suposta: o ROI pode mudar sem que ninguém lembre de atualizar constante
// nenhuma aqui.
export function useAspecto(): [number | null, (e: React.SyntheticEvent<HTMLImageElement>) => void] {
  const [a, setA] = useState<number | null>(null);
  const medir = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.naturalWidth && img.naturalHeight) {
      setA((prev) => prev ?? img.naturalWidth / img.naturalHeight);
    }
  };
  return [a, medir];
}

/** Colunas proporcionais aos aspectos medidos. Enquanto não souber, 1fr 1fr. */
export function colunasPorAspecto(a1: number | null, a2: number | null): string {
  if (!a1 || !a2) return "1fr 1fr";
  // Limita a desproporção: mesmo com ROIs muito diferentes, o painel menor
  // precisa continuar legível para julgar a cena.
  const r = Math.min(3, Math.max(1 / 3, a1 / a2));
  return `${r}fr 1fr`;
}
