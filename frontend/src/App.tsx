import { Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAuth } from "./hooks/useAuth";
import { Spinner, ToastHost } from "./components/UIKit";
import { AppShell } from "./components/Layout";
import Login from "./pages/Login";
import Processos from "./pages/Processos";
import DescricaoProcesso from "./pages/DescricaoProcesso";
import Upload from "./pages/Upload";
import Dashboard from "./pages/Dashboard";
import Validacao from "./pages/Validacao";
import Eventos from "./pages/Eventos";
import Padroes from "./pages/Padroes";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading)
    return (
      <div className="center" style={{ minHeight: "100vh" }}>
        <Spinner size={28} />
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
        <Route path="/cadastro" element={<PublicOnly><Login /></PublicOnly>} />
        <Route element={<Protected><AppShell /></Protected>}>
          <Route path="/processos" element={<Processos />} />
          <Route path="/processos/:id/dashboard" element={<Dashboard />} />
          <Route path="/processos/:id/validacao" element={<Validacao />} />
          <Route path="/processos/:id/eventos" element={<Eventos />} />
          <Route path="/processos/:id/padroes" element={<Padroes />} />
          <Route path="/processos/:id/upload" element={<Upload />} />
          <Route path="/processos/:id/descricao" element={<DescricaoProcesso />} />
        </Route>
        <Route path="*" element={<Navigate to="/processos" replace />} />
      </Routes>
      <ToastHost />
    </QueryClientProvider>
  );
}
