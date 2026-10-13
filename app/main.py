"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from pymongo import MongoClient
from bson.json_util import dumps, loads


load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

# Create indexes for faster queries and prevent duplicate records
db.employees.create_index("emp_code", unique=True)
db.employees.create_index("department")


db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True
)

db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1)]
)

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable"
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    import re
    from pymongo.errors import DuplicateKeyError

    # Validate employee code: EMP followed by 4 to 6 digits
    if not re.fullmatch(r"EMP\d{4,6}", body.emp_code):
        raise HTTPException(
            status_code=422,
            detail="emp_code must be EMP followed by 4 to 6 digits"
        )

    # Shift start and end must be different
    if body.shift_start == body.shift_end:
        raise HTTPException(
            status_code=422,
            detail="shift_start and shift_end must be different"
        )

    # Validate shift time format
    for field, value in (
        ("shift_start", body.shift_start),
        ("shift_end", body.shift_end),
    ):
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"{field} must use HH:MM format"
            )

    # Validate joining date
    try:
        datetime.strptime(body.joined_on, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="joined_on must use YYYY-MM-DD format"
        )

    # Check whether the employee already exists
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1"
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100"
        )

    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="status must be PRESENT, WFH, or ON_DUTY"
        )

    if body.punched_at is not None:
        if isinstance(body.punched_at, bool) or body.punched_at < 100000000000:
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds"
            )

        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        )
    else:
        ts = datetime.now(timezone.utc)

    # Store instants at whole-second precision
    ts = ts.replace(microsecond=0)

    # Attendance date is based on Indian Standard Time
    ist_ts = ts.astimezone(IST)
    attendance_date = ist_ts.date().isoformat()

    # Check for an existing attendance record
    if db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": attendance_date
    }):
        raise HTTPException(
            status_code=409,
            detail="Attendance record already exists for this date"
        )

    # Calculate which shift the punch belongs to
    shift_start = datetime.strptime(
        emp["shift_start"], "%H:%M"
    ).time()
    shift_end = datetime.strptime(
        emp["shift_end"], "%H:%M"
    ).time()

    if shift_end <= shift_start and ist_ts.time() < shift_end:
        attendance_date = (
            ist_ts.date() - timedelta(days=1)
        ).isoformat()

    punch_in_ist = ts.astimezone(IST)
    late_minutes = compute_late_minutes(
        punch_in_ist,
        emp["shift_start"]
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except Exception as exc:
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            raise HTTPException(
                status_code=409,
                detail="Attendance record already exists for this date"
            )
        raise

    # Return epoch milliseconds; never expose MongoDB's _id
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(doc["punch_in"].timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1"
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100"
        )

    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="date_from must use YYYY-MM-DD format"
            )

    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="date_to must use YYYY-MM-DD format"
            )

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to"
        )

    if status and status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status"
        )

    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)

    skip = (page - 1) * page_size

    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    for doc in docs:
        for field in ("punch_in", "punch_out"):
            value = doc.get(field)
            if isinstance(value, datetime):
                doc[field] = int(value.timestamp() * 1000)

        for entry in doc.get("history", []):
            if isinstance(entry.get("at"), datetime):
                entry["at"] = int(entry["at"].timestamp() * 1000)

            for change in entry.get("changes", {}).values():
                if isinstance(change, datetime):
                    # This conversion is handled below for the response.
                    pass

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-out")
def punch_out(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or body.punched_at < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )

        try:
            punch_out_time = datetime.fromtimestamp(
                body.punched_at / 1000,
                tz=timezone.utc,
            ).replace(microsecond=0)
        except (ValueError, OverflowError, OSError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punch_out timestamp",
            )
    else:
        punch_out_time = datetime.now(timezone.utc).replace(
            microsecond=0
        )

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punch_out_time},
            "punch_out": None,
        },
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No open punch-in record found",
        )

    punch_in_time = record["punch_in"]

    # MongoDB may return naive datetimes even when stored in UTC.
    # Treat those values as UTC before comparing them.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    if punch_out_time <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="punch_out must be after punch_in",
        )

    if punch_out_time > punch_in_time + timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="punch_out cannot be more than 24 hours after punch_in",
        )

    work_hours = compute_work_hours(punch_in_time, punch_out_time)

    overtime_minutes = compute_overtime(
        punch_out_time.astimezone(IST),
        emp["shift_end"],
        record["date"],
    )

    if overtime_minutes < 30:
        overtime_minutes = 0

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": punch_out_time,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already punched out",
        )

    history = record.get("history", [])
    for entry in history:
        if isinstance(entry.get("at"), datetime):
            entry["at"] = int(entry["at"].timestamp() * 1000)

        for key in ("punch_in", "punch_out"):
            value = entry.get("changes", {}).get(key)
            if isinstance(value, datetime):
                entry["changes"][key] = int(value.timestamp() * 1000)

    return {
        "emp_code": record["emp_code"],
        "date": record["date"],
        "status": record["status"],
        "punch_in": int(punch_in_time.timestamp() * 1000),
        "punch_out": int(punch_out_time.timestamp() * 1000),
        "work_hours": work_hours,
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "history": history,
    }



