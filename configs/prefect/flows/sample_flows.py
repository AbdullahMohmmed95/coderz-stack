"""
Coderz Stack — Prefect Flows
Deployed with cron schedules via prefect.yaml
"""
import datetime
import urllib.request
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from prefect import flow, task, get_run_logger


# ──────────────────────────────────────────────────────────
# Shared config (from environment)
# ──────────────────────────────────────────────────────────

SMTP_HOST  = os.environ.get("ALERT_SMTP_HOST", "host.docker.internal")
SMTP_PORT  = int(os.environ.get("ALERT_SMTP_PORT", "25"))
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "aboodm7med1995@gmail.com")

THRESHOLDS = {
    "cpu_pct":  80.0,   # %
    "mem_pct":  85.0,   # %
    "disk_pct": 90.0,   # % used (= <10% free)
}


# ──────────────────────────────────────────────────────────
# Task: Send email alert
# ──────────────────────────────────────────────────────────

@task(name="send-email-alert", retries=2, retry_delay_seconds=10)
def send_email(subject: str, body: str):
    """Send a plain-text alert email via the local SMTP relay."""
    logger = get_run_logger()
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = ALERT_EMAIL
        msg["To"]      = ALERT_EMAIL
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.sendmail(ALERT_EMAIL, [ALERT_EMAIL], msg.as_string())

        logger.info(f"Alert email sent: {subject}")
    except Exception as exc:
        logger.warning(f"Email send failed: {exc}")
        raise


# ──────────────────────────────────────────────────────────
# Task: Check CPU / Memory / Disk using /proc and /sys
# ──────────────────────────────────────────────────────────

@task(name="cpu-usage", retries=1)
def get_cpu_usage() -> float:
    """Read CPU idle % from /proc/stat and return usage %."""
    logger = get_run_logger()
    import time
    with open("/proc/stat") as f:
        line = f.readline()
    fields = list(map(int, line.split()[1:]))
    idle, total = fields[3], sum(fields)
    time.sleep(0.5)
    with open("/proc/stat") as f:
        line2 = f.readline()
    fields2 = list(map(int, line2.split()[1:]))
    idle2, total2 = fields2[3], sum(fields2)
    cpu_usage = 100.0 * (1.0 - (idle2 - idle) / (total2 - total))
    logger.info(f"CPU usage: {cpu_usage:.1f}%")
    return round(cpu_usage, 2)


@task(name="memory-usage", retries=1)
def get_memory_usage() -> dict:
    """Read memory stats from /proc/meminfo."""
    logger = get_run_logger()
    meminfo = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, val = line.split(":", 1)
            meminfo[key.strip()] = int(val.strip().split()[0])

    total     = meminfo["MemTotal"]
    available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    used_pct  = round(100.0 * (1 - available / total), 2)
    used_mb   = round((total - available) / 1024, 1)
    total_mb  = round(total / 1024, 1)
    logger.info(f"Memory: {used_mb} MB / {total_mb} MB ({used_pct}% used)")
    return {"used_pct": used_pct, "used_mb": used_mb, "total_mb": total_mb}


@task(name="disk-usage", retries=1)
def get_disk_usage(path: str = "/") -> dict:
    """Check disk usage using os.statvfs."""
    logger = get_run_logger()
    st = os.statvfs(path)
    total    = st.f_blocks * st.f_frsize
    free     = st.f_bfree  * st.f_frsize
    used     = total - free
    used_pct = round(100.0 * used / total, 2)
    logger.info(f"Disk ({path}): {used/1e9:.1f} GB / {total/1e9:.1f} GB ({used_pct}% used)")
    return {
        "mount":    path,
        "used_pct": used_pct,
        "used_gb":  round(used / 1e9, 2),
        "total_gb": round(total / 1e9, 2),
    }


# ──────────────────────────────────────────────────────────
# Flow 1: System Health Check — every 5 minutes
# ──────────────────────────────────────────────────────────

