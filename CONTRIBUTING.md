# Contributing Guide

This document provides common patterns, troubleshooting tips, and best practices for working with scripts in this repository.

## Repository Structure

```
tmp-scripts/
├── docs/                           # Shared documentation
│   ├── GITHUB_SETUP.md            # GitHub authentication guide
│   ├── PYTHON_SETUP.md            # Python environment setup
│   └── SLACK_INTEGRATION.md       # Slack notifications guide
├── analyze-org-repos/             # Repository analysis tool
├── ghact-runner/                  # Local GitHub Actions runner
├── nullplatform-setup/            # Nullplatform automation
├── repository-mirrorer/           # Repository sync tool
├── slack-notifier/                # Slack notification SDK
├── workflow-monitor/              # GitHub workflow monitor
└── CONTRIBUTING.md                # This file
```

## Quick Start

1. **Set up Python environment**: See [docs/PYTHON_SETUP.md](docs/PYTHON_SETUP.md)
2. **Configure GitHub access**: See [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md)
3. **Optional - Enable Slack**: See [docs/SLACK_INTEGRATION.md](docs/SLACK_INTEGRATION.md)
4. **Navigate to script folder** and follow its README

## Common Setup Steps

### Environment Variables

Most scripts use environment variables for configuration:

```bash
# GitHub authentication
export GITHUB_TOKEN="ghp_your_token_here"

# Slack notifications (optional)
export SLACK_BOT_TOKEN="xoxb_your_token_here"
export SLACK_CHANNEL="C01234567"

# Dry-run mode (test without making changes)
export DRY_RUN=1
export SLACK_DRY_RUN=1
```

### Testing Before Running

Always test scripts in dry-run mode first:

```bash
# Most scripts support --dry-run flag
python script.py --config config.yaml --dry-run

# Or set environment variable
export DRY_RUN=1
python script.py --config config.yaml
```

### Verbose Output

Enable detailed logging for troubleshooting:

```bash
python script.py --config config.yaml --verbose
```

## Common Patterns

### YAML Configuration

Most scripts use YAML for configuration:

```yaml
# Common structure
setting: value
list_setting:
  - item1
  - item2
nested:
  key: value
```

Validate your YAML:
```bash
python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"
```

### Logging

Scripts provide colored, timestamped output:
- **Info** (Cyan): General progress
- **Success** (Green): Completed successfully
- **Warning** (Yellow): Non-critical issues
- **Error** (Red): Failures

### State Persistence

Some scripts maintain state files to avoid duplicate operations:
- Location: Usually `.{script-name}-state.json` in script folder
- Purpose: Track processed items, avoid re-alerting
- Reset: Delete state file to start fresh

## Common Troubleshooting

### Authentication Issues

**GitHub CLI not authenticated**:
```bash
gh auth status
gh auth login
```

**Token expired or invalid**:
- Regenerate token in GitHub settings
- Update `GITHUB_TOKEN` environment variable
- Verify token has required scopes

### Network/Proxy Issues

**SSL certificate errors**:
```bash
# Use custom CA bundle
export SSL_CERT_FILE=/path/to/ca-bundle.crt
export REQUESTS_CA_BUNDLE=/path/to/ca-bundle.crt
```

**Proxy configuration**:
```bash
export HTTPS_PROXY=http://proxy.company.com:8080
export HTTP_PROXY=http://proxy.company.com:8080
```

### Rate Limiting

**GitHub API rate limits**:
- Check: `gh api rate_limit`
- Wait for reset (typically 1 hour)
- Reduce polling frequency
- Use authenticated requests (5000/hr vs 60/hr)

**Slack API rate limits**:
- Tier 3: 50+ requests per minute
- Add delays between messages if needed
- Use threaded messages to reduce channel noise

### Dependency Issues

**Missing Python packages**:
```bash
pip install -r requirements.txt
```

**Version conflicts**:
```bash
# Use virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration Errors

**YAML parsing errors**:
- Check indentation (use spaces, not tabs)
- Validate with: `python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"`
- Check for special characters that need quoting

**Missing required fields**:
- Review example config files (`*.example.yaml`)
- Check script documentation for required fields

### File Permissions

**Permission denied errors**:
```bash
# Make script executable
chmod +x script.sh

# Fix file ownership
sudo chown $USER:$USER file

# Install to user directory
pip install --user package
```

## Best Practices

### 1. Version Control

- **Never commit tokens or secrets**
- Add sensitive files to `.gitignore`:
  ```
  .env
  *.token
  *-state.json
  config.yaml  # If it contains secrets
  ```
- Use example configs: `config.example.yaml`

### 2. Configuration Management

- Use environment variables for secrets
- Use config files for non-sensitive settings
- Document all configuration options
- Provide example configurations

### 3. Error Handling

- Always check exit codes: `if [ $? -ne 0 ]; then ...`
- Use `--dry-run` before production runs
- Enable `--verbose` when troubleshooting
- Monitor logs for warnings

### 4. Security

- Rotate tokens regularly
- Use minimum required permissions
- Store secrets in secure vaults (GitHub Secrets, Vault, etc.)
- Limit token scopes to necessary operations
- Use separate tokens for dev/staging/prod

### 5. Testing

- Test in non-production environment first
- Validate with small datasets before full runs
- Use `--once` mode for cron jobs
- Monitor first few executions closely

## Script Execution Modes

### One-Time Execution
```bash
# Run once and exit
python script.py --config config.yaml
```

### Continuous Monitoring
```bash
# Run indefinitely with polling
python script.py --config config.yaml  # No --once flag
```

### Scheduled (Cron)
```bash
# Add to crontab
*/15 * * * * cd /path/to/script && python script.py --config config.yaml --once >> logs/cron.log 2>&1
```

### Background Process
```bash
# Run in background with nohup
nohup python script.py --config config.yaml > script.log 2>&1 &

# Check process
ps aux | grep script.py

# Kill process
kill $(ps aux | grep script.py | grep -v grep | awk '{print $2}')
```

## Getting Help

1. **Check script README**: Each script has detailed documentation
2. **Review shared docs**: See `docs/` directory
3. **Enable verbose mode**: `--verbose` flag shows detailed output
4. **Check state files**: May reveal what was processed
5. **Review logs**: Most scripts create timestamped log files
6. **Validate configuration**: Test with `--dry-run`

## Making Changes

### Adding Features

1. Test changes locally with dry-run mode
2. Update script documentation
3. Add examples if introducing new options
4. Test error handling

### Updating Dependencies

1. Update `requirements.txt`
2. Test in clean virtual environment
3. Document breaking changes
4. Update installation instructions if needed

### Creating New Scripts

Follow existing patterns:
- Support `--config`, `--dry-run`, `--verbose` flags
- Use YAML for configuration
- Provide example config file
- Include detailed README
- Support environment variables
- Add Slack integration (optional)
- Handle errors gracefully
- Log progress with timestamps

## Related Documentation

- [Python Setup Guide](docs/PYTHON_SETUP.md)
- [GitHub Setup Guide](docs/GITHUB_SETUP.md)
- [Slack Integration Guide](docs/SLACK_INTEGRATION.md)

## Support

For script-specific issues, see the README in each script's folder.
