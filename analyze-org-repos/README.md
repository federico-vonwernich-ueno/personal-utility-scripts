# Repository CI Analysis Script

A Zsh script that audits all repositories in a GitHub organization to identify which projects are using reusable/unified CI workflows.

## Overview

`analyze-org-repos-with-slack.sh` analyzes repositories in a GitHub organization, detects technology stacks (Maven, Gradle, Node.js, Go, Flutter), identifies CI workflow usage, and sends detailed reports via Slack.

### Key Features

- Fetches all repositories from a GitHub organization
- Detects project types (Maven, Gradle, Node.js, Go, Flutter)
- Analyzes GitHub Actions workflows for reusable CI references
- Generates YAML reports of repositories with and without unified CI
- Sends Slack notifications with progress and results
- Handles GitHub API rate limits with adaptive throttling
- Supports checkpoint/resume for long-running analyses
- Skips archived repositories automatically

## Quick Start

### Prerequisites

- Zsh shell
- GitHub CLI (`gh`) - [Setup guide](../docs/GITHUB_SETUP.md)
- `jq` - JSON processor
- `bc` - Basic calculator (for percentage calculations)
- Python 3 - For Slack notifications and CSV parsing
- Slack Bot Token (optional) - [Setup guide](../docs/SLACK_INTEGRATION.md)

Install tools:
```bash
# macOS
brew install gh jq bc

# Ubuntu/Debian
sudo apt install jq bc
```

### Basic Usage

```bash
# Analyze all repositories in an organization
./analyze-org-repos-with-slack.sh my-organization

# Limit to first 10 repos (for testing)
./analyze-org-repos-with-slack.sh my-organization 10

# Analyze with CSV metrics tracking
./analyze-org-repos-with-slack.sh my-organization --csv-file repos-data.csv

# Combined: limit and CSV
./analyze-org-repos-with-slack.sh my-organization 10 --csv-file repos-data.csv
```

### With Slack Notifications

```bash
export SLACK_BOT_TOKEN="xoxb-your-token"
export SLACK_CHANNEL="#ci-audits"
./analyze-org-repos-with-slack.sh my-organization
```

See [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) for detailed setup.

### Dry-Run Mode

```bash
export SLACK_DRY_RUN=1
./analyze-org-repos-with-slack.sh my-organization 5
```

## Configuration

The organization name is a required command-line parameter:

```bash
./analyze-org-repos-with-slack.sh <organization-name> [limit] [--csv-file <path>]
```

**Arguments**:
- `organization-name` (required): GitHub organization to analyze
- `limit` (optional): Number of repositories to analyze (for testing)
- `--csv-file` (optional): Path to CSV file for adoption/technology tracking

**Environment Variables**:
- `SLACK_BOT_TOKEN`: Slack bot token (optional, see [Slack guide](../docs/SLACK_INTEGRATION.md))
- `SLACK_CHANNEL`: Slack channel for notifications (optional)
- `SLACK_DRY_RUN`: Enable dry-run mode (optional)

### CSV File Format

The CSV file should contain the following columns (case-insensitive):

- **Repositorio**: Full GitHub repository URL (e.g., `https://github.com/org/repo-name`)
- **Adopcion** (or Adopción): Adoption state (e.g., "Adoptado", "Bloqueado", "En Progreso")
- **Tecnología**: Technology annotation (e.g., "Maven", "Node.js", "Golang")

Example CSV:
```csv
Repositorio,Adopcion,Tecnología
https://github.com/my-org/api-service,Adoptado,Maven
https://github.com/my-org/web-app,En Progreso,Node.js
https://github.com/my-org/backend,Bloqueado,Golang
```

**Note**: The script normalizes repository URLs (handles http/https, trailing slashes, .git suffix) and technology names (case-insensitive matching) for robust comparison.

## Output Files

### Repositories WITH Unified CI
- `maven-repos.yml` - Maven projects using maven-ci.yml
- `gradle-repos.yml` - Gradle projects using gradle-ci.yml
- `node-repos.yml` - Node.js projects using node-ci.yml
- `go-repos.yml` - Go projects using go-ci.yml
- `flutter-repos.yml` - Flutter projects using flutter-ci.yml

### Repositories WITHOUT Unified CI
- `maven-repos-without-ci.yml`
- `gradle-repos-without-ci.yml`
- `node-repos-without-ci.yml`
- `go-repos-without-ci.yml`
- `flutter-repos-without-ci.yml`

Each YAML file contains:
```yaml
- url: https://github.com/org/repo-name
  name: repo-name
  branch: main
```

### CSV Analysis Reports (when --csv-file is provided)
- `technology-mismatches.txt` - Repositories where CSV technology annotation doesn't match detected technology
- `technology-adoption-distribution.txt` - Global adoption summary and per-technology percentages
- `maven-adoption.txt` - List of Maven repositories grouped by adoption state
- `gradle-adoption.txt` - List of Gradle repositories grouped by adoption state
- `node-adoption.txt` - List of Node repositories grouped by adoption state
- `go-adoption.txt` - List of Go repositories grouped by adoption state
- `flutter-adoption.txt` - List of Flutter repositories grouped by adoption state

