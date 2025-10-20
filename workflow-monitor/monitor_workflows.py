#!/usr/bin/env python3
"""
GitHub Workflow Monitor Script

This script monitors GitHub workflow runs for specified repositories and detects failures.
It supports:
- Continuous monitoring with configurable polling intervals
- Multiple repositories and workflows
- Failure detection and alerting
- Historical state tracking to detect new failures
- Detailed failure reporting
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install it with: pip install PyYAML")
    sys.exit(1)


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    DEBUG = '\033[90m'  # Dim/gray for debug output


class WorkflowMonitor:
    """Monitors GitHub workflow runs for failures"""
    
    def __init__(self, config: Dict, state_file: Optional[str] = None, log_file: Optional[str] = None):
        self.config = config
        self.poll_interval = config.get('poll_interval', 60)
        self.lookback_minutes = config.get('lookback_minutes', 60)
        self.max_runs_per_check = config.get('max_runs_per_check', 100)
        self.state_file = Path(state_file) if state_file else None
        self.seen_runs: Dict[str, Set[int]] = {}
        
        # Set up log file if specified
        self.log_file = None
        if log_file:
            try:
                self.log_file = open(log_file, 'a', encoding='utf-8')
                self._log_to_file(f"{'='*80}\n")
                self._log_to_file(f"Workflow Monitor started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                self._log_to_file(f"{'='*80}\n")
            except Exception as e:
                print(f"Warning: Could not open log file {log_file}: {e}")
                self.log_file = None
        
        # Load previous state if available
        if self.state_file and self.state_file.exists():
            self._load_state()
        
        # Check gh CLI authentication
        self._check_gh_auth()
    
    def _check_gh_auth(self):
        """Verify GitHub CLI authentication"""
        try:
            result = subprocess.run(
                ['gh', 'auth', 'status'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                self._print_error("GitHub CLI is not authenticated. Run: gh auth login")
                sys.exit(1)
        except subprocess.TimeoutExpired:
            self._print_error("GitHub CLI authentication check timed out")
            sys.exit(1)
        except FileNotFoundError:
            self._print_error("GitHub CLI (gh) is not installed")
            sys.exit(1)
    
    def _log_to_file(self, message: str):
        """Write message to log file (without color codes)"""
        if self.log_file:
            # Strip ANSI color codes for log file
            import re
            clean_message = re.sub(r'\033\[[0-9;]+m', '', message)
            self.log_file.write(clean_message)
            self.log_file.flush()
    
    def _print_info(self, message: str):
        """Print info message"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        output = f"{Colors.OKCYAN}[{timestamp}] ℹ {message}{Colors.ENDC}\n"
        sys.stdout.write(output)
        sys.stdout.flush()
        self._log_to_file(output)
    
    def _print_debug(self, message: str):
        """Print debug message"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        output = f"{Colors.DEBUG}[{timestamp}] 🔍 {message}{Colors.ENDC}\n"
        sys.stdout.write(output)
        sys.stdout.flush()
        self._log_to_file(output)
    
    def _print_success(self, message: str):
        """Print success message"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        output = f"{Colors.OKGREEN}[{timestamp}] ✓ {message}{Colors.ENDC}\n"
        sys.stdout.write(output)
        sys.stdout.flush()
        self._log_to_file(output)
    
    def _print_warning(self, message: str):
        """Print warning message"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        output = f"{Colors.WARNING}[{timestamp}] ⚠ {message}{Colors.ENDC}\n"
        sys.stdout.write(output)
        sys.stdout.flush()
        self._log_to_file(output)
    
    def _print_error(self, message: str):
        """Print error message"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        output = f"{Colors.FAIL}[{timestamp}] ✗ {message}{Colors.ENDC}\n"
        sys.stdout.write(output)
        sys.stdout.flush()
        self._log_to_file(output)
    
    def _print_header(self, message: str):
        """Print header message"""
        header_line = f"\n{Colors.HEADER}{Colors.BOLD}{message}{Colors.ENDC}\n"
        separator_line = f"{Colors.HEADER}{'=' * len(message)}{Colors.ENDC}\n"
        sys.stdout.write(header_line)
        sys.stdout.write(separator_line)
        sys.stdout.flush()
        self._log_to_file(header_line)
        self._log_to_file(separator_line)
    
    def _load_state(self):
        """Load seen runs from state file"""
        try:
            with self.state_file.open('r') as f:
                state = json.load(f)
                self.seen_runs = {k: set(v) for k, v in state.items()}
            self._print_info(f"Loaded state from {self.state_file}")
        except Exception as e:
            self._print_warning(f"Could not load state file: {e}")
            self.seen_runs = {}
    
    def _save_state(self):
        """Save seen runs to state file"""
        if not self.state_file:
            return
        
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with self.state_file.open('w') as f:
                # Convert sets to lists for JSON serialization
                state = {k: list(v) for k, v in self.seen_runs.items()}
                json.dump(state, f, indent=2)
        except Exception as e:
            self._print_warning(f"Could not save state file: {e}")
    
    def _parse_repository(self, repo_input: str) -> str:
        """
        Parse repository input and return in owner/repo format.
        Accepts various formats: owner/repo, URLs, etc.
        """
        import re
        
        # If already in owner/repo format
        if re.match(r'^[^/]+/[^/]+$', repo_input):
            return repo_input
        
        # Try to parse GitHub URLs
        patterns = [
            r'https?://github\.com/([^/]+)/([^/\.]+)',  # HTTPS URL
            r'git@github\.com:([^/]+)/([^/\.]+)',        # SSH URL
        ]
        
        for pattern in patterns:
            match = re.match(pattern, repo_input)
            if match:
                owner, repo = match.groups()
                return f"{owner}/{repo}"
        
        raise ValueError(f"Unable to parse repository from: {repo_input}")
    
    def _get_workflow_runs(self, repo: str, workflow: Optional[str] = None, 
                          branch: Optional[str] = None) -> List[Dict]:
        """
        Get recent workflow runs for a repository
        
        Args:
            repo: Repository in format "owner/repo"
            workflow: Optional workflow file name to filter
            branch: Optional branch name to filter
            
        Returns:
            List of workflow run dictionaries
        """
        try:
            # Calculate lookback time using timezone-aware datetime
            lookback_time = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)
            created_filter = lookback_time.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Build filter description for debug output
            filters = []
            if workflow:
                filters.append(f"workflow={workflow}")
            if branch:
                filters.append(f"branch={branch}")
            filter_str = f" ({', '.join(filters)})" if filters else " (all workflows, all branches)"
            
            self._print_debug(f"Fetching runs from {repo}{filter_str}...")
            
            cmd = [
                'gh', 'run', 'list',
                '--repo', repo,
                '--limit', str(self.max_runs_per_check),
                '--json', 'databaseId,name,displayTitle,status,conclusion,createdAt,updatedAt,headBranch,url,workflowName,event'
            ]
            
            if workflow:
                cmd.extend(['--workflow', workflow])
            
            if branch:
                cmd.extend(['--branch', branch])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                self._print_error(f"Failed to get workflow runs for {repo}: {result.stderr}")
                return []
            
            if not result.stdout:
                self._print_debug(f"No runs returned from {repo}")
                return []
            
            runs = json.loads(result.stdout)
            
            # Filter by creation time
            filtered_runs = [
                run for run in runs 
                if run.get('createdAt', '') >= created_filter
            ]
            
            self._print_debug(f"Found {len(runs)} total runs, {len(filtered_runs)} within lookback window from {repo}")
            
            return filtered_runs
            
        except subprocess.TimeoutExpired:
            self._print_error(f"Timeout while fetching workflow runs for {repo}")
            return []
        except Exception as e:
            self._print_error(f"Error fetching workflow runs for {repo}: {str(e)}")
            return []
    
    def _get_run_jobs(self, repo: str, run_id: int) -> List[Dict]:
        """
        Get jobs for a specific workflow run
        
        Args:
            repo: Repository in format "owner/repo"
            run_id: Workflow run ID
            
        Returns:
            List of job dictionaries
        """
        try:
            self._print_debug(f"Fetching job details for run {run_id} from {repo}")
            
            cmd = [
                'gh', 'run', 'view', str(run_id),
                '--repo', repo,
                '--json', 'jobs'
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return []
            
            if not result.stdout:
                return []
            
            data = json.loads(result.stdout)
            return data.get('jobs', [])
            
        except Exception as e:
            self._print_warning(f"Error fetching jobs for run {run_id}: {str(e)}")
            return []
    
    def _analyze_failure(self, repo: str, run: Dict) -> Dict:
        """
        Analyze a failed workflow run and gather details
        
        Args:
            repo: Repository in format "owner/repo"
            run: Workflow run dictionary
            
        Returns:
            Dictionary with failure analysis
        """
        run_id = run.get('databaseId')
        jobs = self._get_run_jobs(repo, run_id)
        
        failed_jobs = [
            job for job in jobs 
            if job.get('conclusion') in ['failure', 'cancelled', 'timed_out']
        ]
        
        analysis = {
            'run_id': run_id,
            'workflow': run.get('workflowName', run.get('name', 'Unknown')),
            'title': run.get('displayTitle', 'No title'),
            'branch': run.get('headBranch', 'unknown'),
            'url': run.get('url', ''),
            'conclusion': run.get('conclusion', 'unknown'),
            'created_at': run.get('createdAt', ''),
            'updated_at': run.get('updatedAt', ''),
            'event': run.get('event', 'unknown'),
            'failed_jobs': []
        }
        
        for job in failed_jobs:
            analysis['failed_jobs'].append({
                'name': job.get('name', 'Unknown'),
                'conclusion': job.get('conclusion', 'unknown'),
                'started_at': job.get('startedAt', ''),
                'completed_at': job.get('completedAt', '')
            })
        
        return analysis
    
    def _report_failure(self, repo: str, analysis: Dict):
        """
        Report a workflow failure
        
        Args:
            repo: Repository in format "owner/repo"
            analysis: Failure analysis dictionary
        """
        self._print_header(f"FAILURE DETECTED: {repo}")
        self._print_error(f"Workflow: {analysis['workflow']}")
        self._print_error(f"Run ID: {analysis['run_id']}")
        self._print_error(f"Title: {analysis['title']}")
        self._print_error(f"Branch: {analysis['branch']}")
        self._print_error(f"Conclusion: {analysis['conclusion']}")
        self._print_error(f"Event: {analysis['event']}")
        self._print_error(f"Created: {analysis['created_at']}")
        self._print_error(f"URL: {analysis['url']}")
        
        if analysis['failed_jobs']:
            self._print_error(f"\nFailed Jobs ({len(analysis['failed_jobs'])}):")
            for job in analysis['failed_jobs']:
                self._print_error(f"  - {job['name']} ({job['conclusion']})")
        else:
            self._print_warning("  (No job details available)")
        
        print()  # Empty line for readability
        self._log_to_file("\n")
    
    def _check_repository(self, repo_config: Dict) -> Dict:
        """
        Check a single repository for workflow failures
        
        Args:
            repo_config: Repository configuration dictionary
            
        Returns:
            Dictionary with check statistics
        """
        try:
            repo = self._parse_repository(repo_config['repository'])
        except ValueError as e:
            self._print_error(str(e))
            return {'checked': 0, 'failures': 0, 'new_failures': 0}
        
        workflow = repo_config.get('workflow')
        branch = repo_config.get('branch')
        
        # Build description for output
        desc_parts = [repo]
        if workflow:
            desc_parts.append(f"workflow:{workflow}")
        if branch:
            desc_parts.append(f"branch:{branch}")
        check_desc = " / ".join(desc_parts)
        
        self._print_debug(f"Checking {check_desc}")
        
        # Get key for tracking seen runs
        key = f"{repo}:{workflow or 'all'}:{branch or 'all'}"
        if key not in self.seen_runs:
            self.seen_runs[key] = set()
        
        # Fetch recent workflow runs
        runs = self._get_workflow_runs(repo, workflow, branch)
        
        stats = {
            'checked': len(runs),
            'failures': 0,
            'new_failures': 0
        }
        
        if not runs:
            self._print_debug(f"No runs to check for {check_desc}")
            return stats
        
        for run in runs:
            run_id = run.get('databaseId')
            status = run.get('status', '').lower()
            conclusion = run.get('conclusion', '').lower()
            
            # Skip runs that are still in progress - we'll check them next time
            if status != 'completed':
                self._print_debug(f"Run {run_id} is still in progress (status: {status}), will check next time")
                continue
            
            # Only process completed runs from here on
            # Check if this is a completed run with a failure
            if conclusion in ['failure', 'cancelled', 'timed_out']:
                stats['failures'] += 1
                
                # Check if this is a new failure we haven't seen before
                if run_id not in self.seen_runs[key]:
                    stats['new_failures'] += 1

                    # Analyze and report the failure
                    analysis = self._analyze_failure(repo, run)
                    self._report_failure(repo, analysis)

                    # Send Slack notification for the failure
                    try:
                        slack_rc = send_failure_notification(repo, analysis)
                        if slack_rc == 0:
                            self._print_debug("[SLACK] Failure notification sent successfully")
                        elif slack_rc not in (2, 3, 4):  # Ignore missing deps/config errors
                            self._print_debug(f"[SLACK] Notification failed with code {slack_rc}")
                    except Exception as e:
                        self._print_debug(f"[SLACK] Failed to send failure notification: {e}")

                # Mark failed run as seen
                self.seen_runs[key].add(run_id)
            elif conclusion == 'success':
                # Mark successful runs as seen (so we don't keep checking them)
                self.seen_runs[key].add(run_id)
            else:
                # Other conclusions (skipped, neutral, stale, action_required)
                # Mark as seen to avoid repeatedly checking
                if conclusion and run_id:
                    self.seen_runs[key].add(run_id)
        
        # Print summary for this repository check
        if stats['new_failures'] > 0:
            self._print_debug(f"Completed {check_desc}: {stats['checked']} runs checked, {stats['new_failures']} new failures")
        else:
            self._print_debug(f"Completed {check_desc}: {stats['checked']} runs checked, no new failures")
        
        return stats
    
    def monitor_once(self) -> Dict:
        """
        Perform a single monitoring check across all configured repositories
        
        Returns:
            Dictionary with overall statistics
        """
        repositories = self.config.get('repositories', [])
        
        if not repositories:
            self._print_warning("No repositories configured")
            return {'total_checked': 0, 'total_failures': 0, 'total_new_failures': 0}
        
        overall_stats = {
            'total_checked': 0,
            'total_failures': 0,
            'total_new_failures': 0
        }
        
        for repo_config in repositories:
            try:
                stats = self._check_repository(repo_config)
                overall_stats['total_checked'] += stats['checked']
                overall_stats['total_failures'] += stats['failures']
                overall_stats['total_new_failures'] += stats['new_failures']
            except Exception as e:
                self._print_error(f"Error checking repository: {str(e)}")
        
        # Save state after each check
        self._save_state()
        
        return overall_stats
    
    def monitor_continuously(self):
        """Run the monitor in continuous mode"""
        self._print_header("Workflow Monitor Starting")
        self._print_info(f"Repositories to monitor: {len(self.config.get('repositories', []))}")
        self._print_info(f"Poll interval: {self.poll_interval} seconds")
        self._print_info(f"Lookback window: {self.lookback_minutes} minutes")
        self._print_info(f"Max runs per check: {self.max_runs_per_check}")
        if self.state_file:
            self._print_info(f"State file: {self.state_file}")
        if self.log_file:
            self._print_info(f"Log file: {self.log_file.name}")

        # Send Slack startup notification
        try:
            startup_rc = send_startup_notification(self.config)
            if startup_rc == 0:
                self._print_info("[SLACK] Startup notification sent successfully")
            elif startup_rc == 3:
                self._print_debug("[SLACK] SLACK_BOT_TOKEN not set, skipping notification")
            elif startup_rc == 4:
                self._print_debug("[SLACK] SLACK_CHANNEL not set, skipping notification")
            elif startup_rc == 2:
                self._print_debug("[SLACK] slack-sdk not installed, skipping notification")
            else:
                self._print_warning(f"[SLACK] Startup notification failed with code {startup_rc}")
        except Exception as e:
            self._print_warning(f"[SLACK] Failed to send startup notification: {e}")

        check_count = 0
        
        try:
            while True:
                check_count += 1
                self._print_header(f"Check #{check_count} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                
                stats = self.monitor_once()
                
                # Print summary
                self._print_info(f"Checked {stats['total_checked']} workflow runs")
                if stats['total_new_failures'] > 0:
                    self._print_error(f"Found {stats['total_new_failures']} new failures!")
                else:
                    self._print_success("No new failures detected")
                
                # Wait before next check
                self._print_info(f"Next check in {self.poll_interval} seconds...")
                time.sleep(self.poll_interval)
                
        except KeyboardInterrupt:
            self._print_warning("\nMonitoring stopped by user")
            self._save_state()
            self._print_info(f"Completed {check_count} checks")
        finally:
            if self.log_file:
                self._log_to_file(f"{'='*80}\n")
                self._log_to_file(f"Workflow Monitor stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                self._log_to_file(f"{'='*80}\n")
                self.log_file.close()
    
    def monitor_single_check(self):
        """Run the monitor once and exit"""
        self._print_header("Workflow Monitor - Single Check")
        self._print_info(f"Repositories to check: {len(self.config.get('repositories', []))}")
        self._print_info(f"Lookback window: {self.lookback_minutes} minutes")
        if self.log_file:
            self._print_info(f"Log file: {self.log_file.name}")
        
        stats = self.monitor_once()
        
        # Print summary
        self._print_header("Check Complete")
        self._print_info(f"Total runs checked: {stats['total_checked']}")
        self._print_info(f"Total failures found: {stats['total_failures']}")
        
        # Clean up
        if self.log_file:
            self._log_to_file(f"{'='*80}\n")
            self._log_to_file(f"Check completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._log_to_file(f"{'='*80}\n")
            self.log_file.close()
        
        if stats['total_new_failures'] > 0:
            self._print_error(f"New failures detected: {stats['total_new_failures']}")
            return 1
        else:
            self._print_success("No new failures detected")
            return 0


# ============================================================================
# SLACK NOTIFICATIONS
# ============================================================================

def validate_slack_config() -> Optional[int]:
    """
    Validate Slack environment configuration.

    Returns:
        None if valid, or exit code if invalid/missing config
    """
    dry_run_flag = os.environ.get("SLACK_DRY_RUN")
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL")

    if not token and not dry_run_flag:
        return 3  # SLACK_NO_TOKEN

    if not channel and not dry_run_flag:
        return 4  # SLACK_NO_CHANNEL

    return None


def check_slack_dependencies() -> Optional[int]:
    """
    Check if Slack SDK dependencies are available.

    Returns:
        None if dependencies available, or exit code if missing
    """
    try:
        import importlib
        importlib.import_module('slack_sdk')
        importlib.import_module('urllib3')
        return None
    except Exception:
        dry_run_flag = os.environ.get("SLACK_DRY_RUN")

        if dry_run_flag:
            return None  # Allow dry-run to proceed
        else:
            return 2  # MISSING_DEPENDENCY


def send_slack_notification(
    title: str,
    message: str = "",
    status: str = "info",
    template: Optional[str] = None,
    template_vars: Optional[Dict[str, str]] = None
) -> int:
    """
    Send notification via Slack using companion notifier script.

    Respects SLACK_DRY_RUN, SLACK_BOT_TOKEN, SLACK_CHANNEL env vars.

    Args:
        title: Notification title
        message: Message body
        status: Status type (info, success, failure, warning)
        template: Template name (optional)
        template_vars: Template variables (optional)

    Returns:
        Exit code (0 = success, 3 = no token, 4 = no channel, etc.)
    """
    # Locate notifier script
    script_dir = Path(__file__).parent
    slack_script = (script_dir.parent / "slack-notifier" / "slack_notifier_sdk.py").resolve()

    # Validate configuration
    config_error = validate_slack_config()
    if config_error:
        return config_error

    if not slack_script.exists():
        return 2  # MISSING_DEPENDENCY

    # Check dependencies
    dep_error = check_slack_dependencies()
    if dep_error:
        return dep_error

    # Build command
    dry_run_flag = bool(os.environ.get("SLACK_DRY_RUN"))
    cmd = [sys.executable, str(slack_script), "--title", title, "--status", status]

    if message:
        cmd.extend(["--message", message])

    if dry_run_flag:
        cmd.append("--dry-run")

    if template:
        cmd.extend(["--template", template])

    if template_vars:
        for k, v in template_vars.items():
            if k is not None:
                cmd.extend(["--var", f"{k}={v}"])

    # Execute
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode
    except Exception:
        return 1


def send_startup_notification(config: Dict) -> int:
    """
    Send initial Slack notification when monitoring starts.

    Args:
        config: Configuration dictionary

    Returns:
        Notifier exit code
    """
    repositories = config.get('repositories', [])
    poll_interval = config.get('poll_interval', 60)
    lookback_minutes = config.get('lookback_minutes', 60)
    max_runs = config.get('max_runs_per_check', 100)

    title = "Workflow Monitor Starting"

    # Build repository list
    repo_list = []
    for repo_config in repositories:
        repo = repo_config.get('repository', 'Unknown')
        workflow = repo_config.get('workflow')
        branch = repo_config.get('branch')

        desc = repo
        if workflow:
            desc += f" (workflow: {workflow})"
        if branch:
            desc += f" (branch: {branch})"
        repo_list.append(f"• {desc}")

    repo_list_str = "\n".join(repo_list) if repo_list else "None configured"

    message = (
        f"*Monitoring {len(repositories)} repositories*\n\n"
        f"*Configuration:*\n"
        f"• Poll interval: {poll_interval}s\n"
        f"• Lookback window: {lookback_minutes} minutes\n"
        f"• Max runs per check: {max_runs}\n\n"
        f"*Repositories:*\n{repo_list_str}"
    )

    return send_slack_notification(
        title,
        message,
        status="info",
        template="simple",
        template_vars={
            "TIME": datetime.now().isoformat(),
            "REPO_COUNT": str(len(repositories))
        }
    )


def send_failure_notification(repo: str, analysis: Dict) -> int:
    """
    Send Slack notification for a workflow failure.

    Args:
        repo: Repository in format "owner/repo"
        analysis: Failure analysis dictionary

    Returns:
        Notifier exit code
    """
    title = f"Workflow Failure: {repo}"

    # Build failed jobs list
    failed_jobs_list = []
    for job in analysis.get('failed_jobs', []):
        job_name = job.get('name', 'Unknown')
        conclusion = job.get('conclusion', 'unknown')
        failed_jobs_list.append(f"• {job_name} ({conclusion})")

    failed_jobs_str = "\n".join(failed_jobs_list) if failed_jobs_list else "No job details available"

    message = (
        f"*Workflow:* {analysis['workflow']}\n"
        f"*Run ID:* {analysis['run_id']}\n"
        f"*Title:* {analysis['title']}\n"
        f"*Branch:* {analysis['branch']}\n"
        f"*Event:* {analysis['event']}\n"
        f"*Conclusion:* {analysis['conclusion']}\n"
        f"*Created:* {analysis['created_at']}\n\n"
        f"*Failed Jobs:*\n{failed_jobs_str}\n\n"
        f"*URL:* {analysis['url']}"
    )

    return send_slack_notification(
        title,
        message,
        status="failure",
        template="workflow_failure",
        template_vars={
            "WORKFLOW": analysis['workflow'],
            "REPO": repo,
            "BRANCH": analysis['branch'],
            "RUN_ID": str(analysis['run_id']),
            "URL": analysis['url']
        }
    )


def load_config(config_file: str) -> Dict:
    """Load configuration from YAML file"""
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        
        # Validate required fields
        if 'repositories' not in config:
            raise ValueError("Configuration must contain 'repositories' list")
        
        for idx, repo in enumerate(config['repositories']):
            if 'repository' not in repo:
                raise ValueError(f"Repository {idx} missing 'repository' field")
        
        return config
        
    except FileNotFoundError:
        print(f"{Colors.FAIL}Error: Configuration file '{config_file}' not found{Colors.ENDC}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"{Colors.FAIL}Error parsing YAML: {str(e)}{Colors.ENDC}")
        sys.exit(1)
    except ValueError as e:
        print(f"{Colors.FAIL}Configuration error: {str(e)}{Colors.ENDC}")
        sys.exit(1)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Monitor GitHub workflow runs for failures',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run continuous monitoring
  %(prog)s config.yaml
  
  # Run a single check and exit
  %(prog)s config.yaml --once
  
  # Use a custom state file
  %(prog)s config.yaml --state-file /tmp/monitor-state.json
  
  # Save output to log file
  %(prog)s config.yaml --log-file monitor.log

For more information, see README.md
        """
    )
    
    parser.add_argument(
        'config',
        help='Path to YAML configuration file'
    )
    
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run a single check and exit (default: continuous monitoring)'
    )
    
    parser.add_argument(
        '--state-file',
        default='.workflow-monitor-state.json',
        help='Path to state file for tracking seen runs (default: .workflow-monitor-state.json)'
    )
    
    parser.add_argument(
        '--log-file',
        help='Path to log file for saving output (optional, appends to file if exists)'
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    
    # Create monitor
    monitor = WorkflowMonitor(config, state_file=args.state_file, log_file=args.log_file)
    
    # Run monitor
    if args.once:
        exit_code = monitor.monitor_single_check()
        sys.exit(exit_code)
    else:
        monitor.monitor_continuously()


if __name__ == '__main__':
    main()
