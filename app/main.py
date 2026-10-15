"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import logging
from fastapi import FastAPI, HTTPException, Query, Path
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from datetime import datetime, timedelta, timezone, date, time
from dotenv import load_dotenv
from typing import Literal, Optional
from pydantic import BaseModel, Field, StrictInt
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
logger = logging.getLogger(__name__)


@app.on_event("startup")
def create_indexes():
    """Create required indexes safely on every application startup."""
    try:
        db.command("ping")

        # Employee codes must be unique, including concurrent creates.
        #db.employees.create_index(
        #   [("emp_code", ASCENDING)],
        #   unique=True,
        #   name="uq_employee_emp_code",
        #)
        db.employees.create_index(
            [("department", 1), ("joined_on", 1)],
            name="department_joined_on_idx",
        )

        # One attendance record per employee per attendance date.
        db.attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            name="uq_attendance_employee_date",
        )

        # Attendance listing and employee-specific date lookups.
        db.attendance_logs.create_index(
            [("date", DESCENDING), ("emp_code", ASCENDING)],
            name="ix_attendance_date_emp",
        )

        db.attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", DESCENDING)],
            name="ix_attendance_emp_date",
        )

    except PyMongoError:
        logger.exception("MongoDB initialization or index creation failed")

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def normalize_utc(value: datetime) -> datetime:
    """PyMongo returns naive datetimes representing UTC by default."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def epoch_ms_to_datetime(value: int) -> datetime:
    """Convert epoch milliseconds to a UTC datetime, truncated to seconds."""
    return (
        datetime(1970, 1, 1, tzinfo=UTC)
        + timedelta(milliseconds=value)
    ).replace(microsecond=0)


def to_epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    value = normalize_utc(value)
    return int(value.timestamp() * 1000)


def attendance_date(punch_in: datetime, employee: dict) -> str:
    """Return the IST date on which the shift started."""
    local = normalize_utc(punch_in).astimezone(IST)
    shift_start = time.fromisoformat(employee["shift_start"])
    shift_end = time.fromisoformat(employee["shift_end"])

    day = local.date()
    if shift_end <= shift_start and local.time().replace(tzinfo=None) < shift_end:
        day -= timedelta(days=1)

    return day.isoformat()


def compute_late_minutes(
    punch_in: datetime, shift_start: str, attendance_day: str
) -> int:
    shift_date = date.fromisoformat(attendance_day)
    start_time = time.fromisoformat(shift_start)
    shift_start_at = datetime.combine(
        shift_date, start_time, tzinfo=IST
    ).astimezone(UTC)

    seconds_late = (
        normalize_utc(punch_in) - shift_start_at
    ).total_seconds()

    # Strictly more than ten minutes; count from shift start.
    if seconds_late <= 600:
        return 0
    return int(seconds_late // 60)


def shift_end_datetime(
    attendance_day: str, shift_start: str, shift_end: str
) -> datetime:
    day = date.fromisoformat(attendance_day)
    start = time.fromisoformat(shift_start)
    end = time.fromisoformat(shift_end)

    if end <= start:
        day += timedelta(days=1)

    return datetime.combine(day, end, tzinfo=IST).astimezone(UTC)


def compute_overtime(
    punch_out: datetime,
    attendance_day: str,
    shift_start: str,
    shift_end: str,
) -> int:
    end_at = shift_end_datetime(
        attendance_day, shift_start, shift_end
    )
    extra_seconds = (
        normalize_utc(punch_out) - end_at
    ).total_seconds()

    minutes = int(extra_seconds // 60)
    return minutes if minutes >= 30 else 0


def compute_work_hours(
    punch_in: datetime, punch_out: datetime
) -> float:
    seconds = (
        normalize_utc(punch_out) - normalize_utc(punch_in)
    ).total_seconds()

    hours = Decimal(str(seconds)) / Decimal("3600")
    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )



def serialize_attendance(doc: dict) -> dict:
    result = {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_epoch_ms(doc.get("punch_in")),
        "punch_out": to_epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": [],
    }

    for entry in doc.get("history", []):
        changes = {}

        for field, change in entry.get("changes", {}).items():
            before = change.get("from")
            after = change.get("to")

            if field in ("punch_in", "punch_out"):
                before = to_epoch_ms(before)
                after = to_epoch_ms(after)

            changes[field] = {"from": before, "to": after}

        result["history"].append({
            "at": to_epoch_ms(entry["at"]),
            "by": entry["by"],
            "reason": entry["reason"],
            "changes": changes,
        })

    return result


def serialize_employee(doc: dict) -> dict:
    """Convert MongoDB employee documents to API response format."""
    doc = dict(doc)
    doc.pop("_id", None)

    created_at = doc.get("created_at")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        doc["created_at"] = int(created_at.timestamp() * 1000)

    return doc


def round_half_up(value, places=2):
    if value is None:
        return None

    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )

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
    punched_at: StrictInt | None = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: StrictInt | None = Field(
        default=None, ge=100000000000, le=4102444800000
    )
class RegularizeIn(BaseModel):
    status: Literal[
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    ] | None = None

    punch_in: StrictInt | None = Field(
        default=None, ge=100000000000, le=4102444800000
    )
    punch_out: StrictInt | None = Field(
        default=None, ge=100000000000, le=4102444800000
    )

    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        )

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}

    if department is not None:
        query["department"] = department

    total = db.employees.count_documents(query)

    cursor = (
        db.employees.find(query)
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = [serialize_employee(doc) for doc in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    ts = epoch_ms_to_datetime(
        body.punched_at if body.punched_at is not None else now_ms
    )
    day = attendance_date(ts, emp)

    doc = {
        "emp_code": body.emp_code,
        "date": day,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts, emp["shift_start"], day
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this date",
        )

    return serialize_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    ts = epoch_ms_to_datetime(
        body.punched_at if body.punched_at is not None else now_ms
    )
    record = db.attendance_logs.find_one(
    {
        "emp_code": body.emp_code,
        "punch_in": {
            "$type": "date",
            "$lte": ts,
        },
        "punch_out": None,
    },
    sort=[("punch_in", DESCENDING)],
)


    if record is None:
        raise HTTPException(status_code=404, detail="No punch-in found")

    start = normalize_utc(record["punch_in"])
    elapsed = (ts - start).total_seconds()

    if elapsed <= 0 or elapsed > 24 * 3600:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409, detail="Already punched out"
        )

    work_hours = compute_work_hours(start, ts)
    overtime = compute_overtime(
        ts,
        record["date"],
        emp["shift_start"],
        emp["shift_end"],
    )

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime,
                "half_day": work_hours < 4.50,
            }
        },
    )

    # Only one simultaneous punch-out may close the record.
    if result.modified_count != 1:
        raise HTTPException(
            status_code=409, detail="Already punched out"
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_attendance(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: date = Path(...),
    body: RegularizeIn = ...,
):
    day = date.isoformat()

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": day,
    })
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    supplied = body.model_fields_set

    # A correction to ABSENT or LEAVE cannot include punch fields.
    if body.status in ("ABSENT", "LEAVE"):
        if "punch_in" in supplied or "punch_out" in supplied:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch fields",
            )

    old_status = record["status"]
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    old_in = normalize_utc(old_in) if old_in is not None else None
    old_out = normalize_utc(old_out) if old_out is not None else None

    status = body.status if "status" in supplied else old_status

    if status in ("ABSENT", "LEAVE"):
        punch_in = None
        punch_out = None
    else:
        punch_in = (
            epoch_ms_to_datetime(body.punch_in)
            if "punch_in" in supplied and body.punch_in is not None
            else None if "punch_in" in supplied else old_in
        )
        punch_out = (
            epoch_ms_to_datetime(body.punch_out)
            if "punch_out" in supplied and body.punch_out is not None
            else None if "punch_out" in supplied else old_out
        )

        if punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence status requires punch_in",
            )

        if attendance_date(punch_in, emp) != day:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

        if punch_out is not None:
            elapsed = (punch_out - punch_in).total_seconds()
            if elapsed <= 0 or elapsed > 24 * 3600:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in and within 24 hours",
                )

    if status in ("ABSENT", "LEAVE"):
        late_minutes = 0
        work_hours = None
        overtime_minutes = 0
        half_day = False
    else:
        late_minutes = compute_late_minutes(
            punch_in, emp["shift_start"], day
        )

        if punch_out is None:
            work_hours = None
            overtime_minutes = 0
            half_day = False
        else:
            work_hours = compute_work_hours(punch_in, punch_out)
            overtime_minutes = compute_overtime(
                punch_out,
                day,
                emp["shift_start"],
                emp["shift_end"],
            )
            half_day = work_hours < 4.50

    new_values = {
        "status": status,
        "punch_in": punch_in,
        "punch_out": punch_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    # Compare effective values; legacy missing defaults are respected.
    defaults = {
        "work_hours": None,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "half_day": False,
    }

    changes = {}
    for field, new_value in new_values.items():
        old_value = record.get(field, defaults.get(field))
        if field in ("punch_in", "punch_out") and old_value is not None:
            old_value = normalize_utc(old_value)

        if old_value != new_value:
            changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the attendance record",
        )

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Optimistic concurrency: a competing correction must not overwrite
    # this record's state or silently lose an audit entry.
    history = record.get("history", [])
    query = {
        "_id": record["_id"],
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
    }

    if "history" in record:
        query["history"] = history
    else:
        query["history"] = {"$exists": False}

    result = db.attendance_logs.update_one(
        query,
        {
            "$set": new_values,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the correction",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_attendance(updated)


def build_employee_monthly_pipelines(
    emp_code: str,
    month: str,
):
    year, month_number = map(int, month.split("-"))

    if month_number == 12:
        next_month = f"{year + 1:04d}-01"
    else:
        next_month = f"{year:04d}-{month_number + 1:02d}"

    month_start = f"{month}-01"
    month_end = f"{next_month}-01"

    working_pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$set": {
                "_month_start": {
                    "$dateFromString": {"dateString": month_start}
                },
                "_month_end": {
                    "$dateFromString": {"dateString": month_end}
                },
                "_joined": {
                    "$dateFromString": {
                           "dateString": "$joined_on",
                            "onError": None,
                            "onNull": None
                    }
                },
            }
        },
        {
            "$set": {
                "_effective_start": {
                    "$cond": [
                        {"$gt": ["$_joined", "$_month_start"]},
                        "$_joined",
                        "$_month_start",
                    ]
                }
            }
        },
        {
            "$set": {
                "_day_offsets": {
                    "$range": [
                        0,
                        {
                            "$max": [
                                0,
                                {
                                    "$dateDiff": {
                                        "startDate": "$_effective_start",
                                        "endDate": "$_month_end",
                                        "unit": "day",
                                    }
                                },
                            ]
                        },
                    ]
                }
            }
        },
        {
            "$set": {
                "working_days": {
                    "$size": {
                        "$filter": {
                            "input": {
                                "$map": {
                                    "input": "$_day_offsets",
                                    "as": "offset",
                                    "in": {
                                        "$dateAdd": {
                                            "startDate": "$_effective_start",
                                            "unit": "day",
                                            "amount": "$$offset",
                                        }
                                    },
                                }
                            },
                            "as": "day",
                            "cond": {
                                "$and": [
                                    {"$gte": [{"$dayOfWeek": "$$day"}, 2]},
                                    {"$lte": [{"$dayOfWeek": "$$day"}, 6]},
                                ]
                            },
                        }
                    }
                }
            }
        },
        {"$project": {"_id": 0, "working_days": 1}},
    ]

    attendance_pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start,
                    "$lt": month_end,
                },
            }
        },
        {
            "$set": {
                "_calendar_date": {
                    "$dateFromString": {"dateString": "$date"}
                },
                "_late": {"$ifNull": ["$late_minutes", 0]},
                "_overtime": {"$ifNull": ["$overtime_minutes", 0]},
                "_half_day": {"$ifNull": ["$half_day", False]},
            }
        },
        {
            "$set": {
                "_weekday": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_calendar_date"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_calendar_date"}, 6]},
                    ]
                },
                "_is_present": {
                    "$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$and": ["$_weekday", "$_is_present"]},
                            {"$cond": ["$_half_day", 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$_late", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$_late"},
                "total_overtime_minutes": {"$sum": "$_overtime"},
            }
        },
    ]

    return working_pipeline, attendance_pipeline

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
):
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Calculate the exclusive start of the next month.
    year, month_number = map(int, month.split("-"))
    if month_number == 12:
        next_month = f"{year + 1:04d}-01"
    else:
        next_month = f"{year:04d}-{month_number + 1:02d}"

    month_start = f"{month}-01"
    month_end = f"{next_month}-01"

    # MongoDB aggregation: count weekdays from joining date
    # through the end of the requested month.
    working_pipeline = [
        {"$match": {"emp_code": emp_code}},
        {
            "$set": {
                "_month_start": {
                    "$dateFromString": {
                        "dateString": month_start
                    }
                },
                "_month_end": {
                    "$dateFromString": {
                        "dateString": month_end
                    }
                },
                "_joined": {
                    "$dateFromString": {
                        "dateString": "$joined_on",
                        "onError": None,
                        "onNull": None
                    }
                },
            }
        },
        {
            "$set": {
                "_effective_start": {
                    "$cond": [
                        {"$gt": ["$_joined", "$_month_start"]},
                        "$_joined",
                        "$_month_start",
                    ]
                }
            }
        },
        {
            "$set": {
                "_day_offsets": {
                    "$range": [
                        0,
                        {
                            "$max": [
                                0,
                                {
                                    "$dateDiff": {
                                        "startDate": "$_effective_start",
                                        "endDate": "$_month_end",
                                        "unit": "day",
                                    }
                                },
                            ]
                        },
                    ]
                }
            }
        },
        {
            "$set": {
                "working_days": {
                    "$size": {
                        "$filter": {
                            "input": {
                                "$map": {
                                    "input": "$_day_offsets",
                                    "as": "offset",
                                    "in": {
                                        "$dateAdd": {
                                            "startDate": "$_effective_start",
                                            "unit": "day",
                                            "amount": "$$offset",
                                        }
                                    },
                                }
                            },
                            "as": "day",
                            "cond": {
                                "$and": [
                                    {
                                        "$gte": [
                                            {"$dayOfWeek": "$$day"},
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {"$dayOfWeek": "$$day"},
                                            6,
                                        ]
                                    },
                                ]
                            },
                        }
                    }
                }
            }
        },
        {"$project": {"_id": 0, "working_days": 1}},
    ]

    working_result = list(db.employees.aggregate(working_pipeline))
    working_days = (
        working_result[0]["working_days"]
        if working_result
        else 0
    )

    # Aggregate stored attendance fields. Do not recompute derived values.
    attendance_pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start,
                    "$lt": month_end,
                },
            }
        },
        {
            "$set": {
                "_calendar_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "onError": None,
                        "onNull": None
                    }
                },
                "_late": {"$ifNull": ["$late_minutes", 0]},
                "_overtime": {
                    "$ifNull": ["$overtime_minutes", 0]
                },
                "_half_day": {
                    "$ifNull": ["$half_day", False]
                },
            }
        },
        {
            "$set": {
                "_weekday": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_calendar_date"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_calendar_date"}, 6]},
                    ]
                },
                "_is_present": {
                    "$in": [
                        "$status",
                        ["PRESENT", "WFH", "ON_DUTY"],
                    ]
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    "$_weekday",
                                    "$_is_present",
                                ]
                            },
                            {
                                "$cond": [
                                    "$_half_day",
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$_late", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {"$sum": "$_late"},
                "total_overtime_minutes": {"$sum": "$_overtime"},
            }
        },
    ]
    
    working_pipeline, attendance_pipeline = (
        build_employee_monthly_pipelines(
            emp_code=emp_code,
            month=month,
        )
    )

    working_result = list(
        db.employees.aggregate(working_pipeline)
    )

    working_days = (
        working_result[0]["working_days"]
        if working_result
        else 0
    )

    results = list(
        db.attendance_logs.aggregate(attendance_pipeline)
    )


    results = list(db.attendance_logs.aggregate(attendance_pipeline))

    # An employee may have no attendance records in the month.
    totals = results[0] if results else {
        "present_days": 0,
        "leave_days": 0,
        "late_count": 0,
        "total_late_minutes": 0,
        "total_overtime_minutes": 0,
    }

    present_days = round_half_up(totals["present_days"])

    attendance_pct = (
        round_half_up(
            present_days / working_days * 100,
            places=4,
        )
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals["leave_days"],
        "late_count": totals["late_count"],
        "total_late_minutes": totals["total_late_minutes"],
        "total_overtime_minutes": totals["total_overtime_minutes"],
        "attendance_pct": attendance_pct,
    }

def build_department_summary_pipeline(
    month: str,
    department: Optional[str] = None,
):
    from datetime import date, timedelta

    month_start_date = date.fromisoformat(f"{month}-01")

    if month_start_date.month == 12:
        next_month_date = date(
            month_start_date.year + 1, 1, 1
        )
    else:
        next_month_date = date(
            month_start_date.year,
            month_start_date.month + 1,
            1,
        )

    month_start = month_start_date.isoformat()
    next_month = next_month_date.isoformat()
    month_end = (
        next_month_date - timedelta(days=1)
    ).isoformat()

    employee_match = {"joined_on": {"$lte": month_end}}

    if department is not None:
        employee_match["department"] = department

    # Paste your existing pipeline stages here,
    # starting with {"$match": employee_match}
    # and ending with the final $project stage.
    pipeline = [
        # Replace this placeholder with your existing stages.
    ]

    return pipeline


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
):
    pipeline = build_department_summary_pipeline(
        month=month,
        department=department,
    )

    results = list(db.employees.aggregate(pipeline))

    for item in results:
        item.pop("_id", None)

        item["present_days"] = round_half_up(
            item.get("present_days", 0)
        )

        if item.get("avg_work_hours") is not None:
            item["avg_work_hours"] = round_half_up(
                item["avg_work_hours"]
            )

    return {"month": month, "items": results}

    from datetime import date

    month_start_date = date.fromisoformat(f"{month}-01")

    if month_start_date.month == 12:
        next_month_date = date(month_start_date.year + 1, 1, 1)
    else:
        next_month_date = date(
            month_start_date.year,
            month_start_date.month + 1,
            1,
        )

    month_start = month_start_date.isoformat()
    next_month = next_month_date.isoformat()
    month_end = (next_month_date - timedelta(days=1)).isoformat()

    employee_match = {"joined_on": {"$lte": month_end}}
    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {"$gte": ["$date", month_start]},
                                    {"$lt": ["$date", next_month]},
                                ]
                            }
                        }
                    },
                    {
                        "$set": {
                            "_calendar_date": {
                                "$dateFromString": {
                                    "dateString": "$date"
                                }
                            },
                            "_late": {
                                "$ifNull": ["$late_minutes", 0]
                            },
                            "_overtime": {
                                "$ifNull": ["$overtime_minutes", 0]
                            },
                            "_half_day": {
                                "$ifNull": ["$half_day", False]
                            },
                        }
                    },
                    {
                        "$set": {
                            "_weekday": {
                                "$and": [
                                    {
                                        "$gte": [
                                            {"$dayOfWeek": "$_calendar_date"},
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {"$dayOfWeek": "$_calendar_date"},
                                            6,
                                        ]
                                    },
                                ]
                            },
                            "_is_present": {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$_weekday",
                                                "$_is_present",
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                "$_half_day",
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },
                            "work_hours_sum": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$_is_present",
                                                {
                                                    "$ne": [
                                                        "$work_hours",
                                                        None,
                                                    ]
                                                },
                                            ]
                                        },
                                        "$work_hours",
                                        0,
                                    ]
                                }
                            },
                            "work_hours_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                "$_is_present",
                                                {
                                                    "$ne": [
                                                        "$work_hours",
                                                        None,
                                                    ]
                                                },
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": ["$_late", 0]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "total_late_minutes": {"$sum": "$_late"},
                            "leave_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "LEAVE"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                            "on_duty_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "ON_DUTY"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "_attendance",
            }
        },
        {
            "$set": {
                "_stats": {
                    "$arrayElemAt": ["$_attendance", 0]
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {
                    "$sum": {
                        "$ifNull": ["$_stats.present_days", 0]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$ifNull": ["$_stats.work_hours_sum", 0]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$ifNull": ["$_stats.work_hours_count", 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$ifNull": ["$_stats.late_count", 0]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$ifNull": ["$_stats.total_late_minutes", 0]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$ifNull": ["$_stats.leave_count", 0]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$ifNull": ["$_stats.on_duty_count", 0]
                    }
                },
            }
        },
        {"$sort": {"_id": 1}},
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$divide": [
                                "$work_hours_sum",
                                "$work_hours_count",
                            ]
                        },
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
    ]

    results = list(db.employees.aggregate(pipeline))

    for item in results:
        item["present_days"] = round_half_up(item["present_days"])
        if item["avg_work_hours"] is not None:
            item["avg_work_hours"] = round_half_up(
                item["avg_work_hours"]
            )

    return {"month": month, "items": results}



def build_late_leaderboard_pipeline(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    year, month_number = map(int, month.split("-"))

    if month_number == 12:
        next_month = f"{year + 1:04d}-01-01"
    else:
        next_month = f"{year:04d}-{month_number + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": f"{month}-01",
                    "$lt": next_month,
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    pipeline = build_late_leaderboard_pipeline(
        month=month,
        limit=limit,
        department=department,
    )

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}

def build_department_trend_pipeline(
    department: str,
    from_date: date,
    to_date: date,
):
    start_dt = datetime.combine(
        from_date,
        time.min,
        tzinfo=timezone.utc,
    )

    end_exclusive = datetime.combine(
        to_date + timedelta(days=1),
        time.min,
        tzinfo=timezone.utc,
    )

    
        # Keep your existing pipeline stages here.
    pipeline = [
            # Generate the calendar dates inside MongoDB.
            {"$documents": [{"date": start_dt}]},
            {
                "$densify": {
                    "field": "date",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [start_dt, end_exclusive],
                    },
                }
            },
            {
                "$set": {
                    "date_str": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$date",
                            "timezone": "UTC",
                        }
                    },
                    "weekday": {"$dayOfWeek": "$date"},
                }
            },
            # Count employees who have joined by each date.
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"trend_date": "$date_str"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$department",
                                                department,
                                            ]
                                        },
                                        {
                                            "$lte": [
                                                "$joined_on",
                                                "$$trend_date",
                                            ]
                                        },
                                    ]
                                }
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "_headcount",
                }
            },
            {
                "$set": {
                    "headcount": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$_headcount.count", 0]},
                            0,
                        ]
                    }
                }
            },
            # Fetch attendance for each date and keep this department only.
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "localField": "date_str",
                    "foreignField": "date",
                    "pipeline": [
                        {
                            "$lookup": {
                                "from": "employees",
                                "localField": "emp_code",
                                "foreignField": "emp_code",
                                "as": "_employee",
                            }
                        },
                        {"$unwind": "$_employee"},
                        {
                            "$match": {
                                "_employee.department": department
                            }
                        },
                        {
                            "$group": {
                                "_id": None,
                                "present_count": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$in": [
                                                    "$status",
                                                    [
                                                        "PRESENT",
                                                        "WFH",
                                                        "ON_DUTY",
                                                    ],
                                                ]
                                            },
                                            {
                                                "$cond": [
                                                    {
                                                        "$ifNull": [
                                                            "$half_day",
                                                            False,
                                                        ]
                                                    },
                                                    0.5,
                                                    1,
                                                ]
                                            },
                                            0,
                                        ]
                                    }
                                },
                                "late_count": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$gt": [
                                                    {
                                                        "$ifNull": [
                                                            "$late_minutes",
                                                            0,
                                                        ]
                                                    },
                                                    0,
                                                ]
                                            },
                                            1,
                                            0,
                                        ]
                                    }
                                },
                            }
                        },
                    ],
                    "as": "_attendance",
                }
            },
            {
                "$set": {
                    "present_count": {
                        "$ifNull": [
                            {
                                "$arrayElemAt": [
                                    "$_attendance.present_count",
                                    0,
                                ]
                            },
                            0,
                        ]
                    },
                    "late_count": {
                        "$ifNull": [
                            {
                                "$arrayElemAt": [
                                    "$_attendance.late_count",
                                    0,
                                ]
                            },
                            0,
                        ]
                    },
                    "is_working_day": {
                        "$and": [
                            {"$gte": ["$weekday", 2]},
                            {"$lte": ["$weekday", 6]},
                        ]
                    },
                }
            },
            # Round non-negative rates to four decimal places, half-up.
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
                                                                "$present_count",
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
            # Average today's rate and up to six previous rows.
            {
                "$setWindowFields": {
                    "sortBy": {"date": 1},
                    "output": {
                        "_moving_raw": {
                            "$avg": "$attendance_rate",
                            "window": {
                                "documents": [-6, 0],
                            },
                        }
                    },
                }
            },
            {
                "$set": {
                    "moving_avg_7d": {
                        "$cond": [
                            {"$eq": ["$_moving_raw", None]},
                            None,
                            {
                                "$divide": [
                                    {
                                        "$floor": {
                                            "$add": [
                                                {
                                                    "$multiply": [
                                                        "$_moving_raw",
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
                        ]
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$date_str",
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": 1,
                }
            },
            {"$sort": {"date": 1}},
        ]

    return pipeline


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
):
    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (to_date - from_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    # Verify that the department exists.
    if not db.employees.find_one(
        {"department": department},
        {"_id": 1},
    ):
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )
    pipeline = build_department_trend_pipeline(
        department=department,
        from_date=from_date,
        to_date=to_date,
    )

    items = list(db.aggregate(pipeline))

    return {
        "department": department,
        "items": items,
    }

    start_dt = datetime.combine(
        from_date, time.min, tzinfo=timezone.utc
    )
    end_exclusive = datetime.combine(
        to_date + timedelta(days=1),
        time.min,
        tzinfo=timezone.utc,
    )

    pipeline = [
        # Generate the calendar dates inside MongoDB.
        {"$documents": [{"date": start_dt}]},
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_exclusive],
                },
            }
        },
        {
            "$set": {
                "date_str": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                        "timezone": "UTC",
                    }
                },
                "weekday": {"$dayOfWeek": "$date"},
            }
        },
        # Count employees who have joined by each date.
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$date_str"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$department",
                                            department,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$joined_on",
                                            "$$trend_date",
                                        ]
                                    },
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "_headcount",
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$_headcount.count", 0]},
                        0,
                    ]
                }
            }
        },
        # Fetch attendance for each date and keep this department only.
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "date_str",
                "foreignField": "date",
                "pipeline": [
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "_employee",
                        }
                    },
                    {"$unwind": "$_employee"},
                    {
                        "$match": {
                            "_employee.department": department
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                [
                                                    "PRESENT",
                                                    "WFH",
                                                    "ON_DUTY",
                                                ],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {
                                                    "$ifNull": [
                                                        "$half_day",
                                                        False,
                                                    ]
                                                },
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {
                                                    "$ifNull": [
                                                        "$late_minutes",
                                                        0,
                                                    ]
                                                },
                                                0,
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "as": "_attendance",
            }
        },
        {
            "$set": {
                "present_count": {
                    "$ifNull": [
                        {
                            "$arrayElemAt": [
                                "$_attendance.present_count",
                                0,
                            ]
                        },
                        0,
                    ]
                },
                "late_count": {
                    "$ifNull": [
                        {
                            "$arrayElemAt": [
                                "$_attendance.late_count",
                                0,
                            ]
                        },
                        0,
                    ]
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$weekday", 2]},
                        {"$lte": ["$weekday", 6]},
                    ]
                },
            }
        },
        # Round non-negative rates to four decimal places, half-up.
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
                                                            "$present_count",
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
        # Average today's rate and up to six previous rows.
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "_moving_raw": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0],
                        },
                    }
                },
            }
        },
        {
            "$set": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$_moving_raw", None]},
                        None,
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$_moving_raw",
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
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_str",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.aggregate(pipeline))

    return {
        "department": department,
        "items": items,
    }


def build_attendance_list_pipeline(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    match = {}

    if emp_code:
        match["emp_code"] = emp_code

    if date_from or date_to:
        match["date"] = {}

        if date_from:
            match["date"]["$gte"] = date_from.isoformat()

        if date_to:
            match["date"]["$lte"] = date_to.isoformat()

    if status:
        match["status"] = status

    pipeline = []

    if match:
        pipeline.append({"$match": match})

    # Sort before $facet so MongoDB can use the date index.
    pipeline.extend([
        {"$sort": {"date": -1, "emp_code": 1}},
        {
            "$facet": {
                "items": [
                    {"$skip": (page - 1) * page_size},
                    {"$limit": page_size},
                ],
                "meta": [
                    {"$count": "total"},
                ],
            }
        },
    ])

    return pipeline

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
        )

    if status and status not in {
        "PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"
    }:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    pipeline = build_attendance_list_pipeline(
        emp_code=emp_code,
        date_from=date_from,
        date_to=date_to,
        status=status,
        page=page,
        page_size=page_size,
    )

    result = list(db.attendance_logs.aggregate(pipeline))[0]

    # Use your existing serializer to convert BSON timestamps to epoch ms.
    items = [
        serialize_attendance(doc)
        for doc in result["items"]
    ]

    total = (
        result["meta"][0]["total"]
        if result["meta"]
        else 0
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    month: Optional[str] = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"endpoint must be one of: {sorted(allowed)}",
        )

    # Explain attendance list
    if endpoint == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

        if status and status not in {
            "PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"
        }:
            raise HTTPException(
                status_code=422,
                detail="Invalid attendance status",
            )

        pipeline = build_attendance_list_pipeline(
            emp_code=emp_code,
            date_from=date_from,
            date_to=date_to,
            status=status,
            page=page,
            page_size=page_size,
        )

        explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }

    # Explain late leaderboard
    if endpoint == "late_leaderboard":
        if month is None:
            raise HTTPException(
                status_code=422,
                detail="'month' is required for late_leaderboard",
            )

        pipeline = build_late_leaderboard_pipeline(
            month=month,
            limit=limit,
            department=department,
        )

        explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }
    
    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="'emp_code' and 'month' are required",
            )

        pipeline = build_employee_monthly_pipeline(
            emp_code=emp_code,
            month=month,
        )

        explain = db.command({
            "explain": {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }

    
    if endpoint == "department_summary":
        if month is None:
            raise HTTPException(
                status_code=422,
                detail="'month' is required for department_summary",
            )

        pipeline = build_department_summary_pipeline(
            month=month,
            department=department,
        )

        explain = db.command({
            "explain": {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain,
        }
    
    if endpoint == "department_trend":
        if from_date is None or to_date is None:
            raise HTTPException(
                status_code=422,
                detail="'from' and 'to' are required",
            )

        if to_date < from_date:
            raise HTTPException(
                status_code=422,
                detail="'to' must be on or after 'from'",
            )

        if (to_date - from_date).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days",
            )

        if db.employees.find_one(
            {"department": department},
            {"_id": 1},
        ) is None:
            raise HTTPException(
                status_code=404,
                detail="Department not found",
            )

        pipeline = build_department_trend_pipeline(
            department=department,
            from_date=from_date,
            to_date=to_date,
        )

        explain = db.command({
            "explain": {
                "aggregate": 1,
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })

        return {
            "endpoint": endpoint,
            "collection": "database-level aggregation",
            "explain": explain,
        }

    # Other endpoint pipelines will be connected next.
    raise HTTPException(
        status_code=501,
        detail=f"Explain is not connected yet for '{endpoint}'",
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
