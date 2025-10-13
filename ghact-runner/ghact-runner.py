#!/usr/bin/env python3
"""
ghact-runner.py

Clone multiple GitHub repositories, inject a workflow into each, and run it locally
with `act` (no commits/pushes to remote).

This tool is designed for batch-testing GitHub Actions workflows across many repos,
useful for pre-deployment validation, security audits, compliance checks, etc.

Requirements:
    - Python 3.8+
    - GitHub CLI (`gh`) installed & authenticated
    - Git
    - Docker
    - act (https://github.com/nektos/act)
    - PyYAML (`pip install pyyaml`)

Optional:
    - slack-sdk (for Slack notifications)

Usage:
    python ghact-runner.py --config repos.yml
    python ghact-runner.py --config repos.yml --dry-run
    python ghact-runner.py --config repos.yml --gh-path /usr/local/bin/gh
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Tuple

try:
    import yaml  # type: ignore
except ImportError as e:
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml\n"
        f"Import error: {e}",
        file=sys.stderr
    )
    sys.exit(1)


# ============================================================================
# CONSTANTS
# ============================================================================

class ExitCode:
    """Standard exit codes used throughout the script."""
    SUCCESS = 0
    FAILURE = 1
    MISSING_DEPENDENCY = 2
    SLACK_NO_TOKEN = 3
    SLACK_NO_CHANNEL = 4
    COMMAND_NOT_FOUND = 127


class SlackConfig:
    """Slack notification configuration constants."""
    MAX_FILES_BEFORE_ZIP = 20
    MAX_BYTES_BEFORE_ZIP = 8 * 1024 * 1024  # 8 MB
    NOTIFIER_SCRIPT_PATH = "../slack-notifier/slack_notifier_sdk.py"


class Messages:
    """UI messages and text constants."""
    BANNER_CHAR = "="
    BANNER_WIDTH = 80
    SUMMARY_CHAR = "#"


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class RepoSpec:
    """Specification for a repository to process."""
    url: str                      # org/repo, HTTPS URL, or SSH URL
    name: Optional[str] = None    # Optional custom directory name
    branch: Optional[str] = None  # Optional branch to clone


@dataclass
class Config:
    """Complete configuration for the script execution."""
    repos: List[RepoSpec]
    checkout_dir: Path
    workflow_file: Optional[Path]
    workflow_inline: Optional[str]
    workflow_filename: str
    act_event: str
    act_args: List[str]
    platform_mappings: Dict[str, str]
    continue_on_error: bool


@dataclass
class ExecutionResult:
    """Results from processing all repositories."""
    successes: List[str]
    failures: List[Tuple[str, int]]
    start_time: datetime
    end_time: datetime

    @property
    def duration(self) -> timedelta:
        """Calculate execution duration."""
        return self.end_time - self.start_time


# ============================================================================
# CONFIGURATION LOADING
# ============================================================================

def load_config(path: Path) -> Config:
    """
    Load and parse YAML configuration file.

    Args:
        path: Path to YAML config file

    Returns:
        Parsed Config object

    Raises:
        ValueError: If config is invalid or missing required fields
    """
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    repos_raw = raw.get("repos") or []
    if not repos_raw:
        raise ValueError("Config must include at least one repo under 'repos'.")

    def as_path(p):
        return Path(p).expanduser().resolve() if p else None

    repos: List[RepoSpec] = []
    for r in repos_raw:
        if isinstance(r, str):
            repos.append(RepoSpec(url=r))
        else:
            repos.append(
                RepoSpec(
                    url=r["url"],
                    name=r.get("name"),
                    branch=r.get("branch")
                )
            )

    return Config(
        repos=repos,
        checkout_dir=Path(raw["checkout_dir"]).expanduser().resolve(),
        workflow_file=as_path(raw.get("workflow_file")),
        workflow_inline=raw.get("workflow_inline"),
        workflow_filename=raw.get("workflow_filename", "local-ci.yml"),
        act_event=raw.get("act_event", "push"),
        act_args=list(raw.get("act_args", []) or []),
        platform_mappings=dict(raw.get("platform_mappings", {}) or {}),
        continue_on_error=bool(raw.get("continue_on_error", True)),
    )


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def which_or(custom: Optional[str], name: str) -> str:
    """
    Find executable path, either custom or from PATH.

    Args:
        custom: Custom path to executable (optional)
        name: Name of executable to find

    Returns:
        Absolute path to executable

    Raises:
        FileNotFoundError: If executable not found
    """
    if custom:
        p = Path(custom).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"{name} not found at {p}")
        return str(p)

    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"Required tool '{name}' not found on PATH.")
    return found


def repo_dir_name_from(url_or_slug: str) -> str:
    """
    Extract repository directory name from URL or slug.

    Args:
        url_or_slug: Repository URL or org/repo slug

    Returns:
        Directory name for the repository

    Examples:
        >>> repo_dir_name_from("https://github.com/org/repo.git")
        'repo'
        >>> repo_dir_name_from("org/myrepo")
        'myrepo'
    """
    last = url_or_slug.rstrip("/").split("/")[-1]
    return last[:-4] if last.endswith(".git") else last


def format_duration(duration: timedelta) -> str:
    """
    Format duration in human-readable form.

    Args:
        duration: Time duration

    Returns:
        Formatted string like "2h 15m 30s", "5m 12s", or "45s"
    """
    total_seconds = int(duration.total_seconds())
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"


def log_or_print(logger: Optional['RunLogger'], message: str) -> None:
    """
    Log message to logger if available, otherwise print to console.

    Args:
        logger: Optional RunLogger instance
        message: Message to log/print
    """
    if logger:
        logger.log(message)
    else:
        print(message)


# ============================================================================
# LOGGING
# ============================================================================

class RunLogger:
    """
    Dual-output logger that writes to both console and file.

    Streams subprocess output in real-time to both destinations.
    """

    def __init__(self, log_path: Path):
        """
        Initialize logger with output file.

        Args:
            log_path: Path to log file (parent dirs created if needed)
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = log_path.open("w", encoding="utf-8")
        self.path = log_path

    def log(self, msg: str) -> None:
        """
        Log a message, ensuring it ends with newline.

        Args:
            msg: Message to log
        """
        if not msg.endswith("\n"):
            msg += "\n"
        sys.stdout.write(msg)
        self._f.write(msg)
        self._f.flush()

    def write_stream(self, chunk: str) -> None:
        """
        Write raw stream chunk (already contains newlines).

        Args:
            chunk: Raw output chunk from subprocess
        """
        sys.stdout.write(chunk)
        self._f.write(chunk)
        self._f.flush()

    def close(self) -> None:
        """Close the log file."""
        try:
            self._f.close()
        except Exception:
            pass


