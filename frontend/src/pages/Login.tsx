import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { Button, Card, Input } from "../components/UI";

export default function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handle(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setLoading(true);
    const { error } = await supabase.auth.signInWithPassword({ email, password: senha });
    setLoading(false);
    if (error) {
      setErro(error.message);
      return;
    }
    nav("/processos");
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-kv-purple-50 to-kv-indigo-bg px-4">
      <Card className="w-full max-w-md p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-kv-purple to-kv-purple-dark flex items-center justify-center text-white font-bold text-lg">
            K
          </div>
          <h1 className="text-xl font-semibold text-slate-900">Kalidash Vision</h1>
        </div>
        <h2 className="text-2xl font-semibold text-slate-900 mb-1">Entrar</h2>
        <p className="text-sm text-slate-500 mb-6">
          Acesse seus processos e análises de produtividade.
        </p>
        <form onSubmit={handle} className="space-y-4">
          <Input
            label="E-mail"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
          />
          <Input
            label="Senha"
            type="password"
            required
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
            autoComplete="current-password"
          />
          {erro && (
            <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
              {erro}
            </div>
          )}
          <Button type="submit" disabled={loading} className="w-full">
            {loading ? "Entrando..." : "Entrar"}
          </Button>
        </form>
        <p className="text-sm text-slate-500 mt-6 text-center">
          Não tem conta?{" "}
          <Link to="/cadastro" className="text-kv-purple-dark font-medium hover:underline">
            Criar uma agora
          </Link>
        </p>
      </Card>
    </div>
  );
}
