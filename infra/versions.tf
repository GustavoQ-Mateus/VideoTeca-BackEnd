terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# Profile explícito prdal-gustavo (conta pessoal 765656213653).
# NUNCA usar o profile default (conta da empresa).
provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      projeto   = "videoteca"
      ambiente  = var.environment
      gerido_por = "terraform"
    }
  }
}
