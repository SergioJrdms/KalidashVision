// ============================================================
// Zonas por câmera (Fase 28) — editor visual de polígonos sobre um
// frame real da câmera. As zonas dizem ao Prism QUEM analisar:
//  · posto_operador → onde o operador titular trabalha (só ele é analisado)
//  · maquina        → área da máquina (contexto visual, não classifica pessoa)
//  · interacao      → quem entra aqui é analisado como interação com o posto
// Pessoa fora de todas as zonas é IGNORADA pela análise (transeuntes).
// Coordenadas normalizadas [0-1] no espaço do vídeo enviado pelo Pi.
// ============================================================
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Btn, Card, Icon, Empty, PanelHead, Badge, Modal, toast } from "../design/ui";
import type { ProcHeaderMock } from "../lib/adapt";
import type { PapelZona, ZonaCamera, ZonaBody, FrenteMaquina } from "../lib/types";

const PAPEIS: { valor: PapelZona; rotulo: string; cor: string; desc: string }[] = [
  {
    valor: "posto_operador",
    rotulo: "Posto do operador",
    cor: "var(--accent)",
    desc: "Onde o operador titular fica trabalhando. Só quem está aqui é analisado como operador — máx. 1 por câmera.",
  },
  {
    valor: "maquina",
    rotulo: "Máquina",
    cor: "#6f6b80",
    desc: "A área da máquina (torno). Serve de contexto visual — não classifica pessoas.",
  },
  {
    valor: "interacao",
    rotulo: "Interação",
    cor: "#c98a00",
    desc: "Área onde terceiros interagem com o posto (ex.: alguém vem conversar). Quem entra aqui vira evento de interação.",
  },
];

const corPapel = (p: PapelZona) => PAPEIS.find((x) => x.valor === p)?.cor || "var(--accent)";
const rotuloPapel = (p: PapelZona) => PAPEIS.find((x) => x.valor === p)?.rotulo || p;

type Pt = [number, number];

export function ZonasBloco({ proc }: { proc: ProcHeaderMock }) {
  const qc = useQueryClient();
  const camsQ = useQuery({ queryKey: ["cameras", proc.id], queryFn: () => api.cameras.listar(proc.id) });
  const cams = camsQ.data?.cameras || [];
  const [cam, setCam] = useState<string | null>(null);
  const camAtiva = cam ?? cams[0] ?? null;

  return (
    <Card style={{ padding: 22 }}>
      <PanelHead
        titulo="Zonas da análise (quem o Prism observa)"
        ajuda="Desenhe sobre a imagem real da câmera: o posto do operador (só ele é analisado), a área da máquina e a área de interação. Pessoas fora das zonas — quem só passa na frente — são ignoradas pela análise e não gastam processamento."
        leitura="Sem zonas desenhadas, a análise segue como hoje (todas as pessoas). Ao desenhar o posto do operador, o Prism passa a analisar SÓ o titular do posto."
      />
      {camsQ.isLoading ? (
        <Empty icon="loader" title="Carregando câmeras…" />
      ) : cams.length === 0 ? (
        <Empty icon="video-off" title="Nenhuma câmera ainda" desc="Suba ao menos 1 segmento pelo Pi para desenhar zonas sobre um frame real." />
      ) : (
        <>
          <div className="row gap1 wrap" style={{ marginTop: 14, marginBottom: 14 }}>
            {cams.map((c) => (
              <button
                key={c}
                onClick={() => setCam(c)}
                style={{
                  padding: "6px 14px", borderRadius: 99, fontSize: 12.5, fontWeight: 600,
                  border: "1px solid", cursor: "pointer",
                  borderColor: camAtiva === c ? "var(--accent)" : "var(--line)",
                  background: camAtiva === c ? "var(--accent)" : "#fff",
                  color: camAtiva === c ? "#fff" : "var(--muted)",
                }}
              >
                <Icon name="video" size={12} /> {c.replace(/^cam/i, "Câmera ")}
              </button>
            ))}
          </div>
          {camAtiva && <ZonasDaCamera key={camAtiva} proc={proc} camId={camAtiva} onMudou={() => qc.invalidateQueries({ queryKey: ["zonas", proc.id] })} />}
        </>
      )}
    </Card>
  );
}

