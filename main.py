#!/usr/bin/env python3
import argparse
import json
import os
import random
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path


def get_cache_dir():
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
    return get_cache_dir() / "instances.json"


def load_instances():
    instances_file = get_instances_file()
    if instances_file.exists():
        with open(instances_file) as f:
            return json.load(f)
    return {}


def save_instances(instances):
    instances_file = get_instances_file()
    with open(instances_file, "w") as f:
        json.dump(instances, f, indent=2)


def add_instance(name, info):
    instances = load_instances()
    instances[name] = info
    save_instances(instances)


def remove_instance(name):
    instances = load_instances()
    if name in instances:
        del instances[name]
        save_instances(instances)


def generate_random_suffix(length=4):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def check_hcloud_installed():
    if shutil.which("hcloud") is None:
        print("Error: hcloud CLI is not installed.")
        print(
            "Install it via: brew install hcloud (macOS) or see https://github.com/hetznercloud/cli"
        )
        sys.exit(1)


def check_hcloud_authenticated():
    result = subprocess.run(["hcloud", "server", "list"], capture_output=True)
    if result.returncode != 0:
        print("Error: Not authenticated with Hetzner Cloud.")
        print("Run: hcloud context create <context-name>")
        print("Then enter your API token from https://console.hetzner.cloud/")
        sys.exit(1)


SSH_KEY_PATH = "~/.ssh/id_hetzner"


def wait_for_ssh(ip, timeout=60):
    """Wait for SSH to become available on the server."""
    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run(
            [
                "ssh",
                "-i",
                os.path.expanduser(SSH_KEY_PATH),
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "BatchMode=yes",
                f"root@{ip}",
                "true",
            ],
            capture_output=True,
        )
        if result.returncode == 0:
            return True
        time.sleep(2)
    return False


def sync_workdir(ip, workdir):
    """Sync local directory to remote server using rsync."""
    workdir = Path(workdir).resolve()
    remote_path = f"/root/{workdir.name}"

    print(f"Syncing {workdir} to {remote_path}...")

    result = subprocess.run(
        [
            "rsync",
            "-avz",
            "--progress",
            "-e",
            f"ssh -i {os.path.expanduser(SSH_KEY_PATH)} -o StrictHostKeyChecking=no",
            f"{workdir}/",
            f"root@{ip}:{remote_path}/",
        ]
    )

    if result.returncode == 0:
        print(f"Synced to {remote_path}")
        return remote_path
    else:
        print("Warning: rsync failed")
        return None


def ssh_into_server(ip, workdir=None):
    """SSH into the server, replacing current process."""
    ssh_cmd = [
        "ssh",
        "-i",
        os.path.expanduser(SSH_KEY_PATH),
        "-o",
        "StrictHostKeyChecking=no",
        f"root@{ip}",
    ]

    if workdir:
        # Start in the synced directory
        ssh_cmd.extend(["-t", f"cd {workdir} && exec $SHELL -l"])

    os.execvp("ssh", ssh_cmd)


def create_server(name, size, image, location, ssh_key, do_ssh=False, workdir=None):
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
    ]

    if ssh_key:
        cmd.extend(["--ssh-key", ssh_key])

    print("Creating Hetzner VM...")
    print(f"  Name: {name}")
    print(f"  Type: {size}")
    print(f"  Image: {image}")
    print(f"  Location: {location}")
    if ssh_key:
        print(f"  SSH Key: {ssh_key}")
    print()

    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)

    print()
    print("Server created successfully!")
    print()

    result = subprocess.run(
        ["hcloud", "server", "describe", name, "--output", "json"],
        capture_output=True,
        text=True,
    )
    ip = None
    if result.returncode == 0:
        server_info = json.loads(result.stdout)
        ip = server_info.get("public_net", {}).get("ipv4", {}).get("ip")
        if ip:
            print(f"ssh -i {SSH_KEY_PATH} root@{ip}")

        # Save instance to cache
        add_instance(
            name,
            {
                "name": name,
                "size": size,
                "image": image,
                "location": location,
                "ssh_key": ssh_key,
                "ip": ip,
            },
        )

    if ip and (do_ssh or workdir):
        print()
        print("Waiting for SSH to become available...")
        if not wait_for_ssh(ip):
            print("Warning: SSH not available after timeout, trying anyway...")

        remote_workdir = None
        if workdir:
            remote_workdir = sync_workdir(ip, workdir)

        if do_ssh:
            print()
            print("Connecting...")
            ssh_into_server(ip, remote_workdir)


def destroy_server(name):
    result = subprocess.run(["hcloud", "server", "delete", name])
    if result.returncode == 0:
        print(f"Server '{name}' destroyed.")
        remove_instance(name)
    return result.returncode


def cmd_create(args):
    check_hcloud_installed()
    workdir = os.getcwd() if args.workdir else None
    create_server(
        args.name,
        args.size,
        args.image,
        args.location,
        args.ssh_key,
        do_ssh=args.ssh,
        workdir=workdir,
    )


def cmd_destroy(args):
    check_hcloud_installed()
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
        default="id_hetzner_macbook_air",
        help="SSH key name in Hetzner Cloud",
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
    default_name = f"spawnm-tmp-{generate_random_suffix()}"

    parser = argparse.ArgumentParser(
        description="Quickly spin up Hetzner instances.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
    spawnm [create]  Create a new instance (default)
    spawnm destroy   Destroy an instance

Examples:
    spawnm --ssh --workdir
    spawnm create --name web-server --size cx33
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

    # Destroy command
    destroy_parser = subparsers.add_parser("destroy", help="Destroy an instance")
    destroy_parser.add_argument("name", nargs="?", help="Server name to destroy")
    destroy_parser.add_argument(
        "--all", action="store_true", help="Destroy all tracked instances"
    )

    args = parser.parse_args()

    if args.command == "destroy":
        cmd_destroy(args)
    else:
        # Default to create (covers both explicit "create" and no command)
        cmd_create(args)


if __name__ == "__main__":
    main()
