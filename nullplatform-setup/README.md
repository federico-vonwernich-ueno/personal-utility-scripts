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
- Slack integration for completion summary

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

### Required Information

Before creating your config file, gather these IDs:

**1. Organization ID (required)**
```bash
# Get your organization ID
np organization list --format json
```

**2. Account ID (required)**
```bash
# Get your account ID
np account list --format json
```

**3. Existing Namespace**
```bash
# Create a namespace
np namespace create --body '{"name":"my-namespace"}'

# List existing namespaces
np namespace list --format json
```

### Configuration File

Create `nullplatform-setup.yaml`:

```yaml
# Organization ID (required)
organization_id: "your-organization-id-here"

# Account ID (required)
account_id: "your-account-id-here"

applications:
  - name: "my-web-app"
    namespace: "my-namespace"  # Reference existing namespace by name
    repository_url: "https://github.com/my-org/my-web-app"

    scopes:
      - name: "development"
      - name: "production"

    parameters:
      - name: "DATABASE_URL"
        value: "postgres://localhost:5432/mydb"

      - name: "API_KEY"
        value: "sk-xxxxx"
        secret: true
```

For more detailed examples and all available configuration options, see `nullplatform-setup.example.yaml`.

### Key Features

**Nested Structure**: Each application contains its own scopes and parameters - no manual ID management needed.

**Automatic ID Resolution**:
- Namespace names are resolved to IDs automatically
- Application IDs are captured and used for nested resources
- Scope references in parameters are resolved by name

**Repository Connection**: Connect applications to Git repositories:
```yaml
applications:
  - name: "my-app"
    namespace: "my-namespace"
    repository_url: "https://github.com/my-org/my-app"  # Required
```

The `repository_url` field (required) associates your application with a Git repository, enabling automatic CI/CD integration, source code tracking, and repository-based deployments. The repository must already exist in your Git provider.

### Scope Configuration

Scopes define deployment environments (development, staging, production) with their own resource allocations and capabilities. The script uses a **template-based approach** with reasonable defaults that you can override.

**Minimal Configuration** (uses all defaults):
```yaml
scopes:
  - name: "development"
  - name: "production"
```

**Custom Configuration** (override specific fields):
```yaml
scopes:
  - name: "production"
    type: "web_pool"
    dimensions:
      environment: "prod"
      region: "sae-1"

    # Resource specifications
    requested_spec:
      cpu_profile: "standard"          # CPU profile type
      memory_in_gb: 4                  # Memory allocation
      local_storage_in_gb: 50          # Storage allocation

    # Capabilities (override defaults as needed)
    capabilities:
      continuous_delivery:
        enabled: true

      auto_scaling:
        enabled: true
        cpu:
          min_percentage: 30
          max_percentage: 70
        instances:
          min_amount: 2
          max_amount: 10

      health_check:
        type: "http"
        path: "/health"
        configuration:
          timeout: 5
          interval: 10
```

**Key Features:**
- **Template-based defaults**: All scopes get reasonable default capabilities automatically
- **Selective overrides**: Only specify what you want to change from defaults
- **Deep merging**: Your custom capabilities merge with defaults (not replace)
- **Validation**: Configuration is validated before sending to API

**Available Capabilities:**
- `continuous_delivery`: Enable/disable CI/CD integration
- `logs`: Logging configuration (provider, throttling)
- `metrics`: Metrics providers (CloudWatch, Prometheus, etc.)
- `spot_instances`: Cost optimization with spot instances
- `auto_scaling`: CPU and instance-based auto-scaling
- `memory`: Memory allocation
- `storage`: Storage allocation
- `processor`: Processor type and configuration
- `visibility`: Network reachability settings
- `health_check`: Health check endpoint and configuration
- `scheduled_stop`: Automatic scope shutdown for cost savings

**Default Values:**
The script provides sensible defaults for all capabilities:
- `continuous_delivery.enabled`: false
- `logs.provider`: "none"
- `auto_scaling.enabled`: false
- `memory.memory_in_gb`: 1
- `storage.storage_in_gb`: 8
- `health_check.path`: "/health"
- And more (see `DEFAULT_SCOPE_CAPABILITIES` in script)

See `nullplatform-setup.example.yaml` for comprehensive examples showing minimal, basic, and advanced scope configurations.

### Scope-Specific Parameters

Parameters can be application-level (applies to all scopes) or scope-specific (different values per environment). Use the `scope` field to target specific scopes. See `nullplatform-setup.example.yaml` for detailed examples of parameter types, encoding options, and scope targeting.

### Parameter Dimensions

Parameters support **dimensions** for advanced multi-dimensional configurations. Dimensions allow you to have different parameter values based on custom criteria like environment, region, datacenter, etc.

