variable "cluster_name" {
  type = string
}

variable "ecr_repository_arn" {
  type = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster's IAM OIDC provider (module.eks.oidc_provider_arn)."
  type        = string
}

variable "oidc_provider_url" {
  description = "Issuer URL of the EKS cluster's OIDC provider (module.eks.cluster_oidc_issuer_url)."
  type        = string
}
