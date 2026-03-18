from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NoticeDetail:
    notice_id: str
    date: str  # YYYY-MM-DD
    urls_claimed: int
    urls_removed: int
    reporter_name: str
    owner_name: str
    lumen_url: str


@dataclass
class DomainReport:
    domain: str
    total_requested: int = 0
    total_removed: int = 0
    no_action_taken: int = 0
    duplicate: int = 0
    waiting: int = 0
    notices: list[NoticeDetail] = field(default_factory=list)
    error: Optional[str] = None
