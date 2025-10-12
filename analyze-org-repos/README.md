# Repository CI Analysis Script

## Overview

`analyze-org-repos-with-slack.sh` is a comprehensive Zsh script that audits all repositories in a GitHub organization to identify which projects are using reusable/unified CI workflows. It analyzes project types, detects CI workflow references, generates detailed reports, and sends Slack notifications with results.

## What It Does

The script performs the following operations:

1. **Fetches all repositories** from a specified GitHub organization using GraphQL API with pagination
2. **Identifies project types** by detecting technology-specific files:
   - **Maven**: `pom.xml`
   - **Gradle**: `build.gradle` or `build.gradle.kts`
   - **Node.js**: `package.json`
   - **Go**: `go.mod`
3. **Analyzes GitHub Actions workflows** in `.github/workflows/` to detect references to reusable CI workflows:
   - `maven-ci.yml`
   - `gradle-ci.yml`
   - `node-ci.yml`
   - `go-ci.yml`
4. **Generates YAML reports** listing repositories with and without unified CI
5. **Sends Slack notifications** at start and completion with detailed metrics and file attachments
6. **Handles GitHub API rate limits** intelligently with adaptive throttling
7. **Supports checkpoint/resume** functionality for long-running analyses
8. **Logs all output** to timestamped log files

## Architecture & Key Features

### Rate Limiting Management
- **Adaptive Throttling**: Automatically adjusts request delay based on remaining API quota
  - < 50 remaining: 2.0s delay
  - < 100 remaining: 1.0s delay
  - < 500 remaining: 0.5s delay
  - < 1000 remaining: 0.3s delay
  - Otherwise: 0.1s delay
- **Graceful Degradation**: If rate limit is critically low and reset time exceeds 10 minutes, saves progress and exits
- **Automatic Retry**: Retries failed requests up to 3 times with exponential backoff
- **Status Monitoring**: Checks rate limit every 30 seconds and logs remaining quota

### Checkpoint System
- Saves progress after processing each repository
- Stores all counters, last processed repo, and timestamp
- On restart, prompts user to resume from checkpoint or start fresh (10s timeout, defaults to fresh start)
- Automatically clears checkpoint on successful completion
- Critical for handling interruptions during long analyses

### Slack Integration
- **Start Notification**: Sends when analysis begins with configuration details
- **Completion Notification**: Sends detailed summary with:
  - Total repositories analyzed
  - Breakdown by technology (total, with CI, without CI)
  - Execution duration
  - All generated YAML files and log file as attachments
- **Template-Based Messages**: Uses custom templates (simple, workflow_success, workflow_failure)
- **Metadata Fields**: Structured key-value pairs for machine-readable data
- **Dry-Run Mode**: Set `SLACK_DRY_RUN` environment variable to test without sending

### Logging
- All stdout and stderr redirected to timestamped log file via `tee`
- Log file created at: `logs/analyze-org-repos-YYYYMMDD-HHMMSS.log`
- Slack notifier output is clearly separated with visual delimiters
- Log file automatically attached to completion Slack notification

## Output Files

### Repositories WITH Unified CI
- `maven-repos.yml` - Maven projects using maven-ci.yml
- `gradle-repos.yml` - Gradle projects using gradle-ci.yml
- `node-repos.yml` - Node.js projects using node-ci.yml
- `go-repos.yml` - Go projects using go-ci.yml

### Repositories WITHOUT Unified CI
- `maven-repos-without-ci.yml` - Maven projects NOT using unified CI
- `gradle-repos-without-ci.yml` - Gradle projects NOT using unified CI
- `node-repos-without-ci.yml` - Node.js projects NOT using unified CI
- `go-repos-without-ci.yml` - Go projects NOT using unified CI

Each entry contains:
```yaml
- url: https://github.com/org/repo-name
  name: repo-name
  branch: main
```

## Prerequisites

- **Zsh shell** (script uses Zsh-specific features)
- **GitHub CLI (`gh`)** installed and authenticated with read access to target organization
- **jq** - JSON processor for parsing API responses
- **Python 3** - For Slack notifier SDK
- **Slack Notifier SDK** - Located at `../slack-notifier/slack_notifier_sdk.py` (relative to script)

