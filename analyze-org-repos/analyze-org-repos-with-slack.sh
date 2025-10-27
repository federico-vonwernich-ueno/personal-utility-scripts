#!/bin/zsh
# Script para generar lista de repositorios con workflows de CI reutilizables
# Con integración de notificaciones Slack y análisis de métricas CSV
# Requiere: gh, jq, python3, bc, y acceso de lectura a los repositorios
#
# Uso: ./analyze-org-repos-with-slack.sh <ORGANIZATION> [LIMIT] [--csv-file <PATH>]
#   ORGANIZATION: Nombre de la organización de GitHub (requerido)
#   LIMIT: Número máximo de repositorios a analizar (opcional)
#          Si no se especifica, se analizan todos los repositorios
#   --csv-file: Archivo CSV con información de adopción y tecnología (opcional)
#               El CSV debe contener las columnas: Repositorio, Adopcion, Tecnología

#================================================================
# SHELL CONFIGURATION
#================================================================

setopt +o nomatch  # Evita que zsh falle si no hay coincidencias de globbing

#================================================================
# CONSTANTS AND CONFIGURATION
#================================================================

# Script directories
SCRIPT_DIR="${0:a:h}"
SLACK_NOTIFIER_DIR="${SCRIPT_DIR}/../slack-notifier"
SLACK_NOTIFIER_SDK_PYTHON="${SLACK_NOTIFIER_DIR}/slack_notifier_sdk.py"

# Logging configuration
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/analyze-org-repos-$(date '+%Y%m%d-%H%M%S').log"
: > "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)

# Rate limit configuration
MAX_WAIT_TIME=600  # Maximum time to wait for rate limit reset (10 minutes)
RATE_LIMIT_THRESHOLD=100  # Start adaptive throttling when remaining < this
MIN_DELAY=0.1  # Minimum delay between requests (seconds)
MAX_DELAY=2.0  # Maximum delay between requests (seconds)

# Checkpoint configuration
CHECKPOINT_FILE="${SCRIPT_DIR}/.analyze-progress-checkpoint.txt"

# Output files - repositories with unified CI
MAVEN_OUTPUT_FILE="maven-repos.yml"
GRADLE_OUTPUT_FILE="gradle-repos.yml"
NODE_OUTPUT_FILE="node-repos.yml"
GO_OUTPUT_FILE="go-repos.yml"
FLUTTER_OUTPUT_FILE="flutter-repos.yml"

# Output files - repositories without unified CI
MAVEN_WITHOUT_CI_FILE="maven-repos-without-ci.yml"
GRADLE_WITHOUT_CI_FILE="gradle-repos-without-ci.yml"
NODE_WITHOUT_CI_FILE="node-repos-without-ci.yml"
GO_WITHOUT_CI_FILE="go-repos-without-ci.yml"
FLUTTER_WITHOUT_CI_FILE="flutter-repos-without-ci.yml"

# Output file - repositories that failed analysis
FAILED_REPOS_FILE="failed-repos.txt"

# Output files - CSV analysis reports
TECH_MISMATCHES_FILE="technology-mismatches.txt"
TECH_ADOPTION_DIST_FILE="technology-adoption-distribution.txt"

# Project type counters
maven_repos=0
gradle_repos=0
node_repos=0
go_repos=0
flutter_repos=0
other_repos=0

# CI detection counters
maven_ci_repos=0
gradle_ci_repos=0
node_ci_repos=0
go_ci_repos=0
flutter_ci_repos=0

# Archive tracking
archived_repos=0

# Failure tracking
failed_repos=0
typeset -A FAILED_REPOS  # Associative array: repo_name -> error_message

# Rate limit tracking variables
last_rate_limit_check=0
rate_limit_remaining=5000
rate_limit_reset=0

# CSV data tracking
typeset -A CSV_ADOPTION        # Maps repo_url -> adoption value
typeset -A CSV_TECHNOLOGY      # Maps repo_url -> technology value
typeset -A ADOPTION_COUNTERS   # Global adoption state counts
typeset -A TECH_ADOPTION_COUNTERS  # Technology+adoption combo counts (e.g., "maven:Adoptado")
typeset -A TECH_IN_CSV_COUNT   # Count of each tech found in CSV
typeset -A TECH_MISMATCHES     # Maps repo -> "CSV_tech vs Detected_tech"
tech_matches=0
tech_mismatches=0
repos_in_csv=0
not_in_csv_repos=0

#================================================================
# INPUT PARAMETERS AND VALIDATION
#================================================================

# Parse command line arguments
ORG=""
REPO_LIMIT=0
CSV_FILE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --csv-file)
      CSV_FILE="$2"
      shift 2
      ;;
    *)
      # Positional arguments
      if [[ -z "$ORG" ]]; then
        ORG="$1"
      elif [[ "$REPO_LIMIT" -eq 0 ]]; then
        REPO_LIMIT="$1"
      else
        echo "Error: Argumento desconocido: $1"
        echo "Uso: $0 <ORGANIZATION> [LIMIT] [--csv-file <PATH>]"
        exit 1
      fi
      shift
      ;;
  esac
done

# Validate organization parameter
if [[ -z "$ORG" ]]; then
  echo "Error: Debe especificar el nombre de la organización"
  echo "Uso: $0 <ORGANIZATION> [LIMIT] [--csv-file <PATH>]"
  exit 1
fi

# Validate repository limit parameter
if [[ "$REPO_LIMIT" != "0" ]] && ! [[ "$REPO_LIMIT" =~ ^[0-9]+$ ]]; then
  echo "Error: El límite debe ser un número entero positivo"
  echo "Uso: $0 <ORGANIZATION> [LIMIT] [--csv-file <PATH>]"
  exit 1
fi

# Validate CSV file if provided
if [[ -n "$CSV_FILE" ]]; then
  if [[ ! -f "$CSV_FILE" ]]; then
    echo "Error: El archivo CSV no existe: $CSV_FILE"
    exit 1
  fi
  if [[ ! -r "$CSV_FILE" ]]; then
    echo "Error: El archivo CSV no es legible: $CSV_FILE"
    exit 1
  fi
  echo "✓ Archivo CSV encontrado: $CSV_FILE"
fi

#================================================================
# UTILITY FUNCTIONS
#================================================================

# Normalize repository URL for consistent comparison
# Args: $1 - repository URL
# Returns: normalized URL via stdout
normalize_repo_url() {
  local url="$1"
  # Remove trailing slashes
  url="${url%/}"
  # Remove .git suffix
  url="${url%.git}"
  # Convert http to https
  url="${url/http:\/\//https://}"
  echo "$url"
}

# Normalize technology name for comparison
# Args: $1 - technology name from CSV
# Returns: normalized tech name (maven|gradle|node|go|flutter|other) via stdout
normalize_tech() {
  local tech="$1"
  # Convert to lowercase
  tech="${tech:l}"

  # Map variations to canonical names
  case "$tech" in
    *maven*|*java/maven*)
      echo "maven"
      ;;
    *gradle*|*java/gradle*)
      echo "gradle"
      ;;
    *node*|*nodejs*|*javascript*|*js*)
      echo "node"
      ;;
    *golang*|*go*)
      echo "go"
      ;;
    *flutter*|*dart*)
      echo "flutter"
      ;;
    "")
      echo ""
      ;;
    *)
      echo "other"
      ;;
  esac
}

# Parse CSV file and populate global associative arrays
# Args: $1 - CSV file path
# Returns: 0 on success, 1 on error
parse_csv_file() {
  local csv_file="$1"

  echo "📊 Analizando archivo CSV..."

  # Use Python to parse CSV robustly
  local parse_result
  parse_result=$(python3 <<EOF
import csv
import sys
import json

try:
    with open('$csv_file', 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)

        # Check if required columns exist
        if not reader.fieldnames:
            print('ERROR: CSV file is empty or malformed', file=sys.stderr)
            sys.exit(1)

        # Find column names (case-insensitive)
        repo_col = None
        adoption_col = None
        tech_col = None

        for field in reader.fieldnames:
            field_lower = field.lower().strip()
            if 'repositorio' in field_lower:
                repo_col = field
            elif 'adopcion' in field_lower or 'adopción' in field_lower:
                adoption_col = field
            elif 'tecnolog' in field_lower:
                tech_col = field

        if not repo_col:
            print('ERROR: Column "Repositorio" not found in CSV', file=sys.stderr)
            sys.exit(1)

        # Parse rows
        data = []
        for row in reader:
            repo = row.get(repo_col, '').strip()
            adoption = row.get(adoption_col, '').strip() if adoption_col else ''
            tech = row.get(tech_col, '').strip() if tech_col else ''

            if repo:  # Only include rows with repository URL
                data.append({
                    'repo': repo,
                    'adoption': adoption,
                    'tech': tech
                })

        print(json.dumps(data))

except Exception as e:
    print(f'ERROR: {str(e)}', file=sys.stderr)
    sys.exit(1)
EOF
)

  local exit_code=$?
  if (( exit_code != 0 )); then
    echo "❌ Error al analizar CSV: $parse_result" >&2
    return 1
  fi

  # Parse JSON output and populate arrays
  local count=0
  while IFS= read -r line; do
    local repo=$(echo "$line" | jq -r '.repo')
    local adoption=$(echo "$line" | jq -r '.adoption')
    local tech=$(echo "$line" | jq -r '.tech')

    # Normalize repository URL
    repo=$(normalize_repo_url "$repo")

    # Store in associative arrays
    CSV_ADOPTION[$repo]="$adoption"
    CSV_TECHNOLOGY[$repo]="$tech"
    count=$((count + 1))
  done < <(echo "$parse_result" | jq -c '.[]')

  echo "✓ CSV parseado exitosamente: $count repositorios encontrados"
  return 0
}