@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: dict,
):
    from datetime import date as date_type
    from decimal import Decimal, ROUND_HALF_UP
    from pymongo.errors import PyMongoError

    # Validate the attendance date
    try:
        date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format"
        )

    allowed_fields = {"status", "punch_in", "punch_out"}
    supplied_fields = set(body.keys()) - {"reason", "regularized_by"}

    if supplied_fields - allowed_fields:
        raise HTTPException(
            status_code=422,
            detail="Only status, punch_in, and punch_out may be changed"
        )

    reason = body.get("reason")
    regularized_by = body.get("regularized_by")

    if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 200:
        raise HTTPException(
            status_code=422,
            detail="reason must contain 5 to 200 characters"
        )

    if not isinstance(regularized_by, str) or not 1 <= len(regularized_by.strip()) <= 50:
        raise HTTPException(
            status_code=422,
            detail="regularized_by must contain 1 to 50 characters"
        )

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    old_status = record["status"]
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    new_status = body.get("status", old_status)

    if new_status not in {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}:
        raise HTTPException(status_code=422, detail="Invalid attendance status")

    if new_status in {"ABSENT", "LEAVE"}:
        if "punch_in" in body or "punch_out" in body:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch times"
            )
        new_in = None
        new_out = None
    else:
        def parse_instant(field, old_value):
            if field not in body:
                return old_value

            value = body[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 100000000000
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} must be epoch milliseconds"
                )

            try:
                return datetime.fromtimestamp(
                    value / 1000, tz=timezone.utc
                ).replace(microsecond=0)
            except (ValueError, OverflowError, OSError):
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid {field} timestamp"
                )

        new_in = parse_instant("punch_in", old_in)
        new_out = parse_instant("punch_out", old_out)

        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in"
            )

        if new_in.astimezone(IST).date().isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must fall on the attendance date in IST"
            )

        if new_out is not None:
            if new_out <= new_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in"
                )
            if new_out > new_in + timedelta(hours=24):
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be within 24 hours of punch_in"
                )

    # Only record fields that actually changed in history
    changes = {}
    if new_status != old_status:
        changes["status"] = new_status
    if new_in != old_in:
        changes["punch_in"] = (
            int(new_in.timestamp() * 1000) if new_in else None
        )
    if new_out != old_out:
        changes["punch_out"] = (
            int(new_out.timestamp() * 1000) if new_out else None
        )

    if not changes:
        raise HTTPException(status_code=422, detail="Request makes no changes")

    late_minutes = 0
    work_hours = None
    overtime_minutes = 0
    half_day = False

    if new_status in {"PRESENT", "WFH", "ON_DUTY"}:
        shift_start = emp["shift_start"]
        shift_end = emp["shift_end"]

        late_minutes = compute_late_minutes(
            new_in.astimezone(IST), shift_start
        )

        if new_out is not None:
            seconds = (new_out - new_in).total_seconds()
            work_hours = float(
                Decimal(str(seconds / 3600)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
            half_day = work_hours < 4.50

            overtime_minutes = compute_overtime(
                new_out.astimezone(IST), shift_end, date
            )
            if overtime_minutes < 30:
                overtime_minutes = 0

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": regularized_by.strip(),
        "reason": reason.strip(),
        "changes": changes,
    }

    update = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    # Compare the original record so concurrent edits do not silently overwrite history
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "status": old_status,
            "punch_in": old_in,
            "punch_out": old_out,
            "history": record.get("history", []),
        },
        {
            "$set": update,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the correction"
        )

    updated = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        if isinstance(updated.get(field), datetime):
            updated[field] = int(updated[field].timestamp() * 1000)

    for entry in updated.get("history", []):
        if isinstance(entry.get("at"), datetime):
            entry["at"] = int(entry["at"].timestamp() * 1000)
        for key in ("punch_in", "punch_out"):
            value = entry.get("changes", {}).get(key)
            if isinstance(value, datetime):
                entry["changes"][key] = int(value.timestamp() * 1000)

    return updated


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(emp_code: str, month: str):
    from calendar import monthrange
    from decimal import Decimal, ROUND_HALF_UP

    # Validate month format
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    year = month_start.year
    month_number = month_start.month
    last_day = monthrange(year, month_number)[1]
    month_end = month_start.replace(day=last_day)

    try:
        joined_on = datetime.strptime(
            emp["joined_on"], "%Y-%m-%d"
        ).date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Employee joined_on date is invalid"
        )

    # Count weekdays from joining date through the end of the month
    first_workday = max(joined_on, month_start)
    working_days = 0

    if first_workday <= month_end:
        current_day = first_workday
        while current_day <= month_end:
            if current_day.weekday() < 5:
                working_days += 1
            current_day += timedelta(days=1)

    logs = list(db.attendance_logs.find({
        "emp_code": emp_code,
        "date": {
            "$gte": month_start.isoformat(),
            "$lte": month_end.isoformat(),
        },
    }))

    presence_statuses = {"PRESENT", "WFH", "ON_DUTY"}

    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0

    for log in logs:
        log_date = datetime.strptime(log["date"], "%Y-%m-%d").date()

        if log["status"] == "LEAVE":
            leave_days += 1

        if log["status"] in presence_statuses and log_date.weekday() < 5:
            present_days += 0.5 if log.get("half_day", False) else 1.0

        late_minutes = log.get("late_minutes", 0) or 0
        if late_minutes > 0:
            late_count += 1
            total_late_minutes += late_minutes

        total_overtime_minutes += log.get("overtime_minutes", 0) or 0

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            Decimal(str(present_days * 100 / working_days)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    from calendar import monthrange
    from decimal import Decimal, ROUND_HALF_UP

    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)

    employee_query = {}
    if department:
        employee_query["department"] = department

    employees = list(db.employees.find(employee_query, {"_id": 0}))

    # Include employees who had joined by the end of the requested month
    eligible_employees = []
    for emp in employees:
        try:
            joined = datetime.strptime(
                emp["joined_on"], "%Y-%m-%d"
            ).date()
        except ValueError:
            continue

        if joined <= month_end:
            eligible_employees.append(emp)

    if not eligible_employees:
        return []

    eligible_codes = {emp["emp_code"] for emp in eligible_employees}
    department_names = {emp["emp_code"]: emp["department"] for emp in eligible_employees}

    logs = list(db.attendance_logs.find({
        "emp_code": {"$in": list(eligible_codes)},
        "date": {
            "$gte": month_start.isoformat(),
            "$lte": month_end.isoformat(),
        },
    }))

    summaries = {}

    for emp in eligible_employees:
        dept = emp["department"]
        summaries.setdefault(dept, {
            "department": dept,
            "headcount": 0,
            "present_days": 0.0,
            "work_hours_values": [],
            "late_count": 0,
            "total_late_minutes": 0,
            "leave_count": 0,
            "on_duty_count": 0,
        })
        summaries[dept]["headcount"] += 1

    for log in logs:
        emp_dept = department_names.get(log["emp_code"])
        if emp_dept not in summaries:
            continue

        summary = summaries[emp_dept]
        log_date = datetime.strptime(log["date"], "%Y-%m-%d").date()

        if log["status"] in {"PRESENT", "WFH", "ON_DUTY"} and log_date.weekday() < 5:
            summary["present_days"] += (
                0.5 if log.get("half_day", False) else 1.0
            )

        if log["status"] == "LEAVE":
            summary["leave_count"] += 1

        if log["status"] == "ON_DUTY":
            summary["on_duty_count"] += 1

        late = log.get("late_minutes", 0) or 0
        if late > 0:
            summary["late_count"] += 1
            summary["total_late_minutes"] += late

        if (
            log["status"] in {"PRESENT", "WFH", "ON_DUTY"}
            and log.get("work_hours") is not None
        ):
            summary["work_hours_values"].append(
                float(log["work_hours"])
            )

    result = []
    for dept in sorted(summaries):
        summary = summaries[dept]
        values = summary.pop("work_hours_values")

        average_hours = None
        if values:
            average_hours = float(
                Decimal(str(sum(values) / len(values))).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )

        result.append({
            **summary,
            "avg_work_hours": average_hours,
        })

    return result



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    from calendar import monthrange

    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format"
        )

    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50"
        )

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)

    employee_query = {}
    if department:
        employee_query["department"] = department

    employees = list(db.employees.find(employee_query, {"_id": 0}))
    employee_map = {}

    for emp in employees:
        try:
            joined = datetime.strptime(
                emp["joined_on"], "%Y-%m-%d"
            ).date()
        except ValueError:
            continue

        if joined <= month_end:
            employee_map[emp["emp_code"]] = emp

    if not employee_map:
        return []

    logs = list(db.attendance_logs.find({
        "emp_code": {"$in": list(employee_map.keys())},
        "date": {
            "$gte": month_start.isoformat(),
            "$lte": month_end.isoformat(),
        },
        "late_minutes": {"$gt": 0},
    }))

    totals = {}
    for log in logs:
        code = log["emp_code"]
        totals[code] = totals.get(code, 0) + log.get("late_minutes", 0)

    ranked = sorted(
        totals.items(),
        key=lambda item: (-item[1], item[0]),
    )

    result = []
    previous_total = None
    current_rank = 0

    for index, (code, total_late) in enumerate(ranked, start=1):
        if total_late != previous_total:
            current_rank = index
        previous_total = total_late

        emp = employee_map[code]
        result.append({
            "rank": current_rank,
            "emp_code": code,
            "name": emp["name"],
            "department": emp["department"],
            "total_late_minutes": total_late,
        })

    # Include ties at the cutoff rank
    if len(result) > limit:
        cutoff_rank = result[limit - 1]["rank"]
        result = [item for item in result if item["rank"] <= cutoff_rank]

    return result



