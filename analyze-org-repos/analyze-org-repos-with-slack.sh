#!/bin/zsh
# =============================================================================
# Repository CI Analysis Script
# =============================================================================
# Purpose: Analyze all repositories in a GitHub organization to identify which
#          ones are using reusable/unified CI workflows.
#
# Requirements: gh, jq, python3, and read access to the repositories
#
# Usage: ./analyze-org-repos-with-slack.sh ORG [LIMIT]
#   ORG: GitHub organization name (required)
#   LIMIT: Maximum number of repositories to analyze (optional)
#          If not specified, analyzes all repositories
# =============================================================================

setopt +o nomatch  # Prevent zsh from failing on glob non-matches

# =============================================================================
# SECTION 1: CONFIGURATION & CONSTANTS
# =============================================================================

# --- Paths ---
SCRIPT_DIR="${0:a:h}"
SLACK_NOTIFIER_DIR="${SCRIPT_DIR}/../slack-notifier"
SLACK_NOTIFIER_SDK_PYTHON="${SLACK_NOTIFIER_DIR}/slack_notifier_sdk.py"
LOG_DIR="${SCRIPT_DIR}/logs"
CHECKPOINT_FILE="${SCRIPT_DIR}/.analyze-progress-checkpoint.txt"

# --- Analysis Configuration ---
ORG="${1:-}"
REPO_LIMIT=${2:-0}

# --- Rate Limit Configuration ---
MAX_WAIT_TIME=600  # Maximum time to wait for rate limit reset (10 minutes)
MIN_DELAY=0.1      # Minimum delay between requests (seconds)
MAX_DELAY=2.0      # Maximum delay between requests (seconds)

# --- Project Type Configuration (marker files) ---
typeset -A PROJECT_MARKERS=(
  [maven]="pom.xml"
  [gradle]="build.gradle build.gradle.kts"
  [node]="package.json"
  [go]="go.mod"
)

# --- CI Workflow Patterns ---
typeset -A CI_WORKFLOWS=(
  [maven]="maven-ci.yml"
  [gradle]="gradle-ci.yml"
  [node]="node-ci.yml"
  [go]="go-ci.yml"
)

# --- Output Files ---
typeset -A OUTPUT_FILES=(
  [maven_with]="maven-repos.yml"
  [gradle_with]="gradle-repos.yml"
  [node_with]="node-repos.yml"
  [go_with]="go-repos.yml"
  [maven_without]="maven-repos-without-ci.yml"
  [gradle_without]="gradle-repos-without-ci.yml"
  [node_without]="node-repos-without-ci.yml"
  [go_without]="go-repos-without-ci.yml"
)

# --- Counters (initialized dynamically) ---
typeset -A PROJECT_COUNTERS
typeset -A CI_COUNTERS
for type in ${(k)PROJECT_MARKERS}; do
  PROJECT_COUNTERS[$type]=0
  CI_COUNTERS[$type]=0
done
PROJECT_COUNTERS[other]=0

# --- Rate Limit State ---
last_rate_limit_check=0
rate_limit_remaining=5000
rate_limit_reset=0

# --- Execution State ---
START_TIME=$(date +%s)
START_TIME_FORMATTED=$(date '+%Y-%m-%d %H:%M:%S')
RESUME_FROM_CHECKPOINT=false
LAST_PROCESSED_REPO=""
total_repos=0

# =============================================================================
# SECTION 2: UTILITY FUNCTIONS
# =============================================================================

# Decode base64 content (handles GNU and macOS variants)
decode_base64() {
  local input="$1"
  input="$(printf '%s' "$input" | tr -d '\r')"  # Remove carriage returns

  # Try GNU coreutils base64 first
  if printf '%s' "$input" | base64 --decode >/dev/null 2>&1; then
    printf '%s' "$input" | base64 --decode 2>/dev/null || true
    return 0
  # Try macOS base64
  elif printf '%s' "$input" | base64 -D >/dev/null 2>&1; then
    printf '%s' "$input" | base64 -D 2>/dev/null || true
    return 0
  else
    return 1
  fi
}

# Create YAML entry for repository
create_yaml_entry() {
  local url="$1"
  local name="$2"
  local branch="$3"
  echo "  - url: $url"
  echo "    name: $name"
  echo "    branch: $branch"
}

