// ============================================================
// Login — porte fiel de login.jsx (marca + história animada) + auth real.
// ============================================================
import { FormEvent, useState } from "react";
import { supabase } from "../lib/supabase";
import { Btn, Icon, Prism, toast } from "../design/ui";

function MiniReading({ label, detail, icon }: { label: string; detail: string; icon: string }) {
  return (
    <div className="row" style={{ gap: 9, alignItems: "center" }}>
      <span className="center" style={{ width: 25, height: 25, borderRadius: 8, background: "var(--accent-soft)", color: "var(--accent)", flex: "none" }}>
        <Icon name={icon} size={13} />
      </span>
      <div className="col" style={{ gap: 1 }}>
        <strong style={{ fontSize: 11, color: "var(--text)" }}>{label}</strong>
        <span style={{ fontSize: 9.5, color: "var(--muted)" }}>{detail}</span>
      </div>
    </div>
  );
}

function LatheStationScene() {
  return (
    <div
      className="video-skin"
      role="img"
      aria-label="Ilustração de um único operador sendo observado em um posto de torno mecânico"
      style={{ position: "relative", height: 150, width: "100%" }}
    >
      <svg aria-hidden="true" viewBox="0 0 320 200" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <defs>
          <linearGradient id="lathe-floor" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#1a0f30" stopOpacity="0" />
            <stop offset="1" stopColor="#2a1a52" stopOpacity=".65" />
          </linearGradient>
          <linearGradient id="lathe-body" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#4b3180" />
            <stop offset="1" stopColor="#1a0f30" />
          </linearGradient>
        </defs>
        <rect x="0" y="138" width="320" height="62" fill="url(#lathe-floor)" />
        <g stroke="rgba(167,139,250,.2)" strokeWidth=".6">
          <line x1="0" y1="138" x2="320" y2="138" />
          <line x1="34" y1="200" x2="120" y2="138" />
          <line x1="286" y1="200" x2="202" y2="138" />
        </g>
        <g fill="url(#lathe-body)" stroke="rgba(197,185,245,.35)" strokeWidth="1">
          <rect x="126" y="78" width="150" height="70" rx="5" />
          <rect x="142" y="63" width="72" height="22" rx="4" />
          <circle cx="170" cy="111" r="21" fill="#241543" />
          <circle cx="170" cy="111" r="10" fill="#5d4293" />
          <rect x="224" y="94" width="35" height="13" rx="3" fill="#5d4293" />
        </g>
        <g fill="#090512" transform="translate(92,88)">
          <ellipse cx="0" cy="0" rx="7" ry="8" />
          <rect x="-8" y="8" width="16" height="38" rx="6" />
          <path d="M7 17 L42 28 L39 35 L4 25 Z" />
          <rect x="-7" y="43" width="6" height="31" rx="3" />
          <rect x="2" y="43" width="6" height="31" rx="3" />
        </g>
      </svg>
      <div className="scanlines" />
      <div className="scanbeam" />
      <div className="bbox anim-pop" style={{ left: "20%", top: "31%", width: "25%", height: "50%" }}>
        <span className="bbox-tag">P1 · candidato</span>
        <span className="bbox-act">interação com o torno</span>
      </div>
      <div className="row gap2" style={{ position: "absolute", top: 12, left: 12, fontSize: 10, fontFamily: "var(--mono)" }}>
        <span className="row gap1" style={{ background: "rgba(0,0,0,.6)", color: "rgba(255,255,255,.9)", padding: "4px 8px", borderRadius: 7 }}>
          <span className="live-dot on" style={{ background: "#A78BFA" }} /> LEITURA DO POSTO
        </span>
        <span style={{ background: "rgba(0,0,0,.4)", color: "rgba(255,255,255,.7)", padding: "4px 8px", borderRadius: 7 }}>CAM-TORNO</span>
      </div>
      <div className="row" style={{ position: "absolute", bottom: 0, left: 0, right: 0, justifyContent: "space-between", padding: "8px 12px", fontSize: 10, fontFamily: "var(--mono)", background: "linear-gradient(0deg, rgba(0,0,0,.65), transparent)", color: "rgba(255,255,255,.7)" }}>
        <span>1 candidato · 1 posto</span>
        <span style={{ color: "rgba(255,255,255,.5)" }}>detectar · identificar · decidir</span>
      </div>
    </div>
  );
}

