#!/usr/bin/env python3
import argparse
import json
import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from argparse import ArgumentParser, Namespace  # noqa: F401


@dataclass
class CmdArgsCreate:
    name: str
    size: str
    image: str
    location: str
    ssh_key: str | None
    ssh: bool
    workdir: bool
    conf: str | None
    no_conf: bool


@dataclass
class CmdArgsSsh:
    name: str
    ssh_key: str | None
    workdir: bool
    conf: str | None
    no_conf: bool


@dataclass
class CmdArgsDestroy:
    name: str | None
    all: bool


@dataclass
class CmdArgsList:
    pass


@dataclass
class CmdArgsDebugConf:
    name: str
    conf: str | None
    no_conf: bool


class Settings(TypedDict, total=False):
    default_hetzner_ssh_key: str
    ssh_keys: dict[str, str]  # name -> local path (e.g. ~/.ssh/id_hetzner)
    conf: list[str]  # e.g. ["git", "tmux", "fish"]


class InstanceInfo(TypedDict):
    name: str
    size: str
    image: str
    location: str
    ip: str
    dns_ptr: str | None
    root_password: str
    ssh_key: str | None
    created_at: str | None
    spawn_dir: str | None


# Config locations for --conf option
# Each entry maps app name to list of (local_path, remote_path) pairs
# Pairs are tried in order, first existing local_path is used
# Paths starting with $XDG_CONFIG/ use XDG_CONFIG_HOME or ~/.config
CONFIG_LOCATIONS = {
    "git": [
        ("$XDG_CONFIG/git", "/root/.config/git"),
        ("~/.gitconfig", "/root/.gitconfig"),
    ],
    "tmux": [
        ("$XDG_CONFIG/tmux", "/root/.config/tmux"),
        ("~/.tmux.conf", "/root/.tmux.conf"),
    ],
    "fish": [
        ("$XDG_CONFIG/fish", "/root/.config/fish"),
    ],
}


def expand_config_path(path):
    # type: (str) -> str
    """Expand config path, using XDG_CONFIG_HOME for $XDG_CONFIG/ prefix."""
    if path.startswith("$XDG_CONFIG/"):
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            base = xdg_config
        else:
            base = os.path.expanduser("~/.config")
        return os.path.join(base, path[len("$XDG_CONFIG/") :])
    return os.path.expanduser(path)


def get_config_dir():
    # type: () -> Path
    """Get config directory using XDG_CONFIG_HOME or fall back to ~/.config"""
    # See: https://specifications.freedesktop.org/basedir/latest/
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base = Path(xdg_config)
    else:
        base = Path.home() / ".config"
    config_dir = base / "spawnm"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_settings_file():
    # type: () -> Path
    return get_config_dir() / "settings.json"


CACHED_SETTINGS = None


def load_settings():
    # type: () -> Settings | None
    global CACHED_SETTINGS
    if CACHED_SETTINGS:
        return CACHED_SETTINGS

    settings_file = get_settings_file()
    if settings_file.exists():
        with open(settings_file) as f:
            CACHED_SETTINGS = json.load(f)
            return CACHED_SETTINGS
    return None


def save_settings(settings):
    # type: (Settings) -> None
    global CACHED_SETTINGS
    settings_file = get_settings_file()
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)
        CACHED_SETTINGS = settings


