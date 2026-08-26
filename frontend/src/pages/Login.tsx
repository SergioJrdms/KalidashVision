// ============================================================
// Login — porte fiel de login.jsx (marca + história animada) + auth real.
// ============================================================
import { FormEvent, useEffect, useState } from "react";
import { supabase } from "../lib/supabase";
import { Btn, Icon, Prism, CameraScene, toast } from "../design/ui";

function MiniDiscovery({ label, group, target, delay }: { label: string; group: string; target: number; delay: number }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    let raf = 0;
    let t0 = 0;
    const start = performance.now() + delay;
    const step = (t: number) => {
      if (t < start) { raf = requestAnimationFrame(step); return; }
      if (!t0) t0 = t;
      const p = Math.min(1, (t - t0) / 1400);
      setV(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, delay]);
  const settled = v >= target - 0.5;
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", gap: 6 }}>
        <span className="truncate" style={{ fontSize: 11, color: "var(--text)" }}>“{label}”</span>
        <span className="font-mono tnum" style={{ fontSize: 10, color: settled ? "var(--va)" : "var(--muted)" }}>{Math.round(v)}%</span>
      </div>
      <div className="row gap1" style={{ marginTop: 3 }}>
        <span style={{ fontSize: 9.5, color: "var(--accent)", fontWeight: 600 }}>{group}</span>
        <div className="grow track" style={{ height: 5 }}>
          <i style={{ width: `${v}%`, background: settled ? "linear-gradient(90deg,#34D399,#10B981)" : "var(--grad-cta)", transition: "width .2s" }} />
        </div>
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
          <span className="live-dot on" style={{ background: "#A78BFA" }} /> Prism · inteligência que aprende
        </span>
        <h1 className="font-display" style={{ fontSize: 38, lineHeight: 1.05, marginTop: 16, fontWeight: 700, color: "#fff", maxWidth: 460, letterSpacing: "-.02em" }}>
          A inteligência que <span style={{ background: "linear-gradient(180deg,#fff,#C5B9F5)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>aprende a sua operação</span>.
        </h1>
        <p className="pretty" style={{ fontSize: 15.5, lineHeight: 1.6, color: "rgba(255,255,255,.78)", marginTop: 16, maxWidth: 440 }}>
          As câmeras já instaladas alimentam o Prism. Ele descobre os comportamentos do seu chão de fábrica, pergunta quando tem dúvida e fica mais preciso a cada vídeo.
        </p>
      </div>

      <div className="float" style={{ position: "relative", zIndex: 1, marginTop: 34, maxWidth: 460 }}>
        <CameraScene height={150} hud />
        <div className="card" style={{ position: "absolute", right: -14, bottom: -28, width: 256, padding: "12px 14px", background: "#fff" }}>
          <div className="row gap2" style={{ marginBottom: 10 }}>
            <span className="center" style={{ width: 22, height: 22, borderRadius: 7, background: "var(--accent-soft)", color: "var(--accent)" }}>
              <Icon name="scan-search" size={12} />
            </span>
            <span style={{ fontSize: 12, fontWeight: 700, color: "var(--ink)" }}>Comportamentos descobertos</span>
          </div>
          <div className="col" style={{ gap: 11 }}>
            <MiniDiscovery label="empurra carrinho até a prensa" group="movimentação" target={96} delay={300} />
            <MiniDiscovery label="parado, aguardando ciclo" group="espera" target={74} delay={700} />
          </div>
        </div>
      </div>

      <div className="grow" />
      <div className="row gap3" style={{ position: "relative", zIndex: 1, fontSize: 11.5, color: "rgba(255,255,255,.6)", marginTop: 56 }}>
        <span className="row gap1"><Icon name="shield-check" size={13} /> LGPD-ready</span>
        <span className="row gap1"><Icon name="brain-circuit" size={13} /> Aprende a cada vídeo</span>
        <span className="row gap1"><Icon name="lock" size={13} /> Dados isolados por empresa</span>
      </div>
    </div>
  );
}

export default function Login() {
  const [modo, setModo] = useState<"entrar" | "cadastro">("entrar");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [nome, setNome] = useState("");
  const [empresa, setEmpresa] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [okCad, setOkCad] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setLoading(true);
    if (modo === "entrar") {
      const { error } = await supabase.auth.signInWithPassword({ email, password: senha });
      setLoading(false);
      if (error) return setErro(error.message);
      toast("Bem-vindo de volta.", { icon: "check" });
    } else {
      if (!empresa.trim()) { setLoading(false); return setErro("Informe o nome da empresa."); }
      const { data, error } = await supabase.auth.signUp({ email, password: senha, options: { data: { nome: nome.trim(), empresa: empresa.trim() } } });
      setLoading(false);
      if (error) return setErro(error.message);
      if (!data.session) setOkCad(true);
    }
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
              <h2 className="font-display" style={{ fontSize: 26, fontWeight: 700 }}>{modo === "entrar" ? "Entrar na plataforma" : "Criar sua conta"}</h2>
              <p style={{ fontSize: 14, color: "var(--muted)", marginTop: 6 }}>
                {modo === "entrar" ? "Acesse seus processos e o que o Prism já aprendeu." : "O nome da empresa define seu contexto isolado de análise."}
              </p>
            </div>
          </div>

          {okCad ? (
            <div className="card" style={{ padding: 22, textAlign: "center" }}>
              <h3 className="font-display" style={{ fontSize: 18, fontWeight: 700 }}>Conta criada</h3>
              <p style={{ color: "var(--muted)", fontSize: 13.5, marginTop: 6 }}>Confirme seu e-mail (se necessário) e faça login.</p>
              <Btn style={{ marginTop: 16 }} onClick={() => { setOkCad(false); setModo("entrar"); }}>Ir para o login</Btn>
            </div>
          ) : (
            <form onSubmit={submit} className="col" style={{ gap: 15 }}>
              {modo === "cadastro" && (
                <>
                  <div>
                    <label className="label">Seu nome</label>
                    <input className="field" value={nome} onChange={(e) => setNome(e.target.value)} placeholder="Como podemos te chamar" required />
                  </div>
                  <div>
                    <label className="label">Nome da empresa</label>
                    <input className="field" value={empresa} onChange={(e) => setEmpresa(e.target.value)} placeholder="Ex.: Metalúrgica Aurora" required />
                  </div>
                </>
              )}
              <div>
                <label className="label">E-mail corporativo</label>
                <div style={{ position: "relative" }}>
                  <Icon name="mail" size={16} color="var(--faint)" style={{ position: "absolute", left: 13, top: "50%", transform: "translateY(-50%)" }} />
                  <input className="field" style={{ paddingLeft: 38 }} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="voce@empresa.com.br" required />
                </div>
              </div>
              <div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <label className="label">Senha</label>
                  {modo === "entrar" && <a href="#" onClick={(e) => e.preventDefault()} style={{ fontSize: 12, color: "var(--accent)", fontWeight: 600 }}>Esqueci a senha</a>}
                </div>
                <div style={{ position: "relative" }}>
                  <Icon name="lock" size={16} color="var(--faint)" style={{ position: "absolute", left: 13, top: "50%", transform: "translateY(-50%)" }} />
                  <input className="field" style={{ paddingLeft: 38, paddingRight: 40 }} type={showPw ? "text" : "password"} value={senha} onChange={(e) => setSenha(e.target.value)} placeholder="sua senha" required minLength={modo === "cadastro" ? 6 : undefined} />
                  <button type="button" onClick={() => setShowPw((v) => !v)} className="center" style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", width: 26, height: 26, border: "none", background: "none", color: "var(--faint)" }}>
                    <Icon name={showPw ? "eye-off" : "eye"} size={16} />
                  </button>
                </div>
              </div>

              {erro && (
                <div style={{ fontSize: 13, color: "var(--desp)", background: "var(--desp-bg)", border: "1px solid rgba(229,72,77,.2)", borderRadius: 10, padding: "8px 11px" }}>{erro}</div>
              )}

              <Btn type="submit" size="lg" disabled={loading} className="row" style={{ width: "100%", justifyContent: "center" }}>
                {loading ? (
                  <><span className="spin" style={{ width: 16, height: 16, border: "2px solid rgba(255,255,255,.4)", borderTopColor: "#fff", borderRadius: "50%" }} /> Aguarde…</>
                ) : (
                  <>{modo === "entrar" ? "Entrar" : "Criar conta"} <Icon name="arrow-right" size={16} strokeWidth={2.4} /></>
                )}
              </Btn>
            </form>
          )}

          {!okCad && (
            <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 22, textAlign: "center" }}>
              {modo === "entrar" ? (
                <>Ainda não tem acesso? <a href="#" onClick={(e) => { e.preventDefault(); setErro(null); setModo("cadastro"); }} style={{ color: "var(--accent)", fontWeight: 600 }}>Criar conta</a></>
              ) : (
                <>Já tem conta? <a href="#" onClick={(e) => { e.preventDefault(); setErro(null); setModo("entrar"); }} style={{ color: "var(--accent)", fontWeight: 600 }}>Entrar</a></>
              )}
            </p>
          )}
          <p style={{ fontSize: 11, color: "var(--faint)", marginTop: 26, textAlign: "center" }}>
            © 2026 SpectraAI · Prism™ · Seus dados nunca treinam modelos de terceiros.
          </p>
        </div>
      </div>
    </div>
  );
}
