import { FormEvent, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { Button, Card, EmptyState, Input, Spinner } from "../components/UI";

export default function Processos() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["processos"],
    queryFn: () => api.processos.list(),
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

  return (
    <div>
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Meus processos</h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            Cada processo é um contexto <b>isolado</b> de análise. Dados,
            comportamentos aprendidos e sugestões de um processo nunca
            atravessam para outro — mesmo que tenham nomes iguais.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>+ Novo processo</Button>
      </div>

      {processos.length === 0 ? (
        <Card className="p-2">
          <EmptyState
            title="Você ainda não tem nenhum processo"
            description="Crie seu primeiro processo para começar a analisar vídeos de operação."
            action={<Button onClick={() => setOpen(true)}>Criar o primeiro processo</Button>}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {processos.map((p) => (
            <Link
              key={p.id}
              to={`/processos/${p.id}/dashboard`}
              className="block group"
            >
              <Card className="p-5 hover:border-kv-purple-300 hover:shadow-md transition h-full">
                <div className="flex items-start justify-between">
                  <h3 className="font-semibold text-slate-900 group-hover:text-kv-purple-dark">
                    {p.processo}
                  </h3>
                </div>
                <p className="text-sm text-slate-500 mt-2 line-clamp-3 min-h-[3.5rem]">
                  {p.descricao || "Sem descrição. Adicione uma para melhorar a análise."}
                </p>
                <div className="mt-4 text-xs text-slate-400">
                  Atualizado em {new Date(p.atualizado_em).toLocaleDateString("pt-BR")}
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}

      {open && <NovoProcessoModal onClose={() => setOpen(false)} onCreated={() => qc.invalidateQueries({ queryKey: ["processos"] })} />}
    </div>
  );
}

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
