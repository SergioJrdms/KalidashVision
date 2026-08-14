// ============================================================
// Fase 102 — A PRECISÃO MEDIDA. Amostragem CEGA.
//
// "Hoje a estimativa de acerto é impressão, não medida." Esta tela produz o
// número real, e o protocolo é o que o torna real:
//
//   1. sorteia N eventos do dia — SORTEIO DE VERDADE, sem filtrar por suspeita
//   2. mostra os frames SEM a descrição
//   3. o gestor escreve o que vê
//   4. SÓ ENTÃO a descrição é revelada
//   5. o gestor marca: bate · bate em parte · não bate
//
// ⚠️ A ORDEM É O EXPERIMENTO. Ver a descrição antes de responder ancora a
// resposta, e o número deixa de ser acerto para virar concordância com o que
// já estava escrito. Por isso o texto NÃO CHEGA AO NAVEGADOR enquanto o item
// não foi respondido — não basta escondê-lo com CSS, bastaria abrir o
// inspetor. O backend recusa o veredito de um item não respondido.
//
// ⚠️ TRÊS RESULTADOS, NUNCA UMA MÉDIA. "Bate em parte" não é meio-acerto:
// descrição que acerta a ação e erra o detalhe tem conserto de PROMPT;
// descrição que inventa a cena tem conserto de CAPTURA. Colapsar apagaria a
// única informação que diz o que fazer.
// ============================================================
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Btn, Card, Empty, Icon, PanelHead, toast } from "../design/ui";
import { FrameStripReal } from "../lib/frames";
import { leanCor } from "../design/helpers";
import type { Go } from "../design/Shell";
import type { ProcHeaderMock } from "../lib/adapt";

type Item = {
  id: string;
  evento_id: string;
  respondido: boolean;
  revelado: boolean;
  veredito: string | null;
  resposta_humana: string | null;
  n_amostras_no_sorteio: number | null;
  origem_descricao: string | null;
  descricao: string | null;
};

const VEREDITOS: { id: string; rot: string; cor: string; ajuda: string }[] = [
  { id: "bate", rot: "Bate", cor: leanCor("va"),
    ajuda: "A descrição diz o que você viu." },
  { id: "bate_em_parte", rot: "Bate em parte", cor: "#c98a00",
    ajuda: "Acertou a ação, errou o detalhe. Conserto de prompt." },
  { id: "nao_bate", rot: "Não bate", cor: leanCor("desp"),
    ajuda: "Descreve outra cena. Conserto de captura." },
];

export default function Precisao({ proc }: { proc: ProcHeaderMock; go?: Go }) {
  const qc = useQueryClient();
  const hoje = new Date().toISOString().slice(0, 10);
  const [dia, setDia] = useState(hoje);
  const [n, setN] = useState(20);

  const q = useQuery({
    queryKey: ["amostragem", proc.id, dia],
    queryFn: () => api.amostragem.listar(proc.id, dia),
  });

  const sortear = useMutation({
    mutationFn: () => api.amostragem.sortear(proc.id, dia, n),
    onSuccess: (r) => {
      toast(`${r.sorteados} trecho(s) sorteados de ${r.candidatos_no_dia} do dia.`);
      qc.invalidateQueries({ queryKey: ["amostragem", proc.id, dia] });
    },
    onError: (e: unknown) => toast(`Não deu para sortear: ${String(e)}`),
  });

  const itens: Item[] = q.data?.itens || [];
  const taxa = q.data?.taxa;
  const pendentes = itens.filter((i) => !i.veredito);

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="A IA acerta?"
          ajuda="Você julga os trechos SEM ver a descrição. Só depois de escrever o que viu é que o texto do sistema aparece. Assim o número mede o acerto, não a sua concordância com o que já estava escrito."
          leitura="Sorteio de verdade: os trechos vêm ao acaso, não são os suspeitos. Medir só o que parece errado devolveria um número pessimista que pareceria medida."
        />
        <div className="row gap2 wrap" style={{ marginTop: 10, alignItems: "flex-end" }}>
          <label className="col" style={{ gap: 4, fontSize: 12 }}>
            <span style={{ color: "var(--muted)" }}>Dia</span>
            <input className="field" type="date" value={dia}
                   onChange={(e) => setDia(e.target.value)} style={{ width: 160 }} />
          </label>
          <label className="col" style={{ gap: 4, fontSize: 12 }}>
            <span style={{ color: "var(--muted)" }}>Quantos trechos</span>
            <input className="field" type="number" min={5} max={100} value={n}
                   onChange={(e) => setN(Number(e.target.value) || 20)}
                   style={{ width: 90 }} />
          </label>
          <Btn icon="shuffle" onClick={() => sortear.mutate()}
               disabled={sortear.isPending}>
            {sortear.isPending ? "Sorteando…" : "Sortear"}
          </Btn>
        </div>
      </Card>

      {taxa && taxa.n_julgadas > 0 && <Placar taxa={taxa} />}

      {q.isLoading ? (
        <Empty icon="loader" title="Carregando…" />
      ) : itens.length === 0 ? (
        <Empty icon="shuffle" title="Nenhum trecho sorteado neste dia"
               desc="Escolha o dia e sorteie. Os trechos vêm ao acaso." />
      ) : (
        <div className="col" style={{ gap: 12 }}>
          {pendentes.length === 0 && (
            <Card style={{ padding: 14 }}>
              <span style={{ fontSize: 13, color: "var(--muted)" }}>
                <Icon name="check" size={13} /> Todos os trechos deste dia já foram julgados.
              </span>
            </Card>
          )}
          {itens.map((it) => (
            <ItemCego key={it.id} it={it} proc={proc} dia={dia} />
          ))}
        </div>
      )}
    </div>
  );
}