def build_department_trend_pipeline(
    department,
    employees,
    employee_codes,
    start_datetime,
    end_exclusive,
):
    return [
       {"$match": {"department": department}},
       {"$limit": 1},
       {"$project": {"_id": 0, "date_value": {"$literal": start_datetime}}},
        {
            "$densify": {
                "field": "date_value",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_datetime, end_exclusive],
                },
            }
        },
        {
            "$set": {
                "employees": {"$literal": employees},
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date_value",
                        "timezone": "UTC",
                    }
                },
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$date_value"},
                                [1, 7],
                            ]
                        }
                    ]
                },
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "date",
                "foreignField": "date",
                "pipeline": [
                    {"$match": {"emp_code": {"$in": employee_codes}}}
                ],
                "as": "logs",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": "$employees",
                            "as": "emp",
                            "cond": {
                                "$and": [
                                    {
                                        "$ne": [
                                            {"$ifNull": ["$$emp.joined_on", None]},
                                            None,
                                        ]
                                    },
                                    {"$lte": ["$$emp.joined_on", "$date"]},
                                ]
                            },
                        }
                    }
                },
                "present_count": {
                    "$reduce": {
                        "input": "$logs",
                        "initialValue": 0.0,
                        "in": {
                            "$add": [
                                "$$value",
                                {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$$this.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {
                                                    "$eq": [
                                                        {"$ifNull": ["$$this.half_day", False]},
                                                        True,
                                                    ]
                                                },
                                                0.5,
                                                1.0,
                                            ]
                                        },
                                        0.0,
                                    ]
                                },
                            ]
                        },
                    }
                },
                "late_count": {
                    "$reduce": {
                        "input": "$logs",
                        "initialValue": 0,
                        "in": {
                            "$add": [
                                "$$value",
                                {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {"$ifNull": ["$$this.late_minutes", 0]},
                                                0,
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                },
                            ]
                        },
                    }
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            {"$multiply": ["$present_count", 100]},
                                                            "$headcount",
                                                        ]
                                                    },
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date_value": 1},
                "output": {
                    "moving_avg_raw": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$set": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$ne": ["$moving_avg_raw", None]},
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {"$multiply": ["$moving_avg_raw", 10000]},
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {"$sort": {"date_value": 1}},
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]



