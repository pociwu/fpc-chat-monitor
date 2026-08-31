#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${WEBAUTO_APP_DIR:-$HOME/webauto_app}"
HISTORY_DIR="$APP_DIR/History"

if [[ ! -d "$APP_DIR" ]]; then
  echo "ERROR: application directory does not exist: $APP_DIR" >&2
  exit 1
fi

if [[ "${WEBAUTO_SKIP_CONDA:-0}" != "1" ]]; then
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
  conda activate webauto
fi

cd "$APP_DIR"
shopt -s nullglob

# Guard the first upgrade from older versions that did not hold the Python
# runtime lock.  Do not silently start a second monitor and duplicate Telegram.
if ! command -v pgrep >/dev/null 2>&1; then
  echo "ERROR: pgrep is required to verify that no older monitor is running." >&2
  exit 2
fi
pgrep_status=0
running_monitors="$(pgrep -u "$(id -u)" -af '(^|/|[[:space:]])python[^[:space:]]*[[:space:]].*fpc_watch_ui_login_telegram_.*\.py($|[[:space:]])')" || pgrep_status=$?
if (( pgrep_status > 1 )); then
  echo "ERROR: unable to inspect running monitor processes (pgrep exit $pgrep_status)." >&2
  exit 2
fi
if [[ -n "$running_monitors" ]]; then
  echo "ERROR: an FPC chat monitor is already running; stop it before launching another version:" >&2
  echo "$running_monitors" >&2
  exit 2
fi

versioned=(fpc_watch_ui_login_telegram_v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9].[0-9]*.py)
if (( ${#versioned[@]} == 0 )); then
  echo "ERROR: no versioned program found (expected fpc_watch_ui_login_telegram_vYYYY.MM.DD.N.py)" >&2
  exit 1
fi

latest="$(printf '%s\n' "${versioned[@]}" | sort -V | tail -n 1)"
mkdir -p "$HISTORY_DIR"

# A fast-forward pull can remove the previously tracked version before this
# launcher runs. Recover that immediate prior version from Git into History.
if git rev-parse --verify HEAD^ >/dev/null 2>&1; then
  previous="$(git ls-tree -r --name-only HEAD^ -- 'fpc_watch_ui_login_telegram_v*.py' | sort -V | tail -n 1)"
  if [[ -n "$previous" && "$previous" != "$latest" && ! -e "$APP_DIR/$previous" && ! -e "$HISTORY_DIR/$previous" ]]; then
    git show "HEAD^:$previous" > "$HISTORY_DIR/$previous"
    echo "Archived from Git: $previous -> History/$previous"
  fi
fi

for candidate in fpc_watch_ui_login_telegram_*.py; do
  [[ "$candidate" == "$latest" ]] && continue
  destination="$HISTORY_DIR/$candidate"
  if [[ -e "$destination" ]]; then
    destination="$HISTORY_DIR/${candidate%.py}_$(date +%Y%m%d_%H%M%S).py"
  fi
  mv -- "$candidate" "$destination"
  echo "Archived: $candidate -> ${destination#$APP_DIR/}"
done

echo "Starting: $latest"
exec python "$latest"