# ============================================================================
# SUBPROCESS EXECUTION
# ============================================================================

def run(
    cmd: List[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    logger: Optional[RunLogger] = None
) -> int:
    """
    Run a command and return its exit code.

    If logger provided, streams output to both console and log file.

    Args:
        cmd: Command and arguments to execute
        cwd: Working directory (optional)
        env: Environment variables (optional)
        logger: Logger for output streaming (optional)

    Returns:
        Command exit code (0 = success)
    """
    if logger:
        logger.log(f"$ {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd) if cwd else None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                logger.write_stream(line)
            return proc.wait()
        except FileNotFoundError as e:
            logger.log(f"[ERROR] Command not found: {e}")
            return ExitCode.COMMAND_NOT_FOUND
    else:
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env
        ).returncode


# ============================================================================
# REPOSITORY OPERATIONS
# ============================================================================

def clone_with_gh(
    gh_path: str,
    spec: RepoSpec,
    dest_dir: Path,
    dry_run: bool,
    logger: Optional[RunLogger]
) -> None:
    """
    Clone repository using GitHub CLI.

    Uses shallow clone (--depth 1) for efficiency.

    Args:
        gh_path: Path to gh executable
        spec: Repository specification
        dest_dir: Destination directory for clone
        dry_run: If True, show command without executing
        logger: Optional logger for output

    Raises:
        RuntimeError: If clone fails
    """
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = [gh_path, "repo", "clone", spec.url, str(dest_dir), "--", "--depth", "1"]
    if spec.branch:
        cmd.extend(["-b", spec.branch])

    log_or_print(logger, f"==> Cloning with gh: {' '.join(cmd)}")

    if not dry_run:
        rc = run(cmd, logger=logger)
        if rc != 0:
            raise RuntimeError(f"gh repo clone failed for {spec.url}")


def update_existing_repo_with_gh(
    gh_path: str,
    spec: RepoSpec,
    repo_path: Path,
    dry_run: bool,
    logger: Optional[RunLogger]
) -> None:
    """
    Update existing repository using atomic temp-dir-swap strategy.

    Re-clones into temp directory, then atomically swaps with existing repo.
    This ensures clean state and avoids merge conflicts.

    Args:
        gh_path: Path to gh executable
        spec: Repository specification
        repo_path: Path to existing repository
        dry_run: If True, show commands without executing
        logger: Optional logger for output

    Raises:
        RuntimeError: If repo doesn't exist or update fails
    """
    if not repo_path.exists():
        raise RuntimeError(f"Repo path does not exist: {repo_path}")

    parent = repo_path.parent
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"{repo_path.name}.tmp-", dir=str(parent)))

    try:
        log_or_print(
            logger,
            f"==> Updating repo with gh (re-clone to temp, then swap): {spec.url}"
        )

        if dry_run:
            branch_arg = f"-b {spec.branch}" if spec.branch else ""
            log_or_print(
                logger,
                f"[dry-run] Would run: gh repo clone {spec.url} {tmp_dir} "
                f"-- --depth 1 {branch_arg}"
            )
            log_or_print(logger, f"[dry-run] Would replace {repo_path} with {tmp_dir}")
            return

        # Re-clone fresh copy
        cmd = [gh_path, "repo", "clone", spec.url, str(tmp_dir), "--", "--depth", "1"]
        if spec.branch:
            cmd.extend(["-b", spec.branch])

        rc = run(cmd, logger=logger)
        if rc != 0:
            raise RuntimeError(f"gh repo clone (update) failed for {spec.url}")

        # Atomic swap: remove old, rename temp to final
        log_or_print(logger, f"==> Replacing {repo_path} with fresh clone")
        if repo_path.exists():
            shutil.rmtree(repo_path)
        tmp_dir.rename(repo_path)

    except Exception:
        # Clean up temp dir on error
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def write_workflow(
    repo_path: Path,
    filename: str,
    from_file: Optional[Path],
    inline: Optional[str],
    dry_run: bool,
    logger: Optional[RunLogger]
) -> None:
    """
    Write workflow file into repository's .github/workflows directory.

    Args:
        repo_path: Path to repository
        filename: Workflow filename (e.g., "local-ci.yml")
        from_file: Path to external workflow file (optional)
        inline: Inline workflow YAML content (optional)
        dry_run: If True, show action without executing
        logger: Optional logger for output

    Raises:
        ValueError: If neither from_file nor inline provided
    """
    workflows = repo_path / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    dest = workflows / filename

    log_or_print(logger, f"==> Writing workflow: {dest}")

    if dry_run:
        return

    if from_file:
        dest.write_text(from_file.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        if not inline:
            raise ValueError(
                "workflow_inline is empty and no workflow_file provided."
            )
        dest.write_text(inline, encoding="utf-8")


# ============================================================================
# ACT EXECUTION
# ============================================================================

def run_act(
    repo_path: Path,
    act_path: str,
    event: str,
    workflow_filename: str,
    extra_args: List[str],
    platform_map: Dict[str, str],
    dry_run: bool,
    logger: Optional[RunLogger]
) -> int:
    """
    Run workflow using act (GitHub Actions emulator).

    Args:
        repo_path: Path to repository
        act_path: Path to act executable
        event: GitHub event to trigger (e.g., "push")
        workflow_filename: Workflow file to run
        extra_args: Additional arguments for act
        platform_map: Platform label to Docker image mappings
        dry_run: If True, show command without executing
        logger: Optional logger for output

    Returns:
        Exit code from act (0 = success)
    """
    cmd = [act_path, "-W", f".github/workflows/{workflow_filename}", event]

    # Add platform mappings
    for label, image in platform_map.items():
        cmd.extend(["-P", f"{label}={image}"])

    # Add extra arguments
    cmd.extend(extra_args)

    log_or_print(logger, f"==> Running: {' '.join(cmd)} (cwd={repo_path})")

    if dry_run:
        return ExitCode.SUCCESS

    return run(cmd, cwd=repo_path, env=os.environ.copy(), logger=logger)


# ============================================================================
# SLACK NOTIFICATIONS - VALIDATION
# ============================================================================

def validate_slack_config(logger: Optional[RunLogger] = None) -> Optional[int]:
    """
    Validate Slack environment configuration.

    Args:
        logger: Optional logger for messages

    Returns:
        None if valid, or exit code if invalid/missing config
    """
    dry_run_flag = os.environ.get("SLACK_DRY_RUN")
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL")

    if not token and not dry_run_flag:
        log_or_print(
            logger,
            "[SLACK] SLACK_BOT_TOKEN not defined: skipping Slack notification"
        )
        return ExitCode.SLACK_NO_TOKEN

    if not channel and not dry_run_flag:
        log_or_print(
            logger,
            "[SLACK] SLACK_CHANNEL not defined: skipping Slack notification"
        )
        return ExitCode.SLACK_NO_CHANNEL

    return None


def check_slack_dependencies(logger: Optional[RunLogger] = None) -> Optional[int]:
    """
    Check if Slack SDK dependencies are available.

    Args:
        logger: Optional logger for messages

    Returns:
        None if dependencies available, or exit code if missing
    """
    try:
        import importlib
        importlib.import_module('slack_sdk')
        importlib.import_module('urllib3')
        return None
    except Exception as e:
        dry_run_flag = os.environ.get("SLACK_DRY_RUN")

        if dry_run_flag:
            log_or_print(
                logger,
                f"[SLACK] (dry-run) Dependencies missing ({e}); "
                "simulating success"
            )
            return None  # Allow dry-run to proceed
        else:
            log_or_print(
                logger,
                f"[SLACK] Dependencies missing: {e}. "
                "Install 'slack-sdk' to enable notifications."
            )
            return ExitCode.MISSING_DEPENDENCY


# ============================================================================
# SLACK NOTIFICATIONS - FILE HANDLING
# ============================================================================

def prepare_file_attachments(
    files: Optional[List[str]],
    logger: Optional[RunLogger] = None
) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Prepare files for Slack attachment, zipping if needed.

    If there are many files or they're large, creates a ZIP archive.

    Args:
        files: List of file paths to attach
        logger: Optional logger for messages

    Returns:
        Tuple of (prepared_file_list, message_suffix)
        where message_suffix describes the ZIP if created
    """
    if not files:
        return None, None

    files_list = [str(f) for f in files if f]
    if not files_list:
        return None, None

    # Calculate total size
    try:
        total_bytes = sum(
            Path(p).stat().st_size if Path(p).is_file() else 0
            for p in files_list
        )
    except Exception:
        total_bytes = 0

    # Check if we should zip
    should_zip = (
        len(files_list) > SlackConfig.MAX_FILES_BEFORE_ZIP or
        total_bytes > SlackConfig.MAX_BYTES_BEFORE_ZIP
    )

    if not should_zip:
        return files_list, None

    # Create ZIP archive
    log_or_print(
        logger,
        f"[SLACK] Many/large files ({len(files_list)} files, "
        f"{total_bytes} bytes) — creating ZIP"
    )

    try:
        tmp = tempfile.NamedTemporaryFile(
            prefix="ghact-logs-",
            suffix=".zip",
            delete=False
        )
        tmp_name = tmp.name
        tmp.close()

        with zipfile.ZipFile(tmp_name, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in files_list:
                try:
                    if Path(p).is_file():
                        arcname = os.path.relpath(p, start=str(Path('..').resolve()))
                        zf.write(p, arcname=arcname)
                except Exception:
                    continue

        zip_basename = os.path.basename(tmp_name)
        message_suffix = f"\n\nLogs attached as ZIP file: {zip_basename}"
        return [tmp_name], message_suffix

    except Exception as e:
        log_or_print(logger, f"[SLACK] Error creating ZIP: {e}")
        return files_list, None  # Fallback to original list


def cleanup_temp_file(file_path: str, dry_run: bool = False) -> None:
    """
    Clean up temporary file.

    Args:
        file_path: Path to temporary file
        dry_run: If True, don't actually delete
    """
    if dry_run:
        return

    try:
        os.unlink(file_path)
    except Exception:
        pass


# ============================================================================
# SLACK NOTIFICATIONS - COMMAND BUILDING
# ============================================================================

def build_slack_command(
    slack_script: Path,
    title: str,
    message: str,
    status: str,
    files_list: Optional[List[str]],
    template: Optional[str],
    template_vars: Optional[Dict[str, str]],
    dry_run_flag: bool
) -> List[str]:
    """
    Build command line for Slack notifier script.

    Args:
        slack_script: Path to slack_notifier_sdk.py
        title: Notification title
        message: Notification message body
        status: Status type (info, success, failure)
        files_list: Files to attach (optional)
        template: Template name (optional)
        template_vars: Template variables (optional)
        dry_run_flag: Whether SLACK_DRY_RUN is set

    Returns:
        Complete command as list of strings
    """
    cmd = [sys.executable, str(slack_script), "--title", title, "--status", status]

    if message:
        cmd.extend(["--message", message])

    if files_list:
        cmd.append("--files")
        cmd.extend(files_list)

    if dry_run_flag:
        cmd.append("--dry-run")

    if template:
        cmd.extend(["--template", template])

    if template_vars:
        for k, v in template_vars.items():
            if k is not None:
                cmd.extend(["--var", f"{k}={v}"])

    return cmd


# ============================================================================
# SLACK NOTIFICATIONS - MAIN FUNCTION
# ============================================================================

def send_slack_notification(
    title: str,
    message: str = "",
    status: str = "info",
    files: Optional[List[str]] = None,
    logger: Optional[RunLogger] = None,
    template: Optional[str] = None,
    template_vars: Optional[Dict[str, str]] = None
) -> int:
    """
    Send notification via Slack using companion notifier script.

    Automatically zips large/many files before attaching.
    Respects SLACK_DRY_RUN, SLACK_BOT_TOKEN, SLACK_CHANNEL env vars.

    Args:
        title: Notification title
        message: Message body
        status: Status type (info, success, failure)
        files: Files to attach (optional)
        logger: Optional logger for output
        template: Template name (optional)
        template_vars: Template variables (optional)

    Returns:
        Exit code (0 = success, 3 = no token, 4 = no channel, etc.)
    """
    # Locate notifier script
    script_dir = Path(__file__).parent
    slack_script = (script_dir.parent / "slack-notifier" / "slack_notifier_sdk.py").resolve()

    # Validate configuration
    config_error = validate_slack_config(logger)
    if config_error:
        return config_error

    if not slack_script.exists():
        log_or_print(logger, f"[SLACK] Notifier SDK not found at: {slack_script}")
        return ExitCode.MISSING_DEPENDENCY

    # Check dependencies
    dep_error = check_slack_dependencies(logger)
    if dep_error:
        return dep_error

    # Prepare file attachments
    files_list, zip_message = prepare_file_attachments(files, logger)
    if zip_message:
        message = (message + zip_message).strip()

    # Build command
    dry_run_flag = bool(os.environ.get("SLACK_DRY_RUN"))
    cmd = build_slack_command(
        slack_script, title, message, status,
        files_list, template, template_vars, dry_run_flag
    )

    # Execute
    log_or_print(logger, f"[SLACK] Running notifier: {' '.join(cmd)}")
    rc = run(cmd, logger=logger)

    # Cleanup temp ZIP if created
    if files_list and len(files_list) == 1 and files_list[0].endswith('.zip'):
        cleanup_temp_file(files_list[0], dry_run=dry_run_flag)

    log_or_print(logger, f"[SLACK] Notifier exit code: {rc}")
    return rc


# ============================================================================
# SLACK NOTIFICATIONS - SPECIALIZED SENDERS
# ============================================================================

def send_startup_notification(
    cfg: Config,
    logger: Optional[RunLogger] = None
) -> int:
    """
    Send initial Slack notification with repository list.

    Args:
        cfg: Configuration object
        logger: Optional logger

    Returns:
        Notifier exit code
    """
    title = ":rocket: Starting ghact-runner.py"
    total_repos = len(cfg.repos)
    time_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    message = (
        f"*Running on {total_repos} repositories*\n\n"
        f"Full repository list attached to this message.\n\n"
        f"*Start time:* {time_str}\n\n"
        f"Logs will be collected and attached when complete."
    )

    # Create temporary repo list file
    repo_list_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(
            prefix="ghact-repos-",
            suffix=".txt",
            delete=False,
            mode="w",
            encoding="utf-8"
        )
        repo_list_path = tmp.name

        for r in cfg.repos:
            line = r.url if not r.name else f"{r.url} \t {r.name}"
            tmp.write(line + "\n")
        tmp.close()

    except Exception as e:
        log_or_print(logger, f"[SLACK] Could not create repo list file: {e}")
        repo_list_path = None

    files = [repo_list_path] if repo_list_path else None

    try:
        return send_slack_notification(
            title,
            message,
            status="info",
            files=files,
            logger=logger,
            template="simple",
            template_vars={
                "TIME": datetime.now().isoformat(),
                "REPO_COUNT": str(total_repos)
            }
        )
    finally:
        if repo_list_path:
            cleanup_temp_file(repo_list_path)


def send_completion_notification(
    result: ExecutionResult,
    logs_root: Path,
    logger: Optional[RunLogger] = None
) -> int:
    """
    Send final Slack notification with execution results and logs.

    Args:
        result: Execution results
        logs_root: Path to logs directory
        logger: Optional logger

    Returns:
        Notifier exit code
    """
    # Collect log files
    files_to_attach: List[str] = []
    if logs_root.exists():
        for p in sorted(logs_root.rglob('*')):
            if p.is_file():
                files_to_attach.append(str(p))

    # Zip log files
    zip_path = None
    if files_to_attach:
        try:
            tmp = tempfile.NamedTemporaryFile(
                prefix="ghact-logs-",
                suffix=".zip",
                delete=False
            )
            zip_path = tmp.name
            tmp.close()

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for p in files_to_attach:
                    try:
                        ppath = Path(p)
                        arcname = ppath.relative_to(logs_root)
                        zf.write(p, arcname=str(arcname))
                    except Exception:
                        continue

            files_to_attach = [zip_path]
        except Exception as e:
            print(f"WARNING: Could not create logs ZIP: {e}", file=sys.stderr)

    # Create success/failure list files
    success_file = None
    failure_file = None
    try:
        # Success list
        sf = tempfile.NamedTemporaryFile(
            prefix="ghact-successes-",
            suffix=".txt",
            delete=False,
            mode="w",
            encoding="utf-8"
        )
        success_file = sf.name
        for url in result.successes:
            sf.write(url + "\n")
        sf.close()

        # Failure list
        ff = tempfile.NamedTemporaryFile(
            prefix="ghact-failures-",
            suffix=".txt",
            delete=False,
            mode="w",
            encoding="utf-8"
        )
        failure_file = ff.name
        for url, rc in result.failures:
            ff.write(f"{url}\t{rc}\n")
        ff.close()

        # Attach both lists
        if success_file:
            files_to_attach.append(success_file)
        if failure_file:
            files_to_attach.append(failure_file)

    except Exception as e:
        print(f"WARNING: Could not create success/failure lists: {e}", file=sys.stderr)

    # Build notification message
    status = "success" if not result.failures else "failure"
    title = f"ghact-runner.py: Result ({'OK' if status == 'success' else 'FAIL'})"

    duration_str = format_duration(result.duration)
    time_str = result.end_time.strftime("%Y-%m-%d %H:%M:%S")

    details = []
    emoji = ":white_check_mark:" if not result.failures else ":x:"
    details.append(f"*Execution finished* {emoji}")
    details.append(f"*Time:* {time_str}")
    details.append(f"*Duration:* {duration_str}")
    details.append(f"*Successes:* {len(result.successes)}")
    details.append(f"*Failures:* {len(result.failures)}")

    if files_to_attach:
        details.append("\nComplete logs attached as ZIP.")
        details.append("Success/failure lists attached as text files.")

    message = "\n".join(details)

    # Send notification
    try:
        return send_slack_notification(
            title,
            message,
            status=status,
            files=files_to_attach or None,
            logger=logger,
            template="workflow_success" if status == "success" else "workflow_failure",
            template_vars={
                "TIME": datetime.now().isoformat(),
                "SUCCESS_COUNT": str(len(result.successes)),
                "FAILURE_COUNT": str(len(result.failures))
            }
        )
    finally:
        # Cleanup temp files
        if zip_path:
            cleanup_temp_file(zip_path)
        if success_file:
            cleanup_temp_file(success_file)
        if failure_file:
            cleanup_temp_file(failure_file)


# ============================================================================
# ENVIRONMENT SETUP
# ============================================================================

def verify_required_tools(
    gh_path: Optional[str],
    act_path: Optional[str]
) -> Tuple[str, str]:
    """
    Verify all required tools are available.

    Args:
        gh_path: Custom path to gh (optional)
        act_path: Custom path to act (optional)

    Returns:
        Tuple of (gh_executable_path, act_executable_path)

    Raises:
        FileNotFoundError: If any required tool is missing
    """
    gh = which_or(gh_path, "gh")
    act = which_or(act_path, "act")
    which_or(None, "docker")  # Just verify presence
    which_or(None, "git")     # Just verify presence

    return gh, act


def initialize_logs_directory(logs_root: Path) -> None:
    """
    Clear and initialize logs directory.

    Args:
        logs_root: Path to logs directory
    """
    try:
        if logs_root.exists():
            # Remove all children but keep the directory
            for child in logs_root.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                except Exception:
                    continue
        else:
            logs_root.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(
            f"WARNING: Could not fully clear logs directory {logs_root}: {e}",
            file=sys.stderr
        )


# ============================================================================
# REPOSITORY PROCESSING
# ============================================================================

def process_repository(
    spec: RepoSpec,
    cfg: Config,
    gh: str,
    act: str,
    logs_root: Path,
    dry_run: bool
) -> Tuple[bool, int]:
    """
    Process a single repository: clone/update, inject workflow, run act.

    Args:
        spec: Repository specification
        cfg: Configuration
        gh: Path to gh executable
        act: Path to act executable
        logs_root: Path to logs directory
        dry_run: Dry-run mode flag

    Returns:
        Tuple of (success: bool, exit_code: int)
    """
    # Setup paths and logger
    dirname = spec.name or repo_dir_name_from(spec.url)
    repo_path = cfg.checkout_dir / dirname
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = logs_root / dirname / f"{timestamp}.log"
    logger = RunLogger(log_path)

    try:
        # Banner
        logger.log(Messages.BANNER_CHAR * Messages.BANNER_WIDTH)
        logger.log(f"Processing: {spec.url}")
        logger.log(f"Log file: {log_path}")

        # Clone or update
        if repo_path.exists():
            update_existing_repo_with_gh(gh, spec, repo_path, dry_run, logger)
        else:
            clone_with_gh(gh, spec, repo_path, dry_run, logger)

        # Inject workflow
        write_workflow(
            repo_path,
            cfg.workflow_filename,
            cfg.workflow_file,
            cfg.workflow_inline,
            dry_run,
            logger
        )

        # Run act
        rc = run_act(
            repo_path,
            act_path=act,
            event=cfg.act_event,
            workflow_filename=cfg.workflow_filename,
            extra_args=cfg.act_args,
            platform_map=cfg.platform_mappings,
            dry_run=dry_run,
            logger=logger
        )

        if rc == 0:
            logger.log(f"✅ act run succeeded for {spec.url}")
            return True, rc
        else:
            logger.log(f"❌ act run FAILED (exit {rc}) for {spec.url}")
            return False, rc

    except Exception as e:
        logger.log(f"❌ ERROR for {spec.url}: {e}")
        return False, -1
    finally:
        logger.close()


# ============================================================================
# REPORTING
# ============================================================================

def print_summary(result: ExecutionResult) -> None:
    """
    Print execution summary to console.

    Args:
        result: Execution results
    """
    print("\n" + Messages.SUMMARY_CHAR * Messages.BANNER_WIDTH)
    print("Summary")
    print(f"  Duration: {format_duration(result.duration)}")
    print(f"  Successes: {len(result.successes)}")
    for url in result.successes:
        print(f"    - {url}")
    print(f"  Failures: {len(result.failures)}")
    for url, rc in result.failures:
        print(f"    - {url} (exit {rc})")


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main() -> None:
    """Main entry point for the script."""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Clone repos with gh, inject workflow, and run via act."
    )
    parser.add_argument(
        "--config",
        default="repos.yml",
        help="Path to YAML config file"
    )
    parser.add_argument(
        "--gh-path",
        default=None,
        help="Path to gh executable (if not on PATH)"
    )
    parser.add_argument(
        "--act-path",
        default=None,
        help="Path to act executable (if not on PATH)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes"
    )
    args = parser.parse_args()

    # Load configuration
    cfg = load_config(Path(args.config).expanduser().resolve())

    # Record start time
    start_time = datetime.now().astimezone()

    # Verify tools
    try:
        gh, act = verify_required_tools(args.gh_path, args.act_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(ExitCode.MISSING_DEPENDENCY)

    print(f"Using gh: {gh}")
    print(f"Using act: {act}")
    print(f"Checkout dir: {cfg.checkout_dir}")

    # Initialize results tracking
    successes: List[str] = []
    failures: List[Tuple[str, int]] = []

    # Setup logs
    logs_root = Path("logs").resolve()
    initialize_logs_directory(logs_root)

    # Send startup notification
    try:
        startup_rc = send_startup_notification(cfg)
        if startup_rc not in (0, ExitCode.SLACK_NO_TOKEN, ExitCode.SLACK_NO_CHANNEL):
            print(f"[SLACK] Startup notification returned: {startup_rc}")
    except Exception as e:
        print(f"[SLACK] Failed to send startup notification: {e}")

    # Process each repository
    for spec in cfg.repos:
        success, rc = process_repository(
            spec, cfg, gh, act, logs_root, args.dry_run
        )

        if success:
            successes.append(spec.url)
        else:
            failures.append((spec.url, rc))
            if not cfg.continue_on_error:
                break

    # Record end time and create result
    end_time = datetime.now().astimezone()
    result = ExecutionResult(
        successes=successes,
        failures=failures,
        start_time=start_time,
        end_time=end_time
    )

    # Print summary
    print_summary(result)

    # Send completion notification
    try:
        final_rc = send_completion_notification(result, logs_root)
        if final_rc not in (0, ExitCode.SLACK_NO_TOKEN, ExitCode.SLACK_NO_CHANNEL):
            print(f"[SLACK] Completion notification returned: {final_rc}")
    except Exception as e:
        print(f"[SLACK] Failed to send completion notification: {e}")

    # Exit with appropriate code
    sys.exit(ExitCode.SUCCESS if not failures else ExitCode.FAILURE)


if __name__ == "__main__":
    main()