### Environment Variables (Optional)
- `SLACK_BOT_TOKEN` - Slack bot token for sending notifications (omit to skip Slack)
- `SLACK_CHANNEL` - Slack channel ID/name for notifications (omit to skip Slack)
- `SLACK_DRY_RUN` - Set to any value to enable dry-run mode (no actual Slack messages sent)

## Usage

### Basic Usage (Analyze All Repositories)
```bash
./analyze-org-repos-with-slack.sh my-organization
```

### Limit Analysis (Testing)
```bash
./analyze-org-repos-with-slack.sh my-organization 10
```
Analyzes only the first 10 repositories in the specified organization.

### With Slack Notifications
```bash
export SLACK_BOT_TOKEN="xoxb-your-token"
export SLACK_CHANNEL="#ci-audits"
./analyze-org-repos-with-slack.sh my-organization
```

### Dry-Run Mode (Test Without Sending Slack Messages)
```bash
export SLACK_DRY_RUN=1
./analyze-org-repos-with-slack.sh my-organization 5
```

### Resume from Checkpoint
If the script is interrupted, on next run it will prompt:
```
❓ ¿Desea continuar desde el último checkpoint? (y/n) [10s timeout, default=n]
```
- Press `y` to resume from last processed repository
- Press `n` or wait 10 seconds to start fresh

## Configuration

### Organization Name
The organization is now a **required command-line parameter**:
```bash
./analyze-org-repos-with-slack.sh your-org-name
```
This allows analyzing different organizations without editing the script.

**Note:** If a checkpoint exists for a different organization, the script will warn you and start fresh.

### Rate Limit Settings
Lines 33-37:
```bash
MAX_WAIT_TIME=600          # Max time to wait for rate limit reset (10 min)
RATE_LIMIT_THRESHOLD=100   # Threshold for adaptive throttling
MIN_DELAY=0.1              # Minimum delay between requests (seconds)
MAX_DELAY=2.0              # Maximum delay between requests (seconds)
```

## User Stories

### 1. DevOps Audit
**As a DevOps engineer**, I want to audit all repositories in my organization to see which ones are using our standardized CI workflows, so I can:
- Identify repositories that need migration to unified CI
- Track CI adoption progress across teams
- Prioritize migration efforts based on project types

### 2. Technology Stack Analysis
**As a platform team member**, I want to understand the distribution of technology stacks (Maven, Gradle, Node, Go) across our organization, so I can:
- Make data-driven decisions about tooling investments
- Identify underserved technology ecosystems
- Plan capacity for different CI infrastructure needs

### 3. Automated Reporting
**As a team lead**, I want to receive automated Slack notifications with detailed reports about CI adoption, so I can:
- Track progress without manually running scripts
- Share metrics with stakeholders instantly
- Download generated reports directly from Slack

### 4. Resilient Long-Running Analysis
**As a developer**, I want the script to handle GitHub API rate limits gracefully and save progress, so I can:
- Analyze large organizations (hundreds of repos) without manual intervention
- Resume analysis after interruptions without losing progress
- Avoid hitting GitHub API limits that would block other tools

### 5. Compliance Tracking
**As an auditor**, I want to generate lists of repositories lacking standardized CI, so I can:
- Create action items for teams to adopt best practices
- Measure compliance with organizational CI standards
- Generate reports for security and governance reviews

### 6. Operational Transparency
**As a system administrator**, I want all script execution to be logged with timestamps, so I can:
- Troubleshoot issues when analyses fail
- Maintain audit trails of who ran what and when
- Review detailed analysis history attached to Slack notifications

### 7. Incremental Processing
**As a user running long analyses**, I want to be able to resume from checkpoints if the script is interrupted, so I can:
- Handle network interruptions gracefully
- Stop and resume analysis across different time windows
- Avoid wasting API quota re-processing repositories

### 8. Testing and Validation
**As a developer testing the script**, I want to limit analysis to a small number of repositories, so I can:
- Verify functionality quickly without analyzing entire org
- Test Slack integration with minimal API usage
- Iterate on script changes efficiently

## Workflow Analysis Details

For each repository, the script:

1. **Determines default branch** (usually `main` or `master`)
2. **Detects project type** by checking for marker files
3. **Lists all YAML workflow files** in `.github/workflows/`
4. **Downloads and decodes each workflow file** (base64 decoding with GNU/macOS compatibility)
5. **Analyzes workflow content** for references to reusable CI workflows
6. **Extracts metrics**: file size, line count, `uses:` count, `run:` count
7. **Flags repositories** without unified CI for their project type

