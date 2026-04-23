#!/usr/bin/env bash
set -euo pipefail

# Instagram SuperPower -- Manage Watchlist
# Usage:
#   bash watchlist.sh list                -- показать все отслеживаемые аккаунты
#   bash watchlist.sh add <username>      -- добавить аккаунт
#   bash watchlist.sh remove <username>   -- удалить аккаунт
#   bash watchlist.sh scan [top_N] [days] -- проанализировать все аккаунты в списке

WATCHLIST="$HOME/.secrets/cobalt/watchlist.json"
ACTION="${1:?Usage: watchlist.sh list|add|remove|scan [args]}"

# Инициализация файла
if [ ! -f "$WATCHLIST" ]; then
    echo '{"accounts":[]}' > "$WATCHLIST"
    chmod 600 "$WATCHLIST"
fi

case "$ACTION" in
    list)
        python3 -c "
import json
wl = json.load(open('$WATCHLIST'))
accs = wl.get('accounts', [])
if not accs:
    print('Watchlist is empty')
else:
    print(f'Tracking {len(accs)} accounts:')
    for a in accs:
        print(f'  @{a}')
"
        ;;

    add)
        USERNAME="${2:?Usage: watchlist.sh add <username>}"
        USERNAME="${USERNAME#@}"  # убираем @ если есть
        python3 -c "
import json
wl = json.load(open('$WATCHLIST'))
accs = wl.get('accounts', [])
if '$USERNAME' in accs:
    print('@$USERNAME already in watchlist')
else:
    accs.append('$USERNAME')
    wl['accounts'] = accs
    json.dump(wl, open('$WATCHLIST', 'w'), indent=2)
    print('@$USERNAME added to watchlist')
    print(f'Total: {len(accs)} accounts')
"
        ;;

    remove)
        USERNAME="${2:?Usage: watchlist.sh remove <username>}"
        USERNAME="${USERNAME#@}"
        python3 -c "
import json
wl = json.load(open('$WATCHLIST'))
accs = wl.get('accounts', [])
if '$USERNAME' not in accs:
    print('@$USERNAME not in watchlist')
else:
    accs.remove('$USERNAME')
    wl['accounts'] = accs
    json.dump(wl, open('$WATCHLIST', 'w'), indent=2)
    print('@$USERNAME removed')
    print(f'Remaining: {len(accs)} accounts')
"
        ;;

    scan)
        TOP_N="${2:-3}"
        DAYS="${3:-14}"
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        
        ACCOUNTS=$(python3 -c "
import json
wl = json.load(open('$WATCHLIST'))
for a in wl.get('accounts', []):
    print(a)
")
        
        if [ -z "$ACCOUNTS" ]; then
            echo "Watchlist is empty. Add accounts first: watchlist.sh add <username>"
            exit 0
        fi
        
        COUNT=$(echo "$ACCOUNTS" | wc -l | tr -d ' ')
        echo "Scanning $COUNT accounts -- top $TOP_N reels, last $DAYS days"
        echo "Rate limit: 3 sec pause between accounts"
        echo ""
        
        while IFS= read -r acc; do
            echo "=========================================="
            echo "Analyzing @${acc}..."
            echo "=========================================="
            bash "$SCRIPT_DIR/analyze.sh" "$acc" "$TOP_N" "$DAYS" 2>&1 || echo "FAILED for @${acc}"
            echo ""
            # Пауза между аккаунтами для rate limit
            echo "Pausing 10 seconds before next account..."
            sleep 10
        done <<< "$ACCOUNTS"
        
        echo "Scan complete."
        ;;

    *)
        echo "Unknown action: $ACTION"
        echo "Usage: watchlist.sh list|add|remove|scan [args]"
        exit 1
        ;;
esac
