import { Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuth } from "./hooks/useAuth";
import { Spinner } from "./components/UI";
import { AppShell } from "./components/Layout";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Processos from "./pages/Processos";
import DescricaoProcesso from "./pages/DescricaoProcesso";
import Upload from "./pages/Upload";
import Dashboard from "./pages/Dashboard";
import Validacao from "./pages/Validacao";
import Chat from "./pages/Chat";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading)
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Spinner className="h-8 w-8" />
      </div>
    );
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function PublicOnly({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return <Navigate to="/processos" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Routes>
        <Route path="/login" element={<PublicOnly><Login /></PublicOnly>} />
        <Route path="/cadastro" element={<PublicOnly><Signup /></PublicOnly>} />
        <Route element={<Protected><AppShell /></Protected>}>
          <Route path="/processos" element={<Processos />} />
          <Route path="/processos/:id/descricao" element={<DescricaoProcesso />} />
          <Route path="/processos/:id/upload" element={<Upload />} />
          <Route path="/processos/:id/dashboard" element={<Dashboard />} />
          <Route path="/processos/:id/validacao" element={<Validacao />} />
          <Route path="/processos/:id/chat" element={<Chat />} />
        </Route>
        <Route path="*" element={<Navigate to="/processos" replace />} />
      </Routes>
    </QueryClientProvider>
  );
}
