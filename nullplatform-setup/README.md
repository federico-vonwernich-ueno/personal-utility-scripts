# Nullplatform Setup Script

A Python script to automate the creation of applications, parameters, and scopes in nullplatform using the `np` CLI based on a YAML configuration file.

## Features

- **Config-driven setup**: Define all resources in a YAML file
- **Automated resource creation**: Creates namespaces, applications, scopes, and parameters
- **ID tracking**: Automatically tracks created resource IDs for dependencies
- **Dry-run mode**: Preview changes before applying
- **Error handling**: Gracefully handles existing resources and errors
- **Verbose logging**: Detailed output for troubleshooting
- **JSON-based communication**: Uses np CLI's JSON format for reliable parsing

## Prerequisites

### 1. Nullplatform CLI

Install the nullplatform CLI:

```bash
curl https://cli.nullplatform.com/install.sh | sh
```

Verify installation:

```bash
np --version
```

### 2. Python Dependencies

```bash
pip install PyYAML
```

### 3. Nullplatform API Key

Create an API key in nullplatform:

1. Log in to your nullplatform account
2. Navigate to Settings → API Keys
3. Create a new API key
4. Copy the key for use with this script

## Installation

1. Copy the script files to your desired location:
```bash
cp nullplatform-setup.py /path/to/your/scripts/
cp nullplatform-setup.example.yaml /path/to/your/scripts/
```

2. Make the script executable:
```bash
chmod +x nullplatform-setup.py
```

## Configuration

### Create Configuration File

Copy the example config and customize it:

```bash
cp nullplatform-setup.example.yaml nullplatform-setup.yaml
```

### Configuration Structure

```yaml
# Optional: Create or use existing namespace
namespace:
  name: "my-namespace"

# Applications to create
applications:
  - name: "my-web-app"
    namespace_id: "ns-xxxxx"
    # Add other fields as needed

# Scopes (environments)
scopes:
  - name: "development"
    application_id: "app-xxxxx"
    # Add dimensions, type, etc.

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

### Resource IDs

**Important**: You'll need to provide IDs for parent resources:

- `namespace_id`: Required for applications (get from `np namespace list`)
- `application_id`: Required for scopes and parameters (get from `np application list`)
- `scope_id`: Optional for parameters to set scope-specific values

**Tip**: Run the script incrementally:
1. Create namespace first
2. Note the namespace ID from output
3. Update config with namespace ID
4. Create applications
5. Note application IDs
6. Create scopes and parameters with those IDs

## Usage

### Set API Key

Set your nullplatform API key as an environment variable:

```bash
export NULLPLATFORM_API_KEY="your-api-key-here"
```

Or pass it via command line:

```bash
python nullplatform-setup.py --api-key "your-api-key-here"
```

### Basic Usage

Run with default config file:

```bash
python nullplatform-setup.py
```

Specify a config file:

```bash
python nullplatform-setup.py --config my-config.yaml
```

### Dry Run

Preview what would be created without making changes:

```bash
python nullplatform-setup.py --dry-run
```

### Verbose Output

Enable detailed logging:

```bash
python nullplatform-setup.py --verbose
```

### Combine Options

```bash
python nullplatform-setup.py --config my-config.yaml --dry-run --verbose
```

## How It Works

### Creation Order

Resources are created in this order to satisfy dependencies:

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

The script handles various scenarios:

- **Resource already exists**: Logs a warning and continues
- **Creation error**: Logs error details and continues with next resource
- **Missing dependencies**: Fails with clear error message
- **API errors**: Shows full error from nullplatform

## Command Reference

The script uses these `np` CLI commands:

```bash
# Create resources
np namespace create --body '{"name": "..."}'
np application create --body '{"name": "...", "namespace_id": "..."}'
np scope create --body '{"name": "...", "application_id": "..."}'
np parameter create --body '{"name": "...", "application_id": "..."}'
np parameter value create --id <param-id> --body '{"value": "..."}'

