#!/bin/bash

# Setup script for portf CLI with tab completion

set -e

echo "🚀 Setting up portf CLI..."

# Make portf executable
chmod +x portf
echo "✅ Made portf script executable"

# Detect shell
if [[ -n "$ZSH_VERSION" ]]; then
    SHELL_TYPE="zsh"
elif [[ -n "$BASH_VERSION" ]]; then
    SHELL_TYPE="bash"
else
    echo "⚠️  Unknown shell. Defaulting to bash."
    SHELL_TYPE="bash"
fi

echo "📝 Detected shell: $SHELL_TYPE"

# Get current directory
CURRENT_DIR=$(pwd)

# Add to PATH (add line to shell rc file if not already present)
if [[ "$SHELL_TYPE" == "zsh" ]]; then
    SHELL_RC="$HOME/.zshrc"
    COMPLETION_DIR="$HOME/.zsh/completions"
    
    # Create completion directory if it doesn't exist
    mkdir -p "$COMPLETION_DIR"
    
    # Copy completion file
    cp portf_completion.zsh "$COMPLETION_DIR/_portf"
    echo "✅ Installed zsh completion"
    
    # Add to fpath if not already present
    if ! grep -q "fpath.*$COMPLETION_DIR" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# Portfolio Manager CLI completions" >> "$SHELL_RC"
        echo "fpath=($COMPLETION_DIR \$fpath)" >> "$SHELL_RC"
        echo "autoload -Uz compinit && compinit" >> "$SHELL_RC"
        echo "✅ Added zsh completion to $SHELL_RC"
    else
        echo "ℹ️  zsh completion already configured in $SHELL_RC"
    fi
    
elif [[ "$SHELL_TYPE" == "bash" ]]; then
    SHELL_RC="$HOME/.bashrc"
    
    # Source completion file
    if ! grep -q "portf_completion.bash" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# Portfolio Manager CLI completions" >> "$SHELL_RC"
        echo "source $CURRENT_DIR/portf_completion.bash" >> "$SHELL_RC"
        echo "✅ Added bash completion to $SHELL_RC"
    else
        echo "ℹ️  bash completion already configured in $SHELL_RC"
    fi
fi

# Add current directory to PATH if not already present
if ! grep -q "export PATH.*$CURRENT_DIR" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# Portfolio Manager CLI" >> "$SHELL_RC"
    echo "export PATH=\"$CURRENT_DIR:\$PATH\"" >> "$SHELL_RC"
    echo "✅ Added $CURRENT_DIR to PATH in $SHELL_RC"
else
    echo "ℹ️  PATH already includes $CURRENT_DIR"
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "To start using the portf command:"
echo "1. Reload your shell: source $SHELL_RC"
echo "2. Or open a new terminal"
echo ""
echo "Then you can use:"
echo "  portf --help"
echo "  portf export-transactions --help"
echo "  portf export-to-sheets --help"
echo ""
echo "Tab completion should work for commands and options!"
