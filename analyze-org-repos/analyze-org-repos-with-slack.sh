#!/bin/zsh
# Script para generar lista de repositorios con workflows de CI reutilizables en ueno-tecnologia-org
# Con integración de notificaciones Slack
# Requiere: gh, jq, python3, y acceso de lectura a los repositorios
#
# Uso: ./analyze-org-repos-with-slack.sh <ORGANIZATION> [LIMIT]
#   ORGANIZATION: Nombre de la organización de GitHub (requerido)
#   LIMIT: Número máximo de repositorios a analizar (opcional)
#          Si no se especifica, se analizan todos los repositorios

setopt +o nomatch # Evita que zsh falle si no hay coincidencias de globbing

# Obtener la ruta del directorio del script
SCRIPT_DIR="${0:a:h}"
SLACK_NOTIFIER_DIR="${SCRIPT_DIR}/../slack-notifier"
SLACK_NOTIFIER_SDK_PYTHON="${SLACK_NOTIFIER_DIR}/slack_notifier_sdk.py"
# (El script viejo slack_notifier.py ya no se usa)

# --- Añadido: configuración de log y redirección de salida ---
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/analyze-org-repos-$(date '+%Y%m%d-%H%M%S').log"
# Crear/recortar el archivo log
: > "$LOG_FILE"
# Redirigir stdout y stderr a tee para escribir en log y seguir mostrando en consola
exec > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)
# --- Fin añadido ---

ORG="${1:-}"

# Límite de repositorios (0 = sin límite)
REPO_LIMIT=${2:-0}

# Validar que se proporcionó la organización
if [[ -z "$ORG" ]]; then
  echo "Error: Debe especificar el nombre de la organización"
  echo "Uso: $0 <ORGANIZATION> [LIMIT]"
  exit 1
fi

# Rate limit configuration
MAX_WAIT_TIME=600  # Maximum time to wait for rate limit reset (10 minutes)
RATE_LIMIT_THRESHOLD=100  # Start adaptive throttling when remaining < this
MIN_DELAY=0.1  # Minimum delay between requests (seconds)
MAX_DELAY=2.0  # Maximum delay between requests (seconds)

# Checkpoint file for progress tracking
CHECKPOINT_FILE="${SCRIPT_DIR}/.analyze-progress-checkpoint.txt"

# Validar que el límite sea un número
if [[ "$REPO_LIMIT" != "0" ]] && ! [[ "$REPO_LIMIT" =~ ^[0-9]+$ ]]; then
  echo "Error: El límite debe ser un número entero positivo"
  echo "Uso: $0 <ORGANIZATION> [LIMIT]"
  exit 1
fi

# Archivos de salida para cada tipo de CI
MAVEN_OUTPUT_FILE="maven-repos.yml"
GRADLE_OUTPUT_FILE="gradle-repos.yml"
NODE_OUTPUT_FILE="node-repos.yml"
GO_OUTPUT_FILE="go-repos.yml"

# Archivos de salida para repos sin CI unificado
MAVEN_WITHOUT_CI_FILE="maven-repos-without-ci.yml"
GRADLE_WITHOUT_CI_FILE="gradle-repos-without-ci.yml"
NODE_WITHOUT_CI_FILE="node-repos-without-ci.yml"
GO_WITHOUT_CI_FILE="go-repos-without-ci.yml"

# Contadores para métricas de tipo de proyecto
maven_repos=0
gradle_repos=0
node_repos=0
go_repos=0
other_repos=0

# Contadores para métricas de CI
maven_ci_repos=0
gradle_ci_repos=0
node_ci_repos=0
go_ci_repos=0

# Rate limit tracking
last_rate_limit_check=0
rate_limit_remaining=5000
rate_limit_reset=0

# Function to check and handle rate limits
check_rate_limit() {
  local current_time=$(date +%s)
  # Check rate limit every 10 API calls or every 30 seconds
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

# Function to calculate adaptive delay based on rate limit
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

# Function to throttle API requests
throttle_request() {
  local delay=$(get_adaptive_delay)
  sleep "$delay"
}

# Function to make a safe gh api call with rate limit handling
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

    # Other error - return it
    echo "$output"
    return $result
  done

  # Max retries exceeded
  echo "❌ Max retries exceeded for gh api call" >&2
  return 1
}