# List resources (to check existing)
np namespace list --format json
np application list --format json
np scope list --format json
np parameter list --format json
```

## Example Output

```
2025-10-20 16:30:00 - INFO - Loading configuration from nullplatform-setup.yaml
2025-10-20 16:30:00 - INFO - Config loaded: 2 apps, 3 parameters, 2 scopes
2025-10-20 16:30:01 - INFO - Creating namespace: my-namespace
2025-10-20 16:30:02 - INFO - ✓ Created namespace: my-namespace (ID: ns-abc123)
2025-10-20 16:30:02 - INFO - Creating application: my-web-app
2025-10-20 16:30:03 - INFO - ✓ Created application: my-web-app (ID: app-def456)
2025-10-20 16:30:03 - INFO - Creating scope: development
2025-10-20 16:30:04 - INFO - ✓ Created scope: development (ID: scope-ghi789)
2025-10-20 16:30:04 - INFO - Creating parameter: DATABASE_URL
2025-10-20 16:30:05 - INFO - ✓ Created parameter: DATABASE_URL (ID: param-jkl012)
2025-10-20 16:30:05 - INFO - ✓ Set value for parameter: DATABASE_URL

============================================================
SETUP SUMMARY
============================================================
Total resources: 7
Created:         7
Already exists:  0
Errors:          0
============================================================
```

## Troubleshooting

### "np: command not found"

The nullplatform CLI is not installed or not in PATH. Install it:

```bash
curl https://cli.nullplatform.com/install.sh | sh
```

If installed but not in PATH, use `--np-path`:

```bash
python nullplatform-setup.py --np-path /path/to/np
```

### "API key required"

Set the NULLPLATFORM_API_KEY environment variable or pass via `--api-key`.

### "Failed to create resource: already exists"

This is normal if resources exist. The script will log a warning and continue. If you need to update existing resources, use the nullplatform UI or `np <resource> update` commands.

### "Missing required field: namespace_id"

Applications require a namespace_id. Either:
1. Create a namespace first and use its ID
2. Get an existing namespace ID with: `np namespace list --format json`

### JSON parsing errors

If you see "Failed to parse response", the np CLI might have returned an error or unexpected format. Run with `--verbose` to see full command output.

## Advanced Usage

### Custom np CLI Path

If `np` is not in your PATH:

```bash
python nullplatform-setup.py --np-path ~/.local/bin/np
```

### Incremental Setup

You can run the script multiple times. It will skip resources that already exist.

1. First run: Create namespace and applications
2. Update config with created IDs
3. Second run: Create scopes and parameters

### Integration with CI/CD

Example GitHub Actions workflow:

```yaml
name: Setup Nullplatform Resources

on:
  workflow_dispatch:

jobs:
  setup:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install nullplatform CLI
        run: curl https://cli.nullplatform.com/install.sh | sh

      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install PyYAML

      - name: Run setup
        env:
          NULLPLATFORM_API_KEY: ${{ secrets.NULLPLATFORM_API_KEY }}
        run: |
          python nullplatform-setup.py --config nullplatform-setup.yaml
```

## API Reference

For detailed information about the request body formats for each resource type, refer to:

- [Nullplatform Documentation](https://docs.nullplatform.com/)
- `np <command> create --help` for command-specific help

## Slack Integration

The nullplatform setup script can send real-time notifications to Slack, keeping your team informed of resource creation progress and results.

### Features

- **Threaded notifications**: All updates posted as threaded replies for easy tracking
- **Setup start notification**: Announces when setup begins with configuration details
- **Progress updates**: Real-time updates for each resource (created/exists/error)
- **Summary notification**: Comprehensive summary at the end with statistics
- **Error resilient**: Script continues even if Slack notifications fail
- **Resource type breakdown**: Shows statistics by resource type (namespace, apps, scopes, parameters)

### Setup

#### 1. Install Slack Dependencies

```bash
pip install slack-sdk urllib3
```

#### 2. Create a Slack App

1. Go to https://api.slack.com/apps
2. Click "Create New App" → "From scratch"
3. Name it "Nullplatform Setup Bot" and select your workspace
4. Navigate to "OAuth & Permissions"
5. Add these Bot Token Scopes:
   - `chat:write` - Send messages
   - `chat:write.public` - Send to public channels without joining
6. Install the app to your workspace
7. Copy the "Bot User OAuth Token" (starts with `xoxb-`)

#### 3. Configure Environment Variables

Set these environment variables:

```bash
# Required for Slack notifications
export SLACK_BOT_TOKEN="xoxb-your-token-here"
export SLACK_CHANNEL="C01234567"  # Channel ID or #channel-name

