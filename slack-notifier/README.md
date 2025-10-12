# Slack Notifier SDK

A production-grade Slack notification tool built on the official `slack_sdk` library. Designed for CI/CD pipelines, automated monitoring, and workflow notifications with rich formatting support.

## Features

- ✅ **Reliable file uploads** using Slack's modern `files_upload_v2` API
- 🎨 **Rich message formatting** with Block Kit templates
- 🔄 **Automatic retry logic** with exponential backoff
- 🤖 **Auto-join public channels** to prevent `not_in_channel` errors
- 📝 **Template system** with variable substitution
- 🔧 **Flexible configuration** via CLI args, config files, or environment variables
- 🌐 **Channel name resolution** (converts `#engineering` → `C01234567`)
- 🧪 **Dry-run mode** for testing without sending messages
- 🔍 **Verbose logging** for debugging
- 🔒 **Corporate proxy support** with custom CA bundles

## Installation

```bash
pip install -r requirements.txt
```

### Requirements

- Python 3.7+
- `slack-sdk >= 3.21.0` (with `files_upload_v2` support)
- `requests >= 2.20.0`
- `PyYAML >= 6.0` (optional, for YAML config files)

## Quick Start

### 1. Get a Slack Bot Token

1. Create a Slack App at https://api.slack.com/apps
2. Add these OAuth scopes:
   - `chat:write` - Send messages
   - `files:write` - Upload files
   - `channels:read` - List channels
   - `channels:join` - Auto-join public channels
   - `groups:read` - Access private channels (if needed)
3. Install the app to your workspace
4. Copy the Bot User OAuth Token (starts with `xoxb-`)

### 2. Basic Usage

```bash
# Set token (or use --token flag)
export SLACK_BOT_TOKEN="xoxb-your-token-here"

# Send a simple message
python slack_notifier_sdk.py \
  --title "Build Complete" \
  --message "Version 1.2.3 deployed successfully" \
  --status success \
  --channel engineering
```

### 3. With File Attachments

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

| Argument | Required | Description |
|----------|----------|-------------|
| `--title` | ✅ | Notification title |
| `--message` | ❌ | Message body (supports markdown) |
| `--status` | ❌ | Status level: `success`, `failure`, `error`, `warning`, `info`, `debug` (default: `info`) |
| `--channel` | ❌ | Channel ID (`C01234567`) or name (`engineering`, `#engineering`) |
| `--files` | ❌ | Files to upload (space-separated) |
| `--token` | ❌ | Bot token (overrides `SLACK_BOT_TOKEN` env var) |
| `--template` | ❌ | Template name or path (see Templates section) |
| `--var` | ❌ | Template variable `KEY=VALUE` (can repeat) |
| `--config` | ❌ | Path to JSON/YAML config file |
| `--verbose` | ❌ | Enable verbose output |
| `--dry-run` | ❌ | Simulate actions without contacting Slack |
| `--make-public` | ❌ | Create public permalinks (may be restricted) |
| `--insecure` | ❌ | Disable TLS verification (INSECURE, testing only) |
| `--ca-file` | ❌ | Custom CA bundle path (safer than `--insecure`) |

### Environment Variables

```bash
export SLACK_BOT_TOKEN="xoxb-..."  # Bot token
export SLACK_CHANNEL="C01234567"   # Default channel
```

### Configuration File

Create `config.yaml`:

```yaml
# Authentication
token: xoxb-your-token-here
channel: C01234567

# Behavior
verbose: true
verify_tls: true
dry_run: false

# Templates
template: workflow_success
template_vars:
  ENVIRONMENT: production
  REGION: us-east-1
  BUILD_URL: https://ci.example.com/builds/123
```

Then use it:

```bash
python slack_notifier_sdk.py \
  --config config.yaml \
  --title "Deploy Complete" \
  --message "Production updated successfully"
```

### Configuration Precedence

```
CLI arguments (highest priority)
    ↓
Config file (--config)
    ↓
Environment variables
    ↓
Defaults (lowest priority)
```

## Template System

Templates use Slack's Block Kit for rich formatting with variable substitution.

### Built-in Templates

Located in `./templates/`:

1. **`simple.json`** - Basic header + message
2. **`workflow_success.json`** - Header, message, status context
3. **`workflow_failure.json`** - Same as success (customize as needed)

### Built-in Variables

| Variable | Source | Example |
|----------|--------|---------|
| `{{TITLE}}` | `--title` | "Deploy Complete" |
| `{{MESSAGE}}` | `--message` | "Version 1.2.3 deployed" |
| `{{STATUS}}` | `--status` (uppercase) | "SUCCESS" |
| `{{ICON}}` | Auto-mapped from status | `:white_check_mark:` |

