# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.

# Design Decisions

## 1. Why use MongoDB aggregation pipelines for analytics?

MongoDB aggregation pipelines perform filtering, grouping, joins, and calculations close to the stored data. This reduces unnecessary data transfer to Python and supports execution-plan inspection for performance analysis.

## 2. How are duplicate punch-ins prevented?

A unique compound index on `emp_code` and attendance `date` enforces one attendance record per employee per day. Duplicate-key errors are translated into HTTP 409 responses. This protects against concurrent requests as well as ordinary duplicate submissions.

## 3. How are timestamps and attendance dates handled?

API timestamps are validated as epoch milliseconds and converted into timezone-aware datetimes. Attendance dates and shift calculations use India Standard Time (IST), including appropriate handling of overnight shifts.

## 4. How is attendance regularization audited?

Regularization accepts only permitted manual corrections. Each successful correction appends one history entry containing the correction timestamp, actor, reason, and changed fields. Atomic database updates help prevent concurrent corrections from overwriting audit history.

## 5. How are analytics made accurate and efficient?

Analytics use indexed queries and aggregation pipelines. Monthly calculations account for joining dates, weekdays, and employees without attendance records. Trend endpoints generate missing dates, apply the required rounding rules, and return execution statistics to help identify inefficient query plans.
