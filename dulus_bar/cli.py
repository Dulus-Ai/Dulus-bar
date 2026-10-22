"""``dulusbar`` command line.

    dulusbar                     start the island (detaches from the terminal)
    dulusbar status              what every agent is doing right now
    dulusbar notify "Built ✓"    post a status from any script
    dulusbar ask "Deploy?"       Allow / Deny on the island; exit 0 / 1 / 2
    dulusbar wrap Codex -- codex run any CLI agent wired to the island
    dulusbar hooks install       connect Claude Code (and Codex) in one step
    dulusbar doctor              diagnose the setup
    dulusbar quit                stop the running island

Heavy imports (Qt, websockets) happen only in the commands that need them,
so ``dulusbar hook claude`` — run by Claude Code on every tool call — and
``notify`` / ``ask`` start in milliseconds.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # argparse is imported lazily: `dulusbar hook` never needs it
    import argparse

from . import __version__
from . import protocol as P

_STATUS_EVENTS = {"running": P.MESSAGE, "done": P.COMPLETED, "error": P.ERROR, "idle": P.IDLE}
_TRUTHY = ("1", "true", "yes", "on")


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


# --- run -------------------------------------------------------------------------


def _launched_from_console() -> bool:
    """True when we're attached to a terminal we should escape from."""
    if os.name == "nt":
        try:
            import ctypes

            return bool(ctypes.windll.kernel32.GetConsoleWindow())  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


