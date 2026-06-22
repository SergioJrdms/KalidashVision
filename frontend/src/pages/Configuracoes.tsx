// ============================================================
// Configurações do processo — por enquanto só "Turnos de gravação".
// Os turnos definem QUANDO a borda (Raspberry Pi) grava o RTSP da câmera
// daquele processo. A pausa de almoço é o GAP entre intervalos consecutivos.
// ============================================================
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Btn, Card, Icon, Empty, Eyebrow, PanelHead, Modal, toast } from "../design/ui";
import type { Go } from "../design/Shell";
import type { ProcHeaderMock } from "../lib/adapt";
import type { TurnoProcesso, IntervaloTurno } from "../lib/types";

const DIAS = [
  { n: 1, abrev: "Seg", longo: "Segunda" },
  { n: 2, abrev: "Ter", longo: "Terça" },
  { n: 3, abrev: "Qua", longo: "Quarta" },
  { n: 4, abrev: "Qui", longo: "Quinta" },
  { n: 5, abrev: "Sex", longo: "Sexta" },
  { n: 6, abrev: "Sáb", longo: "Sábado" },
  { n: 7, abrev: "Dom", longo: "Domingo" },
];

const PRESET_TURNO_PADRAO = {
  nome: "Turno 1",
  intervalos: [
    { inicio: "07:00", fim: "12:00" },
    { inicio: "13:00", fim: "17:00" },
  ] as IntervaloTurno[],
  dias_semana: [1, 2, 3, 4, 5],
  ativo: true,
};

export default function Configuracoes({ proc }: { proc: ProcHeaderMock; go: Go }) {
  return (
    <div className="col" style={{ gap: 22, maxWidth: 920, margin: "0 auto" }}>
      <div className="col" style={{ gap: 6 }}>
        <Eyebrow icon="settings">Configurações do processo</Eyebrow>
        <h1 className="font-display" style={{ fontSize: 24, fontWeight: 700 }}>{proc.nome}</h1>
        <p style={{ fontSize: 13.5, color: "var(--muted)", lineHeight: 1.5, maxWidth: 640 }}>
          Ajustes que valem só para esta linha. As alterações são lidas pelo Pi
          local que captura as imagens — então afetam diretamente quando e como
          os vídeos chegam à plataforma.
        </p>
      </div>

      <TurnosBloco proc={proc} />
    </div>
  );
}

// ── Bloco: turnos de gravação ────────────────────────────────
function TurnosBloco({ proc }: { proc: ProcHeaderMock }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["turnos", proc.id], queryFn: () => api.turnos.listar(proc.id) });
  const [novo, setNovo] = useState(false);
  const turnos = q.data || [];

  function invalidate() { qc.invalidateQueries({ queryKey: ["turnos", proc.id] }); }

  return (
    <Card style={{ padding: 22 }}>
      <PanelHead
        titulo="Turnos de gravação"
        ajuda="Define quando a câmera grava. Cada turno tem dias da semana e um ou mais intervalos de horário; a pausa (ex.: almoço) é o intervalo entre dois blocos. A borda (Pi) só captura dentro desses horários."
        leitura="A borda lê este horário para iniciar e parar a gravação automaticamente."
        right={<Btn icon="plus" size="sm" onClick={() => setNovo(true)}>Novo turno</Btn>}
      />

      {q.isLoading ? (
        <Empty icon="loader" title="Carregando turnos…" />
      ) : turnos.length === 0 ? (
        <Empty
          icon="calendar-clock"
          title="Nenhum turno cadastrado"
          desc="Crie o primeiro para definir os horários em que a câmera deve gravar. Sem turnos, a borda não saberá quando capturar."
          action={<Btn icon="plus" onClick={() => setNovo(true)}>Criar primeiro turno</Btn>}
        />
      ) : (
        <div className="col" style={{ gap: 12 }}>
          {turnos.map((t) => (
            <TurnoCard key={t.id} turno={t} proc={proc} onSaved={invalidate} />
          ))}
        </div>
      )}

      {novo && (
        <NovoTurnoModal
          proc={proc}
          existentes={turnos.length}
          onClose={() => setNovo(false)}
          onCreated={() => { setNovo(false); invalidate(); }}
        />
      )}
    </Card>
  );
}

