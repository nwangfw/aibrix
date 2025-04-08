#!/bin/bash
echo -e "\033[1m===== Setting up environment for SkyPilot and Kubernetes =====\033[0m"

set -ex

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check and install required packages
echo "Checking and installing required packages..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    if ! command_exists brew; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    
    # Install required packages
    brew install python3 curl netcat socat kubectl
else
    # Linux
    if ! command_exists apt-get; then
        echo "Unsupported package manager. Please install required packages manually."
        exit 1
    fi
    
    sudo apt update && sudo apt install -y python3 python3-venv python3-pip curl netcat socat
fi

# Install kubectl if not already installed
if ! command_exists kubectl; then
    echo "Installing kubectl..."
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
    sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
fi

# Create and activate Python virtual environment
echo "Setting up Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# Install SkyPilot with Lambda and Kubernetes support
echo "Installing SkyPilot..."
pip3 install skypilot-nightly[lambda,kubernetes]

# Configure Lambda Cloud credentials
echo "Configuring Lambda Cloud credentials..."
mkdir -p ~/.lambda_cloud
chmod 700 ~/.lambda_cloud

# Create lambda_keys file with hardcoded API key
echo "Creating Lambda Cloud credentials file..."
echo "api_key = "replace with your api key" > ~/.lambda_cloud/lambda_keys

# Generate a strong passphrase for K3S token
echo "Generating K3S token..."
K3S_TOKEN=$(openssl rand -base64 16)
echo "Generated K3S token: $K3S_TOKEN"

# Update cloud_k8s.yaml with the generated token
if [ -f "cloud_k8s.yaml" ]; then
    echo "Updating cloud_k8s.yaml with K3S token..."
    # Escape forward slashes in the token
    ESCAPED_TOKEN=$(echo "$K3S_TOKEN" | sed 's/\//\\\//g')
    sed -i '' "s/SKY_K3S_TOKEN:.*/SKY_K3S_TOKEN: $ESCAPED_TOKEN/" cloud_k8s.yaml
else
    echo "Warning: cloud_k8s.yaml not found. Please create it manually with the K3S token."
fi

echo -e "\033[1m===== Environment setup complete =====\033[0m"
echo -e "Next steps:"
echo -e "1. Configure your Lambda Public Cloud Firewall to allow incoming traffic to ports TCP/443 and TCP/6443"
echo -e "2. Run \033[1mmake all\033[0m to deploy the Kubernetes cluster and install AIBrix"