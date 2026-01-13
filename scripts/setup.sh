#!/bin/bash
# Setup Pipeline Hardening environment

set -e

cd "$(dirname "$0")/.."

echo "Setting up Pipeline Hardening..."

# Create virtual environment
if [ ! -d "backend/.venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv backend/.venv
fi

# Activate and install dependencies
source backend/.venv/bin/activate
pip install -r backend/requirements.txt

# Create test artifacts directory
mkdir -p test-artifacts

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    cat > .env << 'EOF'
# GitHub Configuration
GITHUB_TOKEN=your_github_token_here
GITHUB_REPO_OWNER=your_github_username
GITHUB_REPO_NAME=PipelineHardening

# Database
DATABASE_URL=sqlite+aiosqlite:///./pipeline.db
EOF
    echo "Please edit .env and add your GitHub token"
fi

echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env and add your GitHub token"
echo "2. Run: ./scripts/run_server.sh"
echo "3. Test: ./scripts/run_tests.sh"