# Function to save checkpoint
save_checkpoint() {
  local last_repo="$1"
  cat > "$CHECKPOINT_FILE" <<EOF
# Checkpoint file - DO NOT EDIT MANUALLY
LAST_PROCESSED_REPO="$last_repo"
MAVEN_REPOS=$maven_repos
GRADLE_REPOS=$gradle_repos
NODE_REPOS=$node_repos
GO_REPOS=$go_repos
OTHER_REPOS=$other_repos
MAVEN_CI_REPOS=$maven_ci_repos
GRADLE_CI_REPOS=$gradle_ci_repos
NODE_CI_REPOS=$node_ci_repos
GO_CI_REPOS=$go_ci_repos
TOTAL_REPOS=$total_repos
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
EOF
  echo "💾 Progress saved to checkpoint file: $CHECKPOINT_FILE"
}

# Function to load checkpoint
load_checkpoint() {
  if [[ -f "$CHECKPOINT_FILE" ]]; then
    echo "📂 Found checkpoint file. Loading previous progress..."
    source "$CHECKPOINT_FILE"
    echo "   Last processed: $LAST_PROCESSED_REPO"
    echo "   Timestamp: $TIMESTAMP"
    echo "   Total repos processed: $TOTAL_REPOS"
    return 0
  fi
  return 1
}

# Function to clear checkpoint
clear_checkpoint() {
  if [[ -f "$CHECKPOINT_FILE" ]]; then
    rm -f "$CHECKPOINT_FILE"
    echo "🗑️  Checkpoint file cleared"
  fi
}

# Función para enviar notificación Slack usando slack_notifier_sdk.py
# Args:
# 1: título
# 2: mensaje (markdown)
# 3: status (INFO|SUCCESS|ERROR|WARNING|DEBUG)
# 4: fields en formato key:value,key2:val2 (opcional)
# 5: lista de archivos separados por espacios a adjuntar (opcional)
send_slack_notification() {
    local title="$1"; shift
    local message="$1"; shift
    local notif_status="$1"; shift
    local fields="${1:-}"; shift || true
    local files_list="${1:-}"; shift || true

    # Validaciones previas: token y canal (si dry-run forzado se omite)
    local dry_run_flag="${SLACK_DRY_RUN:-}"  # cualquier valor => dry-run
    if [[ -z "$SLACK_BOT_TOKEN" && -z "$dry_run_flag" ]]; then
      echo "[INFO] SLACK_BOT_TOKEN no definido: omitiendo envío Slack (title='$title')" >&2
      return 3
    fi
    if [[ -z "$SLACK_CHANNEL" && -z "$dry_run_flag" ]]; then
      echo "[INFO] SLACK_CHANNEL no definido: omitiendo envío Slack (title='$title')" >&2
      return 4
    fi

    # Normalizar status a minúsculas aceptadas por slack_notifier_sdk.py
    local status_lc="${notif_status:l}"
    case "$status_lc" in
      success|error|failure|warning|info|debug) ;; # válido
      *) status_lc="info" ;;
    esac

    if [[ ! -f "$SLACK_NOTIFIER_SDK_PYTHON" ]]; then
      echo "[WARNING] Slack notifier SDK no encontrado en: $SLACK_NOTIFIER_SDK_PYTHON" >&2
      return 2
    fi

    # Selección automática de template según status
    local template_name="simple"
    case "$status_lc" in
      success) template_name="workflow_success" ;;
      failure|error) template_name="workflow_failure" ;;
      *) template_name="simple" ;;
    esac

    # Convertir fields en variables para el template (--var KEY=VAL) y generar METADATA separado
    local -a template_vars_args=()
    local metadata_md=""
    if [[ -n "$fields" ]]; then
      local IFS=','
      for pair in $fields; do
        [[ -z "$pair" ]] && continue
        if [[ "$pair" == *:* ]]; then
          local k="${pair%%:*}"; local v="${pair#*:}"
          # Construir bullets de metadatos (cada par en su línea)
          local bullet_key="$k"
          metadata_md+=$'• '*"${bullet_key}"$': '*"${v}"$'\n'
          # Sanitizar clave para variable
          local k_clean="${k//[^A-Za-z0-9]/_}"
            k_clean="${k_clean:u}"
            case "$k_clean" in
              TITLE|MESSAGE|STATUS|ICON|METADATA) k_clean="META_${k_clean}" ;;
            esac
            template_vars_args+=(--var "${k_clean}=${v}")
        fi
      done
    fi

    # Recortar salto final y envolver en encabezado si hay contenido
    if [[ -n "$metadata_md" ]]; then
      metadata_md="*Metadatos:*\n${metadata_md%\n}"
      # Pasar como variable METADATA para usar bloque dedicado del template
      template_vars_args+=(--var "METADATA=${metadata_md}")
    fi

    # Variables adicionales útiles
    template_vars_args+=(--var "ORG=${ORG}")
    template_vars_args+=(--var "REPO_LIMIT=${REPO_LIMIT}")

    # Construir comando base
    local -a cmd=(python3 "$SLACK_NOTIFIER_SDK_PYTHON" --title "$title" --status "$status_lc" --template "$template_name" ${template_vars_args[@]})

    # Añadir mensaje sólo si no está vacío
    if [[ -n "$message" ]]; then
      cmd+=(--message "$message")
    fi

    # Añadir dry-run si variable de entorno SLACK_DRY_RUN está definida
    if [[ -n "$dry_run_flag" ]]; then
      cmd+=(--dry-run)
      echo "[DRY-RUN] (Slack)" >&2
    fi

    # Construir array de archivos si existe files_list
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

    # --- Mejorado visualmente dentro del mismo log: separar la salida del notificador ---
    echo "----- INICIO SLACK NOTIFIER OUTPUT -----" >&2

    # Capturar salida en temporal para conservar rc real
    tmp_out="$(mktemp -t slack-notifier.XXXXXX 2>/dev/null || mktemp)"
    if "${cmd[@]}" >"$tmp_out" 2>&1; then
      rc=0
    else
      rc=$?
    fi

    # Reimprimir la salida prefijando cada línea con [SLACK] (llega a la consola y al log principal)
    sed 's/^/   [SLACK] /' "$tmp_out" >&2

    echo "----- FIN SLACK NOTIFIER OUTPUT (rc=$rc) -----" >&2

    rm -f "$tmp_out" 2>/dev/null || true
    # --- Fin mejora visual ---

    if (( rc != 0 )); then
      echo "[WARNING] Falló el envío de notificación Slack (rc=$rc, title='$title')" >&2
    fi
    return $rc
}

