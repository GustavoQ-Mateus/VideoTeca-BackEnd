# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versões seguem [SemVer](https://semver.org/lang/pt-BR/). Tags `vX.Y.Z` disparam
o deploy de produção (`.github/workflows/deploy.yml`, runbook em `docs/RELEASE.md`).

## [Não publicado]

## [1.0.0] — 2026-11

Primeira versão de produção da API da Videoteca UNIFOR, consolidando as
sprints 1 a 7 do backlog.

### Sprint 1 — Fundação (T01, T02, T25, T27)
- API FastAPI + PostgreSQL (asyncpg) com autenticação JWT e perfis aluno,
  professor, funcionário e admin; catálogo público, eventos e áreas por perfil.
- Modelagem do banco versionada: `001_init_schema`, `002_constraints_indexes`,
  `003_anticonflict` (EXCLUDE GiST contra reservas sobrepostas), seed de
  desenvolvimento e DER (`db/DER.md`).
- Fundação AWS em Terraform: VPC com subnets públicas/privadas, security groups,
  roles IAM do ECS, Secrets Manager/SSM e repositório ECR.

### Sprint 2 — Banco e API na AWS (T26, T28)
- Amazon Aurora PostgreSQL Serverless v2 (writer + reader, escala a 0 ACU,
  backup de 7 dias).
- API em ECS Fargate atrás de Application Load Balancer com health check
  `/health`, autoscaling por CPU e deploy rolling com circuit breaker/rollback.
- Recursos pagos atrás do flag `enable_runtime` (padrão desligado).

### Sprint 3 — Processamento em lote e workers (T29, T30)
- Módulo Terraform `infra/sprint3`: AWS Batch para relatórios e e-mails
  (lembretes de sessão), workers em cluster ECS com nós EC2 em auto scaling
  group, rollout controlado e monitoramento; testes com provider simulado.

### Sprint 4 — Salas e reservas (T05, T06)
- Disponibilidade de salas por data, capacidade e recursos.
- Reserva atômica com bloqueio de conflito de horário e sala; reservas de
  professor ficam pendentes até análise do funcionário.

### Sprint 5 — Circulação e sessões (T10, T17)
- Empréstimo e devolução de equipamentos, com marcação de atraso e avaria e
  controle de estoque.
- Filmes com cartaz (validado, regravado sem metadados) e sessões de exibição:
  criação, edição, publicação e validação de conflito com reservas.
- Migrations `004` a `008`.

### Sprint 6 — Design system, direitos, segurança e LGPD (T20, T21, T22)
- Redesenho responsivo da interface com design system próprio (FrontEnd).
- Direitos autorais: status de autorização no cadastro do filme, comprovantes
  anexados (PDF/PNG/JPEG validados por assinatura) e bloqueio de publicação de
  sessão sem autorização válida (migrations `009_film_rights`,
  `010_rights_proofs`).
- Segurança: headers de proteção, limite de corpo, rejeição de caracteres de
  controle, rate limit no login, erros 500 sem stack e validação de
  configuração em produção (segredo JWT forte, CORS sem `*`).
- LGPD: aceite de política versionada, exportação dos dados do titular e
  anonimização sob pedido (migration `011_privacy`).
- Desempenho: índices para as consultas mais usadas, paginação/busca no
  catálogo e timeout de consulta de 55 s (migration `012_performance`).

### Sprint 7 — Qualidade, documentação e operação (T23, T24, T31)
- Testes unitários das regras de negócio e testes de integração dos fluxos da
  API; CI com pytest e Terraform (`fmt`, `validate`, `test`).
- Documentação da API: OpenAPI 1.0.0 com tags descritas, `docs/API.md` e
  `docs/openapi.json`; manuais do usuário e do funcionário (FrontEnd).
- Observabilidade: logs JSON por requisição com `X-Request-ID`, métricas EMF
  (`Videoteca/API`), metric filters, consultas salvas e dashboard CloudWatch.
- Alarmes (SNS por e-mail): 5xx, latência p95, hosts não saudáveis, CPU/memória
  do ECS, CPU/conexões do Aurora e erros da aplicação.
- Custo: AWS Budget mensal (50/80/100% real e 100% previsto) e Cost Anomaly
  Detection.
- Publicação: imagem Docker sem root e com healthcheck; deploy por tag `v*`
  via GitHub Actions com OIDC (sem chaves estáticas), aprovação manual no
  environment `production`, imagem por digest, migrations como passo
  explícito e rollback automático; runbook `docs/RELEASE.md`.

### Corrigido
- `docker-compose.yml` montava apenas as migrations 001–003; agora monta
  001–012.

[Não publicado]: https://github.com/GustavoQ-Mateus/VideoTeca-BackEnd/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/GustavoQ-Mateus/VideoTeca-BackEnd/releases/tag/v1.0.0