# Initialize logging
setup_logging() {
  mkdir -p "$LOG_DIR"
  LOG_FILE="${LOG_DIR}/analyze-org-repos-$(date '+%Y%m%d-%H%M%S').log"
  : > "$LOG_FILE"
  exec > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)
}

# Validate command line arguments
validate_arguments() {
  # Check if organization is provided
  if [[ -z "$ORG" ]]; then
    echo "Error: Nombre de organización requerido"
    echo ""
    echo "Uso: $0 ORG [LIMIT]"
    echo "  ORG: Nombre de la organización de GitHub (requerido)"
    echo "  LIMIT: Número máximo de repositorios a analizar (opcional)"
    echo ""
    echo "Ejemplos:"
    echo "  $0 my-company          # Analizar todos los repositorios"
    echo "  $0 my-company 10       # Analizar solo los primeros 10 repositorios"
    exit 1
  fi

  # Check if limit is valid number
  if [[ "$REPO_LIMIT" != "0" ]] && ! [[ "$REPO_LIMIT" =~ ^[0-9]+$ ]]; then
    echo "Error: El límite debe ser un número entero positivo"
    echo "Uso: $0 ORG [LIMIT]"
    exit 1
  fi
}

# Initialize or clear output files
initialize_output_files() {
  if [[ "$RESUME_FROM_CHECKPOINT" != "true" ]]; then
    echo "🗑️  Limpiando archivos de salida anteriores..."
    for file in ${(v)OUTPUT_FILES}; do
      : > "$file"
    done
    echo "✅ Archivos de salida inicializados"
  fi
}

# =============================================================================
# SECTION 3: CHECKPOINT MANAGEMENT
# =============================================================================

save_checkpoint() {
  local last_repo="$1"

  # Build checkpoint data dynamically
  cat > "$CHECKPOINT_FILE" <<EOF
# Checkpoint file - DO NOT EDIT MANUALLY
ORG="$ORG"
LAST_PROCESSED_REPO="$last_repo"
TOTAL_REPOS=$total_repos
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
EOF

  # Add all project counters
  for type in ${(k)PROJECT_COUNTERS}; do
    local var_name="${type:u}_REPOS"
    echo "${var_name}=${PROJECT_COUNTERS[$type]}" >> "$CHECKPOINT_FILE"
  done

  # Add all CI counters
  for type in ${(k)CI_COUNTERS}; do
    local var_name="${type:u}_CI_REPOS"
    echo "${var_name}=${CI_COUNTERS[$type]}" >> "$CHECKPOINT_FILE"
  done

  echo "💾 Progress saved to checkpoint file: $CHECKPOINT_FILE"
}

load_checkpoint() {
  if [[ -f "$CHECKPOINT_FILE" ]]; then
    echo "📂 Found checkpoint file. Loading previous progress..."
    source "$CHECKPOINT_FILE"

    # Verify organization matches
    local checkpoint_org="${ORG}"
    ORG="${1:-}"  # Get org from command line argument
    if [[ -n "$checkpoint_org" ]] && [[ "$checkpoint_org" != "$ORG" ]]; then
      echo "⚠️  Warning: Checkpoint is for organization '$checkpoint_org' but you specified '$ORG'"
      echo "   Ignoring checkpoint and starting fresh."
      return 1
    fi
    ORG="$checkpoint_org"  # Use org from checkpoint

    # Load counters back into associative arrays
    for type in ${(k)PROJECT_COUNTERS}; do
      local var_name="${type:u}_REPOS"
      PROJECT_COUNTERS[$type]=${(P)var_name:-0}
    done
    for type in ${(k)CI_COUNTERS}; do
      local var_name="${type:u}_CI_REPOS"
      CI_COUNTERS[$type]=${(P)var_name:-0}
    done

    echo "   Organization: $ORG"
    echo "   Last processed: $LAST_PROCESSED_REPO"
    echo "   Timestamp: $TIMESTAMP"
    echo "   Total repos processed: $TOTAL_REPOS"
    return 0
  fi
  return 1
}

clear_checkpoint() {
  if [[ -f "$CHECKPOINT_FILE" ]]; then
    rm -f "$CHECKPOINT_FILE"
    echo "🗑️  Checkpoint file cleared"
  fi
}

# =============================================================================
# SECTION 4: GITHUB API & RATE LIMITING
# =============================================================================

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
      return 2  # Signal to exit
    elif (( wait_time > 0 )); then
      echo "⏳ Rate limit low ($rate_limit_remaining remaining). Waiting ${wait_time}s until reset..." >&2
      sleep "$wait_time"
      echo "✅ Rate limit reset. Continuing..." >&2
    fi
  fi
  return 0
}

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

