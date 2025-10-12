#!/usr/bin/env python3
"""
A lightweight Slack notifier using the official Slack Python SDK.

This is a separate implementation from `slack_notifier.py` that focuses on
reliable file uploads via `slack_sdk.WebClient.files_upload` and sending a
message that references uploaded files by permalink.

Usage (CLI):
    python slack_notifier_sdk.py --title "Hello" --message "See files" --files a.txt b.png --channel C12345678 --token xoxb-...

Environment:
    SLACK_BOT_TOKEN: optional fallback bot token
    SLACK_CHANNEL: optional fallback channel

Dependencies (see requirements.txt):
    slack-sdk

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
RETRY_BACKOFF = 1.0


class SlackNotifierSDK:
    """Slack notifier that uses slack_sdk.WebClient for uploads and messages.

    Methods provided are intentionally small and focused: upload_files and
    send_message_with_files (used by send_simple/send_rich CLI helpers).
    """

    def __init__(self, token: Optional[str] = None, channel: Optional[str] = None, verbose: bool = False, verify_tls: bool = True, dry_run: bool = False):
        token = token or os.environ.get("SLACK_BOT_TOKEN")
        self.token = token
        self.channel = channel or os.environ.get("SLACK_CHANNEL")
        self.verbose = verbose
        self.dry_run = bool(dry_run)
        # We always use files_upload_v2 (no fallback to files_upload()).
        # If the installed slack-sdk does not provide files_upload_v2, uploads
        # will raise a clear RuntimeError instructing the user to upgrade.

        # Configure global TLS verification behavior (useful for dev/test).
        # NOTE: disabling TLS verification is insecure and should be used only for testing.
        self._verify_tls = bool(verify_tls)
        if not self._verify_tls:
            # Disable certificate verification globally by replacing the default SSL context
            # This affects libraries that use the default SSL context (urllib3/requests).
            ssl._create_default_https_context = ssl._create_unverified_context
            urllib3.disable_warnings(InsecureRequestWarning)

        # Create the WebClient normally; in dry_run mode we still create the client so
        # callers can inspect it, but network calls will be skipped.
        self.client = WebClient(token=token) if token and not self.dry_run else (WebClient(token=token) if token else None)

    def _log(self, *args, **kwargs):
        if self.verbose:
            print(*args, **kwargs, file=sys.stderr)

    def resolve_channel_id(self, channel: Optional[str]) -> Optional[str]:
        """Resolve a channel name (e.g. 'my-channel' or '#my-channel') to a channel ID (C... or G...).

        If channel already looks like an ID (starts with C/G/D), return it.
        Returns None if no channel provided or not found.
        """
        if not channel:
            return None

        # If it's already an ID (C... for public/private channels, G... for private), return as-is
        ch = channel.strip()
        if ch.startswith("C") or ch.startswith("G") or ch.startswith("D"):
            return ch

        # strip leading '#'
        if ch.startswith("#"):
            ch = ch[1:]

        # Need a client to lookup
        if not self.client:
            return None

        try:
            # conversations_list only returns channels the bot/user can see. We'll request both public and private.
            # Note: for workspaces with many channels this may need pagination; we try a reasonably large limit first.
            cursor = None
            while True:
                params = {"limit": 1000, "types": "public_channel,private_channel"}
                if cursor:
                    params["cursor"] = cursor
                resp = self.client.conversations_list(**params)
                channels = resp.get("channels") or []
                for c in channels:
                    # match by name
                    if c.get("name") == ch or c.get("name_normalized") == ch:
                        return c.get("id")
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except SlackApiError as e:
            # print helpful debug info
            try:
                r = getattr(e, "response", None)
                data = getattr(r, "data", None) if r is not None else None
                print(f"conversations_list failed: {data}", file=sys.stderr)
            except Exception:
                print(f"conversations_list failed: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Error while resolving channel '{channel}': {e}", file=sys.stderr)
            return None

        return None

    def ensure_bot_in_channel(self, channel_id: str) -> bool:
        """Ensure the bot (token) is a member of the given channel_id.

        Returns True if the bot is a member or was able to join. Returns False otherwise.
        For public channels, the method will attempt `conversations_join` if not a member.
        For private channels the bot must be invited (we will not auto-invite).
        """
        if not channel_id or not self.client:
            return False

        try:
            info = self.client.conversations_info(channel=channel_id)
            ch = info.get("channel") or {}
            is_member = ch.get("is_member") or ch.get("is_member", False)
            is_private = ch.get("is_private") or False
            if is_member:
                return True

            # Not a member. If not private, try to join (works for public channels)
            if not is_private:
                try:
                    self._log(f"Attempting to join channel {channel_id}")
                    jresp = self.client.conversations_join(channel=channel_id)
                    ok = jresp.get("ok") if hasattr(jresp, "get") else getattr(jresp, "ok", False)
                    if ok:
                        print(f"Joined channel {channel_id}", file=sys.stderr)
                        return True
                except SlackApiError as e:
                    # Joining can fail due to permissions; warn and return False
                    print(f"Could not auto-join channel {channel_id}: {getattr(e, 'response', None)}", file=sys.stderr)
                    traceback.print_exc()
                    return False

            # Private channel or cannot join
            print(f"Bot is not a member of channel {channel_id}. For private channels invite the bot; for public channels ensure the bot can join.", file=sys.stderr)
            return False
        except SlackApiError as e:
            # Could not fetch info (maybe channel doesn't exist or token lacks scopes)
            try:
                resp = getattr(e, "response", None)
                data = getattr(resp, "data", None) if resp is not None else None
                print(f"conversations_info failed for {channel_id}: {data}", file=sys.stderr)
            except Exception:
                print(f"conversations_info failed: {e}", file=sys.stderr)
            traceback.print_exc()
            return False
        except Exception as e:
            print(f"Error checking channel membership for {channel_id}: {e}", file=sys.stderr)
            traceback.print_exc()
            return False

    def test_connection(self) -> bool:
        """Run a lightweight auth_test to validate token and network connectivity.

        Returns True if auth_test succeeds, False otherwise. Prints details to stderr.
        """
        if self.dry_run:
            print("(dry-run) auth_test: simulated ok", file=sys.stderr, flush=True)
            return True

        if not self.client:
            print("No Slack client configured (missing token)", file=sys.stderr)
            return False

        try:
            resp = self.client.auth_test()
            ok = resp.get("ok") if hasattr(resp, "get") else getattr(resp, "ok", False)
            user = resp.get("user") if hasattr(resp, "get") else getattr(resp, "user", None)
            team = resp.get("team") if hasattr(resp, "get") else getattr(resp, "team", None)
            print(f"auth_test: ok={ok}, user={user}, team={team}", file=sys.stderr, flush=True)
            return bool(ok)
        except Exception as e:
            print(f"auth_test failed: {e}", file=sys.stderr, flush=True)
            traceback.print_exc()
            return False

    def upload_files(self, files: List[str], channels: Optional[str] = None, make_public: bool = False, initial_comment: Optional[str] = None, thread_ts: Optional[str] = None) -> List[Dict[str, Optional[str]]]:
        """Upload multiple files to Slack and return list of dicts with metadata.

        If `initial_comment` is provided, it will be attached to the first successful
        file upload (using Slack's `initial_comment` argument). This lets us present
        a message attached to the uploaded file itself.

        Returns a list of dicts: {"path": str, "id": Optional[str], "permalink": Optional[str], "permalink_public": Optional[str]}
        """
        if not self.client:
            raise RuntimeError("Slack WebClient not configured. Provide a bot token (SLACK_BOT_TOKEN or --token).")

        uploaded = []
        chan = channels or self.channel
        resolved_chan = None
        if chan:
            resolved_chan = self.resolve_channel_id(chan)
            if not resolved_chan:
                print(f"Channel not found or inaccessible to the bot: '{chan}'. Try using the channel ID (C...) or ensure the bot is a member.", file=sys.stderr)
                # proceed but let the API return the error; we still pass the original chan so error messages from Slack remain informative
                resolved_chan = chan
            else:
                # show mapping so user can see what ID is being used
                print(f"Resolved channel '{chan}' -> '{resolved_chan}'", file=sys.stderr, flush=True)

        # Ensure bot is in the channel before attempting file uploads that require a valid channel
        if resolved_chan:
            try:
                in_channel = self.ensure_bot_in_channel(resolved_chan)
                if not in_channel:
                    print(f"Cannot upload files because the bot is not a member of channel {resolved_chan}.", file=sys.stderr)
                    print("If this is a private channel, invite the app/bot to the channel. If public, ensure the bot has permission to join.", file=sys.stderr)
                    return uploaded
            except Exception as e:
                print(f"Error while ensuring bot membership in channel {resolved_chan}: {e}", file=sys.stderr)
                traceback.print_exc()
                return uploaded

        # Track whether we've attached the initial_comment already.
        attach_comment_first = bool(initial_comment)
        for path in files:
            p = Path(path)
            if not p.is_file():
                # Always inform user when a file is missing
                print(f"File not found, skipping: {path}", file=sys.stderr)
                continue

            attempt = 0
            last_exc = None
            while attempt < DEFAULT_UPLOAD_RETRIES:
                try:
                    if self.dry_run:
                        print(f"(dry-run) would upload: {path} -> channel={chan}", file=sys.stderr, flush=True)
                        uploaded.append({"path": str(p), "id": "DRYRUN", "permalink": f"https://example.local/{p.name}", "permalink_public": None})
                        break
                    self._log(f"Uploading file ({attempt + 1}): {path} -> channel={chan}")
                    # slack_sdk handles multipart streaming when given file=open(...)
                    with open(p, "rb") as fh:
                        # Prepare initial_comment for this upload attempt: only attach for the first file
                        ic = initial_comment if attach_comment_first else None
                        # Always use files_upload_v2; do not fallback to the old API.
                        if hasattr(self.client, "files_upload_v2"):
                            self._log("Using files_upload_v2() (required)")
                            # files_upload_v2 accepts a list of channel IDs; pass as list when
                            # we have a resolved channel id.
                            channels_param = [resolved_chan] if resolved_chan and isinstance(resolved_chan, str) else resolved_chan
                            # Only attach the provided initial_comment to the first file.
                            ic = initial_comment if attach_comment_first else None
                            resp = self.client.files_upload_v2(
                                channels=channels_param,
                                file=fh,
                                filename=p.name,
                                title=p.name,
                                initial_comment=ic,
                                thread_ts=thread_ts,
                            )
                        else:
                            # If files_upload_v2 is not available, raise a clear error so
                            # callers know to upgrade slack-sdk. We intentionally do not
                            # fallback to files_upload() anymore.
                            raise RuntimeError("slack_sdk.WebClient does not support files_upload_v2; please upgrade slack-sdk to a version that provides files_upload_v2")

                        # Log a minimal API response so users can see server response
                        try:
                            ok_flag = resp.get("ok") if hasattr(resp, "get") else getattr(resp, "ok", None)
                            file_id_dbg = (resp.get('file') or {}).get('id') if hasattr(resp, 'get') else None
                            print(f"files_upload response: ok={ok_flag}, file_id={file_id_dbg}", file=sys.stderr, flush=True)
                        except Exception:
                            pass

                    file_obj = resp.get("file") or {}
                    file_id = file_obj.get("id")
                    permalink = file_obj.get("permalink") or file_obj.get("url_private")
                    permalink_public = file_obj.get("permalink_public")

                    # Optionally try to make a public permalink if requested and not present
                    if make_public and file_id and not permalink_public:
                        try:
                            self._log(f"Requesting public permalink for file_id={file_id}")
                            resp2 = self.client.files_sharedPublicURL(file=file_id)
                            file_obj2 = resp2.get("file") or {}
                            permalink_public = file_obj2.get("permalink_public") or permalink_public
                        except SlackApiError as e:
                            # Some workspaces disallow public links; that's OK
                            self._log(f"Could not create public permalink: {e.response.get('error')}")

                    uploaded.append({
                        "path": str(p),
                        "id": file_id,
                        "permalink": permalink,
                        "permalink_public": permalink_public,
                    })
                    # If we attached the initial comment to this first successful upload,
                    # don't attach it again for subsequent files.
                    if attach_comment_first:
                        attach_comment_first = False
                    # Inform user of successful upload (quiet but visible)
                    print(f"Uploaded: {p.name} -> {permalink or file_id}")
                    break

                except SlackApiError as e:
                    last_exc = e
                    # Better extraction of SlackApiError info: SlackResponse may be present
                    last_exc = e
                    err_info = None
                    try:
                        resp = getattr(e, "response", None)
                        # SlackResponse has .data and .status_code; try to read them
                        if resp is not None:
                            data = None
                            status = None
                            try:
                                data = getattr(resp, "data", None) or (resp._body if hasattr(resp, '_body') else None) or (resp if isinstance(resp, dict) else None)
                            except Exception:
                                data = None
                            try:
                                status = getattr(resp, "status_code", None) or getattr(resp, "status", None)
                            except Exception:
                                status = None
                            err_field = None
                            if isinstance(data, dict):
                                err_field = data.get("error")
                            err_info = {"status": status, "error": err_field, "data": data}
                        else:
                            err_info = {"error": str(e)}
                    except Exception:
                        err_info = {"error": repr(e)}

                    print(f"SlackApiError uploading {path}: {err_info}", file=sys.stderr)
                    traceback.print_exc()
                    self._log(f"SlackApiError uploading {path}: {err_info}")
                    attempt += 1
                    time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))
                except Exception as e:
                    last_exc = e
                    # Print unexpected exceptions to stderr so the CLI is informative
                    print(f"Exception uploading {path}: {e}", file=sys.stderr)
                    traceback.print_exc()
                    self._log(f"Exception uploading {path}: {e}")
                    attempt += 1
                    time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))

            else:
                # exhausted attempts
                self._log(f"Failed to upload after {DEFAULT_UPLOAD_RETRIES} attempts: {path}")
                uploaded.append({"path": str(p), "id": None, "permalink": None, "permalink_public": None})

        return uploaded

    def send_message_with_files(self, channel: Optional[str], text: str, files_meta: Optional[List[Dict[str, Optional[str]]]] = None, blocks: Optional[List[Dict]] = None, extra_args: Optional[Dict] = None) -> bool:
        """Send a chat message that references uploaded files by permalink.

        If files_meta is provided, their best available permalink will be appended to the message
        as Slack-formatted links: <url|filename>

        extra_args: optional dict forwarded to chat_postMessage (e.g. username, icon_emoji).
        """
        if not self.client:
            raise RuntimeError("Slack WebClient not configured. Provide a bot token (SLACK_BOT_TOKEN or --token).")

        chan = channel or self.channel
        if not chan:
            raise ValueError("A channel must be provided either via --channel or SLACK_CHANNEL env var.")

        full_text = text or ""
        if files_meta:
            links = []
            for meta in files_meta:
                # Prefer the workspace/private permalink (requires Slack auth) over a public permalink.
                url = meta.get("permalink") or meta.get("url_private")
                if self.verbose and meta.get("permalink_public") and (meta.get("permalink_public") != url):
                    try:
                        print(f"(verbose) Ignoring public permalink for {meta.get('path')}: {meta.get('permalink_public')}", file=sys.stderr)
                    except Exception:
                        pass
                if self.verbose:
                    try:
                        print("(verbose) file meta:", json.dumps(meta, indent=2), file=sys.stderr)
                    except Exception:
                        pass
                fname = Path(meta.get("path", "")).name
                if url:
                    links.append(f"<{url}|{fname}>")
                else:
                    links.append(f"{fname} (upload failed)")
            if links:
                full_text = full_text + "\n\nArchivos:\n" + "\n".join(links)

        try:
            if self.dry_run:
                print(f"(dry-run) would post message to {chan}: {text}", file=sys.stderr, flush=True)
                if files_meta:
                    print(f"(dry-run) files: {[m.get('path') for m in files_meta]}", file=sys.stderr, flush=True)
                if blocks:
                    print(f"(dry-run) blocks count={len(blocks)}", file=sys.stderr, flush=True)
                return True
            # resolve channel for posting as well
            post_chan = channel or self.channel
            post_chan_id = self.resolve_channel_id(post_chan) if post_chan else None
            if post_chan and not post_chan_id:
                print(f"Channel not found or inaccessible to the bot: '{post_chan}'. Try using the channel ID (C...) or ensure the bot is a member.", file=sys.stderr)
                post_chan_id = post_chan
            else:
                print(f"Resolved channel '{post_chan}' -> '{post_chan_id}'", file=sys.stderr, flush=True)
            self._log(f"Posting message to channel {post_chan_id}: {full_text}")
            post_kwargs = dict(channel=post_chan_id, text=full_text, blocks=blocks)
            if extra_args:
                # only include safe keys to avoid collisions with required ones
                for k in ("username", "icon_emoji", "icon_url", "thread_ts", "mrkdwn"):
                    if k in extra_args:
                        post_kwargs[k] = extra_args[k]
            resp = self.client.chat_postMessage(**post_kwargs)
            # Print minimal server response so the CLI user knows message was accepted
            try:
                ok_flag = resp.get("ok") if hasattr(resp, "get") else getattr(resp, "ok", None)
                ch = resp.get("channel") if hasattr(resp, "get") else getattr(resp, "channel", None)
                ts = resp.get("ts") if hasattr(resp, "get") else getattr(resp, "ts", None)
                print(f"chat_postMessage response: ok={ok_flag}, channel={ch}, ts={ts}", file=sys.stderr)
            except Exception:
                pass
            return True
        except SlackApiError as e:
            # Always print/send SlackApiError to stderr with details
            try:
                resp = getattr(e, "response", None)
                data = getattr(resp, "data", None) if resp is not None else None
                status = getattr(resp, "status_code", None) if resp is not None else None
                err_field = data.get("error") if isinstance(data, dict) else None
                print(f"Failed to post message (SlackApiError): status={status}, error={err_field}, data={data}", file=sys.stderr)
            except Exception:
                print(f"Failed to post message (SlackApiError): {repr(e)}", file=sys.stderr)
            traceback.print_exc()
            return False
        except Exception as e:
            # Catch transport/urllib errors (SSL/URLError etc.) and show traceback
            print(f"Failed to post message (exception): {e}", file=sys.stderr)
            traceback.print_exc()
            return False

    def post_message(self, channel: Optional[str], text: str, blocks: Optional[List[Dict]] = None) -> Optional[str]:
        """Post a simple chat message and return the message timestamp (ts) or None on failure.

        For dry-run this will print a simulated message and return None.
        """
        if self.dry_run:
            print(f"(dry-run) would post message to {channel}: {text}", file=sys.stderr)
            return None
        post_chan = channel or self.channel
        post_chan_id = self.resolve_channel_id(post_chan) if post_chan else None
        if post_chan and not post_chan_id:
            print(f"Channel not found or inaccessible to the bot: '{post_chan}'. Try using the channel ID (C...) or ensure the bot is a member.", file=sys.stderr)
            post_chan_id = post_chan
        try:
            resp = self.client.chat_postMessage(channel=post_chan_id, text=text, blocks=blocks)
            ts = resp.get("ts") if hasattr(resp, "get") else getattr(resp, "ts", None)
            print(f"chat_postMessage response: ts={ts}", file=sys.stderr)
            return ts
        except Exception as e:
            print(f"Failed to post message: {e}", file=sys.stderr)
            traceback.print_exc()
            return None


# CLI
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

    # Top-level diagnostics and crash handler: always print useful info and tracebacks
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

    # ---------------- Template helpers -----------------
    def _load_template(template_arg: str) -> Optional[Dict]:
        """Load a template JSON/YAML file. Accepts:
        - Absolute/relative path
        - Base name (without extension) resolved inside ./templates relative to this script.
        Returns dict or None.
        """
        if not template_arg:
            return None
        p = Path(template_arg)
        if not p.exists():
            # Search in local templates directory
            base = template_arg
            script_dir = Path(__file__).parent
            templates_dir = script_dir / "templates"
            for ext in (".json", ".yml", ".yaml"):
                cand = templates_dir / f"{base}{ext}"
                if cand.exists():
                    p = cand
                    break
        if not p.exists():
            print(f"Template not found: {template_arg}", file=sys.stderr)
            return None
        try:
            text = p.read_text(encoding="utf-8")
            # Try JSON first
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

    def _apply_vars(obj, vars_map: Dict[str, str]):
        """Recursively replace {{VAR}} placeholders in all string values of obj."""
        if isinstance(obj, dict):
            return {k: _apply_vars(v, vars_map) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_apply_vars(v, vars_map) for v in obj]
        if isinstance(obj, str):
            out = obj
            for k, v in vars_map.items():
                out = out.replace(f"{{{{{k}}}}}", str(v))
            return out
        return obj

    def _prune_empty_blocks(template_dict: Dict) -> Dict:
        """Remove section blocks whose text ends up vacío (Slack exige >0 chars)."""
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
                            # skip this empty section
                            continue
                new_blocks.append(b)
            template_dict["blocks"] = new_blocks
        except Exception:
            pass
        return template_dict

    def _extract_blocks_and_args(template_dict: Dict) -> (Optional[List[Dict]], Dict):
        """Return (blocks, extra_args_for_chat_post). Recognizes username/icon_emoji.
        Does not modify the original dict.
        """
        if not template_dict:
            return None, {}
        blocks = template_dict.get("blocks") if isinstance(template_dict, dict) else None
        extra = {}
        for k in ("username", "icon_emoji", "icon_url", "mrkdwn"):
            if isinstance(template_dict, dict) and k in template_dict:
                extra[k] = template_dict[k]
        return blocks, extra

    def _default_icon_for_status(status: str) -> str:
        m = {
            "SUCCESS": ":white_check_mark:",
            "FAILURE": ":x:",
            "ERROR": ":x:",
            "WARNING": ":warning:",
            "INFO": ":information_source:",
            "DEBUG": ":mag:",
        }
        return m.get(status.upper(), ":speech_balloon:")

    try:
        _print_startup_info()

        parser = argparse.ArgumentParser(description="Slack notifier (slack_sdk version)")
        parser.add_argument("-t", "--title", required=True, help="Notification title")
        parser.add_argument("-m", "--message", help="Notification message")
        parser.add_argument("-s", "--status", default="info", choices=["success", "failure", "error", "warning", "info", "debug"], help="Notification status")
        parser.add_argument("--channel", help="Slack channel ID or name")
        # Backwards-compatible alias: allow --file as well as --files
        parser.add_argument("--file", "--files", dest="files", nargs="*", help="Files to upload and reference (alias --file)")
        parser.add_argument("--token", help="Slack Bot Token (xoxb-...); falls back to SLACK_BOT_TOKEN env var")
        parser.add_argument("--make-public", action="store_true", default=None, help="Attempt to create public permalinks for uploaded files (may be disallowed)")
        parser.add_argument("--verbose", action="store_true", default=None, help="Verbose output")
        parser.add_argument("--insecure", action="store_true", default=None, help="Disable TLS certificate verification (insecure; use only for testing)")
        parser.add_argument("--ca-file", help="Path to a CA bundle PEM file to use for TLS verification (safer than --insecure)")
        parser.add_argument("--dry-run", action="store_true", default=None, help="Simulate actions without contacting Slack")
        parser.add_argument("--config", help="Path to a JSON or YAML config file to use for defaults")
        # New template options
        parser.add_argument("--template", help="Template name or path (looks inside ./templates if not a path)")
        parser.add_argument("--var", action="append", dest="vars", help="Template variable KEY=VALUE (can repeat)")

        args = parser.parse_args()

        # Helper: load config file (YAML preferred, fallback to JSON)
        cfg = {}
        if args.config:
            cfg_path = args.config
            if not os.path.isfile(cfg_path):
                print(f"Config file not found: {cfg_path}", file=sys.stderr, flush=True)
                sys.exit(2)
            try:
                try:
                    import yaml
                    with open(cfg_path, "r") as fh:
                        cfg = yaml.safe_load(fh) or {}
                except Exception:
                    # Fall back to JSON if PyYAML not available or parsing fails
                    with open(cfg_path, "r") as fh:
                        cfg = json.load(fh) or {}
            except Exception as e:
                print(f"Failed to load config file {cfg_path}: {e}", file=sys.stderr, flush=True)
                traceback.print_exc()
                sys.exit(2)

        # Merge precedence: CLI args (if provided) > config file > environment vars > defaults
        # Token
        token = args.token or cfg.get("token") or cfg.get("slack_bot_token") or os.environ.get("SLACK_BOT_TOKEN")
        # Channel
        channel = args.channel or cfg.get("channel") or cfg.get("default_channel") or os.environ.get("SLACK_CHANNEL")

        # Booleans: detect if CLI explicitly set (args.<flag> is True/False) vs None (not provided)
        def pick_bool(arg_val, cfg_key, default=False, invert_cfg_key=None):
            if arg_val is not None:
                return bool(arg_val)
            if cfg_key in cfg:
                return bool(cfg.get(cfg_key))
            if invert_cfg_key and (invert_cfg_key in cfg):
                return not bool(cfg.get(invert_cfg_key))
            return bool(default)

        verbose = pick_bool(args.verbose, "verbose", default=False)
        dry_run = pick_bool(args.dry_run, "dry_run", default=False)
        make_public = pick_bool(args.make_public, "make_public", default=False)
        # verify_tls defaults to True; args.insecure means verify_tls=False
        if args.insecure is not None:
            verify_tls = not bool(args.insecure)
        else:
            if "verify_tls" in cfg:
                verify_tls = bool(cfg.get("verify_tls"))
            elif "insecure" in cfg:
                verify_tls = not bool(cfg.get("insecure"))
            else:
                verify_tls = True

        ca_path = args.ca_file or cfg.get("ca_file") or cfg.get("ssl_cert_file") or cfg.get("ssl_cert")

        # If the user provided a CA bundle, use it (preferred over --insecure)
        if ca_path:
            if not os.path.isfile(ca_path):
                print(f"CA file not found: {ca_path}", file=sys.stderr, flush=True)
                sys.exit(2)
            # Point common env vars to the provided bundle so requests/openssl use it
            os.environ["REQUESTS_CA_BUNDLE"] = ca_path
            os.environ["SSL_CERT_FILE"] = ca_path
            print(f"Using CA bundle: {ca_path}", file=sys.stderr, flush=True)

        notifier = SlackNotifierSDK(token=token, channel=channel, verbose=verbose, verify_tls=verify_tls, dry_run=dry_run)
        # files_upload_v2 is required and will be used; if the installed slack-sdk lacks files_upload_v2
        # the script will raise an informative error during upload.

        # CLI-level quick status so the user sees what's being used (don't print tokens)
        print(f"SLACK notifier: token present={bool(token)}, channel={channel}, verbose={verbose}, dry_run={dry_run}", file=sys.stderr, flush=True)
        # Run an auth_test immediately so the user gets fast feedback if the token/network is a problem
        auth_ok = notifier.test_connection()
        print(f"auth_test_ok={auth_ok}", file=sys.stderr, flush=True)

        # ----------------- Template processing -----------------
        template_dict = None
        if args.template or cfg.get("template"):
            template_source = args.template or cfg.get("template")
            template_dict_raw = _load_template(template_source)
            # Build vars map
            vars_map: Dict[str, str] = {}
            # Defaults
            status_upper = args.status.upper()
            vars_map.update({
                "TITLE": args.title,
                "MESSAGE": args.message or "",
                "STATUS": status_upper,
                "ICON": _default_icon_for_status(status_upper),
            })
            # From config file (template_vars section or flat keys)
            cfg_vars = cfg.get("template_vars") if isinstance(cfg.get("template_vars"), dict) else {}
            if isinstance(cfg_vars, dict):
                for k, v in cfg_vars.items():
                    vars_map.setdefault(str(k), str(v))
            # From CLI --var (override)
            if args.vars:
                for item in args.vars:
                    if not item:
                        continue
                    if "=" in item:
                        k, v = item.split("=", 1)
                        vars_map[str(k).strip()] = v
                    else:
                        # If only KEY provided, treat as empty string
                        vars_map[item.strip()] = ""
            if template_dict_raw:
                template_dict = _apply_vars(template_dict_raw, vars_map)
                template_dict = _prune_empty_blocks(template_dict)
                if verbose:
                    print("(verbose) template after substitution:", json.dumps(template_dict, indent=2), file=sys.stderr)
            else:
                print("Template specified but could not be loaded; continuing without template", file=sys.stderr)

        # Compose a basic message including title and status (fallback text)
        status_text = f"[{args.status.upper()}] "
        base_msg = f"{status_text}{args.title}"
        if args.message:
            base_msg = base_msg + "\n\n" + args.message

        # If template loaded, extract blocks + username/icon
        template_blocks = None
        template_extra_args: Dict = {}
        if template_dict:
            template_blocks, template_extra_args = _extract_blocks_and_args(template_dict)

        # Decide sending strategy with / without files
        files_meta = None
        ok = False
        if args.files:
            if not token:
                print("A bot token is required to upload files. Provide --token or set SLACK_BOT_TOKEN.", file=sys.stderr)
                sys.exit(2)
            # First try: post a top-level message (honoring template if present) and capture its ts. Upload files into that thread.
            initial_blocks = template_blocks
            post_ts = notifier.post_message(channel=channel, text=base_msg, blocks=initial_blocks)
            if post_ts:
                files_meta = notifier.upload_files(args.files, channels=channel, make_public=make_public, initial_comment=None, thread_ts=post_ts)
                ok = dry_run or bool(files_meta and any(m.get("id") for m in files_meta))
            else:
                # Fallback: attach message as initial_comment on first file (template blocks won't appear in this path)
                files_meta = notifier.upload_files(args.files, channels=channel, make_public=make_public, initial_comment=base_msg)
                ok = dry_run or bool(files_meta and any(m.get("id") for m in files_meta))
            # Optionally send an additional summary message including file links if template asks – reuse send_message_with_files
            if ok and template_blocks and not post_ts:  # only if we didn't succeed posting initial message
                notifier.send_message_with_files(channel=channel, text=base_msg, files_meta=files_meta, blocks=template_blocks, extra_args=template_extra_args)
        else:
            # No files: single message (maybe with template blocks)
            ok = notifier.send_message_with_files(channel=channel, text=base_msg, files_meta=None, blocks=template_blocks, extra_args=template_extra_args)

        # Provide immediate CLI feedback even when not verbose
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
