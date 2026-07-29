# VPC and EKS use the audited terraform-aws-modules community modules rather
# than hand-rolled resources: correctly wiring IRSA/OIDC and node security
# groups is exactly the kind of plumbing worth reusing rather than
# reinventing. The ECR repo and the app's IAM role are bespoke to this
# project, so they are hand-written in modules/ below.

data "aws_availability_zones" "available" {
  state = "available"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr

  azs             = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnets  = [cidrsubnet(var.vpc_cidr, 4, 0), cidrsubnet(var.vpc_cidr, 4, 1)]
  private_subnets = [cidrsubnet(var.vpc_cidr, 4, 2), cidrsubnet(var.vpc_cidr, 4, 3)]

  enable_nat_gateway   = true
  single_nat_gateway   = true # cost-conscious: one NAT gateway, not one per AZ
  enable_dns_hostnames = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = true

  eks_managed_node_groups = {
    default = {
      instance_types = [var.node_instance_type]
      min_size       = 1
      max_size       = var.node_desired_size + 1
      desired_size   = var.node_desired_size
    }
  }

  enable_irsa = true
}

module "ecr" {
  source       = "./modules/ecr"
  cluster_name = var.cluster_name
}

module "app_iam" {
  source             = "./modules/app_iam"
  cluster_name       = var.cluster_name
  ecr_repository_arn = module.ecr.repository_arn
  oidc_provider_arn  = module.eks.oidc_provider_arn
  oidc_provider_url  = module.eks.cluster_oidc_issuer_url
}
