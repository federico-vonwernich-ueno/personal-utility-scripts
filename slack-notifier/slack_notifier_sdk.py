#!/usr/bin/env python3
"""
Slack notifier using the official Slack SDK.

Usage:
    python slack_notifier_sdk.py --title "Hello" --message "Text" --files a.txt --channel C12345678 --token xoxb-...

Environment: SLACK_BOT_TOKEN, SLACK_CHANNEL
Dependencies: slack-sdk
"""

from __future__ import annotations

import os
import sys
import time
import json
import mimetypes
from typing import List, Optional, Dict
from pathlib import Path

import urllib3
from urllib3.exceptions import InsecureRequestWarning
import ssl

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import traceback


DEFAULT_UPLOAD_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0
FILE_LINKS_LABEL = "Archivos:"
STATUS_ICONS = {
    "SUCCESS": ":white_check_mark:",
    "FAILURE": ":x:",
    "ERROR": ":x:",
    "WARNING": ":warning:",
    "INFO": ":information_source:",
    "DEBUG": ":mag:",
}


class TemplateProcessor:
    """Template loading and variable substitution."""

    @staticmethod
    def load_template(template_arg: str) -> Optional[Dict]:
        """Load template from path or name (searches ./templates for base names)."""
        if not template_arg:
            return None
        p = Path(template_arg)
        if not p.exists():
            templates_dir = Path(__file__).parent / "templates"
            for ext in (".json", ".yml", ".yaml"):
                cand = templates_dir / f"{template_arg}{ext}"
                if cand.exists():
                    p = cand
                    break
        if not p.exists():
            print(f"Template not found: {template_arg}", file=sys.stderr)
            return None
        try:
            text = p.read_text(encoding="utf-8")
            try:
                return json.loads(text)
            except Exception:
                try:
                    import yaml  # type: ignore
                    return yaml.safe_load(text)
                except Exception as e:
                    print(f"Failed to parse template {p}: {e}", file=sys.stderr)
                    return None
        except Exception as e:
            print(f"Failed to load template {p}: {e}", file=sys.stderr)
            return None

    @staticmethod
    def apply_variables(obj, vars_map: Dict[str, str]):
        """Replace {{VAR}} placeholders recursively."""
        if isinstance(obj, dict):
            return {k: TemplateProcessor.apply_variables(v, vars_map) for k, v in obj.items()}
        if isinstance(obj, list):
            return [TemplateProcessor.apply_variables(v, vars_map) for v in obj]
        if isinstance(obj, str):
            out = obj
            for k, v in vars_map.items():
                out = out.replace(f"{{{{{k}}}}}", str(v))
            return out
        return obj

    @staticmethod
    def prune_empty_blocks(template_dict: Dict) -> Dict:
        """Remove empty section blocks."""
        try:
            blocks = template_dict.get("blocks") if isinstance(template_dict, dict) else None
            if not isinstance(blocks, list):
                return template_dict
            new_blocks = []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "section":
                    txt_obj = b.get("text")
                    if isinstance(txt_obj, dict):
                        raw_text = txt_obj.get("text", "")
                        if not raw_text or not str(raw_text).strip():
                            continue
                new_blocks.append(b)
            template_dict["blocks"] = new_blocks
        except Exception:
            pass
        return template_dict

    @staticmethod
    def extract_blocks_and_args(template_dict: Dict) -> tuple[Optional[List[Dict]], Dict]:
        """Extract blocks and chat_postMessage args from template."""
        if not template_dict:
            return None, {}
        blocks = template_dict.get("blocks") if isinstance(template_dict, dict) else None
        extra = {}
        for k in ("username", "icon_emoji", "icon_url", "mrkdwn"):
            if isinstance(template_dict, dict) and k in template_dict:
                extra[k] = template_dict[k]
        return blocks, extra

    @staticmethod
    def get_status_icon(status: str) -> str:
        """Return emoji icon for status."""
        return STATUS_ICONS.get(status.upper(), ":speech_balloon:")