Each per-technology file contains:
```
# Maven Adoption Report
# Total: 104 repos
# Generated: 2025-10-28 04:18:03

CI Cambios aplicados (67 repos - 64.4%):
- https://github.com/org/api-service
- https://github.com/org/payment-gateway
...

No (18 repos - 17.3%):
- https://github.com/org/legacy-app
...
```

### Logs
- `logs/analyze-org-repos-YYYYMMDD-HHMMSS.log` - Timestamped execution logs

## How It Works

1. Fetches all repositories from the specified GitHub organization using GraphQL API
2. Parses CSV file (if provided) and loads adoption/technology data
3. For each repository:
   - Identifies project type by detecting marker files (`pom.xml`, `package.json`, etc.)
   - Lists all YAML workflow files in `.github/workflows/`
   - Downloads and analyzes each workflow file
   - Checks for references to reusable CI workflows
   - If CSV provided: matches repo against CSV data, tracks adoption state, validates technology annotation
4. Generates YAML reports for repositories with and without unified CI
5. Generates CSV analysis reports (if CSV provided):
   - Technology mismatches between CSV annotation and detected technology
   - Global adoption distribution summary
   - Per-technology adoption reports with repository listings
6. Sends Slack notifications with results (if configured), including:
   - Global adoption summary
   - Technology-specific adoption breakdown

### CSV Analysis Features

When a CSV file is provided with `--csv-file`, the script performs additional analysis:

**Adoption Tracking**:
- Tracks all unique adoption states found in CSV
- Calculates global adoption distribution (percentages)
- Breaks down adoption by detected technology (Maven, Gradle, Node, Go, Flutter)
- Generates per-technology reports listing specific repositories for each adoption state

**Technology Validation**:
- Compares CSV technology annotation against detected technology
- Reports accuracy metrics (correct vs mismatched annotations)
- Generates detailed report of all technology mismatches

**Per-Technology Reports**:
- One file per technology (maven-adoption.txt, gradle-adoption.txt, etc.)
- Each adoption state shows the actual repository URLs
- Useful for identifying which specific repositories need attention

**Example Metrics Output**:
```
Maven (15 repos in CSV):
  • Adoptado: 10 repos (66.7%)
  • Bloqueado: 3 repos (20.0%)
  • En Progreso: 2 repos (13.3%)

Node (8 repos in CSV):
  • Adoptado: 6 repos (75.0%)
  • Pendiente: 2 repos (25.0%)

Technology Accuracy: 85.5% (47/55 correct)
```

### Rate Limiting

The script handles GitHub API rate limits automatically:
- Adaptive throttling based on remaining API quota
- Graceful degradation if rate limit is critically low
- Automatic retry with exponential backoff
- Status monitoring every 30 seconds

### Checkpoint/Resume

The script saves progress after each repository:
- Checkpoint file: `.analyze-progress-checkpoint.txt`
- On restart, prompts to resume from last checkpoint
- Auto-clears checkpoint on successful completion
- 10-second timeout defaults to fresh start

## Example Output

```
===============================
Iniciando análisis de repositorios en la organización: my-org
Hora de inicio: 2025-10-23 14:30:00
Límite de repositorios: 10
===============================

Se encontraron 156 repositorios en total.
Se analizarán 10 repositorios.

Procesando repositorio 1/10: api-gateway
  - Tipo de proyecto: Maven (pom.xml encontrado)
  - ✔ Se encontró referencia a maven-ci.yml

===============================
Resumen de Métricas
===============================
Total de repositorios analizados: 10
Repositorios con CI unificado:
  - Maven: 3
  - Node.js: 2
Tipos de proyecto detectados:
  - Maven: 4 (sin CI: 1)
  - Node.js: 3 (sin CI: 1)
===============================
```

## Common Issues

### "No repositories found"
- Verify `gh auth status` shows correct authentication
- Ensure you have read access to the organization
- Check organization name spelling

### Script hangs during execution
- Check rate limit: `gh api rate_limit`
- Script may be waiting for rate limit reset (max 10 minutes)
- Press Ctrl+C and resume from checkpoint later

### Checkpoint not resuming
Delete checkpoint file and start fresh:
```bash
rm .analyze-progress-checkpoint.txt
./analyze-org-repos-with-slack.sh my-organization
```

For more troubleshooting, see [Contributing Guide](../CONTRIBUTING.md).

## Advanced Configuration

Edit script to customize:
- **Line 33-37**: Rate limit thresholds and delays
- **Line 717-755**: Add detection for new CI workflow types
- **Line 817-840**: Update summary metrics display

## Related Documentation

- [GitHub Setup Guide](../docs/GITHUB_SETUP.md) - GitHub CLI authentication
- [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) - Slack notifications setup
- [Contributing Guide](../CONTRIBUTING.md) - Common patterns and troubleshooting

## Security Considerations

- Read-only operations (no modifications to repositories)
- Requires only `repo:read` and `org:read` scopes
- Tokens not written to log files
- All processing happens locally
