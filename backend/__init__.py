"""Auto-carrega o backend/.env no import do pacote.

Isso garante que `uvicorn backend.main:app` enxergue SUPABASE_URL,
SUPABASE_KEY e GROQ_API_KEY sem precisar exportá-las manualmente na
shell (especialmente no Windows). Variáveis já presentes no ambiente
prevalecem sobre as do .env.
"""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass
