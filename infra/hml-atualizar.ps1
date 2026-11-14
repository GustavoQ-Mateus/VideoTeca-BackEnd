<#
  Atualiza a homologação (hml) da Videoteca na conta pessoal AWS 765656213653.
  Pré-requisito: a hml já existe (terraform apply -var-file=hml.tfvars) e o Docker está rodando.

  Uso (na pasta infra):
    .\hml-atualizar.ps1                 # API + front + migrate/seed (tudo)
    .\hml-atualizar.ps1 -Api            # só rebuild/push/redeploy da API
    .\hml-atualizar.ps1 -Web            # só rebuild/push/redeploy do front
    .\hml-atualizar.ps1 -Migrate        # só migrations + seeds (task one-off no Fargate)
    .\hml-atualizar.ps1 -Api -Migrate   # combinações valem
#>
param(
  [switch]$Api,
  [switch]$Web,
  [switch]$Migrate
)
$ErrorActionPreference = "Stop"
if (-not ($Api -or $Web -or $Migrate)) { $Api = $true; $Web = $true; $Migrate = $true }

$Profile_ = "prdal-gustavo"
$Region   = "us-east-1"
$Account  = "765656213653"
$Registry = "$Account.dkr.ecr.$Region.amazonaws.com"
$Cluster  = "videoteca-dev-cluster"
$Tag      = "hml"
$Infra    = $PSScriptRoot
$BackEnd  = Split-Path $Infra -Parent
$FrontEnd = Join-Path (Split-Path $BackEnd -Parent) "VideoTeca-FrontEnd"
$env:AWS_PROFILE = $Profile_

# 1) Trava de segurança: só a conta pessoal.
$conta = aws sts get-caller-identity --profile $Profile_ --query Account --output text
if ($conta -ne $Account) { throw "Conta errada ($conta). Abortado: use só o profile $Profile_ ($Account)." }

function Invoke-Checked([string]$Desc, [scriptblock]$Cmd) {
  Write-Host "==> $Desc" -ForegroundColor Cyan
  & $Cmd
  if ($LASTEXITCODE -ne 0) { throw "Falhou: $Desc (exit $LASTEXITCODE)" }
}

if ($Api -or $Web) {
  # No PowerShell 5.1 o pipe para docker login corrompe a senha; o cmd repassa os bytes intactos.
  Invoke-Checked "Login no ECR" { cmd /c "aws ecr get-login-password --region $Region --profile $Profile_ | docker login --username AWS --password-stdin $Registry" }
}

if ($Api) {
  $img = "$Registry/videoteca/api:$Tag"
  Invoke-Checked "Build da API" { docker build --platform linux/amd64 -t $img $BackEnd }
  Invoke-Checked "Push da API"  { docker push $img }
}

if ($Migrate) {
  # Task one-off com a mesma task definition/rede do serviço da API: migrations 001..N + db/seed/seed_*.sql.
  $net = aws ecs describe-services --cluster $Cluster --services videoteca-dev-api --region $Region `
    --query "services[0].networkConfiguration" --output json
  $netFile = Join-Path $env:TEMP "videoteca-net.json"
  $ovrFile = Join-Path $env:TEMP "videoteca-migrate.json"
  [IO.File]::WriteAllText($netFile, ($net -join "`n"))
  [IO.File]::WriteAllText($ovrFile, '{"containerOverrides":[{"name":"api","command":["python","-m","app.migrate","--seed"]}]}')
  Write-Host "==> Rodando migrate + seed (task one-off)" -ForegroundColor Cyan
  $task = aws ecs run-task --cluster $Cluster --launch-type FARGATE --task-definition videoteca-dev-api `
    --network-configuration "file://$netFile" --overrides "file://$ovrFile" --started-by hml-migrate `
    --region $Region --query "tasks[0].taskArn" --output text
  if (-not $task -or $task -eq "None") { throw "run-task não iniciou a task." }
  aws ecs wait tasks-stopped --cluster $Cluster --tasks $task --region $Region
  $taskId = $task.Split("/")[-1]
  aws logs get-log-events --log-group-name /ecs/videoteca-dev-api --log-stream-name "api/api/$taskId" `
    --start-from-head --region $Region --query "events[].message" --output text
  $code = aws ecs describe-tasks --cluster $Cluster --tasks $task --region $Region `
    --query "tasks[0].containers[0].exitCode" --output text
  if ($code -ne "0") { throw "Migrate/seed falhou (exit $code)." }
  Write-Host "Migrate + seed OK" -ForegroundColor Green
}

if ($Api) {
  Invoke-Checked "Redeploy da API" { aws ecs update-service --cluster $Cluster --service videoteca-dev-api --force-new-deployment --region $Region --query "service.serviceName" --output text }
}

if ($Web) {
  # NEXT_PUBLIC_API_URL entra no bundle no build: usa a URL do CloudFront (mesma origem do front).
  Push-Location $Infra
  try { $appUrl = (terraform output -raw app_url) } finally { Pop-Location }
  if ($appUrl -notlike "https://*") { throw "app_url inválida: $appUrl" }
  $img = "$Registry/videoteca/web:$Tag"
  Invoke-Checked "Build do front ($appUrl)" { docker build --platform linux/amd64 --build-arg "NEXT_PUBLIC_API_URL=$appUrl" -t $img $FrontEnd }
  Invoke-Checked "Push do front" { docker push $img }
  Invoke-Checked "Redeploy do front" { aws ecs update-service --cluster $Cluster --service videoteca-dev-web --force-new-deployment --region $Region --query "service.serviceName" --output text }
}

if ($Api -or $Web) {
  $svcs = @(); if ($Api) { $svcs += "videoteca-dev-api" }; if ($Web) { $svcs += "videoteca-dev-web" }
  Write-Host "==> Aguardando serviços estáveis: $($svcs -join ', ')" -ForegroundColor Cyan
  aws ecs wait services-stable --cluster $Cluster --services $svcs --region $Region
  if ($LASTEXITCODE -ne 0) { throw "Serviços não estabilizaram (veja o console do ECS / logs)." }
}
Write-Host "Pronto." -ForegroundColor Green
