// ============================================================
// Como funciona — tela de ajuda visual e não-técnica.
// Explica a metodologia de aprendizado, o vocabulário (evento,
// comportamento, classificação) e como a validação ajuda o Prism.
// ============================================================
import { Card, Icon, Prism, Eyebrow, MaturityMeter } from "../design/ui";
import { leanCor, leanLabel } from "../design/helpers";
import type { Go } from "../design/Shell";

export default function Ajuda({ go }: { go: Go }) {
  return (
    <div className="col" style={{ gap: 22, maxWidth: 980, margin: "0 auto" }}>
      <Hero />
      <Vocabulario />
      <Ciclo />
      <ComoValida />
      <Maturidade />
      <Sugestoes />
      <Fecho go={go} />
    </div>
  );
}

// ── Hero ────────────────────────────────────────────────────
function Hero() {
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ background: "var(--grad-brand)", padding: "30px 30px 34px", color: "#fff", position: "relative", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: -90, right: -70, width: 300, height: 300, borderRadius: "50%", background: "radial-gradient(closest-side, rgba(167,139,250,.45), transparent)" }} />
        <div className="row gap3" style={{ position: "relative", alignItems: "center" }}>
          <Prism size={62} ring />
          <div style={{ minWidth: 0 }}>
            <span className="eyebrow" style={{ color: "rgba(255,255,255,.8)", background: "rgba(255,255,255,.14)", border: "none" }}>
              <Icon name="sparkles" size={12} /> Como funciona
            </span>
            <h1 className="font-display" style={{ fontSize: 26, fontWeight: 800, color: "#fff", marginTop: 8, letterSpacing: "-.02em" }}>
              O Prism aprende a <i>sua</i> operação
            </h1>
            <p style={{ fontSize: 14.5, color: "rgba(255,255,255,.82)", marginTop: 6, lineHeight: 1.55, maxWidth: 640 }}>
              Pense nele como um colega novo, muito atento. No começo ele observa e dá palpites. Você ensina o que está certo, e a cada vídeo ele entende melhor o seu jeito de trabalhar — até acertar quase tudo sozinho.
            </p>
          </div>
        </div>
      </div>
    </Card>
  );
}

// ── Vocabulário ─────────────────────────────────────────────
function Vocabulario() {
  const itens = [
    {
      icon: "user", cor: "var(--accent)", termo: "Ação (evento)",
      def: "Uma coisa que UMA pessoa fez, num momento específico do vídeo.",
      exemplo: "“empurrou o carrinho” · 9s → 12s",
    },
    {
      icon: "layers", cor: "var(--accent)", termo: "Comportamento",
      def: "O nome que se repete para ações do mesmo tipo. Junta todas as ações parecidas sob um rótulo.",
      exemplo: "“empurrar_carrinho” · 4 ocorrências",
    },
    {
      icon: "tag", cor: "var(--va)", termo: "Classificação",
      def: "Se aquele tempo agrega valor (produtivo) ou não (desperdício) — a leitura de produtividade é binária.",
      exemplo: "produtivo · desperdício",
    },
  ];
  return (
    <section className="col" style={{ gap: 12 }}>
      <Titulo eyebrow="O essencial" texto="O vocabulário, em 1 minuto" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(250px,1fr))", gap: 14 }}>
        {itens.map((it) => (
          <Card key={it.termo} style={{ padding: 18 }}>
            <span className="center" style={{ width: 40, height: 40, borderRadius: 12, background: "var(--soft)", color: it.cor, marginBottom: 12 }}><Icon name={it.icon} size={20} /></span>
            <h3 className="font-display" style={{ fontSize: 16, fontWeight: 700, color: "var(--ink)" }}>{it.termo}</h3>
            <p style={{ fontSize: 13, color: "var(--text)", lineHeight: 1.5, marginTop: 5 }}>{it.def}</p>
            <div className="row gap1" style={{ marginTop: 12, padding: "7px 10px", borderRadius: 9, background: "var(--soft)", border: "1px solid var(--line)" }}>
              <Icon name="corner-down-right" size={13} color="var(--faint)" />
              <span className="font-mono" style={{ fontSize: 11, color: "var(--muted)" }}>{it.exemplo}</span>
            </div>
          </Card>
        ))}
      </div>
      {/* relação entre os três */}
      <Card style={{ padding: "14px 18px" }}>
        <div className="row gap2 wrap" style={{ justifyContent: "center", alignItems: "center", fontSize: 13, color: "var(--text)" }}>
          <span className="row gap1"><b style={{ color: "var(--ink)" }}>Muitas ações</b> parecidas</span>
          <Icon name="arrow-right" size={16} color="var(--accent)" />
          <span className="row gap1">viram um <b style={{ color: "var(--ink)" }}>comportamento</b></span>
          <Icon name="arrow-right" size={16} color="var(--accent)" />
          <span className="row gap1">que recebe uma <b style={{ color: "var(--ink)" }}>classificação</b></span>
        </div>
        <div className="row gap2 wrap" style={{ justifyContent: "center", marginTop: 12 }}>
          {(["va", "desp"] as const).map((c) => (
            <span key={c} className="row gap1" style={{ fontSize: 12, fontWeight: 600, color: "var(--text)", padding: "4px 11px", borderRadius: 99, border: "1px solid var(--line)", background: "#fff" }}>
              <i style={{ width: 9, height: 9, borderRadius: 3, background: leanCor(c) }} /> {leanLabel(c)}
            </span>
          ))}
        </div>
      </Card>
    </section>
  );
}

