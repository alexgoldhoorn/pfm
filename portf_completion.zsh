#compdef portf ./portf

# Zsh completion for portf command

_portf() {
    local context state line
    typeset -A opt_args
    
    local commands=(
        'login:Login as a user'
        'register:Register a new user'
        'add-asset:Add a new asset'
        'add-transaction:Add a new asset transaction'
        'remove-asset:Remove an asset'
        'update-asset:Update an existing asset'
        'delete-asset:Delete an asset (soft delete)'
        'list-assets:List all assets'
        'list-sectors:List all GICS sectors'
        'show-mapping:Show ticker-to-sector mapping'
        'portfolio-value:Show current portfolio value and positions'
        'list-transactions:List and filter transactions'
        'delete-transaction:Delete a transaction by ID'
        'update-transaction:Update a transaction'
        'import-csv:Import transactions from CSV file'
        'add-entity:Add a new entity (broker, bank, etc.)'
        'list-entities:List all entities'
        'add-portfolio:Add a new portfolio'
        'list-portfolios:List all portfolios'
        'paste-transaction:Paste transaction text or CSV data'
        'export-transactions:Export filtered transactions to CSV file'
        'export-to-sheets:Export portfolio data to Google Sheets'
        'stock-report:Generate a stock analysis report'
        'chat:Interactive LLM chat about your portfolio'
        'extract-tax-report:Extract tax report with capital gains/losses'
        'update-prices:Update asset prices from external API'
    )
    
    _arguments -C \
        '--help[Show help message]' \
        '--server[Server URL for server mode]:server url:' \
        '--api-key[API key for server mode authentication]:api key:' \
        '--db-path[Path to the database file]:database path:_files' \
        '--debug[Enable debug mode]' \
        '--multi-agent[Launch interactive multi-agent analysis console]' \
        '--multi-agent-quick[Run quick multi-agent analysis]:symbols:' \
        '1: :_describe -t commands "portf commands" commands' \
        '*::arg:->args'
    
    case $state in
        args)
            case $words[1] in
                export-transactions)
                    _arguments \
                        '--help[Show help message]' \
                        '--symbol[Filter by asset symbol]:symbol:' \
                        '--start-date[Start date filter]:start date:' \
                        '--end-date[End date filter]:end date:' \
                        '--output[Output CSV file path]:output file:_files -g "*.csv"'
                    ;;
                export-to-sheets)
                    _arguments \
                        '--help[Show help message]' \
                        '--spreadsheet-id[Existing Google Spreadsheet ID]:spreadsheet id:' \
                        '--create-new[Always create a new spreadsheet]'
                    ;;
                import-csv)
                    _arguments \
                        '--help[Show help message]' \
                        '--format[Format of input data]:format:(myinvestor coinbase indexacapital)' \
                        '*:csv files:_files -g "*.csv"'
                    ;;
                add-asset)
                    _arguments \
                        '--help[Show help message]' \
                        '--exchange[Stock exchange]:exchange:' \
                        '--currency[Currency]:currency:' \
                        '--asset-type[Asset type]:asset type:(stock bond etf crypto)'
                    ;;
                *)
                    _arguments '--help[Show help message]'
                    ;;
            esac
            ;;
    esac
}

_portf "$@"