function Placar({ taxa }: { taxa: NonNullable<ReturnType<typeof Object>> & Record<string, any> }) {
  return (
    <Card style={{ padding: "18px 20px" }}>
      <div className="row gap1" style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
        Taxa de acerto medida
      </div>
      <div className="row gap4 wrap" style={{ marginTop: 10 }}>
        {VEREDITOS.map((v) => (
          <div key={v.id} className="col" style={{ gap: 2, minWidth: 120 }}>
            <span className="tnum font-display" style={{ fontSize: 26, fontWeight: 700, color: v.cor }}>
              {taxa[`${v.id}_pct`].toFixed(0)}%
            </span>
            <span style={{ fontSize: 12, color: "var(--text)" }}>{v.rot}</span>
            <span style={{ fontSize: 11, color: "var(--faint)" }}>
              {taxa[v.id]} de {taxa.n_julgadas}
            </span>
          </div>
        ))}
      </div>
      {/* ⚠️ Poucas julgadas: a taxa oscila demais para valer. Dizer isso é
          parte da medida, não ressalva cosmética. */}
      {!taxa.confiavel && (
        <p style={{ fontSize: 12, color: "var(--muted)", margin: "10px 0 0" }}>
          Ainda são poucos trechos julgados para esta taxa valer como leitura —
          ela oscila muito. Julgue mais alguns.
        </p>
      )}
      {taxa.sem_observacao?.n > 0 && taxa.sem_observacao.bate_pct !== null && (
        <p style={{ fontSize: 12, color: "var(--muted)", margin: "8px 0 0" }}>
          Dos julgados, {taxa.sem_observacao.n} vinham de trechos em que{" "}
          <b>nenhum quadro foi analisado</b> — esses bateram em{" "}
          <b>{taxa.sem_observacao.bate_pct.toFixed(0)}%</b>. Se este número for
          bem menor que o geral, a herança está inventando.
        </p>
      )}
      {taxa.n_pendentes > 0 && (
        <p style={{ fontSize: 11.5, color: "var(--faint)", margin: "8px 0 0" }}>
          {taxa.n_pendentes} trecho(s) ainda sem veredito.
        </p>
      )}
    </Card>
  );
}

function ItemCego({ it, proc, dia }: { it: Item; proc: ProcHeaderMock; dia: string }) {
  const qc = useQueryClient();
  const [resposta, setResposta] = useState("");
  const [revelado, setRevelado] = useState<string | null>(it.descricao);

  const responder = useMutation({
    mutationFn: () => api.amostragem.responder(it.id, resposta),
    onSuccess: (r) => {
      setRevelado(r.descricao || "(sem descrição)");
      qc.invalidateQueries({ queryKey: ["amostragem", proc.id, dia] });
    },
    onError: (e: unknown) => toast(String(e)),
  });
  const julgar = useMutation({
    mutationFn: (v: string) => api.amostragem.veredito(it.id, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["amostragem", proc.id, dia] }),
    onError: (e: unknown) => toast(String(e)),
  });

  const jaJulgado = !!it.veredito;
  const vd = VEREDITOS.find((v) => v.id === it.veredito);

  return (
    <Card style={{ padding: 14, borderLeft: jaJulgado ? `3px solid ${vd?.cor}` : "3px solid var(--line)" }}>
      <FrameStripReal ativo={{ id: it.evento_id, pessoa: 0, label: "", ini: 0, fim: 0 }} />

      {!it.respondido && !revelado ? (
        <div className="col" style={{ gap: 8, marginTop: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
            O que você vê neste trecho?
          </span>
          <input className="field" autoFocus value={resposta}
                 placeholder="Ex.: operador medindo a peça com paquímetro"
                 onChange={(e) => setResposta(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter" && resposta.trim()) responder.mutate(); }} />
          <div className="row gap2">
            <Btn icon="eye" onClick={() => responder.mutate()}
                 disabled={!resposta.trim() || responder.isPending}>
              Responder e revelar
            </Btn>
            <span style={{ fontSize: 11.5, color: "var(--faint)", alignSelf: "center" }}>
              A descrição do sistema só aparece depois desta resposta.
            </span>
          </div>
        </div>
      ) : (
        <div className="col" style={{ gap: 8, marginTop: 10 }}>
          <div className="row gap2 wrap" style={{ fontSize: 12.5 }}>
            <span style={{ color: "var(--muted)" }}>Você viu:</span>
            <b style={{ color: "var(--ink)" }}>{it.resposta_humana || resposta}</b>
          </div>
          <div className="row gap2 wrap" style={{ fontSize: 12.5, alignItems: "baseline" }}>
            <span style={{ color: "var(--muted)" }}>O sistema descreveu:</span>
            <b style={{ color: "var(--ink)" }}>{revelado || it.descricao}</b>
          </div>
          {/* O certificado de origem ao lado da descrição julgada: sem ele,
              não dá para cruzar acerto × observação. */}
          {!it.n_amostras_no_sorteio && (
            <span style={{ fontSize: 11.5, color: leanCor("desp") }}>
              <Icon name="alert-triangle" size={11} /> nenhum quadro deste trecho
              foi analisado — a descrição veio de {it.origem_descricao}
            </span>
          )}
          {jaJulgado ? (
            <span style={{ fontSize: 12.5, fontWeight: 700, color: vd?.cor }}>
              <Icon name="check" size={12} /> {vd?.rot}
            </span>
          ) : (
            <div className="row gap2 wrap">
              {VEREDITOS.map((v) => (
                <Btn key={v.id} onClick={() => julgar.mutate(v.id)}
                     disabled={julgar.isPending} title={v.ajuda}>
                  {v.rot}
                </Btn>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
