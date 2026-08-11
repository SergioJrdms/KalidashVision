// ============================================================
// Fase 91 — QUEM DOMINOU O POSTO. Tela de CONFERÊNCIA, não de decisão.
//
// O princípio: o titular não é quem está na zona num instante — é quem DOMINA
// a presença na zona ao longo do dia. Instante é ruído (o líder passa, o
// colega encosta); domínio é regime.
//
// Esta tela existe para uma pergunta só, e é o dono que responde: os grupos
// são pessoas de verdade ou viraram sopa? Sem a imagem ao lado do número não
// dá para dizer, e é essa resposta que decide se a identificação um dia pode
// mexer no número de produtividade. Até lá, SOMBRA: nada aqui altera papel,
// evento ou métrica.
//
// IDENTIDADE ANÔNIMA POR PAPEL: `g1`/`g2` valem para UM dia e UMA câmera. Não
// há nome, não há cadastro, não há re-identificação persistente entre dias.
// É decisão de LGPD, não de conveniência — para medir o POSTO basta saber que
// o mesmo alguém dominou o dia.
// ============================================================
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, PanelHead, Empty, Icon } from "../design/ui";
import { leanCor } from "../design/helpers";
import type { ProcHeaderMock } from "../lib/adapt";
import type { CameraTitular, GrupoTitular } from "../lib/types";

function hoje(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);          // o passe roda sobre ONTEM
  return d.toISOString().slice(0, 10);
}

export default function Titular({ proc }: { proc: ProcHeaderMock }) {
  const [dia, setDia] = useState(hoje());
  const q = useQuery({
    queryKey: ["titular", proc.id, dia],
    queryFn: () => api.titular.dia(proc.id, dia),
  });
  const d = q.data;

  return (
    <div className="col" style={{ gap: 16 }}>
      <Card style={{ padding: 20 }}>
        <PanelHead
          titulo="Quem dominou o posto"
          ajuda="O titular não é quem está na zona num instante — é quem DOMINA a presença na zona ao longo do dia. Os tracks são agrupados por aparência (cor primeiro, que é o único sinal robusto quando o track tem 8 segundos), por CÂMERA e por DIA, porque cam1 e cam2 não são a mesma régua."
          leitura="Olhe os recortes lado a lado: cada grupo é uma pessoa, ou o agrupamento virou sopa? É essa resposta que decide se isto um dia pode mexer no número."
        />
        <div className="row gap2 wrap" style={{ alignItems: "center", marginTop: 4 }}>
          <label style={{ fontSize: 12.5, color: "var(--muted)" }}>Dia</label>
          <input type="date" value={dia} onChange={(e) => setDia(e.target.value)}
                 style={{ border: "1px solid var(--line)", borderRadius: 8,
                          padding: "5px 9px", fontSize: 13 }} />
          {d && (
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {d.n_descritores} track(s) descritos
            </span>
          )}
        </div>
        <div style={{ marginTop: 10, padding: "8px 11px", background: "var(--soft)",
                      border: "1px solid var(--line-2)", borderRadius: 8,
                      fontSize: 11.5, color: "var(--muted)", lineHeight: 1.55 }}>
          <b style={{ color: "var(--ink)" }}>Modo sombra.</b> Nada aqui muda papel,
          evento ou métrica de produtividade. Os rótulos <code className="font-mono">g1</code>,{" "}
          <code className="font-mono">g2</code> são posicionais e valem para um dia e uma
          câmera — <b>não são pessoas</b>, não há cadastro nem reconhecimento entre dias.
        </div>
      </Card>

      {q.isLoading && <Empty icon="loader" title="Agrupando os descritores…" />}
      {!q.isLoading && !d && (
        <Empty icon="alert-triangle" title="Não foi possível carregar"
               desc={q.error ? String((q.error as Error).message || q.error) : undefined} />
      )}

      {d && (d.continuidade || []).length > 0 && (
        // ALERTA, nunca correção automática. Mudança de titular pode ser troca
        // de turno, férias, camisa nova ou erro do agrupamento — e o sistema
        // não distingue nenhuma dessas. Quem decide é gente.
        <Card style={{ padding: 14, borderLeft: "3px solid var(--apoio)" }}>
          <div className="col" style={{ gap: 6 }}>
            <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--apoio)" }}>
              <Icon name="alert-triangle" size={13} /> Continuidade entre dias
            </span>
            {d.continuidade.map((a) => (
              <span key={a.cam_id} style={{ fontSize: 12, color: "var(--text)" }}>
                <b>{a.cam_id}</b>: {a.alerta}
              </span>
            ))}
          </div>
        </Card>
      )}

      {d && d.cameras.length === 0 && (
        <Empty icon="calendar-days" title="Nenhum descritor neste dia"
               desc="Os descritores passaram a ser gravados na Fase 83. Escolha um dia posterior." />
      )}

      {d && d.cameras.map((c) => <BlocoCamera key={c.cam_id} c={c} />)}

      {d && (
        <span style={{ fontSize: 10.5, color: "var(--faint)", lineHeight: 1.5 }}>{d.nota}</span>
      )}
    </div>
  );
}