**Key Features:**
- Define custom dimension keys (e.g., `environment`, `region`, `country`)
- Set different values for each dimension combination
- **Requirement:** Dimensions must use application-level parameters (cannot combine with `scope` field)

**Example use cases:**
- Multi-region deployments with region-specific endpoints
- Environment-specific configurations (dev, staging, production)
- Country-specific settings for compliance or localization
- Custom dimension combinations for complex deployment strategies

**Configuration:**
```yaml
parameters:
  - name: "API_ENDPOINT"
    value: "https://api.dev.us-east-1.example.com"
    dimensions:
      environment: "development"
      region: "us-east-1"

  - name: "API_ENDPOINT"
    value: "https://api.prod.eu-west-1.example.com"
    dimensions:
      environment: "production"
      region: "eu-west-1"
```

See `nullplatform-setup.example.yaml` for more examples.

### Multiple Values Per Parameter

A single parameter can have **multiple values** targeting different scopes or dimensions. This allows you to define a parameter once and provide different values for different environments, regions, or dimension combinations.

**Two Syntax Options:**

1. **Single Value** (simple case):
```yaml
parameters:
  - name: "DATABASE_URL"
    value: "postgres://localhost:5432/mydb"
```

2. **Multiple Values** (advanced case):
```yaml
parameters:
  - name: "LOG_LEVEL"
    type: "environment"
    values:
      - value: "debug"
        scope: "development"

      - value: "info"
        scope: "staging"

      - value: "warn"
        scope: "production"
```

**Key Features:**

- **Define once, target many**: Single parameter definition with multiple values
- **Scope targeting**: Each value can target a specific scope
- **Dimension targeting**: Each value can target dimension combinations
- **Application-level fallback**: Values without scope/dimensions apply to entire application
- **Precedence order**: Scope-specific > Dimension-specific > Application-level

**Scope-Specific Values:**
```yaml
parameters:
  - name: "LOG_LEVEL"
    values:
      - value: "debug"
        scope: "development"
      - value: "info"
        scope: "production"
      - value: "info"  # Fallback for any scope not explicitly configured
```

**Dimension-Based Values:**
```yaml
parameters:
  - name: "MAX_CONNECTIONS"
    values:
      - value: "100"
        dimensions:
          environment: "development"
          region: "us-east-1"

      - value: "500"
        dimensions:
          environment: "production"
          region: "us-east-1"

      - value: "500"
        dimensions:
          environment: "production"
          region: "eu-west-1"
```

**Important Notes:**

- Cannot combine `scope` and `dimensions` in the same value
- Dimensions require application-level parameters (no scope targeting)
- Each value in the `values` array is created independently
- Use single `value` field for simple cases, `values` array for complex scenarios

See `nullplatform-setup.example.yaml` for comprehensive examples.

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
- **Invalid scope capabilities**: Validates scope capabilities structure before API call, fails fast with clear error message
- **None/invalid entries**: Skips None or invalid dict entries in scopes/parameters lists with warnings, continues processing valid entries

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

The script sends a single summary notification upon completion with:
- Total resources processed (created, existing, errors)
- Duration of the setup
- Breakdown by resource type
- Error details if any failures occurred

Setup:
1. Install dependencies: `pip install slack-sdk urllib3`
2. Configure environment variables (see [Slack Integration Guide](../docs/SLACK_INTEGRATION.md))
3. Run script normally - notification sent automatically at completion

The script uses a custom Slack notification template located at `templates/nullplatform_setup_summary.json`.

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

## Security Features

Sensitive data is automatically redacted in all logs (debug, dry-run, errors), making logs safe to share for troubleshooting.

**What's Redacted:**
- API keys → `[REDACTED]`
- Secret parameter values (when `secret: true`)
- Sensitive field names: `api_key`, `token`, `password`, `credential`

**Example:**
```yaml
parameters:
  - name: "DB_PASSWORD"
    value: "secret123"
    secret: true
```

Logs show `"value": "[REDACTED]"` while API requests contain the actual value.

**Best Practices:**
- Mark all sensitive parameters with `secret: true`
- Use environment variables for API keys
- Review logs before sharing (despite redaction)
- Rotate keys immediately if exposed

## Related Documentation

- [Python Setup Guide](../docs/PYTHON_SETUP.md) - Python environment setup
- [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) - Slack notifications
- [Contributing Guide](../CONTRIBUTING.md) - Common patterns and troubleshooting
- [Nullplatform CLI Docs](https://docs.nullplatform.com/docs/cli/)
- [Nullplatform API Docs](https://docs.nullplatform.com/docs/api-getting-started)
