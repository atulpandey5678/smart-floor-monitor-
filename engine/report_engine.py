"""Report Engine — computes daily and weekly aggregate reports."""

import csv
import io
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Dict, List

import anthropic

from db.repository import Repository

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────


@dataclass
class WorkerBreakdown:
    """Per-worker stats within a report."""
    badge_id: str
    name: str
    total_hours: float
    efficiency: float  # percentage


@dataclass
class TrendComparison:
    """Percentage change vs previous 7-day period."""
    active_hours_change_pct: float
    avg_utilization_change_pct: float
    avg_efficiency_change_pct: float


@dataclass
class DailyReport:
    """Aggregated report for a single calendar day."""
    report_date: str  # "YYYY-MM-DD"
    total_sessions: int
    total_active_hours: float
    workers: List[WorkerBreakdown] = field(default_factory=list)
    alerts_summary: Dict[str, int] = field(default_factory=dict)
    machine_utilization: Dict[str, float] = field(default_factory=dict)


@dataclass
class WeeklyReport:
    """Aggregated report for a 7-day period."""
    week_start: str  # "YYYY-MM-DD" (Monday)
    week_end: str  # "YYYY-MM-DD" (Sunday)
    total_sessions: int
    total_active_hours: float
    avg_machine_utilization: Dict[str, float] = field(default_factory=dict)
    avg_worker_efficiency: Dict[str, float] = field(default_factory=dict)
    alerts_summary: Dict[str, int] = field(default_factory=dict)
    trend: TrendComparison = field(default_factory=lambda: TrendComparison(0.0, 0.0, 0.0))
    daily_active_hours: List[float] = field(default_factory=list)


# ── ReportEngine ───────────────────────────────────────────────