// ── Ciclo de aprendizado ────────────────────────────────────
function Ciclo() {
  const passos = [
    { icon: "eye", titulo: "Observa", txt: "O Prism assiste aos vídeos e dá um primeiro palpite sobre cada ação." },
    { icon: "git-pull-request-arrow", titulo: "Você valida", txt: "Na tela de Validação você confirma, corrige ou descarta os palpites." },
    { icon: "brain", titulo: "Aprende", txt: "Cada decisão sua vira memória — o jeito certo de nomear na sua operação." },
    { icon: "trending-up", titulo: "Melhora", txt: "No próximo vídeo ele já acerta sozinho o que você ensinou." },
  ];
  return (
    <section className="col" style={{ gap: 12 }}>
      <Titulo eyebrow="A metodologia" texto="O ciclo de aprendizado" />
      <Card style={{ padding: 20 }}>
        <div className="row gap2 wrap" style={{ alignItems: "stretch", justifyContent: "center" }}>
          {passos.map((p, i) => (
            <div key={p.titulo} className="row gap2" style={{ alignItems: "center" }}>
              <div className="col" style={{ alignItems: "center", textAlign: "center", width: 168, gap: 8 }}>
                <span className="center" style={{ width: 52, height: 52, borderRadius: 16, background: "var(--accent-soft)", color: "var(--accent)", position: "relative" }}>
                  <Icon name={p.icon} size={24} />
                  <span className="center" style={{ position: "absolute", top: -6, right: -6, width: 20, height: 20, borderRadius: 99, background: "var(--grad-cta)", color: "#fff", fontSize: 11, fontWeight: 700 }}>{i + 1}</span>
                </span>
                <span className="font-display" style={{ fontSize: 14.5, fontWeight: 700, color: "var(--ink)" }}>{p.titulo}</span>
                <span style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.45 }}>{p.txt}</span>
              </div>
              {i < passos.length - 1 && <Icon name="arrow-right" size={20} color="var(--p-200)" />}
            </div>
          ))}
        </div>
        <div className="row gap2" style={{ justifyContent: "center", marginTop: 16, padding: "10px 14px", borderRadius: 12, background: "var(--soft)", border: "1px solid var(--line)" }}>
          <Prism size={24} ring />
          <span style={{ fontSize: 13, color: "var(--text)", fontWeight: 600 }}>Quanto mais você ensina, <span style={{ color: "var(--accent)" }}>menos eventos chegam até você</span> — ele passa a confirmar sozinho.</span>
        </div>
      </Card>
    </section>
  );
}

// ── Como a validação ajuda ──────────────────────────────────
function ComoValida() {
  const acoes = [
    { icon: "check", cor: "var(--va)", bg: "var(--va-bg)", titulo: "Confirmar", txt: "Você diz que o Prism acertou. Isso reforça o aprendizado — ele fica mais confiante naquele tipo de ação." },
    { icon: "pencil", cor: "var(--accent)", bg: "var(--accent-soft)", titulo: "Corrigir", txt: "Você dá o nome certo da sua operação. Ele para de errar e usa o seu vocabulário dali pra frente." },
    { icon: "x", cor: "var(--desp)", bg: "var(--desp-bg)", titulo: "Descartar", txt: "Era um falso alarme. Ele aprende a não marcar mais coisas parecidas." },
    { icon: "message-circle", cor: "var(--accent)", bg: "var(--accent-soft)", titulo: "Responder o Prism", txt: "Quando ele tem dúvida, pergunta. Sua resposta vira contexto permanente e melhora as próximas análises." },
    // Fase 80: a quinta ação não é uma decisão — é a única que só OLHA. Existe
    // porque nem tudo passa pela fila: um dia inteiro de posto vazio sai dela
    // por mecanismo e ficaria invisível sem alguém abrir para conferir.
    { icon: "search", cor: "var(--muted)", bg: "var(--soft)", titulo: "Auditar um dia", txt: "Nem tudo passa pela fila: um dia inteiro de posto vazio sai dela sozinho. Na Auditoria do dia você abre um dia qualquer e confere por amostragem — sem julgar nada, só para ver se a leitura bate com a realidade." },
  ];
  return (
    <section className="col" style={{ gap: 12 }}>
      <Titulo eyebrow="O seu papel" texto="Como a sua validação ajuda" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))", gap: 14 }}>
        {acoes.map((a) => (
          <Card key={a.titulo} style={{ padding: 16 }}>
            <div className="row gap2" style={{ marginBottom: 8 }}>
              <span className="center" style={{ width: 34, height: 34, borderRadius: 10, background: a.bg, color: a.cor, flex: "none" }}><Icon name={a.icon} size={17} strokeWidth={2.4} /></span>
              <span className="font-display" style={{ fontSize: 15, fontWeight: 700, color: "var(--ink)" }}>{a.titulo}</span>
            </div>
            <p style={{ fontSize: 12.5, color: "var(--text)", lineHeight: 1.5 }}>{a.txt}</p>
          </Card>
        ))}
      </div>
    </section>
  );
}

