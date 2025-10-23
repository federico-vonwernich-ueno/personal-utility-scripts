# Python Setup Guide

This guide covers Python environment setup for scripts in this repository.

## Python Version

All scripts require **Python 3.8 or higher**.

Check your Python version:
```bash
python3 --version
```

## Virtual Environment (Recommended)

Using a virtual environment isolates dependencies and prevents conflicts.

### Create Virtual Environment

```bash
# In the repository root
python3 -m venv venv
```

### Activate Virtual Environment

**Linux/macOS**:
```bash
source venv/bin/activate
```

**Windows**:
```bash
venv\Scripts\activate
```

### Deactivate
```bash
deactivate
```

## Installing Dependencies

Each script folder may have its own `requirements.txt` file.

### Global Dependencies (All Scripts)

```bash
pip install PyYAML
```

### Script-Specific Dependencies

Navigate to the script folder and install:

```bash
cd script-folder
pip install -r requirements.txt
```

### Common Dependencies by Script

| Script | Dependencies |
|--------|-------------|
| **analyze-org-repos** | None (uses `jq` and `gh` CLI) |
| **ghact-runner** | `pyyaml` |
| **nullplatform-setup** | `PyYAML` |
| **repository-mirrorer** | `PyGithub`, `PyYAML` |
| **slack-notifier** | `slack-sdk`, `PyYAML`, `urllib3` |
| **workflow-monitor** | `PyYAML` |

### Install All Dependencies at Once

From repository root:
```bash
# Install common dependencies
pip install PyYAML

# Install Slack support (optional)
pip install slack-sdk urllib3

# Install GitHub API support (for repository-mirrorer)
pip install PyGithub
```

## Troubleshooting

### "python3: command not found"

Install Python:

**Ubuntu/Debian**:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

**macOS**:
```bash
brew install python3
```

**Other platforms**: See https://www.python.org/downloads/

### "pip: command not found"

Install pip:
```bash
# Ubuntu/Debian
sudo apt install python3-pip

# macOS (usually included with python3)
python3 -m ensurepip --upgrade
```

### "ModuleNotFoundError: No module named 'X'"

Install the missing module:
```bash
pip install X
```

Or install from requirements file:
```bash
pip install -r requirements.txt
```

### Permission Errors

Use virtual environment or install with `--user`:
```bash
pip install --user package-name
```

### SSL Certificate Errors

For corporate proxies, you may need to specify a custom CA bundle:

```bash
pip install --cert /path/to/ca-bundle.crt package-name
```

Or set environment variable:
```bash
export SSL_CERT_FILE=/path/to/ca-bundle.crt
pip install package-name
```

### Outdated pip

Upgrade pip:
```bash
pip install --upgrade pip
```

## Best Practices

1. **Use virtual environments** for each project
2. **Pin dependency versions** in requirements.txt for reproducibility
3. **Keep dependencies updated** for security patches
4. **Document custom dependencies** in script-specific READMEs
5. **Test in clean environment** before deploying

## Creating requirements.txt

To generate a requirements file from your current environment:

```bash
pip freeze > requirements.txt
```

To create a minimal requirements file (recommended):
```bash
# List only direct dependencies, let pip resolve transitive ones
pip list --not-required --format=freeze > requirements.txt
```

## Upgrading Dependencies

Upgrade all packages:
```bash
pip install --upgrade -r requirements.txt
```

Upgrade specific package:
```bash
pip install --upgrade package-name
```

## Checking Installed Packages

List all installed packages:
```bash
pip list
```

Show details of specific package:
```bash
pip show package-name
```

## Python Version Management

For managing multiple Python versions, consider using:

- **pyenv**: https://github.com/pyenv/pyenv
- **conda**: https://docs.conda.io/

Example with pyenv:
```bash
# Install pyenv
curl https://pyenv.run | bash

# Install Python 3.11
pyenv install 3.11.0

# Set local version for this directory
pyenv local 3.11.0
```

## Related Documentation

- [Python Official Documentation](https://docs.python.org/3/)
- [pip Documentation](https://pip.pypa.io/)
- [Virtual Environments Guide](https://docs.python.org/3/tutorial/venv.html)
- [PyPI (Python Package Index)](https://pypi.org/)