#### Status Icon Mapping

- `SUCCESS` → `:white_check_mark:`
- `FAILURE` / `ERROR` → `:x:`
- `WARNING` → `:warning:`
- `INFO` → `:information_source:`
- `DEBUG` → `:mag:`

### Custom Variables

Pass variables with `--var`:

```bash
python slack_notifier_sdk.py \
  --template workflow_success \
  --title "Deploy to {{ENVIRONMENT}}" \
  --message "Build {{BUILD_ID}} deployed to {{REGION}}" \
  --var ENVIRONMENT=production \
  --var BUILD_ID=12345 \
  --var REGION=us-east-1 \
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
        "text": "{{ICON}} {{TITLE}}",
        "emoji": true
      }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Environment:* {{ENVIRONMENT}}\n*Build:* <{{BUILD_URL}}|#{{BUILD_ID}}>\n\n{{MESSAGE}}"
      }
    },
    {
      "type": "divider"
    },
    {
      "type": "context",
      "elements": [
        {
          "type": "mrkdwn",
          "text": "Region: {{REGION}} | Status: *{{STATUS}}*"
        }
      ]
    }
  ]
}
```

Use it:

```bash
python slack_notifier_sdk.py \
  --template my_template \
  --title "Production Deploy" \
  --message "All services updated" \
  --status success \
  --var ENVIRONMENT=production \
  --var BUILD_ID=12345 \
  --var BUILD_URL=https://ci.example.com/builds/12345 \
  --var REGION=us-east-1
```

### Template Resolution

The script searches for templates in this order:

1. Absolute/relative path provided
2. `./templates/{name}.json`
3. `./templates/{name}.yml`
4. `./templates/{name}.yaml`

## File Upload Behavior

### How It Works

When files are provided with `--files`:

1. **Initial message posted** with template blocks (if provided)
2. **Files uploaded as thread replies** to keep the channel clean
3. **File links appended** to message text for quick access

#### Upload Strategy

```
WITH FILES:
  Step 1: post_message(text, blocks) → returns timestamp
  Step 2: upload_files(thread_ts=timestamp) → attaches to thread

  Fallback (if Step 1 fails):
  → upload_files(initial_comment=text) → message on first file only
```

### Retry Logic

- **3 attempts** per file with exponential backoff
- Wait times: 0s → 1s → 2s
- Continues with remaining files if one fails

### Channel Membership

The script **automatically handles channel membership**:

1. **Resolves channel name** to ID (`#engineering` → `C01234567`)
2. **Checks bot membership** using `conversations_info`
3. **Auto-joins public channels** if not a member
4. **Prints warning for private channels** (bot must be invited manually)
5. **Fails fast** with actionable error message if upload would fail

### File Link Format

Files are appended to messages as Slack-formatted links:

```
Your message text

Archivos:
<https://workspace.slack.com/files/U123/F456/file1.txt|file1.txt>
<https://workspace.slack.com/files/U123/F789/file2.png|file2.png>
```

## Examples

### CI/CD Integration (GitHub Actions)

```yaml
- name: Notify Slack on Success
  if: success()
  run: |
    python slack_notifier_sdk.py \
      --title "✅ Build Succeeded" \
      --message "Commit: ${{ github.sha }}\nBranch: ${{ github.ref_name }}" \
      --status success \
      --files build-log.txt coverage.html \
      --channel ci-notifications \
      --template workflow_success \
      --var BUILD_URL="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}" \
      --var ENVIRONMENT="production"
  env:
    SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
```

### Monitoring Alert

```bash
#!/bin/bash
# monitor.sh - Check service health and notify on failure

if ! curl -f http://localhost:8080/health; then
  python slack_notifier_sdk.py \
    --title "🚨 Service Down" \
    --message "API health check failed at $(date)" \
    --status error \
    --channel alerts \
    --template workflow_failure
fi
```

### Daily Report

```bash
#!/bin/bash
# daily_report.sh - Generate and send daily metrics

# Generate reports
python generate_metrics.py > metrics.txt
python generate_chart.py > chart.png

# Send to Slack
python slack_notifier_sdk.py \
  --title "📊 Daily Metrics Report" \
  --message "$(cat metrics.txt)" \
  --status info \
  --files chart.png metrics.csv \
  --channel daily-reports \
  --template simple
```

### With Configuration File

```yaml
# config/production.yaml
token: xoxb-production-token
channel: prod-deploys
verbose: false
template: workflow_success
template_vars:
  ENVIRONMENT: production
  REGION: us-west-2
  TEAM: platform
```

