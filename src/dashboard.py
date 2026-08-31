"""Server-rendered dashboard for live Lumen DMCA results."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit

ROLE_LABELS = {
    "targeted": "NSN page targeted",
    "source": "NSN is the source",
    "unresolved": "Pending retrieval",
    "other": "Role needs review",
}

STATUS_LABELS = {
    "complete": "Complete",
    "pending": "Pending",
    "preflight": "Preparing",
    "email_timeout": "Email delayed",
    "token_consumption_error": "Access link lost",
    "submission_started": "Submission started",
    "submission_unknown": "Submission status unknown",
    "submission_rejected": "Submission rejected",
    "preflight_error": "Preflight error",
}


def _e(value) -> str:
    return escape(str(value or ""), quote=True)


def _short_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        text = (parsed.hostname or "") + (parsed.path or "/")
        return text if len(text) <= 72 else text[:69] + "..."
    except ValueError:
        return value[:72]


def _fmt_timestamp(value: str) -> str:
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    except ValueError:
        return value


def _url_list(items: list[dict], empty: str) -> str:
    if not items:
        return f'<p class="empty-detail">{_e(empty)}</p>'
    rows = []
    for item in items:
        monitored = '<span class="url-mark">NSN</span>' if item.get("monitored") else ""
        url = item.get("url", "")
        rows.append(
            '<li>'
            f'{monitored}<a href="{_e(url)}" target="_blank" rel="noreferrer">{_e(_short_url(url))}</a>'
            '</li>'
        )
    return '<ul class="url-list">' + "".join(rows) + "</ul>"


def _notice_row(notice: dict) -> str:
    role = notice["role"]
    status = notice["status"]
    monitored = notice.get("monitored_urls") or []
    primary = monitored[0] if monitored else ""
    more = len(monitored) - 1
    primary_html = (
        f'<a href="{_e(primary)}" target="_blank" rel="noreferrer">{_e(_short_url(primary))}</a>'
        + (f'<span class="more-count">+{more}</span>' if more > 0 else "")
        if primary else '<span class="muted">Not identified</span>'
    )
    search_blob = " ".join([
        str(notice["notice_id"]), notice["domain"], notice.get("sender", ""),
        " ".join(monitored), ROLE_LABELS.get(role, role), STATUS_LABELS.get(status, status),
    ]).lower()
    sender = notice.get("sender") or "Not published"
    return f"""
    <details class="notice" data-role="{_e(role)}" data-status="{_e(status)}" data-search="{_e(search_blob)}">
      <summary>
        <span class="notice-date">{_e(notice.get('date') or 'Date unknown')}</span>
        <span class="notice-id">#{notice['notice_id']}</span>
        <span class="notice-domain">{_e(notice['domain'])}</span>
        <span class="role role-{_e(role)}">{_e(ROLE_LABELS.get(role, role))}</span>
        <span class="notice-page">{primary_html}</span>
        <span class="status">{_e(STATUS_LABELS.get(status, status))}</span>
        <span class="chevron" aria-hidden="true"></span>
      </summary>
      <div class="notice-detail">
        <div class="detail-meta">
          <div><span>Sender</span><strong>{_e(sender)}</strong></div>
          <div><span>Technical status</span><strong>{_e(status)}</strong></div>
          <div><span>Attempts</span><strong>{notice.get('attempts', 0)}</strong></div>
        </div>
        <div class="url-columns">
          <section>
            <h3>Original URLs</h3>
            {_url_list(notice.get('original_urls', []), 'No original URL published.')}
          </section>
          <section>
            <h3>Reported infringing URLs</h3>
            {_url_list(notice.get('infringing_urls', []), 'No URL retrieved.')}
          </section>
        </div>
      </div>
    </details>"""


def _priority_item(notice: dict) -> str:
    role = notice["role"]
    if role == "targeted":
        action = "Review the page and decide whether to fix or dispute the claim."
    else:
        action = "Retrieve the notice details without resubmitting an accepted request."
    urls = notice.get("monitored_urls") or []
    page = urls[0] if urls else ""
    page_html = (
        f'<a href="{_e(page)}" target="_blank" rel="noreferrer">{_e(_short_url(page))}</a>'
        if page else '<span class="muted">URL unavailable</span>'
    )
    return f"""
      <article class="priority-item">
        <div class="priority-date">{_e(notice.get('date') or 'Date unknown')}</div>
        <div>
          <div class="priority-title">#{notice['notice_id']} · {_e(notice['domain'])}</div>
          <div class="priority-page">{page_html}</div>
          <p>{_e(action)}</p>
        </div>
        <span class="role role-{_e(role)}">{_e(ROLE_LABELS.get(role, role))}</span>
      </article>"""


def render_dashboard(data: dict) -> str:
    summary = data["summary"]
    metadata = data.get("metadata", {})
    notices = data.get("notices", [])
    priorities = [item for item in notices if item["role"] in {"targeted", "unresolved", "other"}]
    priority_html = "".join(_priority_item(item) for item in priorities[:8])
    if not priority_html:
        priority_html = '<p class="empty-state">No notice currently requires action.</p>'
    notices_html = "".join(_notice_row(item) for item in notices)
    if not notices_html:
        notices_html = '<p class="empty-state">The database does not contain any notices yet.</p>'

    synced_at = _fmt_timestamp(metadata.get("synced_at", ""))
    generated = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DMCA Monitor · NSN</title>
<style>
:root{{--ink:#18212b;--muted:#69737d;--paper:#f3f1ec;--surface:#fff;--line:#d9d5cc;--dark:#202a33;--target:#a33a2d;--target-bg:#f7e8e5;--source:#296347;--source-bg:#e5f0e9;--pending:#775b22;--pending-bg:#f4ecd8;--blue:#285a7d;}}
*{{box-sizing:border-box}}
html{{background:var(--paper)}}
body{{margin:0;color:var(--ink);background:var(--paper);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.45}}
a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}
.topbar{{background:var(--dark);color:#fff;padding:18px clamp(20px,4vw,58px);display:flex;align-items:center;justify-content:space-between;gap:20px}}
.brand{{font-size:17px;font-weight:720;letter-spacing:.01em}}.brand span{{font-weight:450;color:#b9c1c8;margin-left:8px}}
.sync{{font-size:12px;color:#c8ced3;text-align:right}}
main{{width:100%;padding:34px clamp(18px,4vw,58px) 64px}}
.intro{{display:flex;align-items:flex-end;justify-content:space-between;gap:28px;margin-bottom:26px}}
h1{{font-size:clamp(30px,4vw,48px);line-height:1.06;letter-spacing:-.035em;margin:0 0 9px}}.intro p{{margin:0;color:var(--muted);max-width:720px;font-size:15px}}
.coverage{{font-size:13px;color:var(--muted);white-space:nowrap;padding-bottom:5px}}.coverage strong{{color:var(--ink)}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--line);background:var(--surface);margin-bottom:30px}}
.metric{{padding:22px 24px;border-right:1px solid var(--line)}}.metric:last-child{{border-right:0}}.metric-label{{display:block;font-size:12px;color:var(--muted);margin-bottom:7px}}.metric-value{{font-size:31px;line-height:1;font-weight:740;letter-spacing:-.03em}}.metric-note{{display:block;font-size:11px;color:var(--muted);margin-top:8px}}
.section-head{{display:flex;align-items:baseline;justify-content:space-between;gap:18px;margin:32px 0 12px}}h2{{font-size:19px;margin:0;letter-spacing:-.01em}}.section-head p{{margin:0;color:var(--muted);font-size:12px}}
.priority-list{{background:var(--surface);border:1px solid var(--line)}}.priority-item{{display:grid;grid-template-columns:105px minmax(0,1fr) auto;gap:20px;padding:17px 20px;border-bottom:1px solid var(--line);align-items:start}}.priority-item:last-child{{border-bottom:0}}.priority-date{{font-size:12px;color:var(--muted);padding-top:3px}}.priority-title{{font-weight:700;font-size:14px}}.priority-page{{font-size:13px;margin-top:3px;word-break:break-word}}.priority-item p{{font-size:12px;color:var(--muted);margin:7px 0 0}}
.filters{{display:grid;grid-template-columns:minmax(240px,1fr) 190px 190px;gap:10px;margin-bottom:12px}}.filters input,.filters select{{width:100%;height:42px;border:1px solid var(--line);background:var(--surface);color:var(--ink);padding:0 13px;font:inherit;font-size:13px;border-radius:0}}.filters input:focus,.filters select:focus{{outline:2px solid #9db7c9;outline-offset:1px}}
.list-head{{display:grid;grid-template-columns:105px 90px 150px 150px minmax(230px,1fr) 115px 20px;gap:12px;padding:9px 16px;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.045em}}
.notice-list{{border:1px solid var(--line);background:var(--surface)}}details.notice{{border-bottom:1px solid var(--line)}}details.notice:last-child{{border-bottom:0}}details.notice[hidden]{{display:none}}summary{{display:grid;grid-template-columns:105px 90px 150px 150px minmax(230px,1fr) 115px 20px;gap:12px;align-items:center;padding:14px 16px;cursor:pointer;list-style:none;font-size:13px}}summary::-webkit-details-marker{{display:none}}summary:hover{{background:#faf9f6}}.notice-date,.notice-id,.status{{color:var(--muted)}}.notice-id{{font-variant-numeric:tabular-nums}}.notice-domain{{font-weight:670;overflow:hidden;text-overflow:ellipsis}}.notice-page{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.more-count{{font-size:11px;color:var(--muted);margin-left:7px}}.chevron{{width:8px;height:8px;border-right:1.5px solid var(--muted);border-bottom:1.5px solid var(--muted);transform:rotate(45deg);transition:transform .15s}}details[open] .chevron{{transform:rotate(225deg)}}
.role{{display:inline-flex;align-items:center;width:max-content;max-width:100%;padding:4px 8px;border:1px solid transparent;font-size:11px;font-weight:700;line-height:1.2}}.role-targeted{{color:var(--target);background:var(--target-bg);border-color:#e7c4bd}}.role-source{{color:var(--source);background:var(--source-bg);border-color:#bdd7c8}}.role-unresolved,.role-other{{color:var(--pending);background:var(--pending-bg);border-color:#dfd0a9}}
.notice-detail{{padding:20px 22px 24px;background:#f8f7f3;border-top:1px solid var(--line)}}.detail-meta{{display:grid;grid-template-columns:2fr 1fr 100px;gap:18px;padding-bottom:18px;border-bottom:1px solid var(--line)}}.detail-meta span{{display:block;font-size:11px;color:var(--muted);margin-bottom:4px}}.detail-meta strong{{font-size:13px;font-weight:650}}.url-columns{{display:grid;grid-template-columns:1fr 1fr;gap:32px;padding-top:18px}}.url-columns h3{{font-size:13px;margin:0 0 10px}}.url-list{{list-style:none;margin:0;padding:0}}.url-list li{{display:flex;align-items:flex-start;gap:8px;font-size:12px;padding:6px 0;border-bottom:1px solid #e7e3da;word-break:break-word}}.url-list li:last-child{{border-bottom:0}}.url-mark{{font-size:9px;font-weight:750;color:var(--target);border:1px solid #d8aaa0;padding:2px 4px;flex:0 0 auto}}.empty-detail,.empty-state{{color:var(--muted);font-size:13px;margin:0;padding:18px}}.muted{{color:var(--muted)}}
.result-count{{font-size:12px;color:var(--muted);margin-top:10px;text-align:right}}
@media(max-width:980px){{.metrics{{grid-template-columns:1fr 1fr}}.metric:nth-child(2){{border-right:0}}.metric:nth-child(-n+2){{border-bottom:1px solid var(--line)}}.intro{{align-items:flex-start;flex-direction:column}}.filters{{grid-template-columns:1fr 1fr}}.filters input{{grid-column:1/-1}}.list-head{{display:none}}summary{{grid-template-columns:88px 76px 1fr 20px}}.notice-domain{{grid-column:3}}.role{{grid-column:1/3}}.notice-page{{grid-column:3/5;white-space:normal}}.status{{display:none}}}}
@media(max-width:640px){{.topbar{{align-items:flex-start;flex-direction:column}}.sync{{text-align:left}}main{{padding-top:24px}}.metrics{{grid-template-columns:1fr 1fr}}.metric{{padding:18px 16px}}.metric-value{{font-size:26px}}.priority-item{{grid-template-columns:1fr}}.priority-date{{padding:0}}.filters{{grid-template-columns:1fr}}.filters input{{grid-column:auto}}summary{{grid-template-columns:1fr auto;padding:14px}}.notice-date{{grid-column:1}}.notice-id{{grid-column:2}}.notice-domain{{grid-column:1/3}}.role{{grid-column:1/3}}.notice-page{{grid-column:1/3}}.chevron{{position:absolute;right:16px}}summary{{position:relative}}.detail-meta,.url-columns{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">DMCA Monitor <span>NSN</span></div>
  <div class="sync">Database synced: {_e(synced_at)}<br>Page generated: {_e(generated)}</div>
</header>
<main>
  <section class="intro">
    <div>
      <h1>Lumen notices</h1>
      <p>NSN pages targeted by claims, NSN content copied elsewhere, and notices still awaiting retrieval. Data comes directly from Lumen rather than Google's delayed report.</p>
    </div>
    <div class="coverage"><strong>{summary['site_scopes']}</strong> sites · <strong>{summary['search_domains']}</strong> domains · <strong>{summary['baseline_domains']}</strong> baselines</div>
  </section>

  <section class="metrics" aria-label="Summary">
    <div class="metric"><span class="metric-label">Notices recorded</span><strong class="metric-value">{summary['total_notices']}</strong><span class="metric-note">{summary['complete']} with full details</span></div>
    <div class="metric"><span class="metric-label">NSN pages targeted</span><strong class="metric-value">{summary['targeted']}</strong><span class="metric-note">Review first</span></div>
    <div class="metric"><span class="metric-label">NSN is the source</span><strong class="metric-value">{summary['source']}</strong><span class="metric-note">Content copied to other sites</span></div>
    <div class="metric"><span class="metric-label">Pending retrieval or review</span><strong class="metric-value">{summary['unresolved']}</strong><span class="metric-note">Lost link, pending request, or unclear role</span></div>
  </section>

  <section>
    <div class="section-head"><h2>Needs attention</h2><p>Targeted pages and incomplete notices</p></div>
    <div class="priority-list">{priority_html}</div>
  </section>

  <section>
    <div class="section-head"><h2>All notices</h2><p>Open a row to view its URLs</p></div>
    <div class="filters">
      <input id="search" type="search" placeholder="Search by domain, URL, sender, or ID" aria-label="Search">
      <select id="role-filter" aria-label="Filter by role">
        <option value="">All roles</option><option value="targeted">NSN page targeted</option><option value="source">NSN is the source</option><option value="unresolved">Pending retrieval</option><option value="other">Role needs review</option>
      </select>
      <select id="status-filter" aria-label="Filter by status">
        <option value="">All statuses</option><option value="complete">Complete</option><option value="pending">Pending</option><option value="token_consumption_error">Access link lost</option><option value="email_timeout">Email delayed</option>
      </select>
    </div>
    <div class="list-head"><span>Date</span><span>Notice</span><span>Domain</span><span>Role</span><span>Monitored page</span><span>Status</span><span></span></div>
    <div class="notice-list" id="notice-list">{notices_html}</div>
    <div class="result-count" id="result-count">{len(notices)} result(s)</div>
  </section>
</main>
<script>
const rows=[...document.querySelectorAll('details.notice')];
const search=document.getElementById('search');
const role=document.getElementById('role-filter');
const status=document.getElementById('status-filter');
const count=document.getElementById('result-count');
function applyFilters(){{
  const q=search.value.trim().toLowerCase(); let visible=0;
  rows.forEach(row=>{{
    const ok=(!q||row.dataset.search.includes(q))&&(!role.value||row.dataset.role===role.value)&&(!status.value||row.dataset.status===status.value);
    row.hidden=!ok; if(ok) visible++;
  }});
  count.textContent=`${{visible}} result(s)`;
}}
[search,role,status].forEach(el=>el.addEventListener('input',applyFilters));
</script>
</body>
</html>"""