@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = __import__("fastapi").Query(..., alias="from"),
    to_date: str = __import__("fastapi").Query(..., alias="to"),
):
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if end < start:
        raise HTTPException(
            status_code=422,
            detail="to must not be before from",
        )

    if (end - start).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    employees = list(
        db.employees.find(
            {"department": department},
            {"_id": 0, "emp_code": 1, "joined_on": 1},
        )
    )

    if not employees:
        raise HTTPException(
            status_code=404,
            detail="No employees found in department",
        )

    employee_codes = [
        emp["emp_code"] for emp in employees
    ]

    start_datetime = datetime.combine(start, datetime.min.time())
    end_exclusive = datetime.combine(
        end + timedelta(days=1),
        datetime.min.time(),
    )

    pipeline = build_department_trend_pipeline(
        department,
        employees,
        employee_codes,
        start_datetime,
        end_exclusive,
    )

    items = list(db.employees.aggregate(pipeline))

    return {
        "department": department,
        "items": items,
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    from_date: Optional[str] = __import__("fastapi").Query(None, alias="from"),
    to_date: Optional[str] = __import__("fastapi").Query(None, alias="to"),
):
    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail="Unsupported endpoint name",
        )

    def explain_find(collection, query, **options):
        command = {
            "find": collection,
            "filter": query,
        }
        command.update(options)

        return db.command({
            "explain": command,
            "verbosity": "executionStats",
        })

    if endpoint == "attendance_list":
        if page < 1:
            raise HTTPException(
                status_code=422,
                detail="page must be at least 1",
            )

        if page_size < 1 or page_size > 100:
            raise HTTPException(
                status_code=422,
                detail="page_size must be between 1 and 100",
            )

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must not be after date_to",
            )

        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if date_from or date_to:
            query["date"] = {}

            if date_from:
                query["date"]["$gte"] = date_from

            if date_to:
                query["date"]["$lte"] = date_to

        if status:
            if status not in {
                "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
            }:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid attendance status",
                )

            query["status"] = status

        plan = explain_find(
            "attendance_logs",
            query,
            sort={"date": -1, "emp_code": 1},
            skip=(page - 1) * page_size,
            limit=page_size,
        )

        return Response(
            content=dumps(plan),
            media_type="application/json",
        )

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        try:
            month_start = datetime.strptime(
                month, "%Y-%m"
            ).date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        from calendar import monthrange

        last_day = monthrange(
            month_start.year, month_start.month
        )[1]
        month_end = month_start.replace(day=last_day)

        if not db.employees.find_one({"emp_code": emp_code}):
            raise HTTPException(
                status_code=404,
                detail="Employee not found",
            )

        query = {
            "emp_code": emp_code,
            "date": {
                "$gte": month_start.isoformat(),
                "$lte": month_end.isoformat(),
            },
        }

        plan = explain_find("attendance_logs", query)

        return Response(
            content=dumps(plan),
            media_type="application/json",
        )

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        try:
            month_start = datetime.strptime(
                month, "%Y-%m"
            ).date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        from calendar import monthrange

        last_day = monthrange(
            month_start.year, month_start.month
        )[1]
        month_end = month_start.replace(day=last_day)

        employee_query = {}
        if department:
            employee_query["department"] = department

        employees = list(
            db.employees.find(employee_query, {"_id": 0})
        )

        eligible_codes = []

        for emp in employees:
            try:
                joined = datetime.strptime(
                    emp["joined_on"], "%Y-%m-%d"
                ).date()
            except (ValueError, KeyError):
                continue

            if joined <= month_end:
                eligible_codes.append(emp["emp_code"])

        query = {
            "emp_code": {"$in": eligible_codes},
            "date": {
                "$gte": month_start.isoformat(),
                "$lte": month_end.isoformat(),
            },
        }

        plan = explain_find("attendance_logs", query)

        return Response(
            content=dumps(plan),
            media_type="application/json",
        )

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        if limit < 1 or limit > 50:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 50",
            )

        try:
            month_start = datetime.strptime(
                month, "%Y-%m"
            ).date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        from calendar import monthrange

        last_day = monthrange(
            month_start.year, month_start.month
        )[1]
        month_end = month_start.replace(day=last_day)

        employee_query = {}
        if department:
            employee_query["department"] = department

        employees = list(
            db.employees.find(employee_query, {"_id": 0})
        )

        eligible_codes = []

        for emp in employees:
            try:
                joined = datetime.strptime(
                    emp["joined_on"], "%Y-%m-%d"
                ).date()
            except (ValueError, KeyError):
                continue

            if joined <= month_end:
                eligible_codes.append(emp["emp_code"])

        query = {
            "emp_code": {"$in": eligible_codes},
            "date": {
                "$gte": month_start.isoformat(),
                "$lte": month_end.isoformat(),
            },
            "late_minutes": {"$gt": 0},
        }

        plan = explain_find("attendance_logs", query)

        return Response(
            content=dumps(plan),
            media_type="application/json",
        )

    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="department, from, and to are required",
            )

        try:
            start = datetime.strptime(from_date, "%Y-%m-%d").date()
            end = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="from and to must use YYYY-MM-DD format",
            )

        if end < start:
            raise HTTPException(
                status_code=422,
                detail="to must not be before from",
            )

        if (end - start).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days",
            )

        employees = list(
            db.employees.find(
                {"department": department},
                {"_id": 0, "emp_code": 1, "joined_on": 1},
            )
        )

        if not employees:
            raise HTTPException(
                status_code=404,
                detail="No employees found in department",
            )

        employee_codes = [emp["emp_code"] for emp in employees]

        start_datetime = datetime.combine(start, datetime.min.time())
        end_exclusive = datetime.combine(
            end + timedelta(days=1),
            datetime.min.time(),
        )

        pipeline = build_department_trend_pipeline(
            department,
            employees,
            employee_codes,
            start_datetime,
            end_exclusive,
        )

        plan = db.command(
            {
                "explain": {
                    "aggregate": "employees",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }
        )

        return Response(
            content=dumps(plan),
            media_type="application/json",
        )

    raise HTTPException(
        status_code=400,
        detail="Unsupported explain endpoint",
    )






# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
