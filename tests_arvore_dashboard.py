"""Contrato de integração: a árvore embutida reutiliza o Dashboard, sem nova leitura."""
from pathlib import Path
import sys

RAIZ = Path(__file__).resolve().parent
ARVORE = (RAIZ / "frontend" / "src" / "pages" / "Arvore.tsx").read_text(encoding="utf-8")
DASH = (RAIZ / "frontend" / "src" / "pages" / "Dashboard.tsx").read_text(encoding="utf-8")
ok = fail = 0


def check(nome, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {nome}")
    else:
        fail += 1
        print(f"  FAIL {nome} {extra}")


print("[1] A mesma árvore recebe dados já carregados")
check("componente reutilizável é exportado", "export function ArvoreProdutividade" in ARVORE)
check("recebe distribuição por propriedade", "distribuicao: DistribuicaoComportamento[]" in ARVORE)
check("standalone continua carregando o dashboard", 'queryKey: ["dashboard", proc.id]' in ARVORE)
check("standalone entrega sua distribuição ao componente", "<ArvoreProdutividade proc={proc} distribuicao={q.data.snapshot.distribuicao_comportamentos}" in ARVORE)

print("\n[2] Dashboard começa recolhido e usa a janela atual")
check("Dashboard importa a árvore reutilizável", 'import { ArvoreProdutividade } from "./Arvore";' in DASH)
check("estado inicial é recolhido", "const [aberta, setAberta] = useState(false);" in DASH)
check("controle expõe estado de expansão", "aria-expanded={aberta}" in DASH)
check("controle alterna abrir e fechar", 'aberta ? "Fechar árvore" : "Ver árvore"' in DASH)
check("árvore recebe a distribuição da resposta atual", "distribuicao={q.data.snapshot.distribuicao_comportamentos}" in DASH)
_inicio = DASH.find("function ArvoreNoDashboard")
_fim = DASH.find("function formatarLeitura", _inicio)
_embutida = DASH[_inicio:_fim]
check("componente embutido não inicia nova query", _inicio >= 0 and "useQuery(" not in _embutida)

print(f"\n{ok} ok · {fail} falha(s)")
sys.exit(1 if fail else 0)