class ConfigLoader:
    """Configuration file loading and precedence handling."""

    @staticmethod
    def load_config_file(config_path: str) -> Dict:
        """Load config from JSON or YAML file."""
        if not os.path.isfile(config_path):
            print(f"Config file not found: {config_path}", file=sys.stderr, flush=True)
            sys.exit(2)
        try:
            try:
                import yaml
                with open(config_path, "r") as fh:
                    return yaml.safe_load(fh) or {}
            except Exception:
                with open(config_path, "r") as fh:
                    return json.load(fh) or {}
        except Exception as e:
            print(f"Failed to load config file {config_path}: {e}", file=sys.stderr, flush=True)
            traceback.print_exc()
            sys.exit(2)

    @staticmethod
    def pick_bool(arg_val, cfg: Dict, cfg_key: str, default: bool = False, invert_cfg_key: Optional[str] = None) -> bool:
        """Determine boolean value with precedence: CLI arg > config file > default."""
        if arg_val is not None:
            return bool(arg_val)
        if cfg_key in cfg:
            return bool(cfg.get(cfg_key))
        if invert_cfg_key and (invert_cfg_key in cfg):
            return not bool(cfg.get(invert_cfg_key))
        return bool(default)


class SlackNotifierSDK:
    """Slack notifier using slack_sdk.WebClient."""

    def __init__(self, token: Optional[str] = None, channel: Optional[str] = None, verbose: bool = False, verify_tls: bool = True, dry_run: bool = False):
        token = token or os.environ.get("SLACK_BOT_TOKEN")
        self.token = token
        self.channel = channel or os.environ.get("SLACK_CHANNEL")
        self.verbose = verbose
        self.dry_run = bool(dry_run)

        # Configure TLS verification (disable only for testing)
        self._verify_tls = bool(verify_tls)
        if not self._verify_tls:
            ssl._create_default_https_context = ssl._create_unverified_context
            urllib3.disable_warnings(InsecureRequestWarning)

        self.client = WebClient(token=token) if token and not self.dry_run else (WebClient(token=token) if token else None)

    def _log(self, *args, **kwargs):
        if self.verbose:
            print(*args, **kwargs, file=sys.stderr)

    def _log_info(self, msg: str):
        print(msg, file=sys.stderr, flush=True)

    def _log_debug(self, msg: str):
        if self.verbose:
            print(f"(verbose) {msg}", file=sys.stderr)

    def _safe_response_get(self, response, key: str, default=None):
        if hasattr(response, "get"):
            return response.get(key, default)
        return getattr(response, key, default)

    def _extract_slack_error(self, exception: SlackApiError) -> dict:
        try:
            resp = getattr(exception, "response", None)
            if resp is None:
                return {"error": str(exception)}

            data = getattr(resp, "data", None) or (
                resp._body if hasattr(resp, '_body') else None
            ) or (resp if isinstance(resp, dict) else None)

            status = getattr(resp, "status_code", None) or getattr(resp, "status", None)
            error_msg = data.get("error") if isinstance(data, dict) else None

            return {
                "status": status,
                "error": error_msg,
                "data": data
            }
        except Exception:
            return {"error": repr(exception)}

    def _log_api_response(self, method: str, response):
        try:
            ok_flag = self._safe_response_get(response, "ok")
            if method == "files_upload_v2":
                file_obj = self._safe_response_get(response, "file") or {}
                file_id = file_obj.get("id") if isinstance(file_obj, dict) else None
                self._log_info(f"{method}: ok={ok_flag}, file_id={file_id}")
            elif method == "chat_postMessage":
                channel = self._safe_response_get(response, "channel")
                ts = self._safe_response_get(response, "ts")
                self._log_info(f"{method}: ok={ok_flag}, channel={channel}, ts={ts}")
            elif method == "auth_test":
                user = self._safe_response_get(response, "user")
                team = self._safe_response_get(response, "team")
                self._log_info(f"{method}: ok={ok_flag}, user={user}, team={team}")
            else:
                self._log_info(f"{method}: ok={ok_flag}")
        except Exception:
            pass

    def test_connection(self) -> bool:
        """Validate token and connectivity via auth_test."""
        if self.dry_run:
            self._log_info("(dry-run) auth_test: simulated ok")
            return True

        if not self.client:
            self._log_info("No Slack client configured (missing token)")
            return False

        try:
            resp = self.client.auth_test()
            self._log_api_response("auth_test", resp)
            return bool(self._safe_response_get(resp, "ok"))
        except Exception as e:
            self._log_info(f"auth_test failed: {e}")
            traceback.print_exc()
            return False

    def resolve_channel_id(self, channel: Optional[str]) -> Optional[str]:
        """Resolve channel name to ID (C.../G.../D...)."""
        if not channel:
            return None

        ch = channel.strip()
        if ch.startswith("C") or ch.startswith("G") or ch.startswith("D"):
            return ch

        if ch.startswith("#"):
            ch = ch[1:]

        if not self.client:
            return None

        try:
            cursor = None
            while True:
                params = {"limit": 1000, "types": "public_channel,private_channel"}
                if cursor:
                    params["cursor"] = cursor
                resp = self.client.conversations_list(**params)
                channels = resp.get("channels") or []
                for c in channels:
                    if c.get("name") == ch or c.get("name_normalized") == ch:
                        return c.get("id")
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except SlackApiError as e:
            err_info = self._extract_slack_error(e)
            self._log_info(f"conversations_list failed: {err_info}")
            return None
        except Exception as e:
            self._log_info(f"Error while resolving channel '{channel}': {e}")
            return None

        return None

    def ensure_bot_in_channel(self, channel_id: str) -> bool:
        """Ensure bot is a member; auto-join public channels if needed."""
        if not channel_id or not self.client:
            return False

        try:
            info = self.client.conversations_info(channel=channel_id)
            ch = info.get("channel") or {}
            is_member = ch.get("is_member") or ch.get("is_member", False)
            is_private = ch.get("is_private") or False
            if is_member:
                return True

            if not is_private:
                try:
                    self._log_debug(f"Attempting to join channel {channel_id}")
                    jresp = self.client.conversations_join(channel=channel_id)
                    if self._safe_response_get(jresp, "ok"):
                        self._log_info(f"Joined channel {channel_id}")
                        return True
                except SlackApiError as e:
                    err_info = self._extract_slack_error(e)
                    self._log_info(f"Could not auto-join channel {channel_id}: {err_info}")
                    traceback.print_exc()
                    return False

            self._log_info(f"Bot is not a member of channel {channel_id}. For private channels invite the bot; for public channels ensure the bot can join.")
            return False
        except SlackApiError as e:
            err_info = self._extract_slack_error(e)
            self._log_info(f"conversations_info failed for {channel_id}: {err_info}")
            traceback.print_exc()
            return False
        except Exception as e:
            self._log_info(f"Error checking channel membership for {channel_id}: {e}")
            traceback.print_exc()
            return False

    def upload_files(self, files: List[str], channels: Optional[str] = None, initial_comment: Optional[str] = None, thread_ts: Optional[str] = None) -> List[Dict[str, Optional[str]]]:
        """Upload files to Slack; returns list of {"path", "id", "permalink"}."""
        if not self.client:
            raise RuntimeError("Slack WebClient not configured. Provide a bot token (SLACK_BOT_TOKEN or --token).")

        uploaded = []
        chan = channels or self.channel
        resolved_chan = None
        if chan:
            resolved_chan = self.resolve_channel_id(chan)
            if not resolved_chan:
                self._log_info(f"Channel not found or inaccessible to the bot: '{chan}'. Try using the channel ID (C...) or ensure the bot is a member.")
                resolved_chan = chan
            else:
                self._log_info(f"Resolved channel '{chan}' -> '{resolved_chan}'")

        if resolved_chan:
            try:
                in_channel = self.ensure_bot_in_channel(resolved_chan)
                if not in_channel:
                    self._log_info(f"Cannot upload files because the bot is not a member of channel {resolved_chan}.")
                    self._log_info("If this is a private channel, invite the app/bot to the channel. If public, ensure the bot has permission to join.")
                    return uploaded
            except Exception as e:
                self._log_info(f"Error while ensuring bot membership in channel {resolved_chan}: {e}")
                traceback.print_exc()
                return uploaded

        attach_comment_first = bool(initial_comment)
        for path in files:
            p = Path(path)
            if not p.is_file():
                self._log_info(f"File not found, skipping: {path}")
                continue

            attempt = 0
            last_exc = None
            while attempt < DEFAULT_UPLOAD_RETRIES:
                try:
                    if self.dry_run:
                        self._log_info(f"(dry-run) would upload: {path} -> channel={chan}")
                        uploaded.append({"path": str(p), "id": "DRYRUN", "permalink": f"https://example.local/{p.name}"})
                        break
                    self._log_debug(f"Uploading file (attempt {attempt + 1}): {path} -> channel={chan}")
                    with open(p, "rb") as fh:
                        ic = initial_comment if attach_comment_first else None
                        if hasattr(self.client, "files_upload_v2"):
                            self._log_debug("Using files_upload_v2() (required)")
                            channels_param = [resolved_chan] if resolved_chan and isinstance(resolved_chan, str) else resolved_chan
                            resp = self.client.files_upload_v2(
                                channels=channels_param,
                                file=fh,
                                filename=p.name,
                                title=p.name,
                                initial_comment=ic,
                                thread_ts=thread_ts,
                            )
                        else:
                            raise RuntimeError("slack_sdk.WebClient does not support files_upload_v2; please upgrade slack-sdk")

                        self._log_api_response("files_upload_v2", resp)

                    file_obj = resp.get("file") or {}
                    file_id = file_obj.get("id")
                    permalink = file_obj.get("permalink") or file_obj.get("url_private")

                    uploaded.append({
                        "path": str(p),
                        "id": file_id,
                        "permalink": permalink,
                    })
                    if attach_comment_first:
                        attach_comment_first = False
                    print(f"Uploaded: {p.name} -> {permalink or file_id}")
                    break

                except SlackApiError as e:
                    last_exc = e
                    err_info = self._extract_slack_error(e)
                    self._log_info(f"SlackApiError uploading {path}: {err_info}")
                    traceback.print_exc()
                    attempt += 1
                    time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                except Exception as e:
                    last_exc = e
                    self._log_info(f"Exception uploading {path}: {e}")
                    traceback.print_exc()
                    attempt += 1
                    time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

            else:
                self._log(f"Failed to upload after {DEFAULT_UPLOAD_RETRIES} attempts: {path}")
                uploaded.append({"path": str(p), "id": None, "permalink": None})

        return uploaded

    def send_message_with_files(self, channel: Optional[str], text: str, files_meta: Optional[List[Dict[str, Optional[str]]]] = None, blocks: Optional[List[Dict]] = None, extra_args: Optional[Dict] = None) -> bool:
        """Send message with file permalinks appended."""
        if not self.client:
            raise RuntimeError("Slack WebClient not configured. Provide a bot token (SLACK_BOT_TOKEN or --token).")

        chan = channel or self.channel
        if not chan:
            raise ValueError("A channel must be provided either via --channel or SLACK_CHANNEL env var.")

        full_text = text or ""
        if files_meta:
            links = []
            for meta in files_meta:
                url = meta.get("permalink") or meta.get("url_private")
                self._log_debug(f"file meta: {json.dumps(meta, indent=2)}")

                fname = Path(meta.get("path", "")).name
                if url:
                    links.append(f"<{url}|{fname}>")
                else:
                    links.append(f"{fname} (upload failed)")
            if links:
                full_text = full_text + f"\n\n{FILE_LINKS_LABEL}\n" + "\n".join(links)

        try:
            if self.dry_run:
                self._log_info(f"(dry-run) would post message to {chan}: {text}")
                if files_meta:
                    self._log_info(f"(dry-run) files: {[m.get('path') for m in files_meta]}")
                if blocks:
                    self._log_info(f"(dry-run) blocks count={len(blocks)}")
                return True

            post_chan = channel or self.channel
            post_chan_id = self.resolve_channel_id(post_chan) if post_chan else None
            if post_chan and not post_chan_id:
                self._log_info(f"Channel not found or inaccessible to the bot: '{post_chan}'. Try using the channel ID (C...) or ensure the bot is a member.")
                post_chan_id = post_chan
            else:
                self._log_info(f"Resolved channel '{post_chan}' -> '{post_chan_id}'")
            self._log_debug(f"Posting message to channel {post_chan_id}: {full_text}")
            post_kwargs = dict(channel=post_chan_id, text=full_text, blocks=blocks)
            if extra_args:
                for k in ("username", "icon_emoji", "icon_url", "thread_ts", "mrkdwn"):
                    if k in extra_args:
                        post_kwargs[k] = extra_args[k]
            resp = self.client.chat_postMessage(**post_kwargs)
            self._log_api_response("chat_postMessage", resp)
            return True
        except SlackApiError as e:
            err_info = self._extract_slack_error(e)
            self._log_info(f"Failed to post message (SlackApiError): {err_info}")
            traceback.print_exc()
            return False
        except Exception as e:
            self._log_info(f"Failed to post message (exception): {e}")
            traceback.print_exc()
            return False

    def post_message(self, channel: Optional[str], text: str, blocks: Optional[List[Dict]] = None) -> Optional[str]:
        """Post message and return timestamp (ts) or None."""
        if self.dry_run:
            self._log_info(f"(dry-run) would post message to {channel}: {text}")
            return None
        post_chan = channel or self.channel
        post_chan_id = self.resolve_channel_id(post_chan) if post_chan else None
        if post_chan and not post_chan_id:
            self._log_info(f"Channel not found or inaccessible to the bot: '{post_chan}'. Try using the channel ID (C...) or ensure the bot is a member.")
            post_chan_id = post_chan
        try:
            resp = self.client.chat_postMessage(channel=post_chan_id, text=text, blocks=blocks)
            ts = self._safe_response_get(resp, "ts")
            self._log_info(f"chat_postMessage response: ts={ts}")
            return ts
        except Exception as e:
            self._log_info(f"Failed to post message: {e}")
            traceback.print_exc()
            return None


