import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Button, Card, Spinner, Textarea } from "../components/UI";
import { ProcessoTabs } from "../components/Layout";

export default function DescricaoProcesso() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const novo = params.get("novo") === "1";
  const [descricao, setDescricao] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["processo", id],
    queryFn: () => api.processos.detalhe(id!),
    enabled: !!id,
  });

  useEffect(() => {
    if (data) setDescricao(data.descricao || "");
  }, [data]);

  const mut = useMutation({
    mutationFn: (d: string) => api.processos.setDescricao(id!, d),
    onSuccess: () => nav(`/processos/${id}/upload`),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    mut.mutate(descricao.trim());
  }

  if (isLoading)
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner className="h-8 w-8" />
      </div>
    );

  if (novo) {
    return (
      <Card className="p-8 max-w-3xl mx-auto">
        <h1 className="text-2xl font-semibold text-slate-900">
          Descreva seu processo
        </h1>
        <p className="text-sm text-slate-500 mt-2 mb-6">
          Esta etapa é opcional, mas <b>melhora muito a precisão</b> da análise.
          Conte como o seu processo funciona: o que os operadores fazem, em que
          ordem, em quais estações. O sistema vai usar esse texto para reconhecer
          melhor os comportamentos esperados e sinalizar o que é incomum.
        </p>
        <form onSubmit={submit} className="space-y-4">
          <Textarea
            value={descricao}
            onChange={(e) => setDescricao(e.target.value)}
            rows={10}
            placeholder="Ex.: Os operadores retiram a bobina do estoque, levam até a prensa, posicionam na máquina e acionam o ciclo. Depois conferem a peça e registram no computador..."
          />
          <div className="flex justify-between items-center">
            <Button
              type="button"
              variant="ghost"
              onClick={() => nav(`/processos/${id}/upload`)}
            >
              Pular essa etapa
            </Button>
            <Button type="submit" disabled={mut.isPending}>
              {mut.isPending ? "Salvando..." : "Salvar e continuar"}
            </Button>
          </div>
        </form>
      </Card>
    );
  }

  return (
    <div>
      <ProcessoTabs />
      <Card className="p-8 max-w-3xl">
        <h2 className="text-xl font-semibold text-slate-900 mb-1">
          Descrição do processo
        </h2>
        <p className="text-sm text-slate-500 mb-6">
          Quanto mais clara a descrição, melhor a análise. Vale a pena revisar
          conforme você aprende mais sobre os resultados.
        </p>
        <form onSubmit={submit} className="space-y-4">
          <Textarea
            value={descricao}
            onChange={(e) => setDescricao(e.target.value)}
            rows={10}
          />
          <div className="flex justify-end">
            <Button type="submit" disabled={mut.isPending}>
              {mut.isPending ? "Salvando..." : "Salvar"}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
