#!/usr/bin/env python3
import argparse
import json
import random
import shutil
import string
import subprocess
import sys


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


def main():
    default_name = f"spawn-tmp-{generate_random_suffix()}"

    parser = argparse.ArgumentParser(
        description="Create a Hetzner Cloud VM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s --name web-server --size cx33
    %(prog)s --name db-server --size cx43 --ssh-key my-key
""",
    )
    parser.add_argument(
        "--name",
        default=default_name,
        help="Server name (default: spawn-tmp-XXXX, random suffix)",
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

    args = parser.parse_args()

    check_hcloud_installed()
    # check_hcloud_authenticated()
    create_server(args.name, args.size, args.image, args.location, args.ssh_key)


if __name__ == "__main__":
    main()
