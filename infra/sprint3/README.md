# Sprint 3 — Terraform de Batch e workers EC2

Módulo independente da fundação existente em `../`. Provider AWS 5.100, Terraform >= 1.9.
Escopo acordado: somente IaC de T29/T30. Não inclui implementação, build ou execução dos jobs/workers nem provisionamento AWS.

Validação sem credenciais e sem criar recursos:

```sh
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform test
```

Os testes usam `mock_provider` e somente `command = plan`. `enabled` é falso por padrão.

## Integração com a fundação T25/T26

Forneça `vpc_id`, `vpc_cidr`, `private_subnet_ids` e `database_secret_arn` da fundação.
As subnets precisam de saída via NAT ou endpoints ECR API/DKR, S3, Logs, SQS, Secrets Manager e ECS; a fundação atual não fornece NAT. O security group do Aurora deve permitir TCP/5432 a partir de `process_security_group_id`. Não há SSH nem entrada pública nos nós.
Use um secret com a chave padrão do Secrets Manager; uma chave KMS própria exige conceder `kms:Decrypt` somente para seu ARN.

Imagens `reports_image`, `email_image` e `worker_image` devem usar digest SHA-256. Os comandos são entradas obrigatórias; não apontamos para executáveis fictícios dentro da API.
Forneça `ses_identity_arn` de identidade verificada. O job recebe permissão somente para essa identidade. Em sandbox SES, destinatários também precisam de verificação.

## Contratos dos containers

- Relatórios: recebe DATABASE_URL, REPORTS_BUCKET e REPORTS_PREFIX=exports/. Pode escrever somente nesse prefixo S3.
- E-mails: recebe DATABASE_URL e AWS_REGION, usa SES. Deduplicação de envio é responsabilidade do job.
- Sincronização: recebe DATABASE_URL, SYNC_QUEUE_URL e SYNC_INTERVAL_SECONDS. Deve consumir SQS com idempotência e emitir logs JSON com `level`. A aplicação produtora precisa de permissão `sqs:SendMessage` restrita ao ARN de saída.
- Jobs têm timeout de 15 minutos e até 3 tentativas. Falhas finais geram eventos, logs e alarmes.
- A fila de sincronização manda mensagens à DLQ após 5 recebimentos. Corrija a causa antes de redrive; reenvio de jobs Batch usa a fila e a revisão da definição de job retornadas nos outputs.
- O serviço ECS faz rollout com 100% saudável, circuit breaker e rollback por alarmes. Escalonamento dos nós é gerenciado pelo capacity provider; não adicione outra política ao ASG.

`alarm_email` é opcional e cria uma assinatura SNS que precisa ser confirmada pelo destinatário durante um provisionamento futuro.

Referências: [Batch job definitions](https://registry.terraform.io/providers/hashicorp/aws/5.100.0/docs/resources/batch_job_definition), [ECS capacity providers](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html).
