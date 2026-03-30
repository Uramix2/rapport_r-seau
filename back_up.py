import requests
import json
import socket
import time
import os
import datetime
from pathlib import Path

server = "192.168.212.175"
project_id = "5c3881a2-1019-45c2-b545-312bad4fa36f"
backup = "backup.json"
output_dir = "configs"
backup_dir = "backups"



COMMANDS = {
    "router": [
        "terminal length 0",
        "show running-config",
        "show ip interface brief",
        "show ip route",
    ],
    "switch": [
        "terminal length 0",
        "show running-config",
        "show vlan brief",
        "show interfaces trunk",
        "show spanning-tree",
    ],
    "default": [
        "terminal length 0",
        "show running-config",
    ]
}


def get_nodes(base_url):
    url = f"{base_url}/projects/{project_id}/nodes"
    response = requests.get(url)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        nodes = response.json()
        with open(backup, "w") as f:
            json.dump(nodes, f, indent=2)
        return nodes
    else:
        print(f"Erreur {response.status_code}: {response.text}")
        return []


def detect_device_type(node):
    name = node.get("name", "").lower()
    node_type = node.get("node_type", "").lower()  

    if "router" in node_type or name.startswith("r"):
        return "router"
    elif "switch" in node_type or name.startswith("sw"):
        return "switch"
    return "default"


class TelnetClient:
    """Remplace telnetlib (supprime en Python 3.13) via socket brut avec gestion IAC."""

    def __init__(self, host, port, timeout=10):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect((host, port))

    def _filter_iac(self, data):
        """Filtre les négociations IAC Telnet et répond automatiquement."""
        out = b""
        i = 0
        while i < len(data):
            if data[i] == 255 and i + 2 < len(data):   # IAC
                cmd, opt = data[i + 1], data[i + 2]
                if cmd == 253:   # DO  → répondre WONT
                    self.sock.send(bytes([255, 252, opt]))
                elif cmd == 251: # WILL → répondre DONT
                    self.sock.send(bytes([255, 254, opt]))
                i += 3
            else:
                out += bytes([data[i]])
                i += 1
        return out

    def read_eager(self, timeout=2):
        self.sock.settimeout(timeout)
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += self._filter_iac(chunk)
        except socket.timeout:
            pass
        return buf.decode("ascii", errors="ignore")

    def write(self, command):
        self.sock.send(command.encode("ascii") + b"\n")

    def close(self):
        self.sock.close()


def send_telnet_command(tn, command, wait=1.5):
    tn.write(command)
    time.sleep(wait)
    return tn.read_eager()


def get_config(node, telnet_port):
    output = ""
    try:
        tn = TelnetClient(server, telnet_port, timeout=10)
        time.sleep(2)

        output += tn.read_eager()

        send_telnet_command(tn, "", wait=1)
        send_telnet_command(tn, "enable", wait=1)
        send_telnet_command(tn, "", wait=0.5)

        device_type = detect_device_type(node)
        commands = COMMANDS.get(device_type, COMMANDS["default"])

        for cmd in commands:
            print(f"    -> {cmd}")
            result = send_telnet_command(tn, cmd, wait=2)
            output += f"\n{'='*50}\n$ {cmd}\n{'='*50}\n{result}"

        tn.close()
        print(f"Config recuperee ({len(output)} caracteres)")

    except Exception as e:
        print(f"Erreur Telnet: {e}")
        output = f"Erreur: {e}"

    return output


def get_information_perif(nodes):
    if not nodes:
        print("Aucun noeud trouve.")
        return

    print(f"\n{'Nom':<25} {'Console Type':<15} {'Port':<8} {'Commande connexion'}")
    print("-" * 80)
    for node in nodes:
        name         = node.get("name", "N/A")
        console      = node.get("console", "N/A")
        console_type = node.get("console_type", "telnet")
        status       = node.get("status", "unknown")
        status_label = "OK" if status == "started" else "STOP"

        cmd = f"telnet {server} {console}" if console != "N/A" else "Pas de console"
        print(f"[{status_label}] {name:<23} {console_type:<15} {str(console):<8} {cmd}")


def write_config(node_name, config, cycle_dir):
    filename = os.path.join(cycle_dir, f"{node_name}_config.txt")
    with open(filename, "w") as f:
        f.write(f"{'='*60}\n CONFIG: {node_name}\n{'='*60}\n\n")
        f.write(config)
    print(f"Config sauvegardee -> {filename}")


if __name__ == "__main__":
    base_url = f"http://{server}:80/v2"

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)

    while True:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        print(f"\n[{datetime.datetime.now()}] Recuperation des configs...")
        nodes = get_nodes(base_url)

        if os.path.exists(backup):
            backup_dated = os.path.join(backup_dir, f"backup_{timestamp}.json")
            os.rename(backup, backup_dated)
            print(f"Backup sauvegarde -> {backup_dated}")

        get_information_perif(nodes)

        cycle_dir = os.path.join(output_dir, timestamp)
        os.makedirs(cycle_dir, exist_ok=True)

        for node in nodes:
            name         = node.get("name", "unknown")
            console_port = node.get("console")
            status       = node.get("status")
            console_type = node.get("console_type", "telnet")

            if status != "started":
                print(f"\n[SKIP] {name} non demarre.")
                continue
            if console_type != "telnet" or not console_port:
                print(f"\n[SKIP] {name} pas de console Telnet.")
                continue

            print(f"\nConnexion a {name} (port {console_port})...")
            config = get_config(node, console_port)
            write_config(name, config, cycle_dir)

        print(f"\n[OK] Cycle termine. Prochaine mise a jour dans 30 min...")
        time.sleep(1800)