// ── Maturidade ──────────────────────────────────────────────
function Maturidade() {
  return (
    <section className="col" style={{ gap: 12 }}>
      <Titulo eyebrow="O termômetro" texto="A maturidade do Prism" />
      <Card style={{ padding: 20 }}>
        <div className="row gap4 wrap" style={{ alignItems: "center" }}>
          <MaturityMeter pct={72} size={92} />
          <div className="grow" style={{ minWidth: 240 }}>
            <p style={{ fontSize: 13.5, color: "var(--text)", lineHeight: 1.55 }}>
              É o quanto o Prism já <b style={{ color: "var(--ink)" }}>conhece daquela linha</b>. Começa baixo e sobe sozinho conforme você usa o sistema:
            </p>
            <ul className="col" style={{ gap: 7, listStyle: "none", padding: 0, margin: "12px 0 0" }}>
              {[
                ["video", "Quanto mais vídeos analisados"],
                ["check-check", "Quanto mais eventos você valida"],
                ["tag", "Quanto mais comportamentos classificados"],
                ["message-circle", "Quanto mais perguntas do Prism respondidas"],
              ].map(([ic, t]) => (
                <li key={t} className="row gap2" style={{ fontSize: 12.5, color: "var(--text)" }}>
                  <Icon name={ic} size={15} color="var(--va)" /> {t}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Card>
    </section>
  );
}

// ── Sugestões ───────────────────────────────────────────────
function Sugestoes() {
  return (
    <section className="col" style={{ gap: 12 }}>
      <Titulo eyebrow="O resultado" texto="As sugestões de produtividade" />
      <Card style={{ padding: 18 }}>
        <p style={{ fontSize: 13.5, color: "var(--text)", lineHeight: 1.55 }}>
          Com base no que observa, o Prism aponta <b style={{ color: "var(--ink)" }}>oportunidades de melhoria</b> na operação. Você fica no controle do que fazer com cada uma:
        </p>
        <div className="row gap3 wrap" style={{ marginTop: 14 }}>
          <div className="row gap2" style={{ alignItems: "flex-start", flex: "1 1 260px" }}>
            <span className="center" style={{ width: 32, height: 32, borderRadius: 9, background: "var(--va-bg)", color: "var(--va)", flex: "none" }}><Icon name="check" size={16} strokeWidth={2.5} /></span>
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>Marcar como realizada</div>
              <p style={{ fontSize: 12.5, color: "var(--muted)", lineHeight: 1.45, marginTop: 2 }}>Quando você aplicou a melhoria. Ela sai da lista. Se o problema voltar num próximo vídeo, o Prism avisa que <b style={{ color: "var(--desp)" }}>não foi cumprida</b>.</p>
            </div>
          </div>
          <div className="row gap2" style={{ alignItems: "flex-start", flex: "1 1 260px" }}>
            <span className="center" style={{ width: 32, height: 32, borderRadius: 9, background: "var(--line-2)", color: "var(--muted)", flex: "none" }}><Icon name="x" size={16} /></span>
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 700, color: "var(--ink)" }}>Dispensar</div>
              <p style={{ fontSize: 12.5, color: "var(--muted)", lineHeight: 1.45, marginTop: 2 }}>Quando não faz sentido pra você. Ela some da lista e dá espaço para o que importa.</p>
            </div>
          </div>
        </div>
      </Card>
    </section>
  );
}

// ── Fecho ───────────────────────────────────────────────────
function Fecho({ go }: { go: Go }) {
  return (
    <Card style={{ padding: 20, background: "linear-gradient(120deg, var(--soft), #fff 70%)" }}>
      <div className="row gap3 wrap" style={{ alignItems: "center", justifyContent: "space-between" }}>
        <div className="row gap2" style={{ alignItems: "center", minWidth: 0 }}>
          <Prism size={36} ring />
          <span style={{ fontSize: 14, color: "var(--ink)", fontWeight: 600 }}>Pronto. Agora é só ensinar o Prism — ele cuida do resto.</span>
        </div>
        <button onClick={() => go("processos")} className="btn btn-primary row gap2">
          <Icon name="layout-grid" size={16} /> Ver meus processos
        </button>
      </div>
    </Card>
  );
}

// ── Título de seção ─────────────────────────────────────────
function Titulo({ eyebrow, texto }: { eyebrow: string; texto: string }) {
  return (
    <div className="col" style={{ gap: 6 }}>
      <Eyebrow>{eyebrow}</Eyebrow>
      <h2 className="font-display" style={{ fontSize: 19, fontWeight: 700, color: "var(--ink)" }}>{texto}</h2>
    </div>
  );
}
