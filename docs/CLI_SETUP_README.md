# Portfolio Manager CLI Setup

This guide explains how to set up the `portf` command with tab completion.

## 📋 **Problem**
The Portfolio Manager CLI commands like `export-transactions` and `export-to-sheets` work with `python -m portf_manager` but you want:
- Simple `portf` command instead of `python -m portf_manager`
- Tab completion for commands and options

## 🚀 **Quick Setup**

Run the setup script:
```bash
./setup_portf_cli.sh
```

Then reload your shell:
```bash
source ~/.zshrc    # for zsh
# or
source ~/.bashrc   # for bash
```

## ✅ **What This Does**

1. **Creates `portf` wrapper script** - Converts `python -m portf_manager` calls
2. **Adds tab completion** - Complete commands and options with Tab key
3. **Adds to PATH** - Use `portf` from anywhere in this directory
4. **Shell integration** - Works with both bash and zsh

## 🎯 **Usage Examples**

After setup, you can use:

```bash
# Basic commands
portf --help
portf list-assets
portf login

# Export commands (now working!)
portf export-transactions --help
portf export-transactions --symbol AAPL
portf export-transactions --output my_transactions.csv

portf export-to-sheets --help  
portf export-to-sheets --create-new
portf export-to-sheets --spreadsheet-id 1ABC...XYZ

# Tab completion works!
portf exp<Tab>           # Shows export-transactions, export-to-sheets
portf export-to-sheets --<Tab>  # Shows --spreadsheet-id, --create-new, --help
```

## 🔧 **Manual Setup (Alternative)**

If you prefer manual setup:

### 1. Make portf executable
```bash
chmod +x portf
```

### 2. Add to PATH (optional)
```bash
export PATH="$(pwd):$PATH"
echo 'export PATH="$(pwd):$PATH"' >> ~/.zshrc  # Add permanently
```

### 3. Enable tab completion
For **zsh**:
```bash
mkdir -p ~/.zsh/completions
cp portf_completion.zsh ~/.zsh/completions/_portf

# Add to ~/.zshrc:
echo 'fpath=(~/.zsh/completions $fpath)' >> ~/.zshrc
echo 'autoload -Uz compinit && compinit' >> ~/.zshrc
```

For **bash**:
```bash
# Add to ~/.bashrc:
echo "source $(pwd)/portf_completion.bash" >> ~/.bashrc
```

## 🧪 **Testing**

Test the setup:
```bash
# Test command recognition
portf --help

# Test tab completion (press Tab after typing)
portf <Tab>
portf export-<Tab>
portf export-to-sheets --<Tab>

# Test actual commands
portf list-assets
portf export-transactions --help
```

## 📁 **Files Created**

- `portf` - Main wrapper script
- `portf_completion.bash` - Bash tab completion
- `portf_completion.zsh` - Zsh tab completion  
- `setup_portf_cli.sh` - Automated setup script

## 🐛 **Troubleshooting**

**Tab completion not working?**
- Reload shell: `source ~/.zshrc` or `source ~/.bashrc`
- Check completion files are in the right place
- For zsh: `ls ~/.zsh/completions/_portf`

**Command not found?**
- Use `./portf` if not in PATH
- Or run setup script to add to PATH

**Permission denied?**
```bash
chmod +x portf
chmod +x setup_portf_cli.sh
```

## 🎉 **Success!**

You should now have:
- ✅ `portf` command working
- ✅ Tab completion for all commands
- ✅ `export-transactions` and `export-to-sheets` commands working
- ✅ Shell integration complete

The commands `export-transactions` and `export-to-sheets` now work perfectly with tab completion!
