"""DMCA Dashboard — generates a self-contained HTML dashboard."""

import json
from collections import Counter
from datetime import datetime, timedelta

from src.models import DomainReport


def _parse_date(date_str: str):
    """Parse YYYY-MM-DD string to datetime, or None if invalid."""
    if date_str == "N/A" or len(date_str) < 10:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None


def _alert_level(days_since):
    """Return (level, color) based on days since last notice."""
    if days_since is None:
        return "gray", "#9ca3af"
    if days_since <= 7:
        return "red", "#ef4444"
    if days_since <= 30:
        return "orange", "#f97316"
    if days_since <= 90:
        return "yellow", "#eab308"
    return "green", "#22c55e"


def prepare_dashboard_data(reports: list[DomainReport], now=None) -> dict:
    """Transform DomainReport list into a JSON-serializable dashboard dict."""
    if now is None:
        now = datetime.utcnow()

    total_notices = 0
    total_requested = 0
    total_removed = 0
    active_7d = 0
    active_30d = 0
    active_90d = 0

    domain_rows = []
    all_notices_feed = []

    for r in reports:
        # Sort notices by date desc
        sorted_notices = sorted(
            r.notices,
            key=lambda n: n.date if n.date != "N/A" else "0000-00-00",
            reverse=True,
        )

        total_notices += len(r.notices)
        total_requested += r.total_requested
        total_removed += r.total_removed

        # Determine latest notice date and days since
        last_date = None
        last_date_str = "N/A"
        days_since = None
        if sorted_notices:
            last_date_str = sorted_notices[0].date
            last_date = _parse_date(last_date_str)
            if last_date:
                days_since = (now - last_date).days

        level, color = _alert_level(days_since)

        # Track active domains
        if days_since is not None:
            if days_since <= 7:
                active_7d += 1
            if days_since <= 30:
                active_30d += 1
            if days_since <= 90:
                active_90d += 1

        # Top reporter/owner
        reporters = Counter(n.reporter_name for n in r.notices)
        owners = Counter(n.owner_name for n in r.notices)
        top_reporter = reporters.most_common(1)[0][0] if reporters else ""
        top_owner = owners.most_common(1)[0][0] if owners else ""

        tr_url = f"https://transparencyreport.google.com/copyright/domains/{r.domain}?hl=fr"

        notice_dicts = [
            {
                "notice_id": n.notice_id,
                "date": n.date,
                "urls_claimed": n.urls_claimed,
                "urls_removed": n.urls_removed,
                "reporter_name": n.reporter_name,
                "owner_name": n.owner_name,
                "lumen_url": n.lumen_url,
            }
            for n in sorted_notices
        ]

        domain_rows.append({
            "domain": r.domain,
            "total_requested": r.total_requested,
            "total_removed": r.total_removed,
            "no_action_taken": r.no_action_taken,
            "duplicate": r.duplicate,
            "waiting": r.waiting,
            "num_notices": len(r.notices),
            "last_notice_date": last_date_str,
            "days_since_last": days_since,
            "alert_level": level,
            "alert_color": color,
            "top_reporter": top_reporter,
            "top_owner": top_owner,
            "transparency_url": tr_url,
            "error": r.error,
            "notices": notice_dicts,
        })

        # Feed items — reuse dicts, adding domain key
        for nd in notice_dicts:
            all_notices_feed.append({**nd, "domain": r.domain})

    # Sort feed by date desc, take last 20
    all_notices_feed.sort(
        key=lambda x: x["date"] if x["date"] != "N/A" else "0000-00-00",
        reverse=True,
    )
    recent_feed = all_notices_feed[:50]

    # Sort domain rows: red first, then orange, yellow, green, gray
    level_order = {"red": 0, "orange": 1, "yellow": 2, "green": 3, "gray": 4}
    domain_rows.sort(key=lambda x: level_order.get(x["alert_level"], 5))

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "summary": {
            "total_domains": len(reports),
            "total_notices": total_notices,
            "total_requested": total_requested,
            "total_removed": total_removed,
            "removal_rate": round(total_removed / total_requested * 100, 1) if total_requested > 0 else 0,
            "active_7d": active_7d,
            "active_30d": active_30d,
            "active_90d": active_90d,
        },
        "domains": domain_rows,
        "recent_feed": recent_feed,
    }


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