class ReportEngine:
    """Computes daily and weekly aggregate reports from session/alert data."""

    def __init__(self, repository: Repository, shift_hours: float = 8.0):
        self.repository = repository
        self.shift_hours = shift_hours

    async def daily_report(self, report_date: date) -> DailyReport:
        """Compute the daily report for a given calendar day."""
        sessions = await self.repository.get_sessions_for_date(report_date)
        alerts = await self.repository.get_alerts_for_date(report_date)

        total_sessions = len(sessions)
        total_seconds = sum(s.get("active_duration_seconds", 0) or 0 for s in sessions)
        total_active_hours = round(total_seconds / 3600, 4)

        # Per-worker breakdown
        worker_data: Dict[str, Dict] = {}
        for s in sessions:
            bid = s.get("badge_id", "UNKNOWN")
            if bid not in worker_data:
                worker_data[bid] = {
                    "name": s.get("employee_name", bid),
                    "total_seconds": 0.0,
                }
            worker_data[bid]["total_seconds"] += s.get("active_duration_seconds", 0) or 0

        workers = []
        for bid, data in worker_data.items():
            total_hrs = round(data["total_seconds"] / 3600, 4)
            efficiency = self._compute_worker_efficiency(sessions, bid)
            workers.append(WorkerBreakdown(
                badge_id=bid,
                name=data["name"],
                total_hours=total_hrs,
                efficiency=efficiency,
            ))

        # Alert summary by type
        alerts_summary: Dict[str, int] = {}
        for a in alerts:
            atype = a.get("alert_type", "unknown")
            alerts_summary[atype] = alerts_summary.get(atype, 0) + 1

        # Machine utilization
        machine_ids = set(s.get("machine_id", "M-01") for s in sessions)
        machine_utilization: Dict[str, float] = {}
        for mid in machine_ids:
            machine_utilization[mid] = self._compute_machine_utilization(sessions, mid)

        return DailyReport(
            report_date=report_date.isoformat(),
            total_sessions=total_sessions,
            total_active_hours=total_active_hours,
            workers=workers,
            alerts_summary=alerts_summary,
            machine_utilization=machine_utilization,
        )

    async def weekly_report(self, week_start: date) -> WeeklyReport:
        """Compute weekly report aggregating 7 consecutive daily reports."""
        week_end = week_start + timedelta(days=6)

        # Compute current week daily reports
        daily_reports = []
        for i in range(7):
            day = week_start + timedelta(days=i)
            daily_reports.append(await self.daily_report(day))

        total_sessions = sum(dr.total_sessions for dr in daily_reports)
        total_active_hours = round(sum(dr.total_active_hours for dr in daily_reports), 4)
        daily_active_hours = [dr.total_active_hours for dr in daily_reports]

        # Aggregate machine utilization (average across days that have data)
        machine_util_sums: Dict[str, List[float]] = {}
        for dr in daily_reports:
            for mid, util in dr.machine_utilization.items():
                if mid not in machine_util_sums:
                    machine_util_sums[mid] = []
                machine_util_sums[mid].append(util)

        avg_machine_utilization = {
            mid: round(sum(vals) / len(vals), 2) if vals else 0.0
            for mid, vals in machine_util_sums.items()
        }

        # Aggregate worker efficiency (average across days that have data)
        worker_eff_sums: Dict[str, List[float]] = {}
        for dr in daily_reports:
            for w in dr.workers:
                if w.badge_id not in worker_eff_sums:
                    worker_eff_sums[w.badge_id] = []
                worker_eff_sums[w.badge_id].append(w.efficiency)

        avg_worker_efficiency = {
            bid: round(sum(vals) / len(vals), 2) if vals else 0.0
            for bid, vals in worker_eff_sums.items()
        }

        # Aggregate alerts summary
        alerts_summary: Dict[str, int] = {}
        for dr in daily_reports:
            for atype, count in dr.alerts_summary.items():
                alerts_summary[atype] = alerts_summary.get(atype, 0) + count

        # Compute trend comparison vs previous 7 days
        prev_start = week_start - timedelta(days=7)
        prev_reports = []
        for i in range(7):
            day = prev_start + timedelta(days=i)
            prev_reports.append(await self.daily_report(day))

        prev_total_hours = sum(dr.total_active_hours for dr in prev_reports)

        # Average utilization for trend
        curr_utils = list(avg_machine_utilization.values())
        curr_avg_util = sum(curr_utils) / len(curr_utils) if curr_utils else 0.0

        prev_machine_utils: Dict[str, List[float]] = {}
        for dr in prev_reports:
            for mid, util in dr.machine_utilization.items():
                if mid not in prev_machine_utils:
                    prev_machine_utils[mid] = []
                prev_machine_utils[mid].append(util)
        prev_avg_utils = [
            sum(vals) / len(vals) for vals in prev_machine_utils.values()
        ] if prev_machine_utils else []
        prev_avg_util = sum(prev_avg_utils) / len(prev_avg_utils) if prev_avg_utils else 0.0

        # Average efficiency for trend
        curr_effs = list(avg_worker_efficiency.values())
        curr_avg_eff = sum(curr_effs) / len(curr_effs) if curr_effs else 0.0

        prev_worker_effs: Dict[str, List[float]] = {}
        for dr in prev_reports:
            for w in dr.workers:
                if w.badge_id not in prev_worker_effs:
                    prev_worker_effs[w.badge_id] = []
                prev_worker_effs[w.badge_id].append(w.efficiency)
        prev_avg_effs = [
            sum(vals) / len(vals) for vals in prev_worker_effs.values()
        ] if prev_worker_effs else []
        prev_avg_eff = sum(prev_avg_effs) / len(prev_avg_effs) if prev_avg_effs else 0.0

        # Compute percentage changes (0.0 if previous is zero)
        def pct_change(current, previous):
            if previous == 0.0:
                return 0.0
            return round((current - previous) / previous * 100, 2)

        trend = TrendComparison(
            active_hours_change_pct=pct_change(total_active_hours, prev_total_hours),
            avg_utilization_change_pct=pct_change(curr_avg_util, prev_avg_util),
            avg_efficiency_change_pct=pct_change(curr_avg_eff, prev_avg_eff),
        )

        return WeeklyReport(
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            total_sessions=total_sessions,
            total_active_hours=total_active_hours,
            avg_machine_utilization=avg_machine_utilization,
            avg_worker_efficiency=avg_worker_efficiency,
            alerts_summary=alerts_summary,
            trend=trend,
            daily_active_hours=daily_active_hours,
        )

    def _compute_machine_utilization(self, sessions: List[dict], machine_id: str) -> float:
        """Machine_Utilization = sum(active_duration_seconds for machine) / (shift_hours * 3600) * 100."""
        shift_seconds = self.shift_hours * 3600
        if shift_seconds == 0:
            return 0.0
        total = sum(
            (s.get("active_duration_seconds", 0) or 0)
            for s in sessions
            if s.get("machine_id") == machine_id
        )
        return round(total / shift_seconds * 100, 2)

    def _compute_worker_efficiency(self, sessions: List[dict], badge_id: str) -> float:
        """Worker_Efficiency = sum(worker's active_duration_seconds) / (shift_hours * 3600) * 100."""
        shift_seconds = self.shift_hours * 3600
        if shift_seconds == 0:
            return 0.0
        total = sum(
            (s.get("active_duration_seconds", 0) or 0)
            for s in sessions
            if s.get("badge_id") == badge_id
        )
        return round(total / shift_seconds * 100, 2)

    def _format_csv(self, report) -> str:
        """Serialize a DailyReport or WeeklyReport to CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)

        if isinstance(report, DailyReport):
            # Summary section
            writer.writerow(["Daily Report", report.report_date])
            writer.writerow([])
            writer.writerow(["Summary"])
            writer.writerow(["Total Sessions", report.total_sessions])
            writer.writerow(["Total Active Hours", report.total_active_hours])
            writer.writerow([])

            # Machine Utilization
            writer.writerow(["Machine Utilization"])
            writer.writerow(["Machine ID", "Utilization %"])
            for mid, util in report.machine_utilization.items():
                writer.writerow([mid, util])
            writer.writerow([])

            # Worker Breakdown
            writer.writerow(["Worker Breakdown"])
            writer.writerow(["Badge ID", "Name", "Total Hours", "Efficiency %"])
            for w in report.workers:
                writer.writerow([w.badge_id, w.name, w.total_hours, w.efficiency])
            writer.writerow([])

            # Alerts Summary
            writer.writerow(["Alerts Summary"])
            writer.writerow(["Alert Type", "Count"])
            for atype, count in report.alerts_summary.items():
                writer.writerow([atype, count])

        elif isinstance(report, WeeklyReport):
            # Summary section
            writer.writerow(["Weekly Report", f"{report.week_start} to {report.week_end}"])
            writer.writerow([])
            writer.writerow(["Summary"])
            writer.writerow(["Total Sessions", report.total_sessions])
            writer.writerow(["Total Active Hours", report.total_active_hours])
            writer.writerow([])

            # Machine Utilization
            writer.writerow(["Avg Machine Utilization"])
            writer.writerow(["Machine ID", "Avg Utilization %"])
            for mid, util in report.avg_machine_utilization.items():
                writer.writerow([mid, util])
            writer.writerow([])

            # Worker Efficiency
            writer.writerow(["Avg Worker Efficiency"])
            writer.writerow(["Badge ID", "Avg Efficiency %"])
            for bid, eff in report.avg_worker_efficiency.items():
                writer.writerow([bid, eff])
            writer.writerow([])

            # Trend
            writer.writerow(["Trend vs Previous Week"])
            writer.writerow(["Metric", "Change %"])
            writer.writerow(["Active Hours", report.trend.active_hours_change_pct])
            writer.writerow(["Avg Utilization", report.trend.avg_utilization_change_pct])
            writer.writerow(["Avg Efficiency", report.trend.avg_efficiency_change_pct])
            writer.writerow([])

            # Alerts Summary
            writer.writerow(["Alerts Summary"])
            writer.writerow(["Alert Type", "Count"])
            for atype, count in report.alerts_summary.items():
                writer.writerow([atype, count])

        return output.getvalue()

    def generate_ai_summary(self, report: DailyReport) -> str:
        """Use Claude API to generate a plain-English summary of the DailyReport."""
        from config import CLAUDE_API_KEY
        if not CLAUDE_API_KEY or not CLAUDE_API_KEY.startswith("sk-ant"):
            return "<p><em>AI Summary disabled (no valid Claude API key).</em></p>"

        try:
            client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
            
            # Format the data for Claude
            data_str = f"Date: {report.report_date}\n"
            data_str += f"Total Sessions: {report.total_sessions}\n"
            data_str += f"Total Active Hours: {report.total_active_hours}\n"
            data_str += "Alerts:\n"
            if report.alerts_summary:
                for k, v in report.alerts_summary.items():
                    data_str += f"- {k}: {v}\n"
            else:
                data_str += "- None\n"
            
            data_str += "Machine Utilization:\n"
            for k, v in report.machine_utilization.items():
                data_str += f"- {k}: {v}%\n"

            prompt = (
                "You are an expert factory floor manager. Review the following daily production data "
                "from our computer vision tracking system. Write a short, professional, 2-paragraph "
                "summary explaining how the shift went. Highlight any significant downtime alerts or "
                "low machine utilization. Keep it very plain English and easy to understand for a supervisor.\n\n"
                f"Data:\n{data_str}"
            )

            message = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=300,
                temperature=0.7,
                system="You are an expert factory floor manager AI assistant.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            ai_text = message.content[0].text
            # Convert simple newlines to HTML paragraphs for the email
            paragraphs = ai_text.strip().split("\n\n")
            html_out = "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())
            return f'<div style="background:#F3F4F6;padding:12px;border-radius:8px;border-left:4px solid #6366F1;color:#1F2937;margin-bottom:16px;"><strong>✨ AI Shift Summary</strong>{html_out}</div>'

        except Exception as e:
            logger.error("Failed to generate AI summary: %s", e)
            return f"<p><em>AI Summary unavailable: {e}</em></p>"
