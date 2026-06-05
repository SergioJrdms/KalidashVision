import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Btn, Card, Help, Icon, Spinner, toast } from "../components/UIKit";

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

export default function DescricaoProcesso() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const novo = params.get("novo") === "1";
  const [desc, setDesc] = useState("");
  const [area, setArea] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["processo", id],
    queryFn: () => api.processos.detalhe(id!),
    enabled: !!id,
  });
  useEffect(() => {
    if (data) {
      setDesc(data.descricao || "");
      setArea(data.area || "");
    }
  }, [data]);

  const salvarDesc = useMutation({
    mutationFn: (d: string) => api.processos.setDescricao(id!, d),
  });
  const salvarArea = useMutation({
    mutationFn: (a: string) => api.processos.setArea(id!, a || null),
  });

  async function submitTudo(e: FormEvent) {
    e.preventDefault();
    await Promise.all([salvarDesc.mutateAsync(desc.trim()), salvarArea.mutateAsync(area.trim())]);
    qc.invalidateQueries({ queryKey: ["processo", id] });
    qc.invalidateQueries({ queryKey: ["processos"] });
    toast("Salvo.", { icon: "check", color: "#3EE6AE" });
    if (novo) nav(`/processos/${id}/upload`);
  }

  if (isLoading)
    return (
      <div className="center" style={{ padding: 60 }}>
        <Spinner size={26} />
      </div>
    );

  return (
    <div className="col" style={{ gap: 18, maxWidth: 760, margin: "0 auto" }}>
      {novo && (
        <div className="row gap2" style={{ color: "var(--muted)" }}>
          <Icon name="info" size={16} />
          <span style={{ fontSize: 13 }}>
            Descreva o processo — opcional, mas <b>melhora muito a precisão</b> do Prism.
          </span>
        </div>
      )}

      <Card style={{ padding: 24 }}>
        <h1 className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>
          {novo ? "Descreva seu processo" : "Descrição do processo"}
        </h1>
        <p style={{ fontSize: 13.5, color: "var(--muted)", marginTop: 6 }}>
          O texto é injetado nos prompts da IA. Ajuda o Prism a (1) reconhecer
          comportamentos esperados, (2) usar vocabulário do seu domínio e (3)
          sinalizar o que foge do fluxo.
        </p>

        <form onSubmit={submitTudo} className="col gap3" style={{ marginTop: 18 }}>
          <label className="col" style={{ gap: 6 }}>
            <span className="label row gap1">
              Área <Help text="Categoria do processo — ex.: Estamparia, Logística. Aparece como etiqueta no portfólio." />
            </span>
            <input
              className="field"
              list="areas-list"
              value={area}
              onChange={(e) => setArea(e.target.value)}
              placeholder="Ex.: Estamparia"
              maxLength={60}
              style={{ maxWidth: 280 }}
            />
            <datalist id="areas-list">
              {AREAS_SUGERIDAS.map((a) => (
                <option key={a} value={a} />
              ))}
            </datalist>
          </label>

          <label className="col" style={{ gap: 6 }}>
            <span className="label">Como o processo funciona</span>
            <textarea
              className="field"
              rows={10}
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="Ex.: Os operadores retiram a bobina do estoque, levam até a prensa, posicionam o blank e acionam o ciclo. Depois conferem a peça com paquímetro e registram no terminal..."
            />
          </label>

          <div className="row" style={{ justifyContent: "space-between" }}>
            {novo ? (
              <Btn type="button" variant="ghost" onClick={() => nav(`/processos/${id}/upload`)}>
                Pular esta etapa
              </Btn>
            ) : (
              <span />
            )}
            <Btn type="submit" disabled={salvarDesc.isPending || salvarArea.isPending}>
              {salvarDesc.isPending || salvarArea.isPending ? "Salvando..." : novo ? "Salvar e continuar" : "Salvar"}
            </Btn>
          </div>
        </form>
      </Card>
    </div>
  );
}