# Decode base64 content with fallback for different OS (GNU/macOS)
# Args: $1 - base64 encoded string
# Returns: decoded string via stdout
decode_base64() {
  local input="$1"
  input="$(printf '%s' "$input" | tr -d '\r')"
  if printf '%s' "$input" | base64 --decode >/dev/null 2>&1; then
    printf '%s' "$input" | base64 --decode 2>/dev/null || true
    return 0
  elif printf '%s' "$input" | base64 -D >/dev/null 2>&1; then
    printf '%s' "$input" | base64 -D 2>/dev/null || true
    return 0
  else
    return 1
  fi
}

#================================================================
# GITHUB API FUNCTIONS
#================================================================

# Check and handle GitHub API rate limits
# Returns: 0 on success, 2 if rate limit critical (caller should exit)
check_rate_limit() {
  local current_time=$(date +%s)
  # Check rate limit every 30 seconds
  if (( current_time - last_rate_limit_check < 30 )); then
    return 0
  fi
  last_rate_limit_check=$current_time

  # Get current rate limit status
  local rate_info=$(gh api rate_limit 2>/dev/null || echo "{}")
  rate_limit_remaining=$(echo "$rate_info" | jq -r '.resources.core.remaining // 5000')
  rate_limit_reset=$(echo "$rate_info" | jq -r '.resources.core.reset // 0')
  local rate_limit=$(echo "$rate_info" | jq -r '.resources.core.limit // 5000')
  echo "[INFO] Rate limit status: $rate_limit_remaining/$rate_limit remaining" >&2

  # If rate limit is very low, wait or exit
  if (( rate_limit_remaining < 10 )); then
    local wait_time=$((rate_limit_reset - current_time))
    if (( wait_time > MAX_WAIT_TIME )); then
      echo "⚠️  Rate limit critical ($rate_limit_remaining remaining). Reset in ${wait_time}s (>${MAX_WAIT_TIME}s)." >&2
      echo "💾 Saving progress and exiting gracefully..." >&2
      save_checkpoint
      return 2  # Signal to exit
    elif (( wait_time > 0 )); then
      echo "⏳ Rate limit low ($rate_limit_remaining remaining). Waiting ${wait_time}s until reset..." >&2
      sleep "$wait_time"
      echo "✅ Rate limit reset. Continuing..." >&2
    fi
  fi
  return 0
}

# Calculate adaptive delay based on current rate limit status
# Returns: delay in seconds via stdout
get_adaptive_delay() {
  local remaining=$rate_limit_remaining
  local delay=$MIN_DELAY

  # Adaptive throttling based on remaining calls
  if (( remaining < 50 )); then
    delay=$MAX_DELAY
  elif (( remaining < 100 )); then
    delay=1.0
  elif (( remaining < 500 )); then
    delay=0.5
  elif (( remaining < 1000 )); then
    delay=0.3
  fi

  echo "$delay"
}

# Sleep to throttle API requests based on current rate limit
throttle_request() {
  local delay=$(get_adaptive_delay)
  sleep "$delay"
}

# Make a safe GitHub API call with rate limit handling and retries
# Args: passes all arguments to 'gh api' command
# Returns: API response via stdout, exit code 0 on success, 2 if rate limit critical
safe_gh_api() {
  local max_retries=3
  local retry=0
  local backoff=1

  while (( retry < max_retries )); do
    # Check rate limit before making request
    if ! check_rate_limit; then
      exit_code=$?
      if (( exit_code == 2 )); then
        return 2  # Signal to exit script
      fi
    fi

    # Throttle request
    throttle_request

    # Make the API call
    local output
    local http_code
    output=$(gh api "$@" 2>&1)
    local result=$?

    # Success
    if (( result == 0 )); then
      echo "$output"
      return 0
    fi

    # Check if it's a rate limit error (403 or 429)
    if echo "$output" | grep -qi "rate limit\|API rate limit exceeded"; then
      retry=$((retry + 1))
      local current_time=$(date +%s)
      local wait_time=$((rate_limit_reset - current_time))

      # If we don't have reset time, use exponential backoff
      if (( wait_time <= 0 )); then
        wait_time=$((backoff * 60))
        backoff=$((backoff * 2))
      fi

      if (( wait_time > MAX_WAIT_TIME )); then
        echo "⚠️  Rate limit exceeded. Reset time too far (${wait_time}s). Saving progress..." >&2
        save_checkpoint
        return 2
      fi

      echo "⏳ Rate limit hit (attempt $retry/$max_retries). Waiting ${wait_time}s..." >&2
      sleep "$wait_time"

      # Refresh rate limit info
      check_rate_limit
      continue
    fi

    # Check if it's a network error (timeout, TLS, connection issues)
    if echo "$output" | grep -qi "timeout\|TLS handshake\|connection refused\|connection reset\|network is unreachable\|temporary failure in name resolution\|error connecting to"; then
      retry=$((retry + 1))
      if (( retry >= max_retries )); then
        echo "❌ Network error after $max_retries attempts: $output" >&2
        echo "$output"
        return 1
      fi

      # Exponential backoff for network errors: 2s, 4s, 8s
      local wait_time=$((2 ** retry))
      echo "⏳ Network error detected (attempt $retry/$max_retries). Retrying in ${wait_time}s..." >&2
      echo "   Error: $(echo "$output" | head -1)" >&2
      sleep "$wait_time"
      continue
    fi

    # Other error - return it
    echo "$output"
    return $result
  done

  # Max retries exceeded
  echo "❌ Max retries exceeded for gh api call" >&2
  return 1
}

# Make a safe GitHub repo view call with retry logic
# Args: passes all arguments to 'gh repo view' command
# Returns: command output via stdout, exit code 0 on success, 1 on failure after retries
safe_gh_repo_view() {
  local max_retries=3
  local retry=0

  while (( retry < max_retries )); do
    # Make the command call
    local output
    output=$(gh repo view "$@" 2>&1)
    local result=$?

    # Success
    if (( result == 0 )); then
      echo "$output"
      return 0
    fi

    # Check if it's a network error
    if echo "$output" | grep -qi "timeout\|TLS handshake\|connection refused\|connection reset\|network is unreachable\|temporary failure in name resolution\|error connecting to"; then
      retry=$((retry + 1))
      if (( retry >= max_retries )); then
        echo "❌ Network error after $max_retries attempts: $output" >&2
        echo "$output"
        return 1
      fi

      # Exponential backoff for network errors: 2s, 4s, 8s
      local wait_time=$((2 ** retry))
      echo "⏳ Network error in gh repo view (attempt $retry/$max_retries). Retrying in ${wait_time}s..." >&2
      echo "   Error: $(echo "$output" | head -1)" >&2
      sleep "$wait_time"
      continue
    fi

    # Other error - return it
    echo "$output"
    return $result
  done

  # Max retries exceeded
  echo "❌ Max retries exceeded for gh repo view" >&2
  return 1
}

#================================================================
# CHECKPOINT FUNCTIONS
#================================================================

# Save current progress to checkpoint file
# Args: $1 - last processed repository name
save_checkpoint() {
  local last_repo="$1"
  cat > "$CHECKPOINT_FILE" <<EOF
# Checkpoint file - DO NOT EDIT MANUALLY
LAST_PROCESSED_REPO="$last_repo"
MAVEN_REPOS=$maven_repos
GRADLE_REPOS=$gradle_repos
NODE_REPOS=$node_repos
GO_REPOS=$go_repos
FLUTTER_REPOS=$flutter_repos
OTHER_REPOS=$other_repos
MAVEN_CI_REPOS=$maven_ci_repos
GRADLE_CI_REPOS=$gradle_ci_repos
NODE_CI_REPOS=$node_ci_repos
GO_CI_REPOS=$go_ci_repos
FLUTTER_CI_REPOS=$flutter_ci_repos
ARCHIVED_REPOS=$archived_repos
FAILED_REPOS_COUNT=$failed_repos
TOTAL_REPOS=$total_repos
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
EOF

  # Save failed repos associative array
  if (( failed_repos > 0 )); then
    echo "" >> "$CHECKPOINT_FILE"
    echo "# Failed repositories" >> "$CHECKPOINT_FILE"
    echo "typeset -A FAILED_REPOS" >> "$CHECKPOINT_FILE"
    for repo in "${(@k)FAILED_REPOS}"; do
      local error_msg="${FAILED_REPOS[$repo]}"
      # Escape quotes in error message
      error_msg="${error_msg//\"/\\\"}"
      echo "FAILED_REPOS[\"$repo\"]=\"$error_msg\"" >> "$CHECKPOINT_FILE"
    done
  fi

  echo "💾 Progress saved to checkpoint file: $CHECKPOINT_FILE"
}

