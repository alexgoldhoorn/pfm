#!/bin/bash
# Bash completion for portf command

_portf_completion() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    
    # Main commands
    commands="login register add-asset add-transaction remove-asset update-asset delete-asset list-assets list-sectors show-mapping portfolio-value list-transactions delete-transaction update-transaction import-csv add-entity list-entities add-portfolio list-portfolios paste-transaction export-transactions export-to-sheets stock-report chat extract-tax-report update-prices"
    
    # Global options
    global_options="--help --server --api-key --db-path --debug --multi-agent --multi-agent-quick"
    
    # If we're completing the first argument (command)
    if [[ ${COMP_CWORD} == 1 ]] ; then
        COMPREPLY=( $(compgen -W "${commands} ${global_options}" -- ${cur}) )
        return 0
    fi
    
    # Get the command being used
    command="${COMP_WORDS[1]}"
    
    # Command-specific completions
    case "$command" in
        export-transactions)
            local export_opts="--symbol --start-date --end-date --output --help"
            COMPREPLY=( $(compgen -W "${export_opts}" -- ${cur}) )
            ;;
        export-to-sheets)
            local sheets_opts="--spreadsheet-id --create-new --help"
            COMPREPLY=( $(compgen -W "${sheets_opts}" -- ${cur}) )
            ;;
        add-asset)
            local asset_opts="--exchange --currency --asset-type --help"
            COMPREPLY=( $(compgen -W "${asset_opts}" -- ${cur}) )
            ;;
        import-csv)
            # Complete with CSV files
            COMPREPLY=( $(compgen -f -X '!*.csv' -- ${cur}) )
            ;;
        --output)
            # Complete with CSV files for output
            COMPREPLY=( $(compgen -f -X '!*.csv' -- ${cur}) )
            ;;
        *)
            # Default to --help for unknown commands
            COMPREPLY=( $(compgen -W "--help" -- ${cur}) )
            ;;
    esac
}

# Register completion for portf command
complete -F _portf_completion portf
complete -F _portf_completion ./portf
