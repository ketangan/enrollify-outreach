"""Pacific business-day eligibility for daily outreach."""
import calendar
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def closure(day: date) -> str:
    if day.weekday() >= 5:
        return "Weekend"

    # Include next January 1 because its observed holiday can fall on December 31.
    fixed = [(day.year, 1, 1), (day.year + 1, 1, 1), (day.year, 6, 19),
             (day.year, 7, 4), (day.year, 11, 11), (day.year, 12, 25)]
    for year, month, number in fixed:
        holiday = date(year, month, number)
        offset = {5: -1, 6: 1}.get(holiday.weekday(), 0)
        if day == holiday + timedelta(days=offset):
            return "Observed federal holiday"

    week = (day.day - 1) // 7 + 1
    if day.weekday() == calendar.MONDAY:
        if (day.month, week) in {(1, 3), (2, 3), (9, 1), (10, 2)}:
            return "Federal holiday"
        if day.month == 5 and day.day + 7 > calendar.monthrange(day.year, 5)[1]:
            return "Memorial Day"

    thursday = day - timedelta(days=1) if day.weekday() == calendar.FRIDAY else day
    if thursday.month == 11 and thursday.weekday() == calendar.THURSDAY and 22 <= thursday.day <= 28:
        return "Thanksgiving break"
    return ""


if __name__ == "__main__":
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    reason = closure(today)
    if today == date(2026, 11, 24) and os.environ["GITHUB_EVENT_NAME"] == "schedule":
        reason = "November 24 outreach handled by manual dispatch"
    print(today, reason or "Outreach open")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        print(f"open={'false' if reason else 'true'}", file=output)
