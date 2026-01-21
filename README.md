spawnm
======

"spawn machine": Quickly spin up temporary Hetzner Cloud VMs.

Simply run

    spawnm --ssh --workdir

to directly SSH into a new server with the contents of the current folder
copied into it.
    
_\*Requires Hetzner's CLI `hcloud` to be set up and authed._

Installation
------------

```bash
uv install spawnm
```

### Prerequisites

1. Install the Hetzner Cloud CLI:
   ```bash
   brew install hcloud  # macOS
   ```
   Or see https://github.com/hetznercloud/cli for other platforms.

2. Configure `hcloud` with your API token:
   ```bash
   hcloud context create myproject
   # Enter your API token from https://console.hetzner.cloud/
   ```

3. Upload your SSH key to Hetzner Cloud:
   ```bash
   hcloud ssh-key create --name id_hetzner_macbook_air --public-key-from-file ~/.ssh/id_hetzner.pub
   ```

Usage
-----

### Create a VM

```bash
# Create with defaults
spawnm

# Create and SSH in immediately
spawnm --ssh

# Create, sync current directory, and SSH in
spawnm --ssh --workdir

# Create with custom options
spawnm --name my-server --size cx33 --image ubuntu-24.04
```

### Destroy a VM

```bash
# Destroy the only tracked instance
spawnm destroy

# Destroy a specific instance
spawnm destroy my-server

# Destroy all tracked instances
spawnm destroy --all
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--name` | `spawn-tmp-XXXX` | Server name (random suffix by default) |
| `--size` | `cx23` | Server type (cx23, cx33, cx43, etc.) |
| `--image` | `ubuntu-24.04` | OS image |
| `--location` | `fsn1` | Datacenter (fsn1, nbg1, hel1, ash) |
| `--ssh-key` | `id_hetzner_macbook_air` | SSH key name in Hetzner Cloud |
| `--ssh` | - | SSH into server after creation |
| `--workdir` | - | Sync current directory to the server |

## Server Types

| Type | vCPU | RAM | Disk |
|------|------|-----|------|
| cx22 | 2 | 4 GB | 40 GB |
| cx23 | 2 | 4 GB | 80 GB |
| cx32 | 4 | 8 GB | 80 GB |
| cx33 | 4 | 8 GB | 160 GB |
| cx42 | 8 | 16 GB | 160 GB |
| cx43 | 8 | 16 GB | 320 GB |

## Locations

| Code | Location |
|------|----------|
| fsn1 | Falkenstein, Germany |
| nbg1 | Nuremberg, Germany |
| hel1 | Helsinki, Finland |
| ash | Ashburn, USA |

## State

Instance information is stored in `$XDG_STATE_HOME/spawnm/instances.json` (defaults to `~/.local/state/spawnm/instances.json`).

## License

BSD-2-Clause
