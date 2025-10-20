# GitHub Workflow Monitor

A Python script to continuously monitor GitHub workflow runs across multiple repositories and detect failures in real-time.

## Features

- ✅ Continuous monitoring with configurable polling intervals
- ✅ Single-check mode for one-time monitoring
- ✅ Track multiple repositories and workflows
- ✅ Filter by specific workflows and/or branches
- ✅ Detect and report new failures with detailed information
- ✅ State persistence to avoid duplicate alerts
- ✅ Failed job details for each failure
- ✅ Colored console output with timestamps
- ✅ Configurable lookback window
- ✅ Comprehensive error handling

## Prerequisites

- Python 3.6 or higher
- GitHub CLI (`gh`) installed and authenticated
- PyYAML library

### Installation

1. Install GitHub CLI if not already installed:
   ```bash
   # macOS
   brew install gh
   
   # Other platforms: https://cli.github.com/manual/installation
   ```

2. Authenticate with GitHub:
   ```bash
   gh auth login
   ```

3. Install Python dependencies:
   ```bash
   pip install PyYAML
   ```

## Configuration

Create a YAML configuration file with the following structure:

```yaml
# Global settings
poll_interval: 60           # Seconds between checks (for continuous mode)
lookback_minutes: 60        # How far back to look for workflow runs
max_runs_per_check: 100     # Maximum number of runs to check per repository

# List of repositories to monitor
repositories:
  # Monitor all workflows on all branches
  - repository: "owner/repo-name"
  
  # Monitor specific workflow
  - repository: "owner/another-repo"
    workflow: "ci.yml"
  
  # Monitor specific workflow on specific branch
  - repository: "https://github.com/myorg/myrepo"
    workflow: "deploy.yml"
    branch: "main"
  
  # Repository URL formats also supported
  - repository: "git@github.com:myorg/api.git"
    workflow: "test.yml"
    branch: "develop"
```

See `config.example.yaml` for a complete example.

## Usage

### Continuous Monitoring

Monitor continuously, checking for new failures at regular intervals:

```bash
python monitor_workflows.py config.yaml
```

The monitor will run indefinitely, checking for failures every `poll_interval` seconds. Press `Ctrl+C` to stop.

### Single Check

Run a single check and exit (useful for cron jobs or CI/CD):

```bash
python monitor_workflows.py config.yaml --once
```

Exit codes:
- `0`: No new failures detected
- `1`: New failures detected

### Custom State File

Use a custom location for the state file:

```bash
python monitor_workflows.py config.yaml --state-file /tmp/my-state.json
```

### Help

```bash
python monitor_workflows.py --help
```

## How It Works

1. **Configuration Loading**: Reads and validates the YAML configuration
2. **State Management**: Loads previously seen workflow runs from state file
3. **Polling Loop** (continuous mode):
   - For each configured repository:
     - Fetches recent workflow runs using GitHub CLI
     - Filters runs within the lookback window
     - Checks for completed runs with failure status
     - Identifies new failures not seen before
     - Reports detailed failure information
   - Updates state file with newly seen runs
   - Waits for poll interval before next check
4. **Failure Reporting**: When a new failure is detected:
   - Displays workflow details (name, branch, run ID, URL)
   - Lists all failed jobs with their conclusions
   - Marks the run as seen to avoid duplicate alerts

## Configuration Options

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `poll_interval` | integer | No | 60 | Seconds between checks (continuous mode only) |
| `lookback_minutes` | integer | No | 60 | How many minutes back to check for runs |
| `max_runs_per_check` | integer | No | 100 | Maximum runs to fetch per repository |
| `repositories` | array | Yes | - | List of repository configurations |
| `repositories[].repository` | string | Yes | - | Repository in "owner/name" format or GitHub URL |
| `repositories[].workflow` | string | No | - | Workflow file name to filter (e.g., "ci.yml") |
| `repositories[].branch` | string | No | - | Branch name to filter |

## Examples

### Example 1: Monitor all workflows in a repository

```yaml
poll_interval: 30
lookback_minutes: 30

repositories:
  - repository: "myorg/myrepo"
```

### Example 2: Monitor specific workflows across multiple repos

```yaml
poll_interval: 60
lookback_minutes: 120

repositories:
  - repository: "myorg/frontend"
    workflow: "deploy.yml"
    branch: "production"
  
  - repository: "myorg/backend"
    workflow: "ci.yml"
    branch: "main"
  
  - repository: "myorg/api"
    workflow: "integration-tests.yml"
```

### Example 3: Single check with cron

Add to your crontab to check every 5 minutes:

```bash
*/5 * * * * cd /path/to/workflow-monitor && python monitor_workflows.py config.yaml --once >> monitor.log 2>&1
```

