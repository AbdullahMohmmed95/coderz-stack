"""
Coderz Stack — Automated Backup Flow
Runs daily at 03:00 AM, backs up all services, alerts on failure.
"""
import subprocess
import os
from datetime import datetime
from prefect import flow, task, get_run_logger
from prefect.schedules import CronSchedule


BACKUP_SCRIPT = "/opt/coderz/backup/backup.sh"
BACKUP_ROOT   = "/opt/coderz/backup/snapshots"
KEEP_DAYS     = 7

ALERT_EMAIL   = os.getenv("ALERT_EMAIL", "aboodm7med1995@gmail.com")
SMTP_HOST     = os.getenv("ALERT_SMTP_HOST", "host.docker.internal")
SMTP_PORT     = int(os.getenv("ALERT_SMTP_PORT", "25"))


# ── Tasks ──────────────────────────────────────────────────────────

@task(name="run-backup-script", retries=1, retry_delay_seconds=30)
def run_backup(keep_days: int = KEEP_DAYS) -> dict:
    logger = get_run_logger()
    logger.info(f"Starting backup — keep_days={keep_days}")

    result = subprocess.run(
        ["bash", BACKUP_SCRIPT, "--keep", str(keep_days)],
        capture_output=True, text=True, timeout=1800
    )
    logger.info(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
    if result.returncode != 0:
        logger.error(result.stderr)
        raise RuntimeError(f"Backup script failed (exit {result.returncode})")

    # Parse summary from output
    passed = skipped = failed = size = "?"
    for line in result.stdout.splitlines():
        if "Passed" in line:   passed  = line.split(":")[-1].strip()
        if "Failed" in line:   failed  = line.split(":")[-1].strip()
        if "Skipped" in line:  skipped = line.split(":")[-1].strip()
        if "Total size" in line: size  = line.split(":")[-1].strip()

    return {"passed": passed, "failed": failed, "skipped": skipped, "size": size}


@task(name="check-backup-size")
def check_backup_size() -> str:
    import os
    latest = os.path.join(BACKUP_ROOT, "latest")
    if not os.path.exists(latest):
        return "unknown"
    result = subprocess.run(["du", "-sh", latest], capture_output=True, text=True)
    return result.stdout.split()[0] if result.returncode == 0 else "unknown"


@task(name="list-snapshots")
def list_snapshots() -> list[str]:
    import os
    if not os.path.exists(BACKUP_ROOT):
        return []
    return sorted([
        d for d in os.listdir(BACKUP_ROOT)
        if os.path.isdir(os.path.join(BACKUP_ROOT, d)) and d.startswith("20")
    ])


@task(name="send-alert")
def send_alert(subject: str, body: str):
    logger = get_run_logger()
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = ALERT_EMAIL
        msg["To"]      = ALERT_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.sendmail(ALERT_EMAIL, [ALERT_EMAIL], msg.as_string())
        logger.info(f"Alert sent: {subject}")
    except Exception as e:
        logger.warning(f"Alert email failed: {e}")


# ── Flow ───────────────────────────────────────────────────────────

@flow(
    name="coderz-backup",
    description="Daily backup of all Coderz Stack services",
)
def backup_flow(keep_days: int = KEEP_DAYS):
    logger = get_run_logger()
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"Backup flow started at {started_at}")

    try:
        summary = run_backup(keep_days=keep_days)
        size    = check_backup_size()
        snaps   = list_snapshots()

        logger.info(
            f"Backup done — passed={summary['passed']} "
            f"failed={summary['failed']} skipped={summary['skipped']} "
            f"size={size} total_snapshots={len(snaps)}"
        )

        if summary["failed"] != "0" and summary["failed"] != "?":
            send_alert(
                subject=f"⚠️ Coderz Backup — {summary['failed']} failure(s) on {started_at}",
                body=(
                    f"Backup completed with failures.\n\n"
                    f"  Passed  : {summary['passed']}\n"
                    f"  Failed  : {summary['failed']}\n"
                    f"  Skipped : {summary['skipped']}\n"
                    f"  Size    : {size}\n\n"
                    f"Snapshots available: {len(snaps)}\n"
                    f"Latest: {snaps[-1] if snaps else 'none'}\n\n"
                    f"Check logs: {BACKUP_ROOT}/latest/backup.log"
                )
            )
        else:
            send_alert(
                subject=f"✅ Coderz Backup — OK ({size}) on {started_at}",
                body=(
                    f"All services backed up successfully.\n\n"
                    f"  Passed  : {summary['passed']}\n"
                    f"  Skipped : {summary['skipped']}\n"
                    f"  Size    : {size}\n\n"
                    f"Snapshots kept: {len(snaps)} (last {keep_days} days)\n"
                    f"Latest: {snaps[-1] if snaps else 'none'}\n"
                    f"Location: {BACKUP_ROOT}/latest/"
                )
            )

    except Exception as exc:
        logger.error(f"Backup flow failed: {exc}")
        send_alert(
            subject=f"🔴 Coderz Backup FAILED on {started_at}",
            body=(
                f"The backup flow crashed with an error:\n\n"
                f"  {exc}\n\n"
                f"Manual action required. Run:\n"
                f"  bash /opt/coderz/backup/backup.sh\n"
            )
        )
        raise


if __name__ == "__main__":
    backup_flow()
