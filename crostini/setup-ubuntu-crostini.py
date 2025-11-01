#!/usr/bin/env python3
"""
setup-ubuntu-crostini.py  –  Ubuntu-on-Crostini installer

* 100% restartable
* No marker files – every step is detected by inspecting the system
* Interactive checklist + user confirmation
* Pre-reboot & post-reboot phases

Complements to Grok4 for original construction.
"""

import os
import sys
import subprocess
import textwrap
from pathlib import Path
from datetime import datetime

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
UBUNTU_VERSION = "24.04"
CROS_REPO_BASE = "https://storage.googleapis.com/cros-packages"
CROS_KEY_FINGERPRINT = "1397BC53640DB551"
CROS_UI_CONFIG_PKG = "cros-ui-config"
CROS_UI_FIXED_DEB = Path("cros-ui-config_fixed.deb")

# ----------------------------------------------------------------------
# UTILS
# ----------------------------------------------------------------------
def run(cmd, check=True, capture=False):
    print(f"$ {' '.join(map(str, cmd))}")
    return subprocess.run(
        cmd, check=check, capture_output=capture, text=True, encoding="utf-8"
    )

def confirm(prompt):
    while True:
        resp = input(f"{prompt} [y/N] ").strip().lower()
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no", ""):
            return False

def print_banner(text):
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70 + "\n")

# ----------------------------------------------------------------------
# STEP DEFINITIONS
# ----------------------------------------------------------------------
STEPS = []

def step(name, desc, func, pre_reboot=True, detector=None):
    """
    detector(step) -> bool   (True = already done)
    """
    STEPS.append({
        "name": name,
        "desc": desc,
        "func": func,
        "pre_reboot": pre_reboot,
        "detector": detector or (lambda: False),
    })

# ----------------------------------------------------------------------
# DETECTORS
# ----------------------------------------------------------------------
def gpg_keys_present():
    # Ubuntu archive keys that are missing in a fresh container
    required = {"7638D0442B90D010", "04EE7237B7D453EC"}
    try:
        out = run(["apt-key", "list"], capture=True).stdout
        return all(k in out for k in required)
    except:
        return False

def groups_script_exists():
    return (Path.home() / "update-groups").exists()

def default_user_removed():
    return not Path("/home/ubuntu").exists() and \
           not any(l.startswith("ubuntu") for l in Path("/etc/sudoers.d/90-cloud-init-users").read_text().splitlines() if l.strip())

def cros_repo_present():
    repo_file = Path("/etc/apt/sources.list.d/cros.list")
    if not repo_file.exists():
        return False
    line = repo_file.read_text().strip()
    return line.startswith("deb") and CROS_REPO_BASE in line

def cros_key_present():
    try:
        out = run(["apt-key", "list"], capture=True).stdout
        return CROS_KEY_FINGERPRINT in out
    except:
        return False

def binutils_installed():
    return run(["dpkg", "-s", "binutils"], check=False).returncode == 0

def cros_ui_config_fixed():
    return CROS_UI_FIXED_DEB.exists()

def crostini_tools_installed():
    return (run(["dpkg", "-s", "cros-guest-tools"], check=False).returncode == 0 and
            run(["dpkg", "-s", "adwaita-icon-theme-full"], check=False).returncode == 0)

def common_tools_installed():
    pkgs = ["curl", "wget", "git", "vim", "nano", "htop"]
    return all(run(["dpkg", "-s", p], check=False).returncode == 0 for p in pkgs)

# ----------------------------------------------------------------------
# STEP IMPLEMENTATIONS
# ----------------------------------------------------------------------
def fix_gpg_keys():
    keys = ["7638D0442B90D010", "04EE7237B7D453EC"]
    for k in keys:
        run(["apt-key", "adv", "--keyserver", "keyserver.ubuntu.com", "--recv-keys", k], check=False)
    run(["apt", "update", "--fix-missing"], check=False)

def capture_groups():
    user = os.getenv("USER", "ubuntu")
    script = Path.home() / "update-groups"
    if script.exists():
        return
    try:
        groups = run(["groups", user], capture=True).stdout.strip()
    except:
        groups = "adm dialout cdrom sudo audio video plugdev users input netdev"
    content = f"sudo usermod -aG {groups.replace(' ', ',')} $USER\n"
    script.write_text(content)
    print(f"   Groups saved to {script}")

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
        try:
            milestone = Path("/dev/.cros_milestone").read_text().strip()
        except:
            pass
    line = f"deb {CROS_REPO_BASE}/{milestone} {milestone} main\n"
    repo_file.write_text(line)
    run(["apt-key", "adv", "--keyserver", "keyserver.ubuntu.com", "--recv-keys", CROS_KEY_FINGERPRINT])

def install_binutils():
    run(["apt", "install", "-y", "binutils"])

