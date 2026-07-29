// ============================================================
// Processos — porte fiel de processos.jsx, com dados reais.
// ============================================================
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { mapProcessos, mapInsights, mapPadroesGlobais, type ProcMock } from "../lib/adapt";
import { nivelDe } from "../design/helpers";
import { Btn, Card, Icon, Prism, Help, Badge, PrioBadge, MaturityMeter, LeanBar, Modal, Empty, toast } from "../design/ui";
import type { Go } from "../design/Shell";

// Fase 25: a "visão geral do Prism" (insights + padrões de portfólio) está
// DESATIVADA ("em breve") p/ cortar tokens. Trocar p/ false reativa (e
// KV_INSIGHTS_GLOBAIS_ENABLE=on no backend). Nada foi removido.
const VISAO_GLOBAL_EM_BREVE = true;

export default function Processos({ go }: { go: Go }) {
  const [novo, setNovo] = useState(false);
  const procs = useQuery({ queryKey: ["processos"], queryFn: () => api.processos.list() });
  // Desativadas quando em "em breve" — não disparam request nem consomem token.
  const insights = useQuery({ queryKey: ["insights-globais"], queryFn: () => api.insightsGlobais(), enabled: !VISAO_GLOBAL_EM_BREVE });
  const padroes = useQuery({ queryKey: ["padroes-globais"], queryFn: () => api.padroes.globais(), enabled: !VISAO_GLOBAL_EM_BREVE });

  const PROCESSOS = mapProcessos(procs.data || []);
  const INSIGHTS = mapInsights(insights.data || []);
  const PADROES_GLOBAIS = mapPadroesGlobais(padroes.data || []);

  return (
    <div className="col" style={{ gap: 26, maxWidth: 1180, margin: "0 auto" }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
        <div>
          <h1 className="font-display" style={{ fontSize: 28, fontWeight: 700 }}>Sua operação</h1>
          <p className="pretty" style={{ fontSize: 14.5, color: "var(--muted)", marginTop: 6, maxWidth: 560 }}>
            Cada processo é um contexto isolado de análise. O Prism aprende cada um separadamente — e cruza tudo na visão geral.
          </p>
        </div>
        <Btn icon="plus" onClick={() => setNovo(true)}>Novo processo</Btn>
      </div>

      {VISAO_GLOBAL_EM_BREVE ? (
        <Card>
          <Empty
            icon="sparkles"
            title="Visão geral do Prism — em breve"
            desc="Estamos aprimorando a leitura de portfólio do Prism (insights e padrões cruzando todos os processos). Em breve por aqui."
          />
        </Card>
      ) : (
        <>
          {(INSIGHTS.length > 0 || PROCESSOS.length > 0) && <InsightsGlobais insights={INSIGHTS} count={PROCESSOS.length} go={go} />}
          {PADROES_GLOBAIS.length > 0 && <PadroesGlobais padroes={PADROES_GLOBAIS} />}
        </>
      )}

      <div>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
          <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>
            Meus processos <span style={{ color: "var(--faint)", fontWeight: 500 }}>· {PROCESSOS.length}</span>
          </h2>
        </div>
        {procs.isLoading ? (
          <Card><Empty icon="loader" title="Carregando…" /></Card>
        ) : PROCESSOS.length === 0 ? (
          <Card>
            <Empty icon="layout-grid" title="Você ainda não tem nenhum processo" desc="Crie seu primeiro processo para começar a analisar vídeos. O Prism aprende a cada vídeo." action={<Btn icon="plus" onClick={() => setNovo(true)}>Criar primeiro processo</Btn>} />
          </Card>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 330px), 1fr))", gap: 16 }}>
            {PROCESSOS.map((p, i) => <ProcessoCard key={p.id} p={p} go={go} i={i} />)}
          </div>
        )}
      </div>

      {novo && <NovoProcessoModal onClose={() => setNovo(false)} go={go} />}
    </div>
  );
}