# Optional
export SLACK_DRY_RUN=1  # Test without sending actual notifications
```

For GitHub Actions, add these as repository secrets:
- `SLACK_BOT_TOKEN` - Your Slack bot token
- `SLACK_CHANNEL` - Your Slack channel ID

#### 4. Invite Bot to Channel

In Slack, invite the bot to your channel:
```
/invite @Nullplatform Setup Bot
```

### Usage

Once configured, Slack notifications are sent automatically:

```bash
# Run with Slack notifications (if env vars are set)
python nullplatform-setup.py --config my-config.yaml

# Test Slack notifications without actual setup
export SLACK_DRY_RUN=1
python nullplatform-setup.py --config my-config.yaml --dry-run
```

### Notification Flow

1. **Main Message**: Setup start announcement
   - Total resources to create
   - Namespace name (if applicable)
   - Counts by resource type (applications, scopes, parameters)

2. **Thread Replies**: Progress updates for each resource
   - Status (created/already exists/error)
   - Resource type and name
   - Resource ID (if created)
   - Any error messages

3. **Final Thread Reply**: Comprehensive summary
   - Statistics (created, already exist, errors)
   - Duration
   - Breakdown by resource type
   - List of any errors

### Example Slack Notification

```
⚙️ Nullplatform Setup Starting

Total Resources: 7
Namespace: my-namespace

Applications: 2
Scopes: 2

Parameters: 3
Status: INFO

Progress updates will be posted in this thread
```

Thread replies:
```
✓ Created: namespace/my-namespace
Resource Type: namespace
Name: my-namespace
Status: Created
Resource ID: `ns-abc123`

---

✓ Created: application/my-web-app
Resource Type: application
Name: my-web-app
Status: Created
Resource ID: `app-def456`

---

✓ Nullplatform Setup Complete

Summary Statistics
Total Resources: 7
Duration: 15s

Created: ✓ 7
Already Exist: ℹ️ 0
Errors: ✗ 0

By Resource Type:
• namespace: 1 created, 0 existing, 0 errors
• application: 2 created, 0 existing, 0 errors
• scope: 2 created, 0 existing, 0 errors
• parameter: 3 created, 0 existing, 0 errors
```

### Troubleshooting Slack

#### "SLACK_BOT_TOKEN not set"

- Set the `SLACK_BOT_TOKEN` environment variable
- Or set `SLACK_DRY_RUN=1` to suppress the warning

#### "SLACK_CHANNEL not set"

- Set the `SLACK_CHANNEL` environment variable
- Use channel ID (C01234567) or name (#nullplatform-setup)

#### "slack-sdk not installed"

```bash
pip install slack-sdk urllib3
```

#### "Bot not in channel"

For public channels, the bot can auto-join. For private channels:
```
/invite @Nullplatform Setup Bot
```

#### Notifications not appearing

1. Check bot token is valid
2. Verify channel ID is correct
3. Ensure bot has `chat:write` permission
4. Check script logs for Slack errors (run with `--verbose`)

### Customizing Notifications

Templates are located in `../slack-notifier/templates/`:
- `nullplatform_setup_start.json` - Setup start message
- `nullplatform_setup_progress.json` - Progress update per resource
- `nullplatform_setup_summary.json` - Final summary

You can customize these templates to change the notification format. Templates use variables like `{{RESOURCE_NAME}}`, `{{STATUS}}`, etc.

### Disabling Slack

Slack notifications are automatically disabled if environment variables are not set. To explicitly disable:

```bash
# Don't set SLACK_BOT_TOKEN or SLACK_CHANNEL
unset SLACK_BOT_TOKEN
unset SLACK_CHANNEL
```

The script will run normally without attempting Slack notifications.

## Best Practices

1. **Use dry-run first**: Always test with `--dry-run` before creating resources
2. **Version control your config**: Keep your YAML config in git (without secrets)
3. **Use secrets management**: Don't commit API keys or sensitive parameter values
4. **Incremental setup**: Create resources in stages to manage dependencies
5. **Document IDs**: Keep track of created resource IDs for future reference
6. **Test in non-production**: Test the setup in a development environment first

## Security Notes

- **Never commit API keys** to version control
- **Use environment variables** for sensitive values
- **Mark parameters as secrets** when they contain sensitive data
- **Rotate API keys regularly** for security
- **Use separate API keys** for different environments

## License

This script is provided as-is for internal use.

## Support

For issues with:
- **This script**: Check the troubleshooting section above
- **Nullplatform CLI**: See [Nullplatform CLI Docs](https://docs.nullplatform.com/docs/cli/)
- **Nullplatform API**: See [Nullplatform API Docs](https://docs.nullplatform.com/docs/api-getting-started)