```bash
python slack_notifier_sdk.py \
  --config config/production.yaml \
  --title "Deploy v2.5.0" \
  --message "Rolling update completed" \
  --status success \
  --files deployment-manifest.yaml
```

## Troubleshooting

### Connection Issues

#### Error: `auth_test failed`

**Cause**: Token invalid, network issues, or proxy blocking connection.

**Solutions**:
1. Verify token is correct: `echo $SLACK_BOT_TOKEN`
2. Check network connectivity: `curl https://slack.com/api/auth.test`
3. Run with `--verbose` to see detailed error messages
4. Check startup diagnostics (printed automatically):
   ```
   python_version=3.11.0
   slack_sdk_version=3.21.3
   certifi_where=/path/to/cacert.pem
   HTTPS_PROXY=None
   ```

#### Error: `SSL: CERTIFICATE_VERIFY_FAILED`

**Cause**: Corporate proxy with self-signed certificates.

**Solutions** (in order of preference):

1. **Use custom CA bundle** (recommended):
   ```bash
   python slack_notifier_sdk.py \
     --ca-file /path/to/corporate-ca-bundle.pem \
     --title "Test" \
     --channel test
   ```

2. **Set environment variable**:
   ```bash
   export SSL_CERT_FILE=/path/to/corporate-ca-bundle.pem
   python slack_notifier_sdk.py --title "Test" --channel test
   ```

3. **Disable verification** (INSECURE - testing only):
   ```bash
   python slack_notifier_sdk.py \
     --insecure \
     --title "Test" \
     --channel test
   ```

### Channel Issues

#### Error: `Channel not found or inaccessible to the bot`

**Cause**: Bot not in channel or channel doesn't exist.

**Solutions**:
1. **Invite bot to private channels** manually in Slack UI
2. **Use channel ID instead of name**: `--channel C01234567`
3. **Check bot has required scopes**: `channels:read`, `groups:read`
4. Run with `--verbose` to see channel resolution details

#### Error: `not_in_channel`

**Cause**: Bot membership check failed or auto-join didn't work.

**Solutions**:
1. Manually invite bot to channel
2. Verify bot has `channels:join` scope for public channels
3. Check if channel is private (requires manual invite)

### File Upload Issues

#### Error: `Please upgrade slack-sdk to a version that provides files_upload_v2`

**Cause**: Installed `slack-sdk` is too old.

**Solution**:
```bash
pip install --upgrade 'slack-sdk>=3.21.0'
```

#### Error: `File not found, skipping: /path/to/file`

**Cause**: File doesn't exist at specified path.

**Solutions**:
1. Verify file exists: `ls -la /path/to/file`
2. Use absolute paths instead of relative paths
3. Check file permissions

#### Files Upload But Message Fails

**Cause**: Template syntax error or empty blocks.

**Solutions**:
1. Test without template: remove `--template` flag
2. Validate JSON syntax: `python -m json.tool templates/my_template.json`
3. Check for empty variables: `{{UNDEFINED_VAR}}` results in empty string
4. Run with `--verbose` to see template after substitution

### Template Issues

#### Error: `Template not found`

**Cause**: Template file doesn't exist in expected locations.

**Solutions**:
1. Check file exists: `ls -la templates/my_template.json`
2. Use absolute path: `--template /full/path/to/template.json`
3. Verify file extension (`.json`, `.yml`, or `.yaml`)

#### Variables Not Replaced

**Cause**: Incorrect variable syntax or not passed via `--var`.

**Solutions**:
1. Use double curly braces: `{{VAR}}` not `{VAR}`
2. Pass via CLI: `--var VAR=value`
3. Or set in config file under `template_vars`
4. Check with `--verbose` to see template after substitution

## Architecture Overview

### Class Structure

```
SlackNotifierSDK
├── __init__()           # Initialize WebClient, configure TLS
├── test_connection()    # Validate token with auth_test
├── resolve_channel_id() # Convert name → ID
├── ensure_bot_in_channel() # Auto-join if needed
├── upload_files()       # Multi-file upload with retry
├── post_message()       # Simple message posting
└── send_message_with_files() # Message + file links
```

### Data Flow

```
CLI Input
    ↓
Parse arguments + merge config
    ↓
Create SlackNotifierSDK instance
    ↓
test_connection() [validate token]
    ↓
Load & process template (if provided)
    ↓
┌──────────────────────────────────┐
│ WITH FILES:                      │
│  1. post_message() → get ts      │
│  2. upload_files(thread_ts=ts)   │
│  3. Files appear in thread       │
└──────────────────────────────────┘
┌──────────────────────────────────┐
│ WITHOUT FILES:                   │
│  1. send_message_with_files()    │
│     (blocks from template)       │
└──────────────────────────────────┘
    ↓
Print success/failure
```