def patch_cros_ui_config():
    if CROS_UI_FIXED_DEB.exists():
        return
    run(["apt", "download", CROS_UI_CONFIG_PKG])
    import glob
    deb = glob.glob("cros-ui-config_*_all.deb")
    if not deb:
        raise RuntimeError("cros-ui-config package not found")
    deb = deb[0]

    run(["ar", "x", deb])
    run(["gunzip", "-c", "data.tar.gz"], capture=True)  # just to extract
    settings_path = "./etc/gtk-3.0/settings.ini"
    if not Path(settings_path).exists():
        raise RuntimeError("settings.ini missing in package")
    settings = Path(settings_path).read_text()
    settings = settings.replace("InhibitAllGtkDialogs=1", "InhibitAllGtkDialogs=0")
    Path("settings.ini").write_text(settings)
    run(["gzip", "-c", "settings.ini"], capture=True)
    # rebuild data.tar.gz
    files = [f for f in Path(".").iterdir() if f.name != "settings.ini"]
    run(["tar", "czf", "data.tar.gz", "--transform",
         "s,^settings.ini,./etc/gtk-3.0/settings.ini,", "settings.ini", *files])
    run(["ar", "r", deb, "debian-binary", "control.tar.xz", "data.tar.gz"])
    run(["mv", deb, str(CROS_UI_FIXED_DEB)])

def install_crostini_tools():
    run(["apt", "install", "-y", "cros-guest-tools", str(CROS_UI_FIXED_DEB)])
    run(["apt", "install", "-y", "adwaita-icon-theme-full"])
    CROS_UI_FIXED_DEB.unlink(missing_ok=True)

def install_common_tools():
    run(["apt", "install", "-y", "curl", "wget", "git", "vim", "nano", "htop"])

def apply_user_groups():
    script = Path.home() / "update-groups"
    if not script.exists():
        raise RuntimeError("update-groups script missing – run pre-reboot first")
    run(["bash", str(script)])
    script.unlink()

def set_hostname():
    default = "crostini"
    hostname = input(f"Enter hostname (default: {default}): ").strip()
    if not hostname:
        hostname = default
    run(["hostnamectl", "set-hostname", hostname])

# ----------------------------------------------------------------------
# REGISTER STEPS
# ----------------------------------------------------------------------
step("Fix GPG Keys", "Import missing Ubuntu archive keys", fix_gpg_keys,
     detector=lambda: gpg_keys_present())
step("Capture Default Groups", "Save groups of the default 'ubuntu' user", capture_groups,
     detector=lambda: groups_script_exists())
step("Remove Default ubuntu User", "Delete cloud-init user & sudo entry", remove_default_user,
     detector=lambda: default_user_removed())
step("Add Crostini Package Repo", "Enable cros-packages repository", add_cros_repo,
     detector=lambda: cros_repo_present() and cros_key_present())
step("Install binutils", "Required for .deb repacking", install_binutils,
     detector=lambda: binutils_installed())
step("Patch cros-ui-config", "Disable GTK dialog inhibition", patch_cros_ui_config,
     detector=lambda: cros_ui_config_fixed())
step("Install Crostini Tools", "cros-guest-tools + icon theme", install_crostini_tools,
     detector=lambda: crostini_tools_installed())
step("Install Common Tools", "curl, git, vim, etc.", install_common_tools,
     detector=lambda: common_tools_installed())
step("Apply User Groups", "Restore groups to your account", apply_user_groups,
     pre_reboot=False, detector=lambda: not groups_script_exists())
step("Set Hostname", "Optional hostname change", set_hostname,
     pre_reboot=False, detector=lambda: False)  # always offered

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    print_banner("Ubuntu Crostini Setup Wizard")
    print(f"User: {os.getenv('USER')}   UID: {os.getuid()}")
    if os.geteuid() != 0:
        print("This script must be run with sudo.")
        sys.exit(1)

    # Separate phases
    pre_steps = [s for s in STEPS if s["pre_reboot"]]
    post_steps = [s for s in STEPS if not s["pre_reboot"]]

    pre_done = all(s["detector"]() for s in pre_steps)
    post_done = all(s["detector"]() for s in post_steps)

    if pre_done and post_done:
        print("All steps already completed!")
        return

    # Determine what to run
    if not pre_done:
        pending = [s for s in pre_steps if not s["detector"]()]
        phase = "PRE-REBOOT"
    else:
        pending = [s for s in post_steps if not s["detector"]()]
        phase = "POST-REBOOT"

    print(f"\n{phase} STEPS TO EXECUTE:")
    for i, s in enumerate(pending, 1):
        status = "DONE" if s["detector"]() else "PENDING"
        print(f"  [{i}] [{status}] {s['name']}")
        print(textwrap.indent(s['desc'], "      "))
        print()

    if not confirm("Proceed?"):
        print("Aborted by user.")
        return

    # Execute
    for i, s in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] {s['name']}")
        print(f"    {s['desc']}")
        try:
            s["func"]()
            print("   [COMPLETED]")
        except Exception as e:
            print(f"   [FAILED] {e}")
            if not confirm("Continue anyway?"):
                print("Setup stopped.")
                return

    # Final guidance
    if not pre_done:
        print_banner("PRE-REBOOT PHASE FINISHED")
        print("Please REBOOT your Chromebook now.")
        print("After reboot, open the Terminal and run:")
        print()
        print("    sudo python3 setup-ubuntu-crostini.py")
        print()
    else:
        print_banner("SETUP COMPLETE")
        print("Ubuntu is now fully integrated with Crostini.")
        print("Test a GUI app:  firefox   or   code")
        print("ChromeOS files are at:  /mnt/chromeos/MyFiles")

if __name__ == "__main__":
    main()
                                                                                      
