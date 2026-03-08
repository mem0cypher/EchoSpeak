#!/bin/bash
#
# EchoSpeak Installation Script
#
# Usage:
#   curl -sSL https://echospeak.dev/install.sh | bash
#
# This script:
# 1. Checks prerequisites (Python 3.11+, Node.js 18+)
# 2. Creates virtual environment
# 3. Installs dependencies
# 4. Runs the onboarding wizard
# 5. Starts EchoSpeak

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Print functions
print_header() {
    echo -e "${CYAN}"
    echo "╭─────────────────────────────────────────────────────────────╮"
    echo "│                    EchoSpeak Installer                      │"
    echo "╰─────────────────────────────────────────────────────────────╯"
    echo -e "${NC}"
}

print_step() {
    echo -e "\n${BLUE}▶ $1${NC}\n"
}

print_success() {
    echo -e "${GREEN}  ✓ $1${NC}"
}

print_error() {
    echo -e "${RED}  ✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}  ⚠ $1${NC}"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Get Python version
get_python_version() {
    python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo "0"
}

# Compare versions (returns 0 if $1 >= $2)
version_ge() {
    [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

# Compare versions (returns 0 if $1 <= $2)
version_le() {
    [ "$(printf '%s\n' "$1" "$2" | sort -V | head -n1)" = "$1" ]
}

# Main installation
main() {
    print_header

    # Check Python
    print_step "Checking Python..."
    
    if ! command_exists python3; then
        print_error "Python 3 is not installed."
        echo "  Please install Python 3.11 or higher: https://www.python.org/downloads/"
        exit 1
    fi
    
    PYTHON_VERSION=$(get_python_version)
    if ! version_ge "$PYTHON_VERSION" "3.11"; then
        print_error "Python version $PYTHON_VERSION is too old."
        echo "  Please upgrade to Python 3.11 or higher."
        exit 1
    fi

    if ! version_le "$PYTHON_VERSION" "3.12"; then
        print_error "Python version $PYTHON_VERSION is too new."
        echo "  EchoSpeak backend dependencies currently require Python 3.11 or 3.12."
        exit 1
    fi
    
    print_success "Python $PYTHON_VERSION found"

    # Check Node.js (optional but recommended)
    print_step "Checking Node.js..."
    
    if command_exists node; then
        NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
        if [ "$NODE_VERSION" -ge 18 ]; then
            print_success "Node.js $(node -v) found"
        else
            print_warning "Node.js version is old. Web UI may not work correctly."
        fi
    else
        print_warning "Node.js not found. Web UI will not be available."
        echo "  Install Node.js 18+ for the web UI: https://nodejs.org/"
    fi

    # Determine install directory
    INSTALL_DIR="${INSTALL_DIR:-$HOME/.echospeak}"
    
    if [ -d "$INSTALL_DIR" ]; then
        print_warning "Installation directory already exists: $INSTALL_DIR"
        read -p "  Overwrite? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "  Installation cancelled."
            exit 0
        fi
        rm -rf "$INSTALL_DIR"
    fi

    # Clone or download repository
    print_step "Downloading EchoSpeak..."
    
    if command_exists git; then
        REPO_URL="${REPO_URL:-https://github.com/your-org/EchoSpeak.git}"
        git clone "$REPO_URL" "$INSTALL_DIR" 2>/dev/null || {
            print_error "Failed to clone repository"
            exit 1
        }
        print_success "Cloned from $REPO_URL"
    else
        print_error "Git is required for installation."
        echo "  Install git: https://git-scm.com/downloads"
        exit 1
    fi

    # Create virtual environment
    print_step "Setting up Python environment..."
    
    cd "$INSTALL_DIR/apps/backend"
    python3 -m venv .venv
    print_success "Created virtual environment"
    
    # Activate virtual environment
    if [ -f ".venv/bin/activate" ]; then
        source .venv/bin/activate
    elif [ -f ".venv/Scripts/activate" ]; then
        source .venv/Scripts/activate
    else
        print_error "Failed to activate virtual environment"
        exit 1
    fi
    
    # Install dependencies
    print_step "Installing Python dependencies..."
    
    pip install --upgrade pip >/dev/null 2>&1
    pip install -r requirements.txt >/dev/null 2>&1 || {
        print_error "Failed to install dependencies"
        exit 1
    }
    print_success "Installed Python dependencies"

    # Install web UI dependencies
    if [ -d "$INSTALL_DIR/apps/web" ] && command_exists npm; then
        print_step "Installing web UI dependencies..."
        cd "$INSTALL_DIR/apps/web"
        npm install --silent 2>/dev/null || {
            print_warning "Failed to install web UI dependencies"
        }
        print_success "Installed web UI dependencies"
    fi

    # Create convenience scripts
    print_step "Creating launch scripts..."
    
    cd "$INSTALL_DIR"
    
    # echospeak command
    cat > echospeak << 'EOF'
#!/bin/bash
cd "$(dirname "$0")/apps/backend"
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate 2>/dev/null
if command -v node >/dev/null 2>&1; then
  cd "$(dirname "$0")/apps/onboard-tui" || exit 1
  if [ ! -d "node_modules" ]; then
    npm install
  fi
  npm run -s start
else
  echo "Error: Node.js is required for onboarding. Install Node.js 18+ and try again."
  exit 1
fi
EOF
    chmod +x echospeak
    
    # start command
    cat > start << 'EOF'
#!/bin/bash
cd "$(dirname "$0")/apps/backend"
source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate 2>/dev/null
python app.py --mode api &
if [ -d "../web" ]; then
    cd ../web
    npm run dev &
fi
wait
EOF
    chmod +x start
    
    print_success "Created launch scripts"

    # Done
    echo ""
    echo -e "${GREEN}╭─────────────────────────────────────────────────────────────╮${NC}"
    echo -e "${GREEN}│${NC}                                                             ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}   ✓ Installation complete!                                  ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}                                                             ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}   Location: $INSTALL_DIR"
    echo -e "${GREEN}│${NC}                                                             ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}   Next steps:                                                ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}                                                             ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}   1. Run the setup wizard:                                   ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}      $INSTALL_DIR/echospeak onboard          ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}                                                             ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}   2. Or start directly:                                      ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}      $INSTALL_DIR/start                      ${GREEN}│${NC}"
    echo -e "${GREEN}│${NC}                                                             ${GREEN}│${NC}"
    echo -e "${GREEN}╰─────────────────────────────────────────────────────────────╯${NC}"
    echo ""

    # Run wizard?
    read -p "Run setup wizard now? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        if command_exists node; then
            cd "$INSTALL_DIR/apps/onboard-tui"
            if [ ! -d "node_modules" ]; then
                npm install
            fi
            npm run -s start
        else
            print_warning "Node.js not found. Cannot run onboarding wizard."
            echo "  Install Node.js 18+ to use the setup wizard."
        fi
    fi
}

# Run main
main "$@"