# Load progress from checkpoint file
# Returns: 0 if checkpoint found and loaded, 1 otherwise
load_checkpoint() {
  if [[ -f "$CHECKPOINT_FILE" ]]; then
    echo "📂 Found checkpoint file. Loading previous progress..."
    source "$CHECKPOINT_FILE"

    # Restore failed_repos counter from checkpoint
    if [[ -n "${FAILED_REPOS_COUNT:-}" ]]; then
      failed_repos=$FAILED_REPOS_COUNT
    fi

    echo "   Last processed: $LAST_PROCESSED_REPO"
    echo "   Timestamp: $TIMESTAMP"
    echo "   Total repos processed: $TOTAL_REPOS"
    if (( failed_repos > 0 )); then
      echo "   Failed repos: $failed_repos"
    fi
    return 0
  fi
  return 1
}

# Remove checkpoint file
clear_checkpoint() {
  if [[ -f "$CHECKPOINT_FILE" ]]; then
    rm -f "$CHECKPOINT_FILE"
    echo "🗑️  Checkpoint file cleared"
  fi
}

#================================================================
# SLACK NOTIFICATION FUNCTIONS
#================================================================

# Send Slack notification using slack_notifier_sdk.py
# Args:
#   $1 - title
#   $2 - message (markdown)
#   $3 - status (INFO|SUCCESS|ERROR|WARNING|DEBUG)
#   $4 - fields in format key:value,key2:val2 (optional)
#   $5 - space-separated list of files to attach (optional)
# Returns: 0 on success, non-zero on failure
send_slack_notification() {
    local title="$1"; shift
    local message="$1"; shift
    local notif_status="$1"; shift
    local fields="${1:-}"; shift || true
    local files_list="${1:-}"; shift || true

    # Check for required environment variables (skip if dry-run)
    local dry_run_flag="${SLACK_DRY_RUN:-}"
    if [[ -z "$SLACK_BOT_TOKEN" && -z "$dry_run_flag" ]]; then
      echo "[INFO] SLACK_BOT_TOKEN no definido: omitiendo envío Slack (title='$title')" >&2
      return 3
    fi
    if [[ -z "$SLACK_CHANNEL" && -z "$dry_run_flag" ]]; then
      echo "[INFO] SLACK_CHANNEL no definido: omitiendo envío Slack (title='$title')" >&2
      return 4
    fi

    # Normalize status to lowercase
    local status_lc="${notif_status:l}"
    case "$status_lc" in
      success|error|failure|warning|info|debug) ;;
      *) status_lc="info" ;;
    esac

    # Check if SDK script exists
    if [[ ! -f "$SLACK_NOTIFIER_SDK_PYTHON" ]]; then
      echo "[WARNING] Slack notifier SDK no encontrado en: $SLACK_NOTIFIER_SDK_PYTHON" >&2
      return 2
    fi

    # Select template based on status
    local template_name="simple"
    case "$status_lc" in
      success) template_name="workflow_success" ;;
      failure|error) template_name="workflow_failure" ;;
      *) template_name="simple" ;;
    esac

    # Convert fields to template variables and metadata
    local -a template_vars_args=()
    local metadata_md=""
    if [[ -n "$fields" ]]; then
      local IFS=','
      for pair in $fields; do
        [[ -z "$pair" ]] && continue
        if [[ "$pair" == *:* ]]; then
          local k="${pair%%:*}"; local v="${pair#*:}"
          local bullet_key="$k"
          metadata_md+=$'• '*"${bullet_key}"$': '*"${v}"$'\n'
          # Sanitize key for variable name
          local k_clean="${k//[^A-Za-z0-9]/_}"
            k_clean="${k_clean:u}"
            case "$k_clean" in
              TITLE|MESSAGE|STATUS|ICON|METADATA) k_clean="META_${k_clean}" ;;
            esac
            template_vars_args+=(--var "${k_clean}=${v}")
        fi
      done
    fi

    # Add metadata header if present
    if [[ -n "$metadata_md" ]]; then
      metadata_md="*Metadatos:*\n${metadata_md%\n}"
      template_vars_args+=(--var "METADATA=${metadata_md}")
    fi

    # Add additional useful variables
    template_vars_args+=(--var "ORG=${ORG}")
    template_vars_args+=(--var "REPO_LIMIT=${REPO_LIMIT}")

    # Build base command
    local -a cmd=(python3 "$SLACK_NOTIFIER_SDK_PYTHON" --title "$title" --status "$status_lc" --template "$template_name" ${template_vars_args[@]})

    # Add message if not empty
    if [[ -n "$message" ]]; then
      cmd+=(--message "$message")
    fi

    # Add dry-run flag if set
    if [[ -n "$dry_run_flag" ]]; then
      cmd+=(--dry-run)
      echo "[DRY-RUN] (Slack)" >&2
    fi

    # Add files if present
    if [[ -n "$files_list" ]]; then
      local -a valid_files=()
      for f in ${(z)files_list}; do
        if [[ -f "$f" ]]; then
          valid_files+="$f"
        else
          echo "[INFO] Archivo para adjuntar no encontrado (omitido): $f" >&2
        fi
      done
      if (( ${#valid_files[@]} > 0 )); then
        cmd+=(--files ${valid_files[@]})
      fi
    fi

    echo "Enviando notificación Slack: '$title' (status=$status_lc, template=$template_name)" >&2

    # Execute command and capture output
    echo "----- INICIO SLACK NOTIFIER OUTPUT -----" >&2
    tmp_out="$(mktemp -t slack-notifier.XXXXXX 2>/dev/null || mktemp)"
    if "${cmd[@]}" >"$tmp_out" 2>&1; then
      rc=0
    else
      rc=$?
    fi
    sed 's/^/   [SLACK] /' "$tmp_out" >&2
    echo "----- FIN SLACK NOTIFIER OUTPUT (rc=$rc) -----" >&2
    rm -f "$tmp_out" 2>/dev/null || true

    if (( rc != 0 )); then
      echo "[WARNING] Falló el envío de notificación Slack (rc=$rc, title='$title')" >&2
    fi
    return $rc
}

#================================================================
# CSV REPORTING FUNCTIONS
#================================================================

# Generate technology mismatches report file
# Returns: 0 on success
generate_tech_mismatches_report() {
  if (( tech_mismatches == 0 )); then
    return 0
  fi

  echo "📄 Generando reporte de discrepancias de tecnología..."

  : > "$TECH_MISMATCHES_FILE"  # Clear/create file
  cat >> "$TECH_MISMATCHES_FILE" <<EOF
# Discrepancias en Anotación de Tecnología
# Fecha: $(date '+%Y-%m-%d %H:%M:%S')
# Total de discrepancias: $tech_mismatches

REPOSITORIO | URL | TECNOLOGÍA CSV | TECNOLOGÍA DETECTADA | ADOPCIÓN
------------|-----|----------------|---------------------|----------
EOF

  for repo in "${(@k)TECH_MISMATCHES}"; do
    local repo_url="https://github.com/$ORG/$repo"
    local normalized_url=$(normalize_repo_url "$repo_url")
    local mismatch_info="${TECH_MISMATCHES[$repo]}"
    local adoption="${CSV_ADOPTION[$normalized_url]:-N/A}"

    # Parse mismatch info: "CSV: tech_name (normalized) vs Detected: tech_name"
    local csv_tech=$(echo "$mismatch_info" | sed -n 's/^CSV: \(.*\) (.*) vs Detected: .*$/\1/p')
    local detected_tech=$(echo "$mismatch_info" | sed -n 's/^.* vs Detected: \(.*\)$/\1/p')

    echo "$repo | $repo_url | $csv_tech | $detected_tech | $adoption" >> "$TECH_MISMATCHES_FILE"
  done

  echo "✓ Reporte generado: $TECH_MISMATCHES_FILE"
  return 0
}

# Calculate and format technology-adoption distribution
# Returns: formatted text via stdout
calculate_tech_adoption_distribution() {
  local output=""

  # For each technology that has repos in CSV
  for tech in maven gradle node go flutter other; do
    local tech_count=${TECH_IN_CSV_COUNT[$tech]:-0}

    if (( tech_count == 0 )); then
      continue
    fi

    # Capitalize tech name for display
    local tech_display="${tech:u:0:1}${tech:1}"

    output+="${tech_display} ($tech_count repos in CSV):\n"

    # Find all adoption states for this tech
    typeset -A adoption_states
    for key in "${(@k)TECH_ADOPTION_COUNTERS}"; do
      if [[ "$key" == "$tech:"* ]]; then
        local adoption="${key#*:}"
        local count=${TECH_ADOPTION_COUNTERS[$key]}
        adoption_states[$adoption]=$count
      fi
    done

    # Sort adoption states by count (descending) and display
    for adoption in "${(@k)adoption_states}"; do
      local count=${adoption_states[$adoption]}
      local percentage=$(printf "%.1f" $(echo "scale=2; $count * 100 / $tech_count" | bc))
      output+="  • $adoption: $count repos ($percentage%)\n"
    done

    output+="\n"
  done

  echo "$output"
}

# Generate technology-adoption distribution report file
# Returns: 0 on success
generate_tech_adoption_distribution_report() {
  if (( repos_in_csv == 0 )); then
    return 0
  fi

  echo "📄 Generando reporte de distribución tecnología-adopción..."

  : > "$TECH_ADOPTION_DIST_FILE"  # Clear/create file
  cat >> "$TECH_ADOPTION_DIST_FILE" <<EOF
# Distribución de Adopción por Tecnología
# Fecha: $(date '+%Y-%m-%d %H:%M:%S')
# Total de repositorios en CSV: $repos_in_csv

## Resumen Global de Adopción

EOF

  # Global adoption distribution
  for adoption in "${(@k)ADOPTION_COUNTERS}"; do
    local count=${ADOPTION_COUNTERS[$adoption]}
    local percentage=$(printf "%.1f" $(echo "scale=2; $count * 100 / $repos_in_csv" | bc))
    echo "$adoption: $count repos ($percentage%)" >> "$TECH_ADOPTION_DIST_FILE"
  done

  cat >> "$TECH_ADOPTION_DIST_FILE" <<EOF

## Distribución por Tecnología

EOF

  # Technology-specific distribution
  calculate_tech_adoption_distribution >> "$TECH_ADOPTION_DIST_FILE"

  echo "✓ Reporte generado: $TECH_ADOPTION_DIST_FILE"
  return 0
}

# Display CSV metrics summary
# Returns: formatted summary text via stdout
display_csv_metrics_summary() {
  if [[ -z "$CSV_FILE" || $repos_in_csv -eq 0 ]]; then
    return 0
  fi

  echo "==============================="
  echo "Métricas de CSV"
  echo "==============================="
  echo "Total de repositorios analizados: $total_repos"
  echo "Repositorios encontrados en CSV: $repos_in_csv"
  echo "Repositorios NO en CSV: $not_in_csv_repos"

  if (( repos_in_csv > 0 )); then
    echo ""
    echo "-------------------------------"
    echo "Distribución Global de Adopción:"
    for adoption in "${(@k)ADOPTION_COUNTERS}"; do
      local count=${ADOPTION_COUNTERS[$adoption]}
      local percentage=$(printf "%.1f" $(echo "scale=2; $count * 100 / $repos_in_csv" | bc))
      echo "  - $adoption: $count ($percentage%)"
    done

    local total_with_tech=$((tech_matches + tech_mismatches))
    if (( total_with_tech > 0 )); then
      echo ""
      echo "-------------------------------"
      echo "Precisión de Anotación de Tecnología:"
      echo "  - Total con tecnología anotada: $total_with_tech"
      echo "  - Correctamente anotados: $tech_matches ($(printf "%.1f" $(echo "scale=2; $tech_matches * 100 / $total_with_tech" | bc))%)"
      echo "  - Anotaciones incorrectas: $tech_mismatches ($(printf "%.1f" $(echo "scale=2; $tech_mismatches * 100 / $total_with_tech" | bc))%)"
    fi

    echo ""
    echo "-------------------------------"
    echo "Distribución por Tecnología:"
    echo ""
    calculate_tech_adoption_distribution
  fi

  echo "==============================="
}

#================================================================
# MAIN EXECUTION
#================================================================

# Record start time
START_TIME=$(date +%s)
START_TIME_FORMATTED=$(date '+%Y-%m-%d %H:%M:%S')

echo "\n==============================="
echo "Iniciando análisis de repositorios en la organización: $ORG"
echo "Hora de inicio: $START_TIME_FORMATTED"
if [[ $REPO_LIMIT -gt 0 ]]; then
  echo "Límite de repositorios: $REPO_LIMIT"
else
  echo "Límite de repositorios: Sin límite (análisis completo)"
fi
echo "==============================="

# Check for checkpoint and ask user if they want to resume
RESUME_FROM_CHECKPOINT=false
LAST_PROCESSED_REPO=""
if load_checkpoint; then
  echo ""
  echo "❓ ¿Desea continuar desde el último checkpoint? (y/n) [10s timeout, default=n]"
  read -r -t 10 response || response="n"
  if [[ "$response" =~ ^[Yy]$ ]]; then
    RESUME_FROM_CHECKPOINT=true
    echo "▶️  Resumiendo desde checkpoint..."
    echo "   Último repositorio procesado: $LAST_PROCESSED_REPO"
  else
    clear_checkpoint
    echo "🔄 Iniciando análisis desde cero..."
  fi
else
  echo "🆕 No se encontró checkpoint. Iniciando análisis desde cero..."
fi
echo ""

# Parse CSV file if provided
if [[ -n "$CSV_FILE" ]]; then
  if ! parse_csv_file "$CSV_FILE"; then
    echo "⚠️  Advertencia: No se pudo parsear el archivo CSV. Continuando sin datos de CSV."
    CSV_FILE=""  # Disable CSV processing
  fi
  echo ""
fi

# Clear/initialize output files when starting from scratch
if [[ "$RESUME_FROM_CHECKPOINT" != "true" ]]; then
  echo "🗑️  Limpiando archivos de salida anteriores..."
  : > "$MAVEN_OUTPUT_FILE"
  : > "$GRADLE_OUTPUT_FILE"
  : > "$NODE_OUTPUT_FILE"
  : > "$GO_OUTPUT_FILE"
  : > "$FLUTTER_OUTPUT_FILE"
  : > "$MAVEN_WITHOUT_CI_FILE"
  : > "$GRADLE_WITHOUT_CI_FILE"
  : > "$NODE_WITHOUT_CI_FILE"
  : > "$GO_WITHOUT_CI_FILE"
  : > "$FLUTTER_WITHOUT_CI_FILE"
  echo "✅ Archivos de salida inicializados"
fi

# Prepare start message with limit information
LIMIT_MSG=""
if [[ $REPO_LIMIT -gt 0 ]]; then
  LIMIT_MSG=" (limitado a $REPO_LIMIT repositorios)"
fi

# Send start notification
if send_slack_notification \
    "📊 Análisis de Repositorios - Iniciado" \
    "Iniciando análisis de repositorios en la organización \`$ORG\`$LIMIT_MSG" \
    "INFO" \
    "Organización:$ORG,Inicio:$START_TIME_FORMATTED,Límite:${REPO_LIMIT:-Sin límite}" ""; then
  echo "\n✓ Notificación de inicio enviada a Slack"
else
  echo "\n⚠ No se pudo enviar la notificación de inicio a Slack (ver mensajes anteriores)"
fi

echo "\n==============================="
echo "Obteniendo lista de repositorios..."
echo "===============================\n"

# Fetch all repositories using GraphQL with pagination
REPOS=()
typeset -A REPO_ARCHIVED  # Associative array: repo_name -> true/false
END_CURSOR=""
page_count=0

# Define GraphQL query
read -r -d '' REPOS_QUERY <<'EOF'
query($owner: String!, $endCursor: String) {
  organization(login: $owner) {
    repositories(first: 100, after: $endCursor) {
      nodes {
        name
        isArchived
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
EOF

echo "Fetching repository list from organization..."

while true; do
  page_count=$((page_count + 1))
  echo "  Fetching page $page_count..."
  # Call API with or without cursor
  if [[ -z "$END_CURSOR" ]]; then
    PAGE_REPOS=$(safe_gh_api graphql -F owner="$ORG" -f query="$REPOS_QUERY")
  else
    PAGE_REPOS=$(safe_gh_api graphql -F owner="$ORG" -F endCursor="$END_CURSOR" -f query="$REPOS_QUERY")
  fi
  api_result=$?
  if (( api_result == 2 )); then
    echo "⚠️  Rate limit exceeded during repository fetch. Exiting gracefully..."
    exit 3
  fi
  if (( api_result != 0 )); then
    echo "❌ Error fetching repositories: $PAGE_REPOS"
    exit 1
  fi
  REPO_DATA=$(echo "$PAGE_REPOS" | jq -r '.data.organization.repositories' 2>/dev/null)
  if [[ -z "$REPO_DATA" ]] || [[ "$REPO_DATA" == "null" ]]; then
    echo "❌ Failed to parse repository data. Raw response (first 5 lines):"
    echo "$PAGE_REPOS" | head -5
    exit 1
  fi
  NEW_REPOS=($(echo "$REPO_DATA" | jq -r '.nodes[].name' 2>/dev/null))
  if [[ ${#NEW_REPOS[@]} -eq 0 ]]; then
    echo "⚠️  No repositories found in this page"
    break
  fi

  # Store archived status for each repository
  while IFS= read -r repo_name; do
    [[ -z "$repo_name" ]] && continue
    local is_archived=$(echo "$REPO_DATA" | jq -r ".nodes[] | select(.name == \"$repo_name\") | .isArchived" 2>/dev/null)
    REPO_ARCHIVED[$repo_name]="$is_archived"
  done <<< "$(printf '%s\n' "${NEW_REPOS[@]}")"

  REPOS+=(${NEW_REPOS[@]})
  echo "    Found ${#NEW_REPOS[@]} repositories (total: ${#REPOS[@]})"
  HAS_NEXT_PAGE=$(echo "$REPO_DATA" | jq -r '.pageInfo.hasNextPage' 2>/dev/null)
  END_CURSOR=$(echo "$REPO_DATA" | jq -r '.pageInfo.endCursor // ""' 2>/dev/null)
  if [[ "$HAS_NEXT_PAGE" != "true" ]]; then
    echo "  ✅ All pages fetched"
    break
  fi

done

total_repos=${#REPOS[@]}
echo "Se encontraron $total_repos repositorios en total.\n"

# Apply repository limit if configured
if [[ $REPO_LIMIT -gt 0 ]] && [[ $total_repos -gt $REPO_LIMIT ]]; then
  echo "⚠ Aplicando límite: analizando solo los primeros $REPO_LIMIT repositorios"
  REPOS=(${REPOS[@]:0:$REPO_LIMIT})
  total_repos=$REPO_LIMIT
fi

echo "Se analizarán $total_repos repositorios.\n"

# List repositories at start
if [[ $total_repos -eq 0 ]]; then
  echo "No se encontraron repositorios en la organización: $ORG."
  if ! send_slack_notification \
      "❌ Análisis de Repositorios - Error" \
      "No se encontraron repositorios en la organización \`$ORG\`" \
      "ERROR" \
      "Organización:$ORG" ""; then
    echo "⚠ No se pudo enviar la notificación de error a Slack"
  fi
  exit 1
fi
for i in {1..$total_repos}; do
  echo "$i. ${REPOS[$i]}"
done

echo "\n==============================="
echo "Procesando repositorios..."
echo "===============================\n"

# Process each repository
COUNTER=1
for REPO in $REPOS; do
  # Check if we should skip this repo (already processed in checkpoint)
  if [[ "$RESUME_FROM_CHECKPOINT" == "true" ]] && [[ -n "$LAST_PROCESSED_REPO" ]]; then
    if [[ "$REPO" != "$LAST_PROCESSED_REPO" ]]; then
      echo "⏭️  Saltando repositorio ya procesado: $REPO"
      COUNTER=$((COUNTER + 1))
      continue
    else
      echo "✅ Alcanzado último repositorio procesado: $REPO - Procesándolo nuevamente..."
      RESUME_FROM_CHECKPOINT=false  # Stop skipping after this
    fi
  fi

  REPO_URL="https://github.com/$ORG/$REPO"
  echo "---------------------------------------"
  echo "Procesando repositorio $COUNTER/$total_repos: $REPO"
  echo "URL: $REPO_URL"
  COUNTER=$((COUNTER + 1))

  # Check if repository is archived
  if [[ "${REPO_ARCHIVED[$REPO]}" == "true" ]]; then
    echo "📦 Repositorio archivado - omitiendo análisis"
    archived_repos=$((archived_repos + 1))
    save_checkpoint "$REPO"
    echo ""
    continue
  fi

  # Try to get default branch with retry logic
  DEFAULT_BRANCH_OUTPUT=$(safe_gh_repo_view "$ORG/$REPO" --json defaultBranchRef -q .defaultBranchRef.name 2>&1)
  branch_exit_code=$?

  if (( branch_exit_code != 0 )); then
    echo "❌ Failed to get default branch for $REPO after retries"
    echo "   Error: $(echo "$DEFAULT_BRANCH_OUTPUT" | head -1)"
    failed_repos=$((failed_repos + 1))
    FAILED_REPOS[$REPO]="Failed to get default branch: $(echo "$DEFAULT_BRANCH_OUTPUT" | head -1 | tr '\n' ' ')"
    save_checkpoint "$REPO"
    echo ""
    continue
  fi

  DEFAULT_BRANCH="$DEFAULT_BRANCH_OUTPUT"
  echo "Branch por defecto: $DEFAULT_BRANCH"

  # Detect project type
  PROJECT_TYPE=""
  if safe_gh_api repos/$ORG/$REPO/contents/pom.xml?ref=$DEFAULT_BRANCH >/dev/null 2>&1; then
    exit_code=$?
    if (( exit_code == 2 )); then
      save_checkpoint "$REPO"
      echo "⚠️  Rate limit reached. Progress saved. Exiting..."
      exit 3
    fi
    echo "  - Tipo de proyecto: Maven (pom.xml encontrado)"
    maven_repos=$((maven_repos + 1))
    echo "    -> Contador Maven: $maven_repos"
    PROJECT_TYPE="maven"
  elif safe_gh_api repos/$ORG/$REPO/contents/build.gradle?ref=$DEFAULT_BRANCH >/dev/null 2>&1 || safe_gh_api repos/$ORG/$REPO/contents/build.gradle.kts?ref=$DEFAULT_BRANCH >/dev/null 2>&1; then
    exit_code=$?
    if (( exit_code == 2 )); then
      save_checkpoint "$REPO"
      echo "⚠️  Rate limit reached. Progress saved. Exiting..."
      exit 3
    fi
    echo "  - Tipo de proyecto: Gradle (build.gradle o build.gradle.kts encontrado)"
    gradle_repos=$((gradle_repos + 1))
    echo "    -> Contador Gradle: $gradle_repos"
    PROJECT_TYPE="gradle"
  elif safe_gh_api repos/$ORG/$REPO/contents/package.json?ref=$DEFAULT_BRANCH >/dev/null 2>&1; then
    exit_code=$?
    if (( exit_code == 2 )); then
      save_checkpoint "$REPO"
      echo "⚠️  Rate limit reached. Progress saved. Exiting..."
      exit 3
    fi
    echo "  - Tipo de proyecto: Node (package.json encontrado)"
    node_repos=$((node_repos + 1))
    echo "    -> Contador Node: $node_repos"
    PROJECT_TYPE="node"
  elif safe_gh_api repos/$ORG/$REPO/contents/go.mod?ref=$DEFAULT_BRANCH >/dev/null 2>&1; then
    exit_code=$?
    if (( exit_code == 2 )); then
      save_checkpoint "$REPO"
      echo "⚠️  Rate limit reached. Progress saved. Exiting..."
      exit 3
    fi
    echo "  - Tipo de proyecto: Go (go.mod encontrado)"
    go_repos=$((go_repos + 1))
    echo "    -> Contador Go: $go_repos"
    PROJECT_TYPE="go"
  elif safe_gh_api repos/$ORG/$REPO/contents/pubspec.yaml?ref=$DEFAULT_BRANCH >/dev/null 2>&1; then
    exit_code=$?
    if (( exit_code == 2 )); then
      save_checkpoint "$REPO"
      echo "⚠️  Rate limit reached. Progress saved. Exiting..."
      exit 3
    fi
    echo "  - Tipo de proyecto: Flutter (pubspec.yaml encontrado)"
    flutter_repos=$((flutter_repos + 1))
    echo "    -> Contador Flutter: $flutter_repos"
    PROJECT_TYPE="flutter"
  else
    echo "  - Tipo de proyecto: No determinado"
    other_repos=$((other_repos + 1))
    echo "    -> Contador 'Otros': $other_repos"
  fi

  # Track CSV data if available
  if [[ -n "$CSV_FILE" ]]; then
    # Normalize the repository URL for comparison
    NORMALIZED_REPO_URL=$(normalize_repo_url "$REPO_URL")

    # Check if this repo is in the CSV
    if [[ -n "${CSV_ADOPTION[$NORMALIZED_REPO_URL]:-}" ]]; then
      repos_in_csv=$((repos_in_csv + 1))
      local csv_adoption="${CSV_ADOPTION[$NORMALIZED_REPO_URL]}"
      local csv_tech="${CSV_TECHNOLOGY[$NORMALIZED_REPO_URL]}"

      echo "  📊 Repositorio encontrado en CSV:"
      echo "     Adopción: $csv_adoption"
      echo "     Tecnología (CSV): $csv_tech"

      # Track adoption globally
      if [[ -n "$csv_adoption" ]]; then
        ADOPTION_COUNTERS[$csv_adoption]=$((${ADOPTION_COUNTERS[$csv_adoption]:-0} + 1))
      fi

      # Track technology + adoption combination
      if [[ -n "$PROJECT_TYPE" && -n "$csv_adoption" ]]; then
        local key="${PROJECT_TYPE}:${csv_adoption}"
        TECH_ADOPTION_COUNTERS[$key]=$((${TECH_ADOPTION_COUNTERS[$key]:-0} + 1))
        TECH_IN_CSV_COUNT[$PROJECT_TYPE]=$((${TECH_IN_CSV_COUNT[$PROJECT_TYPE]:-0} + 1))
      fi

      # Compare technology annotations
      if [[ -n "$csv_tech" && -n "$PROJECT_TYPE" ]]; then
        local normalized_csv_tech=$(normalize_tech "$csv_tech")
        echo "     Tecnología detectada: $PROJECT_TYPE"
        echo "     Tecnología normalizada (CSV): $normalized_csv_tech"

        if [[ "$normalized_csv_tech" == "$PROJECT_TYPE" ]]; then
          echo "     ✓ Tecnología coincide"
          tech_matches=$((tech_matches + 1))
        else
          echo "     ⚠ Tecnología NO coincide"
          tech_mismatches=$((tech_mismatches + 1))
          TECH_MISMATCHES[$REPO]="CSV: $csv_tech ($normalized_csv_tech) vs Detected: $PROJECT_TYPE"
        fi
      fi
    else
      not_in_csv_repos=$((not_in_csv_repos + 1))
      echo "  ℹ️  Repositorio NO encontrado en CSV"
    fi
  fi

  # List workflow files in .github/workflows
  echo "  🔍 Buscando workflows en .github/workflows..."
  echo "     API endpoint: repos/$ORG/$REPO/contents/.github/workflows?ref=$DEFAULT_BRANCH"

  FILES_RESPONSE=$(safe_gh_api repos/$ORG/$REPO/contents/.github/workflows?ref=$DEFAULT_BRANCH 2>&1)
  exit_code=$?

  # Check for rate limit
  if (( exit_code == 2 )); then
    save_checkpoint "$REPO"
    echo "⚠️  Rate limit reached. Progress saved. Exiting..."
    exit 3
  fi

  # Check if it's a network error after retries (fatal error)
  if (( exit_code == 1 )) && echo "$FILES_RESPONSE" | grep -qi "Network error after\|Max retries exceeded"; then
    echo "     ❌ Network error after retries - marking repository as failed"
    echo "     Response preview (first 2 lines):"
    echo "$FILES_RESPONSE" | head -2 | sed 's/^/       /'
    failed_repos=$((failed_repos + 1))
    FAILED_REPOS[$REPO]="Failed to fetch workflows: $(echo "$FILES_RESPONSE" | grep -i 'error' | head -1 | tr '\n' ' ')"
    save_checkpoint "$REPO"
    echo ""
    continue
  fi

  # Debug: Show API response details
  if (( exit_code != 0 )); then
    echo "     ⚠️  API call failed with exit code: $exit_code"
    echo "     Response preview (first 3 lines):"
    echo "$FILES_RESPONSE" | head -3 | sed 's/^/       /'
  fi

  # Check if directory exists (404 means no .github/workflows directory)
  if echo "$FILES_RESPONSE" | grep -qi "Not Found"; then
    echo "     ❌ Directorio .github/workflows no encontrado (404 Not Found)"
    FILES=""
  elif echo "$FILES_RESPONSE" | grep -qi "This repository is empty"; then
    echo "     ℹ️  Repositorio vacío"
    FILES=""
  elif echo "$FILES_RESPONSE" | grep -qi "permission\|forbidden\|401\|403"; then
    echo "     ⚠️  Error de permisos al acceder a .github/workflows"
    echo "     Response preview:"
    echo "$FILES_RESPONSE" | head -5 | sed 's/^/       /'
    FILES=""
  elif (( exit_code != 0 )); then
    echo "     ⚠️  Error desconocido al obtener workflows"
    echo "     Full response:"
    echo "$FILES_RESPONSE" | sed 's/^/       /'
    FILES=""
  else
    # Extract both .yml and .yaml files
    ALL_FILES=$(echo "$FILES_RESPONSE" | jq -r '.[].name' 2>/dev/null || echo "")
    FILES=$(echo "$FILES_RESPONSE" | jq -r '.[] | select(.name | endswith(".yml") or endswith(".yaml")) | .name' 2>/dev/null || echo "")

    # Show what we found
    if [[ -n "$ALL_FILES" ]]; then
      TOTAL_FILES=$(echo "$ALL_FILES" | grep -c '^' || echo "0")
      WORKFLOW_FILES=$(echo "$FILES" | grep -c '^' 2>/dev/null || echo "0")
      echo "     ✓ Directorio encontrado: $TOTAL_FILES archivo(s) total, $WORKFLOW_FILES workflow(s) (.yml/.yaml)"

      if [[ $WORKFLOW_FILES -eq 0 ]] && [[ $TOTAL_FILES -gt 0 ]]; then
        echo "     ℹ️  Archivos en el directorio (no son workflows .yml/.yaml):"
        echo "$ALL_FILES" | head -5 | sed 's/^/       - /'
        if [[ $TOTAL_FILES -gt 5 ]]; then
          echo "       ... y $((TOTAL_FILES - 5)) más"
        fi
      fi
    else
      echo "     ℹ️  Directorio .github/workflows existe pero está vacío"
      FILES=""
    fi
  fi

  # Initialize CI detection flags
  maven_found=0
  gradle_found=0
  node_found=0
  go_found=0
  flutter_found=0

  if [[ -n "$FILES" ]]; then
    # Show workflow count and numbered list
    WF_COUNT=$(printf "%s\n" "$FILES" | sed '/^\s*$/d' | wc -l | tr -d ' ')
    echo "Workflows encontrados: $WF_COUNT"
    printf "%s\n" "$FILES" | nl -w2 -s'. ' | sed 's/^/  /'

    # Process each workflow file
    while IFS= read -r WF; do
      [[ -z "$WF" ]] && continue

      # Fetch workflow file metadata and content
      SIZE=$(safe_gh_api repos/"$ORG"/"$REPO"/contents/.github/workflows/"$WF"?ref="$DEFAULT_BRANCH" --jq '.size' 2>/dev/null || echo "N/A")
      if (( $? == 2 )); then
        save_checkpoint "$REPO"
        echo "⚠️  Rate limit reached. Progress saved. Exiting..."
        exit 3
      fi

      SHA=$(safe_gh_api repos/"$ORG"/"$REPO"/contents/.github/workflows/"$WF"?ref="$DEFAULT_BRANCH" --jq '.sha' 2>/dev/null || echo "N/A")
      if (( $? == 2 )); then
        save_checkpoint "$REPO"
        echo "⚠️  Rate limit reached. Progress saved. Exiting..."
        exit 3
      fi

      CONTENT_B64=$(safe_gh_api repos/"$ORG"/"$REPO"/contents/.github/workflows/"$WF"?ref="$DEFAULT_BRANCH" --jq '.content' 2>/dev/null || echo "")
      if (( $? == 2 )); then
        save_checkpoint "$REPO"
        echo "⚠️  Rate limit reached. Progress saved. Exiting..."
        exit 3
      fi

      # Decode content
      CONTENT=""
      if [[ -n "$CONTENT_B64" && "$CONTENT_B64" != "null" ]]; then
        if CONTENT_DECODED="$(decode_base64 "$CONTENT_B64")"; then
          CONTENT="$CONTENT_DECODED"
        else
          CONTENT=""
        fi
      fi

      # Calculate content metrics
      if [[ -n "$CONTENT" ]]; then
        LINES=$(printf "%s" "$CONTENT" | sed -n '1,$p' | wc -l | tr -d ' ')
        USES_COUNT=$(printf "%s" "$CONTENT" | grep -c -E '^\s*uses:' || true)
        RUN_COUNT=$(printf "%s" "$CONTENT" | grep -c -E '^\s*run:' || true)
      else
        LINES=0
        USES_COUNT=0
        RUN_COUNT=0
      fi

      # Detect references to reusable workflows
      REFS=$(printf "%s" "$CONTENT" | grep -oE '(maven-ci.yml|gradle-ci.yml|node-ci.yml|go-ci.yml|flutter-ci.yml)' | sort -u | tr '\n' ',' | sed 's/,$//')
      if [[ -z "$REFS" ]]; then REFS="(ninguna)"; fi

      # Print workflow analysis
      echo ""
      echo "  Analizando archivo: $WF"
      echo "    - Tamaño: $SIZE bytes"
      echo "    - SHA: $SHA"
      echo "    - Líneas: $LINES"
      echo "    - Occurencias: uses=$USES_COUNT  run=$RUN_COUNT"
      echo "    - Referencias detectadas: $REFS"

      # Check for maven-ci.yml
      if [[ $maven_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'maven-ci.yml'; then
        echo "    ✔ Se encontró referencia a maven-ci.yml en $WF"
        maven_ci_repos=$((maven_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$MAVEN_OUTPUT_FILE"
        echo "    name: $REPO" >> "$MAVEN_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$MAVEN_OUTPUT_FILE"
        maven_found=1
      fi

      # Check for gradle-ci.yml
      if [[ $gradle_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'gradle-ci.yml'; then
        echo "    ✔ Se encontró referencia a gradle-ci.yml en $WF"
        gradle_ci_repos=$((gradle_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$GRADLE_OUTPUT_FILE"
        echo "    name: $REPO" >> "$GRADLE_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$GRADLE_OUTPUT_FILE"
        gradle_found=1
      fi

      # Check for node-ci.yml
      if [[ $node_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'node-ci.yml'; then
        echo "    ✔ Se encontró referencia a node-ci.yml en $WF"
        node_ci_repos=$((node_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$NODE_OUTPUT_FILE"
        echo "    name: $REPO" >> "$NODE_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$NODE_OUTPUT_FILE"
        node_found=1
      fi

      # Check for go-ci.yml
      if [[ $go_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'go-ci.yml'; then
        echo "    ✔ Se encontró referencia a go-ci.yml en $WF"
        go_ci_repos=$((go_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$GO_OUTPUT_FILE"
        echo "    name: $REPO" >> "$GO_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$GO_OUTPUT_FILE"
        go_found=1
      fi

      # Check for flutter-ci.yml
      if [[ $flutter_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'flutter-ci.yml'; then
        echo "    ✔ Se encontró referencia a flutter-ci.yml en $WF"
        flutter_ci_repos=$((flutter_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$FLUTTER_OUTPUT_FILE"
        echo "    name: $REPO" >> "$FLUTTER_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$FLUTTER_OUTPUT_FILE"
        flutter_found=1
      fi
    done <<< "$FILES"
  else
    echo ""
    echo "  ⚠️  No se encontraron workflows .yml/.yaml utilizables"
    echo "     (Ver mensajes anteriores para más detalles)"
  fi

  # Save repositories without unified CI
  if [[ "$PROJECT_TYPE" == "maven" ]] && [[ $maven_found -eq 0 ]]; then
    echo ""
    echo "  ⚠ Repositorio Maven sin CI unificado"
    echo "  - url: $REPO_URL" >> "$MAVEN_WITHOUT_CI_FILE"
    echo "    name: $REPO" >> "$MAVEN_WITHOUT_CI_FILE"
    echo "    branch: $DEFAULT_BRANCH" >> "$MAVEN_WITHOUT_CI_FILE"
  fi

  if [[ "$PROJECT_TYPE" == "gradle" ]] && [[ $gradle_found -eq 0 ]]; then
    echo ""
    echo "  ⚠ Repositorio Gradle sin CI unificado"
    echo "  - url: $REPO_URL" >> "$GRADLE_WITHOUT_CI_FILE"
    echo "    name: $REPO" >> "$GRADLE_WITHOUT_CI_FILE"
    echo "    branch: $DEFAULT_BRANCH" >> "$GRADLE_WITHOUT_CI_FILE"
  fi

  if [[ "$PROJECT_TYPE" == "node" ]] && [[ $node_found -eq 0 ]]; then
    echo ""
    echo "  ⚠ Repositorio Node sin CI unificado"
    echo "  - url: $REPO_URL" >> "$NODE_WITHOUT_CI_FILE"
    echo "    name: $REPO" >> "$NODE_WITHOUT_CI_FILE"
    echo "    branch: $DEFAULT_BRANCH" >> "$NODE_WITHOUT_CI_FILE"
  fi

  if [[ "$PROJECT_TYPE" == "go" ]] && [[ $go_found -eq 0 ]]; then
    echo ""
    echo "  ⚠ Repositorio Go sin CI unificado"
    echo "  - url: $REPO_URL" >> "$GO_WITHOUT_CI_FILE"
    echo "    name: $REPO" >> "$GO_WITHOUT_CI_FILE"
    echo "    branch: $DEFAULT_BRANCH" >> "$GO_WITHOUT_CI_FILE"
  fi

  if [[ "$PROJECT_TYPE" == "flutter" ]] && [[ $flutter_found -eq 0 ]]; then
    echo ""
    echo "  ⚠ Repositorio Flutter sin CI unificado"
    echo "  - url: $REPO_URL" >> "$FLUTTER_WITHOUT_CI_FILE"
    echo "    name: $REPO" >> "$FLUTTER_WITHOUT_CI_FILE"
    echo "    branch: $DEFAULT_BRANCH" >> "$FLUTTER_WITHOUT_CI_FILE"
  fi

  # Save checkpoint after each repo
  save_checkpoint "$REPO"

  echo ""
done

# Clear checkpoint on successful completion
clear_checkpoint

# Generate failed repositories report
if (( failed_repos > 0 )); then
  echo "\n==============================="
  echo "Generando reporte de repositorios fallidos..."
  echo "==============================="

  : > "$FAILED_REPOS_FILE"  # Clear/create file
  echo "# Repositorios que fallaron durante el análisis" >> "$FAILED_REPOS_FILE"
  echo "# Fecha: $(date '+%Y-%m-%d %H:%M:%S')" >> "$FAILED_REPOS_FILE"
  echo "# Total: $failed_repos repositorios" >> "$FAILED_REPOS_FILE"
  echo "" >> "$FAILED_REPOS_FILE"
  echo "REPOSITORIO | ERROR" >> "$FAILED_REPOS_FILE"
  echo "------------|------" >> "$FAILED_REPOS_FILE"

  for repo in "${(@k)FAILED_REPOS}"; do
    local error_msg="${FAILED_REPOS[$repo]}"
    echo "$repo | $error_msg" >> "$FAILED_REPOS_FILE"
  done

  echo "✅ Reporte de repositorios fallidos guardado en: $FAILED_REPOS_FILE"
  echo "   Total de repositorios fallidos: $failed_repos"
fi

# Generate CSV reports if applicable
if [[ -n "$CSV_FILE" && $repos_in_csv -gt 0 ]]; then
  echo "\n==============================="
  echo "Generando reportes de CSV..."
  echo "==============================="

  generate_tech_mismatches_report
  generate_tech_adoption_distribution_report

  echo ""
fi

# Calculate execution time
END_TIME=$(date +%s)
END_TIME_FORMATTED=$(date '+%Y-%m-%d %H:%M:%S')
DURATION=$((END_TIME - START_TIME))
DURATION_MIN=$((DURATION / 60))
DURATION_SEC=$((DURATION % 60))

echo "==============================="
echo "Análisis completado. Resultados guardados en los archivos correspondientes."
echo "Hora de finalización: $END_TIME_FORMATTED"
echo "Duración: ${DURATION_MIN}m ${DURATION_SEC}s"
echo "==============================="

echo "\n==============================="
echo "Resumen de Métricas"
echo "==============================="
echo "Total de repositorios procesados: $total_repos"
if [[ $REPO_LIMIT -gt 0 ]]; then
  echo "(⚠ Análisis limitado a $REPO_LIMIT repositorios)"
fi
if (( archived_repos > 0 )); then
  echo ""
  echo "📦 Repositorios archivados (omitidos): $archived_repos"
fi
if (( failed_repos > 0 )); then
  echo ""
  echo "⚠️  Repositorios que fallaron al analizar: $failed_repos"
  echo "    (Ver $FAILED_REPOS_FILE para detalles)"
  successful_repos=$((total_repos - failed_repos - archived_repos))
  echo "✅ Repositorios analizados exitosamente: $successful_repos"
fi
echo "-------------------------------"
echo "Repositorios con CI unificado:"
echo "  - Maven: $maven_ci_repos"
echo "  - Gradle: $gradle_ci_repos"
echo "  - Node.js: $node_ci_repos"
echo "  - Go: $go_ci_repos"
echo "  - Flutter: $flutter_ci_repos"
echo "-------------------------------"
echo "Tipos de proyecto detectados:"
echo "  - Maven: $maven_repos (sin CI: $((maven_repos - maven_ci_repos)))"
echo "  - Gradle: $gradle_repos (sin CI: $((gradle_repos - gradle_ci_repos)))"
echo "  - Node.js: $node_repos (sin CI: $((node_repos - node_ci_repos)))"
echo "  - Go: $go_repos (sin CI: $((go_repos - go_ci_repos)))"
echo "  - Flutter: $flutter_repos (sin CI: $((flutter_repos - flutter_ci_repos)))"
echo "  - Otros: $other_repos"
if (( failed_repos > 0 )); then
  echo ""
  echo "ℹ️  Nota: Los contadores anteriores no incluyen los $failed_repos repositorios"
  echo "   que fallaron durante el análisis (por errores de red u otros problemas)."
fi
if (( archived_repos > 0 )); then
  echo ""
  echo "ℹ️  Nota: Los contadores anteriores no incluyen los $archived_repos repositorios"
  echo "   archivados que fueron omitidos del análisis."
fi
echo "-------------------------------"
echo "Archivos generados:"
echo "  Con CI unificado: *-repos.yml"
echo "  Sin CI unificado: *-repos-without-ci.yml"
if (( failed_repos > 0 )); then
  echo "  Repositorios fallidos: $FAILED_REPOS_FILE"
fi
if [[ -n "$CSV_FILE" && $repos_in_csv -gt 0 ]]; then
  echo "  Discrepancias de tecnología: $TECH_MISMATCHES_FILE"
  echo "  Distribución adopción: $TECH_ADOPTION_DIST_FILE"
fi
echo "==============================="

# Display CSV metrics if available
if [[ -n "$CSV_FILE" ]]; then
  echo ""
  display_csv_metrics_summary
fi

# Prepare Slack summary message
TOTAL_WITH_CI=$((maven_ci_repos + gradle_ci_repos + node_ci_repos + go_ci_repos + flutter_ci_repos))
TOTAL_WITHOUT_CI=$((maven_repos - maven_ci_repos + gradle_repos - gradle_ci_repos + node_repos - node_ci_repos + go_repos - go_ci_repos + flutter_repos - flutter_ci_repos))

SLACK_MESSAGE=$(cat <<EOF
*Repositorios procesados:* $total_repos
*Con CI unificado:* $TOTAL_WITH_CI
*Sin CI unificado:* $TOTAL_WITHOUT_CI
*Archivados (omitidos):* $archived_repos

*Desglose por tecnología:*
• Maven: $maven_repos total ($maven_ci_repos con CI, $((maven_repos - maven_ci_repos)) sin CI)
• Gradle: $gradle_repos total ($gradle_ci_repos con CI, $((gradle_repos - gradle_ci_repos)) sin CI)
• Node.js: $node_repos total ($node_ci_repos con CI, $((node_repos - node_ci_repos)) sin CI)
• Go: $go_repos total ($go_ci_repos con CI, $((go_repos - go_ci_repos)) sin CI)
• Flutter: $flutter_repos total ($flutter_ci_repos con CI, $((flutter_repos - flutter_ci_repos)) sin CI)
• Otros: $other_repos

⏱️ *Duración:* ${DURATION_MIN}m ${DURATION_SEC}s
EOF
)

# Add CSV metrics to Slack message if available
if [[ -n "$CSV_FILE" && $repos_in_csv -gt 0 ]]; then
  SLACK_MESSAGE+=$'\n\n📊 *Métricas de CSV:*'
  SLACK_MESSAGE+=$'\n'"• Repos en CSV: $repos_in_csv / $total_repos"
  SLACK_MESSAGE+=$'\n'"• Repos NO en CSV: $not_in_csv_repos"

  # Add adoption distribution
  if (( ${#ADOPTION_COUNTERS[@]} > 0 )); then
    SLACK_MESSAGE+=$'\n\n*Distribución de Adopción:*'
    for adoption in "${(@k)ADOPTION_COUNTERS}"; do
      local count=${ADOPTION_COUNTERS[$adoption]}
      local percentage=$(printf "%.1f" $(echo "scale=2; $count * 100 / $repos_in_csv" | bc))
      SLACK_MESSAGE+=$'\n'"• $adoption: $count ($percentage%)"
    done
  fi

  # Add technology accuracy
  local total_with_tech=$((tech_matches + tech_mismatches))
  if (( total_with_tech > 0 )); then
    local accuracy=$(printf "%.1f" $(echo "scale=2; $tech_matches * 100 / $total_with_tech" | bc))
    SLACK_MESSAGE+=$'\n\n'"*Precisión de Tecnología:* $accuracy% ($tech_matches/$total_with_tech correctos)"
    if (( tech_mismatches > 0 )); then
      SLACK_MESSAGE+=$'\n'"⚠️ $tech_mismatches discrepancias detectadas"
    fi
  fi

  # Add technology-specific adoption breakdown (top 3 technologies)
  SLACK_MESSAGE+=$'\n\n'"*Adopción por Tecnología:*"
  for tech in maven gradle node go flutter; do
    local tech_count=${TECH_IN_CSV_COUNT[$tech]:-0}
    if (( tech_count == 0 )); then
      continue
    fi

    local tech_display="${tech:u:0:1}${tech:1}"
    SLACK_MESSAGE+=$'\n'"• $tech_display ($tech_count repos):"

    # Find adoption states for this tech
    local found_adoption=false
    for key in "${(@k)TECH_ADOPTION_COUNTERS}"; do
      if [[ "$key" == "$tech:"* ]]; then
        local adoption="${key#*:}"
        local count=${TECH_ADOPTION_COUNTERS[$key]}
        local percentage=$(printf "%.0f" $(echo "scale=2; $count * 100 / $tech_count" | bc))
        SLACK_MESSAGE+=$' '"$adoption: ${percentage}%,"
        found_adoption=true
      fi
    done

    # Remove trailing comma
    if [[ "$found_adoption" == "true" ]]; then
      SLACK_MESSAGE="${SLACK_MESSAGE%,}"
    fi
  done
fi

# Add failure information if applicable
if (( failed_repos > 0 )); then
  SLACK_MESSAGE+=$'\n\n⚠️ *Repositorios que fallaron:* '$failed_repos
  SLACK_MESSAGE+=$'\n_Ver archivo adjunto '$FAILED_REPOS_FILE$' para detalles_'
fi

# Add limit warning if applicable
if [[ $REPO_LIMIT -gt 0 ]]; then
  SLACK_MESSAGE+=$'\n\n⚠️ _Análisis limitado a '$REPO_LIMIT$' repositorios (modo prueba)_'
fi

# Build list of files to attach
ATTACH_FILES="$MAVEN_OUTPUT_FILE $GRADLE_OUTPUT_FILE $NODE_OUTPUT_FILE $GO_OUTPUT_FILE $FLUTTER_OUTPUT_FILE $MAVEN_WITHOUT_CI_FILE $GRADLE_WITHOUT_CI_FILE $NODE_WITHOUT_CI_FILE $GO_WITHOUT_CI_FILE $FLUTTER_WITHOUT_CI_FILE"
ATTACH_FILES="$ATTACH_FILES $LOG_FILE"

# Add failed repos file if it exists
if (( failed_repos > 0 )); then
  ATTACH_FILES="$ATTACH_FILES $FAILED_REPOS_FILE"
fi

# Add CSV report files if they exist
if [[ -n "$CSV_FILE" && $repos_in_csv -gt 0 ]]; then
  if [[ -f "$TECH_ADOPTION_DIST_FILE" ]]; then
    ATTACH_FILES="$ATTACH_FILES $TECH_ADOPTION_DIST_FILE"
  fi
  if [[ -f "$TECH_MISMATCHES_FILE" ]]; then
    ATTACH_FILES="$ATTACH_FILES $TECH_MISMATCHES_FILE"
  fi
fi

# Prepare metadata fields for Slack
SLACK_FIELDS="Total:$total_repos,Con CI:$TOTAL_WITH_CI,Sin CI:$TOTAL_WITHOUT_CI,Archivados:$archived_repos"
if (( failed_repos > 0 )); then
  SLACK_FIELDS+=",Fallidos:$failed_repos"
fi
if [[ -n "$CSV_FILE" && $repos_in_csv -gt 0 ]]; then
  SLACK_FIELDS+=",En CSV:$repos_in_csv"
fi
SLACK_FIELDS+=",Duración:${DURATION_MIN}m ${DURATION_SEC}s"

# Send completion notification
if send_slack_notification \
    "✅ Análisis de Repositorios - Completado" \
    "$SLACK_MESSAGE" \
    "SUCCESS" \
    "$SLACK_FIELDS" \
    "$ATTACH_FILES"; then
  echo "\n✅ Notificación de finalización enviada a Slack"
else
  echo "\n⚠ No se pudo enviar la notificación de finalización a Slack"
fi

echo "\nArchivos generados en: $(pwd)"
echo "Log guardado en: $LOG_FILE"

# Final rate limit check
echo "\n==============================="
echo "Rate Limit Final Status"
echo "==============================="
gh api rate_limit --jq '.resources.core | "Remaining: \(.remaining)/\(.limit) | Reset: \(.reset | strftime("%Y-%m-%d %H:%M:%S"))"'