## Output

The script provides colored, timestamped output:

- 🔵 **Info** (Cyan): General information and progress
- ✓ **Success** (Green): No failures detected
- ⚠ **Warning** (Yellow): Non-critical issues
- ✗ **Error** (Red): Failures detected

### Example Output

```
Workflow Monitor Starting
=========================
[2024-10-03 14:30:15] ℹ Repositories to monitor: 3
[2024-10-03 14:30:15] ℹ Poll interval: 60 seconds
[2024-10-03 14:30:15] ℹ Lookback window: 60 minutes
[2024-10-03 14:30:15] ℹ State file: .workflow-monitor-state.json

Check #1 - 2024-10-03 14:30:15
==============================

FAILURE DETECTED: myorg/myrepo
===============================
[2024-10-03 14:30:20] ✗ Workflow: CI Pipeline
[2024-10-03 14:30:20] ✗ Run ID: 12345
[2024-10-03 14:30:20] ✗ Title: Fix authentication issue
[2024-10-03 14:30:20] ✗ Branch: feature/auth-fix
[2024-10-03 14:30:20] ✗ Conclusion: failure
[2024-10-03 14:30:20] ✗ Event: push
[2024-10-03 14:30:20] ✗ Created: 2024-10-03T14:15:30Z
[2024-10-03 14:30:20] ✗ URL: https://github.com/myorg/myrepo/actions/runs/12345
[2024-10-03 14:30:20] ✗ 
Failed Jobs (2):
[2024-10-03 14:30:20] ✗   - Build and Test (failure)
[2024-10-03 14:30:20] ✗   - Lint Code (failure)

[2024-10-03 14:30:25] ℹ Checked 15 workflow runs
[2024-10-03 14:30:25] ✗ Found 1 new failures!
[2024-10-03 14:30:25] ℹ Next check in 60 seconds...
```

## State File

The monitor maintains a state file (default: `.workflow-monitor-state.json`) to track which workflow runs have been seen. This prevents duplicate alerts for the same failure.

The state file is a JSON file with the following structure:

```json
{
  "owner/repo:ci.yml:main": [12345, 12346, 12347],
  "owner/repo:deploy.yml:production": [98765, 98766]
}
```

Each key is in the format `repository:workflow:branch`, and the value is a list of run IDs that have been seen.

**Note**: Delete the state file if you want to re-alert on all failures within the lookback window.

## Error Handling

The script handles various error conditions gracefully:

- GitHub CLI not installed or not authenticated
- Invalid YAML configuration
- Network timeouts and API errors
- Missing or invalid repository names
- GitHub API rate limits

## Use Cases

### 1. Development Team Dashboard
Run the monitor continuously on a dedicated machine or server to track workflow failures across all your repositories.

### 2. CI/CD Integration
Use `--once` mode in a scheduled CI/CD job to check for failures and send notifications via your preferred alerting system.

### 3. Post-Deployment Monitoring
Monitor specific workflows on production branches to catch deployment failures immediately.

### 4. Quality Gate
Integrate into your development workflow to ensure critical workflows are passing before allowing merges.

## Limitations

- Requires GitHub CLI authentication
- Subject to GitHub API rate limits (monitor adjusts polling with exponential backoff)
- Only detects failures for runs within the lookback window
- Does not send external notifications (can be integrated with alerting tools)

## Integration with Alerting Systems

The monitor can be easily integrated with alerting systems:

### Slack/Discord/Teams
Parse the output and send webhook notifications when failures are detected.

### PagerDuty/OpsGenie
Run with `--once` and trigger incidents based on exit code.

### Email
Pipe output to an email command when failures occur.

Example wrapper script for Slack:

```bash
#!/bin/bash
output=$(python monitor_workflows.py config.yaml --once 2>&1)
if [ $? -ne 0 ]; then
  curl -X POST -H 'Content-type: application/json' \
    --data "{\"text\":\"Workflow Failures Detected:\n\`\`\`$output\`\`\`\"}" \
    YOUR_SLACK_WEBHOOK_URL
fi
```

## Troubleshooting

### "GitHub CLI is not authenticated"
Run `gh auth login` to authenticate with GitHub.

### No failures detected but I know there are failures
- Check the `lookback_minutes` setting - increase it to look further back
- Verify the `workflow` and `branch` filters if specified
- Delete the state file to reset seen runs

### Getting rate limited
- Increase `poll_interval` to check less frequently
- Reduce `max_runs_per_check` to fetch fewer runs
- The monitor will automatically back off when rate limits are hit

### State file growing too large
The state file tracks all seen runs. To clean it up:
1. Stop the monitor
2. Delete the state file
3. Restart the monitor

## License

This script is part of the core-architecture-gh-workflows repository.
