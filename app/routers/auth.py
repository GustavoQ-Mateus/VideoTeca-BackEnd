from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.hardening import check_login_rate, login_limiter
from app.core.security import create_token, current_user, hash_password, require_role, verify_password
from app.db.postgres import get_pool

router = APIRouter()
# Constant-time-ish answer for unknown logins (avoids user enumeration by response time).
_DUMMY_HASH = hash_password("videoteca-dummy-password")


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    login: str = Field(min_length=1, max_length=254)  # matrícula, SIAPE ou email
    password: str = Field(min_length=1, max_length=128)
    role: Literal["aluno", "professor", "funcionario", "admin"]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(min_length=1, max_length=200)
    role: Literal["aluno", "professor", "funcionario", "admin"]
    registration: str | None = Field(default=None, max_length=40)
    course: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)


def _password_matches(plain: str, hashed: str | None) -> bool:
    try:
        return verify_password(plain, hashed or _DUMMY_HASH) and hashed is not None
    except ValueError:  # malformed hash or password over bcrypt's 72-byte limit
        return False


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    key = check_login_rate(request, body.login)
    pool = get_pool()

    # Busca por email ou matrícula
    row = await pool.fetchrow(
        "SELECT u.id, u.password_hash, p.role, p.full_name, p.registration, p.course, p.phone, u.email "
        "FROM users u JOIN profiles p ON p.id = u.id "
        "WHERE u.email = $1 OR p.registration = $1",
        body.login,
    )
    if not _password_matches(body.password, row["password_hash"] if row else None):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

    if row["role"] != body.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Este usuário não tem perfil de {body.role}",
        )

    login_limiter.reset(key)
    token = create_token(str(row["id"]), row["role"])
    return {
        "token": token,
        "user": {
            "id": str(row["id"]),
            "email": row["email"],
            "name": row["full_name"],
            "role": row["role"],
            "registration": row["registration"],
            "course": row["course"],
            "phone": row["phone"],
        },
    }


@router.get("/me")
async def me(user=Depends(current_user)):
    return user


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, user=Depends(require_role("funcionario", "admin"))):
    # Contas são criadas pela equipe; só admin cria perfis de equipe (evita escalonamento de privilégio).
    if body.role in ("funcionario", "admin") and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores criam contas de equipe")
    pool = get_pool()
    email = body.email.lower()
    existing = await pool.fetchval("SELECT id FROM users WHERE lower(email) = $1", email)
    if existing:
        raise HTTPException(status_code=409, detail="Email já cadastrado")

    pw_hash = hash_password(body.password)
    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id = await conn.fetchval(
                "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
                email, pw_hash,
            )
            await conn.execute(
                "INSERT INTO profiles (id, full_name, role, registration, course, phone) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                user_id, body.full_name, body.role,
                body.registration, body.course, body.phone,
            )
    return {"ok": True, "id": str(user_id)}
