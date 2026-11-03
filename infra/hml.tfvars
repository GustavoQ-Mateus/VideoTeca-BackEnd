# Homologação na conta pessoal (T01.5): front + API + Aurora atrás do CloudFront.
#   terraform apply -var-file=hml.tfvars
# Custo ligado: ~US$ 55–70/mês (ALB, 2 tasks Fargate, IPv4 públicos, Aurora por ACU).
# Desligar: terraform destroy -var-file=hml.tfvars  (ou enable_runtime=false)
region         = "us-east-1"
aws_profile    = "prdal-gustavo"
environment    = "dev"
enable_runtime = true
enable_edge    = true

api_image_tag = "hml"
web_image_tag = "hml"

budget_limit_usd = 70
# A conta já tem um monitor de anomalia por serviço (único por conta; não é da Videoteca).
enable_cost_anomaly_monitor = false
# Preencha para receber alertas (confirme o e-mail que a AWS enviar):
# budget_alert_emails = ["voce@exemplo.com"]
# alarm_email         = "voce@exemplo.com"