def _esc(val):
    return str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _esc_js(val):
    """Escape a value for safe embedding inside a JavaScript single-quoted string."""
    return str(val).replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _render_head(server_mode=False):
    return """<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DMCA Report Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{height:100%;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f1f5f9;color:#1e293b;line-height:1.5;}

/* Layout */
.app{display:flex;height:100vh;overflow:hidden;}

/* Sidebar */
.sidebar{width:240px;min-width:240px;background:#0f172a;display:flex;flex-direction:column;padding:0;overflow-y:auto;}
.sidebar-brand{padding:24px 20px 8px;color:#fff;font-size:1.05rem;font-weight:700;letter-spacing:0.01em;}
.sidebar-brand small{display:block;font-size:0.65rem;font-weight:600;color:#94a3b8;letter-spacing:0.12em;text-transform:uppercase;margin-top:2px;}
.sidebar-nav{list-style:none;margin-top:24px;padding:0 0 20px;}
.sidebar-nav li{position:relative;}
.sidebar-nav a{display:flex;align-items:center;gap:12px;padding:10px 20px;color:#94a3b8;text-decoration:none;font-size:0.9rem;font-weight:500;transition:color .15s,background .15s;border-left:3px solid transparent;}
.sidebar-nav a:hover{color:#cbd5e1;background:rgba(255,255,255,0.04);}
.sidebar-nav a.active{color:#fff;border-left-color:#3b82f6;background:rgba(255,255,255,0.06);}
.sidebar-nav a svg{width:18px;height:18px;flex-shrink:0;}

/* Main area */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden;}

/* Topbar */
.topbar{height:56px;min-height:56px;background:#fff;display:flex;align-items:center;justify-content:flex-end;padding:0 28px;gap:16px;border-bottom:1px solid #e2e8f0;}
.topbar .timestamp{color:#64748b;font-size:0.8rem;margin-right:auto;padding-left:4px;}

/* Content area */
.content{flex:1;overflow-y:auto;padding:32px 36px;}

/* Pages */
.page{display:none;}
.page.active{display:block;}

/* Page title */
.page-title{font-size:1.75rem;font-weight:700;color:#0f172a;margin-bottom:4px;}
.page-subtitle{color:#64748b;font-size:0.95rem;margin-bottom:28px;}

/* Stat cards */
.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-bottom:32px;}
.stat-card{background:#fff;border-radius:14px;padding:22px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.06);display:flex;flex-direction:column;gap:6px;position:relative;overflow:hidden;}
.stat-card-header{display:flex;align-items:center;justify-content:space-between;}
.stat-card-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;}
.stat-card-icon svg{width:20px;height:20px;color:#fff;}
.stat-card-icon.blue{background:#3b82f6;}
.stat-card-icon.violet{background:#8b5cf6;}
.stat-card-icon.amber{background:#f59e0b;}
.stat-card-icon.green{background:#22c55e;}
.stat-card .trend{font-size:0.75rem;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap;}
.stat-card .trend.up{background:#dcfce7;color:#16a34a;}
.stat-card .trend.down{background:#fef2f2;color:#dc2626;}
.stat-card .trend.steady{background:#f1f5f9;color:#64748b;}
.stat-card .label{font-size:0.7rem;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;}
.stat-card .value{font-size:1.85rem;font-weight:700;color:#0f172a;line-height:1.1;}

/* Two-column chart row */
.chart-row{display:grid;grid-template-columns:1fr 1.4fr;gap:24px;margin-bottom:32px;}

/* Chart cards */
.chart-card{background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,0.06);}
.chart-card-title{font-size:1rem;font-weight:700;color:#0f172a;margin-bottom:2px;}
.chart-card-subtitle{font-size:0.65rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:20px;}

/* Donut */
.donut-wrapper{display:flex;flex-direction:column;align-items:center;}
.donut-svg{width:200px;height:200px;}
.donut-center{font-size:2.2rem;font-weight:700;fill:#0f172a;}
.donut-center-label{font-size:0.65rem;font-weight:700;fill:#3b82f6;text-transform:uppercase;letter-spacing:0.1em;}
.donut-legend{display:flex;gap:24px;margin-top:18px;font-size:0.8rem;color:#475569;}
.donut-legend span{display:flex;align-items:center;gap:6px;}
.legend-dot{width:10px;height:10px;border-radius:50%;display:inline-block;}
.legend-dot.blue{background:#1d4ed8;}
.legend-dot.gray{background:#e2e8f0;}

/* Histogram */
.histogram-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:2px;}
.histogram-toggles{display:flex;gap:0;}
.histogram-toggles button{padding:5px 14px;font-size:0.75rem;font-weight:600;border:1px solid #e2e8f0;background:#fff;color:#64748b;cursor:pointer;transition:all .15s;}
.histogram-toggles button:first-child{border-radius:6px 0 0 6px;}
.histogram-toggles button:last-child{border-radius:0 6px 6px 0;}
.histogram-toggles button.active{background:#1d4ed8;color:#fff;border-color:#1d4ed8;}
.histogram-svg{width:100%;margin-top:12px;}
.histogram-bar{fill:#e2e8f0;rx:3;transition:fill .2s;}
.histogram-bar.active{fill:#1d4ed8;}
.histogram-label{fill:#94a3b8;font-size:11px;text-anchor:middle;font-weight:500;}
.histogram-tick{fill:#94a3b8;font-size:10px;text-anchor:end;}
.histogram-empty{text-align:center;color:#94a3b8;padding:40px 0;font-size:0.85rem;}

/* Domain page toolbar */
.domain-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px;flex-wrap:wrap;}
.domain-search{width:100%;max-width:320px;padding:10px 16px 10px 40px;border:1px solid #e2e8f0;border-radius:10px;font-size:0.9rem;color:#1e293b;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'/%3E%3C/svg%3E") 14px center no-repeat;outline:none;transition:border-color .15s;}
.domain-search:focus{border-color:#3b82f6;}
.domain-sort-btn{display:flex;align-items:center;gap:6px;padding:9px 16px;border:1px solid #e2e8f0;border-radius:10px;background:#fff;color:#64748b;font-size:0.85rem;font-weight:500;cursor:pointer;transition:border-color .15s;}
.domain-sort-btn:hover{border-color:#94a3b8;}
.domain-sort-btn svg{width:16px;height:16px;}

/* Domain card grid */
.domain-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:20px;}
.domain-card{background:#fff;border-radius:14px;padding:24px 28px;box-shadow:0 1px 3px rgba(0,0,0,0.06);cursor:pointer;transition:box-shadow .15s,transform .15s;display:flex;flex-direction:column;gap:16px;position:relative;}
.domain-card:hover{box-shadow:0 4px 16px rgba(0,0,0,0.1);transform:translateY(-1px);}

/* Card top row: icon + name + subtitle */
.dc-header{display:flex;align-items:center;gap:14px;}
.dc-icon{width:44px;height:44px;border-radius:12px;background:#eff6ff;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.dc-icon svg{width:22px;height:22px;color:#3b82f6;}
.dc-title{font-size:0.95rem;font-weight:700;color:#0f172a;word-break:break-all;}
.dc-subtitle{font-size:0.7rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.06em;margin-top:1px;}

/* Card stats row */
.dc-stats{display:flex;gap:24px;flex-wrap:wrap;}
.dc-stat-group{display:flex;flex-direction:column;gap:1px;}
.dc-stat-label{font-size:0.65rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;}
.dc-stat-value{font-size:0.9rem;font-weight:600;color:#0f172a;}
.dc-stat-value.muted{color:#64748b;font-weight:500;}

/* Manage link */
.dc-footer{display:flex;align-items:center;justify-content:flex-end;}
.dc-manage{font-size:0.85rem;font-weight:600;color:#64748b;text-decoration:none;display:flex;align-items:center;gap:4px;transition:color .15s;}
.dc-manage:hover{color:#3b82f6;}
.dc-manage svg{width:16px;height:16px;}

/* Counter + pagination */
.domain-counter{color:#94a3b8;font-size:0.85rem;margin-top:24px;text-align:center;}
.domain-empty{text-align:center;color:#94a3b8;padding:40px 0;font-size:0.9rem;}

@media(max-width:900px){.domain-grid{grid-template-columns:1fr;}}

/* Placeholder pages */
.placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:300px;color:#94a3b8;}
.placeholder svg{width:48px;height:48px;margin-bottom:12px;opacity:0.5;}
.placeholder p{font-size:1rem;font-weight:500;}

/* Domain detail page */
.detail-back{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border:1px solid #e2e8f0;border-radius:10px;background:#fff;color:#64748b;font-size:0.85rem;font-weight:600;cursor:pointer;transition:color .15s,border-color .15s;margin-bottom:20px;}
.detail-back:hover{color:#3b82f6;border-color:#3b82f6;}
.detail-back svg{width:16px;height:16px;}
.detail-header{display:flex;align-items:center;gap:14px;margin-bottom:28px;}
.detail-header h1{font-size:1.75rem;font-weight:700;color:#0f172a;}
.detail-header .alert-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0;}
.detail-header a{font-size:0.8rem;color:#3b82f6;text-decoration:none;margin-left:auto;font-weight:500;}
.detail-header a:hover{text-decoration:underline;}
.detail-chart-row{margin-bottom:32px;}
.detail-feed{background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,0.06);}
.detail-feed h2{font-size:1rem;font-weight:700;color:#0f172a;margin-bottom:4px;}
.detail-feed .feed-subtitle{font-size:0.65rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:16px;}
.detail-feed table{width:100%;border-collapse:collapse;font-size:0.85rem;}
.detail-feed th{text-align:left;font-size:0.65rem;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;padding:8px 12px;border-bottom:2px solid #e2e8f0;}
.detail-feed td{padding:10px 12px;border-bottom:1px solid #f1f5f9;color:#1e293b;}
.detail-feed tr:nth-child(even) td{background:#f8fafc;}
.detail-feed .lumen-link{color:#3b82f6;text-decoration:none;}
.detail-feed .lumen-link:hover{text-decoration:underline;}

/* Activity page */
.activity-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px;flex-wrap:wrap;}
.activity-search{width:100%;max-width:320px;padding:10px 16px 10px 40px;border:1px solid #e2e8f0;border-radius:10px;font-size:0.9rem;color:#1e293b;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'/%3E%3C/svg%3E") 14px center no-repeat;outline:none;transition:border-color .15s;}
.activity-search:focus{border-color:#3b82f6;}
.activity-date-group{font-size:0.7rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.1em;padding:20px 0 10px;display:flex;align-items:center;gap:16px;}
.activity-date-group::after{content:'';flex:1;height:1px;background:#e2e8f0;}
.activity-list{display:flex;flex-direction:column;gap:0;}
.activity-item{background:#fff;border-radius:12px;padding:20px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.04);display:flex;align-items:center;gap:24px;border-bottom:1px solid #f1f5f9;border-left:3px solid #e2e8f0;transition:box-shadow .15s;}
.activity-item:hover{box-shadow:0 2px 8px rgba(0,0,0,0.08);}
.activity-date-col{min-width:90px;text-align:right;flex-shrink:0;}
.activity-date-col .act-date{font-size:0.9rem;font-weight:700;color:#0f172a;}
.activity-info{flex:1;min-width:0;}
.activity-info-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.activity-domain{font-size:0.95rem;font-weight:700;color:#0f172a;}
.activity-meta{font-size:0.8rem;color:#64748b;margin-top:4px;}
.activity-meta span{font-size:0.6rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;margin-right:4px;}
.activity-right{display:flex;align-items:center;gap:16px;flex-shrink:0;}
.activity-detail-link{font-size:0.85rem;font-weight:600;color:#64748b;text-decoration:none;display:flex;align-items:center;gap:4px;transition:color .15s;white-space:nowrap;}
.activity-detail-link:hover{color:#3b82f6;}
.activity-load-more{display:flex;align-items:center;justify-content:center;gap:8px;margin:28px auto 0;padding:10px 28px;border:1px solid #e2e8f0;border-radius:10px;background:#fff;color:#64748b;font-size:0.9rem;font-weight:600;cursor:pointer;transition:border-color .15s,color .15s;}
.activity-load-more:hover{border-color:#3b82f6;color:#3b82f6;}
.activity-load-more svg{width:16px;height:16px;}
.activity-empty{text-align:center;color:#94a3b8;padding:40px 0;font-size:0.9rem;display:none;}

/* Overview recent feed */
.overview-feed{background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,0.06);margin-top:32px;}
.overview-feed-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;}
.overview-feed-header h2{font-size:1rem;font-weight:700;color:#0f172a;}
.overview-feed-link{font-size:0.85rem;font-weight:600;color:#3b82f6;text-decoration:none;display:flex;align-items:center;gap:4px;}
.overview-feed-link:hover{text-decoration:underline;}
.overview-feed-item{display:flex;align-items:center;gap:20px;padding:14px 0;border-bottom:1px solid #f1f5f9;}
.overview-feed-item:last-child{border-bottom:none;}
.overview-feed-date{min-width:80px;font-size:0.8rem;font-weight:600;color:#64748b;text-align:right;flex-shrink:0;}
.overview-feed-domain{font-size:0.9rem;font-weight:700;color:#0f172a;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.overview-feed-reporter{font-size:0.8rem;color:#64748b;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}

/* Histogram tooltip */
.hist-tooltip{position:fixed;pointer-events:none;background:#0f172a;color:#fff;font-size:0.78rem;padding:6px 12px;border-radius:8px;line-height:1.4;white-space:nowrap;z-index:9999;opacity:0;transition:opacity .15s;box-shadow:0 4px 12px rgba(0,0,0,0.15);}
.hist-tooltip.visible{opacity:1;}
.hist-tooltip .tt-date{font-weight:600;}
.hist-tooltip .tt-count{color:#93c5fd;}

/* Domain management bar (server mode) */
.domain-manage-bar{display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap;}
.domain-add-form{display:flex;gap:8px;align-items:center;}
.manage-input{padding:10px 16px;border:1px solid #e2e8f0;border-radius:10px;font-size:0.9rem;color:#1e293b;background:#fff;outline:none;transition:border-color .15s;width:260px;}
.manage-input:focus{border-color:#3b82f6;}
.manage-btn{padding:10px 20px;border:none;border-radius:10px;font-size:0.85rem;font-weight:600;cursor:pointer;transition:background .15s,color .15s;background:#3b82f6;color:#fff;}
.manage-btn:hover{background:#2563eb;}
.manage-btn:disabled{opacity:0.5;cursor:not-allowed;}
.manage-btn-outline{padding:10px 20px;border:2px solid #3b82f6;border-radius:10px;font-size:0.85rem;font-weight:600;cursor:pointer;background:#fff;color:#3b82f6;transition:background .15s,color .15s;}
.manage-btn-outline:hover{background:#eff6ff;}
.manage-btn-outline:disabled{opacity:0.5;cursor:not-allowed;}
.manage-status{font-size:0.8rem;color:#64748b;margin-left:8px;}
.manage-status .dots::after{content:'';animation:dots 1.5s steps(4,end) infinite;}
@keyframes dots{0%{content:'';}25%{content:'.';}50%{content:'..';}75%{content:'...';}}

/* Domain remove button (server mode) */
.domain-remove-btn{position:absolute;top:12px;right:12px;width:28px;height:28px;border-radius:8px;border:1px solid #e2e8f0;background:#fff;color:#94a3b8;cursor:pointer;display:flex;align-items:center;justify-content:center;opacity:0;transition:opacity .15s,color .15s,border-color .15s;z-index:2;}
.domain-card:hover .domain-remove-btn{opacity:1;}
.domain-remove-btn:hover{color:#ef4444;border-color:#fca5a5;background:#fef2f2;}

/* Responsive */
@media(max-width:1100px){.stat-grid{grid-template-columns:repeat(2,1fr);}.chart-row{grid-template-columns:1fr;}}
@media(max-width:768px){.sidebar{display:none;}.stat-grid{grid-template-columns:1fr;}.domain-manage-bar{flex-direction:column;align-items:stretch;}}
</style>
</head>"""