// ── Card de turno (edição inline) ────────────────────────────
function TurnoCard({ turno, proc, onSaved }: { turno: TurnoProcesso; proc: ProcHeaderMock; onSaved: () => void }) {
  const [nome, setNome] = useState(turno.nome);
  const [intervalos, setIntervalos] = useState<IntervaloTurno[]>(turno.intervalos);
  const [dias, setDias] = useState<number[]>(turno.dias_semana);
  const [ativo, setAtivo] = useState<boolean>(turno.ativo);
  const [excluir, setExcluir] = useState(false);

  useEffect(() => {
    setNome(turno.nome);
    setIntervalos(turno.intervalos);
    setDias(turno.dias_semana);
    setAtivo(turno.ativo);
  }, [turno.id, turno.nome, turno.intervalos, turno.dias_semana, turno.ativo]);

  const dirty = useMemo(() => {
    return (
      nome.trim() !== turno.nome
      || ativo !== turno.ativo
      || JSON.stringify(intervalos) !== JSON.stringify(turno.intervalos)
      || JSON.stringify([...dias].sort()) !== JSON.stringify([...turno.dias_semana].sort())
    );
  }, [nome, intervalos, dias, ativo, turno]);

  const erro = validarTurnoCliente({ nome, intervalos, dias_semana: dias });

  const salvar = useMutation({
    mutationFn: () => api.turnos.atualizar(turno.id, { nome: nome.trim(), intervalos, dias_semana: dias, ativo }),
    onSuccess: () => { toast("Turno atualizado.", { icon: "check" }); onSaved(); },
    onError: (e: Error) => toast(`Falha: ${e.message}`, { icon: "alert-triangle", color: "#F8B4B6" }),
  });
  const excluirM = useMutation({
    mutationFn: () => api.turnos.excluir(turno.id),
    onSuccess: () => { toast("Turno excluído.", { icon: "check" }); setExcluir(false); onSaved(); },
    onError: (e: Error) => toast(`Falha: ${e.message}`, { icon: "alert-triangle", color: "#F8B4B6" }),
  });

  return (
    <Card flat style={{ padding: 16, borderColor: ativo ? "var(--line)" : "var(--line-2)", opacity: ativo ? 1 : 0.7 }}>
      <div className="row gap2" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        <input className="field" value={nome} onChange={(e) => setNome(e.target.value)} placeholder="Nome do turno" style={{ maxWidth: 260, fontWeight: 600 }} />
        <span className="grow" />
        <label className="row gap1" style={{ fontSize: 12.5, color: "var(--muted)", cursor: "pointer" }}>
          <input type="checkbox" checked={ativo} onChange={(e) => setAtivo(e.target.checked)} style={{ accentColor: "var(--accent)" }} />
          {ativo ? "Ativo" : "Inativo"}
        </label>
      </div>

      <DiasSelector value={dias} onChange={setDias} />

      <IntervalosEditor value={intervalos} onChange={setIntervalos} />

      {erro && (
        <p className="row gap1" style={{ fontSize: 12, color: "var(--desp)", marginTop: 8 }}>
          <Icon name="alert-triangle" size={13} /> {erro}
        </p>
      )}

      <div className="row gap2" style={{ marginTop: 14, justifyContent: "space-between", flexWrap: "wrap" }}>
        <button onClick={() => setExcluir(true)} className="row gap1" style={{ border: "none", background: "transparent", color: "var(--desp)", fontSize: 12.5, fontWeight: 600, padding: "6px 0" }}>
          <Icon name="trash-2" size={14} /> Excluir turno
        </button>
        <Btn icon="save" disabled={!dirty || !!erro || salvar.isPending} onClick={() => salvar.mutate()} size="sm">
          {salvar.isPending ? "Salvando…" : "Salvar alterações"}
        </Btn>
      </div>

      {excluir && (
        <Modal onClose={() => setExcluir(false)} width={420}>
          <div className="row gap2" style={{ marginBottom: 10 }}>
            <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--desp-bg)", color: "var(--desp)" }}>
              <Icon name="alert-triangle" size={18} />
            </span>
            <h3 className="font-display" style={{ fontSize: 17, fontWeight: 700 }}>Excluir turno?</h3>
          </div>
          <p style={{ fontSize: 13.5, color: "var(--text)", lineHeight: 1.5 }}>
            Você está prestes a excluir <b>{turno.nome}</b> do processo <b>{proc.nome}</b>. Os horários definidos aqui deixam de valer
            para a borda no próximo ciclo de captura.
          </p>
          <div className="row gap2" style={{ justifyContent: "flex-end", marginTop: 18 }}>
            <Btn variant="ghost" onClick={() => setExcluir(false)}>Cancelar</Btn>
            <Btn variant="danger" icon="trash-2" disabled={excluirM.isPending} onClick={() => excluirM.mutate()}>
              {excluirM.isPending ? "Excluindo…" : "Excluir"}
            </Btn>
          </div>
        </Modal>
      )}
    </Card>
  );
}

