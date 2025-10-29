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

### Prerequisites

Before using the script, you must have an existing namespace. Create one if needed:

```bash
# Create a namespace
np namespace create --body '{"name":"my-namespace"}'

# List existing namespaces
np namespace list --format json
```

### Configuration File

Create `nullplatform-setup.yaml` with nested structure:

```yaml
applications:
  - name: "my-web-app"
    namespace: "my-namespace"  # Reference existing namespace by name

    scopes:
      - name: "development"
      - name: "staging"
      - name: "production"

    parameters:
      - name: "DATABASE_URL"
        value: "postgres://localhost:5432/mydb"

      - name: "LOG_LEVEL"
        scope: "development"  # Scope-specific value
        value: "debug"

      - name: "LOG_LEVEL"
        scope: "production"
        value: "error"

  - name: "my-api-service"
    namespace: "my-namespace"
    repository:
      url: "https://github.com/my-org/my-api-service"

    scopes:
      - name: "development"
      - name: "production"

    parameters:
      - name: "API_KEY"
        value: "sk-xxxxx"
```

### Key Features

**Nested Structure**: Each application contains its own scopes and parameters - no manual ID management needed.

**Automatic ID Resolution**:
- Namespace names are resolved to IDs automatically
- Application IDs are captured and used for nested resources
- Scope references in parameters are resolved by name

**Repository Connection**: Optionally connect applications to Git repositories:
```yaml
applications:
  - name: "my-app"
    namespace: "my-namespace"
    repository:
      url: "https://github.com/my-org/my-app"
```

The `repository` field associates your application with a Git repository, enabling:
- Automatic CI/CD integration
- Source code tracking
- Repository-based deployments

**Note**: The repository must already exist in your Git provider.

### Scope-Specific Parameters

Parameters can be application-level or scope-specific:

```yaml
parameters:
  # Application-level (applies to all scopes)
  - name: "FEATURE_FLAGS"
    value: "new-ui,analytics"

  # Scope-specific (different values per environment)
  - name: "API_URL"
    scope: "development"
    value: "https://api-dev.example.com"

  - name: "API_URL"
    scope: "production"
    value: "https://api.example.com"
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

### Nested Structure Workflow

For each application in your config:

1. **Resolve Namespace**: Looks up namespace ID by name from existing namespaces
2. **Create Application**: Creates the application in the resolved namespace
3. **Create Scopes**: Creates all scopes for this application using the application ID
4. **Create Parameters**: Creates all parameters, resolving scope references by name

Everything happens in one run - no manual ID copying between steps!

### Automatic ID Resolution

**Namespaces**: Referenced by name, resolved to ID via API lookup
```yaml
namespace: "my-namespace"  # Script looks up: ns-abc123
```

**Scopes**: Referenced by name within parameters
```yaml
parameters:
  - name: "API_URL"
    scope: "development"  # Script uses scope ID from earlier creation
```

**Applications**: IDs captured automatically and used for nested resources

### Error Handling

- **Namespace not found**: Shows available namespaces, provides creation command
- **Application creation fails**: Skips its scopes and parameters, continues with next app
- **Scope not found**: Warns but continues (parameter becomes application-level)
- **Resource already exists**: Logs warning, attempts to retrieve ID, continues

## Example Output

```
2025-10-29 16:30:00 - INFO - Loading configuration from nullplatform-setup.yaml
2025-10-29 16:30:00 - INFO - Config loaded: 2 applications
2025-10-29 16:30:01 - INFO - Processing application: my-web-app
2025-10-29 16:30:01 - INFO - Creating application: my-web-app
2025-10-29 16:30:02 - INFO - ✓ Created application: my-web-app (ID: app-abc123)
2025-10-29 16:30:02 - INFO - Creating scope: development
2025-10-29 16:30:03 - INFO - ✓ Created scope: development (ID: scope-def456)
2025-10-29 16:30:03 - INFO - Creating parameter: DATABASE_URL
2025-10-29 16:30:04 - INFO - ✓ Created parameter: DATABASE_URL (ID: param-ghi789)
2025-10-29 16:30:04 - INFO - Processing application: my-api-service
...

============================================================
SETUP SUMMARY
============================================================
Total resources: 8
Created:         8
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

### "Namespace 'my-namespace' not found"
The namespace must exist before running the script. Either:
1. Create it: `np namespace create --body '{"name":"my-namespace"}'`
2. Check existing: `np namespace list --format json`
3. Use a different namespace name in your config

### "Scope 'development' not found for parameter"
This warning means the parameter references a scope that wasn't created. Check:
1. Scope name is spelled correctly in both places
2. Scope was successfully created (check logs for errors)
3. Scope and parameter are under the same application

### JSON parsing errors
Run with `--verbose` to see full command output and identify issues.

For more troubleshooting, see [Contributing Guide](../CONTRIBUTING.md).

## Advanced Usage

### Custom np CLI Path

```bash
python nullplatform-setup.py --np-path ~/.local/bin/np
```

### Incremental Setup

The script is idempotent - you can run it multiple times safely:

1. Existing resources are detected and skipped
2. New resources in config are created
3. Useful for gradually building out your infrastructure

Example workflow:
```bash
# First run: Create applications with basic scopes
python nullplatform-setup.py

# Later: Add more parameters to config
# Edit nullplatform-setup.yaml to add parameters

# Second run: Creates new parameters, skips existing resources
python nullplatform-setup.py
```

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

1. **Always use dry-run first**: `--dry-run` to preview before creating resources
2. **Version control your config**: Commit YAML files (without secrets) to git
3. **Use environment variables for secrets**: Never hardcode API keys or sensitive values
4. **Create namespace first**: Ensure namespace exists before running script
5. **Group related resources**: Keep applications and their resources together in config
6. **Use meaningful names**: Clear application and scope names help with debugging
7. **Test in non-production**: Create a test namespace to validate config first
8. **Start simple**: Begin with basic config, then add complexity gradually

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
