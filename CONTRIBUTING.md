# Padrão de contribuição — Videoteca UNIFOR

## Branches

| Branch | Papel |
|--------|-------|
| `main` | Produção. Protegida — só recebe merge via PR aprovado. |
| `dev` | Integração da sprint. Base das branches de trabalho. |
| `feat/T00-descricao` | Uma branch por tarefa/subtarefa do backlog (ex.: `feat/T02-modelar-banco`). |
| `fix/descricao` | Correções. |

Fluxo: `feat/*` → PR para `dev` → ao fechar a sprint, `dev` → `main`.

## Commits — Conventional Commits + ID da tarefa

Formato: `tipo(escopo): descrição [Txx]`

```
feat(api): endpoint POST /reservas com validação [T06.3]
fix(auth): corrige expiração do refresh token [T03.3]
chore(infra): dockerfile e compose local [T01.4]
docs(readme): instruções de setup local [T01.1]
```

Tipos: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`.

Sempre referenciar o ID da tarefa do backlog (`[T01.3]`) para manter a
rastreabilidade requisito → tarefa → commit.

## Regras

- Nunca commitar segredos: `.env`, `*.pem`, chaves. Use `.env.example`.
- Segredos de produção vêm do AWS Secrets Manager (T25).
- PRs pequenos, um por subtarefa; nenhuma subtarefa passa de tamanho **M**.
