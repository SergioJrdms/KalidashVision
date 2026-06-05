import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { Btn, Icon, Wordmark, toast } from "../components/UIKit";
import { PrismAvatar } from "../components/PrismAvatar";

type Modo = "entrar" | "cadastro";

export default function Login() {
  const [modo, setModo] = useState<Modo>("entrar");
  return (
    <div className="row" style={{ minHeight: "100vh", background: "var(--app-bg)" }}>
      <PainelHistoria />
      <PainelForm modo={modo} setModo={setModo} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Painel esquerdo — "a história do aprendizado"
// ════════════════════════════════════════════════════════════════════════
function PainelHistoria() {
  return (
    <div
      className="col hidden lg:flex"
      style={{
        flex: "1 1 0",
        minWidth: 0,
        background: "var(--grad-brand)",
        color: "#fff",
        padding: "44px 56px",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        className="col"
        style={{ flex: 1, justifyContent: "center", gap: 24, maxWidth: 480, margin: "auto" }}
      >
        <div className="row gap2">
          <PrismAvatar size={42} ring />
          <span className="font-display" style={{ fontSize: 19, fontWeight: 800 }}>
            Kalidash Vision
          </span>
        </div>
        <h1
          className="font-display"
          style={{ fontSize: 38, lineHeight: 1.1, fontWeight: 800, color: "#fff" }}
        >
          O Prism aprende a sua operação assistindo aos vídeos.
        </h1>
        <p style={{ fontSize: 15, lineHeight: 1.55, color: "rgba(255,255,255,.82)" }}>
          A cada turno processado, ele descobre comportamentos, mede o tempo e te
          ajuda a transformar desperdício em valor agregado. Você ensina, ele
          memoriza, a operação melhora.
        </p>
        <div className="col gap3">
          <Recurso icone="video" t="Vídeo da operação" d="Você sobe um vídeo do dia a dia da linha." />
          <Recurso icone="brain" t="Prism aprende" d="Detecta pessoas, descreve ações e organiza por comportamento." />
          <Recurso icone="trending-up" t="Insights crescem" d="Sugestões Lean priorizadas — alinhadas ao seu jeito de operar." />
        </div>
      </div>
    </div>
  );
}

function Recurso({ icone, t, d }: { icone: string; t: string; d: string }) {
  return (
    <div className="row gap3">
      <span
        style={{
          width: 38,
          height: 38,
          borderRadius: 10,
          background: "rgba(255,255,255,.12)",
          border: "1px solid rgba(255,255,255,.18)",
          display: "grid",
          placeItems: "center",
          flex: "none",
        }}
      >
        <Icon name={icone} size={18} color="#fff" />
      </span>
      <div>
        <div style={{ fontWeight: 700 }}>{t}</div>
        <div style={{ fontSize: 13, color: "rgba(255,255,255,.78)" }}>{d}</div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════
// Painel direito — formulário
// ════════════════════════════════════════════════════════════════════════
function PainelForm({ modo, setModo }: { modo: Modo; setModo: (m: Modo) => void }) {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [nome, setNome] = useState("");
  const [empresa, setEmpresa] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [okCadastro, setOkCadastro] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setLoading(true);
    if (modo === "entrar") {
      const { error } = await supabase.auth.signInWithPassword({ email, password: senha });
      setLoading(false);
      if (error) return setErro(error.message);
      toast("Bem-vindo de volta.", { icon: "check", color: "#3EE6AE" });
      nav("/processos");
    } else {
      if (!empresa.trim()) { setLoading(false); return setErro("Informe o nome da empresa."); }
      const { data, error } = await supabase.auth.signUp({
        email,
        password: senha,
        options: { data: { nome: nome.trim(), empresa: empresa.trim() } },
      });
      setLoading(false);
      if (error) return setErro(error.message);
      if (data.session) nav("/processos");
      else setOkCadastro(true);
    }
  }

  if (okCadastro) {
    return (
      <div className="center" style={{ flex: 1, padding: 36 }}>
        <div className="card" style={{ maxWidth: 440, padding: 28, textAlign: "center" }}>
          <Wordmark size={18} />
          <h2 className="font-display" style={{ fontSize: 22, fontWeight: 700, marginTop: 16 }}>
            Conta criada
          </h2>
          <p style={{ color: "var(--muted)", marginTop: 6 }}>
            Confirme seu e-mail (se necessário) e faça login para começar.
          </p>
          <Btn className="mt-4" onClick={() => { setOkCadastro(false); setModo("entrar"); }}>
            Ir para o login
          </Btn>
        </div>
      </div>
    );
  }

  return (
    <div className="center" style={{ flex: "1 1 0", padding: 36, minWidth: 0 }}>
      <div className="card" style={{ width: "100%", maxWidth: 420, padding: 28 }}>
        <Wordmark size={18} />
        <h2 className="font-display" style={{ fontSize: 24, fontWeight: 700, marginTop: 18 }}>
          {modo === "entrar" ? "Entrar" : "Criar conta"}
        </h2>
        <p style={{ color: "var(--muted)", fontSize: 13.5, marginTop: 4 }}>
          {modo === "entrar"
            ? "Acesse seus processos e análises de produtividade."
            : "O nome da empresa define seu contexto isolado de análise."}
        </p>

        <form onSubmit={submit} className="col gap3" style={{ marginTop: 18 }}>
          {modo === "cadastro" && (
            <>
              <Campo label="Seu nome" value={nome} onChange={setNome} autoFocus />
              <Campo label="Nome da empresa" value={empresa} onChange={setEmpresa} placeholder="Ex.: Metalúrgica Alfa" />
            </>
          )}
          <Campo
            label="E-mail"
            type="email"
            value={email}
            onChange={setEmail}
            autoFocus={modo === "entrar"}
            autoComplete="email"
          />
          <Campo
            label="Senha"
            type="password"
            value={senha}
            onChange={setSenha}
            autoComplete={modo === "entrar" ? "current-password" : "new-password"}
            minLength={modo === "cadastro" ? 6 : undefined}
          />
          {erro && (
            <div
              style={{
                fontSize: 13,
                color: "var(--desp)",
                background: "var(--desp-bg)",
                border: "1px solid rgba(229,72,77,.2)",
                borderRadius: 10,
                padding: "8px 11px",
              }}
            >
              {erro}
            </div>
          )}
          <Btn type="submit" disabled={loading} className="mt-1">
            {loading ? "Aguarde..." : modo === "entrar" ? "Entrar" : "Criar conta"}
          </Btn>
        </form>

        <p style={{ marginTop: 18, fontSize: 13.5, color: "var(--muted)", textAlign: "center" }}>
          {modo === "entrar" ? (
            <>
              Não tem conta?{" "}
              <button
                onClick={() => { setErro(null); setModo("cadastro"); }}
                style={{ color: "var(--accent-deep)", fontWeight: 700, background: "none", border: 0 }}
              >
                Criar uma agora
              </button>
            </>
          ) : (
            <>
              Já tem conta?{" "}
              <button
                onClick={() => { setErro(null); setModo("entrar"); }}
                style={{ color: "var(--accent-deep)", fontWeight: 700, background: "none", border: 0 }}
              >
                Entrar
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}

function Campo({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  autoFocus,
  autoComplete,
  minLength,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  autoFocus?: boolean;
  autoComplete?: string;
  minLength?: number;
}) {
  return (
    <label className="col" style={{ gap: 6 }}>
      <span className="label">{label}</span>
      <input
        className="field"
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoFocus={autoFocus}
        autoComplete={autoComplete}
        minLength={minLength}
        required
      />
    </label>
  );
}