function BlocoCamera({ c }: { c: CameraTitular }) {
  const semTitular = !c.titular;
  return (
    <Card style={{ padding: 20 }}>
      <div className="row gap2 wrap" style={{ alignItems: "baseline", marginBottom: 10 }}>
        <span className="font-mono" style={{ fontSize: 14, fontWeight: 700 }}>{c.cam_id}</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          {c.n_tracks} track(s) → {c.n_grupos} grupo(s) · {c.minutos_posto_total.toFixed(0)} min no posto
        </span>
        <span className="grow" />
        {semTitular ? (
          // Dia sem titular NÃO é falha: é a guarda de piso dizendo que ninguém
          // dominou. Coroar um intruso no dia em que o operador faltou seria
          // pior que não ter resposta.
          <span style={{ fontSize: 11.5, fontWeight: 700, color: "var(--apoio)" }}>
            SEM TITULAR — {c.motivo}
          </span>
        ) : (
          <span style={{ fontSize: 11.5, fontWeight: 700, color: leanCor("va") }}>
            titular: <code className="font-mono">{c.titular}</code> · {c.motivo}
          </span>
        )}
      </div>
      <div className="row wrap" style={{ gap: 12 }}>
        {c.grupos.map((g) => <CartaoGrupo key={g.grupo} g={g} />)}
      </div>
    </Card>
  );
}

function CartaoGrupo({ g }: { g: GrupoTitular }) {
  const cor = g.eh_titular ? leanCor("va") : "var(--line)";
  return (
    <div className="col" style={{
      gap: 5, width: 148, padding: 9, borderRadius: 10,
      border: `2px solid ${cor}`, background: g.eh_titular ? "var(--soft)" : "#fff",
    }}>
      <div style={{ width: "100%", height: 150, borderRadius: 7, overflow: "hidden",
                    background: "var(--line-2)", display: "flex",
                    alignItems: "center", justifyContent: "center" }}>
        {g.recorte
          ? <img src={g.recorte} alt={`recorte do grupo ${g.grupo}`}
                 style={{ width: "100%", height: "100%", objectFit: "cover" }} />
          : <span style={{ fontSize: 10.5, color: "var(--faint)", textAlign: "center", padding: 6 }}>
              sem recorte<br />(frame não aquecido)
            </span>}
      </div>
      <div className="row gap1" style={{ alignItems: "baseline" }}>
        <code className="font-mono" style={{ fontSize: 12, fontWeight: 700 }}>{g.grupo}</code>
        {g.eh_titular && (
          <span style={{ fontSize: 9.5, fontWeight: 700, color: leanCor("va"),
                         border: `1px solid ${leanCor("va")}`, borderRadius: 99, padding: "0 5px" }}>
            TITULAR
          </span>
        )}
      </div>
      <span className="tnum" style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>
        {g.minutos_posto.toFixed(0)} min no posto
      </span>
      <span style={{ fontSize: 11, color: "var(--muted)" }}>
        {g.pct_do_posto.toFixed(0)}% do tempo · {g.n_tracks} track(s)
      </span>
      {g.n_tracks === 1 && (
        // Grupo de 1 é o sintoma de fragmentação: ou é alguém que passou uma
        // vez, ou o agrupamento não conseguiu ligar este track a nenhum outro.
        <span style={{ fontSize: 10, color: "var(--faint)" }}>
          track solto — pode ser fragmentação
        </span>
      )}
    </div>
  );
}
