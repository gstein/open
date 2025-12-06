#!/usr/bin/env python3
"""
setup-ubuntu-crostini.py – Crostini Ubuntu 24.04 (Nov 2025)

* Milestone 141+ (sparse repo OK)
* Modern key import (no apt-key)
* Accurate detection
* update-groups in /root, moved post-reboot
"""

import os
import sys
import subprocess
import textwrap
from pathlib import Path

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
CROS_REPO_BASE = "https://storage.googleapis.com/cros-packages"
DEBIAN_NAME = "trixie"  # underlies 24.04
TERMINA_DEB = Path("/opt/google/cros-containers/cros-guest-tools.deb")
UPDATE_GROUPS_FILE = Path("/root/update-groups")
GOOGLE_KEY_URL = "https://dl.google.com/linux/linux_signing_key.pub"
GOOGLE_KEY_FILE = Path("/etc/apt/trusted.gpg.d/google.asc")

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
    r = subprocess.run(
        ["dpkg-query", "-W", "-f", "${Status}", pkg],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return r.returncode == 0

def groups_script_exists():
    return UPDATE_GROUPS_FILE.exists()

def default_user_removed():
    return not Path("/home/ubuntu").exists()

def cros_repo_present():
    f = Path("/etc/apt/sources.list.d/cros.list")
    return f.exists() and CROS_REPO_BASE in f.read_text()

def google_key_present():
    return GOOGLE_KEY_FILE.exists()

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
def capture_groups():
    if UPDATE_GROUPS_FILE.exists():
        return
    try:
        groups = run(["groups", "ubuntu"], capture=True).stdout.strip()
    except:
        groups = "adm,dialout,cdrom,sudo,audio,video,plugdev,users,input,netdev"
    UPDATE_GROUPS_FILE.write_text(f"sudo usermod -aG {groups} $USER\n")
    print(f"   Saved groups to {UPDATE_GROUPS_FILE}")

def remove_default_user():
    run(["killall", "-u", "ubuntu"], check=False)
    run(["userdel", "-r", "ubuntu"], check=False)
    sudoers = Path("/etc/sudoers.d/90-cloud-init-users")
    if sudoers.exists():
        lines = [l for l in sudoers.read_text().splitlines() if not l.startswith("ubuntu")]
        sudoers.write_text("\n".join(lines) + "\n")

def add_cros_repo():
    repo_file = Path("/etc/apt/sources.list.d/cros.list")
    milestone = Path("/dev/.cros_milestone").read_text().strip() if Path("/dev/.cros_milestone").exists() else "stretch"
    repo_file.write_text(f"deb {CROS_REPO_BASE}/{milestone} {DEBIAN_NAME} main\n")
    run(["wget", "-q", "-O", "-", GOOGLE_KEY_URL], capture=True)
    run(["tee", str(GOOGLE_KEY_FILE)])
    print(f"   Repo added for milestone {milestone} (sparse OK).")
    run(["apt", "update"], check=False)

def install_crostini_tools():
    if TERMINA_DEB.exists():
        run(["dpkg", "-i", str(TERMINA_DEB)], check=False)
        print("   Installed cros-guest-tools from termina cache.")
    else:
        run(["apt", "install", "-y", "cros-guest-tools"], check=False)
        print("   Attempted apt install of cros-guest-tools.")
    run(["apt", "install", "-y", "adwaita-icon-theme-full", "-f"])
    print("   [OK] Tools installed.")

def apply_user_groups():
    if not UPDATE_GROUPS_FILE.exists():
        raise RuntimeError("update-groups missing – run pre-reboot")
    local_script = Path.home() / "update-groups"
    run(["mv", str(UPDATE_GROUPS_FILE), str(local_script)])
    run(["chown", f"{os.getlogin()}:{os.getlogin()}", str(local_script)])
    run(["bash", str(local_script)])
    local_script.unlink()

def set_hostname():
    default = "ubuntu-crostini"
    hn = input(f"Hostname [{default}]: ").strip() or default
    run(["hostnamectl", "set-hostname", hn])

# ----------------------------------------------------------------------
# REGISTER STEPS
# ----------------------------------------------------------------------
step("Capture Groups", "Save default user groups", capture_groups, detector=groups_script_exists)
step("Remove Default User", "Delete ubuntu cloud-init user", remove_default_user, detector=default_user_removed)
step("Add Crostini Repo", "Enable cros-packages (non-blocking)", add_cros_repo, detector=lambda: cros_repo_present() and google_key_present())
step("Install Crostini Tools", "cros-guest-tools + icons", install_crostini_tools, detector=crostini_tools_installed)
step("Apply Groups", "Restore groups post-reboot", apply_user_groups, pre_reboot=False, detector=lambda: not groups_script_exists())
step("Set Hostname", "Optional hostname", set_hostname, pre_reboot=False, detector=lambda: False)

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    print_banner("Crostini Ubuntu 24.04 (Nov 2025)")
    if os.geteuid() != 0:
        print("Run with sudo.")
        sys.exit(1)

    pre = [s for s in STEPS if s["pre_reboot"]]
    post = [s for s in STEPS if not s["pre_reboot"]]

    pre_done = all(s["detector"]() for s in pre)
    post_done = all(s["detector"]() for s in post)

    if pre_done and post_done:
        print("All steps complete.")
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
        print("After reboot:")
        print("1. Open the Terminal app (boots container, creates user).")
        print("2. sudo ./setup.py  # Runs post-reboot from your location")
    else:
        print_banner("COMPLETE")
        print("Test: ls /mnt/chromeos/MyFiles, zenity --info --text='OK', firefox &")

if __name__ == "__main__":
    main()