def run_install():
    # type: () -> Settings
    """Run the initial install step to configure spawnm."""
    print("Welcome to spawnm!")
    print()
    print("Before using spawnm, you need to configure your Hetzner SSH key.")

    # Fetch SSH keys from Hetzner
    result = subprocess.run(
        ["hcloud", "ssh-key", "list", "--output", "json"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print("Error: Failed to fetch SSH keys from Hetzner.")
        print("Make sure you're authenticated: hcloud context create <context-name>")
        sys.exit(1)

    ssh_keys = json.loads(result.stdout)

    if not ssh_keys:
        print("No SSH keys found in your Hetzner account.")
        print(
            "Please add an SSH key at: https://console.hetzner.cloud/ -> Security -> SSH Keys"
        )
        sys.exit(1)

    # Display available keys
    print("Available SSH keys in Hetzner:")
    print()
    for i, key in enumerate(ssh_keys, 1):
        name = key.get("name", "unknown")
        fingerprint = key.get("fingerprint", "")
        print(f"  {i}. {name}")
        print(f"     Fingerprint: {fingerprint}")
    print()

    # Let user select a key
    while True:
        choice = input(f"Select an SSH key [1-{len(ssh_keys)}]: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(ssh_keys):
                break
            print(f"Please enter a number between 1 and {len(ssh_keys)}")
        except ValueError:
            print("Please enter a valid number")

    selected_key = ssh_keys[idx]
    ssh_key_name = selected_key.get("name")

    print()
    print(f"Selected: {ssh_key_name}")
    print()

    # Ask for local key path
    default_path = f"~/.ssh/id_{ssh_key_name.replace(' ', '_').lower()}"
    local_path = input(f"Enter local SSH key path [{default_path}]: ").strip()
    if not local_path:
        local_path = default_path

    # Verify the key exists
    expanded_path = os.path.expanduser(local_path)
    if not os.path.exists(expanded_path):
        print(f"Warning: {local_path} does not exist")
        confirm = input("Continue anyway? [y/N]: ").strip().lower()
        if confirm != "y":
            sys.exit(1)

    settings = {
        "default_hetzner_ssh_key": ssh_key_name,
        "ssh_keys": {ssh_key_name: local_path},
    }  # type: Settings
    save_settings(settings)

    print()
    print(f"Settings saved to {get_settings_file()}")
    print()
    return settings


def ensure_installed():
    # type: () -> None
    """Ensure spawnm is configured, running install if needed."""
    settings = load_settings()
    if settings is None:
        settings = run_install()
    return None


def get_cache_dir():
    # type: () -> Path
    """Get cache directory using XDG_STATE_HOME or fall back to ~/.local/state"""
    # See: https://specifications.freedesktop.org/basedir/latest/
    xdg_cache = os.environ.get("XDG_STATE_HOME")
    if xdg_cache:
        base = Path(xdg_cache)
    else:
        base = Path.home() / ".local/state"
    cache_dir = base / "spawnm"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def uninstall():
    # type: () -> None
    """Uninstall spawnm by removing config and cache directories."""
    config_dir = get_config_dir()
    cache_dir = get_cache_dir()

    if config_dir.exists():
        shutil.rmtree(config_dir)
        print(f"Removed config directory: {config_dir}")

    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        print(f"Removed cache directory: {cache_dir}")

    print("spawnm uninstalled.")


def get_instances_file():
    # type: () -> Path
    return get_cache_dir() / "instances.json"


def load_instances():
    # type: () -> dict[str, InstanceInfo]
    instances_file = get_instances_file()
    if instances_file.exists():
        with open(instances_file) as f:
            return json.load(f)
    return {}


def save_instances(instances):
    # type: (dict[str, InstanceInfo]) -> None
    instances_file = get_instances_file()
    with open(instances_file, "w") as f:
        json.dump(instances, f, indent=2)


def add_instance(name, info):
    # type: (str, InstanceInfo) -> None
    instances = load_instances()
    instances[name] = info
    save_instances(instances)


def remove_instance(name):
    # type: (str) -> None
    instances = load_instances()
    if name in instances:
        del instances[name]
        save_instances(instances)


def generate_random_suffix(length=4):
    # type: (int) -> str
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def format_relative_time(iso_timestamp):
    # type: (str | None) -> str
    """Format a timestamp as relative time like '5 minutes ago' or '2026 Jan 24th'."""
    if not iso_timestamp:
        return ""

    created = datetime.fromisoformat(iso_timestamp)

    now = datetime.now()
    diff = now - created
    seconds = diff.total_seconds()

    if seconds < 0:
        return ""

    minutes = seconds / 60
    hours = minutes / 60
    days = hours / 24
    weeks = days / 7

    if minutes < 1:
        return "just now"
    elif minutes < 2:
        return "1 minute ago"
    elif minutes < 60:
        return f"{int(minutes)} minutes ago"
    elif hours < 2:
        return "1 hour ago"
    elif hours < 24:
        return f"{int(hours)} hours ago"
    elif days < 2:
        return "1 day ago"
    elif days < 7:
        return f"{int(days)} days ago"
    elif weeks < 2:
        return "1 week ago"
    elif days < 30:
        return f"{int(weeks)} weeks ago"
    else:
        # More than a month - show the date
        day = created.day
        suffix = "th"
        if day % 10 == 1 and day != 11:
            suffix = "st"
        elif day % 10 == 2 and day != 12:
            suffix = "nd"
        elif day % 10 == 3 and day != 13:
            suffix = "rd"
        return created.strftime(f"%Y %b {day}{suffix}")


def check_hcloud_installed():
    # type: () -> None
    if shutil.which("hcloud") is None:
        print("Error: hcloud CLI is not installed.")
        print(
            "Install it via: brew install hcloud (macOS) or see https://github.com/hetznercloud/cli"
        )
        sys.exit(1)


def check_hcloud_authenticated():
    # type: () -> None
    result = subprocess.run(["hcloud", "server", "list"], capture_output=True)
    if result.returncode != 0:
        print("Error: Not authenticated with Hetzner Cloud.")
        print("Run: hcloud context create <context-name>")
        print("Then enter your API token from https://console.hetzner.cloud/")
        sys.exit(1)


def is_sshpass_installed():
    # type: () -> bool
    return shutil.which("sshpass") is not None


def ensure_sshpass_installed():
    # type: () -> None
    if is_sshpass_installed():
        print("Error: sshpass is not installed.")
        print(
            "Install it via: brew install sshpass (macOS) or apt install sshpass (Linux)"
        )
        sys.exit(1)


def base_ssh_cmd(ssh_key_file, password=None):
    # type: (str, str | None) -> list[str]
    # sshpass_args = []
    sshkey_args = []
    # if password and is_sshpass_installed():
    #     sshpass_args = [
    #         "sshpass",
    #         "-p",
    #         password,
    #     ]
    sshkey_args = [
        "-i",
        os.path.expanduser(ssh_key_file),
        "-o",
        "BatchMode=yes",
    ]

    return [
        # *sshpass_args,
        "ssh",
        *sshkey_args,
        "-o",
        "StrictHostKeyChecking=no",
    ]


def wait_for_ssh(host, ssh_key_file, password, timeout=60):
    # type: (str, str, str | None, int) -> bool
    """Wait for SSH to become available on the server."""

    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run(
            [
                *base_ssh_cmd(ssh_key_file=ssh_key_file, password=password),
                "-o",
                "ConnectTimeout=5",
                f"root@{host}",
                "true",
            ],
            capture_output=True,
        )
        if result.returncode == 0:
            return True
        time.sleep(2)
    return False


def sync_workdir(host, ssh_key_file, password, workdir):
    # type: (str, str, str | None, str) -> str | None
    """Sync local directory to remote server using rsync."""
    workdir_path = Path(workdir).resolve()
    remote_path = f"/root/{workdir_path.name}"

    print(f"Syncing {workdir_path} to {remote_path}...")

    result = subprocess.run(
        [
            "rsync",
            "-avz",
            "--progress",
            "-e",
            " ".join(base_ssh_cmd(ssh_key_file=ssh_key_file, password=password)),
            f"{workdir_path}/",
            f"root@{host}:{remote_path}/",
        ]
    )

    if result.returncode == 0:
        print(f"Synced to {remote_path}")
        return remote_path
    else:
        print("Warning: rsync failed")
        return None


def sync_config(host, ssh_key_file, password, apps):
    # type: (str, str, str | None, list[str]) -> None
    """Sync config files for specified apps to remote server using a single tar transfer."""
    # Collect all configs to sync
    configs = []  # list of (app, local_path, remote_path)
    for app in apps:
        app = app.strip().lower()
        if app not in CONFIG_LOCATIONS:
            print(f"Warning: Unknown config app '{app}', skipping")
            print(f"  Supported: {', '.join(CONFIG_LOCATIONS.keys())}")
            continue

        path_pairs = CONFIG_LOCATIONS[app]

        # Find first existing local path
        local_path = None
        remote_path = None
        for local_template, remote in path_pairs:
            expanded = expand_config_path(local_template)
            if os.path.exists(expanded):
                local_path = expanded
                remote_path = remote
                break

        if not local_path:
            looked_in = [p[0] for p in path_pairs]
            print(f"Warning: No config found for '{app}', skipping")
            print(f"  Looked in: {', '.join(looked_in)}")
            continue

        configs.append((app, local_path, remote_path))

    if not configs:
        return

    # Create a temp directory with the target structure (relative to /root)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        for app, local_path, remote_path in configs:
            # remote_path is like /root/.config/git or /root/.gitconfig
            # We want the path relative to /root
            rel_path = remote_path.removeprefix("/root/")
            target = tmpdir_path / rel_path

            local_path_obj = Path(local_path)
            print(f"  {app}: {local_path} -> {remote_path}")

            if local_path_obj.is_dir():
                # Copy directory contents
                shutil.copytree(local_path, target)
            else:
                # Copy file
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_path, target)

        # Tar and pipe over SSH to extract at /root
        print("Transferring configs...")
        tar_cmd = subprocess.Popen(
            ["tar", "-cf", "-", "-C", tmpdir, "."],
            stdout=subprocess.PIPE,
        )
        ssh_cmd = subprocess.Popen(
            [
                *base_ssh_cmd(ssh_key_file=ssh_key_file, password=password),
                f"root@{host}",
                "tar -xf - -C /root --warning=no-unknown-keyword",
            ],
            stdin=tar_cmd.stdout,
        )
        tar_cmd.stdout.close()
        ssh_cmd.wait()

        if ssh_cmd.returncode == 0:
            print("  Synced all configs")
        else:
            print("  Warning: Failed to sync configs")


def ssh_into_server(host, ssh_key_file, password, workdir=None):
    # type: (str, str, str | None, str | None) -> None
    """SSH into the server, replacing current process."""
    ssh_cmd = [
        *base_ssh_cmd(ssh_key_file=ssh_key_file, password=password),
        f"root@{host}",
    ]

    if workdir:
        # Start in the synced directory
        ssh_cmd.extend(["-t", f"cd {workdir} && exec $SHELL -l"])

    os.execvp(ssh_cmd[0], ssh_cmd)


def find_named_instance(name):
    # type: (str) -> InstanceInfo | None
    """Find an instance that was spawned from the current directory."""
    instances = load_instances()
    return instances.get(name)


def find_instance_for_current_dir():
    # type: () -> InstanceInfo | None
    """Find an instance that was spawned from the current directory."""
    current_dir = os.getcwd()
    instances = load_instances()
    for instance in instances.values():
        if instance.get("spawn_dir") == current_dir:
            return instance
    return None


def connect_or_create_server(
    name, size, image, location, ssh_key_name, do_ssh=False, workdir=None, conf=None
):
    # type: (str, str, str, str, str, bool, str | None, list[str] | None) -> None
    # Look up local path for the SSH key
    settings = load_settings() or {}  # type: Settings | dict[str, dict[str, str]]
    ssh_keys_map = settings.get("ssh_keys", {})
    local_ssh_key_path = ssh_keys_map.get(ssh_key_name) if ssh_key_name else None

    # If --ssh is used, check for existing instance spawned from this directory
    existing = find_instance_for_current_dir()
    if existing:
        # Connect
        host = existing.get("dns_ptr") or existing.get("ip")
        ssh_key_name = existing.get("ssh_key")
        root_password = existing.get("root_password")
    else:
        # Create
        cmd = [
            "hcloud",
            "server",
            "create",
            "--name",
            name,
            "--type",
            size,
            "--image",
            image,
            "--location",
            location,
            "--output",
            "json",
        ]

        if ssh_key_name:
            cmd.extend(["--ssh-key", ssh_key_name])

        print("Creating Hetzner VM...")
        print(f"  Name: {name}")
        print(f"  Type: {size}")
        print(f"  Image: {image}")
        print(f"  Location: {location}")
        if ssh_key_name:
            print(f"  SSH Key: {ssh_key_name}")
        print()

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr)
            sys.exit(result.returncode)

        # Parse JSON output to get IP, DNS, and root password
        create_info = json.loads(result.stdout)
        root_password = create_info.get("root_password")
        server_info = create_info.get("server", {})
        ipv4_info = server_info.get("public_net", {}).get("ipv4", {})
        ip = ipv4_info.get("ip")
        dns_ptr = ipv4_info.get("dns_ptr")

        # Use DNS hostname if available, otherwise fall back to IP
        host = dns_ptr or ip

        print("Server created successfully!")
        print()
        if dns_ptr:
            print(f"  Hostname: {dns_ptr}")
        if ip:
            print(f"  IP: {ip}")
        if root_password:
            print(f"  Root password: {root_password}")
        print()

        # Save instance to cache
        add_instance(
            name,
            {
                "name": name,
                "size": size,
                "image": image,
                "location": location,
                "ip": ip,
                "dns_ptr": dns_ptr,
                "root_password": root_password,
                "ssh_key": ssh_key_name,
                "created_at": datetime.now().isoformat(),
                "spawn_dir": os.getcwd(),
            },
        )

    if host and (do_ssh or workdir or conf):
        print("Waiting for SSH to become available...")
        if not wait_for_ssh(
            host, ssh_key_file=local_ssh_key_path, password=root_password
        ):
            print("Warning: SSH not available after timeout, trying anyway...")

    ssh_setup_and_connect(
        host, local_ssh_key_path, root_password, do_ssh, workdir, conf
    )


def ssh_setup_and_connect(
    host, local_ssh_key_path, root_password, do_ssh, workdir, conf
):
    # type: (str, str, str | None, bool, str | None, list[str] | None) -> None
    remote_workdir = None
    if workdir:
        remote_workdir = sync_workdir(
            host,
            ssh_key_file=local_ssh_key_path,
            password=root_password,
            workdir=workdir,
        )

    if conf:
        sync_config(
            host, ssh_key_file=local_ssh_key_path, password=root_password, apps=conf
        )

    if do_ssh:
        print()
        print("Connecting...")
        ssh_into_server(
            host,
            ssh_key_file=local_ssh_key_path,
            password=root_password,
            workdir=remote_workdir,
        )
    else:
        if local_ssh_key_path:
            print(f"ssh -i {local_ssh_key_path} root@{host}")
        # else:
        #     print(f"sshpass -p {root_password} ssh root@{host}")


def destroy_server(name):
    # type: (str) -> int
    result = subprocess.run(["hcloud", "server", "delete", name])
    if result.returncode == 0:
        print(f"Server '{name}' destroyed.")
        remove_instance(name)
    return result.returncode


def cmd_create(args):
    # type: (CmdArgsCreate) -> None
    settings = load_settings() or {}

    workdir = os.getcwd() if args.workdir else None

    # Determine which configs to sync:
    # 1. --no-conf disables all config syncing
    # 2. --conf overrides the default
    # 3. Otherwise use conf from settings
    conf = None
    if not args.no_conf:
        if args.conf:
            conf = [app.strip() for app in args.conf.split(",") if app.strip()]
        elif settings.get("conf"):
            conf = settings["conf"]

    connect_or_create_server(
        name=args.name,
        size=args.size,
        image=args.image,
        location=args.location,
        ssh_key_name=args.ssh_key or settings.get("default_hetzner_ssh_key"),
        do_ssh=args.ssh,
        workdir=workdir,
        conf=conf,
    )


def cmd_ssh(args):
    # type: (CmdArgsSsh) -> None
    settings = load_settings() or {}

    workdir = os.getcwd() if args.workdir else None

    # Determine which configs to sync:
    # 1. --no-conf disables all config syncing
    # 2. --conf overrides the default
    # 3. Otherwise use conf from settings
    conf = None
    if not args.no_conf:
        if args.conf:
            conf = [app.strip() for app in args.conf.split(",") if app.strip()]
        elif settings.get("conf"):
            conf = settings["conf"]

    if args.name:
        existing = find_named_instance(args.name)
        if not existing:
            print("Could not find instance with name:", args.name)
            exit(1)

    existing = find_instance_for_current_dir()
    if not existing:
        print("Could not find instance connected to current directory:", os.getcwd())
        exit(1)

    # Connect
    host = existing.get("dns_ptr") or existing.get("ip")
    ssh_key_name = existing.get("ssh_key")
    root_password = existing.get("root_password")

    ssh_keys_map = settings.get("ssh_keys", {})
    local_ssh_key_path = ssh_keys_map.get(ssh_key_name) if ssh_key_name else None

    ssh_setup_and_connect(
        host=host,
        local_ssh_key_path=local_ssh_key_path,
        root_password=root_password,
        do_ssh=True,
        workdir=workdir,
        conf=conf,
    )


def cmd_list(args):
    # type: (CmdArgsList) -> None
    cached = load_instances()

    # Get live server list from Hetzner
    result = subprocess.run(
        ["hcloud", "server", "list", "--output", "json"],
        capture_output=True,
        text=True,
    )

    hetzner_servers = {}
    if result.returncode == 0:
        servers = json.loads(result.stdout)
        for server in servers:
            name = server.get("name", "")
            if name.startswith("spawnm-tmp-"):
                ipv4_info = server.get("public_net", {}).get("ipv4", {})
                hetzner_servers[name] = {
                    "ip": ipv4_info.get("ip"),
                    "dns_ptr": ipv4_info.get("dns_ptr"),
                    "status": server.get("status"),
                    "size": server.get("server_type", {}).get("name"),
                }

    # Merge: all servers from Hetzner + cached servers not in Hetzner
    all_names = set(hetzner_servers.keys()) | set(cached.keys())

    if not all_names:
        print("No instances found.")
        return

    print(f"Instances ({len(all_names)}):")
    for name in sorted(all_names):
        # Get created_at from cache if available
        cached_info = cached.get(name, {})
        created_at = cached_info.get("created_at")
        created_str = format_relative_time(created_at)
        created_display = f"  {created_str}" if created_str else ""

        if name in hetzner_servers:
            info = hetzner_servers[name]
            # Prefer dns_ptr from live data, fall back to cache, then IP
            host = (
                info.get("dns_ptr")
                or cached_info.get("dns_ptr")
                or info.get("ip", "unknown")
            )
            size = info.get("size", "unknown")
            status = info.get("status", "unknown")
            print(f"  {name}  {host}  {status}  ({size}){created_display}")
        else:
            # In cache but not in Hetzner (stale)
            info = cached_info
            host = info.get("dns_ptr") or info.get("ip", "unknown")
            size = info.get("size", "unknown")
            print(f"  {name}  {host}  not found  ({size}){created_display}")


def cmd_destroy(args):
    # type: (CmdArgsDestroy) -> None
    instances = load_instances()

    if args.all:
        if not instances:
            print("No instances to destroy.")
            return
        for name in list(instances.keys()):
            destroy_server(name)
        return

    if args.name:
        destroy_server(args.name)
        return

    # No name provided and no --all flag
    if not instances:
        print("No instances to destroy.")
        return

    if len(instances) == 1:
        name = list(instances.keys())[0]
        destroy_server(name)
        return

    # Multiple instances - ask user to specify
    print(f"Multiple instances found ({len(instances)}):")
    for name, info in instances.items():
        ip = info.get("ip", "unknown")
        print(f"  - {name} ({ip})")
    print()
    print("Please specify which instance to destroy:")
    print("  spawnm destroy <name>")
    print("  spawnm destroy --all")
    sys.exit(1)


def cmd_uninstall(args):
    # type: (Namespace) -> None
    uninstall()


def cmd_debug_conf(args):
    # type: (CmdArgsDebugConf) -> None
    """Sync config files to an existing server."""
    settings = load_settings() or {}
    instances = load_instances()

    if args.name not in instances:
        print(f"Error: Instance '{args.name}' not found in cache.")
        print("Use 'spawnm list' to see tracked instances.")
        sys.exit(1)

    instance = instances[args.name]
    host = instance.get("dns_ptr")
    ssh_key_name = instance.get("ssh_key")
    root_password = instance.get("root_password")

    if not host:
        print(f"Error: No hostname found for instance '{args.name}'")
        sys.exit(1)

    # Look up local SSH key path
    ssh_keys_map = settings.get("ssh_keys", {})
    local_ssh_key_path = ssh_keys_map.get(ssh_key_name) if ssh_key_name else None

    # Determine which configs to sync
    conf = None
    if not args.no_conf:
        if args.conf:
            conf = [app.strip() for app in args.conf.split(",") if app.strip()]
        elif settings.get("conf"):
            conf = settings["conf"]

    if not conf:
        print("No configs specified. Use --conf or set 'conf' in settings.json")
        sys.exit(1)

    print(f"Syncing configs to {args.name} ({host})...")
    sync_config(host, ssh_key_file=local_ssh_key_path, password=root_password, apps=conf)


def add_create_args(parser, default_name):
    # type: (ArgumentParser, str) -> None
    """Add create command arguments to a parser."""
    parser.add_argument(
        "--name",
        default=default_name,
        help="Server name (default: spawnm-tmp-XXXX, random suffix)",
    )
    parser.add_argument(
        "--size",
        default="cx23",
        help="Server type (default: cx23). Examples: cx23, cx33, cx43",
    )
    parser.add_argument(
        "--image", default="ubuntu-24.04", help="OS image (default: ubuntu-24.04)"
    )
    parser.add_argument(
        "--location",
        default="fsn1",
        help="Datacenter location (default: fsn1). Options: fsn1, nbg1, hel1, ash",
    )
    parser.add_argument(
        "--ssh-key",
        default=None,
        help="SSH key name in Hetzner Cloud (default: from settings)",
    )
    parser.add_argument(
        "--ssh",
        action="store_true",
        help="SSH into the server after creation",
    )
    parser.add_argument(
        "--workdir",
        action="store_true",
        help="Sync current directory to the server",
    )
    parser.add_argument(
        "--conf",
        default=None,
        help="Sync config files for specified apps (comma-separated). "
        f"Supported: {', '.join(CONFIG_LOCATIONS.keys())}. "
        "Set default_conf in settings.json to sync by default.",
    )
    parser.add_argument(
        "--no-conf",
        action="store_true",
        dest="no_conf",
        help="Disable config file syncing (overrides default_conf in settings)",
    )


def main():
    # type: () -> None
    default_name = f"spawnm-tmp-{generate_random_suffix()}"

    parser = argparse.ArgumentParser(
        description="Quickly spin up Hetzner instances.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
    spawnm [create]  Create a new instance (default)
    spawnm list      List tracked instances
    spawnm destroy   Destroy an instance

Examples:
    spawnm --ssh --workdir
    spawnm create --name web-server --size cx33
    spawnm --ssh --conf git,tmux,fish
    spawnm list
    spawnm destroy my-server
    spawnm destroy --all
""",
    )
    subparsers = parser.add_subparsers(dest="command")

    # No command is passed
    add_create_args(parser, default_name)

    # `create`command
    create_parser = subparsers.add_parser("create", help="Create a new instance")
    add_create_args(create_parser, default_name)

    # `ssh` command
    ssh_parser = subparsers.add_parser("ssh", help="Connect to an existing instance")
    ssh_parser.add_argument("name", nargs="?", help="Server name to connect to")
    ssh_parser.add_argument("--workdir", action="store_true", help="")

    # `list` command
    subparsers.add_parser("list", help="List tracked instances")

    # `destroy` command
    destroy_parser = subparsers.add_parser("destroy", help="Destroy an instance")
    destroy_parser.add_argument("name", nargs="?", help="Server name to destroy")
    destroy_parser.add_argument(
        "--all", action="store_true", help="Destroy all tracked instances"
    )

    # `uninstall` command
    subparsers.add_parser("uninstall", help="Uninstall spawnm (remove settings)")

    # `debug` command with subcommands
    debug_parser = subparsers.add_parser("debug", help="Debug commands")
    debug_subparsers = debug_parser.add_subparsers(dest="debug_command")

    # debug conf <name>
    debug_conf_parser = debug_subparsers.add_parser(
        "conf", help="Sync config files to an existing server"
    )
    debug_conf_parser.add_argument("name", help="Server name to sync configs to")
    debug_conf_parser.add_argument(
        "--conf",
        default=None,
        help="Config apps to sync (comma-separated). "
        f"Supported: {', '.join(CONFIG_LOCATIONS.keys())}",
    )
    debug_conf_parser.add_argument(
        "--no-conf",
        action="store_true",
        dest="no_conf",
        help="Disable default config syncing",
    )

    args = parser.parse_args()

    check_hcloud_installed()
    ensure_installed()

    if args.command == "list":
        cmd_list(args)  # type: ignore
    elif args.command == "destroy":
        cmd_destroy(args)  # type: ignore
    elif args.command == "uninstall":
        cmd_uninstall(args)  # type: ignore
    elif args.command == "debug":
        if args.debug_command == "conf":
            cmd_debug_conf(args)  # type: ignore
        else:
            debug_parser.print_help()
    elif args.command == "ssh":
        cmd_ssh(args)  # type: ignore
    else:
        # Default to create (covers both explicit "create" and no command)
        cmd_create(args)  # type: ignore


if __name__ == "__main__":
    main()
