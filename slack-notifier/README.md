# Slack Notifier SDK

Production-grade Slack notification tool built on the official `slack_sdk` library for CI/CD pipelines, automated monitoring, and workflow notifications.

## Overview

`slack_notifier_sdk.py` is a Python SDK for sending rich, templated Slack messages with file attachments. Used by other scripts in this repository for automated notifications.

### Key Features

- Reliable file uploads using Slack's `files_upload_v2` API
- Rich message formatting with Block Kit templates
- Automatic retry logic with exponential backoff
- Auto-join public channels
- Template system with variable substitution
- Flexible configuration (CLI args, config files, environment variables)
- Channel name resolution (`#engineering` → `C01234567`)
- Dry-run mode and verbose logging
- Corporate proxy support with custom CA bundles

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

**Requirements**:
- Python 3.7+
- `slack-sdk >= 3.21.0`
- `requests >= 2.20.0`
- `PyYAML >= 6.0` (optional, for YAML configs)

See [Python Setup Guide](../docs/PYTHON_SETUP.md) for virtual environment setup.

### Get Slack Bot Token

1. Create Slack App at https://api.slack.com/apps
2. Add OAuth scopes:
   - `chat:write` - Send messages
   - `files:write` - Upload files
   - `channels:read` - List channels
   - `channels:join` - Auto-join public channels
3. Install app to workspace
4. Copy Bot User OAuth Token (starts with `xoxb-`)

See [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) for detailed setup.

### Basic Usage

```bash
export SLACK_BOT_TOKEN="xoxb-your-token-here"

python slack_notifier_sdk.py \
  --title "Build Complete" \
  --message "Version 1.2.3 deployed successfully" \
  --status success \
  --channel engineering
```

### With File Attachments

```bash
python slack_notifier_sdk.py \
  --title "Test Results" \
  --message "All tests passed!" \
  --status success \
  --files coverage.html test-results.xml \
  --channel qa-team
```

## Configuration

### CLI Arguments

| Argument | Description | Required |
|----------|-------------|----------|
| `--title` | Notification title | Yes |
| `--message` | Message body (supports markdown) | No |
| `--status` | Status level: `success`, `failure`, `error`, `warning`, `info`, `debug` | No (default: `info`) |
| `--channel` | Channel ID (`C01234567`) or name (`engineering`, `#engineering`) | No |
| `--files` | Files to upload (space-separated) | No |
| `--token` | Bot token (overrides env var) | No |
| `--template` | Template name or path | No |
| `--var` | Template variable `KEY=VALUE` (can repeat) | No |
| `--config` | Path to JSON/YAML config file | No |
| `--verbose` | Enable verbose output | No |
| `--dry-run` | Simulate without sending | No |
| `--ca-file` | Custom CA bundle path | No |
| `--insecure` | Disable TLS verification (testing only) | No |

### Environment Variables

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_CHANNEL="C01234567"
```

### Configuration File

Create `config.yaml`:

```yaml
token: xoxb-your-token-here
channel: C01234567
verbose: true
template: workflow_success
template_vars:
  ENVIRONMENT: production
  BUILD_URL: https://ci.example.com/builds/123
```

Use it:
```bash
python slack_notifier_sdk.py \
  --config config.yaml \
  --title "Deploy Complete" \
  --message "Production updated successfully"
```

## Template System

Templates use Slack Block Kit for rich formatting with variable substitution.

### Built-in Templates

Located in `./templates/`:
- `simple.json` - Basic header + message
- `workflow_success.json` - Header, message, status context
- `workflow_failure.json` - Same structure as success

### Template Resolution

Templates can be specified in two ways:

**1. By name** (for built-in templates):
```bash
--template simple
--template workflow_success
```
These are automatically resolved from `./templates/` directory.

**2. By path** (for custom templates):
```bash
--template /path/to/custom_template.json
--template ./my-templates/custom.json
```
Full or relative paths are used as-is.

### Built-in Variables

| Variable | Source | Example |
|----------|--------|---------|
| `{{TITLE}}` | `--title` | "Deploy Complete" |
| `{{MESSAGE}}` | `--message` | "Version 1.2.3 deployed" |
| `{{STATUS}}` | `--status` (uppercase) | "SUCCESS" |
| `{{ICON}}` | Auto-mapped from status | `:white_check_mark:` |

Status icon mapping:
- `SUCCESS` → `:white_check_mark:`
- `FAILURE` / `ERROR` → `:x:`
- `WARNING` → `:warning:`
- `INFO` → `:information_source:`
- `DEBUG` → `:mag:`

### Custom Variables

```bash
python slack_notifier_sdk.py \
  --template workflow_success \
  --title "Deploy to {{ENVIRONMENT}}" \
  --message "Build {{BUILD_ID}} deployed" \
  --var ENVIRONMENT=production \
  --var BUILD_ID=12345 \
  --channel deploys