# Guardar hora de inicio
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

# Clear/initialize output files when starting from scratch
if [[ "$RESUME_FROM_CHECKPOINT" != "true" ]]; then
  echo "🗑️  Limpiando archivos de salida anteriores..."
  : > "$MAVEN_OUTPUT_FILE"
  : > "$GRADLE_OUTPUT_FILE"
  : > "$NODE_OUTPUT_FILE"
  : > "$GO_OUTPUT_FILE"
  : > "$MAVEN_WITHOUT_CI_FILE"
  : > "$GRADLE_WITHOUT_CI_FILE"
  : > "$NODE_WITHOUT_CI_FILE"
  : > "$GO_WITHOUT_CI_FILE"
  echo "✅ Archivos de salida inicializados"
fi

# Preparar mensaje de inicio con información del límite
LIMIT_MSG=""
if [[ $REPO_LIMIT -gt 0 ]]; then
  LIMIT_MSG=" (limitado a $REPO_LIMIT repositorios)"
fi

# Enviar notificación de inicio
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

# Listar todos los repositorios (maneja paginación manualmente)
REPOS=()
END_CURSOR=""  # usar cadena vacía para primera página
page_count=0

# Definir query GraphQL multilinea (sin escapes \n que rompan sintaxis)
read -r -d '' REPOS_QUERY <<'EOF'
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

echo "Fetching repository list from organization..."

while true; do
  page_count=$((page_count + 1))
  echo "  Fetching page $page_count..."
  # Llamar a la API; sólo pasar endCursor si no está vacío
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

# Aplicar límite si está configurado
if [[ $REPO_LIMIT -gt 0 ]] && [[ $total_repos -gt $REPO_LIMIT ]]; then
  echo "⚠ Aplicando límite: analizando solo los primeros $REPO_LIMIT repositorios"
  REPOS=(${REPOS[@]:0:$REPO_LIMIT})
  total_repos=$REPO_LIMIT
fi

echo "Se analizarán $total_repos repositorios.\n"

# Enumerar los repositorios al inicio
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

