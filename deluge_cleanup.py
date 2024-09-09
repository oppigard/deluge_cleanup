import argparse
import subprocess
import yaml
import os

class Deluge:
    def __init__(self, host: str, port: str, user: str, password: str, container: str = None, verbose: int = 1):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.container = container
        self.verbose = verbose

    def run_command(self, command: str) -> str:
        full_command = f'deluge-console "connect {self.host}:{self.port} {self.user} {self.password}; {command}"'
        if self.verbose >= 2:
            print(f"Running command: {full_command}")
        
        try:
            if self.container:
                result = subprocess.run(["docker", "exec", "-it", "deluge", "bash", "-c", full_command],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            else:
                result = subprocess.run(full_command, shell=True, check=True, capture_output=True, text=True)
            if self.verbose >= 3:
                print(f"Command output: {result.stdout}")
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            if self.verbose >= 1:
                print(f"Error executing command: {e}")
            return ""

    def get_all_torrents(self) -> str:
        return self.run_command("info --detailed")

    def stop_and_remove(self, torrent: 'Deluge.Torrent'):
        torrent.stop(self)
        torrent.remove(self)

    class Torrent:
        def __init__(self, name: str, torrent_id: str, state: str, ratio: float, tracker: str):
            self.name = name
            self.id = torrent_id
            self.state = state
            self.ratio = ratio
            self.tracker = tracker

        def stop(self, deluge: 'Deluge'):
            if deluge.verbose >= 2:
                print(f"Stopping torrent: {self.name}")
            deluge.run_command(f"pause {self.id}")

        def remove(self, deluge: 'Deluge'):
            if deluge.verbose >= 2:
                print(f"Removing torrent: {self.name}")
            deluge.run_command(f"rm {self.id}")

def parse_torrent_info(torrent_info: str, verbose: int) -> list:
    torrents = []
    sections = torrent_info.split("\n\n")
    
    for section in sections:
        lines = section.split("\n")
        name, torrent_id, state, ratio, tracker = "", "", "", 0, ""

        for line in lines:
            if line.startswith("Name:"):
                name = line.split("Name:")[1].strip()
            elif line.startswith("ID:"):
                torrent_id = line.split("ID:")[1].strip()
            elif line.startswith("State:"):
                state = line.split("State:")[1].strip()
            elif "Share Ratio:" in line:
                ratio = float(line.split("Share Ratio:")[1].strip())
            elif line.startswith("Tracker:"):
                tracker = line.split("Tracker:")[1].strip()
        
        if name and torrent_id:
            torrent = Deluge.Torrent(name, torrent_id, state, ratio, tracker)
            torrents.append(torrent)

            if verbose >= 3:
                print(f"Parsed torrent: {torrent.name} with ID {torrent.id}, Ratio {torrent.ratio}, Tracker {torrent.tracker}")
    
    if verbose >= 1:
        print(f"Total torrents parsed: {len(torrents)}")
    
    return torrents

def load_config() -> dict:
    config_file = "config.yaml"
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as file:
                return yaml.safe_load(file)
        except Exception as e:
            return {}
    return {}

def save_config(config: dict):
    with open("config.yaml", "w") as file:
        yaml.dump(config, file)

def print_stat(name: str, value: int):
    print(f"{name:<30}{value:>5}")

class Args:
    container: str|None
    host: str
    port: str
    user: str
    password: str
    ratio_limit: float
    allowed_trackers: list
    test: bool
    verbose: int

def main():
    config = load_config()

    parser = argparse.ArgumentParser(description="Manage Deluge torrents based on criteria.")
    parser.add_argument("--host", help="Deluge daemon host", default=config.get("host"))
    parser.add_argument("--port", help="Deluge daemon port", default=config.get("port"))
    parser.add_argument("--user", help="Deluge daemon username", default=config.get("user"))
    parser.add_argument("--password", help="Deluge daemon password", default=config.get("password"))
    parser.add_argument("--ratio_limit",
                        type=float,
                        default=config.get("ratio_limit", 0.5),
                        help="Ratio limit to stop and remove torrents")
    parser.add_argument("--container",
                        help="Docker container name to run deluge-console, leave blank if not container",
                        default=config.get("container"))
    parser.add_argument("--allowed_trackers",
                        nargs='*',
                        default=config.get("allowed_trackers", []),
                        help="List of allowed trackers to ignore torrents from")
    parser.add_argument("--test", action="store_true", help="Run in test mode without making any changes")
    parser.add_argument("-v", "--verbose",
                        type=int,
                        choices=[0, 1, 2, 3],
                        default=0,
                        help="Verbosity level: 1 for minimal output, 2 for detailed, 3 for debug")
    
    args: Args = parser.parse_args()

    required_args = ['host', 'port', 'user', 'password']
    missing_args = [arg for arg in required_args if not getattr(args, arg)]
    if missing_args:
        raise ValueError(f"Missing required arguments: {', '.join(missing_args)}")

    verbose = args.verbose
    del args.verbose
    
    test = args.test
    del args.test
    
    if config != vars(args):
        save_config(vars(args))
        if verbose >= 1:
            print("Configuration saved to config.yaml")

    if test:
        verbose = 2
        print("Running in test mode. No changes will be made.")
    
    deluge = Deluge(host=args.host,
                    port=args.port,
                    user=args.user,
                    password=args.password,
                    container=args.container,
                    verbose=verbose)

    torrent_info = deluge.get_all_torrents()
    torrents: list[Deluge.Torrent] = parse_torrent_info(torrent_info, verbose=verbose)

    deleted = 0
    skipped_tracker = 0
    skipped_ratio = 0
    skipped_tracker_ratio = 0
    
    for torrent in torrents:
        if torrent.tracker in args.allowed_trackers:
            if torrent.state == "Seeding" and torrent.ratio >= args.ratio_limit:
                skipped_tracker_ratio += 1
                if verbose >= 2:
                    print(f"Ignoring torrent '{torrent.name}' from allowed tracker '{torrent.tracker}' with ratio {torrent.ratio} exceeding limit {args.ratio_limit}.")
            else:
                skipped_tracker += 1
                if verbose >= 2:
                    print(f"Ignoring torrent '{torrent.name}' from allowed tracker '{torrent.tracker}'.")
            continue
        
        if torrent.state == "Seeding" and torrent.ratio >= args.ratio_limit:
            deleted += 1
            if verbose >= 2:
                print(f"Torrent '{torrent.name}' (Tracker: {torrent.tracker}) with ratio {torrent.ratio} exceeds limit {args.ratio_limit}.")
            if not test:
                deluge.stop_and_remove(torrent)
                if verbose >= 2:
                    print(f"Stopped and removed torrent '{torrent.name}' (ID: {torrent.id})")
            else:
                if verbose >= 2:
                    print("Test mode: Torrent not stopped or removed.")
        else:
            skipped_ratio += 1
            if verbose >= 2:
                print(f"Skipping torrent '{torrent.name}' with ratio {torrent.ratio} and tracker '{torrent.tracker}'.")            
    if verbose >= 2:
        print(35*"-")

    if verbose >= 1:
        print_stat("Total torrents", len(torrents))
        if not test:
            print_stat("Deleted", deleted)
        else:
            print_stat("Would be deleted", deleted)
        print("-" * 35)
        print_stat("Total skipped", skipped_tracker + skipped_ratio + skipped_tracker_ratio)
        print_stat("Due to ratio", skipped_ratio)
        print_stat("Allowed tracker (under limit)", skipped_tracker)
        print_stat("Allowed tracker (over limit)", skipped_tracker_ratio)


if __name__ == "__main__":
    main()
