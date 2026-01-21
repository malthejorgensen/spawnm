#!/usr/bin/env python3
import argparse
import json
import os
import random
import shutil
import string
import subprocess
import sys
from pathlib import Path


def get_cache_dir():
    """Get cache directory using XDG_STATE_HOME or fall back to ~/.local/state"""
    # See: https://specifications.freedesktop.org/basedir/latest/
    xdg_cache = os.environ.get("XDG_STATE_HOME")
    if xdg_cache:
        base = Path(xdg_cache)
    else:
        base = Path.home() / ".local/state"
    cache_dir = base / "spawm"
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


def create_server(name, size, image, location, ssh_key):
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
    if result.returncode == 0:
        server_info = json.loads(result.stdout)
        ip = server_info.get("public_net", {}).get("ipv4", {}).get("ip")
        if ip:
            print(f"ssh -i ~/.ssh/id_hetzner root@{ip}")

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


def destroy_server(name):
    result = subprocess.run(["hcloud", "server", "delete", name])
    if result.returncode == 0:
        print(f"Server '{name}' destroyed.")
        remove_instance(name)
    return result.returncode


def cmd_create(args):
    check_hcloud_installed()
    create_server(args.name, args.size, args.image, args.location, args.ssh_key)


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
    print("  spawm destroy <name>")
    print("  spawm destroy --all")
    sys.exit(1)


def main():
    default_name = f"spawn-tmp-{generate_random_suffix()}"

    parser = argparse.ArgumentParser(
        description="Quickly spin up Hetzner instances.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    # Create command
    create_parser = subparsers.add_parser("create", help="Create a new instance")
    create_parser.add_argument(
        "--name",
        default=default_name,
        help="Server name (default: spawn-tmp-XXXX, random suffix)",
    )
    create_parser.add_argument(
        "--size",
        default="cx23",
        help="Server type (default: cx23). Examples: cx23, cx33, cx43",
    )
    create_parser.add_argument(
        "--image", default="ubuntu-24.04", help="OS image (default: ubuntu-24.04)"
    )
    create_parser.add_argument(
        "--location",
        default="fsn1",
        help="Datacenter location (default: fsn1). Options: fsn1, nbg1, hel1, ash",
    )
    create_parser.add_argument(
        "--ssh-key",
        default="id_hetzner_macbook_air",
        help="SSH key name in Hetzner Cloud",
    )

    # Destroy command
    destroy_parser = subparsers.add_parser("destroy", help="Destroy an instance")
    destroy_parser.add_argument("name", nargs="?", help="Server name to destroy")
    destroy_parser.add_argument(
        "--all", action="store_true", help="Destroy all tracked instances"
    )

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "destroy":
        cmd_destroy(args)
    else:
        # Default to create for backwards compatibility
        # Re-parse with create as implicit command
        create_parser = argparse.ArgumentParser(
            description="Quickly spin up Hetzner instances.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Commands:
    spawm create    Create a new instance
    spawm destroy   Destroy an instance

Examples:
    spawm create --name web-server --size cx33
    spawm destroy my-server
    spawm destroy --all
""",
        )
        create_parser.add_argument(
            "--name",
            default=default_name,
            help="Server name (default: spawn-tmp-XXXX, random suffix)",
        )
        create_parser.add_argument(
            "--size",
            default="cx23",
            help="Server type (default: cx23). Examples: cx23, cx33, cx43",
        )
        create_parser.add_argument(
            "--image", default="ubuntu-24.04", help="OS image (default: ubuntu-24.04)"
        )
        create_parser.add_argument(
            "--location",
            default="fsn1",
            help="Datacenter location (default: fsn1). Options: fsn1, nbg1, hel1, ash",
        )
        create_parser.add_argument(
            "--ssh-key",
            default="id_hetzner_macbook_air",
            help="SSH key name in Hetzner Cloud",
        )
        args = create_parser.parse_args()
        check_hcloud_installed()
        create_server(args.name, args.size, args.image, args.location, args.ssh_key)


if __name__ == "__main__":
    main()
