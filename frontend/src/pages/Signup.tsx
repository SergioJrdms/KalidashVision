import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { Button, Card, Input } from "../components/UI";

export default function Signup() {
  const nav = useNavigate();
  const [nome, setNome] = useState("");
  const [empresa, setEmpresa] = useState("");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handle(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    if (!empresa.trim()) {
      setErro("Informe o nome da empresa.");
      return;
    }
    setLoading(true);
    const { data, error } = await supabase.auth.signUp({
      email,
      password: senha,
      options: {
        data: {
          nome: nome.trim(),
          empresa: empresa.trim(),
        },
      },
    });
    setLoading(false);
    if (error) {
      setErro(error.message);
      return;
    }
    if (data.session) {
      nav("/processos");
    } else {
      setOk(true);
    }
  }

  if (ok) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-kv-purple-50 to-kv-indigo-bg px-4">
        <Card className="w-full max-w-md p-8 text-center">
          <h2 className="text-xl font-semibold text-slate-900 mb-2">Conta criada</h2>
          <p className="text-slate-600 text-sm mb-6">
            Confirme seu e-mail (se necessário) e faça login para começar.
          </p>
          <Link to="/login">
            <Button>Ir para o login</Button>
          </Link>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-kv-purple-50 to-kv-indigo-bg px-4 py-10">
      <Card className="w-full max-w-md p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-kv-purple to-kv-purple-dark flex items-center justify-center text-white font-bold text-lg">
            K
          </div>
          <h1 className="text-xl font-semibold text-slate-900">Kalidash Vision</h1>
        </div>
        <h2 className="text-2xl font-semibold text-slate-900 mb-1">Criar conta</h2>
        <p className="text-sm text-slate-500 mb-6">
          O nome da empresa define seu contexto isolado de análise.
        </p>
        <form onSubmit={handle} className="space-y-4">
          <Input
            label="Seu nome"
            required
            value={nome}
            onChange={(e) => setNome(e.target.value)}
          />
          <Input
            label="Nome da empresa"
            required
            value={empresa}
            onChange={(e) => setEmpresa(e.target.value)}
            placeholder="Ex.: Metalúrgica Alfa"
          />
          <Input
            label="E-mail"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Input
            label="Senha"
            type="password"
            required
            minLength={6}
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
          />
          {erro && (
            <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
              {erro}
            </div>
          )}
          <Button type="submit" disabled={loading} className="w-full">
            {loading ? "Criando..." : "Criar conta"}
          </Button>
        </form>
        <p className="text-sm text-slate-500 mt-6 text-center">
          Já tem conta?{" "}
          <Link to="/login" className="text-kv-purple-dark font-medium hover:underline">
            Entrar
          </Link>
        </p>
      </Card>
    </div>
  );
}