throttle_request() {
  local delay=$(get_adaptive_delay)
  sleep "$delay"
}

safe_gh_api() {
  local max_retries=3
  local retry=0
  local backoff=1

  while (( retry < max_retries )); do
    # Check rate limit before making request
    if ! check_rate_limit; then
      local exit_code=$?
      if (( exit_code == 2 )); then
        return 2  # Signal to exit script
      fi
    fi

    # Throttle request
    throttle_request

    # Make the API call
    local output
    output=$(gh api "$@" 2>&1)
    local result=$?

    # Success
    if (( result == 0 )); then
      echo "$output"
      return 0
    fi

    # Check if it's a rate limit error
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
        return 2
      fi

      echo "⏳ Rate limit hit (attempt $retry/$max_retries). Waiting ${wait_time}s..." >&2
      sleep "$wait_time"

      # Refresh rate limit info
      check_rate_limit
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

# Wrapper for API calls that handles rate limit exits
api_call_or_exit() {
  local result
  result=$(safe_gh_api "$@")
  local exit_code=$?

  if (( exit_code == 2 )); then
    save_checkpoint "$CURRENT_REPO"
    echo "⚠️  Rate limit reached. Progress saved. Exiting..." >&2
    exit 3
  fi

  echo "$result"
  return $exit_code
}

fetch_all_repositories() {
  local repos_query
  read -r -d '' repos_query <<'EOF'
query($owner: String!, $endCursor: String) {
  organization(login: $owner) {
    repositories(first: 100, after: $endCursor) {
      nodes {
        name
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
EOF

  local -a all_repos=()
  local end_cursor=""
  local page_count=0

  echo "Fetching repository list from organization..." >&2

  while true; do
    page_count=$((page_count + 1))
    echo "  Fetching page $page_count..." >&2

    # Call API with or without cursor
    local page_data
    if [[ -z "$end_cursor" ]]; then
      page_data=$(api_call_or_exit graphql -F owner="$ORG" -f query="$repos_query")
    else
      page_data=$(api_call_or_exit graphql -F owner="$ORG" -F endCursor="$end_cursor" -f query="$repos_query")
    fi

    # Parse response
    local repo_data=$(echo "$page_data" | jq -r '.data.organization.repositories' 2>/dev/null)
    if [[ -z "$repo_data" ]] || [[ "$repo_data" == "null" ]]; then
      echo "❌ Failed to parse repository data" >&2
      exit 1
    fi

    local new_repos=($(echo "$repo_data" | jq -r '.nodes[].name' 2>/dev/null))
    if [[ ${#new_repos[@]} -eq 0 ]]; then
      echo "⚠️  No repositories found in this page" >&2
      break
    fi

    all_repos+=(${new_repos[@]})
    echo "    Found ${#new_repos[@]} repositories (total: ${#all_repos[@]})" >&2

    local has_next=$(echo "$repo_data" | jq -r '.pageInfo.hasNextPage' 2>/dev/null)
    end_cursor=$(echo "$repo_data" | jq -r '.pageInfo.endCursor // ""' 2>/dev/null)

    if [[ "$has_next" != "true" ]]; then
      echo "  ✅ All pages fetched" >&2
      break
    fi
  done

  echo "${all_repos[@]}"
}

# =============================================================================
# SECTION 5: SLACK INTEGRATION
# =============================================================================

send_slack_notification() {
  local title="$1"; shift
  local message="$1"; shift
  local notif_status="$1"; shift
  local fields="${1:-}"; shift || true
  local files_list="${1:-}"; shift || true

  # Validation: check for token and channel (skip if dry-run)
  local dry_run_flag="${SLACK_DRY_RUN:-}"
  if [[ -z "$SLACK_BOT_TOKEN" && -z "$dry_run_flag" ]]; then
    echo "[INFO] SLACK_BOT_TOKEN no definido: omitiendo envío Slack (title='$title')" >&2
    return 3
  fi
  if [[ -z "$SLACK_CHANNEL" && -z "$dry_run_flag" ]]; then
    echo "[INFO] SLACK_CHANNEL no definido: omitiendo envío Slack (title='$title')" >&2
    return 4
  fi

  # Normalize status
  local status_lc="${notif_status:l}"
  case "$status_lc" in
    success|error|failure|warning|info|debug) ;;
    *) status_lc="info" ;;
  esac

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
        local k="${pair%%:*}"
        local v="${pair#*:}"
        metadata_md+=$'• '*"${k}"$': '*"${v}"$'\n'

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

  # Add metadata if present
  if [[ -n "$metadata_md" ]]; then
    metadata_md="*Metadatos:*\n${metadata_md%\n}"
    template_vars_args+=(--var "METADATA=${metadata_md}")
  fi

  # Add context variables
  template_vars_args+=(--var "ORG=${ORG}")
  template_vars_args+=(--var "REPO_LIMIT=${REPO_LIMIT}")

  # Build command
  local -a cmd=(python3 "$SLACK_NOTIFIER_SDK_PYTHON" --title "$title" --status "$status_lc" --template "$template_name" ${template_vars_args[@]})

  if [[ -n "$message" ]]; then
    cmd+=(--message "$message")
  fi

  if [[ -n "$dry_run_flag" ]]; then
    cmd+=(--dry-run)
    echo "[DRY-RUN] (Slack)" >&2
  fi

  # Add files
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
  echo "----- INICIO SLACK NOTIFIER OUTPUT -----" >&2

  local tmp_out="$(mktemp -t slack-notifier.XXXXXX 2>/dev/null || mktemp)"
  local rc=0
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

# =============================================================================
# SECTION 6: BUSINESS LOGIC - PROJECT ANALYSIS
# =============================================================================

# Detect project type by checking for marker files
# Returns: "maven"|"gradle"|"node"|"go"|"other"
detect_project_type() {
  local repo="$1"
  local org="$2"
  local branch="$3"

  for type in ${(k)PROJECT_MARKERS}; do
    local markers=(${(z)PROJECT_MARKERS[$type]})
    for marker in $markers; do
      if api_call_or_exit repos/$org/$repo/contents/$marker?ref=$branch >/dev/null 2>&1; then
        echo "$type"
        return 0
      fi
    done
  done

  echo "other"
}

# Analyze workflows in a repository
# Returns: space-separated list of CI types found (e.g., "maven gradle")
analyze_workflows() {
  local repo="$1"
  local org="$2"
  local branch="$3"

  # Get list of workflow files
  local workflow_files=$(api_call_or_exit repos/$org/$repo/contents/.github/workflows?ref=$branch --jq '.[] | select(.name | endswith(".yml")) | .name' 2>/dev/null || echo "")

  if [[ -z "$workflow_files" ]]; then
    echo "No se encontraron workflows en .github/workflows para $repo."
    return 0
  fi

  local wf_count=$(printf "%s\n" "$workflow_files" | sed '/^\s*$/d' | wc -l | tr -d ' ')
  echo "Workflows encontrados: $wf_count"
  printf "%s\n" "$workflow_files" | nl -w2 -s'. ' | sed 's/^/  /'

  # Track which CI types were found
  typeset -A found_ci_types
  for type in ${(k)CI_WORKFLOWS}; do
    found_ci_types[$type]=0
  done

  # Process each workflow file
  while IFS= read -r wf_file; do
    [[ -z "$wf_file" ]] && continue

    echo ""
    echo "  Analizando archivo: $wf_file"

    # Fetch workflow content to temp file to avoid shell interpretation issues
    local temp_file=$(mktemp)
    if ! api_call_or_exit repos/"$org"/"$repo"/contents/.github/workflows/"$wf_file"?ref="$branch" > "$temp_file" 2>/dev/null; then
      echo "    - Error al obtener contenido"
      rm -f "$temp_file"
      continue
    fi

    # Extract fields from response with error handling
    local size sha content_b64
    size=$(jq -r '.size // "N/A"' "$temp_file" 2>/dev/null || echo "N/A")
    sha=$(jq -r '.sha // "N/A"' "$temp_file" 2>/dev/null || echo "N/A")
    content_b64=$(jq -r '.content // ""' "$temp_file" 2>/dev/null || echo "")

    # Decode content
    local content=""
    if [[ -n "$content_b64" && "$content_b64" != "null" ]]; then
      if content=$(decode_base64 "$content_b64"); then
        :  # Success
      else
        content=""
      fi
    fi

    # Calculate metrics
    local lines=0 uses_count=0 run_count=0
    if [[ -n "$content" ]]; then
      lines=$(printf "%s" "$content" | wc -l | tr -d ' ')
      uses_count=$(printf "%s" "$content" | grep -c -E '^\s*uses:' || true)
      run_count=$(printf "%s" "$content" | grep -c -E '^\s*run:' || true)
    fi

    # Detect CI workflow references
    local refs=$(printf "%s" "$content" | grep -oE '(maven-ci.yml|gradle-ci.yml|node-ci.yml|go-ci.yml)' | sort -u | tr '\n' ',' | sed 's/,$//')
    [[ -z "$refs" ]] && refs="(ninguna)"

    # Display info
    echo "    - Tamaño: $size bytes"
    echo "    - SHA: $sha"
    echo "    - Líneas: $lines"
    echo "    - Occurencias: uses=$uses_count  run=$run_count"
    echo "    - Referencias detectadas: $refs"

    # Check for each CI type
    for type in ${(k)CI_WORKFLOWS}; do
      local pattern="${CI_WORKFLOWS[$type]}"
      if [[ ${found_ci_types[$type]} -eq 0 ]] && printf "%s" "$content" | grep -q "$pattern"; then
        echo "    ✔ Se encontró referencia a $pattern en $wf_file"
        found_ci_types[$type]=1
      fi
    done

    # Cleanup temp file
    rm -f "$temp_file"

  done <<< "$workflow_files"

  # Return list of found CI types
  local -a result=()
  for type in ${(k)found_ci_types}; do
    if [[ ${found_ci_types[$type]} -eq 1 ]]; then
      result+=($type)
    fi
  done
  echo "${result[@]}"
}

# Write repository to output file
record_repository() {
  local repo="$1"
  local url="$2"
  local branch="$3"
  local project_type="$4"
  local found_ci_types="$5"  # Space-separated list

  # Skip if not a known project type
  [[ "$project_type" == "other" ]] && return

  # Check if this project type has CI
  local has_ci=false
  for ci_type in ${(z)found_ci_types}; do
    if [[ "$ci_type" == "$project_type" ]]; then
      has_ci=true
      break
    fi
  done

  # Write to appropriate file
  local entry=$(create_yaml_entry "$url" "$repo" "$branch")

  if $has_ci; then
    local file="${OUTPUT_FILES[${project_type}_with]}"
    echo "$entry" >> "$file"
  else
    echo ""
    echo "  ⚠ Repositorio ${project_type:u} sin CI unificado"
    local file="${OUTPUT_FILES[${project_type}_without]}"
    echo "$entry" >> "$file"
  fi
}

# Update counters based on analysis results
update_counters() {
  local project_type="$1"
  local found_ci_types="$2"  # Space-separated list

  # Increment project counter
  PROJECT_COUNTERS[$project_type]=$((PROJECT_COUNTERS[$project_type] + 1))
  echo "    -> Contador ${project_type:u}: ${PROJECT_COUNTERS[$project_type]}"

  # Increment CI counter if found
  for ci_type in ${(z)found_ci_types}; do
    if [[ "$ci_type" == "$project_type" ]]; then
      CI_COUNTERS[$project_type]=$((CI_COUNTERS[$project_type] + 1))
      break
    fi
  done
}

# Process a single repository (orchestration function)
process_repository() {
  local repo="$1"
  local counter="$2"
  local total="$3"

  local repo_url="https://github.com/$ORG/$repo"
  echo "---------------------------------------"
  echo "Procesando repositorio $counter/$total: $repo"
  echo "URL: $repo_url"

  # Get default branch
  local default_branch=$(gh repo view "$ORG/$repo" --json defaultBranchRef -q .defaultBranchRef.name)
  echo "Branch por defecto: $default_branch"

  # Detect project type
  local project_type=$(detect_project_type "$repo" "$ORG" "$default_branch")
  echo "  - Tipo de proyecto: ${project_type:u}"

  # Analyze workflows
  local found_ci_types=$(analyze_workflows "$repo" "$ORG" "$default_branch")

  # Update counters
  update_counters "$project_type" "$found_ci_types"

  # Record to output files
  record_repository "$repo" "$repo_url" "$default_branch" "$project_type" "$found_ci_types"

  # Save checkpoint
  save_checkpoint "$repo"

  echo ""
}

# =============================================================================
# SECTION 7: REPORTING
# =============================================================================

print_summary() {
  local duration_min=$1
  local duration_sec=$2

  echo "==============================="
  echo "Resumen de Métricas"
  echo "==============================="
  echo "Total de repositorios analizados: $total_repos"
  if [[ $REPO_LIMIT -gt 0 ]]; then
    echo "(⚠ Análisis limitado a $REPO_LIMIT repositorios)"
  fi

  echo "-------------------------------"
  echo "Repositorios con CI unificado:"
  for type in maven gradle node go; do
    [[ -n "${CI_COUNTERS[$type]}" ]] && echo "  - ${type:u}: ${CI_COUNTERS[$type]}"
  done

  echo "-------------------------------"
  echo "Tipos de proyecto detectados:"
  for type in maven gradle node go; do
    if [[ -n "${PROJECT_COUNTERS[$type]}" ]]; then
      local without_ci=$((PROJECT_COUNTERS[$type] - CI_COUNTERS[$type]))
      echo "  - ${type:u}: ${PROJECT_COUNTERS[$type]} (sin CI: $without_ci)"
    fi
  done
  echo "  - Otros: ${PROJECT_COUNTERS[other]}"

  echo "-------------------------------"
  echo "Archivos generados:"
  echo "  Con CI unificado: *-repos.yml"
  echo "  Sin CI unificado: *-repos-without-ci.yml"
  echo "==============================="
}

generate_slack_summary() {
  local duration_min=$1
  local duration_sec=$2

  # Calculate totals
  local total_with_ci=0
  local total_without_ci=0
  for type in maven gradle node go; do
    total_with_ci=$((total_with_ci + CI_COUNTERS[$type]))
    total_without_ci=$((total_without_ci + PROJECT_COUNTERS[$type] - CI_COUNTERS[$type]))
  done

  # Build message
  local message=$(cat <<EOF
*Repositorios analizados:* $total_repos
*Con CI unificado:* $total_with_ci
*Sin CI unificado:* $total_without_ci

*Desglose por tecnología:*
• Maven: ${PROJECT_COUNTERS[maven]} total (${CI_COUNTERS[maven]} con CI, $((PROJECT_COUNTERS[maven] - CI_COUNTERS[maven])) sin CI)
• Gradle: ${PROJECT_COUNTERS[gradle]} total (${CI_COUNTERS[gradle]} con CI, $((PROJECT_COUNTERS[gradle] - CI_COUNTERS[gradle])) sin CI)
• Node.js: ${PROJECT_COUNTERS[node]} total (${CI_COUNTERS[node]} con CI, $((PROJECT_COUNTERS[node] - CI_COUNTERS[node])) sin CI)
• Go: ${PROJECT_COUNTERS[go]} total (${CI_COUNTERS[go]} con CI, $((PROJECT_COUNTERS[go] - CI_COUNTERS[go])) sin CI)
• Otros: ${PROJECT_COUNTERS[other]}

⏱️ *Duración:* ${duration_min}m ${duration_sec}s
EOF
)

  if [[ $REPO_LIMIT -gt 0 ]]; then
    message+=$'\n⚠️ _Análisis limitado a '$REPO_LIMIT$' repositorios _'
  fi

  echo "$message"
}

# =============================================================================
# SECTION 8: MAIN EXECUTION
# =============================================================================

# Initialize script environment
initialize_script() {
  setup_logging
  validate_arguments

  echo "\n==============================="
  echo "Iniciando análisis de repositorios en la organización: $ORG"
  echo "Hora de inicio: $START_TIME_FORMATTED"
  if [[ $REPO_LIMIT -gt 0 ]]; then
    echo "Límite de repositorios: $REPO_LIMIT"
  else
    echo "Límite de repositorios: Sin límite (análisis completo)"
  fi
  echo "==============================="

  # Check for checkpoint (pass ORG from command line for validation)
  if load_checkpoint "$ORG"; then
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

  initialize_output_files

  # Send start notification
  local limit_msg=""
  [[ $REPO_LIMIT -gt 0 ]] && limit_msg=" (limitado a $REPO_LIMIT repositorios)"

  if send_slack_notification \
      "📊 Análisis de Repositorios - Iniciado" \
      "Iniciando análisis de repositorios en la organización \`$ORG\`$limit_msg" \
      "INFO" \
      "Organización:$ORG,Inicio:$START_TIME_FORMATTED,Límite:${REPO_LIMIT:-Sin límite}" ""; then
    echo "\n✓ Notificación de inicio enviada a Slack"
  else
    echo "\n⚠ No se pudo enviar la notificación de inicio a Slack"
  fi
}

# Main execution flow
main() {
  initialize_script

  echo "\n==============================="
  echo "Obteniendo lista de repositorios..."
  echo "===============================\n"

  # Fetch all repositories
  local -a repos=($(fetch_all_repositories))
  total_repos=${#repos[@]}
  echo "Se encontraron $total_repos repositorios en total.\n"

  # Apply limit if configured
  if [[ $REPO_LIMIT -gt 0 ]] && [[ $total_repos -gt $REPO_LIMIT ]]; then
    echo "⚠ Aplicando límite: analizando solo los primeros $REPO_LIMIT repositorios"
    repos=(${repos[@]:0:$REPO_LIMIT})
    total_repos=$REPO_LIMIT
  fi

  echo "Se analizarán $total_repos repositorios.\n"

  # Check if we have repositories
  if [[ $total_repos -eq 0 ]]; then
    echo "No se encontraron repositorios en la organización: $ORG."
    send_slack_notification \
        "❌ Análisis de Repositorios - Error" \
        "No se encontraron repositorios en la organización \`$ORG\`" \
        "ERROR" \
        "Organización:$ORG" ""
    exit 1
  fi

  # List repositories
  for i in {1..$total_repos}; do
    echo "$i. ${repos[$i]}"
  done

  echo "\n==============================="
  echo "Procesando repositorios..."
  echo "===============================\n"

  # Process each repository
  local counter=1
  for repo in $repos; do
    # Check if we should skip (checkpoint resume)
    if [[ "$RESUME_FROM_CHECKPOINT" == "true" ]] && [[ -n "$LAST_PROCESSED_REPO" ]]; then
      if [[ "$repo" != "$LAST_PROCESSED_REPO" ]]; then
        echo "⏭️  Saltando repositorio ya procesado: $repo"
        counter=$((counter + 1))
        continue
      else
        echo "✅ Alcanzado último repositorio procesado: $repo - Procesándolo nuevamente..."
        RESUME_FROM_CHECKPOINT=false
      fi
    fi

    # Set current repo for error handling
    CURRENT_REPO="$repo"

    # Process repository
    process_repository "$repo" "$counter" "$total_repos"

    counter=$((counter + 1))
  done

  # Clear checkpoint on successful completion
  clear_checkpoint

  # Calculate execution time
  local end_time=$(date +%s)
  local end_time_formatted=$(date '+%Y-%m-%d %H:%M:%S')
  local duration=$((end_time - START_TIME))
  local duration_min=$((duration / 60))
  local duration_sec=$((duration % 60))

  echo "==============================="
  echo "Análisis completado. Resultados guardados en los archivos correspondientes."
  echo "Hora de finalización: $end_time_formatted"
  echo "Duración: ${duration_min}m ${duration_sec}s"
  echo "==============================="

  # Print summary
  echo ""
  print_summary "$duration_min" "$duration_sec"

  # Send completion notification
  local slack_message=$(generate_slack_summary "$duration_min" "$duration_sec")
  local total_with_ci=$((CI_COUNTERS[maven] + CI_COUNTERS[gradle] + CI_COUNTERS[node] + CI_COUNTERS[go]))
  local total_without_ci=$((PROJECT_COUNTERS[maven] - CI_COUNTERS[maven] + PROJECT_COUNTERS[gradle] - CI_COUNTERS[gradle] + PROJECT_COUNTERS[node] - CI_COUNTERS[node] + PROJECT_COUNTERS[go] - CI_COUNTERS[go]))

  local attach_files="${OUTPUT_FILES[maven_with]} ${OUTPUT_FILES[gradle_with]} ${OUTPUT_FILES[node_with]} ${OUTPUT_FILES[go_with]} ${OUTPUT_FILES[maven_without]} ${OUTPUT_FILES[gradle_without]} ${OUTPUT_FILES[node_without]} ${OUTPUT_FILES[go_without]} $LOG_FILE"

  if send_slack_notification \
      "✅ Análisis de Repositorios - Completado" \
      "$slack_message" \
      "SUCCESS" \
      "Total:$total_repos,Con CI:$total_with_ci,Sin CI:$total_without_ci,Duración:${duration_min}m ${duration_sec}s" \
      "$attach_files"; then
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
}

# Run main function
main "$@"
