#!/usr/bin/env python3
"""
setup-ubuntu-crostini.py – Silent, Developer-First Crostini Integration

* No dpkg spam
* No marker files
* No common tools
* Only essential GUI/file/audio integration
* Pre/post-reboot aware
* 100% investigative
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
CROS_KEY_FINGERPRINT = "1397BC53640DB551"
CROS_UI_CONFIG_PKG = "cros-ui-config"
CROS_UI_FIXED_DEB = Path("cros-ui-config_fixed.deb")

# ----------------------------------------------------------------------
# UTILS
# ----------------------------------------------------------------------
def run(cmd, check=True, capture=False):
    """Run command, capture stdout/stderr if needed, suppress all output."""
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
# SILENT DETECTORS (no dpkg spam)
# ----------------------------------------------------------------------
def _dpkg_status(pkg):
    """Return True if package is installed, silently."""
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

def cros_ui_config_patched():
    return CROS_UI_FIXED_DEB.exists()

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

def patch_cros_ui_config():
    if CROS_UI_FIXED_DEB.exists():
        return
    run(["apt", "download", CROS_UI_CONFIG_PKG])
    import glob
    deb = next((f for f in glob.glob("cros-ui-config_*_all.deb")), None)
    if not deb:
        raise RuntimeError("cros-ui-config package not found")
    run(["ar", "x", deb])
    settings_path = "./etc/gtk-3.0/settings.ini"
    if not Path(settings_path).exists():
        raise RuntimeError("settings.ini not in package")
    settings = Path(settings_path).read_text()
    settings = settings.replace("InhibitAllGtkDialogs=1", "InhibitAllGtkDialogs=0")
    Path("settings.ini").write_text(settings)
    run(["gzip", "-c", "settings.ini"], capture=True)
    files = [f for f in Path(".").iterdir() if f.name != "settings.ini"]
    run(["tar", "czf", "data.tar.gz", "--transform", "s,^settings.ini,./etc/gtk-3.0/settings.ini,", "settings.ini", *files])
    run(["ar", "r", deb, "debian-binary", "control.tar.xz", "data.tar.gz"])
    run(["mv", deb, str(CROS_UI_FIXED_DEB)])

def install_crostini_tools():
    run(["apt", "install", "-y", "cros-guest-tools", str(CROS_UI_FIXED_DEB)])
    run(["apt", "install", "-y", "adwaita-icon-theme-full"])
    CROS_UI_FIXED_DEB.unlink(missing_ok=True)

def apply_user_groups():
    script = Path.home() / "update-groups"
    if not script.exists():
        raise RuntimeError("update-groups missing – run pre-reboot phase")
    run(["bash", str(script)])
    script.unlink()

def set_hostname():
    default = "crostini"
    hn = input(f"Hostname [{default}]: ").strip() or default
    run(["hostnamectl", "set-hostname", hn])

# ----------------------------------------------------------------------
# REGISTER
# ----------------------------------------------------------------------
step("Fix GPG Keys", "Import missing keys", fix_gpg_keys, detector=gpg_keys_present)
step("Capture Groups", "Save default user groups", capture_groups, detector=groups_script_exists)
step("Remove Default User", "Delete ubuntu cloud-init user", remove_default_user, detector=default_user_removed)
step("Add Crostini Repo", "Enable cros-packages", add_cros_repo, detector=lambda: cros_repo_present() and cros_key_present())
step("Patch cros-ui-config", "Fix GTK dialog lock", patch_cros_ui_config, detector=cros_ui_config_patched)
step("Install Crostini Tools", "cros-guest-tools + icons", install_crostini_tools, detector=crostini_tools_installed)
step("Apply Groups", "Restore groups post-reboot", apply_user_groups, pre_reboot=False, detector=lambda: not groups_script_exists())
step("Set Hostname", "Optional hostname", set_hostname, pre_reboot=False, detector=lambda: False)

# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    print_banner("Crostini Ubuntu Integration")
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
        print("Test: /mnt/chromeos/MyFiles, zenity --info")

if __name__ == "__main__":
    main()
