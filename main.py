#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
import json
import os
import random
import shutil
import string
import subprocess
import sys
import time
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


@dataclass
class CmdArgsDestroy:
    name: str | None
    all: bool


@dataclass
class CmdArgsList:
    pass


class Settings(TypedDict):
    default_hetzner_ssh_key: str
    ssh_keys: dict[str, str]  # name -> local path (e.g. ~/.ssh/id_hetzner)


class InstanceInfo(TypedDict):
    name: str
    size: str
    image: str
    location: str
    ip: str
    root_password: str
    ssh_key: str | None


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


def load_settings():
    # type: () -> Settings | None
    settings_file = get_settings_file()
    if settings_file.exists():
        with open(settings_file) as f:
            return json.load(f)
    return None


def save_settings(settings):
    # type: (Settings) -> None
    settings_file = get_settings_file()
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)


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


def wait_for_ssh(ip, ssh_key_file, password, timeout=60):
    # type: (str, str, str | None, int) -> bool
    """Wait for SSH to become available on the server."""

    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run(
            [
                *base_ssh_cmd(ssh_key_file=ssh_key_file, password=password),
                "-o",
                "ConnectTimeout=5",
                f"root@{ip}",
                "true",
            ],
            capture_output=True,
        )
        if result.returncode == 0:
            return True
        time.sleep(2)
    return False


def sync_workdir(ip, ssh_key_file, password, workdir):
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
            *base_ssh_cmd(ssh_key_file=ssh_key_file, password=password),
            f"{workdir_path}/",
            f"root@{ip}:{remote_path}/",
        ]
    )

    if result.returncode == 0:
        print(f"Synced to {remote_path}")
        return remote_path
    else:
        print("Warning: rsync failed")
        return None


def ssh_into_server(ip, ssh_key_file, password, workdir=None):
    # type: (str, str, str | None, str | None) -> None
    """SSH into the server, replacing current process."""
    ssh_cmd = [
        *base_ssh_cmd(ssh_key_file=ssh_key_file, password=password),
        f"root@{ip}",
    ]

    if workdir:
        # Start in the synced directory
        ssh_cmd.extend(["-t", f"cd {workdir} && exec $SHELL -l"])

    os.execvp(ssh_cmd[0], ssh_cmd)


def create_server(
    name, size, image, location, ssh_key_name, do_ssh=False, workdir=None
):
    # type: (str, str, str, str, str, bool, str | None) -> None
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

    # Look up local path for the SSH key
    settings = load_settings() or {}  # type: Settings | dict[str, dict[str, str]]
    ssh_keys_map = settings.get("ssh_keys", {})
    local_ssh_key_path = ssh_keys_map.get(ssh_key_name) if ssh_key_name else None

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

    # Parse JSON output to get IP and root password
    create_info = json.loads(result.stdout)
    root_password = create_info.get("root_password")
    server_info = create_info.get("server", {})
    ip = server_info.get("public_net", {}).get("ipv4", {}).get("ip")

    print("Server created successfully!")
    print()
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
            "root_password": root_password,
            "ssh_key": ssh_key_name,
        },
    )

    if ip and (do_ssh or workdir):
        print("Waiting for SSH to become available...")
        if not wait_for_ssh(
            ip, ssh_key_file=local_ssh_key_path, password=root_password
        ):
            print("Warning: SSH not available after timeout, trying anyway...")

    remote_workdir = None
    if workdir:
        remote_workdir = sync_workdir(
            ip, ssh_key_file=local_ssh_key_path, password=root_password, workdir=workdir
        )

    if do_ssh:
        print()
        print("Connecting...")
        ssh_into_server(
            ip,
            ssh_key_file=local_ssh_key_path,
            password=root_password,
            workdir=remote_workdir,
        )
    else:
        if local_ssh_key_path:
            print(f"ssh -i {local_ssh_key_path} root@{ip}")
        # else:
        #     print(f"sshpass -p {root_password} ssh root@{ip}")


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
    create_server(
        name=args.name,
        size=args.size,
        image=args.image,
        location=args.location,
        ssh_key_name=args.ssh_key or settings.get("default_hetzner_ssh_key"),
        do_ssh=args.ssh,
        workdir=workdir,
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
                hetzner_servers[name] = {
                    "ip": server.get("public_net", {}).get("ipv4", {}).get("ip"),
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
        if name in hetzner_servers:
            info = hetzner_servers[name]
            ip = info.get("ip", "unknown")
            size = info.get("size", "unknown")
            status = info.get("status", "unknown")
            print(f"  {name}  {ip}  {status}  ({size})")
        else:
            # In cache but not in Hetzner (stale)
            info = cached[name]
            ip = info.get("ip", "unknown")
            size = info.get("size", "unknown")
            print(f"  {name}  {ip}  not found  ({size})")


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
    spawnm list
    spawnm destroy my-server
    spawnm destroy --all
""",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Add create args to main parser (for default behavior)
    add_create_args(parser, default_name)

    # Create command (explicit)
    create_parser = subparsers.add_parser("create", help="Create a new instance")
    add_create_args(create_parser, default_name)

    # List command
    subparsers.add_parser("list", help="List tracked instances")

    # Destroy command
    destroy_parser = subparsers.add_parser("destroy", help="Destroy an instance")
    destroy_parser.add_argument("name", nargs="?", help="Server name to destroy")
    destroy_parser.add_argument(
        "--all", action="store_true", help="Destroy all tracked instances"
    )

    args = parser.parse_args()

    check_hcloud_installed()
    ensure_installed()

    if args.command == "list":
        cmd_list(args)  # type: ignore
    elif args.command == "destroy":
        cmd_destroy(args)  # type: ignore
    else:
        # Default to create (covers both explicit "create" and no command)
        cmd_create(args)  # type: ignore


if __name__ == "__main__":
    main()
