"""
install_service.py — Register the system as an OS background service.

Windows : Creates a Windows Task Scheduler task (no admin needed for user tasks).
          The task starts automatically on logon, survives desktop lock,
          and uses pythonw.exe (no console window).

Linux   : Creates a systemd USER service.
          loginctl enable-linger ensures it runs even after logout.

Usage:
  python install_service.py           # install (default)
  python install_service.py remove    # uninstall
  python install_service.py status    # check status
"""
import os, sys, platform, subprocess, textwrap

TASK    = "SAROptionsSystem"
SERVICE = "sar-options"
DIR     = os.path.dirname(os.path.abspath(__file__))
APP     = os.path.join(DIR, "app.py")


# ── Windows ───────────────────────────────────────────────────────────────────

def _pythonw():
    base = os.path.dirname(sys.executable)
    for p in [os.path.join(base, "pythonw.exe"),
               sys.executable.replace("python.exe", "pythonw.exe")]:
        if os.path.exists(p):
            return p
    return sys.executable


def win_install():
    pw = _pythonw()
    xml = textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-16"?>
    <Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
      <RegistrationInfo>
        <Description>SAR Options Intraday Trading System</Description>
      </RegistrationInfo>
      <Triggers>
        <LogonTrigger><Enabled>true</Enabled><Delay>PT1M</Delay></LogonTrigger>
      </Triggers>
      <Principals>
        <Principal id="Author">
          <LogonType>InteractiveToken</LogonType>
          <RunLevel>HighestAvailable</RunLevel>
        </Principal>
      </Principals>
      <Settings>
        <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
        <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
        <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
        <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
        <Priority>5</Priority>
        <RestartOnFailure><Interval>PT1M</Interval><Count>10</Count></RestartOnFailure>
      </Settings>
      <Actions>
        <Exec>
          <Command>{pw}</Command>
          <Arguments>"{APP}"</Arguments>
          <WorkingDirectory>{DIR}</WorkingDirectory>
        </Exec>
      </Actions>
    </Task>
    """)
    xp = os.path.join(DIR, "_task.xml")
    with open(xp, "w", encoding="utf-16") as f:
        f.write(xml)
    try:
        subprocess.run(["schtasks","/delete","/tn",TASK,"/f"], capture_output=True)
        r = subprocess.run(["schtasks","/create","/tn",TASK,"/xml",xp,"/f"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[ERROR] {r.stderr.strip()}")
            return
        subprocess.run(["schtasks","/run","/tn",TASK], capture_output=True)
        print(f"[OK] Task '{TASK}' installed and started.")
        print(f"     Dashboard → http://localhost:5000")
    finally:
        try: os.remove(xp)
        except: pass


def win_remove():
    subprocess.run(["schtasks","/end","/tn",TASK], capture_output=True)
    r = subprocess.run(["schtasks","/delete","/tn",TASK,"/f"],
                       capture_output=True, text=True)
    print("[OK] Removed." if r.returncode == 0 else f"[WARN] {r.stderr.strip()}")


def win_status():
    r = subprocess.run(["schtasks","/query","/tn",TASK,"/fo","LIST"],
                       capture_output=True, text=True)
    print(r.stdout if r.returncode == 0 else f"Task '{TASK}' not found.")


# ── Linux ─────────────────────────────────────────────────────────────────────

def lin_install():
    sd = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(sd, exist_ok=True)
    unit = textwrap.dedent(f"""\
    [Unit]
    Description=SAR Options Intraday Trading System
    After=network-online.target

    [Service]
    Type=simple
    WorkingDirectory={DIR}
    ExecStart={sys.executable} {APP}
    Restart=always
    RestartSec=15
    StandardOutput=append:{DIR}/trading.log
    StandardError=append:{DIR}/trading.log

    [Install]
    WantedBy=default.target
    """)
    sf = os.path.join(sd, f"{SERVICE}.service")
    with open(sf, "w") as f:
        f.write(unit)
    for cmd in [["systemctl","--user","daemon-reload"],
                ["systemctl","--user","enable",SERVICE],
                ["systemctl","--user","start", SERVICE],
                ["loginctl","enable-linger",os.environ.get("USER","")]]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(f"[{'OK' if r.returncode==0 else 'WARN'}] {' '.join(cmd)}")
    print(f"\n[OK] Service '{SERVICE}' running. Dashboard → http://localhost:5000")


def lin_remove():
    for cmd in [["systemctl","--user","stop",SERVICE],
                ["systemctl","--user","disable",SERVICE]]:
        subprocess.run(cmd, capture_output=True)
    sf = os.path.expanduser(f"~/.config/systemd/user/{SERVICE}.service")
    try:
        os.remove(sf)
        subprocess.run(["systemctl","--user","daemon-reload"], capture_output=True)
        print("[OK] Service removed.")
    except FileNotFoundError:
        print("[WARN] Service file not found.")


def lin_status():
    r = subprocess.run(["systemctl","--user","status",SERVICE],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)


# ── Entry ─────────────────────────────────────────────────────────────────────

def main():
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "install"
    is_win = platform.system() == "Windows"
    print("="*55)
    print(f"  SAR Options System — Service Manager")
    print(f"  Platform: {platform.system()}  Action: {action}")
    print("="*55)
    dispatch = {"install": win_install if is_win else lin_install,
                "remove":  win_remove  if is_win else lin_remove,
                "status":  win_status  if is_win else lin_status}
    dispatch.get(action, dispatch["install"])()

if __name__ == "__main__":
    main()
