// ============================================================
// Fase 52 — Saúde da borda (o Pi que grava este processo).
//
// A ideia que organiza a tela: OFFLINE NÃO É SEMPRE PROBLEMA. Às 22h, no
// domingo ou no almoço, o Pi DEVE estar parado. Um painel que pisca vermelho
// fora do turno ensina o cliente a ignorar o alerta — e aí ele ignora o alerta
// de verdade. Por isso o estado nunca é "online/offline": é o OBSERVADO contra
// o ESPERADO (o turno). Só "sem sinal DENTRO do turno" é vermelho.
//
// Todo o cálculo mora no backend (GET /processos/{id}/saude). Aqui só pintamos.
// ============================================================
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, Icon, PanelHead } from "../design/ui";
import { tempoRelativo } from "../design/helpers";
import type { ProcHeaderMock } from "../lib/adapt";
import type { EstadoSaude, SaudeCamera, SaudeEdge } from "../lib/types";

// Paleta por estado. Repouso usa o roxo suave da plataforma de propósito:
// tem que ler como "quieto", nunca como defeito.
const TOM: Record<EstadoSaude, { cor: string; bg: string; icone: string }> = {
  capturando: { cor: "var(--va)", bg: "var(--va-bg)", icone: "video" },
  em_repouso: { cor: "var(--accent)", bg: "var(--accent-soft)", icone: "moon" },
  sem_sinal: { cor: "var(--desp)", bg: "var(--desp-bg)", icone: "alert-triangle" },
  sem_captura: { cor: "var(--desp)", bg: "var(--desp-bg)", icone: "video-off" },
  sem_dados: { cor: "var(--muted)", bg: "var(--soft)", icone: "plug" },
};

