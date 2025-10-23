# GitHub Workflow Monitor

Continuously monitor GitHub workflow runs across multiple repositories and detect failures in real-time.

## Overview

`monitor_workflows.py` tracks GitHub Actions workflow runs across multiple repositories, detects new failures, and reports them with detailed information. Unlike tools that *trigger* workflows, this script *monitors* existing workflow runs.

### Key Features

- Continuous monitoring with configurable polling intervals
- Single-check mode for one-time monitoring (great for cron jobs)
- Track multiple repositories and workflows
- Filter by specific workflows and/or branches
- Detect and report new failures with job details
- State persistence to avoid duplicate alerts
- Colored console output with timestamps
- Configurable lookback window

## Quick Start

### Prerequisites

- Python 3.6+
- GitHub CLI (`gh`) - [Setup guide](../docs/GITHUB_SETUP.md)
- PyYAML: `pip install PyYAML`

See [Python Setup Guide](../docs/PYTHON_SETUP.md) for virtual environment setup.

### Installation

```bash
# Install GitHub CLI
brew install gh  # macOS
# OR see: https://cli.github.com/manual/installation

# Authenticate
gh auth login

# Install Python dependencies
pip install PyYAML
```

### Quick Test

```bash
# Check prerequisites
./quickstart.sh

# Run a single check
python3 monitor_workflows.py test-config.yaml --once

# Monitor continuously
python3 monitor_workflows.py test-config.yaml
```

## Configuration

Create a YAML configuration file:

```yaml
# Global settings
poll_interval: 60           # Seconds between checks (continuous mode)
lookback_minutes: 60        # How far back to look for workflow runs
max_runs_per_check: 100     # Max runs to check per repository

# Repositories to monitor
repositories:
  # Monitor all workflows on all branches
  - repository: "owner/repo-name"

  # Monitor specific workflow
  - repository: "owner/another-repo"
    workflow: "ci.yml"

  # Monitor specific workflow on specific branch
  - repository: "owner/myrepo"
    workflow: "deploy.yml"
    branch: "main"
```

See `config.example.yaml` for detailed examples.

## Usage

### Continuous Monitoring

Monitor continuously, checking for failures at regular intervals:

```bash
python monitor_workflows.py config.yaml
```

Runs indefinitely, checking every `poll_interval` seconds. Press Ctrl+C to stop.

### Single Check

Run once and exit (useful for cron jobs or CI/CD):

```bash
python monitor_workflows.py config.yaml --once
```

Exit codes:
- `0`: No new failures detected
- `1`: New failures detected

### Custom State File

```bash
python monitor_workflows.py config.yaml --state-file /tmp/my-state.json
```

## How It Works

1. **Configuration Loading**: Reads and validates YAML config
2. **State Management**: Loads previously seen workflow runs
3. **Polling Loop** (continuous mode):
   - For each repository:
     - Fetches recent workflow runs using GitHub CLI
     - Filters runs within lookback window
     - Checks for completed runs with failure status
     - Identifies new failures not seen before
     - Reports detailed failure information
   - Updates state file with newly seen runs
   - Waits for poll interval before next check
4. **Failure Reporting**: When detected:
   - Displays workflow details (name, branch, run ID, URL)
   - Lists all failed jobs with conclusions
   - Marks run as seen to avoid duplicate alerts

## Example Output

```
Workflow Monitor Starting
=========================
[2024-10-23 14:30:15] ℹ Repositories to monitor: 3
[2024-10-23 14:30:15] ℹ Poll interval: 60 seconds
[2024-10-23 14:30:15] ℹ Lookback window: 60 minutes

Check #1 - 2024-10-23 14:30:15
==============================

FAILURE DETECTED: myorg/myrepo
===============================
[2024-10-23 14:30:20] ✗ Workflow: CI Pipeline
[2024-10-23 14:30:20] ✗ Run ID: 12345
[2024-10-23 14:30:20] ✗ Branch: feature/auth-fix
[2024-10-23 14:30:20] ✗ URL: https://github.com/myorg/myrepo/actions/runs/12345

Failed Jobs (2):
  - Build and Test (failure)
  - Lint Code (failure)

[2024-10-23 14:30:25] ✗ Found 1 new failures!
[2024-10-23 14:30:25] ℹ Next check in 60 seconds...
```

## State Management

The monitor maintains a state file (`.workflow-monitor-state.json`) to track seen workflow runs and prevent duplicate alerts.

Format:
```json
{
  "owner/repo:ci.yml:main": [12345, 12346, 12347],
  "owner/repo:deploy.yml:production": [98765, 98766]
}
```

**Reset alerts**: Delete the state file to re-alert on all failures within lookback window.

## Use Cases

### 1. Development Dashboard
Run continuously on a server to track all workflow failures in real-time:
```bash
nohup python3 monitor_workflows.py config.yaml > monitor.log 2>&1 &
```

### 2. Cron Job for Alerts
Check periodically and integrate with alerting:
```bash
# Add to crontab (every 5 minutes)
*/5 * * * * cd /path/to/workflow-monitor && python3 monitor_workflows.py config.yaml --once
```

### 3. Quality Gate
Monitor critical workflows before deployments:
```yaml
repositories:
  - repository: "myorg/prod-service"
    workflow: "deploy.yml"
    branch: "production"
```

## Common Issues

### "No repositories found"
- Verify repository name format: `owner/repo`
- Check GitHub CLI authentication: `gh auth status`

### No failures showing but I know there are some
- Increase `lookback_minutes` in config
- Delete state file to reset: `rm .workflow-monitor-state.json`
- Verify `workflow` and `branch` filters

### Getting rate limited
- Increase `poll_interval` to check less frequently
- Reduce `max_runs_per_check`
- The monitor will automatically back off when rate limits are hit

### State file growing too large
Delete the state file and restart the monitor.

For more troubleshooting, see [Contributing Guide](../CONTRIBUTING.md).

## Integration with Alerting

Example wrapper for Slack:

```bash
#!/bin/bash
output=$(python monitor_workflows.py config.yaml --once 2>&1)
if [ $? -ne 0 ]; then
  curl -X POST -H 'Content-type: application/json' \
    --data "{\"text\":\"Workflow Failures:\n\`\`\`$output\`\`\`\"}" \
    YOUR_SLACK_WEBHOOK_URL
fi
```

Can also integrate with PagerDuty, OpsGenie, email, etc.

## Configuration Options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `poll_interval` | integer | 60 | Seconds between checks (continuous mode) |
| `lookback_minutes` | integer | 60 | How many minutes back to check |
| `max_runs_per_check` | integer | 100 | Max runs to fetch per repository |
| `repositories` | array | - | List of repository configurations |
| `repositories[].repository` | string | - | Repository in "owner/name" format |
| `repositories[].workflow` | string | - | Workflow file name filter (optional) |
| `repositories[].branch` | string | - | Branch name filter (optional) |

## Tips

1. **Start with --once**: Test your config before running continuously
2. **Tune poll_interval**: Balance responsiveness vs API rate limits
3. **Use lookback_minutes wisely**: Too small = miss failures, too large = more API calls
4. **Monitor specific branches**: Focus on important branches to reduce noise
5. **Integrate with alerts**: Pipe output to notification systems

## Related Documentation

- [GitHub Setup Guide](../docs/GITHUB_SETUP.md) - GitHub CLI authentication
- [Python Setup Guide](../docs/PYTHON_SETUP.md) - Python environment setup
- [Contributing Guide](../CONTRIBUTING.md) - Common patterns and troubleshooting