## Error Handling

- **Rate Limit Exceeded**: Saves checkpoint and exits with code 3
- **API Errors**: Retries up to 3 times with exponential backoff
- **No Repositories Found**: Sends error notification to Slack, exits with code 1
- **Invalid Limit Parameter**: Shows usage message, exits with code 1
- **Slack Notification Failures**: Logs warning but continues execution
- **Missing Slack SDK**: Logs warning, skips notifications

## Performance Characteristics

- **API Calls per Repository**: ~5-15 calls depending on workflow count
- **Throttling Impact**: Adaptive delays increase total runtime but prevent rate limit exhaustion
- **Typical Runtime**:
  - 50 repos: ~5-10 minutes
  - 100 repos: ~10-20 minutes
  - 500+ repos: May require checkpoint/resume across multiple sessions
- **Rate Limit Usage**: Check final status at end of execution

## Output Example

### Console Output
```
===============================
Iniciando análisis de repositorios en la organización: some-org
Hora de inicio: 2025-10-12 14:30:00
Límite de repositorios: 10
===============================

Se encontraron 156 repositorios en total.
Se analizarán 10 repositorios.

===============================
Procesando repositorios...
===============================

---------------------------------------
Procesando repositorio 1/10: api-gateway
URL: https://github.com/some-org/api-gateway
Branch por defecto: main
  - Tipo de proyecto: Maven (pom.xml encontrado)
    -> Contador Maven: 1
Workflows encontrados: 2
  1. ci.yml
  2. deploy.yml

  Analizando archivo: ci.yml
    - Tamaño: 1245 bytes
    - SHA: abc123...
    - Líneas: 45
    - Occurencias: uses=8  run=3
    - Referencias detectadas: maven-ci.yml
    ✔ Se encontró referencia a maven-ci.yml en ci.yml

...

===============================
Análisis completado. Resultados guardados en los archivos correspondientes.
Hora de finalización: 2025-10-12 14:35:30
Duración: 5m 30s
===============================

===============================
Resumen de Métricas
===============================
Total de repositorios analizados: 10
(⚠ Análisis limitado a 10 repositorios)
-------------------------------
Repositorios con CI unificado:
  - Maven: 3
  - Gradle: 1
  - Node.js: 2
  - Go: 0
-------------------------------
Tipos de proyecto detectados:
  - Maven: 4 (sin CI: 1)
  - Gradle: 2 (sin CI: 1)
  - Node.js: 3 (sin CI: 1)
  - Go: 1 (sin CI: 1)
  - Otros: 0
-------------------------------
```

## Maintenance Notes

### Updating Organization
Change line 28:
```bash
ORG="your-org-name"
```

### Adding New CI Workflow Types
To track additional reusable workflows:
1. Add output file variables (lines 49-59)
2. Add counter variables (lines 61-72)
3. Add detection logic in workflow analysis loop (lines 717-755)
4. Add "without CI" tracking logic (lines 762-792)
5. Update summary metrics (lines 817-840)
6. Update Slack message format (lines 847-861)

### Customizing Slack Messages
Edit the `send_slack_notification` function (lines 243-376) or modify the template selection logic (lines 280-286).

## Troubleshooting

### Script hangs during execution
- Check rate limit status: `gh api rate_limit`
- Script may be waiting for rate limit reset (max 10 minutes)
- Press Ctrl+C and resume from checkpoint later

### "No repositories found"
- Verify `gh auth status` shows correct authentication
- Ensure authenticated account has read access to organization
- Check organization name spelling

### Slack notifications not sending
- Verify `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` are set
- Check bot has permission to post in channel
- Review Slack notifier SDK logs in output
- Try dry-run mode to test: `export SLACK_DRY_RUN=1`

### Checkpoint not resuming correctly
- Delete checkpoint file: `rm .analyze-progress-checkpoint.txt`
- Start fresh analysis

## Security Considerations

- **Read-Only Operations**: Script only reads repository data, never modifies
- **Token Permissions**: Requires only `repo:read` and `org:read` scopes
- **No Secrets Logged**: Tokens not written to log files
- **Local Execution**: All processing happens locally, no external data sharing

## License & Attribution

Script generated for internal use. Customize as needed for your organization's requirements.