if __name__ == "__main__":
    import argparse
    import platform
    try:
        import certifi
    except Exception:
        certifi = None
    try:
        import slack_sdk
        slack_sdk_version = getattr(slack_sdk, "__version__", None)
    except Exception:
        slack_sdk = None
        slack_sdk_version = None

    def _print_startup_info():
        print("--- slack_notifier_sdk startup ---", file=sys.stderr, flush=True)
        print(f"python_version={platform.python_version()}", file=sys.stderr, flush=True)
        print(f"slack_sdk_version={slack_sdk_version}", file=sys.stderr, flush=True)
        if certifi:
            try:
                print(f"certifi_where={certifi.where()}", file=sys.stderr, flush=True)
            except Exception:
                pass
        print(f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY')}", file=sys.stderr, flush=True)
        print(f"HTTP_PROXY={os.environ.get('HTTP_PROXY')}", file=sys.stderr, flush=True)
        print("--- end startup ---", file=sys.stderr, flush=True)

    try:
        _print_startup_info()

        parser = argparse.ArgumentParser(description="Slack notifier")
        parser.add_argument("-t", "--title", required=True, help="Notification title")
        parser.add_argument("-m", "--message", help="Notification message")
        parser.add_argument("-s", "--status", default="info", choices=["success", "failure", "error", "warning", "info", "debug"], help="Notification status")
        parser.add_argument("--channel", help="Slack channel ID or name")
        parser.add_argument("--file", "--files", dest="files", nargs="*", help="Files to upload")
        parser.add_argument("--token", help="Slack Bot Token (xoxb-...)")
        parser.add_argument("--verbose", action="store_true", default=None, help="Verbose output")
        parser.add_argument("--insecure", action="store_true", default=None, help="Disable TLS verification (testing only)")
        parser.add_argument("--ca-file", help="CA bundle PEM file path")
        parser.add_argument("--dry-run", action="store_true", default=None, help="Simulate without contacting Slack")
        parser.add_argument("--config", help="Config file path (JSON/YAML)")
        parser.add_argument("--template", help="Template name or path")
        parser.add_argument("--var", action="append", dest="vars", help="Template variable KEY=VALUE")

        args = parser.parse_args()

        cfg = ConfigLoader.load_config_file(args.config) if args.config else {}

        token = args.token or cfg.get("token") or cfg.get("slack_bot_token") or os.environ.get("SLACK_BOT_TOKEN")
        channel = args.channel or cfg.get("channel") or cfg.get("default_channel") or os.environ.get("SLACK_CHANNEL")

        verbose = ConfigLoader.pick_bool(args.verbose, cfg, "verbose", default=False)
        dry_run = ConfigLoader.pick_bool(args.dry_run, cfg, "dry_run", default=False)

        if args.insecure is not None:
            verify_tls = not bool(args.insecure)
        else:
            verify_tls = ConfigLoader.pick_bool(None, cfg, "verify_tls", default=True, invert_cfg_key="insecure")

        ca_path = args.ca_file or cfg.get("ca_file") or cfg.get("ssl_cert_file") or cfg.get("ssl_cert")

        if ca_path:
            if not os.path.isfile(ca_path):
                print(f"CA file not found: {ca_path}", file=sys.stderr, flush=True)
                sys.exit(2)
            os.environ["REQUESTS_CA_BUNDLE"] = ca_path
            os.environ["SSL_CERT_FILE"] = ca_path
            print(f"Using CA bundle: {ca_path}", file=sys.stderr, flush=True)

        notifier = SlackNotifierSDK(token=token, channel=channel, verbose=verbose, verify_tls=verify_tls, dry_run=dry_run)

        print(f"SLACK notifier: token present={bool(token)}, channel={channel}, verbose={verbose}, dry_run={dry_run}", file=sys.stderr, flush=True)
        auth_ok = notifier.test_connection()
        print(f"auth_test_ok={auth_ok}", file=sys.stderr, flush=True)
        template_dict = None
        if args.template or cfg.get("template"):
            template_source = args.template or cfg.get("template")
            template_dict_raw = TemplateProcessor.load_template(template_source)

            status_upper = args.status.upper()
            vars_map: Dict[str, str] = {
                "TITLE": args.title,
                "MESSAGE": args.message or "",
                "STATUS": status_upper,
                "ICON": TemplateProcessor.get_status_icon(status_upper),
            }

            cfg_vars = cfg.get("template_vars") if isinstance(cfg.get("template_vars"), dict) else {}
            if isinstance(cfg_vars, dict):
                for k, v in cfg_vars.items():
                    vars_map.setdefault(str(k), str(v))

            if args.vars:
                for item in args.vars:
                    if not item:
                        continue
                    if "=" in item:
                        k, v = item.split("=", 1)
                        vars_map[str(k).strip()] = v
                    else:
                        vars_map[item.strip()] = ""

            if template_dict_raw:
                template_dict = TemplateProcessor.apply_variables(template_dict_raw, vars_map)
                template_dict = TemplateProcessor.prune_empty_blocks(template_dict)
                if verbose:
                    print("(verbose) template after substitution:", json.dumps(template_dict, indent=2), file=sys.stderr)
            else:
                print("Template specified but could not be loaded; continuing without template", file=sys.stderr)

        status_text = f"[{args.status.upper()}] "
        base_msg = f"{status_text}{args.title}"
        if args.message:
            base_msg = base_msg + "\n\n" + args.message

        template_blocks = None
        template_extra_args: Dict = {}
        if template_dict:
            template_blocks, template_extra_args = TemplateProcessor.extract_blocks_and_args(template_dict)
        files_meta = None
        ok = False

        if args.files:
            if not token:
                print("A bot token is required to upload files. Provide --token or set SLACK_BOT_TOKEN.", file=sys.stderr)
                sys.exit(2)
            initial_blocks = template_blocks
            post_ts = notifier.post_message(channel=channel, text=base_msg, blocks=initial_blocks)
            if post_ts:
                files_meta = notifier.upload_files(args.files, channels=channel, initial_comment=None, thread_ts=post_ts)
                ok = dry_run or bool(files_meta and any(m.get("id") for m in files_meta))
            else:
                files_meta = notifier.upload_files(args.files, channels=channel, initial_comment=base_msg)
                ok = dry_run or bool(files_meta and any(m.get("id") for m in files_meta))
            if ok and template_blocks and not post_ts:
                notifier.send_message_with_files(channel=channel, text=base_msg, files_meta=files_meta, blocks=template_blocks, extra_args=template_extra_args)
        else:
            ok = notifier.send_message_with_files(channel=channel, text=base_msg, files_meta=None, blocks=template_blocks, extra_args=template_extra_args)

        if ok:
            print("Message sent successfully", flush=True)
            sys.exit(0)
        else:
            print("Failed to send message", file=sys.stderr, flush=True)
            sys.exit(1)
    except Exception as e:
        print("Unhandled exception in main:", e, file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(2)