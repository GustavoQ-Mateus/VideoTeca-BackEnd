from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.security import create_token, current_user, hash_password, verify_password
from app.db.postgres import get_pool

router = APIRouter()


class LoginRequest(BaseModel):
    login: str      # matrícula, SIAPE ou email
    password: str
    role: str       # aluno | professor | funcionario


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    role: str
    registration: str | None = None
    course: str | None = None
    phone: str | None = None


@router.post("/login")
async def login(body: LoginRequest):
    pool = get_pool()

    # Busca por email ou matrícula
    row = await pool.fetchrow(
        "SELECT u.id, u.password_hash, p.role, p.full_name, p.registration, p.course, p.phone, u.email "
        "FROM users u JOIN profiles p ON p.id = u.id "
        "WHERE u.email = $1 OR p.registration = $1",
        body.login,
    )
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

    if row["role"] != body.role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Este usuário não tem perfil de {body.role}",
        )

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


@router.post("/register")
async def register(body: RegisterRequest):
    pool = get_pool()
    existing = await pool.fetchval("SELECT id FROM users WHERE email = $1", body.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    pw_hash = hash_password(body.password)
    async with pool.acquire() as conn:
        async with conn.transaction():
            user_id = await conn.fetchval(
                "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
                body.email, pw_hash,
            )
            await conn.execute(
                "INSERT INTO profiles (id, full_name, role, registration, course, phone) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                user_id, body.full_name, body.role,
                body.registration, body.course, body.phone,
            )
    return {"ok": True}