def _relaunch_detached() -> bool:
    """Relaunch in the background with no console. Returns True on success."""
    import subprocess

    from .integrations import self_argv

    env = dict(os.environ, DULUS_BAR_DETACHED="1")
    argv = self_argv("run")
    try:
        if os.name == "nt":
            detached_process, create_no_window = 0x00000008, 0x08000000
            subprocess.Popen(
                argv, env=env, creationflags=detached_process | create_no_window,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                argv, env=env, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        return True
    except Exception:
        return False


def _port_open(port: int) -> bool:
    import socket

    try:
        with socket.create_connection((P.DEFAULT_HOST, port), timeout=0.25):
            return True
    except OSError:
        return False


def _run_ui() -> int:
    try:
        from .macos_native import run_native, should_use_native

        if should_use_native():
            try:
                return run_native()
            except Exception as exc:
                if _env_on("DULUS_BAR_NATIVE_REQUIRED"):
                    raise
                print(f"[dulusbar] native macOS surface unavailable ({exc}); using Qt fallback",
                      file=sys.stderr)
        from .overlay import run_overlay

        return run_overlay()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Dulus Bar crashed: {exc}", file=sys.stderr)
        raise


def cmd_run(args: argparse.Namespace) -> int:
    from .client import probe

    port = P.configured_port()
    if probe(f"ws://{P.DEFAULT_HOST}:{port}") is not None:
        print(f"[dulusbar] Dulus Bar is already running (ws://{P.DEFAULT_HOST}:{port}).", file=sys.stderr)
        return 0
    if _port_open(port):
        print(f"[dulusbar] port {port} is taken by another program. "
              "Start Dulus Bar on another one with DULUS_BAR_PORT=<port>.", file=sys.stderr)
        return 1
    foreground = (getattr(args, "foreground", False) or getattr(args, "foreground_top", False)
                  or _env_on("DULUS_BAR_FOREGROUND"))
    if not foreground and os.environ.get("DULUS_BAR_DETACHED") != "1" and _launched_from_console():
        if _relaunch_detached():
            return 0  # the detached copy took over — free this terminal
    return _run_ui()


# --- status ------------------------------------------------------------------------


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _connect(**kwargs: Any):
    from .client import BarClient, BarUnavailable

    try:
        return BarClient(**kwargs).connect()
    except BarUnavailable:
        print("[dulusbar] Dulus Bar isn't running — start it with: dulusbar", file=sys.stderr)
        return None


def cmd_status(args: argparse.Namespace) -> int:
    from .client import BarError

    bar = _connect(timeout=2.0, client="dulusbar-status")
    if bar is None:
        return 2
    try:
        status = bar.status()
    except BarError as exc:
        print(f"[dulusbar] the island refused: {exc}", file=sys.stderr)
        return 1
    finally:
        bar.close()
    if args.json:
        print(json.dumps(status, indent=2))
        return 0
    server = status.get("server") or {}
    sessions = status.get("sessions") or []
    perms = status.get("permissions") or []
    print(f"Dulus Bar {server.get('version', '?')} · {P.server_url()} · "
          f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}")
    now = time.time()
    marks = {"running": "●", "waiting": "◆", "done": "✓", "error": "✗", "idle": "○"}
    for s in sessions:
        name = s.get("agent", "?") + (f" · {s['title']}" if s.get("title") else "")
        meta = " · ".join(x for x in (s.get("model"), f"ctx {s['ctx']}" if s.get("ctx") else "") if x)
        line = (f"  {marks.get(s.get('status'), '•')} {name:<28.28} {s.get('status', ''):<8} "
                f"{(s.get('message') or ''):<44.44} {_ago(now - float(s.get('status_since') or now)):>6}")
        print(line + (f"   {meta}" if meta else ""))
    if perms:
        print("Waiting for approval:")
        for p in perms:
            label = p.get("tool", "") + (f"  {p['args']}" if p.get("args") else "")
            print(f"  ? {p.get('agent')}: {label[:90]}")
    return 0


# --- notify / ask --------------------------------------------------------------------


def cmd_notify(args: argparse.Namespace) -> int:
    from .client import send_event

    text = " ".join(args.message).strip()
    session = args.session or f"notify-{args.agent}"
    payload: Dict[str, Any] = {"text": text, "pid_host": P.host_id()}
    if args.title:
        payload["title"] = args.title
    if args.cwd:
        payload["cwd"] = os.getcwd()
    if not send_event(args.agent, _STATUS_EVENTS[args.status], session, payload):
        if not args.quiet:
            print("[dulusbar] Dulus Bar isn't running; notification dropped.", file=sys.stderr)
    return 0


def _ask_in_terminal(question: str) -> int:
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return 2
    return 0 if answer in ("y", "yes", "s", "si", "sí") else 1


def cmd_ask(args: argparse.Namespace) -> int:
    from .client import BarClient, BarUnavailable

    question = " ".join(args.question).strip() or "Continue?"
    session = args.session or f"ask-{os.getpid()}"
    try:
        bar = BarClient(args.agent, session, timeout=2.0, client="dulusbar-ask").connect()
    except BarUnavailable:
        if not args.no_tty and sys.stdin.isatty():
            return _ask_in_terminal(question)
        print("[dulusbar] Dulus Bar isn't running; nobody to ask.", file=sys.stderr)
        return 2
    try:
        decision = bar.request_permission(question, "", detail=args.detail or "",
                                          timeout=args.timeout, extra={"cwd": os.getcwd()})
        bar.event(P.SESSION_ENDED)  # answered: nothing left to show for this script
    except KeyboardInterrupt:
        return 2
    except BarUnavailable:
        decision = None
    finally:
        bar.close()
    if decision is None:
        print("[dulusbar] no answer.", file=sys.stderr)
        return 2
    return 0 if decision.approved else 1


# --- wrap / hook / hooks ----------------------------------------------------------------


def cmd_wrap(args: argparse.Namespace) -> int:
    import asyncio

    from .wrappers.base_wrapper import AgentWrapper

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("usage: dulusbar wrap NAME -- COMMAND [ARGS...]", file=sys.stderr)
        return 2
    return asyncio.run(AgentWrapper(args.name, command).run())


def cmd_hook(args: argparse.Namespace) -> int:
    if args.agent == "claude":
        from .integrations.claude import hook_main

        return hook_main()
    from .integrations.codex import handle_notify

    return handle_notify(args.payload[-1] if args.payload else sys.stdin.read())


def _detect_agents() -> List[str]:
    import shutil
    from pathlib import Path

    found = []
    if shutil.which("claude") or (Path.home() / ".claude").is_dir() or os.environ.get("CLAUDE_CONFIG_DIR"):
        found.append("claude")
    if shutil.which("codex") or (Path.home() / ".codex").is_dir() or os.environ.get("CODEX_HOME"):
        found.append("codex")
    return found or ["claude"]


def cmd_hooks(args: argparse.Namespace) -> int:
    from .integrations import ConfigError, claude, codex

    modules = {"claude": claude, "codex": codex}
    if args.target == "all":
        targets = list(modules)
    elif args.target == "auto":
        targets = _detect_agents() if args.action == "install" else list(modules)
    else:
        targets = [args.target]
    names = {"claude": "Claude Code", "codex": "Codex"}
    code = 0
    for target in targets:
        module = modules[target]
        try:
            if args.action == "install":
                result = module.install()
                print(f"✓ {names[target]}: connected ({result['path']})")
                if target == "claude":
                    print("  Restart Claude Code sessions (or run /hooks) to pick the hooks up.")
            elif args.action == "uninstall":
                result = module.uninstall()
                verb = "disconnected" if result.get("removed") else "was not connected"
                print(f"✓ {names[target]}: {verb} ({result['path']})")
            else:
                st = module.status()
                if st.get("error"):
                    mark, text = "✗", st["error"]
                elif st.get("installed"):
                    current = st.get("current")
                    mark = "✓" if current else "!"
                    text = "connected" if current else "connected, but from another install — run: dulusbar hooks install"
                elif st.get("partial"):
                    mark, text = "!", "partly connected — run: dulusbar hooks install"
                elif st.get("other_notify"):
                    mark, text = "!", "notify is used by another program"
                else:
                    mark, text = "·", "not connected — run: dulusbar hooks install " + target
                print(f"{mark} {names[target]}: {text} ({st['path']})")
        except ConfigError as exc:
            print(f"✗ {names[target]}: {exc}", file=sys.stderr)
            code = 1
    return code


def cmd_quit(args: argparse.Namespace) -> int:
    from .client import BarError

    bar = _connect(timeout=2.0, client="dulusbar-quit")
    if bar is None:
        return 0
    try:
        bar.shutdown()
        print("[dulusbar] Dulus Bar is quitting.")
        return 0
    except BarError as exc:
        print(f"[dulusbar] couldn't stop it: {exc}", file=sys.stderr)
        return 1
    finally:
        bar.close()


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    return run_doctor()


# --- parser ---------------------------------------------------------------------------


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(
        prog="dulusbar",
        description="Dulus Bar — a Dynamic Island for your AI agents.",
    )
    parser.add_argument("--version", action="version", version=f"dulus-bar {__version__}")
    parser.add_argument("--foreground", dest="foreground_top", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the island (default)")
    run.add_argument("--foreground", action="store_true", help="stay attached to this terminal")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="show what every agent is doing")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    status.set_defaults(func=cmd_status)

    notify = sub.add_parser("notify", help="post a status to the island from any script")
    notify.add_argument("message", nargs="+")
    notify.add_argument("-a", "--agent", default="Script", help="name shown on the island")
    notify.add_argument("-s", "--session", help="session id (default: one per agent name)")
    notify.add_argument("--status", choices=sorted(_STATUS_EVENTS), default="done")
    notify.add_argument("--title", help="short title next to the name (e.g. the project)")
    notify.add_argument("--cwd", action="store_true", help="tag it with the current directory")
    notify.add_argument("-q", "--quiet", action="store_true", help="no warning when the island is off")
    notify.set_defaults(func=cmd_notify)

    ask = sub.add_parser("ask", help="Allow / Deny on the island (exit 0 allowed, 1 denied, 2 no answer)")
    ask.add_argument("question", nargs="+")
    ask.add_argument("-a", "--agent", default="Script", help="name shown on the island")
    ask.add_argument("-s", "--session", help="session id")
    ask.add_argument("--detail", help="extra lines shown on the card (e.g. the command)")
    ask.add_argument("--timeout", type=float, help="give up after this many seconds")
    ask.add_argument("--no-tty", action="store_true", help="don't fall back to asking in the terminal")
    ask.set_defaults(func=cmd_ask)

    wrap = sub.add_parser("wrap", help="run any CLI agent wired to the island")
    wrap.add_argument("name", help="name shown on the island, e.g. Codex")
    wrap.add_argument("command", nargs=argparse.REMAINDER, help="-- command [args...]")
    wrap.set_defaults(func=cmd_wrap)

    hooks = sub.add_parser("hooks", help="connect Claude Code / Codex (install, uninstall, status)")
    hooks.add_argument("action", choices=("install", "uninstall", "status"), nargs="?", default="status")
    hooks.add_argument("target", choices=("auto", "all", "claude", "codex"), nargs="?", default="auto")
    hooks.set_defaults(func=cmd_hooks)

    hook = sub.add_parser("hook", help=argparse.SUPPRESS)  # what Claude Code / Codex run
    hook.add_argument("agent", choices=("claude", "codex"))
    hook.add_argument("payload", nargs="*")
    hook.set_defaults(func=cmd_hook)

    doctor = sub.add_parser("doctor", help="diagnose the setup")
    doctor.set_defaults(func=cmd_doctor)

    quit_ = sub.add_parser("quit", help="stop the running island")
    quit_.set_defaults(func=cmd_quit)
    return parser


def _windows_console(argv: List[str]) -> None:
    """Frozen Windows builds are GUI apps without a console: give the CLI one.

    A command typed in a terminal attaches to it; ``wrap``, opened by the
    island in a new window, gets its own. Never for ``hook``: its stdout is the
    pipe Claude Code reads the decision from, not a screen.
    """
    from .integrations import is_frozen

    command = argv[0] if argv else "run"
    if os.name != "nt" or not is_frozen() or command in ("run", "hook"):
        return
    if all(stream is not None for stream in (sys.stdin, sys.stdout, sys.stderr)):
        return  # a console build, or redirected streams: leave them be
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if not (kernel32.AttachConsole(-1) or (command == "wrap" and kernel32.AllocConsole())):
            return
        if sys.stdin is None:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [a for a in argv if not a.startswith("-psn_")]  # macOS Finder launch noise
    if argv[:2] == ["hook", "claude"]:  # hot path: skip argparse entirely
        from .integrations.claude import hook_main

        return hook_main()
    _windows_console(argv)
    args = build_parser().parse_args(argv)
    func = getattr(args, "func", None) or cmd_run
    return int(func(args) or 0)
