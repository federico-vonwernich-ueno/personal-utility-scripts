# Nullplatform Setup Script

Automate the creation of applications, parameters, and scopes in nullplatform using the `np` CLI based on a YAML configuration file.

## Overview

`nullplatform-setup.py` is a config-driven automation tool that creates nullplatform resources (namespaces, applications, scopes, parameters) with automatic ID tracking, dry-run mode, and optional Slack notifications.

### Key Features

- Config-driven setup (define all resources in YAML)
- Automated resource creation with dependency handling
- ID tracking (automatically tracks created resource IDs)
- Dry-run mode for preview before applying
- Verbose logging for troubleshooting
- JSON-based communication with `np` CLI
- Slack integration for progress updates

## Quick Start

### Prerequisites

**1. Nullplatform CLI**

```bash
curl https://cli.nullplatform.com/install.sh | sh
np --version
```

**2. Python Dependencies**

```bash
pip install PyYAML
```

See [Python Setup Guide](../docs/PYTHON_SETUP.md) for virtual environment setup.

**3. Nullplatform API Key**

Create an API key in nullplatform:
1. Log in to your nullplatform account
2. Navigate to Settings → API Keys
3. Create and copy the API key

### Installation

```bash
# Make script executable
chmod +x nullplatform-setup.py
```

## Configuration

Create `nullplatform-setup.yaml`:

```yaml
# Optional: Create or use existing namespace
namespace:
  name: "my-namespace"

# Applications to create
applications:
  - name: "my-web-app"
    namespace_id: "ns-xxxxx"

  # Application with repository connection
  - name: "my-api-service"
    namespace_id: "ns-xxxxx"
    repository:
      url: "https://github.com/my-org/my-api-service"

# Scopes (environments)
scopes:
  - name: "development"
    application_id: "app-xxxxx"

  - name: "production"
    application_id: "app-xxxxx"

# Parameters (environment variables, config values)
parameters:
  - name: "DATABASE_URL"
    application_id: "app-xxxxx"
    value: "postgres://localhost:5432/mydb"

  - name: "API_KEY"
    application_id: "app-xxxxx"
    value: "sk-xxxxx"
    scope_id: "scope-xxxxx"  # Optional: scope-specific value
```

### Connecting Repositories

You can optionally connect applications to existing Git repositories:

```yaml
applications:
  - name: "my-app"
    namespace_id: "ns-xxxxx"
    repository:
      url: "https://github.com/my-org/my-app"
```

The `repository` field tells Nullplatform to associate your application with the specified Git repository. This enables:
- Automatic CI/CD integration
- Source code tracking
- Repository-based deployments

**Note**: The repository must already exist in your Git provider (GitHub, GitLab, etc.). The script creates the association in Nullplatform but does not create the repository itself.

### Resource IDs

You'll need IDs for parent resources. Run the script incrementally:

1. Create namespace first → Note the namespace ID
2. Update config with namespace ID → Create applications
3. Note application IDs → Create scopes and parameters

Get existing IDs:
```bash
np namespace list --format json
np application list --format json
np scope list --format json
```

## Usage

### Set API Key

```bash
export NULLPLATFORM_API_KEY="your-api-key-here"
```

Or pass via CLI:
```bash
python nullplatform-setup.py --api-key "your-api-key-here"
```

### Basic Usage

```bash
# Default config file (nullplatform-setup.yaml)
python nullplatform-setup.py

# Specify config file
python nullplatform-setup.py --config my-config.yaml
```

### Dry Run

Preview changes without creating resources:

```bash
python nullplatform-setup.py --dry-run
```

### Verbose Output

```bash
python nullplatform-setup.py --verbose
```

### Combine Options

```bash
python nullplatform-setup.py --config my-config.yaml --dry-run --verbose
```

### With Slack Notifications

```bash
export SLACK_BOT_TOKEN="xoxb-your-token"
export SLACK_CHANNEL="#nullplatform-setup"
python nullplatform-setup.py --config my-config.yaml
```