// ── Modal "Novo turno" ───────────────────────────────────────
function NovoTurnoModal({ proc, existentes, onClose, onCreated }: { proc: ProcHeaderMock; existentes: number; onClose: () => void; onCreated: () => void }) {
  const [nome, setNome] = useState(existentes === 0 ? PRESET_TURNO_PADRAO.nome : `Turno ${existentes + 1}`);
  const [intervalos, setIntervalos] = useState<IntervaloTurno[]>(PRESET_TURNO_PADRAO.intervalos);
  const [dias, setDias] = useState<number[]>(PRESET_TURNO_PADRAO.dias_semana);

  const erro = validarTurnoCliente({ nome, intervalos, dias_semana: dias });

  const criar = useMutation({
    mutationFn: () => api.turnos.criar(proc.id, { nome: nome.trim(), intervalos, dias_semana: dias, ativo: true }),
    onSuccess: () => { toast("Turno criado.", { icon: "check" }); onCreated(); },
    onError: (e: Error) => toast(`Falha: ${e.message}`, { icon: "alert-triangle", color: "#F8B4B6" }),
  });

  return (
    <Modal onClose={onClose} width={540}>
      <div className="row gap2" style={{ marginBottom: 10 }}>
        <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: "var(--accent-soft)", color: "var(--accent)" }}>
          <Icon name="calendar-plus" size={18} />
        </span>
        <h3 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>Novo turno</h3>
      </div>
      <p style={{ fontSize: 12.5, color: "var(--muted)", margin: "4px 0 14px" }}>
        Defina o nome, em que dias da semana e em quais horários a câmera deve gravar. A pausa de almoço
        é o intervalo entre dois blocos (ex.: 07:00–12:00 e 13:00–17:00).
      </p>

      <label className="label">Nome</label>
      <input className="field" autoFocus value={nome} onChange={(e) => setNome(e.target.value)} placeholder="Ex.: Turno 1, Manhã, Diurno…" />

      <div style={{ marginTop: 14 }}>
        <DiasSelector value={dias} onChange={setDias} />
      </div>

      <div style={{ marginTop: 14 }}>
        <IntervalosEditor value={intervalos} onChange={setIntervalos} />
      </div>

      {erro && (
        <p className="row gap1" style={{ fontSize: 12.5, color: "var(--desp)", marginTop: 10 }}>
          <Icon name="alert-triangle" size={13} /> {erro}
        </p>
      )}

      <div className="row gap2" style={{ justifyContent: "flex-end", marginTop: 18 }}>
        <Btn variant="ghost" onClick={onClose}>Cancelar</Btn>
        <Btn icon="check" disabled={!!erro || criar.isPending} onClick={() => criar.mutate()}>
          {criar.isPending ? "Criando…" : "Criar turno"}
        </Btn>
      </div>
    </Modal>
  );
}