function minutosPara(txt: string): string {
  const m = Math.max(0, Math.round(Number(txt) || 0));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h}h${String(r).padStart(2, "0")}` : `${h}h`;
}

/** "há 47 minutos" a partir de segundos — a tela nunca mostra ISO. */
function duracaoLonga(seg: number | null): string {
  if (seg == null) return "—";
  const min = Math.round(seg / 60);
  if (min < 1) return "menos de 1 minuto";
  if (min < 60) return `${min} minuto${min > 1 ? "s" : ""}`;
  const h = Math.floor(min / 60);
  const r = min % 60;
  if (h < 24) return r ? `${h}h${String(r).padStart(2, "0")}` : `${h} hora${h > 1 ? "s" : ""}`;
  const d = Math.round(h / 24);
  return `${d} dia${d > 1 ? "s" : ""}`;
}

export function SaudeBloco({ proc }: { proc: ProcHeaderMock }) {
  const q = useQuery({
    queryKey: ["saude", proc.id],
    queryFn: () => api.saude.obter(proc.id),
    // Atualiza sozinha sem piscar: o react-query mantém `data` durante o
    // refetch de fundo, então só mostramos "carregando" na PRIMEIRA carga.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    retry: false,
  });

  if (q.isLoading) {
    return (
      <Card style={{ padding: 22 }}>
        <PanelHead titulo="Saúde da captura" ajuda={AJUDA} />
        <p style={{ fontSize: 13, color: "var(--muted)", margin: 0 }}>Consultando o Pi…</p>
      </Card>
    );
  }

  // Erro de rede não pode virar drama: o painel é diagnóstico, não o produto.
  if (q.isError || !q.data) {
    return (
      <Card style={{ padding: 22 }}>
        <PanelHead titulo="Saúde da captura" ajuda={AJUDA} />
        <p style={{ fontSize: 13, color: "var(--muted)", margin: 0 }}>
          Não consegui consultar o estado da captura agora. A gravação no Pi não é
          afetada por isso — tento de novo em 30 segundos.
        </p>
      </Card>
    );
  }

  const s = q.data;
  const tom = TOM[s.estado] || TOM.sem_dados;

  return (
    <Card style={{ padding: 22 }}>
      <PanelHead
        titulo="Saúde da captura"
        ajuda={AJUDA}
        leitura="Fora do turno, parado é o esperado — só alarma se faltar sinal DENTRO do horário."
        right={
          s.device_id ? (
            <span
              className="font-mono"
              title={`Aparelho ${s.device_id}${s.runner_versao ? ` · runner v${s.runner_versao}` : ""}`}
              style={{ fontSize: 11, color: "var(--faint)" }}
            >
              {s.device_id}
            </span>
          ) : undefined
        }
      />

      {s.estado === "sem_dados" ? (
        <VazioConvite />
      ) : (
        <div className="col" style={{ gap: 18 }}>
          <Destaque s={s} tom={tom} />
          <Cameras cameras={s.cameras} />
          {s.disco && <Armazenamento disco={s.disco} />}
          <FaixaCobertura s={s} />
        </div>
      )}
    </Card>
  );
}

const AJUDA =
  "Mostra se o Pi que grava este processo está de pé. O estado compara o que " +
  "foi observado com o que era esperado pelo turno: fora do horário, parado é " +
  "normal e aparece como 'em repouso'. Vermelho só quando falta sinal dentro " +
  "do turno. Atualiza sozinho a cada 30 segundos.";

// ── Vazio é convite, não desculpa ────────────────────────────────
function VazioConvite() {
  return (
    <div
      className="col"
      style={{
        gap: 6, padding: "22px 20px", borderRadius: 12,
        border: "1px dashed var(--line)", background: "var(--soft)",
      }}
    >
      <span className="row" style={{ gap: 8, alignItems: "center", fontSize: 14.5, fontWeight: 700, color: "var(--ink)" }}>
        <Icon name="plug" size={16} /> Nenhum sinal recebido ainda
      </span>
      <p style={{ fontSize: 13, color: "var(--muted)", margin: 0, lineHeight: 1.55, maxWidth: 560 }}>
        O painel liga assim que o Pi enviar o primeiro sinal. Se a captura já
        deveria estar rodando, confira se o aparelho está ligado e conectado à
        rede da fábrica.
      </p>
    </div>
  );
}

// ── 1 · Estado, em destaque ──────────────────────────────────────
function Destaque({ s, tom }: { s: SaudeEdge; tom: { cor: string; bg: string; icone: string } }) {
  let frase = "";
  let contexto = "";

  if (s.estado === "capturando") {
    frase = "Capturando agora";
    contexto = s.turno.ativa
      ? `Turno ${s.turno.ativa.nome} · janela ${s.turno.ativa.inicio}–${s.turno.ativa.fim}`
      : "Gravação em andamento";
  } else if (s.estado === "em_repouso") {
    frase = s.turno.proxima ? `Em repouso até ${s.turno.proxima.inicio}` : "Em repouso";
    contexto = s.turno.proxima
      ? `Próxima janela começa em ${minutosPara(String(s.turno.proxima.em_min))}`
      : s.turno.configurado
        ? "Fora do horário de captura — nada a fazer"
        : "Nenhum turno configurado. Cadastre um abaixo para o Pi saber quando gravar.";
  } else if (s.estado === "sem_captura") {
    // O Pi responde, mas não está gravando. Problema DIFERENTE de "sem sinal":
    // o aparelho está de pé, quem morreu foi a gravação.
    frase = "Parado dentro do turno";
    contexto = s.turno.ativa
      ? `O Pi está respondendo, mas não está gravando (janela ${s.turno.ativa.inicio}–${s.turno.ativa.fim}). ` +
        "Reinicie a captura no aparelho."
      : "O Pi está respondendo, mas a gravação não está rodando.";
  } else {
    frase = `Sem sinal há ${duracaoLonga(s.idade_s)}`;
    contexto = s.turno.ativa
      ? `Deveria estar gravando agora (janela ${s.turno.ativa.inicio}–${s.turno.ativa.fim}). ` +
        "Verifique a energia do Pi e o cabo de rede."
      : "O Pi parou de responder dentro do horário de captura.";
  }

  return (
    <div
      className="row"
      style={{
        gap: 14, alignItems: "center", padding: "16px 18px", borderRadius: 12,
        background: tom.bg, borderLeft: `3px solid ${tom.cor}`,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 38, height: 38, borderRadius: 10, background: "#fff",
          display: "grid", placeItems: "center", color: tom.cor, flex: "none",
        }}
      >
        <Icon name={tom.icone} size={19} />
      </span>
      <div className="col" style={{ gap: 3, minWidth: 0 }}>
        <span className="font-display" style={{ fontSize: 19, fontWeight: 700, color: tom.cor, lineHeight: 1.2 }}>
          {frase}
        </span>
        <span style={{ fontSize: 12.5, color: "var(--muted)", lineHeight: 1.45 }}>{contexto}</span>
      </div>
      {s.ultimo_heartbeat_em && (
        <span
          className="font-mono"
          title={new Date(s.ultimo_heartbeat_em).toLocaleString("pt-BR")}
          style={{ marginLeft: "auto", fontSize: 11, color: "var(--faint)", flex: "none", whiteSpace: "nowrap" }}
        >
          sinal {tempoRelativo(s.ultimo_heartbeat_em)}
        </span>
      )}
    </div>
  );
}

// ── 2 · Câmeras ──────────────────────────────────────────────────
function Cameras({ cameras }: { cameras: SaudeCamera[] }) {
  if (!cameras.length) return null;
  return (
    <div className="col" style={{ gap: 8 }}>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
        Câmeras
      </span>
      <div className="row wrap" style={{ gap: 10 }}>
        {cameras.map((c) => {
          const tom = TOM[c.estado] || TOM.sem_dados;
          const rotulo =
            c.estado === "capturando" ? "Gravando"
              : c.estado === "em_repouso" ? "Em repouso"
                : c.estado === "sem_dados" ? "Sem dados"
                  : c.estado === "sem_captura" ? "Parada" : "Sem sinal";
          return (
            <div
              key={c.cam_id}
              className="col"
              style={{
                gap: 5, flex: "1 1 220px", minWidth: 200, padding: "12px 14px",
                borderRadius: 10, border: "1px solid var(--line)",
                background: c.estado === "sem_sinal" ? tom.bg : "#fff",
              }}
            >
              <div className="row" style={{ gap: 7, alignItems: "center" }}>
                <span aria-hidden style={{ width: 8, height: 8, borderRadius: 99, background: tom.cor, flex: "none" }} />
                <span style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.nome}
                </span>
              </div>
              <span style={{ fontSize: 12, fontWeight: 600, color: tom.cor }}>{rotulo}</span>
              <span
                className="font-mono"
                title={c.ultimo_segmento_em ? new Date(c.ultimo_segmento_em).toLocaleString("pt-BR") : undefined}
                style={{ fontSize: 11, color: "var(--faint)" }}
              >
                {c.ultimo_segmento_em ? `último trecho ${tempoRelativo(c.ultimo_segmento_em)}` : "nenhum trecho ainda"}
              </span>
              {c.estado === "sem_sinal" && (
                <span style={{ fontSize: 11.5, color: "var(--desp)", lineHeight: 1.45 }}>
                  Verifique a alimentação e o cabo de rede desta câmera.
                </span>
              )}
              {c.falhas > 0 && c.estado !== "sem_sinal" && (
                <span style={{ fontSize: 11, color: "var(--muted)" }}>
                  {c.falhas} reconexão{c.falhas > 1 ? "ões" : ""} desde o último sinal
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 3 · Armazenamento ────────────────────────────────────────────
function Armazenamento({ disco }: { disco: NonNullable<SaudeEdge["disco"]> }) {
  const uso = disco.uso_pct ?? null;
  const apertado = (uso ?? 0) >= 85 || (disco.dias_restantes != null && disco.dias_restantes <= 2);
  const cor = apertado ? "var(--desp)" : uso != null && uso >= 70 ? "var(--apoio)" : "var(--va)";
  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
          Armazenamento do Pi
        </span>
        <span className="font-mono" style={{ fontSize: 12, color: "var(--text)" }}>
          {disco.livre_gb.toFixed(1)} GB livres{uso != null ? ` · ${uso.toFixed(0)}% usado` : ""}
        </span>
      </div>
      <div className="track" style={{ height: 10, borderRadius: 99, overflow: "hidden", background: "var(--line-2)" }}>
        <i
          style={{
            display: "block", height: "100%", borderRadius: 99, background: cor,
            width: `${Math.min(100, Math.max(2, uso ?? 0))}%`,
          }}
        />
      </div>
      <span style={{ fontSize: 11.5, color: apertado ? "var(--desp)" : "var(--muted)", lineHeight: 1.45 }}>
        {disco.dias_restantes != null
          ? `No ritmo atual, o cartão enche em cerca de ${disco.dias_restantes} dia${disco.dias_restantes > 1 ? "s" : ""}.` +
            (apertado ? " Libere espaço antes que a gravação pare." : "")
          : "Espaço estável — a limpeza automática está dando conta do ritmo de gravação."}
      </span>
    </div>
  );
}

// ── 4 · A faixa de cobertura (o elemento assinatura) ─────────────
// O FUNDO desenha o turno (o esperado). O PREENCHIMENTO desenha os sinais
// recebidos (o real). Buraco dentro de uma janela = falha visível num relance.
// Fora da janela = simplesmente vazio, sem alarme.
function FaixaCobertura({ s }: { s: SaudeEdge }) {
  const blocos = s.cobertura_24h || [];
  if (!blocos.length) return null;

  const horas: { pos: number; hora: string }[] = [];
  blocos.forEach((b, i) => {
    const d = new Date(b.inicio);
    if (d.getMinutes() === 0 && d.getHours() % 6 === 0) {
      horas.push({ pos: (i / blocos.length) * 100, hora: `${String(d.getHours()).padStart(2, "0")}h` });
    }
  });

  const buracos = blocos.filter((b) => b.esperado && !b.houve).length;
  const minutosFalt = buracos * 15;

  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--muted)" }}>
          Últimas 24 horas
        </span>
        <span style={{ fontSize: 11.5, color: buracos ? "var(--desp)" : "var(--muted)" }}>
          {!s.turno.configurado
            ? "sem turno configurado"
            : buracos === 0
              ? "cobertura completa no turno"
              : `${minutosFalt >= 60 ? `${Math.floor(minutosFalt / 60)}h${String(minutosFalt % 60).padStart(2, "0")}` : `${minutosFalt} min`} sem sinal dentro do turno`}
        </span>
      </div>

      <div
        role="img"
        aria-label={
          buracos === 0
            ? "Faixa das últimas 24 horas: cobertura completa dentro do turno."
            : `Faixa das últimas 24 horas: ${minutosFalt} minutos sem sinal dentro do turno.`
        }
        className="row"
        style={{ gap: 1, height: 34, alignItems: "stretch", borderRadius: 8, overflow: "hidden", background: "var(--soft)" }}
      >
        {blocos.map((b, i) => {
          const d = new Date(b.inicio);
          const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
          // esperado + houve  → verde (gravou como devia)
          // esperado + faltou → vermelho (o buraco que importa)
          // fora do turno     → cinza clarinho se houve pulso, vazio se não
          const fundo = b.esperado
            ? b.houve ? "var(--va)" : "var(--desp-bg)"
            : b.houve ? "var(--line)" : "transparent";
          const borda = b.esperado && !b.houve ? "inset 0 0 0 1px var(--desp)" : undefined;
          return (
            <span
              key={b.inicio}
              title={`${hhmm} · ${b.esperado ? (b.houve ? "gravando" : "SEM SINAL (deveria gravar)") : b.houve ? "Pi ligado, fora do turno" : "fora do turno"}`}
              style={{ flex: 1, background: fundo, boxShadow: borda, minWidth: 0 }}
            />
          );
        })}
      </div>

      <div style={{ position: "relative", height: 13 }}>
        {horas.map((h) => (
          <span
            key={h.hora + h.pos}
            className="font-mono"
            style={{ position: "absolute", left: `${h.pos}%`, fontSize: 9.5, color: "var(--faint)" }}
          >
            {h.hora}
          </span>
        ))}
      </div>

      <div className="row wrap" style={{ gap: 12, fontSize: 11, color: "var(--muted)" }}>
        <span className="row" style={{ gap: 5 }}>
          <i style={{ width: 9, height: 9, borderRadius: 2, background: "var(--va)" }} /> gravou
        </span>
        <span className="row" style={{ gap: 5 }}>
          <i style={{ width: 9, height: 9, borderRadius: 2, background: "var(--desp-bg)", boxShadow: "inset 0 0 0 1px var(--desp)" }} /> faltou (dentro do turno)
        </span>
        <span className="row" style={{ gap: 5 }}>
          <i style={{ width: 9, height: 9, borderRadius: 2, background: "var(--line)" }} /> fora do turno
        </span>
      </div>
    </div>
  );
}