@flow(name="system-health-check", log_prints=True)
def system_health_check():
    """Checks CPU, Memory, and Disk usage every 5 minutes."""
    cpu  = get_cpu_usage()
    mem  = get_memory_usage()
    disk = get_disk_usage("/")

    print("=" * 48)
    print(f"  System Health — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 48)
    print(f"  CPU Usage   : {cpu}%")
    print(f"  Memory Used : {mem['used_mb']} MB / {mem['total_mb']} MB ({mem['used_pct']}%)")
    print(f"  Disk Used   : {disk['used_gb']} GB / {disk['total_gb']} GB ({disk['used_pct']}%)")
    print("=" * 48)

    return {"cpu_pct": cpu, "memory": mem, "disk": disk}


# ──────────────────────────────────────────────────────────
# Task: Service health checks
# ──────────────────────────────────────────────────────────

@task(name="check-service", retries=2, retry_delay_seconds=5)
def check_service(name: str, url: str) -> dict:
    """HTTP GET health check for a service."""
    logger = get_run_logger()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = resp.status
        result = {"service": name, "url": url, "status": status, "healthy": status < 400}
        logger.info(f"{name}: HTTP {status} — OK")
        return result
    except Exception as exc:
        logger.warning(f"{name}: FAILED — {exc}")
        return {"service": name, "url": url, "status": 0, "healthy": False, "error": str(exc)}


# ──────────────────────────────────────────────────────────
# Flow 2: Services Health Check — every 10 minutes
# ──────────────────────────────────────────────────────────

SERVICES = {
    "Elasticsearch": "http://elasticsearch:9200/_cluster/health",
    "Prometheus":    "http://prometheus:9090/-/healthy",
    "Grafana":       "http://grafana:3000/api/health",
    "Prefect":       "http://prefect-server:4200/api/health",
    "Kibana":        "http://kibana:5601/api/status",
}


@flow(name="services-health-check", log_prints=True)
def services_health_check():
    """Pings all stack services every 10 minutes. Sends email on failure."""
    results = [check_service(name, url) for name, url in SERVICES.items()]

    healthy = [r for r in results if r["healthy"]]
    failed  = [r for r in results if not r["healthy"]]

    print("=" * 48)
    print(f"  Services Health — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 48)
    for r in results:
        icon = "OK" if r["healthy"] else "FAIL"
        print(f"  [{icon}] {r['service']:<18} HTTP {r['status']}")
    print(f"\n  {len(healthy)}/{len(results)} services healthy")
    print("=" * 48)

    if failed:
        body = (
            f"Coderz Stack — Service Failure Alert\n"
            f"Time: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            f"FAILED services:\n"
        )
        for r in failed:
            err = r.get("error", f"HTTP {r['status']}")
            body += f"  - {r['service']}: {err}\n"
        body += f"\nHealthy: {len(healthy)}/{len(results)}\n"
        send_email(
            subject=f"[Coderz ALERT] {len(failed)} service(s) down",
            body=body,
        )
        raise Exception(f"Unhealthy services: {[r['service'] for r in failed]}")

    return {"healthy": len(healthy), "total": len(results), "results": results}


# ──────────────────────────────────────────────────────────
# Tasks: Daily report helpers
# ──────────────────────────────────────────────────────────

@task(name="collect-uptime")
def collect_uptime() -> str:
    with open("/proc/uptime") as f:
        seconds = float(f.read().split()[0])
    days    = int(seconds // 86400)
    hours   = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"


@task(name="collect-load-avg")
def collect_load_avg() -> dict:
    with open("/proc/loadavg") as f:
        parts = f.read().split()
    return {"1m": float(parts[0]), "5m": float(parts[1]), "15m": float(parts[2])}


# ──────────────────────────────────────────────────────────
# Flow 3: Daily Summary Report — every day at 06:00 UTC
# ──────────────────────────────────────────────────────────

@flow(name="daily-summary-report", log_prints=True)
def daily_summary_report():
    """Generates a full system summary report once per day and emails it."""
    uptime = collect_uptime()
    load   = collect_load_avg()
    cpu    = get_cpu_usage()
    mem    = get_memory_usage()
    disk   = get_disk_usage("/")

    report = {
        "date":     datetime.datetime.utcnow().strftime("%Y-%m-%d"),
        "uptime":   uptime,
        "load_avg": load,
        "cpu_pct":  cpu,
        "memory":   mem,
        "disk":     disk,
    }

    body = (
        f"{'=' * 56}\n"
        f"  CODERZ DAILY REPORT — {report['date']}\n"
        f"{'=' * 56}\n"
        f"  Server Uptime  : {uptime}\n"
        f"  Load Avg       : {load['1m']} / {load['5m']} / {load['15m']} (1m/5m/15m)\n"
        f"  CPU Usage      : {cpu}%\n"
        f"  Memory         : {mem['used_mb']} MB / {mem['total_mb']} MB ({mem['used_pct']}%)\n"
        f"  Disk           : {disk['used_gb']} GB / {disk['total_gb']} GB ({disk['used_pct']}%)\n"
        f"{'=' * 56}\n"
    )

    # Warn about thresholds in the report
    warnings = []
    if cpu > THRESHOLDS["cpu_pct"]:
        warnings.append(f"  WARNING: CPU at {cpu}% (threshold: {THRESHOLDS['cpu_pct']}%)")
    if mem["used_pct"] > THRESHOLDS["mem_pct"]:
        warnings.append(f"  WARNING: Memory at {mem['used_pct']}% (threshold: {THRESHOLDS['mem_pct']}%)")
    if disk["used_pct"] > THRESHOLDS["disk_pct"]:
        warnings.append(f"  WARNING: Disk at {disk['used_pct']}% (threshold: {THRESHOLDS['disk_pct']}%)")

    if warnings:
        body += "\nTHRESHOLD WARNINGS:\n" + "\n".join(warnings) + "\n"

    print(body)

    send_email(
        subject=f"[Coderz] Daily Report — {report['date']}",
        body=body,
    )

    return report


# ──────────────────────────────────────────────────────────
# Flow 4: Threshold Alert Check — every 15 minutes
# ──────────────────────────────────────────────────────────

@flow(name="threshold-alert-check", log_prints=True)
def threshold_alert_check():
    """
    Checks CPU, RAM, Disk against thresholds every 15 minutes.
    Sends an email alert if any threshold is exceeded.
    """
    cpu  = get_cpu_usage()
    mem  = get_memory_usage()
    disk = get_disk_usage("/")

    alerts = []

    if cpu > THRESHOLDS["cpu_pct"]:
        alerts.append(f"CPU usage: {cpu}% (threshold: {THRESHOLDS['cpu_pct']}%)")

    if mem["used_pct"] > THRESHOLDS["mem_pct"]:
        alerts.append(
            f"Memory usage: {mem['used_pct']}% — {mem['used_mb']} MB / {mem['total_mb']} MB "
            f"(threshold: {THRESHOLDS['mem_pct']}%)"
        )

    if disk["used_pct"] > THRESHOLDS["disk_pct"]:
        alerts.append(
            f"Disk usage: {disk['used_pct']}% — {disk['used_gb']} GB / {disk['total_gb']} GB "
            f"(threshold: {THRESHOLDS['disk_pct']}%)"
        )

    if alerts:
        now  = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        body = (
            f"Coderz Stack — Resource Threshold Alert\n"
            f"Time: {now} UTC\n\n"
            f"The following thresholds have been exceeded:\n\n"
        )
        for a in alerts:
            body += f"  * {a}\n"
        body += "\nPlease investigate at http://109.199.120.120:3000\n"

        send_email(
            subject=f"[Coderz ALERT] Resource threshold exceeded ({len(alerts)} issue(s))",
            body=body,
        )
        print(f"ALERT: {len(alerts)} threshold(s) exceeded — email sent")
    else:
        print(f"OK: All resources within thresholds (CPU:{cpu}% MEM:{mem['used_pct']}% DISK:{disk['used_pct']}%)")

    return {"alerts": alerts, "cpu": cpu, "mem": mem["used_pct"], "disk": disk["used_pct"]}


# ──────────────────────────────────────────────────────────
# Flow 5: Weekly Cleanup Report — every Sunday at 02:00 UTC
# ──────────────────────────────────────────────────────────

@flow(name="weekly-cleanup-report", log_prints=True)
def weekly_cleanup_report():
    """
    Weekly summary: checks resource trends and sends a cleanup reminder
    if disk usage is above 70%.
    """
    disk = get_disk_usage("/")
    mem  = get_memory_usage()
    cpu  = get_cpu_usage()
    uptime = collect_uptime()

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    body = (
        f"Coderz Stack — Weekly Status Report\n"
        f"Week ending: {now}\n\n"
        f"Server Uptime : {uptime}\n"
        f"CPU           : {cpu}%\n"
        f"Memory        : {mem['used_mb']} MB / {mem['total_mb']} MB ({mem['used_pct']}%)\n"
        f"Disk          : {disk['used_gb']} GB / {disk['total_gb']} GB ({disk['used_pct']}%)\n\n"
    )

    if disk["used_pct"] > 70:
        body += (
            f"ACTION RECOMMENDED: Disk is at {disk['used_pct']}%.\n"
            f"Consider running: docker system prune -f\n"
            f"And: journalctl --vacuum-time=7d\n"
        )

    print(body)
    send_email(subject=f"[Coderz] Weekly Report — {now}", body=body)
    return {"cpu": cpu, "mem": mem, "disk": disk}


# ──────────────────────────────────────────────────────────
# Task: Run kubectl command and return output
# ──────────────────────────────────────────────────────────

@task(name="kubectl-get-pods", retries=1)
def get_k8s_pods() -> list:
    """Get all pods across all namespaces via kubectl."""
    import subprocess
    logger = get_run_logger()
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-A", "--no-headers",
             "-o", "custom-columns=NS:.metadata.namespace,NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready"],
            capture_output=True, text=True, timeout=15,
            env={**__import__("os").environ, "KUBECONFIG": "/root/.kube/config"}
        )
        pods = []
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                pods.append({"namespace": parts[0], "name": parts[1], "status": parts[2], "ready": parts[3] if len(parts) > 3 else "unknown"})
        logger.info(f"Found {len(pods)} pods across all namespaces")
        return pods
    except Exception as exc:
        logger.warning(f"kubectl failed: {exc}")
        return []


@task(name="kubectl-get-nodes", retries=1)
def get_k8s_nodes() -> list:
    """Get k3s node status via kubectl."""
    import subprocess
    logger = get_run_logger()
    try:
        result = subprocess.run(
            ["kubectl", "get", "nodes", "--no-headers",
             "-o", "custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1].type,READY:.status.conditions[-1].status,VERSION:.status.nodeInfo.kubeletVersion"],
            capture_output=True, text=True, timeout=15,
            env={**__import__("os").environ, "KUBECONFIG": "/root/.kube/config"}
        )
        nodes = []
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                nodes.append({"name": parts[0], "condition": parts[1], "ready": parts[2], "version": parts[3] if len(parts) > 3 else ""})
        logger.info(f"Found {len(nodes)} node(s)")
        return nodes
    except Exception as exc:
        logger.warning(f"kubectl nodes failed: {exc}")
        return []


# ──────────────────────────────────────────────────────────
# Flow 6: Kubernetes (k3s) Health Check — every 15 minutes
# ──────────────────────────────────────────────────────────

@flow(name="k8s-health-check", log_prints=True)
def k8s_health_check():
    """
    Checks k3s cluster health every 15 minutes.
    Emails alert if any node is NotReady or pods are in Failed/Unknown state.
    """
    pods  = get_k8s_pods()
    nodes = get_k8s_nodes()

    failed_pods  = [p for p in pods  if p["status"] in ("Failed", "Unknown", "CrashLoopBackOff")]
    not_ready    = [n for n in nodes if n["ready"] != "True"]
    pending_pods = [p for p in pods  if p["status"] == "Pending"]

    print("=" * 56)
    print(f"  K3s Health — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 56)
    print(f"  Nodes      : {len(nodes)} total, {len(nodes) - len(not_ready)} ready")
    print(f"  Pods       : {len(pods)} total, {len(failed_pods)} failed, {len(pending_pods)} pending")
    for n in nodes:
        icon = "OK" if n["ready"] == "True" else "FAIL"
        print(f"  [{icon}] {n['name']} — {n['condition']} ({n['version']})")
    print("=" * 56)

    issues = []
    if not_ready:
        issues.append(f"Nodes NotReady: {[n['name'] for n in not_ready]}")
    if failed_pods:
        issues.append(f"Failed pods: {[(p['namespace'] + '/' + p['name']) for p in failed_pods]}")

    if issues:
        now  = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        body = (
            f"Coderz Stack — Kubernetes Alert\n"
            f"Time: {now} UTC\n\n"
            f"Issues detected:\n\n"
        )
        for issue in issues:
            body += f"  * {issue}\n"
        body += f"\nTotal pods: {len(pods)} | Pending: {len(pending_pods)}\n"
        body += f"\nCheck cluster: kubectl get pods -A\n"
        send_email(
            subject=f"[Coderz ALERT] k3s cluster issue — {len(issues)} problem(s)",
            body=body,
        )
        raise Exception(f"k3s issues: {issues}")

    return {"nodes": len(nodes), "pods": len(pods), "failed": len(failed_pods)}


# ──────────────────────────────────────────────────────────
# Flow 7: Docker Restart Monitor — every 10 minutes
# ──────────────────────────────────────────────────────────

@task(name="docker-container-stats", retries=1)
def get_docker_stats() -> list:
    """Check container restart counts via Docker socket."""
    import subprocess
    logger = get_run_logger()
    try:
        result = subprocess.run(
            ["docker", "ps", "--format",
             "{{.Names}}\t{{.Status}}\t{{.RunningFor}}"],
            capture_output=True, text=True, timeout=15
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name   = parts[0]
                status = parts[1]
                # Detect unhealthy or restarting containers
                restarting = "Restarting" in status
                unhealthy  = "unhealthy" in status.lower()
                restart_count = 0
                if "(" in status and ")" in status:
                    try:
                        restart_count = int(status.split("(")[1].split(")")[0])
                    except ValueError:
                        pass
                containers.append({
                    "name": name, "status": status,
                    "restarting": restarting, "unhealthy": unhealthy,
                    "restart_count": restart_count
                })
        logger.info(f"Found {len(containers)} running containers")
        return containers
    except Exception as exc:
        logger.warning(f"docker ps failed: {exc}")
        return []


@flow(name="docker-restart-monitor", log_prints=True)
def docker_restart_monitor():
    """
    Monitors Docker containers for excessive restarts or unhealthy status
    every 10 minutes. Sends email alert on issues.
    """
    containers = get_docker_stats()

    restarting = [c for c in containers if c["restarting"]]
    unhealthy  = [c for c in containers if c["unhealthy"]]
    high_restart = [c for c in containers if c["restart_count"] >= 3]

    print("=" * 56)
    print(f"  Docker Monitor — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 56)
    print(f"  Total containers : {len(containers)}")
    print(f"  Restarting       : {len(restarting)}")
    print(f"  Unhealthy        : {len(unhealthy)}")
    print(f"  High restart cnt : {len(high_restart)}")
    print("=" * 56)

    problems = restarting + [c for c in unhealthy if c not in restarting]
    if problems or high_restart:
        now  = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        body = (
            f"Coderz Stack — Docker Container Alert\n"
            f"Time: {now} UTC\n\n"
        )
        if restarting:
            body += f"RESTARTING containers:\n"
            for c in restarting:
                body += f"  * {c['name']}: {c['status']}\n"
            body += "\n"
        if unhealthy:
            body += f"UNHEALTHY containers:\n"
            for c in unhealthy:
                body += f"  * {c['name']}: {c['status']}\n"
            body += "\n"
        if high_restart:
            body += f"HIGH RESTART COUNT (>=3):\n"
            for c in high_restart:
                body += f"  * {c['name']}: {c['restart_count']} restarts\n"
            body += "\n"
        body += f"Check logs: docker compose -f /opt/coderz/docker-compose.yml logs <container>\n"
        send_email(
            subject=f"[Coderz ALERT] Docker container issue — {len(problems) + len(high_restart)} container(s)",
            body=body,
        )
        raise Exception(f"Container issues: {[c['name'] for c in problems]}")

    return {"total": len(containers), "healthy": len(containers) - len(problems)}


# ──────────────────────────────────────────────────────────
# Allow running flows directly for testing
# ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    system_health_check()
