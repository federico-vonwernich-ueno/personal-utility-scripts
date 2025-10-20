# Workflow Monitor - Overview

## What is it?

The Workflow Monitor is a Python script that continuously monitors GitHub workflow runs across multiple repositories and alerts you when failures occur. Unlike the workflow-runner script that *triggers* workflows, this script *monitors* existing workflow runs.

## Key Features

- **Real-time Monitoring**: Continuously checks for workflow failures at configurable intervals
- **Smart Alerting**: Only alerts on NEW failures (uses state file to avoid duplicate alerts)
- **Flexible Filtering**: Monitor all workflows, specific workflows, or specific branches
- **Detailed Reporting**: Shows failed job details, run URLs, and timestamps
- **Multiple Modes**: Run continuously or as a one-time check (great for cron jobs)

## Quick Start

1. **Check Prerequisites**:
   ```bash
   ./quickstart.sh
   ```

2. **Run a Test** (single check):
   ```bash
   python3 monitor_workflows.py test-config.yaml --once
   ```

3. **Monitor Continuously**:
   ```bash
   python3 monitor_workflows.py test-config.yaml
   ```

## Configuration

Edit `test-config.yaml` or create your own config file:

```yaml
poll_interval: 60        # Check every 60 seconds
lookback_minutes: 60     # Look back 60 minutes for runs
max_runs_per_check: 100  # Max runs to check per repo

repositories:
  - repository: "owner/repo"
    workflow: "ci.yml"     # Optional: specific workflow
    branch: "main"         # Optional: specific branch
```

## Use Cases

### 1. Development Dashboard
Run the monitor continuously on a server to track all workflow failures in real-time:
```bash
nohup python3 monitor_workflows.py config.yaml > monitor.log 2>&1 &
```

### 2. Cron Job for Alerts
Check periodically and integrate with your alerting system:
```bash
# Add to crontab to check every 5 minutes
*/5 * * * * cd /path/to/workflow-monitor && python3 monitor_workflows.py config.yaml --once
```

### 3. CI/CD Integration
Use in your CI/CD pipeline to verify workflow health:
```bash
# Exit code 0 = no failures, 1 = failures detected
python3 monitor_workflows.py config.yaml --once
if [ $? -ne 0 ]; then
    echo "Workflow failures detected!"
    # Send notification, create ticket, etc.
fi
```

### 4. Quality Gate
Monitor critical workflows before allowing deployments:
```yaml
repositories:
  - repository: "myorg/prod-service"
    workflow: "deploy.yml"
    branch: "production"
```

## Comparison with Other Scripts

| Script | Purpose | Mode |
|--------|---------|------|
| `ghact-runner` | Run workflows locally with `act` | One-time execution |
| `workflow-runner` | Trigger workflows on GitHub | Active triggering |
| **`workflow-monitor`** | **Monitor for failures** | **Passive monitoring** |

## Files

- `monitor_workflows.py` - Main monitoring script
- `config.example.yaml` - Full configuration example with comments
- `test-config.yaml` - Test configuration for quick trials
- `README.md` - Detailed documentation
- `quickstart.sh` - Interactive setup and test script

## Output Example

When a failure is detected:

```
FAILURE DETECTED: myorg/myrepo
===============================
[2024-10-07 15:30:20] ✗ Workflow: CI Pipeline
[2024-10-07 15:30:20] ✗ Run ID: 12345
[2024-10-07 15:30:20] ✗ Branch: feature/new-feature
[2024-10-07 15:30:20] ✗ Conclusion: failure
[2024-10-07 15:30:20] ✗ URL: https://github.com/myorg/myrepo/actions/runs/12345

Failed Jobs (2):
  - Build and Test (failure)
  - Lint Code (failure)
```

## State Management

The monitor keeps track of seen runs in `.workflow-monitor-state.json` to avoid duplicate alerts. 

**To reset alerts**: Delete the state file and restart the monitor.

## Tips

1. **Start with --once**: Test your configuration with `--once` before running continuously
2. **Tune poll_interval**: Balance between responsiveness and API rate limits
3. **Use lookback_minutes wisely**: Too small = miss failures, too large = more API calls
4. **Monitor specific branches**: Focus on important branches (main, production) to reduce noise
5. **Integrate with alerts**: Pipe output to Slack, email, or PagerDuty for instant notifications

## Troubleshooting

**No failures showing but I know there are some:**
- Increase `lookback_minutes` in config
- Delete the state file to reset
- Verify `workflow` and `branch` filters

**Getting rate limited:**
- Increase `poll_interval`
- Reduce `max_runs_per_check`
- Monitor fewer repositories

**Want to see all failures again:**
```bash
rm .workflow-monitor-state.json
python3 monitor_workflows.py config.yaml --once
```

## Next Steps

1. Copy `config.example.yaml` to create your own configuration
2. Add your repositories and workflows
3. Test with `--once` mode
4. Deploy for continuous monitoring or schedule with cron
5. Integrate with your alerting/notification system

For detailed documentation, see [README.md](README.md)
