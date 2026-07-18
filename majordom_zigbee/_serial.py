import subprocess


def port_holder(port: str) -> str:
    """Return human-readable description of which process holds the port, or empty string."""
    try:
        # fuser prints the device name to stderr, PIDs to stdout — capture separately
        result = subprocess.run(["fuser", port], capture_output=True, text=True)
        out = result.stdout.strip()
        pids = [p for p in out.split() if p.isdigit()]
        names = []
        for pid in pids:
            try:
                comm = subprocess.check_output(["cat", f"/proc/{pid}/comm"], text=True).strip()
                names.append(f"pid={pid} ({comm})")
            except (subprocess.CalledProcessError, FileNotFoundError):
                names.append(f"pid={pid}")
        return ", ".join(names)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
