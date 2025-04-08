#!/bin/bash
echo -e "\033[1m===== Deploying model using deploy.yaml =====\033[0m"

set -e

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "kubectl is not installed. Please install it first."
    exit 1
fi

# Deploy the model
echo "Deploying model from deploy.yaml..."
kubectl apply -f model.yaml

# Wait for the deployment to be ready
echo "Waiting for deployment to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/deepseek-r1-distill-llama-8b

echo -e "\033[1m===== Model deployment complete =====\033[0m"
echo -e "You can check the deployment status with: \033[1mkubectl get pods\033[0m"
echo -e "To view the logs: \033[1mkubectl logs -f deployment/deepseek-r1-distill-llama-8b\033[0m" 