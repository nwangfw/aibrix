.PHONY: all clean setup-env setup-k8s install-aibrix

all: setup-env setup-k8s install-aibrix

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

clean:
	@echo "Cleaning up..."
	@rm -rf aibrix
	@rm -rf .venv
	@echo "Cleanup complete" 