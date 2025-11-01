#!/usr/bin/env python3
"""
setup-ubuntu-crostini.py – Crostini Ubuntu 24.04 Integration (Nov 2025)

* Milestone 141+ compatible (sparse repos OK)
* No cros-ui-config (deprecated)
* Silent, investigative, pre/post-reboot
* cros-guest-tools + icons only
"""

import os
import sys
import subprocess
import textwrap
from pathlib import Path
import glob

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
CROS_REPO_BASE = "https://storage.googleapis.com/cros-packages"
CROS_KEY_FINGERPRINT = "1397BC53640DB551"

# ----------------------------------------------------------------------
# UTILS
# ----------------------------------------------------------------------
def run(cmd, check=True, capture=False):
    print(f"$ {' '.join(map(str, cmd))}")
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8"
    )

def confirm(prompt):
    while True:
        resp = input(f"{prompt} [y/N] ").strip().lower()
        if resp in ("y", "yes"): return True
        if resp in ("n", "no", ""): return False

def print_banner(text):
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70 + "\n")

# ----------------------------------------------------------------------
# SILENT DETECTORS
# ----------------------------------------------------------------------
def _dpkg_status(pkg):
    result = subprocess.run(
        ["dpkg-query", "-W", "-f", "${Status}", pkg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode == 0

def gpg_keys_present():
    required = {"7638D0442B90D010", "04EE7237B7D453EC"}
    try:
        out = run(["apt-key", "list"], capture=True).stdout
        return all(k in out for k in required)
    except:
        return False

def groups_script_exists():
    return (Path.home() / "update-groups").exists()

def default_user_removed():
    return not Path("/home/ubuntu").exists()

def cros_repo_present():
    f = Path("/etc/apt/sources.list.d/cros.list")
    return f.exists() and CROS_REPO_BASE in f.read_text()

def cros_key_present():
    try:
        out = run(["apt-key", "list"], capture=True).stdout
        return CROS_KEY_FINGERPRINT in out
    except:
        return False

def crostini_tools_installed():
    return _dpkg_status("cros-guest-tools") and _dpkg_status("adwaita-icon-theme-full")

# ----------------------------------------------------------------------
# STEPS
# ----------------------------------------------------------------------
STEPS = []

def step(name, desc, func, pre_reboot=True, detector=None):
    STEPS.append({
        "name": name,
        "desc": desc,
        "func": func,
        "pre_reboot": pre_reboot,
        "detector": detector or (lambda: False),
    })

# ----------------------------------------------------------------------
# IMPLEMENTATIONS
# ----------------------------------------------------------------------
def fix_gpg_keys():
    for k in ["7638D0442B90D010", "04EE7237B7D453EC"]:
        run(["apt-key", "adv", "--keyserver", "keyserver.ubuntu.com", "--recv-keys", k], check=False)
    run(["apt", "update", "--fix-missing"], check=False)

def capture_groups():
    script = Path.home() / "update-groups"
    if script.exists():
        return
    try:
        groups = run(["groups", "ubuntu"], capture=True).stdout.strip()
    except:
        groups = "adm,dialout,cdrom,sudo,audio,video,plugdev,users,input,netdev"
    script.write_text(f"sudo usermod -aG {groups} $USER\n")
    print(f"   Saved groups to {script}")

def remove_default_user():
    run(["killall", "-u", "ubuntu"], check=False)
    run(["userdel", "-r", "ubuntu"], check=False)
    sudoers = Path("/etc/sudoers.d/90-cloud-init-users")
    if sudoers.exists():
        lines = [l for l in sudoers.read_text().splitlines() if not l.startswith("ubuntu")]
        sudoers.write_text("\n".join(lines) + "\n")

def add_cros_repo():
    repo_file = Path("/etc/apt/sources.list.d/cros.list")
    milestone = "stretch"
    if Path("/dev/.cros_milestone").exists():
        milestone = Path("/dev/.cros_milestone").read_text().strip()
    repo_file.write_text(f"deb {CROS_REPO_BASE}/{milestone} {milestone} main\n")
    run(["apt-key", "adv", "--keyserver", "keyserver.ubuntu.com", "--recv-keys", CROS_KEY_FINGERPRINT])
    print(f"   Repo added for milestone {milestone} (sparse OK in 2025).")
    run(["apt", "update"], check=False)  # Warns on no Release, but continues

def install_crostini_tools():
    run(["apt", "install", "-y", "cros-guest-tools"])
    run(["apt", "install", "-y", "adwaita-icon-theme-full", "-f"])  # -f fixes any deps
    print("   [OK] Tools installed (file sharing, GUI, audio ready).")

def apply_user_groups():
    script = Path.home() / "update-groups"
    if not script.exists():
        raise RuntimeError("update-groups missing – run pre-reboot phase")
    run(["bash", str(script)])
    script.unlink()

def set_hostname():
    default = "ubuntu-crostini"
    hn = input(f"Hostname [{default}]: ").strip() or default
    run(["hostnamectl", "set-hostname", hn])

# ----------------------------------------------------------------------
# REGISTER (No cros-ui-config Step)
# ----------------------------------------------------------------------
step("Fix GPG Keys", "Import missing keys", fix_gpg_keys, detector=gpg_keys_present)
step("Capture Groups", "Save default user groups", capture_groups, detector=groups_script_exists)
step("Remove Default User", "Delete ubuntu cloud-init user", remove_default_user, detector=default_user_removed)
step("Add Crostini Repo", "Enable cros-packages (non-blocking)", add_cros_repo, detector=lambda: cros_repo_present() and cros_key_present())
step("Install Crostini Tools", "cros-guest-tools + icons (no config patch)", install_crostini_tools, detector=crostini_tools_installed)
step("Apply Groups", "Restore groups post-reboot", apply_user_groups, pre_reboot=False, detector=lambda: not groups_script_exists())
step("Set Hostname", "Optional hostname", set_hostname, pre_reboot=False, detector=lambda: False)

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    print_banner("Crostini Ubuntu 24.04 Integration (Nov 2025)")
    if os.geteuid() != 0:
        print("Run with sudo.")
        sys.exit(1)

    pre = [s for s in STEPS if s["pre_reboot"]]
    post = [s for s in STEPS if not s["pre_reboot"]]

    pre_done = all(s["detector"]() for s in pre)
    post_done = all(s["detector"]() for s in post)

    if pre_done and post_done:
        print("Integration complete.")
        return

    pending = [s for s in (pre if not pre_done else post) if not s["detector"]()]
    phase = "PRE-REBOOT" if not pre_done else "POST-REBOOT"

    print(f"\n{phase} STEPS:")
    for i, s in enumerate(pending, 1):
        status = "DONE" if s["detector"]() else "PENDING"
        print(f"  [{i}] [{status}] {s['name']}")
        print(textwrap.indent(s['desc'], "      ") + "\n")

    if not confirm("Proceed?"):
        return

    for i, s in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] {s['name']}")
        try:
            s["func"]()
            print("   [OK]")
        except Exception as e:
            print(f"   [ERROR] {e}")
            if not confirm("Continue?"):
                return

    if not pre_done:
        print_banner("REBOOT REQUIRED")
        print("After reboot, run:")
        print("    sudo python3 setup-ubuntu-crostini.py")
    else:
        print_banner("COMPLETE")
        print("Test: ls /mnt/chromeos/MyFiles, zenity --info --text='GTK OK?', firefox &")

if __name__ == "__main__":
    main()
