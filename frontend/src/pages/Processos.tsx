import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import {
  Badge,
  Btn,
  Card,
  Empty,
  Help,
  Icon,
  LeanBar,
  Modal,
  RingMaturidade,
  Spinner,
  iniciaisDe,
  nivelDe,
  tempoRelativo,
  toast,
} from "../components/UIKit";
import { PrismAvatar } from "../components/PrismAvatar";
import type { InsightGlobal, PadraoGlobal, Processo } from "../lib/types";

const AREAS_SUGERIDAS = [
  "Estamparia",
  "Montagem",
  "Logística",
  "Soldagem",
  "Usinagem",
  "Embalagem",
  "Picking",
  "Pintura",
  "Qualidade",
];

export default function Processos() {
  const qc = useQueryClient();
  const [novo, setNovo] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["processos"], queryFn: () => api.processos.list() });
  const insights = useQuery({ queryKey: ["insights-globais"], queryFn: () => api.insightsGlobais() });
  const padroes = useQuery({ queryKey: ["padroes-globais"], queryFn: () => api.padroes.globais() });

  if (isLoading)
    return (
      <div className="center" style={{ padding: 80 }}>
        <Spinner size={28} />
      </div>
    );

  const processos = (data || []) as Processo[];

  return (
    <div className="col" style={{ gap: 26, maxWidth: 1180, margin: "0 auto" }}>
      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}
      >
        <div>
          <h1 className="font-display" style={{ fontSize: 28, fontWeight: 800 }}>
            Sua operação
          </h1>
          <p
            className="pretty"
            style={{ fontSize: 14.5, color: "var(--muted)", marginTop: 6, maxWidth: 560 }}
          >
            Cada processo é um contexto isolado de análise. O Prism aprende cada um
            separadamente — e cruza tudo na visão geral.
          </p>
        </div>
        <Btn icon="plus" onClick={() => setNovo(true)}>
          Novo processo
        </Btn>
      </div>

      {processos.length > 0 && (
        <InsightsGlobaisBloco
          insights={insights.data || []}
          carregando={insights.isLoading}
        />
      )}

      {processos.length > 0 && (padroes.data?.length ?? 0) > 0 && (
        <PadroesGlobaisBloco padroes={padroes.data || []} />
      )}

      <div>
        <div
          className="row"
          style={{ justifyContent: "space-between", marginBottom: 14 }}
        >
          <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>
            Meus processos{" "}
            <span style={{ color: "var(--faint)", fontWeight: 500 }}>
              · {processos.length}
            </span>
          </h2>
        </div>
        {processos.length === 0 ? (
          <Card style={{ padding: 6 }}>
            <Empty
              icon="layout-grid"
              title="Você ainda não tem nenhum processo"
              desc="Crie seu primeiro processo para começar a analisar vídeos. O Prism aprende a cada vídeo enviado."
              action={<Btn icon="plus" onClick={() => setNovo(true)}>Criar primeiro processo</Btn>}
            />
          </Card>
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))",
              gap: 16,
            }}
          >
            {processos.map((p) => (
              <ProcessoCard
                key={p.id}
                p={p}
                onExcluido={() => {
                  qc.invalidateQueries({ queryKey: ["processos"] });
                  qc.invalidateQueries({ queryKey: ["insights-globais"] });
                  qc.invalidateQueries({ queryKey: ["padroes-globais"] });
                }}
              />
            ))}
          </div>
        )}
      </div>

      {novo && (
        <NovoProcessoModal
          onClose={() => setNovo(false)}
          onCreated={() => qc.invalidateQueries({ queryKey: ["processos"] })}
        />
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Insights globais
// ════════════════════════════════════════════════════════════════════════
function InsightsGlobaisBloco({
  insights,
  carregando,
}: {
  insights: InsightGlobal[];
  carregando: boolean;
}) {
  return (
    <Card style={{ padding: 22 }}>
      <div className="row gap2" style={{ marginBottom: 4 }}>
        <PrismAvatar size={28} ring />
        <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>
          Visão geral do Prism
        </h2>
        <Help text="O Prism olha todos os processos juntos: qual priorizar, onde está a maior oportunidade e padrões entre processos." />
      </div>
      <p style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 16 }}>
        Insights consolidados de toda a sua operação.
      </p>

      {carregando ? (
        <div className="row gap2" style={{ color: "var(--muted)", fontSize: 13 }}>
          <Spinner size={16} /> carregando insights…
        </div>
      ) : insights.length === 0 ? (
        <div
          className="soft"
          style={{
            border: "1px solid var(--line)",
            borderRadius: 12,
            padding: 16,
            fontSize: 13.5,
            color: "var(--muted)",
          }}
        >
          Processe vídeos em seus processos para o Prism gerar uma visão consolidada
          (qual priorizar, padrões e a maior oportunidade do portfólio).
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 12,
          }}
        >
          {insights.map((it) => (
            <div
              key={it.id}
              style={{
                border: "1px solid var(--line)",
                borderRadius: 14,
                padding: 16,
                background: "#fff",
              }}
            >
              <div className="row gap2 wrap" style={{ marginBottom: 6 }}>
                <Badge tone={badgePrioridade(it.prioridade)}>
                  {(it.prioridade || "info").toUpperCase()}
                </Badge>
              </div>
              <h4 style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>{it.titulo}</h4>
              <p
                className="pretty"
                style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, marginTop: 6 }}
              >
                {it.descricao}
              </p>
              {it.processos_relacionados && it.processos_relacionados.length > 0 && (
                <div className="row gap1 wrap" style={{ marginTop: 10 }}>
                  {it.processos_relacionados.map((n) => (
                    <span key={n} className="badge badge-purple" style={{ fontSize: 10.5 }}>
                      {n}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function badgePrioridade(p: string): "high" | "warn" | "info" | "neutral" {
  const x = (p || "").toLowerCase();
  if (x === "alta") return "high";
  if (x === "media") return "warn";
  if (x === "info") return "info";
  return "neutral";
}

// ════════════════════════════════════════════════════════════════════════
// Padrões globais (Camada C)
// ════════════════════════════════════════════════════════════════════════
const TIPO_PADRAO_GLOBAL: Record<string, string> = {
  compartilhado: "Compartilhado",
  benchmarking: "Benchmarking",
  sistemico: "Sistêmico",
};

function PadroesGlobaisBloco({ padroes }: { padroes: PadraoGlobal[] }) {
  return (
    <div>
      <div className="row gap2" style={{ marginBottom: 12 }}>
        <Icon name="git-compare" size={18} color="var(--accent)" />
        <h2 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>
          Padrões entre as linhas
        </h2>
        <Help text="O que se repete entre processos: comportamentos compartilhados, benchmarking e problemas sistêmicos." />
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
          gap: 14,
        }}
      >
        {padroes.map((p) => (
          <Card key={p.id} style={{ padding: 18 }}>
            <div className="row gap2 wrap" style={{ marginBottom: 8 }}>
              <span className="badge badge-purple">
                {TIPO_PADRAO_GLOBAL[p.tipo] || p.tipo}
              </span>
              <Badge tone={p.confianca === "alta" ? "ok" : p.confianca === "media" ? "warn" : "neutral"}>
                confiança {p.confianca}
              </Badge>
            </div>
            <h4 style={{ fontSize: 15, fontWeight: 700 }}>{p.titulo}</h4>
            <p
              className="pretty"
              style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, marginTop: 6 }}
            >
              {p.descricao}
            </p>
            {p.recomendacao && (
              <div
                className="soft"
                style={{
                  borderRadius: 10,
                  padding: "10px 12px",
                  marginTop: 10,
                  fontSize: 12.5,
                  color: "var(--text)",
                  border: "1px solid var(--line)",
                }}
              >
                <b style={{ color: "var(--ink)" }}>Recomendação. </b>
                {p.recomendacao}
              </div>
            )}
            {p.processos_relacionados && p.processos_relacionados.length > 0 && (
              <div className="row gap1 wrap" style={{ marginTop: 10 }}>
                {p.processos_relacionados.map((n) => (
                  <span key={n} className="badge badge-purple" style={{ fontSize: 10.5 }}>
                    {n}
                  </span>
                ))}
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Card de processo
// ════════════════════════════════════════════════════════════════════════
function ProcessoCard({ p, onExcluido }: { p: Processo; onExcluido: () => void }) {
  const nav = useNavigate();
  const [menu, setMenu] = useState(false);
  const [confirmar, setConfirmar] = useState(false);
  const mat = p.maturidade ?? 0;
  const nivel = nivelDe(mat);
  const cv = p.composicao_valor;

  return (
    <Card
      className="hoverlift click"
      style={{ padding: 18, position: "relative" }}
      onClick={() => nav(`/processos/${p.id}/dashboard`)}
    >
      <div className="row gap3" style={{ alignItems: "flex-start", marginBottom: 12 }}>
        <RingMaturidade pct={mat} size={56} />
        <div className="grow col" style={{ minWidth: 0, gap: 4 }}>
          <div className="row gap2 wrap">
            {p.area && (
              <span className="badge badge-purple" style={{ fontSize: 10.5 }}>
                {p.area}
              </span>
            )}
            <span style={{ fontSize: 11, color: nivel.cor, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" }}>
              {nivel.rotulo}
            </span>
          </div>
          <h3 className="truncate" style={{ fontSize: 16, fontWeight: 700, color: "var(--ink)" }} title={p.processo}>
            {p.processo}
          </h3>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            setMenu((v) => !v);
          }}
          onBlur={() => setTimeout(() => setMenu(false), 150)}
          style={{
            background: "transparent",
            border: "1px solid var(--line)",
            color: "var(--muted)",
            borderRadius: 8,
            width: 28,
            height: 28,
            display: "grid",
            placeItems: "center",
          }}
          title="Mais"
        >
          <Icon name="more-horizontal" size={14} />
        </button>
        {menu && (
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "absolute",
              top: 44,
              right: 14,
              background: "#fff",
              border: "1px solid var(--line)",
              borderRadius: 10,
              boxShadow: "var(--glow-lg)",
              padding: 4,
              zIndex: 5,
              minWidth: 180,
            }}
          >
            <MenuItem icon="layout-dashboard" label="Abrir" onClick={() => nav(`/processos/${p.id}/dashboard`)} />
            <MenuItem icon="upload" label="Novo vídeo" onClick={() => nav(`/processos/${p.id}/upload`)} />
            <MenuItem icon="file-text" label="Descrição" onClick={() => nav(`/processos/${p.id}/descricao`)} />
            <div style={{ height: 1, background: "var(--line)", margin: "4px 0" }} />
            <MenuItem icon="trash-2" label="Excluir processo" danger onClick={() => setConfirmar(true)} />
          </div>
        )}
      </div>

      {p.descricao && (
        <p className="clamp2 pretty" style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, minHeight: 40 }}>
          {p.descricao}
        </p>
      )}

      {(p.n_videos ?? 0) > 0 ? (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 8,
              marginTop: 14,
              fontSize: 12,
            }}
          >
            <Stat label="Vídeos" valor={String(p.n_videos)} />
            <Stat label="Validado" valor={`${p.pct_validado ?? 0}%`} />
            <Stat
              label="Sug. alta"
              valor={String(p.n_sugestoes_alta ?? 0)}
              destaque={(p.n_sugestoes_alta ?? 0) > 0}
            />
            <Stat label="Pendências" valor={String(p.eventos_pendentes ?? 0)} />
          </div>

          {cv && (
            <div style={{ marginTop: 12 }}>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
                <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>
                  Valor agregado
                </span>
                <span style={{ fontSize: 11.5, color: "var(--va)", fontWeight: 700 }}>
                  {cv.valor_agregado_pct}%
                </span>
              </div>
              <LeanBar
                va={cv.valor_agregado_pct}
                apoio={cv.apoio_pct}
                desp={cv.desperdicio_pct}
                none={cv.nao_classificado_pct}
              />
            </div>
          )}
        </>
      ) : (
        <p style={{ fontSize: 12, color: "var(--faint)", marginTop: 14, fontStyle: "italic" }}>
          Nenhum vídeo processado ainda.
        </p>
      )}

      <div className="row" style={{ marginTop: 14, justifyContent: "space-between", fontSize: 11, color: "var(--faint)" }}>
        <span>
          {p.ultimo_video_em
            ? `Último vídeo ${tempoRelativo(p.ultimo_video_em)}`
            : `Atualizado em ${tempoRelativo(p.atualizado_em)}`}
        </span>
        <span className="row gap1" style={{ color: "var(--accent)" }}>
          <Icon name="arrow-right" size={12} color="var(--accent)" />
        </span>
      </div>

      {confirmar && (
        <ConfirmarExclusao
          processo={p}
          onClose={() => setConfirmar(false)}
          onExcluido={onExcluido}
        />
      )}
    </Card>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onMouseDown={onClick}
      className="row gap2 click"
      style={{
        width: "100%",
        textAlign: "left",
        padding: "8px 10px",
        background: "transparent",
        border: 0,
        borderRadius: 8,
        fontSize: 13,
        color: danger ? "var(--desp)" : "var(--text)",
      }}
    >
      <Icon name={icon} size={14} color={danger ? "var(--desp)" : "var(--muted)"} />
      <span>{label}</span>
    </button>
  );
}

function Stat({ label, valor, destaque }: { label: string; valor: string; destaque?: boolean }) {
  return (
    <div
      style={{
        padding: "8px 10px",
        background: destaque ? "var(--apoio-bg)" : "var(--soft)",
        borderRadius: 10,
      }}
    >
      <div
        style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600 }}
      >
        {label}
      </div>
      <div style={{ fontWeight: 700, color: destaque ? "#b8740b" : "var(--ink)", fontSize: 14 }}>
        {valor}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Exclusão (confirmação forte)
// ════════════════════════════════════════════════════════════════════════
function ConfirmarExclusao({
  processo,
  onClose,
  onExcluido,
}: {
  processo: Processo;
  onClose: () => void;
  onExcluido: () => void;
}) {
  const [txt, setTxt] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => api.processos.excluir(processo.id),
    onSuccess: () => {
      onExcluido();
      onClose();
      toast(`Processo "${processo.processo}" excluído.`, { icon: "trash-2", color: "#F8B4B6" });
    },
    onError: (e: Error) => setErro(e.message),
  });
  const ok = txt.trim() === processo.processo;
  return (
    <Modal open onClose={onClose}>
      <h3 className="font-display" style={{ fontSize: 18, fontWeight: 700, color: "var(--desp)" }}>
        Excluir processo
      </h3>
      <p style={{ fontSize: 13.5, color: "var(--text)", marginTop: 8 }}>
        Esta ação é <b>irreversível</b>. Vai apagar <b>permanentemente</b> tudo deste processo:
      </p>
      <ul style={{ marginTop: 8, marginLeft: 20, fontSize: 13, color: "var(--text)", listStyle: "disc" }}>
        <li>vídeos enviados (do armazenamento)</li>
        <li>eventos e comportamentos aprendidos</li>
        <li>sugestões, perguntas e padrões</li>
        <li>conversas do Prism deste processo</li>
      </ul>
      <p style={{ marginTop: 12, fontSize: 13, color: "var(--text)" }}>
        Para confirmar, digite o nome:{" "}
        <code
          style={{ background: "var(--line-2)", padding: "2px 6px", borderRadius: 6, fontFamily: "var(--mono)" }}
        >
          {processo.processo}
        </code>
      </p>
      <input
        className="field"
        autoFocus
        value={txt}
        onChange={(e) => setTxt(e.target.value)}
        style={{ marginTop: 10 }}
      />
      {erro && (
        <div
          style={{
            marginTop: 10,
            fontSize: 13,
            color: "var(--desp)",
            background: "var(--desp-bg)",
            border: "1px solid rgba(229,72,77,.2)",
            borderRadius: 10,
            padding: "8px 11px",
          }}
        >
          {erro}
        </div>
      )}
      <div className="row gap2" style={{ marginTop: 18, justifyContent: "flex-end" }}>
        <Btn variant="ghost" onClick={onClose}>
          Cancelar
        </Btn>
        <Btn variant="danger" disabled={!ok || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? "Excluindo..." : "Excluir definitivamente"}
        </Btn>
      </div>
    </Modal>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Modal de novo processo (com area)
// ════════════════════════════════════════════════════════════════════════
function NovoProcessoModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const nav = useNavigate();
  const [nome, setNome] = useState("");
  const [area, setArea] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => api.processos.create(nome.trim(), undefined, area.trim() || undefined),
    onSuccess: (proc) => {
      onCreated();
      onClose();
      toast("Processo criado.", { icon: "check", color: "#3EE6AE" });
      nav(`/processos/${proc.id}/descricao?novo=1`);
    },
    onError: (e: Error) => setErro(e.message),
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!nome.trim()) return;
    setErro(null);
    mut.mutate();
  }
  return (
    <Modal open onClose={onClose} width={460}>
      <h3 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>
        Novo processo
      </h3>
      <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 4 }}>
        Dê um nome curto, como "Linha de Prensa 2" ou "Picking BCP 5".
      </p>
      <form onSubmit={submit} className="col gap3" style={{ marginTop: 16 }}>
        <label className="col" style={{ gap: 6 }}>
          <span className="label">Nome do processo</span>
          <input
            className="field"
            autoFocus
            value={nome}
            onChange={(e) => setNome(e.target.value)}
            maxLength={120}
          />
        </label>
        <label className="col" style={{ gap: 6 }}>
          <span className="label">Área (opcional)</span>
          <input
            className="field"
            list="areas-list"
            value={area}
            onChange={(e) => setArea(e.target.value)}
            placeholder="Ex.: Estamparia"
            maxLength={60}
          />
          <datalist id="areas-list">
            {AREAS_SUGERIDAS.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </label>
        {erro && (
          <div
            style={{
              fontSize: 13,
              color: "var(--desp)",
              background: "var(--desp-bg)",
              border: "1px solid rgba(229,72,77,.2)",
              borderRadius: 10,
              padding: "8px 11px",
            }}
          >
            {erro}
          </div>
        )}
        <div className="row gap2" style={{ justifyContent: "flex-end" }}>
          <Btn type="button" variant="ghost" onClick={onClose}>
            Cancelar
          </Btn>
          <Btn type="submit" disabled={mut.isPending || !nome.trim()}>
            {mut.isPending ? "Criando..." : "Criar"}
          </Btn>
        </div>
      </form>
    </Modal>
  );
}

// helper para os componentes
export { iniciaisDe };
