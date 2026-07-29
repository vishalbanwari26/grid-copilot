output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_name" {
  description = "EKS cluster name, for kubeconfig commands."
  value       = module.eks.cluster_name
}

output "configure_kubectl" {
  description = "Command to point kubectl at this cluster once applied."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}

output "ecr_repository_url" {
  description = "ECR repository URL for the grid-copilot image."
  value       = module.ecr.repository_url
}

output "app_iam_role_arn" {
  description = "IAM role ARN the app's Kubernetes service account can assume via IRSA."
  value       = module.app_iam.role_arn
}
