// ============================================================
// Frames reais do evento — busca /eventos/{id}/frames (3 JPEGs
// base64 com a bbox desenhada) e renderiza no lugar do
// CameraScene ilustrativo. Fallback gracioso ao placeholder
// enquanto carrega ou em vídeos legados (422).
// ============================================================
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
export function FrameStripReal({ ativo }: { ativo: { id: string; pessoa: number; label: string; ini: number; fim: number } }) {
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
            <img src={frames[i]} alt="" style={{ ...imgBase, height: 180 }} />
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
export function FrameStripSegmento({ segmentoId, ini, fim }: { segmentoId: string; ini: number; fim: number }) {
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
            <img src={frames[i]} alt="" style={{ ...imgBase, height: 180 }} />
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
export function FrameReal({ id, pessoa, height, enabled = true }: { id: string; pessoa: number; height: number; enabled?: boolean }) {
  const { data } = useEventFrames(id, enabled);
  const frame = data?.frames?.[0];
  if (frame) return <img src={frame} alt="" style={{ ...imgBase, height }} />;
  return <CameraScene height={height} hud={false} boxes={[{ id: `P-${String(pessoa).padStart(2, "0")}`, x: 34, y: 26, w: 26, h: 50, act: "" }]} />;
}
