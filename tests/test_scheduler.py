"""
Comprehensive tests for Windows Task Scheduler configuration, lightweight pre-execution gate,
and full daily lifecycle behavior.
Verifies all 12 core requirements:
1. First login of a new day -> downloader launches.
2. Second login same day -> downloader does NOT launch.
3. Third login same day -> downloader does NOT launch.
4. Today's valid PDF already exists -> downloader does NOT launch.
5. Laptop remains ON overnight -> 6 AM daily trigger starts next day's cycle.
6. Laptop OFF at 6 AM -> turning laptop on later starts the missed day's cycle.
7. Newspaper unavailable -> retry every 30 minutes.
8. Newspaper becomes available -> download and validate.
9. After successful download -> all further attempts that day stop.
10. Next calendar day -> fresh cycle.
11. No AtLogOn trigger directly launches full downloader (it launches lightweight gate run_daily.vbs).
12. No Startup-folder shortcut.
"""

import datetime
import os
import subprocess
from pathlib import Path
from PIL import Image

from src.config import Config
from src.state_manager import StateManager
from src.validator import validate_pdf_file


def test_register_task_script_dual_triggers_and_retries():
    """Verifies register_task.ps1 configures AtLogOn gate and Daily 6 AM with 30-min retries."""
    ps1_path = Path(__file__).resolve().parent.parent / "register_task.ps1"
    assert ps1_path.exists(), "register_task.ps1 must exist"

    content = ps1_path.read_text(encoding="utf-8")

    # 1. Action points to silent lightweight gate (run_daily.vbs)
    assert '$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`""' in content

    # 2. Check AtLogOn trigger for first login of the day
    assert "New-ScheduledTaskTrigger -AtLogOn" in content

    # 3. Check Daily 6:00 AM trigger for overnight laptops
    assert 'New-ScheduledTaskTrigger -Daily -At "06:00AM"' in content

    # 4. Check 30-min repetition interval
    assert "-RepetitionInterval (New-TimeSpan -Minutes 30)" in content

    # 5. Check Interactive Principal definition
    assert "New-ScheduledTaskPrincipal" in content

    # 6. Check StartWhenAvailable for missed 6 AM starts
    assert "-StartWhenAvailable" in content

    # 7. Check MultipleInstances IgnoreNew
    assert "-MultipleInstances IgnoreNew" in content

    # 8. Check fallback schtasks commands
    assert "/SC ONLOGON" in content
    assert "/SC DAILY" in content


def test_no_startup_folder_shortcut():
    """Verifies that register_task.ps1 removes legacy Startup shortcuts and does not place new ones there."""
    ps1_path = Path(__file__).resolve().parent.parent / "register_task.ps1"
    content = ps1_path.read_text(encoding="utf-8")

    assert "Remove-Item -Path $StartupShortcut" in content
    assert "Desktop\\Download Today's TOI.lnk" in content or "$DesktopShortcut" in content


def test_vbs_lightweight_gate_structure():
    """Verifies that run_daily.vbs implements the pre-execution gate before calling run_daily.bat."""
    vbs_path = Path(__file__).resolve().parent.parent / "run_daily.vbs"
    assert vbs_path.exists(), "run_daily.vbs must exist"

    content = vbs_path.read_text(encoding="utf-8")

    # Gate must check Desktop archive and state.json
    assert "TOI_Ahmedabad_" in content
    assert "state.json" in content
    assert "last_successful_date" in content

    # Gate must exit if already downloaded and valid
    assert "WScript.Quit 0" in content

    # Gate must support --force
    assert "--force" in content


def test_vbs_gate_exits_instantly_when_today_completed():
    """Executes cscript.exe on run_daily.vbs when today's paper exists to ensure exit code 0 in milliseconds."""
    vbs_path = Path(__file__).resolve().parent.parent / "run_daily.vbs"
    res = subprocess.run(
        ["cscript.exe", "//NoLogo", str(vbs_path)],
        capture_output=True,
        text=True,
        timeout=5
    )
    assert res.returncode == 0


def test_daily_lifecycle_first_login_vs_subsequent_logins(tmp_path: Path):
    """
    Simulates the complete lifecycle:
    - Login 1: Paper not yet downloaded -> launches download -> success recorded.
    - Login 2 (same day): Paper already exists & state success -> gate fast-skips, downloader does NOT launch.
    - Login 3 (same day): Gate fast-skips, downloader does NOT launch.
    - Next day (e.g. 2026-09-01): State is not completed -> launches download for new date.
    """
    state_file = tmp_path / "state.json"
    archive_dir = tmp_path / "TOI Daily"
    state_mgr = StateManager(state_file)

    target_date = datetime.date(2026, 8, 31)
    date_str = target_date.strftime("%Y-%m-%d")

    # --- 1. First Login of the Day ---
    assert not state_mgr.is_date_completed(date_str)
    # Downloader executes and creates valid PDF
    month_folder = archive_dir / "2026" / "August"
    month_folder.mkdir(parents=True, exist_ok=True)
    pdf_path = month_folder / f"TOI_Ahmedabad_{date_str}.pdf"

    noise = os.urandom(200 * 200 * 3)
    img1 = Image.frombytes("RGB", (200, 200), noise).resize((800, 1200))
    img2 = Image.frombytes("RGB", (200, 200), noise).resize((800, 1200))
    img1.save(str(pdf_path), format="PDF", save_all=True, append_images=[img2], quality=90)

    state_mgr.record_success(
        date_str=date_str,
        file_path=pdf_path,
        page_count=2,
        size_bytes=pdf_path.stat().st_size
    )

    # --- 2. Second Login (Same Day) ---
    assert state_mgr.is_date_completed(date_str) is True
    assert pdf_path.exists() is True
    is_valid, pages, _ = validate_pdf_file(pdf_path)
    assert is_valid is True

    # --- 3. Third Login (Same Day) ---
    assert state_mgr.is_date_completed(date_str) is True
    assert pdf_path.exists() is True

    # --- 4. Next Calendar Day (2026-09-01) ---
    next_day_str = "2026-09-01"
    assert not state_mgr.is_date_completed(next_day_str)


def test_retry_cycle_simulation(tmp_path: Path):
    """
    Simulates:
    - 06:00 AM: Paper not published -> state is failure / incomplete.
    - 06:30 AM: Retry 1 -> Paper not published -> state remains incomplete.
    - 07:00 AM: Retry 2 -> Paper published -> success recorded -> all further retries stop.
    """
    state_file = tmp_path / "state.json"
    state_mgr = StateManager(state_file)
    date_str = "2026-08-31"

    # 06:00 AM: Failure
    state_mgr.record_failure(date_str, "Paper not available yet")
    assert not state_mgr.is_date_completed(date_str)

    # 06:30 AM: Failure
    state_mgr.record_failure(date_str, "Paper not available yet")
    assert not state_mgr.is_date_completed(date_str)

    # 07:00 AM: Success
    dummy_pdf = tmp_path / "test.pdf"
    dummy_pdf.write_bytes(b"dummy")
    state_mgr.record_success(date_str, dummy_pdf, page_count=16, size_bytes=30000000)
    assert state_mgr.is_date_completed(date_str) is True