def _render_sidebar():
    return """<aside class="sidebar">
  <div class="sidebar-brand">Sentinel Editorial<small>Legal Compliance</small></div>
  <ul class="sidebar-nav">
    <li><a href="#" class="active" data-page="overview" onclick="navigateTo('overview',this);return false;">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
      Overview</a></li>
    <li><a href="#" data-page="domains" onclick="navigateTo('domains',this);return false;">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
      Domains</a></li>
    <li><a href="#" data-page="activity" onclick="navigateTo('activity',this);return false;">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
      Activity</a></li>
  </ul>
</aside>"""


def _render_topbar(data):
    ts = _esc(data["generated_at"])
    return f"""<header class="topbar">
  <span class="timestamp">Generated: {ts}</span>
</header>"""


def _render_overview_feed_items(data):
    feed = data.get("recent_feed", [])[:5]
    if not feed:
        return '    <div style="color:#94a3b8;text-align:center;padding:20px 0;font-size:0.9rem;">No recent activity.</div>\n'
    html = ""
    for item in feed:
        domain = _esc(item["domain"])
        date = _esc(item["date"])
        reporter = _esc(item.get("reporter_name", ""))
        html += (
            f'    <div class="overview-feed-item">\n'
            f'      <div class="overview-feed-date">{date}</div>\n'
            f'      <div class="overview-feed-domain">{domain}</div>\n'
            f'      <div class="overview-feed-reporter">{reporter}</div>\n'
            f'    </div>\n'
        )
    return html


