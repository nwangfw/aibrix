#!/bin/bash
echo -e "\033[1m===== Installing AIBrix on Kubernetes cluster =====\033[0m"

set -e

# Remove existing aibrix directory if it exists
if [ -d "aibrix" ]; then
    echo "Removing existing aibrix directory..."
    rm -rf aibrix
fi

# Clone AIBrix repository
git clone https://github.com/vllm-project/aibrix.git
cd aibrix

# Install component dependencies
echo "Installing AIBrix dependencies..."
kubectl apply -k "github.com/vllm-project/aibrix/config/dependency?ref=v0.2.1" --server-side --force-conflicts

# Install AIBrix components
echo "Installing AIBrix components..."
kubectl apply -k "github.com/vllm-project/aibrix/config/overlays/release?ref=v0.2.1" --server-side --force-conflicts

# Wait for AIBrix pods to be ready
echo "Waiting for AIBrix pods to be ready..."
sleep 10  # Give pods time to start

# Function to check if all pods are ready
check_pods_ready() {
    local namespace=$1
    local pods=$(kubectl get pods -n $namespace -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}')
    local all_ready=true
    
    for status in $pods; do
        if [ "$status" != "True" ]; then
            all_ready=false
            break
        fi
    done
    
    echo $all_ready
}

# Wait for pods to be ready
TIMEOUT=300  # 5 minutes
INTERVAL=10  # Check every 10 seconds
ELAPSED=0

while [ $ELAPSED -lt $TIMEOUT ]; do
    if [ "$(check_pods_ready aibrix-system)" = "true" ]; then
        echo "All AIBrix pods are ready!"
        break
    fi
    echo "Waiting for AIBrix pods to be ready..."
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "Timeout waiting for AIBrix pods to be ready"
    exit 1
fi

echo -e "\033[1m===== AIBrix installation complete =====\033[0m"
echo -e "You can now use AIBrix for your GenAI inference infrastructure.\n"
echo -e "• Check AIBrix status: \033[1mkubectl get pods -n aibrix-system\033[0m"
echo -e "• View AIBrix logs: \033[1mkubectl logs -n aibrix-system deployment/aibrix-controller-manager\033[0m" 