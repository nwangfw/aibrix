#!/bin/bash

# Set up logging
log() {
    echo -e "\033[1m[LOG] $1\033[0m"
}

warning() {
    echo -e "\033[1;33m[WARNING] $1\033[0m"
}

error() {
    echo -e "\033[1;31m[ERROR] $1\033[0m"
    exit 1
}

# Configuration
MODEL_NAME="deepseek-r1-distill-llama-8b"
API_KEY="dummy-key"  # Replace with your actual API key if needed
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATETIME=$(date +"%Y%m%d-%H%M%S")
LOG_FILE="${SCRIPT_DIR}/${MODEL_NAME}-${DATETIME}.log"
RESULTS_DIR="${SCRIPT_DIR}/profiles/results/"

# Create log file
touch "${LOG_FILE}" && chmod 666 "${LOG_FILE}"

# Function to wait for pod to be ready
wait_for_pod() {
    local pod_name=$1
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if kubectl get pod $pod_name -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' | grep -q "True"; then
            return 0
        fi
        log "Waiting for pod $pod_name to be ready... (attempt $attempt/$max_attempts)"
        sleep 10
        attempt=$((attempt + 1))
    done
    return 1
}

# Check if model pod is running
POD_NAME=$(kubectl get pods -l model.aibrix.ai/name=${MODEL_NAME} -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -z "$POD_NAME" ]; then
    error "No model pod found. Please run the setup script first."
fi

# Wait for pod to be ready
if ! wait_for_pod $POD_NAME; then
    error "Pod $POD_NAME failed to become ready"
fi

# Create the results directory if it doesn't exist
log "Creating results directory at ${RESULTS_DIR}..."
mkdir -p "${RESULTS_DIR}"
if [ ! -d "${RESULTS_DIR}" ]; then
    error "Failed to create results directory at ${RESULTS_DIR}"
fi
log "Results directory created successfully"


# Create a Kubernetes deployment to run the benchmark
log "Creating benchmark deployment..."
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${MODEL_NAME}-benchmark-${DATETIME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${MODEL_NAME}-benchmark
  template:
    metadata:
      labels:
        app: ${MODEL_NAME}-benchmark
    spec:
      containers:
      - name: benchmark
        image: wangn19/runtime:benchmarking
        command:
        - bash
        - -c
        - |
          set -x
          echo "Starting benchmark with model: ${MODEL_NAME}"
          
          # Install curl if not present
          if ! command -v curl &> /dev/null; then
            echo "Installing curl..."
            apt-get update && apt-get install -y curl
          fi
           
          # Test a simple completion request
          echo "Testing completion endpoint..."
          curl -v -X POST "http://${MODEL_NAME}.default.svc.cluster.local:8000/v1/completions" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer ${API_KEY}" \
            -d "{
              \"model\": \"deepseek-r1-distill-llama-8b\",
              \"prompt\": \"Hello\",
              \"max_tokens\": 50
            }"
          
          # Run benchmark with specified host and port
          aibrix_benchmark -m ${MODEL_NAME} -o ${MODEL_NAME} \
            --input-start 4 \
            --input-limit 4 \
            --output-start 4 \
            --output-limit 4 \
            --rate-start 1 \
            --rate-limit 1 \
            --api-key "${API_KEY}" \
            --port 8000 \
            --host "${MODEL_NAME}.default.svc.cluster.local" \
            --output result/${MODEL_NAME}.jsonl
          
          echo "Benchmark completed with exit code: $?"
          
        env:
        - name: MODEL_NAME
          value: ${MODEL_NAME}
        - name: API_KEY
          value: ${API_KEY}
        - name: LLM_API_BASE
          value: http://${MODEL_NAME}.default.svc.cluster.local:8000
        - name: MODEL_SERVICE
          value: ${MODEL_NAME}.default.svc.cluster.local
        volumeMounts:
        - name: results
          mountPath: /usr/local/lib/python3.11/site-packages/aibrix/gpu_optimizer/optimizer/profiling/result
      volumes:
      - name: results
        emptyDir: {}
EOF

# Wait for deployment to be ready
log "Waiting for benchmark deployment to be ready..."
kubectl rollout status deployment/${MODEL_NAME}-benchmark-${DATETIME} --timeout=5m

# Get the pod name
DEPLOYMENT_POD=$(kubectl get pods -l app=${MODEL_NAME}-benchmark -o jsonpath='{.items[0].metadata.name}')

#TODO: Need a better way to check if the benchmark is complete)
log "Waiting for benchmark to complete..."
sleep 300  

# Copy results from the pod to local directory
log "Copying results from pod..."
kubectl cp ${DEPLOYMENT_POD}:/usr/local/lib/python3.11/site-packages/aibrix/gpu_optimizer/optimizer/profiling/result/${MODEL_NAME}.jsonl "${RESULTS_DIR}/${MODEL_NAME}.jsonl"

# Get the logs from the pod
log "Getting benchmark logs..."
kubectl logs $DEPLOYMENT_POD > "${LOG_FILE}"

# Clean up the deployment
log "Cleaning up benchmark deployment..."
kubectl delete deployment ${MODEL_NAME}-benchmark-${DATETIME}

log "Benchmark completed. Log file: ${LOG_FILE}"
log "Results saved to: ${RESULTS_DIR}/${MODEL_NAME}.jsonl" 