# Mostrar el número del repositorio durante el procesamiento
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
  DEFAULT_BRANCH=$(gh repo view "$ORG/$REPO" --json defaultBranchRef -q .defaultBranchRef.name)
  echo "Branch por defecto: $DEFAULT_BRANCH"

  # Variables para rastrear el tipo de proyecto
  PROJECT_TYPE=""

  # Verificar tipo de proyecto
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
  else
    echo "  - Tipo de proyecto: No determinado"
    other_repos=$((other_repos + 1))
    echo "    -> Contador 'Otros': $other_repos"
  fi

  # Listar archivos en .github/workflows
  FILES=$(safe_gh_api repos/$ORG/$REPO/contents/.github/workflows?ref=$DEFAULT_BRANCH --jq '.[] | select(.name | endswith(".yml")) | .name' 2>/dev/null || echo "")
  exit_code=$?
  if (( exit_code == 2 )); then
    save_checkpoint "$REPO"
    echo "⚠️  Rate limit reached. Progress saved. Exiting..."
    exit 3
  fi

  # Flags para determinar si se encontró CI unificado
  maven_found=0
  gradle_found=0
  node_found=0
  go_found=0

  if [[ -n "$FILES" ]]; then
    # Mostrar conteo y lista numerada para más claridad
    WF_COUNT=$(printf "%s\n" "$FILES" | sed '/^\s*$/d' | wc -l | tr -d ' ')
    echo "Workflows encontrados: $WF_COUNT"
    printf "%s\n" "$FILES" | nl -w2 -s'. ' | sed 's/^/  /'

    # Process each workflow file - avoid subshell to preserve flag variables
    while IFS= read -r WF; do
      [[ -z "$WF" ]] && continue

      # Decodificador base64 con fallback (GNU / macOS). Definido aquí localmente.
      decode_base64() {
        local input="$1"
        # eliminar saltos de línea que puedan venir en la cadena
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

      # Obtener campos individuales con --jq para evitar pasar JSON potencialmente problemático a jq
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

      # Decodificar contenido si existe; si falla, dejar CONTENT vacío
      CONTENT=""
      if [[ -n "$CONTENT_B64" && "$CONTENT_B64" != "null" ]]; then
        if CONTENT_DECODED="$(decode_base64 "$CONTENT_B64")"; then
          CONTENT="$CONTENT_DECODED"
        else
          CONTENT=""
        fi
      fi

      # Métricas simples del contenido (proteger cuando CONTENT esté vacío)
      if [[ -n "$CONTENT" ]]; then
        LINES=$(printf "%s" "$CONTENT" | sed -n '1,$p' | wc -l | tr -d ' ')
        USES_COUNT=$(printf "%s" "$CONTENT" | grep -c -E '^\s*uses:' || true)
        RUN_COUNT=$(printf "%s" "$CONTENT" | grep -c -E '^\s*run:' || true)
      else
        LINES=0
        USES_COUNT=0
        RUN_COUNT=0
      fi

      # Detectar referencias a los workflows reutilizables (listado único)
      REFS=$(printf "%s" "$CONTENT" | grep -oE '(maven-ci.yml|gradle-ci.yml|node-ci.yml|go-ci.yml)' | sort -u | tr '\n' ',' | sed 's/,$//')
      if [[ -z "$REFS" ]]; then REFS="(ninguna)"; fi

      # Impresión formateada y más clara
      echo ""
      echo "  Analizando archivo: $WF"
      echo "    - Tamaño: $SIZE bytes"
      echo "    - SHA: $SHA"
      echo "    - Líneas: $LINES"
      echo "    - Occurencias: uses=$USES_COUNT  run=$RUN_COUNT"
      echo "    - Referencias detectadas: $REFS"

      # Buscar maven-ci.yml
      if [[ $maven_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'maven-ci.yml'; then
        echo "    ✔ Se encontró referencia a maven-ci.yml en $WF"
        maven_ci_repos=$((maven_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$MAVEN_OUTPUT_FILE"
        echo "    name: $REPO" >> "$MAVEN_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$MAVEN_OUTPUT_FILE"
        maven_found=1
      fi

      # Buscar gradle-ci.yml
      if [[ $gradle_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'gradle-ci.yml'; then
        echo "    ✔ Se encontró referencia a gradle-ci.yml en $WF"
        gradle_ci_repos=$((gradle_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$GRADLE_OUTPUT_FILE"
        echo "    name: $REPO" >> "$GRADLE_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$GRADLE_OUTPUT_FILE"
        gradle_found=1
      fi

      # Buscar node-ci.yml
      if [[ $node_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'node-ci.yml'; then
        echo "    ✔ Se encontró referencia a node-ci.yml en $WF"
        node_ci_repos=$((node_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$NODE_OUTPUT_FILE"
        echo "    name: $REPO" >> "$NODE_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$NODE_OUTPUT_FILE"
        node_found=1
      fi

      # Buscar go-ci.yml
      if [[ $go_found -eq 0 ]] && printf "%s" "$CONTENT" | grep -q 'go-ci.yml'; then
        echo "    ✔ Se encontró referencia a go-ci.yml en $WF"
        go_ci_repos=$((go_ci_repos + 1))
        echo "  - url: $REPO_URL" >> "$GO_OUTPUT_FILE"
        echo "    name: $REPO" >> "$GO_OUTPUT_FILE"
        echo "    branch: $DEFAULT_BRANCH" >> "$GO_OUTPUT_FILE"
        go_found=1
      fi
    done <<< "$FILES"
  else
    echo "No se encontraron workflows en .github/workflows para $REPO."
  fi

  # Guardar repos sin CI unificado según su tipo de proyecto
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

  # Save checkpoint after each repo
  save_checkpoint "$REPO"

  echo ""
done

# Clear checkpoint on successful completion
clear_checkpoint

# Calcular tiempo de ejecución
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
echo "Total de repositorios analizados: $total_repos"
if [[ $REPO_LIMIT -gt 0 ]]; then
  echo "(⚠ Análisis limitado a $REPO_LIMIT repositorios)"
fi
echo "-------------------------------"
echo "Repositorios con CI unificado:"
echo "  - Maven: $maven_ci_repos"
echo "  - Gradle: $gradle_ci_repos"
echo "  - Node.js: $node_ci_repos"
echo "  - Go: $go_ci_repos"
echo "-------------------------------"
echo "Tipos de proyecto detectados:"
echo "  - Maven: $maven_repos (sin CI: $((maven_repos - maven_ci_repos)))"
echo "  - Gradle: $gradle_repos (sin CI: $((gradle_repos - gradle_ci_repos)))"
echo "  - Node.js: $node_repos (sin CI: $((node_repos - node_ci_repos)))"
echo "  - Go: $go_repos (sin CI: $((go_repos - go_ci_repos)))"
echo "  - Otros: $other_repos"
echo "-------------------------------"
echo "Archivos generados:"
echo "  Con CI unificado: *-repos.yml"
echo "  Sin CI unificado: *-repos-without-ci.yml"
echo "==============================="

# Preparar mensaje para Slack con resumen
TOTAL_WITH_CI=$((maven_ci_repos + gradle_ci_repos + node_ci_repos + go_ci_repos))
TOTAL_WITHOUT_CI=$((maven_repos - maven_ci_repos + gradle_repos - gradle_ci_repos + node_repos - node_ci_repos + go_repos - go_ci_repos))

# Construir mensaje multilinea (Markdown) con here-doc para preservar saltos
SLACK_MESSAGE=$(cat <<EOF
*Repositorios analizados:* $total_repos
*Con CI unificado:* $TOTAL_WITH_CI
*Sin CI unificado:* $TOTAL_WITHOUT_CI

*Desglose por tecnología:*
• Maven: $maven_repos total ($maven_ci_repos con CI, $((maven_repos - maven_ci_repos)) sin CI)
• Gradle: $gradle_repos total ($gradle_ci_repos con CI, $((gradle_repos - gradle_ci_repos)) sin CI)
• Node.js: $node_repos total ($node_ci_repos con CI, $((node_repos - node_ci_repos)) sin CI)
• Go: $go_repos total ($go_ci_repos con CI, $((go_repos - go_ci_repos)) sin CI)
• Otros: $other_repos

⏱️ *Duración:* ${DURATION_MIN}m ${DURATION_SEC}s
EOF
)

# Si hubo límite, añadir línea adicional
if [[ $REPO_LIMIT -gt 0 ]]; then
  SLACK_MESSAGE+=$'\n⚠️ _Análisis limitado a '$REPO_LIMIT$' repositorios (modo prueba)_'
fi

# Construir lista de archivos generados a adjuntar (todos los .yml creados por el script)
ATTACH_FILES="$MAVEN_OUTPUT_FILE $GRADLE_OUTPUT_FILE $NODE_OUTPUT_FILE $GO_OUTPUT_FILE $MAVEN_WITHOUT_CI_FILE $GRADLE_WITHOUT_CI_FILE $NODE_WITHOUT_CI_FILE $GO_WITHOUT_CI_FILE"

# --- Añadido: incluir el archivo de log en los adjuntos si existe ---
ATTACH_FILES="$ATTACH_FILES $LOG_FILE"
# --- Fin añadido ---

if send_slack_notification \
    "✅ Análisis de Repositorios - Completado" \
    "$SLACK_MESSAGE" \
    "SUCCESS" \
    "Total:$total_repos,Con CI:$TOTAL_WITH_CI,Sin CI:$TOTAL_WITHOUT_CI,Duración:${DURATION_MIN}m ${DURATION_SEC}s" \
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