function InsightsGlobais({ insights, count, go }: { insights: ReturnType<typeof mapInsights>; count: number; go: Go }) {
  return (
    <Card style={{ padding: 22, background: "linear-gradient(135deg, var(--soft), #fff 60%)" }}>
      <div className="row gap3" style={{ marginBottom: 16 }}>
        <Prism size={40} ring />
        <div className="grow">
          <h2 className="font-display row gap2" style={{ fontSize: 17, fontWeight: 700 }}>
            Visão geral da sua operação <span className="chip" style={{ fontSize: 10.5 }}><span className="live-dot on" /> {count} processo{count !== 1 ? "s" : ""}</span>
          </h2>
          <p style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 2 }}>O Prism olhando todas as linhas juntas — onde priorizar e o que se repete.</p>
        </div>
      </div>
      {insights.length === 0 ? (
        <p style={{ fontSize: 13.5, color: "var(--muted)" }}>Processe vídeos em seus processos para o Prism gerar uma visão consolidada (qual priorizar, padrões e a maior oportunidade).</p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px),1fr))", gap: 12 }}>
          {insights.map((it) => (
            <div key={it.id} className="card-flat hoverlift" style={{ padding: 16 }}>
              <div className="row gap2" style={{ marginBottom: 8 }}>
                <PrioBadge p={it.prioridade} />
                <span style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }} className="grow">{it.titulo}</span>
              </div>
              <p className="pretty" style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5 }}>{it.descricao}</p>
              <div className="row wrap" style={{ gap: 6, marginTop: 10 }}>
                {it.processos.map((n) => <span key={n} className="badge badge-purple" style={{ fontSize: 10.5 }}>{n}</span>)}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function PadroesGlobais({ padroes }: { padroes: ReturnType<typeof mapPadroesGlobais> }) {
  return (
    <div>
      <div className="row gap2" style={{ marginBottom: 12 }}>
        <Icon name="git-compare" size={18} color="var(--accent)" />
        <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>Padrões entre as linhas</h2>
        <Help text="O que se repete entre processos diferentes: comportamentos compartilhados, benchmarking e problemas sistêmicos." />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 360px),1fr))", gap: 14 }}>
        {padroes.map((p) => (
          <Card key={p.id} style={{ padding: 18 }}>
            <div className="row gap2 wrap" style={{ marginBottom: 8 }}>
              <span className="badge badge-purple">{p.tipo === "compartilhado" ? "Compartilhado" : p.tipo === "benchmarking" ? "Benchmarking" : "Sistêmico"}</span>
              <Badge tone={p.confianca === "alta" ? "ok" : "warn"}>confiança {p.confianca}</Badge>
            </div>
            <h4 style={{ fontSize: 15, fontWeight: 700 }}>{p.titulo}</h4>
            <p className="pretty" style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, marginTop: 6 }}>{p.descricao}</p>
            {p.recomendacao && (
              <div className="soft" style={{ borderRadius: 10, padding: "10px 12px", marginTop: 10, fontSize: 12.5, color: "var(--text)", border: "1px solid var(--line)" }}>
                <b style={{ color: "var(--ink)" }}>Recomendação. </b>{p.recomendacao}
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

function ProcessoCard({ p, go, i }: { p: ProcMock; go: Go; i: number }) {
  const nivel = nivelDe(p.maturidade);
  const [menuOpen, setMenuOpen] = useState(false);
  const [excluir, setExcluir] = useState(false);
  return (
    <>
    <Card className="hoverlift click anim-fadeup" style={{ padding: 18, animationDelay: `${i * 60}ms`, display: "flex", flexDirection: "column" }} onClick={() => go("processo", p.id, "dashboard")}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="row gap2" style={{ marginBottom: 4 }}>
            <span className="badge badge-neutral" style={{ fontSize: 10.5 }}>{p.area}</span>
            {p.sugestoesAlta > 0 && <Badge tone="high">{p.sugestoesAlta} alta</Badge>}
          </div>
          <h3 className="font-display truncate" style={{ fontSize: 17, fontWeight: 700 }}>{p.nome}</h3>
        </div>
        <div className="row gap1" style={{ alignItems: "flex-start", flex: "none" }}>
          <MaturityMeter pct={p.maturidade} size={46} compact />
          <div style={{ position: "relative" }} onClick={(e) => e.stopPropagation()}>
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="center"
              title="Ações do processo"
              style={{ width: 28, height: 28, borderRadius: 8, border: "none", background: menuOpen ? "var(--line-2)" : "transparent", color: "var(--muted)", cursor: "pointer" }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--line-2)")}
              onMouseLeave={(e) => { if (!menuOpen) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
            >
              <Icon name="more-vertical" size={16} />
            </button>
            {menuOpen && (
              <>
                <div onClick={() => setMenuOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 50 }} />
                <div style={{ position: "absolute", top: 32, right: 0, zIndex: 51, background: "#fff", border: "1px solid var(--line)", borderRadius: 10, padding: 4, boxShadow: "0 8px 28px -10px rgba(26,16,49,.22)", minWidth: 180 }}>
                  <button
                    onClick={() => { setMenuOpen(false); setExcluir(true); }}
                    className="row gap2"
                    style={{ width: "100%", padding: "9px 11px", border: "none", borderRadius: 7, background: "transparent", color: "var(--desp)", fontSize: 13, fontWeight: 600, textAlign: "left", cursor: "pointer" }}
                    onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--desp-bg)")}
                    onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "transparent")}
                  >
                    <Icon name="trash-2" size={15} /> Excluir processo
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      <p className="clamp2 pretty" style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 8, minHeight: 34, lineHeight: 1.45 }}>{p.descricao || "Sem descrição ainda."}</p>

      <div style={{ marginTop: 12 }}>
        <div className="row" style={{ justifyContent: "space-between", fontSize: 10.5, color: "var(--muted)", marginBottom: 4 }}>
          <span className="row gap1" style={{ color: nivel.cor, fontWeight: 700 }}><Icon name="sparkles" size={11} /> Prism {nivel.rotulo.toLowerCase()}</span>
          <span>produtivo <b className="tnum" style={{ color: "var(--va)" }}>{p.va}%</b></span>
        </div>
        <LeanBar va={p.va} desp={Math.max(0, 100 - p.va)} height={7} />
      </div>

      <div className="row" style={{ gap: 8, marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--line-2)" }}>
        <MiniStat icon="video" valor={p.videos} label="vídeos" />
        <MiniStat icon="check-check" valor={p.validado + "%"} label="validado" />
        <MiniStat icon="inbox" valor={p.pendencias} label="pendências" alert={p.pendencias > 15} />
      </div>
      <div className="row gap1" style={{ marginTop: 10, fontSize: 11, color: "var(--faint)" }}>
        <Icon name="clock" size={12} /> Último vídeo {p.ultimoVideo}
      </div>
    </Card>
    {excluir && <ExcluirProcessoModal p={p} onClose={() => setExcluir(false)} />}
    </>
  );
}

function MiniStat({ icon, valor, label, alert }: { icon: string; valor: number | string; label: string; alert?: boolean }) {
  return (
    <div className="grow center" style={{ background: alert ? "var(--apoio-bg)" : "var(--soft)", borderRadius: 10, padding: "8px 6px", border: "1px solid var(--line-2)" }}>
      <div className="col" style={{ alignItems: "center", gap: 2 }}>
        <Icon name={icon} size={14} color={alert ? "#b8740b" : "var(--muted)"} />
        <span className="tnum" style={{ fontSize: 15, fontWeight: 700, color: alert ? "#b8740b" : "var(--ink)" }}>{valor}</span>
        <span style={{ fontSize: 9.5, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</span>
      </div>
    </div>
  );
}

function NovoProcessoModal({ onClose, go }: { onClose: () => void; go: Go }) {
  const qc = useQueryClient();
  const [nome, setNome] = useState("");
  const [area, setArea] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => api.processos.create(nome.trim(), undefined, area.trim() || undefined),
    onSuccess: (proc) => {
      qc.invalidateQueries({ queryKey: ["processos"] });
      onClose();
      toast("Processo criado — descreva e envie o primeiro vídeo.", { icon: "check" });
      go("processo", proc.id, "descricao");
    },
    onError: (e: Error) => setErro(e.message),
  });
  return (
    <Modal onClose={onClose}>
      <div className="row gap2" style={{ marginBottom: 4 }}>
        <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--accent-soft)", color: "var(--accent)" }}><Icon name="plus" size={18} /></span>
        <h2 className="font-display" style={{ fontSize: 19, fontWeight: 700 }}>Novo processo</h2>
      </div>
      <p style={{ fontSize: 13.5, color: "var(--muted)", margin: "6px 0 16px" }}>Dê um nome curto, como “Linha de Prensa 2” ou “Picking BCP 5”. Depois você descreve e envia o primeiro vídeo.</p>
      <label className="label">Nome do processo</label>
      <input className="field" autoFocus value={nome} onChange={(e) => setNome(e.target.value)} placeholder="Ex.: Linha de Prensa 2" />
      <label className="label" style={{ marginTop: 12 }}>Área (opcional)</label>
      <input className="field" value={area} onChange={(e) => setArea(e.target.value)} placeholder="Ex.: Estamparia" list="areas-list" />
      <datalist id="areas-list">
        {["Estamparia", "Montagem", "Logística", "Soldagem", "Usinagem", "Embalagem", "Picking", "Pintura", "Qualidade"].map((a) => <option key={a} value={a} />)}
      </datalist>
      {erro && <p style={{ fontSize: 12.5, color: "var(--desp)", marginTop: 8 }}>{erro}</p>}
      <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 20 }}>
        <Btn variant="ghost" onClick={onClose}>Cancelar</Btn>
        <Btn disabled={!nome.trim() || mut.isPending} onClick={() => mut.mutate()}>{mut.isPending ? "Criando…" : "Criar processo"}</Btn>
      </div>
    </Modal>
  );
}

