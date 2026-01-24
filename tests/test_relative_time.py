from datetime import datetime, timedelta

from main import format_relative_time


def test_minutes_ago():
    now = datetime.now()
    created = now - timedelta(minutes=5)
    assert format_relative_time(created.isoformat()) == "5 minutes ago"


def test_hours_ago():
    now = datetime.now()
    created = now - timedelta(hours=3)
    assert format_relative_time(created.isoformat()) == "3 hours ago"


def test_days_ago():
    now = datetime.now()
    created = now - timedelta(days=5)
    assert format_relative_time(created.isoformat()) == "5 days ago"


def test_old_date_format():
    # More than a month ago should show the date
    now = datetime.now()
    created = now - timedelta(days=45)
    result = format_relative_time(created.isoformat())
    # Should be in format "YYYY Mon Dth" e.g. "2025 Dec 10th"
    assert created.strftime("%Y %b") in result
    assert any(suffix in result for suffix in ["st", "nd", "rd", "th"])