def _render_page_overview(data):
    s = data["summary"]

    return f"""<div class="page active" id="page-overview">
  <h1 class="page-title">Compliance Jury</h1>
  <p class="page-subtitle">Comprehensive oversight of global digital property rights and DMCA resolution lifecycles.</p>

  <div class="stat-grid">
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon blue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg></div>
        <span class="trend steady" id="trend-domains">Steady</span>
      </div>
      <div class="label">Total Domains</div>
      <div class="value">{_fmt(s["total_domains"])}</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon violet"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></div>
        <span class="trend steady" id="trend-notices">Steady</span>
      </div>
      <div class="label">Total Notices</div>
      <div class="value">{_fmt(s["total_notices"])}</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon amber"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></div>
        <span class="trend steady" id="trend-requested">Steady</span>
      </div>
      <div class="label">URLs Requested</div>
      <div class="value">{_fmt(s["total_requested"])}</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></div>
        <span class="trend steady" id="trend-removed">Steady</span>
      </div>
      <div class="label">URLs Removed</div>
      <div class="value">{_fmt(s["total_removed"])}</div>
    </div>
  </div>

  <div class="chart-row">
    <div class="chart-card">
      <div class="chart-card-title">Removal Ratio</div>
      <div class="chart-card-subtitle">Enforcement Efficiency</div>
      <div class="donut-wrapper">
        <svg class="donut-svg" viewBox="0 0 200 200">
          <circle cx="100" cy="100" r="78" fill="none" stroke="#e2e8f0" stroke-width="16"/>
          <circle cx="100" cy="100" r="78" fill="none" stroke="#1d4ed8" stroke-width="16"
            stroke-dasharray="{s["removal_rate"] / 100 * 2 * 3.14159 * 78:.1f} {2 * 3.14159 * 78:.1f}"
            stroke-dashoffset="{2 * 3.14159 * 78 * 0.25:.1f}"
            stroke-linecap="round" transform="rotate(-90 100 100)"/>
          <text x="100" y="96" text-anchor="middle" class="donut-center">{s["removal_rate"]}%</text>
          <text x="100" y="116" text-anchor="middle" class="donut-center-label">CONFIRMED</text>
        </svg>
        <div class="donut-legend">
          <span><i class="legend-dot blue"></i> Removed</span>
          <span><i class="legend-dot gray"></i> Pending</span>
        </div>
      </div>
    </div>
    <div class="chart-card">
      <div class="histogram-header">
        <div>
          <div class="chart-card-title">Notice Trends</div>
          <div class="chart-card-subtitle">Historical Volume</div>
        </div>
        <div class="histogram-toggles">
          <button class="active" onclick="setHistogramWindow(7,this)">7D</button>
          <button onclick="setHistogramWindow(30,this)">30D</button>
          <button onclick="setHistogramWindow(90,this)">90D</button>
        </div>
      </div>
      <div id="histogram-container"></div>
    </div>
  </div>

  <div class="overview-feed">
    <div class="overview-feed-header">
      <h2>Recent Activity</h2>
      <a class="overview-feed-link" href="#" onclick="navigateTo('activity',document.querySelector('[data-page=activity]'));return false;">View All <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></a>
    </div>
{_render_overview_feed_items(data)}  </div>
</div>"""


