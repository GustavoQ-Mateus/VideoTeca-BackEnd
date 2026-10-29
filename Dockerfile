# Imagem de produção da API (ECS Fargate / docker compose).
# - Sem segredos na imagem: DATABASE_URL, JWT_SECRET etc. chegam por variável
#   de ambiente (Secrets Manager no ECS, .env no compose).
# - Copia só o necessário para rodar (app/ e db/migrations para o passo de
#   migração do deploy) — testes, infra, docs e .env ficam de fora.
# - Roda como usuário sem privilégios.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY db/migrations ./db/migrations

USER 10001:10001

EXPOSE 8000

# Mesmo check da task definition (ecs.tf) e do target group do ALB.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# --proxy-headers: IP real do cliente vem do ALB/nginx (X-Forwarded-For).
# A porta 8000 só é acessível pelo ALB (SG) ou pela rede interna do compose.
# Só confia em X-Forwarded-For vindo do ALB (CIDR da VPC, definido na task definition);
# "*" deixaria o cliente forjar o IP e burlar o rate limit de login.
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