function ZonasDaCamera({ proc, camId, onMudou }: { proc: ProcHeaderMock; camId: string; onMudou: () => void }) {
  const qc = useQueryClient();
  const frameQ = useQuery({
    queryKey: ["frame-ref", proc.id, camId],
    queryFn: () => api.cameras.frameReferencia(proc.id, camId),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  const zonasQ = useQuery({ queryKey: ["zonas", proc.id, camId], queryFn: () => api.zonas.listar(proc.id, camId) });
  const zonas = useMemo(() => zonasQ.data || [], [zonasQ.data]);

  // Estado de edição: pontos do polígono em desenho + zona sendo editada.
  const [pts, setPts] = useState<Pt[]>([]);
  const [editando, setEditando] = useState<ZonaCamera | null>(null);
  const [nome, setNome] = useState("");
  const [papel, setPapel] = useState<PapelZona>("posto_operador");
  // Fase 86: orientação da máquina em relação à câmera. Sem isto o sistema
  // afirma só "de costas para a CÂMERA" (que é objetivo) e proíbe o VLM de
  // falar em relação ao torno — é refinamento, não pré-requisito.
  const [frenteMaquina, setFrenteMaquina] = useState<FrenteMaquina | null>(null);
  const [descricao, setDescricao] = useState("");
  const [excluir, setExcluir] = useState<ZonaCamera | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  function invalidar() {
    qc.invalidateQueries({ queryKey: ["zonas", proc.id, camId] });
    onMudou();
  }
  function limparForm() {
    setPts([]); setEditando(null); setNome(""); setPapel("posto_operador"); setDescricao("");
  }

  const salvarMut = useMutation({
    mutationFn: (body: ZonaBody) =>
      editando ? api.zonas.atualizar(editando.id, body) : api.zonas.criar(proc.id, body),
    onSuccess: () => { toast(editando ? "Zona atualizada." : "Zona criada. O Prism passa a usá-la nos próximos vídeos.", { icon: "check" }); limparForm(); invalidar(); },
    onError: (e: Error) => toast(e.message || "Falha ao salvar a zona.", { icon: "x", color: "var(--desp)" }),
  });
  const excluirMut = useMutation({
    mutationFn: (id: string) => api.zonas.excluir(id),
    onSuccess: () => { toast("Zona excluída.", { icon: "check" }); setExcluir(null); invalidar(); },
  });

  // Esc desfaz o último ponto do desenho.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setPts((p) => p.slice(0, -1));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function cliqueCanvas(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
    // Fechar clicando perto do 1º vértice (raio ~2% da largura).
    if (pts.length >= 3) {
      const [x0, y0] = pts[0];
      if (Math.hypot(x - x0, y - y0) < 0.02) return; // usa o botão Salvar
    }
    setPts((p) => [...p, [Number(x.toFixed(4)), Number(y.toFixed(4))]]);
  }

  function editarZona(z: ZonaCamera) {
    setEditando(z);
    setPts(z.pts_rel.map(([x, y]) => [x, y] as Pt));
    setNome(z.nome);
    setPapel(z.papel);
    setFrenteMaquina(z.frente_maquina ?? null);
    setDescricao(z.descricao_contexto || "");
  }

  function salvar() {
    if (pts.length < 3 || !nome.trim()) return;
    const img = imgRef.current;
    salvarMut.mutate({
      cam_id: camId,
      nome: nome.trim(),
      papel,
      frente_maquina: papel === "maquina" ? frenteMaquina : null,
      pts_rel: pts,
      descricao_contexto: descricao.trim() || null,
      frame_ref_w: img?.naturalWidth || frameQ.data?.largura || null,
      frame_ref_h: img?.naturalHeight || frameQ.data?.altura || null,
      ativo: true,
    });
  }

  if (frameQ.isLoading) return <Empty icon="loader" title="Carregando frame da câmera…" />;
  if (frameQ.isError || !frameQ.data) {
    return <Empty icon="image-off" title="Sem frame de referência" desc={(frameQ.error as Error)?.message || "Esta câmera ainda não tem vídeo processado."} />;
  }

  const toSvg = (p: Pt) => `${p[0] * 1000},${p[1] * 1000}`;

  return (
    <div className="col" style={{ gap: 14 }}>
      {/* Canvas: frame real + SVG overlay (coords 0-1000 = pts_rel*1000) */}
      <div
        onClick={cliqueCanvas}
        style={{ position: "relative", borderRadius: 12, overflow: "hidden", border: "1px solid var(--line)", cursor: "crosshair", lineHeight: 0 }}
        title="Clique para adicionar vértices do polígono. Esc desfaz o último."
      >
        <img ref={imgRef} src={frameQ.data.img} alt={`Frame ${camId}`} style={{ width: "100%", display: "block" }} />
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
          {zonas.filter((z) => !editando || z.id !== editando.id).map((z) => (
            <g key={z.id} opacity={z.ativo ? 1 : 0.35}>
              <polygon points={z.pts_rel.map((p) => toSvg(p as Pt)).join(" ")} fill={corPapel(z.papel)} fillOpacity={0.22} stroke={corPapel(z.papel)} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
              <text x={z.pts_rel[0][0] * 1000} y={Math.max(24, z.pts_rel[0][1] * 1000 - 8)} fontSize={26} fontWeight={700} fill={corPapel(z.papel)} stroke="#fff" strokeWidth={4} paintOrder="stroke">
                {z.nome}
              </text>
            </g>
          ))}
          {pts.length > 0 && (
            <g>
              <polygon points={pts.map(toSvg).join(" ")} fill={corPapel(papel)} fillOpacity={0.25} stroke={corPapel(papel)} strokeWidth={3} strokeDasharray="8 5" vectorEffect="non-scaling-stroke" />
              {pts.map((p, i) => (
                <circle key={i} cx={p[0] * 1000} cy={p[1] * 1000} r={7} fill="#fff" stroke={corPapel(papel)} strokeWidth={3} vectorEffect="non-scaling-stroke" />
              ))}
            </g>
          )}
        </svg>
      </div>
      <div className="row gap2 wrap" style={{ fontSize: 11.5, color: "var(--muted)", alignItems: "center" }}>
        <span><b>{pts.length}</b> vértice(s) — mínimo 3.</span>
        <span>Clique adiciona · Esc desfaz o último.</span>
        {pts.length > 0 && <Btn variant="ghost" size="sm" icon="rotate-ccw" onClick={() => setPts([])}>Recomeçar desenho</Btn>}
        {frameQ.data.video_nome && <span className="grow" style={{ textAlign: "right" }}>frame de <code className="font-mono">{frameQ.data.video_nome}</code></span>}
      </div>

      {/* Formulário da zona em desenho/edição */}
      <div className="col" style={{ gap: 10, padding: 14, borderRadius: 12, background: "var(--soft)", border: "1px solid var(--line-2)" }}>
        <div className="row gap2 wrap">
          <div className="col" style={{ gap: 4, flex: "1 1 200px" }}>
            <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--muted)" }}>Nome da zona</span>
            <input className="field" value={nome} onChange={(e) => setNome(e.target.value)} placeholder='ex.: "posto do torneiro"' />
          </div>
          <div className="col" style={{ gap: 4, flex: "2 1 300px" }}>
            <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--muted)" }}>Tipo</span>
            <div className="row gap1 wrap">
              {PAPEIS.map((p) => (
                <button key={p.valor} onClick={() => setPapel(p.valor)} title={p.desc}
                  style={{ padding: "6px 11px", borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: "pointer", background: "#fff", color: "var(--text)", border: papel === p.valor ? `2px solid ${p.cor}` : "1px solid var(--line)" }}>
                  <i style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2, background: p.cor, marginRight: 6 }} />{p.rotulo}
                </button>
              ))}
            </div>
            <span style={{ fontSize: 11, color: "var(--faint)" }}>{PAPEIS.find((p) => p.valor === papel)?.desc}</span>
            {papel === "maquina" && (
              <div className="col" style={{ gap: 5, marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--line)" }}>
                <span style={{ fontSize: 11.5, fontWeight: 700, color: "var(--text)" }}>
                  Onde está a máquina em relação a esta câmera?
                </span>
                <span style={{ fontSize: 11, color: "var(--faint)", lineHeight: 1.5 }}>
                  A pose diz se o operador está de frente ou de costas para a CÂMERA — isso é medido.
                  Traduzir isso em “de frente para o torno” depende de onde o torno está, e como
                  câmera e torno são fixos, é uma constante. Sem preencher, o sistema não afirma
                  orientação em relação ao torno (e é assim que ele para de inventar “de frente ao torno”).
                </span>
                <div className="row gap1 wrap">
                  {([
                    { v: null, t: "não sei / não configurar" },
                    { v: "camera" as const, t: "de frente para a câmera = de frente para o torno" },
                    { v: "oposta" as const, t: "de costas para a câmera = de frente para o torno" },
                    { v: "perfil" as const, t: "torno de lado (não dá para inferir)" },
                  ]).map((o) => (
                    <button key={String(o.v)} type="button" onClick={() => setFrenteMaquina(o.v)}
                      style={{ padding: "5px 10px", borderRadius: 8, fontSize: 11.5, cursor: "pointer",
                               background: "#fff", color: "var(--text)",
                               fontWeight: frenteMaquina === o.v ? 700 : 500,
                               border: frenteMaquina === o.v ? "2px solid var(--accent)" : "1px solid var(--line)" }}>
                      {o.t}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="col" style={{ gap: 4 }}>
          <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--muted)" }}>Contexto para a IA (opcional)</span>
          <input className="field" value={descricao} onChange={(e) => setDescricao(e.target.value)} placeholder='ex.: "área atrás do torno onde o torneiro opera e monitora a máquina"' />
        </div>
        <div className="row gap1">
          <Btn variant="primary" size="sm" icon="check" disabled={pts.length < 3 || !nome.trim() || salvarMut.isPending} onClick={salvar}>
            {editando ? "Salvar alterações" : "Salvar zona"}
          </Btn>
          {(editando || pts.length > 0) && <Btn variant="ghost" size="sm" onClick={limparForm}>Cancelar</Btn>}
        </div>
      </div>

      {/* Lista de zonas da câmera */}
      {zonas.length > 0 && (
        <div className="col" style={{ gap: 8 }}>
          {zonas.map((z) => (
            <div key={z.id} className="row gap2" style={{ alignItems: "center", padding: "10px 12px", borderRadius: 10, border: "1px solid var(--line-2)", background: "#fff" }}>
              <i style={{ width: 12, height: 12, borderRadius: 3, background: corPapel(z.papel), flex: "none" }} />
              <div className="col grow" style={{ gap: 1, minWidth: 160 }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>{z.nome}</span>
                <span style={{ fontSize: 11.5, color: "var(--muted)" }}>{rotuloPapel(z.papel)} · {z.pts_rel.length} vértices{z.descricao_contexto ? ` · ${z.descricao_contexto}` : ""}</span>
              </div>
              {!z.ativo && <Badge tone="neutral">inativa</Badge>}
              <Btn variant="ghost" size="sm" icon="pencil" onClick={() => editarZona(z)}>Editar</Btn>
              <Btn variant="ghost" size="sm" icon="trash-2" onClick={() => setExcluir(z)}>Excluir</Btn>
            </div>
          ))}
        </div>
      )}

      {excluir && (
        <Modal onClose={() => setExcluir(null)} width={420}>
          <h3 className="font-display" style={{ fontSize: 17, fontWeight: 700, marginBottom: 10 }}>Excluir a zona “{excluir.nome}”?</h3>
          <p style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, marginBottom: 16 }}>
            {excluir.papel === "posto_operador"
              ? "Sem a zona do posto, a análise volta a considerar TODAS as pessoas da cena (comportamento antigo)."
              : "A zona deixa de ser usada nos próximos vídeos. Eventos antigos não mudam."}
          </p>
          <div className="row gap2" style={{ justifyContent: "flex-end" }}>
            <Btn variant="ghost" size="sm" onClick={() => setExcluir(null)}>Cancelar</Btn>
            <Btn variant="danger" size="sm" icon="trash-2" onClick={() => excluirMut.mutate(excluir.id)}>Excluir zona</Btn>
          </div>
        </Modal>
      )}
    </div>
  );
}
