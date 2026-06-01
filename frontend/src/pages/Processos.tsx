import { FormEvent, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { Badge, Button, Card, EmptyState, Input, Spinner } from "../components/UI";
import { PrismAvatar } from "../components/PrismAvatar";
import type { InsightGlobal, Processo } from "../lib/types";

export default function Processos() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["processos"],
    queryFn: () => api.processos.list(),
  });
  const insights = useQuery({
    queryKey: ["insights-globais"],
    queryFn: () => api.insightsGlobais(),
  });

  if (isLoading)
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner className="h-8 w-8" />
      </div>
    );
  if (error)
    return (
      <div className="text-red-700 bg-red-50 border border-red-200 rounded-lg p-4">
        {(error as Error).message}
      </div>
    );

  const processos = data || [];
  const algumComVideo = processos.some((p) => (p.n_videos ?? 0) > 0);

  return (
    <div className="space-y-8">
      {/* Cabeçalho */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Sua operação</h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            Cada processo é um contexto <b>isolado</b> de análise. Abaixo, a visão
            consolidada do Prism e seus processos.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>+ Novo processo</Button>
      </div>

      {/* Insights globais do Prism */}
      {processos.length > 0 && (
        <InsightsGlobais
          insights={insights.data || []}
          carregando={insights.isLoading}
          temVideo={algumComVideo}
        />
      )}

      {/* Grid de processos */}
      <div>
        <h2 className="text-lg font-semibold text-slate-900 mb-3">Meus processos</h2>
        {processos.length === 0 ? (
          <Card className="p-2">
            <EmptyState
              title="Você ainda não tem nenhum processo"
              description="Crie seu primeiro processo para começar a analisar vídeos de operação. O Prism vai aprender sua operação a cada vídeo."
              action={<Button onClick={() => setOpen(true)}>Criar o primeiro processo</Button>}
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {processos.map((p) => (
              <ProcessoCard
                key={p.id}
                p={p}
                onExcluido={() => {
                  qc.invalidateQueries({ queryKey: ["processos"] });
                  qc.invalidateQueries({ queryKey: ["insights-globais"] });
                }}
              />
            ))}
          </div>
        )}
      </div>

      {open && (
        <NovoProcessoModal
          onClose={() => setOpen(false)}
          onCreated={() => qc.invalidateQueries({ queryKey: ["processos"] })}
        />
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Insights globais
// ════════════════════════════════════════════════════════════════════════
function InsightsGlobais({
  insights,
  carregando,
  temVideo,
}: {
  insights: InsightGlobal[];
  carregando: boolean;
  temVideo: boolean;
}) {
  return (
    <Card className="p-6 bg-gradient-to-br from-kv-purple-50 via-white to-kv-indigo-bg/40 border-kv-purple-200">
      <div className="flex items-center gap-2.5 mb-4">
        <PrismAvatar size={36} />
        <div>
          <h2 className="font-semibold text-slate-900 leading-tight">
            Visão geral da sua operação
          </h2>
          <p className="text-xs text-slate-500 leading-tight">
            O Prism olhando todos os processos juntos.
          </p>
        </div>
      </div>

      {carregando ? (
        <div className="flex items-center gap-2 text-sm text-slate-500 py-4">
          <Spinner className="h-4 w-4" /> carregando insights…
        </div>
      ) : !temVideo || insights.length === 0 ? (
        <div className="text-sm text-slate-500 bg-white/60 rounded-xl p-4 border border-kv-purple-100">
          {!temVideo
            ? "Processe vídeos em seus processos para o Prism gerar uma visão consolidada (qual priorizar, onde está a maior oportunidade e padrões entre processos)."
            : "Ainda não há insights consolidados. Eles são gerados automaticamente após o processamento de novos vídeos."}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {insights.map((it) => (
            <InsightCard key={it.id} it={it} />
          ))}
        </div>
      )}
    </Card>
  );
}

function InsightCard({ it }: { it: InsightGlobal }) {
  const tone = (it.prioridade as "alta" | "media" | "info") || "info";
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <div className="flex items-center gap-2 mb-1.5">
        <Badge tone={tone}>{(it.prioridade || "info").toUpperCase()}</Badge>
        <span className="text-sm font-semibold text-slate-900">{it.titulo}</span>
      </div>
      <p className="text-sm text-slate-600 leading-relaxed">{it.descricao}</p>
      {it.processos_relacionados && it.processos_relacionados.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {it.processos_relacionados.map((nome) => (
            <span
              key={nome}
              className="text-[11px] bg-kv-purple-50 text-kv-purple-dark px-2 py-0.5 rounded-full border border-kv-purple-200"
            >
              {nome}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Card de processo enriquecido
// ════════════════════════════════════════════════════════════════════════
function ProcessoCard({ p, onExcluido }: { p: Processo; onExcluido: () => void }) {
  const [menuAberto, setMenuAberto] = useState(false);
  const [confirmar, setConfirmar] = useState(false);
  const nav = useNavigate();

  const temDados = (p.n_videos ?? 0) > 0;
  const va = p.composicao_valor?.valor_agregado_pct;

  return (
    <Card className="p-5 hover:border-kv-purple-300 hover:shadow-md transition h-full flex flex-col relative">
      {/* menu de ações */}
      <div className="absolute top-3 right-3">
        <button
          onClick={(e) => {
            e.preventDefault();
            setMenuAberto((v) => !v);
          }}
          onBlur={() => setTimeout(() => setMenuAberto(false), 150)}
          className="h-7 w-7 rounded-md text-slate-400 hover:text-slate-700 hover:bg-slate-100 flex items-center justify-center"
          title="Ações"
        >
          ⋮
        </button>
        {menuAberto && (
          <div className="absolute right-0 mt-1 w-40 bg-white rounded-lg shadow-lg border border-slate-200 py-1 z-10">
            <button
              onMouseDown={() => nav(`/processos/${p.id}/dashboard`)}
              className="w-full text-left px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
            >
              Abrir
            </button>
            <button
              onMouseDown={() => setConfirmar(true)}
              className="w-full text-left px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
            >
              Excluir processo
            </button>
          </div>
        )}
      </div>

      <Link to={`/processos/${p.id}/dashboard`} className="block group flex-1">
        <h3 className="font-semibold text-slate-900 group-hover:text-kv-purple-dark pr-8">
          {p.processo}
        </h3>
        <p className="text-sm text-slate-500 mt-1.5 line-clamp-2 min-h-[2.5rem]">
          {p.descricao || "Sem descrição. Adicione uma para melhorar a análise."}
        </p>

        {/* mini-estatísticas */}
        {temDados ? (
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <Stat label="Vídeos" valor={String(p.n_videos)} />
            <Stat label="Validado" valor={`${p.pct_validado ?? 0}%`} />
            <Stat
              label="Sug. alta"
              valor={String(p.n_sugestoes_alta ?? 0)}
              destaque={(p.n_sugestoes_alta ?? 0) > 0}
            />
            <Stat label="Pendências" valor={String(p.eventos_pendentes ?? 0)} />
          </div>
        ) : (
          <div className="mt-3 text-xs text-slate-400 italic">
            Nenhum vídeo processado ainda.
          </div>
        )}

        {/* mini barra de valor agregado */}
        {temDados && p.composicao_valor && (
          <div className="mt-3">
            <div className="flex justify-between text-[10px] text-slate-400 mb-1">
              <span>Valor agregado</span>
              <span>{va}%</span>
            </div>
            <div className="flex h-1.5 rounded-full overflow-hidden bg-slate-100">
              <Seg pct={p.composicao_valor.valor_agregado_pct} cor="#10b981" />
              <Seg pct={p.composicao_valor.apoio_pct} cor="#f59e0b" />
              <Seg pct={p.composicao_valor.desperdicio_pct} cor="#ef4444" />
              <Seg pct={p.composicao_valor.nao_classificado_pct} cor="#cbd5e1" />
            </div>
          </div>
        )}
      </Link>

      <div className="mt-4 text-[11px] text-slate-400">
        {p.ultimo_video_em
          ? `Último vídeo em ${new Date(p.ultimo_video_em).toLocaleDateString("pt-BR")}`
          : `Atualizado em ${new Date(p.atualizado_em).toLocaleDateString("pt-BR")}`}
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

function Stat({ label, valor, destaque }: { label: string; valor: string; destaque?: boolean }) {
  return (
    <div className={`rounded-lg px-2 py-1.5 ${destaque ? "bg-amber-50" : "bg-slate-50"}`}>
      <div className="text-slate-400 text-[10px] uppercase tracking-wide">{label}</div>
      <div className={`font-semibold ${destaque ? "text-amber-700" : "text-slate-800"}`}>
        {valor}
      </div>
    </div>
  );
}

function Seg({ pct, cor }: { pct: number; cor: string }) {
  if (!pct || pct <= 0) return null;
  return <div style={{ width: `${pct}%`, background: cor }} title={`${pct}%`} />;
}

// ════════════════════════════════════════════════════════════════════════
// Confirmação forte de exclusão (estilo GitHub: digite o nome)
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
  const [texto, setTexto] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => api.processos.excluir(processo.id),
    onSuccess: () => {
      onExcluido();
      onClose();
    },
    onError: (e: Error) => setErro(e.message),
  });
  const confere = texto.trim() === processo.processo;

  return (
    <div
      className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50 px-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <Card className="w-full max-w-md p-6">
        <h2 className="text-lg font-semibold text-red-700 mb-1">Excluir processo</h2>
        <p className="text-sm text-slate-600 mb-3">
          Esta ação é <b>irreversível</b>. Vai apagar <b>permanentemente</b> tudo
          deste processo:
        </p>
        <ul className="text-sm text-slate-600 list-disc pl-5 mb-4 space-y-0.5">
          <li>vídeos enviados (do armazenamento)</li>
          <li>eventos e comportamentos aprendidos</li>
          <li>sugestões de melhoria e perguntas</li>
          <li>conversas do Prism deste processo</li>
        </ul>
        <p className="text-sm text-slate-600 mb-2">
          Para confirmar, digite o nome do processo:{" "}
          <code className="bg-slate-100 px-1.5 py-0.5 rounded text-slate-800">
            {processo.processo}
          </code>
        </p>
        <Input
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          autoFocus
          placeholder={processo.processo}
        />
        {erro && (
          <div className="mt-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
            {erro}
          </div>
        )}
        <div className="flex gap-2 justify-end mt-5">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            type="button"
            variant="danger"
            disabled={!confere || mut.isPending}
            onClick={() => mut.mutate()}
          >
            {mut.isPending ? "Excluindo..." : "Excluir definitivamente"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
function NovoProcessoModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [nome, setNome] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: (n: string) => api.processos.create(n),
    onSuccess: (proc) => {
      onCreated();
      onClose();
      window.location.href = `/processos/${proc.id}/descricao?novo=1`;
    },
    onError: (e: Error) => setErro(e.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!nome.trim()) return;
    setErro(null);
    mut.mutate(nome.trim());
  }

  return (
    <div className="fixed inset-0 bg-slate-900/30 flex items-center justify-center z-50 px-4">
      <Card className="w-full max-w-md p-6">
        <h2 className="text-lg font-semibold text-slate-900 mb-1">Novo processo</h2>
        <p className="text-sm text-slate-500 mb-4">
          Dê um nome curto, como "Linha de prensa 2" ou "Picking BCP 5".
        </p>
        <form onSubmit={submit} className="space-y-4">
          <Input
            label="Nome do processo"
            value={nome}
            onChange={(e) => setNome(e.target.value)}
            autoFocus
            maxLength={120}
          />
          {erro && (
            <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
              {erro}
            </div>
          )}
          <div className="flex gap-2 justify-end">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit" disabled={mut.isPending || !nome.trim()}>
              {mut.isPending ? "Criando..." : "Criar"}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