See [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) for detailed setup.

## How It Works

### Creation Order

Resources are created in dependency order:

1. **Namespace** (if specified)
2. **Applications** (require namespace ID)
3. **Scopes** (require application ID)
4. **Parameters** (require application ID, optionally scope ID)

### ID Tracking

The script automatically:
- Captures resource IDs from creation responses
- Stores them for use in dependent resources
- Can reference IDs from earlier in the same run

### Error Handling

- **Resource already exists**: Logs warning, continues
- **Creation error**: Logs details, continues with next resource
- **Missing dependencies**: Fails with clear error message
- **API errors**: Shows full error from nullplatform

## Example Output

```
2025-10-23 16:30:00 - INFO - Loading configuration from nullplatform-setup.yaml
2025-10-23 16:30:00 - INFO - Config loaded: 2 apps, 3 parameters, 2 scopes
2025-10-23 16:30:01 - INFO - Creating namespace: my-namespace
2025-10-23 16:30:02 - INFO - ✓ Created namespace: my-namespace (ID: ns-abc123)
2025-10-23 16:30:02 - INFO - Creating application: my-web-app
2025-10-23 16:30:03 - INFO - ✓ Created application: my-web-app (ID: app-def456)

============================================================
SETUP SUMMARY
============================================================
Total resources: 7
Created:         7
Already exists:  0
Errors:          0
============================================================
```

## Common Issues

### "np: command not found"
Install the nullplatform CLI:
```bash
curl https://cli.nullplatform.com/install.sh | sh
```

If installed but not in PATH, use `--np-path`:
```bash
python nullplatform-setup.py --np-path /path/to/np
```

### "API key required"
Set the `NULLPLATFORM_API_KEY` environment variable or pass via `--api-key`.

### "Missing required field: namespace_id"
Applications require a namespace_id. Either:
1. Create a namespace first and use its ID
2. Get an existing namespace ID: `np namespace list --format json`

### JSON parsing errors
Run with `--verbose` to see full command output and identify issues.

For more troubleshooting, see [Contributing Guide](../CONTRIBUTING.md).

## Advanced Usage

### Custom np CLI Path

```bash
python nullplatform-setup.py --np-path ~/.local/bin/np
```

### Incremental Setup

Run the script multiple times. It will skip resources that already exist:

1. First run: Create namespace and applications
2. Update config with created IDs
3. Second run: Create scopes and parameters

## Slack Notifications

The script supports threaded Slack notifications for:
- Setup start with configuration details
- Real-time progress updates for each resource
- Summary with statistics and resource breakdown

Setup:
1. Install dependencies: `pip install slack-sdk urllib3`
2. Configure environment variables (see [Slack Integration Guide](../docs/SLACK_INTEGRATION.md))
3. Run script normally - notifications sent automatically

### Custom Templates

This script uses custom Slack notification templates located in `templates/`:
- `nullplatform_setup_start.json` - Setup start notification
- `nullplatform_setup_progress.json` - Resource creation progress
- `nullplatform_setup_summary.json` - Final summary

These templates are automatically used when Slack notifications are enabled.

## Best Practices

1. Use dry-run first: `--dry-run` before creating resources
2. Version control your config (without secrets)
3. Use secrets management for API keys and parameter values
4. Create resources in stages to manage dependencies
5. Document resource IDs for future reference
6. Test in non-production environment first

## Security Notes

- Never commit API keys to version control
- Use environment variables for sensitive values
- Mark parameters as secrets when they contain sensitive data
- Rotate API keys regularly
- Use separate API keys for different environments

## Related Documentation

- [Python Setup Guide](../docs/PYTHON_SETUP.md) - Python environment setup
- [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) - Slack notifications
- [Contributing Guide](../CONTRIBUTING.md) - Common patterns and troubleshooting
- [Nullplatform CLI Docs](https://docs.nullplatform.com/docs/cli/)
- [Nullplatform API Docs](https://docs.nullplatform.com/docs/api-getting-started)
