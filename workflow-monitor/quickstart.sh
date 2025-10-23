#!/bin/bash
# Quick Start Script for Workflow Monitor

set -e

echo "GitHub Workflow Monitor - Quick Start"
echo "======================================"
echo ""

# Check prerequisites
echo "Checking prerequisites..."

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed"
    exit 1
fi
echo "✓ Python 3 is installed"

# Check gh CLI
if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI (gh) is not installed"
    echo "   Install it from: https://cli.github.com/"
    exit 1
fi
echo "✓ GitHub CLI is installed"

# Check gh authentication
if ! gh auth status &> /dev/null; then
    echo "❌ GitHub CLI is not authenticated"
    echo "   Run: gh auth login"
    exit 1
fi
echo "✓ GitHub CLI is authenticated"

# Check PyYAML
if ! python3 -c "import yaml" &> /dev/null; then
    echo "❌ PyYAML is not installed"
    echo "   Installing PyYAML..."
    pip3 install PyYAML
    echo "✓ PyYAML installed"
else
    echo "✓ PyYAML is installed"
fi

echo ""
echo "All prerequisites met! ✓"
echo ""

# Show usage examples
echo "Usage Examples:"
echo "==============="
echo ""
echo "1. Test with example config (single check):"
echo "   python3 monitor_workflows.py test-config.yaml --once"
echo ""
echo "2. Run continuous monitoring:"
echo "   python3 monitor_workflows.py test-config.yaml"
echo ""
echo "3. Create your own config:"
echo "   cp config.example.yaml my-config.yaml"
echo "   # Edit my-config.yaml with your repositories"
echo "   python3 monitor_workflows.py my-config.yaml --once"
echo ""
echo "4. Run in background (Linux/macOS):"
echo "   nohup python3 monitor_workflows.py my-config.yaml > monitor.log 2>&1 &"
echo ""
echo "5. Use with cron (check every 5 minutes):"
echo "   */5 * * * * cd $(pwd) && python3 monitor_workflows.py my-config.yaml --once"
echo ""