function ExcluirProcessoModal({ p, onClose }: { p: ProcMock; onClose: () => void }) {
  const qc = useQueryClient();
  const [confirma, setConfirma] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => api.processos.excluir(p.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processos"] });
      qc.invalidateQueries({ queryKey: ["insights-globais"] });
      qc.invalidateQueries({ queryKey: ["padroes-globais"] });
      onClose();
      toast(`Processo "${p.nome}" excluído.`, { icon: "check" });
    },
    onError: (e: Error) => setErro(e.message),
  });
  const podeExcluir = confirma.trim() === p.nome && !mut.isPending;
  return (
    <Modal onClose={mut.isPending ? () => {} : onClose}>
      <div className="row gap2" style={{ marginBottom: 8 }}>
        <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--desp-bg)", color: "var(--desp)" }}><Icon name="alert-triangle" size={18} /></span>
        <h2 className="font-display" style={{ fontSize: 19, fontWeight: 700, color: "var(--ink)" }}>Excluir processo</h2>
      </div>
      <p style={{ fontSize: 13.5, color: "var(--text)", lineHeight: 1.55, margin: "8px 0 14px" }}>
        Esta ação apaga <b>{p.videos} vídeo{p.videos === 1 ? "" : "s"}</b>, todos os eventos, comportamentos aprendidos, sugestões, padrões, perguntas e conversas do Prism em <b style={{ color: "var(--ink)" }}>"{p.nome}"</b>. <b style={{ color: "var(--desp)" }}>Não é possível desfazer.</b>
      </p>
      <label className="label">Para confirmar, digite o nome do processo</label>
      <input className="field" autoFocus value={confirma} onChange={(e) => setConfirma(e.target.value)} placeholder={p.nome} />
      {erro && <p style={{ fontSize: 12.5, color: "var(--desp)", marginTop: 8 }}>{erro}</p>}
      <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
        <Btn variant="ghost" disabled={mut.isPending} onClick={onClose}>Cancelar</Btn>
        <Btn variant="danger" disabled={!podeExcluir} onClick={() => mut.mutate()}>{mut.isPending ? "Excluindo…" : "Excluir definitivamente"}</Btn>
      </div>
    </Modal>
  );
}