### API Call Sequence (Typical Run)

```
1. auth_test()                    # Startup validation
2. conversations_list()           # Resolve channel name
3. conversations_info()           # Check membership
4. conversations_join() [maybe]   # Auto-join public channel
5. chat_postMessage()             # Initial message
6. files_upload_v2() × N          # Upload each file
7. conversations_list()           # Resolve channel (again, for message)
---
Total: ~7-9 API calls for typical run with files
```

## Security Considerations

### ✅ Best Practices

- **Token never logged**: Only prints `token present={bool}` in output
- **Files validated**: Checks existence before upload attempt
- **TLS enabled by default**: Must explicitly disable with `--insecure`
- **Scoped permissions**: Only requests necessary OAuth scopes
- **Dry-run mode**: Test workflows without sending data

### ⚠️ Important Notes

1. **Global SSL Context**: When using `--insecure` or `verify_tls=False`, the script modifies Python's global SSL context, affecting ALL HTTPS connections in the process
2. **Token Security**: Store tokens in secrets managers, not in code or config files committed to git
3. **File Content**: The script uploads files as-is without content inspection - ensure sensitive data is not included
4. **Proxy Settings**: When using `--ca-file`, the path is exported to `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` environment variables

### Recommended Practices

```bash
# ✅ Good - Token from environment
export SLACK_BOT_TOKEN="$(vault read -field=token secret/slack)"
python slack_notifier_sdk.py --title "Test" --channel test

# ❌ Bad - Token in command (visible in process list)
python slack_notifier_sdk.py --token xoxb-secret-token --title "Test"

# ✅ Good - Config file with restricted permissions
chmod 600 config/production.yaml
python slack_notifier_sdk.py --config config/production.yaml --title "Deploy"

# ❌ Bad - Config file in git repo
git add config/production.yaml  # Don't do this!
```

## Advanced Usage

### Dry-Run Mode

Test your configuration without sending anything:

```bash
python slack_notifier_sdk.py \
  --dry-run \
  --title "Test Deploy" \
  --message "This won't actually send" \
  --files large-file.zip \
  --channel test
```

Output:
```
(dry-run) auth_test: simulated ok
(dry-run) would upload: large-file.zip -> channel=test
(dry-run) would post message to test: [SUCCESS] Test Deploy
(dry-run) files: ['large-file.zip']
```

### Verbose Logging

See detailed API interactions:

```bash
python slack_notifier_sdk.py \
  --verbose \
  --title "Debug Test" \
  --channel engineering
```

Prints:
- Template after variable substitution (JSON)
- File upload attempts and retries
- API response details
- Channel resolution steps
- File metadata

### Corporate Proxy / Custom CA

For environments with intercepting proxies:

```bash
# Option 1: Custom CA bundle
python slack_notifier_sdk.py \
  --ca-file /etc/ssl/certs/corporate-ca-bundle.crt \
  --title "Behind Proxy" \
  --channel test

# Option 2: Environment variables
export SSL_CERT_FILE=/etc/ssl/certs/corporate-ca-bundle.crt
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/corporate-ca-bundle.crt
python slack_notifier_sdk.py --title "Behind Proxy" --channel test
```

## Development

### Running Tests

```bash
# Test connection only
python slack_notifier_sdk.py \
  --dry-run \
  --title "Connection Test" \
  --channel test

# Test with actual API call
python slack_notifier_sdk.py \
  --title "Live Test" \
  --message "Testing notification system" \
  --channel test-channel
```

### Project Structure

```
slack-notifier/
├── slack_notifier_sdk.py     # Main script
├── requirements.txt           # Python dependencies
├── templates/                 # Built-in templates
│   ├── simple.json
│   ├── workflow_success.json
│   └── workflow_failure.json
├── config/                    # Example configs (not in git)
│   └── production.yaml
└── README.md                  # This file
```

### Contributing

When modifying the script:

1. **Test with dry-run first**: `--dry-run --verbose`
2. **Verify error handling**: Test failure scenarios
3. **Check template compatibility**: Validate JSON with templates
4. **Update documentation**: Keep this README in sync

## License

[Specify your license here]

## Support

For issues or questions:
- Check the Troubleshooting section above
- Review Slack API documentation: https://api.slack.com/docs
- File an issue in your project repository