function LoginBrand() {
  return (
    <div style={{ position: "relative", height: "100%", overflow: "hidden", background: "var(--grad-brand)", color: "#fff", padding: "44px 48px", display: "flex", flexDirection: "column" }}>
      <div style={{ position: "absolute", top: -120, right: -120, width: 420, height: 420, borderRadius: "50%", background: "radial-gradient(closest-side, rgba(104,59,237,.55), transparent)" }} />
      <div style={{ position: "absolute", bottom: -140, left: -100, width: 380, height: 380, borderRadius: "50%", background: "radial-gradient(closest-side, rgba(167,139,250,.3), transparent)" }} />
      <div style={{ position: "absolute", inset: 0, opacity: 0.05, backgroundImage: "radial-gradient(#fff 1px, transparent 1px)", backgroundSize: "20px 20px" }} />

      <div className="row gap2" style={{ position: "relative", zIndex: 1 }}>
        <Prism size={46} ring />
        <span className="font-display" style={{ fontWeight: 800, fontSize: 22, letterSpacing: "-.02em" }}>
          Spectra<span style={{ color: "#C5B9F5" }}>AI</span>
        </span>
      </div>

      <div style={{ position: "relative", zIndex: 1, marginTop: 40 }}>
        <span className="eyebrow" style={{ color: "#C5B9F5" }}>
          <span className="live-dot on" style={{ background: "#A78BFA" }} /> Visão computacional para o posto
        </span>
        <h1 className="font-display" style={{ fontSize: 38, lineHeight: 1.05, marginTop: 16, fontWeight: 700, color: "#fff", maxWidth: 460, letterSpacing: "-.02em" }}>
          Saiba se o operador está no posto — e se está <span style={{ background: "linear-gradient(180deg,#fff,#C5B9F5)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>produtivo</span>.
        </h1>
        <p className="pretty" style={{ fontSize: 15.5, lineHeight: 1.6, color: "rgba(255,255,255,.78)", marginTop: 16, maxWidth: 440 }}>
          A captura automática identifica quem ocupa o posto, descreve a cena e transforma as leituras válidas em percentuais simples de presença e produtividade.
        </p>
      </div>

      <div className="float" style={{ position: "relative", zIndex: 1, marginTop: 34, maxWidth: 460 }}>
        <LatheStationScene />
        <div className="card" style={{ position: "absolute", right: -14, bottom: -28, width: 256, padding: "12px 14px", background: "#fff" }}>
          <div className="row gap2" style={{ marginBottom: 10 }}>
            <span className="center" style={{ width: 22, height: 22, borderRadius: 7, background: "var(--accent-soft)", color: "var(--accent)" }}>
              <Icon name="scan-search" size={12} />
            </span>
            <span style={{ fontSize: 12, fontWeight: 700, color: "var(--ink)" }}>Leitura do posto</span>
          </div>
          <div className="col" style={{ gap: 11 }}>
            <MiniReading label="Operador no posto" detail="presente, ausente ou inconclusivo" icon="user-check" />
            <MiniReading label="Produtividade do operador" detail="produtivo, improdutivo ou inconclusivo" icon="gauge" />
          </div>
        </div>
      </div>

      <div className="grow" />
      <div className="row gap3" style={{ position: "relative", zIndex: 1, fontSize: 11.5, color: "rgba(255,255,255,.6)", marginTop: 56 }}>
        <span className="row gap1"><Icon name="camera" size={13} /> Captura automática</span>
        <span className="row gap1"><Icon name="percent" size={13} /> Indicadores em percentuais</span>
        <span className="row gap1"><Icon name="scan-search" size={13} /> Incerteza visível</span>
      </div>
    </div>
  );
}

export default function Login() {
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setLoading(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password: senha });
    setLoading(false);
    if (error) return setErro(error.message);
    toast("Bem-vindo de volta.", { icon: "check" });
  }

  return (
    <div className="login-grid" style={{ minHeight: "100vh", display: "grid", gridTemplateColumns: "1.05fr .95fr", background: "#fff" }}>
      <div className="login-brand">
        <LoginBrand />
      </div>

      <div className="center" style={{ padding: "40px 28px" }}>
        <div style={{ width: "100%", maxWidth: 380 }}>
          <div className="col" style={{ alignItems: "flex-start", gap: 18, marginBottom: 26 }}>
            <Prism size={56} ring />
            <div>
              <h2 className="font-display" style={{ fontSize: 26, fontWeight: 700 }}>Entrar na plataforma</h2>
              <p style={{ fontSize: 14, color: "var(--muted)", marginTop: 6 }}>
                Acesse a visão dos postos monitorados.
              </p>
            </div>
          </div>

          <form onSubmit={submit} className="col" style={{ gap: 15 }}>
              <div>
                <label className="label" htmlFor="login-email">E-mail corporativo</label>
                <div style={{ position: "relative" }}>
                  <Icon name="mail" size={16} color="var(--faint)" style={{ position: "absolute", left: 13, top: "50%", transform: "translateY(-50%)" }} />
                  <input id="login-email" name="email" autoComplete="email" className="field" style={{ paddingLeft: 38 }} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="voce@empresa.com.br" required />
                </div>
              </div>
              <div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <label className="label" htmlFor="login-password">Senha</label>
                </div>
                <div style={{ position: "relative" }}>
                  <Icon name="lock" size={16} color="var(--faint)" style={{ position: "absolute", left: 13, top: "50%", transform: "translateY(-50%)" }} />
                  <input id="login-password" name="password" autoComplete="current-password" className="field" style={{ paddingLeft: 38, paddingRight: 40 }} type={showPw ? "text" : "password"} value={senha} onChange={(e) => setSenha(e.target.value)} placeholder="sua senha" required />
                  <button type="button" onClick={() => setShowPw((v) => !v)} aria-label={showPw ? "Ocultar senha" : "Mostrar senha"} className="center" style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", width: 26, height: 26, border: "none", background: "none", color: "var(--faint)" }}>
                    <Icon name={showPw ? "eye-off" : "eye"} size={16} />
                  </button>
                </div>
              </div>

              {erro && (
                <div role="alert" style={{ fontSize: 13, color: "var(--desp)", background: "var(--desp-bg)", border: "1px solid rgba(229,72,77,.2)", borderRadius: 10, padding: "8px 11px" }}>{erro}</div>
              )}

              <Btn type="submit" size="lg" disabled={loading} className="row" style={{ width: "100%", justifyContent: "center" }}>
                {loading ? (
                  <><span className="spin" style={{ width: 16, height: 16, border: "2px solid rgba(255,255,255,.4)", borderTopColor: "#fff", borderRadius: "50%" }} /> Aguarde…</>
                ) : (
                  <>Entrar <Icon name="arrow-right" size={16} strokeWidth={2.4} /></>
                )}
              </Btn>
          </form>

          <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 22, textAlign: "center" }}>
            O acesso é liberado pela equipe SpectraAI.
          </p>
          <p style={{ fontSize: 11, color: "var(--faint)", marginTop: 26, textAlign: "center" }}>
            © 2026 SpectraAI · Prism™
          </p>
        </div>
      </div>
    </div>
  );
}
