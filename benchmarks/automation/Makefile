.PHONY: all clean setup-env setup-k8s install-aibrix deploy-model benchmark

all: setup-env setup-k8s install-aibrix deploy-model benchmark

setup-env:
	@echo "Setting up environment..."
	@chmod +x setup_environment.sh
	@./setup_environment.sh

setup-k8s:
	@echo "Setting up Kubernetes cluster..."
	@chmod +x launch_k8s.sh
	@./launch_k8s.sh

install-aibrix:
	@echo "Installing AIBrix..."
	@chmod +x install_aibrix.sh
	@./install_aibrix.sh

deploy-model:
	@echo "Deploying model..."
	@chmod +x deploy_model.sh
	@./deploy_model.sh

benchmark:
	@echo "Running benchmark..."
	@chmod +x benchmarking.sh
	@./benchmarking.sh

clean:
	@echo "Cleaning up..."
	@echo "Terminating Kubernetes cluster..."
	@sky down -y k8s || true
	@rm -rf aibrix
	@rm -rf .venv
	@echo "Cleanup complete" 