```

### Creating Custom Templates

Create `templates/my_template.json`:

```json
{
  "username": "CI/CD Bot",
  "icon_emoji": "{{ICON}}",
  "blocks": [
    {
      "type": "header",
      "text": {
        "type": "plain_text",
        "text": "{{ICON}} {{TITLE}}"
      }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "{{MESSAGE}}"
      }
    }
  ]
}
```

## File Upload Behavior

When files are provided:
1. Initial message posted with template blocks
2. Files uploaded as thread replies
3. File links appended to message text

### Retry Logic

- 3 attempts per file with exponential backoff
- Wait times: 0s → 1s → 2s
- Continues with remaining files if one fails

### Channel Membership

Script automatically:
1. Resolves channel name to ID
2. Checks bot membership
3. Auto-joins public channels
4. Warns for private channels (manual invite required)

## Example Use Cases

### Monitoring Alert

```bash
#!/bin/bash
if ! curl -f http://localhost:8080/health; then
  python slack_notifier_sdk.py \
    --title "🚨 Service Down" \
    --message "API health check failed" \
    --status error \
    --channel alerts
fi
```

## Common Issues

### Connection Issues

**"auth_test failed"**:
- Verify token: `echo $SLACK_BOT_TOKEN`
- Check connectivity: `curl https://slack.com/api/auth.test`
- Run with `--verbose`

**SSL certificate errors**:

Use custom CA bundle (recommended):
```bash
python slack_notifier_sdk.py --ca-file /path/to/ca-bundle.pem --title "Test" --channel test
```

Or set environment variable:
```bash
export SSL_CERT_FILE=/path/to/ca-bundle.pem
```

### Channel Issues

**"Channel not found"**:
- Invite bot to private channels manually
- Use channel ID instead of name: `--channel C01234567`
- Verify bot has `channels:read` scope

### File Upload Issues

**"Please upgrade slack-sdk"**:
```bash
pip install --upgrade 'slack-sdk>=3.21.0'
```

**"File not found"**:
- Verify file exists: `ls -la /path/to/file`
- Use absolute paths

For more troubleshooting, see [Contributing Guide](../CONTRIBUTING.md).

## Advanced Usage

### Dry-Run Mode

```bash
python slack_notifier_sdk.py \
  --dry-run \
  --title "Test" \
  --files large-file.zip \
  --channel test
```

### Verbose Logging

```bash
python slack_notifier_sdk.py --verbose --title "Debug" --channel test
```

Shows:
- Template after variable substitution
- File upload attempts and retries
- API response details
- Channel resolution steps

### Corporate Proxy / Custom CA

```bash
# Custom CA bundle
python slack_notifier_sdk.py \
  --ca-file /etc/ssl/certs/corporate-ca-bundle.crt \
  --title "Behind Proxy" \
  --channel test

# Or via environment
export SSL_CERT_FILE=/etc/ssl/certs/corporate-ca-bundle.crt
python slack_notifier_sdk.py --title "Test" --channel test
```

## Key Architecture

**Class**: `SlackNotifierSDK`
- `__init__()` - Initialize WebClient, configure TLS
- `test_connection()` - Validate token
- `resolve_channel_id()` - Convert name → ID
- `ensure_bot_in_channel()` - Auto-join if needed
- `upload_files()` - Multi-file upload with retry
- `post_message()` - Simple message posting
- `send_message_with_files()` - Message + file links

**Data Flow**:
1. Parse arguments + merge config
2. Create SlackNotifierSDK instance
3. Validate token with `auth_test()`
4. Load & process template
5. Send message and/or upload files
6. Print success/failure

## Security Best Practices

✅ Token never logged
✅ Files validated before upload
✅ TLS enabled by default
✅ Scoped permissions
✅ Dry-run mode available

⚠️ Important:
- Store tokens in secrets managers, not code
- When using `--insecure`, it affects ALL HTTPS in the process
- File content uploaded as-is (no inspection)

## Related Documentation

- [Slack Integration Guide](../docs/SLACK_INTEGRATION.md) - Shared Slack setup
- [Python Setup Guide](../docs/PYTHON_SETUP.md) - Python environment
- [Contributing Guide](../CONTRIBUTING.md) - Common patterns and troubleshooting
- [Slack API Docs](https://api.slack.com/docs)