// ── Subcomponentes ───────────────────────────────────────────
function DiasSelector({ value, onChange }: { value: number[]; onChange: (v: number[]) => void }) {
  function toggle(n: number) {
    onChange(value.includes(n) ? value.filter((x) => x !== n) : [...value, n].sort());
  }
  function preset(dias: number[]) { onChange(dias); }
  return (
    <div>
      <div className="row gap2" style={{ marginBottom: 6, alignItems: "baseline", flexWrap: "wrap" }}>
        <span className="label" style={{ margin: 0 }}>Dias da semana</span>
        <span className="grow" />
        <button onClick={() => preset([1, 2, 3, 4, 5])} style={{ border: "none", background: "transparent", color: "var(--accent)", fontSize: 12, fontWeight: 600, padding: 0, cursor: "pointer" }}>Seg–Sex</button>
        <span style={{ color: "var(--faint)" }}>·</span>
        <button onClick={() => preset([1, 2, 3, 4, 5, 6])} style={{ border: "none", background: "transparent", color: "var(--accent)", fontSize: 12, fontWeight: 600, padding: 0, cursor: "pointer" }}>Seg–Sáb</button>
        <span style={{ color: "var(--faint)" }}>·</span>
        <button onClick={() => preset([1, 2, 3, 4, 5, 6, 7])} style={{ border: "none", background: "transparent", color: "var(--accent)", fontSize: 12, fontWeight: 600, padding: 0, cursor: "pointer" }}>Todos</button>
      </div>
      <div className="row gap1 wrap">
        {DIAS.map((d) => {
          const on = value.includes(d.n);
          return (
            <button
              key={d.n}
              onClick={() => toggle(d.n)}
              title={d.longo}
              style={{
                minWidth: 46, padding: "6px 10px", borderRadius: 99,
                fontSize: 12.5, fontWeight: 600,
                border: on ? "1px solid var(--accent)" : "1px solid var(--line)",
                background: on ? "var(--accent)" : "#fff",
                color: on ? "#fff" : "var(--text)",
                transition: "background .15s, color .15s, border-color .15s",
              }}
            >
              {d.abrev}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function IntervalosEditor({ value, onChange }: { value: IntervaloTurno[]; onChange: (v: IntervaloTurno[]) => void }) {
  function set(idx: number, campo: "inicio" | "fim", v: string) {
    const cp = value.map((it, i) => (i === idx ? { ...it, [campo]: v } : it));
    onChange(cp);
  }
  function add() { onChange([...value, { inicio: "13:00", fim: "17:00" }]); }
  function rm(idx: number) { onChange(value.filter((_, i) => i !== idx)); }

  return (
    <div>
      <div className="row gap2" style={{ marginBottom: 6, alignItems: "baseline" }}>
        <span className="label" style={{ margin: 0 }}>Intervalos de funcionamento</span>
        <span className="grow" />
        <button onClick={add} className="row gap1" style={{ border: "none", background: "transparent", color: "var(--accent)", fontSize: 12, fontWeight: 600, padding: 0, cursor: "pointer" }}>
          <Icon name="plus" size={13} /> Adicionar intervalo
        </button>
      </div>
      {value.length === 0 ? (
        <p style={{ fontSize: 12.5, color: "var(--muted)", padding: "8px 0" }}>
          Sem intervalos definidos. Adicione pelo menos um para a câmera saber quando gravar.
        </p>
      ) : (
        <div className="col" style={{ gap: 8 }}>
          {value.map((it, i) => (
            <div key={i} className="row gap2" style={{ alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ fontSize: 11.5, color: "var(--faint)", fontWeight: 600, minWidth: 18 }}>{i + 1}.</span>
              <input type="time" className="field" value={it.inicio} onChange={(e) => set(i, "inicio", e.target.value)} style={{ maxWidth: 130 }} />
              <span style={{ color: "var(--faint)" }}>→</span>
              <input type="time" className="field" value={it.fim} onChange={(e) => set(i, "fim", e.target.value)} style={{ maxWidth: 130 }} />
              <span className="grow" />
              <button onClick={() => rm(i)} title="Remover intervalo" className="center" style={{ width: 30, height: 30, borderRadius: 8, border: "1px solid var(--line)", background: "#fff", color: "var(--muted)" }}>
                <Icon name="x" size={14} />
              </button>
            </div>
          ))}
          {value.length >= 2 && (
            <p style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>
              <Icon name="info" size={11} />{" "}
              A pausa (ex.: almoço) é o intervalo entre dois blocos consecutivos.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Validação client-side (espelha o backend; UX só) ─────────
function validarTurnoCliente({ nome, intervalos, dias_semana }: { nome: string; intervalos: IntervaloTurno[]; dias_semana: number[] }): string | null {
  if (!nome.trim()) return "Dê um nome ao turno.";
  if (dias_semana.length === 0) return "Escolha pelo menos um dia da semana.";
  if (intervalos.length === 0) return "Adicione pelo menos um intervalo.";
  const min = (s: string) => {
    const [h, m] = s.split(":").map(Number);
    return h * 60 + m;
  };
  const valido = (s: string) => /^([01]\d|2[0-3]):[0-5]\d$/.test(s);
  for (const it of intervalos) {
    if (!valido(it.inicio) || !valido(it.fim)) return "Horário inválido (use o formato HH:MM).";
    if (min(it.inicio) >= min(it.fim)) return `Intervalo ${it.inicio}–${it.fim}: o início precisa ser antes do fim.`;
  }
  const ord = [...intervalos].sort((a, b) => min(a.inicio) - min(b.inicio));
  for (let i = 1; i < ord.length; i++) {
    if (min(ord[i - 1].fim) > min(ord[i].inicio)) {
      return `Intervalos sobrepostos: ${ord[i - 1].inicio}–${ord[i - 1].fim} e ${ord[i].inicio}–${ord[i].fim}.`;
    }
  }
  return null;
}