def _render_page_domains(data, server_mode=False):
    domains = data["domains"]
    total = len(domains)

    def _days_ago_text(days):
        if days is None:
            return ""
        if days == 0:
            return "today"
        if days == 1:
            return "1 day ago"
        return f"{days} days ago"

    # Management bar (server mode only)
    manage_bar = ""
    if server_mode:
        manage_bar = """  <div class="domain-manage-bar">
    <div class="domain-add-form">
      <input type="text" class="manage-input" id="add-domain-input" placeholder="example.com" onkeydown="if(event.key==='Enter')addDomain()">
      <button class="manage-btn" onclick="addDomain()">Add Domain</button>
    </div>
    <button class="manage-btn-outline" id="generate-btn" onclick="generateReports()">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle;margin-right:6px;"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>Generate Reports
    </button>
    <span class="manage-status" id="generate-status"></span>
  </div>
"""

    cards = ""
    for d in domains:
        domain = _esc(d["domain"])
        date_str = _esc(d["last_notice_date"])
        days_ago = _days_ago_text(d["days_since_last"])
        ago_html = f' <span class="dc-stat-value muted">({days_ago})</span>' if days_ago else ""
        notices = d["num_notices"]
        urls = d["total_requested"]

        remove_btn = ""
        if server_mode:
            domain_js = _esc_js(domain)
            remove_btn = (
                f'  <button class="domain-remove-btn" title="Remove domain" '
                f"onclick=\"event.stopPropagation();removeDomain('{domain_js}')\">"
                f'<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
                f'</button>\n'
            )

        cards += (
            f'<div class="domain-card" data-domain="{domain}" data-notices="{notices}" '
            f'data-urls="{urls}" data-date="{_esc(d["last_notice_date"])}" '
            f'onclick="navigateTo(\'domain-detail-{domain}\',null)">\n'
            f'{remove_btn}'
            f'  <div class="dc-header">\n'
            f'    <div class="dc-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg></div>\n'
            f'    <div><div class="dc-title">{domain}</div>'
            f'<div class="dc-subtitle">DMCA monitored domain</div></div>\n'
            f'  </div>\n'
            f'  <div class="dc-stats">\n'
            f'    <div class="dc-stat-group"><div class="dc-stat-label">Last Reported</div><div class="dc-stat-value">{date_str}{ago_html}</div></div>\n'
            f'    <div class="dc-stat-group"><div class="dc-stat-label">Notices</div><div class="dc-stat-value">{notices:,}</div></div>\n'
            f'    <div class="dc-stat-group"><div class="dc-stat-label">URLs Requested</div><div class="dc-stat-value">{urls:,}</div></div>\n'
            f'  </div>\n'
            f'  <div class="dc-footer"><a class="dc-manage" href="#">Manage <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></a></div>\n'
            f'</div>\n'
        )

    return f"""<div class="page" id="page-domains">
  <h1 class="page-title">Domain Directory</h1>
  <p class="page-subtitle">Manage and monitor legal domain registry assets.</p>
{manage_bar}  <div class="domain-toolbar">
    <input type="text" class="domain-search" id="domain-search" placeholder="Filter domains\u2026" oninput="filterDomains()">
    <button class="domain-sort-btn" onclick="cycleDomainSort()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="16" y2="12"/><line x1="4" y1="18" x2="12" y2="18"/></svg>
      <span id="sort-label">Sort</span>
    </button>
  </div>
  <div class="domain-grid" id="domain-grid">
{cards}  </div>
  <div class="domain-empty" id="domain-empty" style="display:none;">No domains match your search.</div>
  <div class="domain-counter" id="domain-counter">Showing {total} of {total} total domain assets</div>
</div>"""


def _fmt(n):
    if n >= 10000:
        return f"{n/1000:.1f}k"
    return f"{n:,}"


def _render_page_domain_details(data):
    pages = ""
    for d in data["domains"]:
        domain = _esc(d["domain"])
        domain_raw = d["domain"]
        color = _esc(d["alert_color"])
        tr_url = _esc(d["transparency_url"])
        num_notices = d["num_notices"]
        total_requested = d["total_requested"]
        total_removed = d["total_removed"]
        ratio = round(total_removed / total_requested * 100, 1) if total_requested > 0 else 0

        # Notice feed rows (up to 20)
        notices = d["notices"][:20]
        feed_rows = ""
        for n in notices:
            lumen = _esc(n["lumen_url"]) if n["lumen_url"] and n["lumen_url"] != "N/A" else ""
            lumen_html = (
                f'<a class="lumen-link" href="{lumen}" target="_blank" rel="noopener">'
                f'<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>'
                if lumen else "—"
            )
            feed_rows += (
                f"<tr>"
                f"<td>{_esc(n['date'])}</td>"
                f"<td>{_esc(n['reporter_name'])}</td>"
                f"<td>{_esc(n['owner_name'])}</td>"
                f"<td>{n['urls_claimed']:,}</td>"
                f"<td>{n['urls_removed']:,}</td>"
                f"<td>{lumen_html}</td>"
                f"</tr>\n"
            )

        pages += f"""<div class="page" id="page-domain-detail-{domain}">
  <button class="detail-back" onclick="navigateTo('domains',document.querySelector('[data-page=domains]'));return false;">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
    Back to Domains
  </button>
  <div class="detail-header">
    <div class="alert-dot" style="background:{color};"></div>
    <h1>{domain}</h1>
    <a href="{tr_url}" target="_blank" rel="noopener">View on Google Transparency Report &rarr;</a>
  </div>

  <div class="stat-grid">
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon violet"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></div>
      </div>
      <div class="label">Total Notices Filed</div>
      <div class="value">{_fmt(num_notices)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon amber"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></div>
      </div>
      <div class="label">Total URLs Requested</div>
      <div class="value">{_fmt(total_requested)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon green"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></div>
      </div>
      <div class="label">Total URLs Removed</div>
      <div class="value">{_fmt(total_removed)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-card-header">
        <div class="stat-card-icon blue"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg></div>
      </div>
      <div class="label">Removal Ratio</div>
      <div class="value">{ratio}%</div>
    </div>
  </div>

  <div class="detail-chart-row">
    <div class="chart-card">
      <div class="chart-card-title">Notice Volume by Month</div>
      <div class="chart-card-subtitle">Historical distribution</div>
      <div id="detail-histogram-{domain}"></div>
    </div>
  </div>

  <div class="detail-feed">
    <h2>Recent Notice Activity</h2>
    <div class="feed-subtitle">Last {len(notices)} notices</div>
    <table>
      <thead><tr><th>Date</th><th>Reporter</th><th>Owner</th><th>URLs Claimed</th><th>URLs Removed</th><th>Lumen</th></tr></thead>
      <tbody>
{feed_rows}      </tbody>
    </table>
  </div>
</div>
"""
    return pages


def _render_page_activity(data):
    feed = data.get("recent_feed", [])

    items_html = ""
    for item in feed:
        domain = _esc(item["domain"])
        date = _esc(item["date"])
        reporter = _esc(item.get("reporter_name", ""))

        items_html += (
            f'<div class="activity-item" data-domain="{domain}" '
            f'data-reporter="{reporter}" '
            f'data-date="{date}" style="display:none;">\n'
            f'  <div class="activity-date-col"><div class="act-date">{date}</div></div>\n'
            f'  <div class="activity-info">\n'
            f'    <div class="activity-info-top">\n'
            f'      <span class="activity-domain">{domain}</span>\n'
            f'    </div>\n'
            f'    <div class="activity-meta"><span>Reporter</span> {reporter}</div>\n'
            f'  </div>\n'
            f'  <div class="activity-right">\n'
            f'    <a class="activity-detail-link" href="#" '
            f"onclick=\"navigateTo('domain-detail-{domain}',null);return false;\">"
            f'View Details <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></a>\n'
            f'  </div>\n'
            f'</div>\n'
        )

    return f"""<div class="page" id="page-activity">
  <h1 class="page-title">Recent Activity</h1>
  <p class="page-subtitle">Real-time chronological feed of DMCA enforcement across all registered domains.</p>

  <div class="activity-toolbar">
    <input type="text" class="activity-search" id="activity-search" placeholder="Search domains or reporters\u2026" oninput="filterActivity()">
  </div>

  <div class="activity-list" id="activity-list">
{items_html}  </div>
  <div class="activity-empty" id="activity-empty">No activity matches your filters.</div>
  <button class="activity-load-more" id="activity-load-more" onclick="loadMoreActivity()">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
    Load More Activity
  </button>
</div>"""


