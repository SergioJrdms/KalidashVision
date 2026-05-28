"""Validação de token do Supabase Auth.

Recebemos o JWT do front-end no header Authorization. Para decodificá-lo
sem verificar assinatura (basta confiar no Supabase upstream e cruzar com
o serviço por meio da service_role) usamos o segredo JWT do Supabase.

No MVP, validamos o token chamando o endpoint /auth/v1/user do Supabase
via supabase-py (auth.get_user). Isso é simples e correto: se o token é
válido, devolve o usuário e seu metadata.
"""
from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status
from supabase import Client, create_client


@lru_cache(maxsize=1)
def _admin_client() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


class CurrentUser:
    def __init__(self, id: str, email: str, empresa: str) -> None:
        self.id = id
        self.email = email
        self.empresa = empresa

    def __repr__(self) -> str:
        return f"<User {self.email} · empresa={self.empresa}>"


async def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token ausente")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token vazio")

    sb = _admin_client()
    try:
        resp = sb.auth.get_user(token)
    except Exception as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Token inválido: {e}")

    user = getattr(resp, "user", None) or (resp.get("user") if isinstance(resp, dict) else None)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário não encontrado")

    user_id = user.id if hasattr(user, "id") else user["id"]
    email = user.email if hasattr(user, "email") else user.get("email", "")
    meta = (
        user.user_metadata
        if hasattr(user, "user_metadata")
        else user.get("user_metadata", {})
    ) or {}
    empresa = (meta.get("empresa") or "").strip()
    if not empresa:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Usuário sem 'empresa' em user_metadata. Refaça o cadastro informando a empresa.",
        )
    return CurrentUser(id=user_id, email=email, empresa=empresa)


UserDep = Depends(get_current_user)
