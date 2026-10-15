# Starter Code Review

This review identifies potential correctness, validation, concurrency, auditability, and query-performance issues to verify in the starter implementation.

## 1. Duplicate attendance records

- **Location:** Attendance creation endpoint and database indexes.
- **Symptom/Test:** Submit two punch-ins for the same employee and attendance date.
- **Fix:** Enforce a unique compound index on `(emp_code, date)` and return HTTP 409 for duplicate records.

## 2. MongoDB internal identifiers in API responses

- **Location:** Employee and attendance response serialization.
- **Symptom/Test:** Inspect API responses for MongoDB `_id` values.
- **Fix:** Exclude `_id` from aggregation projections and remove it from serialized responses.

## 3. Invalid epoch-millisecond timestamps

- **Location:** Punch-in and punch-out request validation.
- **Symptom/Test:** Submit strings, floats, seconds-based timestamps, or timestamps outside the supported range.
- **Fix:** Validate integer epoch milliseconds and reject invalid inputs with HTTP 422.

## 4. Incorrect attendance date around midnight

- **Location:** Attendance-date calculation.
- **Symptom/Test:** Submit a punch-in near midnight UTC and verify the resulting IST attendance date.
- **Fix:** Convert timestamps to IST before determining the attendance date.

## 5. Overnight shift misclassification

- **Location:** Shift-end and attendance-date calculation.
- **Symptom/Test:** Test a shift that starts before midnight and ends the next day.
- **Fix:** Assign the next calendar day to the shift end when the configured shift crosses midnight.

## 6. Incorrect late-arrival threshold

- **Location:** Late-minute calculation.
- **Symptom/Test:** Compare arrival exactly 10 minutes after shift start with arrival 10 minutes and one second late.
- **Fix:** Apply the strict threshold and calculate whole late minutes according to the specification.

## 7. Incorrect overtime threshold

- **Location:** Overtime calculation.
- **Symptom/Test:** Test 29 minutes and 30 minutes after shift end.
- **Fix:** Count overtime only when the qualifying duration reaches at least 30 minutes.

## 8. Inconsistent rounding

- **Location:** Work-hour and analytics calculations.
- **Symptom/Test:** Test values near two-decimal and four-decimal rounding boundaries.
- **Fix:** Use the required half-up rounding method for work hours, totals, and rates.

## 9. Editable derived attendance fields

- **Location:** Attendance regularization request model.
- **Symptom/Test:** Attempt to modify calculated fields such as work hours, overtime, or late minutes.
- **Fix:** Permit corrections only to allowed manual fields and recalculate derived values.

## 10. Missing audit-history entries

- **Location:** Attendance PATCH handler.
- **Symptom/Test:** Correct an attendance record and inspect its history.
- **Fix:** Append exactly one audit entry per successful correction, containing the timestamp, actor, reason, and changed fields.

## 11. Lost updates during concurrent corrections

- **Location:** Attendance regularization database update.
- **Symptom/Test:** Submit simultaneous corrections to the same record.
- **Fix:** Use atomic updates or an appropriate concurrency-control strategy to prevent history entries from being overwritten.

## 12. Incorrect monthly working-day counts

- **Location:** Employee monthly analytics aggregation.
- **Symptom/Test:** Test an employee who joined partway through a month and an employee with no attendance logs.
- **Fix:** Count weekdays from the later of the month start and joining date, including eligible employees with no logs.

## 13. Pagination totals calculated before filtering

- **Location:** Employee and attendance listing endpoints.
- **Symptom/Test:** Apply filters and compare the returned total with the matching records.
- **Fix:** Calculate the total after applying all filters and before pagination.

## 14. Inefficient analytics queries

- **Location:** Monthly, department-summary, leaderboard, and trend aggregations.
- **Symptom/Test:** Inspect execution statistics with representative data.
- **Fix:** Add suitable indexes, use selective match stages, and verify query plans for avoidable collection scans.

## 15. Missing dates in department trends

- **Location:** Department trend aggregation.
- **Symptom/Test:** Request a date range containing weekdays or weekends with no attendance records.
- **Fix:** Generate a complete date series and calculate daily metrics for every requested date.

## Validation note

Before submission, compare every finding with the original starter code. Record the actual function or line location, the observed behavior or reproducible test, and the implemented or recommended fix. Remove any finding that does not apply to the starter implementation.