def _render_page_reports():
    return """<div class="page" id="page-reports">
  <h1 class="page-title">Reports</h1>
  <p class="page-subtitle">Exportable compliance reports and summaries.</p>
  <div class="placeholder">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
    <p>Coming soon</p>
  </div>
</div>"""


def _render_scripts(data_json, server_mode=False):
    return f"""<script>
const DASHBOARD_DATA = {data_json};
const SERVER_MODE = {'true' if server_mode else 'false'};

// --- Navigation ---
function navigateTo(pageId, el) {{
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + pageId).classList.add('active');
  document.querySelectorAll('.sidebar-nav a').forEach(a => a.classList.remove('active'));
  if (pageId.indexOf('domain-detail-') === 0) {{
    var domLink = document.querySelector('[data-page=domains]');
    if (domLink) domLink.classList.add('active');
    var domainName = pageId.replace('domain-detail-', '');
    setTimeout(function() {{ buildDomainHistogram(domainName); }}, 50);
  }} else if (el) {{
    el.classList.add('active');
  }} else {{
    var sideLink = document.querySelector('[data-page=' + pageId + ']');
    if (sideLink) sideLink.classList.add('active');
  }}
  if (SERVER_MODE) window.location.hash = '#' + pageId;
}}

// Restore page from URL hash on load
(function() {{
  var hash = window.location.hash.replace('#', '');
  if (hash && document.getElementById('page-' + hash)) {{
    navigateTo(hash, null);
  }}
}})();

// --- Histogram tooltip (event delegation) ---
var _tt = document.getElementById('hist-tooltip');
document.addEventListener('mouseover', function(evt) {{
  var bar = evt.target.closest('.histogram-bar');
  if (!bar || !bar.dataset.date) return;
  var count = parseInt(bar.dataset.count) || 0;
  _tt.innerHTML = '<span class="tt-date">' + bar.dataset.date + '</span><br><span class="tt-count">' + count + ' notice' + (count !== 1 ? 's' : '') + '</span>';
  _tt.classList.add('visible');
  var r = bar.getBoundingClientRect();
  _tt.style.left = (r.left + r.width / 2 - _tt.offsetWidth / 2) + 'px';
  _tt.style.top = (r.top - _tt.offsetHeight - 8) + 'px';
}});
document.addEventListener('mouseout', function(evt) {{
  if (evt.target.closest('.histogram-bar')) _tt.classList.remove('visible');
}});

// --- Trend badges ---
function computeTrends() {{
  const now = new Date();
  const d30 = new Date(now); d30.setDate(d30.getDate() - 30);
  const d60 = new Date(now); d60.setDate(d60.getDate() - 60);

  let last30 = 0, prior30 = 0;
  DASHBOARD_DATA.domains.forEach(function(dom) {{
    (dom.notices || []).forEach(function(n) {{
      if (!n.date || n.date === 'N/A') return;
      const nd = new Date(n.date);
      if (nd >= d30) last30++;
      else if (nd >= d60) prior30++;
    }});
  }});

  const el = document.getElementById('trend-notices');
  if (prior30 > 0) {{
    const pct = Math.round((last30 - prior30) / prior30 * 100);
    if (pct > 0) {{ el.textContent = '+' + pct + '%'; el.className = 'trend up'; }}
    else if (pct < 0) {{ el.textContent = pct + '%'; el.className = 'trend down'; }}
    else {{ el.textContent = 'Steady'; el.className = 'trend steady'; }}
  }}
}}
computeTrends();

// --- Histogram ---
function buildHistogram(days) {{
  const container = document.getElementById('histogram-container');
  const now = new Date();
  const cutoff = new Date(now); cutoff.setDate(cutoff.getDate() - days);

  // Aggregate daily counts
  const counts = {{}};
  DASHBOARD_DATA.domains.forEach(function(dom) {{
    (dom.notices || []).forEach(function(n) {{
      if (!n.date || n.date === 'N/A') return;
      const nd = new Date(n.date);
      if (nd >= cutoff && nd <= now) {{
        counts[n.date] = (counts[n.date] || 0) + 1;
      }}
    }});
  }});

  // Build sorted day list
  const dayList = [];
  const d = new Date(cutoff);
  while (d <= now) {{
    const key = d.toISOString().slice(0, 10);
    dayList.push({{ date: key, count: counts[key] || 0 }});
    d.setDate(d.getDate() + 1);
  }}

  if (dayList.length === 0 || dayList.every(function(x) {{ return x.count === 0; }})) {{
    container.innerHTML = '<div class="histogram-empty">No notices in this time window</div>';
    return;
  }}

  const maxCount = Math.max.apply(null, dayList.map(function(x) {{ return x.count; }}));
  const svgW = container.clientWidth || 500;
  const svgH = 200;
  const padL = 36, padR = 12, padT = 10, padB = 28;
  const chartW = svgW - padL - padR;
  const chartH = svgH - padT - padB;
  const barGap = Math.max(1, Math.floor(chartW / dayList.length * 0.15));
  const barW = Math.max(2, (chartW / dayList.length) - barGap);

  let bars = '';
  const dayNames = ['SUN','MON','TUE','WED','THU','FRI','SAT'];

  dayList.forEach(function(item, i) {{
    const x = padL + i * (barW + barGap);
    const h = maxCount > 0 ? (item.count / maxCount) * chartH : 0;
    const y = padT + chartH - h;
    const cls = item.count > 0 ? 'histogram-bar active' : 'histogram-bar';
    bars += '<rect class="' + cls + '" x="' + x + '" y="' + y + '" width="' + barW + '" height="' + Math.max(h, 2) + '" rx="3" data-date="' + item.date + '" data-count="' + item.count + '" style="cursor:pointer"/>';

    // Labels: show abbreviated day name for 7d, skip labels for 30d/90d unless start of week
    if (days <= 7) {{
      const dt = new Date(item.date);
      bars += '<text class="histogram-label" x="' + (x + barW / 2) + '" y="' + (svgH - 4) + '">' + dayNames[dt.getDay()] + '</text>';
    }} else if (days <= 30 && i % 7 === 0) {{
      bars += '<text class="histogram-label" x="' + (x + barW / 2) + '" y="' + (svgH - 4) + '">' + item.date.slice(5) + '</text>';
    }} else if (days > 30 && i % 14 === 0) {{
      bars += '<text class="histogram-label" x="' + (x + barW / 2) + '" y="' + (svgH - 4) + '">' + item.date.slice(5) + '</text>';
    }}
  }});

  // Y-axis ticks
  const ticks = [0, Math.round(maxCount / 2), maxCount];
  let ticksHtml = '';
  ticks.forEach(function(v) {{
    const y = padT + chartH - (maxCount > 0 ? (v / maxCount) * chartH : 0);
    ticksHtml += '<text class="histogram-tick" x="' + (padL - 6) + '" y="' + (y + 4) + '">' + v + '</text>';
    ticksHtml += '<line x1="' + padL + '" y1="' + y + '" x2="' + (svgW - padR) + '" y2="' + y + '" stroke="#e2e8f0" stroke-width="1"/>';
  }});

  container.innerHTML = '<svg class="histogram-svg" viewBox="0 0 ' + svgW + ' ' + svgH + '">' + ticksHtml + bars + '</svg>';
}}

function setHistogramWindow(days, el) {{
  document.querySelectorAll('.histogram-toggles button').forEach(function(b) {{ b.classList.remove('active'); }});
  if (el) el.classList.add('active');
  buildHistogram(days);
}}

// Initial render
buildHistogram(7);

// --- Domain detail histogram (monthly) ---
function buildDomainHistogram(domain) {{
  var container = document.getElementById('detail-histogram-' + domain);
  if (!container) return;

  // Find the domain data
  var domData = null;
  DASHBOARD_DATA.domains.forEach(function(d) {{
    if (d.domain === domain) domData = d;
  }});
  if (!domData) {{ container.innerHTML = '<div class="histogram-empty">No data available</div>'; return; }}

  // Group notices by month
  var counts = {{}};
  (domData.notices || []).forEach(function(n) {{
    if (!n.date || n.date === 'N/A') return;
    var key = n.date.slice(0, 7); // YYYY-MM
    counts[key] = (counts[key] || 0) + 1;
  }});

  // Build sorted month list
  var months = Object.keys(counts).sort();
  if (months.length === 0) {{
    container.innerHTML = '<div class="histogram-empty">No notices recorded</div>';
    return;
  }}

  // Fill gaps between first and last month
  var monthList = [];
  var start = months[0].split('-');
  var end = months[months.length - 1].split('-');
  var y = parseInt(start[0]), m = parseInt(start[1]);
  var ey = parseInt(end[0]), em = parseInt(end[1]);
  while (y < ey || (y === ey && m <= em)) {{
    var key = y + '-' + (m < 10 ? '0' + m : '' + m);
    monthList.push({{ month: key, count: counts[key] || 0 }});
    m++;
    if (m > 12) {{ m = 1; y++; }}
  }}

  var maxCount = Math.max.apply(null, monthList.map(function(x) {{ return x.count; }}));
  var svgW = container.clientWidth || 500;
  var svgH = 200;
  var padL = 36, padR = 12, padT = 10, padB = 28;
  var chartW = svgW - padL - padR;
  var chartH = svgH - padT - padB;
  var barGap = Math.max(1, Math.floor(chartW / monthList.length * 0.15));
  var barW = Math.max(4, (chartW / monthList.length) - barGap);

  var bars = '';
  var monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  monthList.forEach(function(item, i) {{
    var x = padL + i * (barW + barGap);
    var h = maxCount > 0 ? (item.count / maxCount) * chartH : 0;
    var yPos = padT + chartH - h;
    var cls = item.count > 0 ? 'histogram-bar active' : 'histogram-bar';
    var mIdxTT = parseInt(item.month.slice(5, 7)) - 1;
    var ttLabel = monthNames[mIdxTT] + ' ' + item.month.slice(0, 4);
    bars += '<rect class="' + cls + '" x="' + x + '" y="' + yPos + '" width="' + barW + '" height="' + Math.max(h, 2) + '" rx="3" data-date="' + ttLabel + '" data-count="' + item.count + '" style="cursor:pointer"/>';

    // Show label every Nth month depending on total count
    var step = monthList.length > 24 ? 6 : (monthList.length > 12 ? 3 : 1);
    if (i % step === 0) {{
      var mIdx = parseInt(item.month.slice(5, 7)) - 1;
      var label = monthNames[mIdx];
      if (step >= 3) label += ' ' + item.month.slice(2, 4);
      bars += '<text class="histogram-label" x="' + (x + barW / 2) + '" y="' + (svgH - 4) + '">' + label + '</text>';
    }}
  }});

  // Y-axis ticks
  var ticks = [0, Math.round(maxCount / 2), maxCount];
  var ticksHtml = '';
  ticks.forEach(function(v) {{
    var yT = padT + chartH - (maxCount > 0 ? (v / maxCount) * chartH : 0);
    ticksHtml += '<text class="histogram-tick" x="' + (padL - 6) + '" y="' + (yT + 4) + '">' + v + '</text>';
    ticksHtml += '<line x1="' + padL + '" y1="' + yT + '" x2="' + (svgW - padR) + '" y2="' + yT + '" stroke="#e2e8f0" stroke-width="1"/>';
  }});

  container.innerHTML = '<svg class="histogram-svg" viewBox="0 0 ' + svgW + ' ' + svgH + '">' + ticksHtml + bars + '</svg>';
}}

// --- Domain search filter ---
function filterDomains() {{
  const q = document.getElementById('domain-search').value.toLowerCase();
  const cards = document.querySelectorAll('#domain-grid .domain-card');
  let visible = 0;
  const total = cards.length;
  cards.forEach(function(card) {{
    const name = card.getAttribute('data-domain').toLowerCase();
    const show = name.indexOf(q) !== -1;
    card.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  document.getElementById('domain-empty').style.display = visible === 0 ? '' : 'none';
  document.getElementById('domain-counter').textContent = 'Showing ' + visible + ' of ' + total + ' total domain assets';
}}

// --- Domain sort ---
const sortModes = ['default', 'name-asc', 'name-desc', 'notices-desc', 'urls-desc', 'date-desc'];
const sortLabels = ['Sort', 'Name A\u2192Z', 'Name Z\u2192A', 'Most Notices', 'Most URLs', 'Latest Activity'];
let sortIndex = 0;
function cycleDomainSort() {{
  sortIndex = (sortIndex + 1) % sortModes.length;
  document.getElementById('sort-label').textContent = sortLabels[sortIndex];
  const grid = document.getElementById('domain-grid');
  const cards = Array.from(grid.querySelectorAll('.domain-card'));
  const mode = sortModes[sortIndex];
  cards.sort(function(a, b) {{
    if (mode === 'name-asc') return a.getAttribute('data-domain').localeCompare(b.getAttribute('data-domain'));
    if (mode === 'name-desc') return b.getAttribute('data-domain').localeCompare(a.getAttribute('data-domain'));
    if (mode === 'notices-desc') return (parseInt(b.getAttribute('data-notices')) || 0) - (parseInt(a.getAttribute('data-notices')) || 0);
    if (mode === 'urls-desc') return (parseInt(b.getAttribute('data-urls')) || 0) - (parseInt(a.getAttribute('data-urls')) || 0);
    if (mode === 'date-desc') {{
      let da = a.getAttribute('data-date') || '0000-00-00', db = b.getAttribute('data-date') || '0000-00-00';
      if (da === 'N/A') da = '0000-00-00'; if (db === 'N/A') db = '0000-00-00';
      return db.localeCompare(da);
    }}
    return 0;
  }});
  cards.forEach(function(c) {{ grid.appendChild(c); }});
}}

// --- Activity feed ---
var activityShown = 0;
var activityBatch = 20;

function buildActivityGroups() {{
  var list = document.getElementById('activity-list');
  list.querySelectorAll('.activity-date-group').forEach(function(h) {{ h.remove(); }});

  var items = Array.from(list.querySelectorAll('.activity-item'));
  var visibleItems = items.filter(function(el) {{ return el.style.display !== 'none'; }});
  var lastDate = null;

  var today = new Date();
  var todayStr = today.toISOString().slice(0, 10);
  var yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  var yesterdayStr = yesterday.toISOString().slice(0, 10);
  var monthNames = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];

  visibleItems.forEach(function(el) {{
    var d = el.getAttribute('data-date');
    if (d && d !== lastDate) {{
      lastDate = d;
      var label;
      if (d === todayStr) {{
        var parts = d.split('-');
        label = 'TODAY \u2014 ' + monthNames[parseInt(parts[1]) - 1] + ' ' + parseInt(parts[2]);
      }} else if (d === yesterdayStr) {{
        var parts = d.split('-');
        label = 'YESTERDAY \u2014 ' + monthNames[parseInt(parts[1]) - 1] + ' ' + parseInt(parts[2]);
      }} else {{
        var parts = d.split('-');
        label = monthNames[parseInt(parts[1]) - 1] + ' ' + parseInt(parts[2]) + ', ' + parts[0];
      }}
      var header = document.createElement('div');
      header.className = 'activity-date-group';
      header.textContent = label;
      el.parentNode.insertBefore(header, el);
    }}
  }});
}}

function filterActivity() {{
  var q = (document.getElementById('activity-search').value || '').toLowerCase();
  var items = document.querySelectorAll('#activity-list .activity-item');
  var visibleCount = 0;
  var shownCount = 0;

  items.forEach(function(el) {{
    var domain = (el.getAttribute('data-domain') || '').toLowerCase();
    var reporter = (el.getAttribute('data-reporter') || '').toLowerCase();
    var matchSearch = !q || domain.indexOf(q) !== -1 || reporter.indexOf(q) !== -1;

    if (matchSearch) {{
      visibleCount++;
      if (shownCount < activityShown) {{
        el.style.display = '';
        shownCount++;
      }} else {{
        el.style.display = 'none';
      }}
    }} else {{
      el.style.display = 'none';
    }}
  }});

  document.getElementById('activity-empty').style.display = visibleCount === 0 ? '' : 'none';
  var btn = document.getElementById('activity-load-more');
  btn.style.display = shownCount < visibleCount ? '' : 'none';

  buildActivityGroups();
}}

function loadMoreActivity() {{
  activityShown += 15;
  filterActivity();
}}

function initActivity() {{
  activityShown = activityBatch;
  filterActivity();
}}

initActivity();

// --- Server mode: domain management ---
if (SERVER_MODE) {{
  window.addDomain = async function() {{
    var input = document.getElementById('add-domain-input');
    var domain = (input.value || '').trim().toLowerCase();
    if (!domain) return;
    try {{
      var resp = await fetch('/api/domains', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{domain: domain}})
      }});
      var data = await resp.json();
      if (resp.ok) {{
        window.location.hash = '#domains';
        window.location.reload();
      }} else {{
        alert(data.error || 'Failed to add domain');
      }}
    }} catch (e) {{
      alert('Network error: ' + e.message);
    }}
  }};

  window.removeDomain = async function(domain) {{
    if (!confirm('Remove "' + domain + '" from monitoring? This also deletes cached data.')) return;
    try {{
      var resp = await fetch('/api/domains/' + encodeURIComponent(domain), {{method: 'DELETE'}});
      var data = await resp.json();
      if (resp.ok) {{
        window.location.hash = '#domains';
        window.location.reload();
      }} else {{
        alert(data.error || 'Failed to remove domain');
      }}
    }} catch (e) {{
      alert('Network error: ' + e.message);
    }}
  }};

  window.generateReports = async function() {{
    var btn = document.getElementById('generate-btn');
    var status = document.getElementById('generate-status');
    btn.disabled = true;
    status.innerHTML = 'Starting<span class="dots"></span>';
    try {{
      var resp = await fetch('/api/generate', {{method: 'POST'}});
      var data = await resp.json();
      if (!resp.ok) {{
        status.textContent = data.error || 'Failed';
        btn.disabled = false;
        return;
      }}
      // Poll for progress
      var poll = setInterval(async function() {{
        try {{
          var sr = await fetch('/api/status');
          var st = await sr.json();
          if (st.error) {{
            clearInterval(poll);
            status.textContent = 'Error: ' + st.error;
            btn.disabled = false;
          }} else if (st.done) {{
            clearInterval(poll);
            status.textContent = 'Done! Reloading...';
            setTimeout(function() {{ window.location.reload(); }}, 500);
          }} else if (st.total > 0) {{
            status.innerHTML = 'Fetching ' + st.current + '/' + st.total + ': ' + (st.domain || '') + '<span class="dots"></span>';
          }}
        }} catch (e) {{}}
      }}, 2000);
    }} catch (e) {{
      status.textContent = 'Network error';
      btn.disabled = false;
    }}
  }};
}}
</script>"""


def render_html(data: dict, server_mode: bool = False) -> str:
    """Render dashboard data as a self-contained HTML file."""
    data_json = json.dumps(data, default=str)

    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n"
        + _render_head(server_mode=server_mode)
        + "\n<body>\n<div class=\"app\">\n"
        + _render_sidebar()
        + "\n<div class=\"main\">\n"
        + _render_topbar(data)
        + "\n<div class=\"content\">\n"
        + _render_page_overview(data)
        + "\n"
        + _render_page_domains(data, server_mode=server_mode)
        + "\n"
        + _render_page_domain_details(data)
        + "\n"
        + _render_page_activity(data)
        + "\n"
        + _render_page_reports()
        + "\n</div>\n</div>\n</div>\n"
        + '<div class="hist-tooltip" id="hist-tooltip"></div>\n'
        + _render_scripts(data_json, server_mode=server_mode)
        + "\n</body>\n</html>"
    )


def generate_dashboard(reports: list[DomainReport], server_mode: bool = False) -> str:
    """Convenience wrapper: prepare data and render HTML."""
    data = prepare_dashboard_data(reports)
    return render_html(data, server_mode=server_mode)
