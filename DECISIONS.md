# DECISIONS.md

## 1. Indexes

Indexes improve query performance by allowing MongoDB to locate relevant documents without scanning the entire collection. I created a unique index on `emp_code` in the employees collection to prevent duplicate employee identifiers. In the attendance collection, I created a unique compound index on `emp_code` and `date` to ensure that an employee cannot have multiple attendance records for the same day. This also supports queries that filter attendance records by employee and date range. I added a compound index on `date` and `emp_code` to support attendance listing and sorting by date in descending order and employee code in ascending order.

Index selection should be based on actual query patterns rather than adding indexes unnecessarily. Every additional index consumes storage and increases the cost of inserts and updates. I would verify the indexes during application initialization and inspect execution statistics for the important endpoints. The goal is to ensure that frequently used queries benefit from index scans and avoid collection scans when possible. I would also test the indexes with realistic data volumes before considering the implementation complete.

## 2. Punch-in Race

A race condition can occur when two punch-in requests for the same employee arrive almost simultaneously. Both requests might check whether an attendance record already exists and find no record. If the application then inserts a record without database-level protection, duplicate attendance records could be created for the same employee and date.

The application-level existence check alone is therefore insufficient. I use a unique compound index on `emp_code` and `date` as the final protection against duplicates. MongoDB enforces this constraint even when multiple requests execute concurrently. One insert can succeed, while the other receives a duplicate-key error.

The application should catch that specific database error and return HTTP 409 Conflict to indicate that an attendance record already exists. It should not expose raw database error messages to the client. Other unexpected database errors should be handled separately rather than incorrectly reported as conflicts. This approach combines a helpful application-level check with reliable database-level enforcement. I would test the behavior by sending concurrent punch-in requests for the same employee and date and verifying that only one record is created.

## 3. Ties in the Leaderboard

The late-arrival leaderboard should rank employees according to their total late minutes for the selected month. Employees with equal totals should receive the same rank. I use competition ranking, where the next rank skips positions occupied by tied employees. For example, totals that produce ranks 1, 2, 2, and 4 demonstrate this behavior.

The ranking should be deterministic. I sort employees by total late minutes in descending order and use `emp_code` in ascending order as a secondary sorting key. The secondary key ensures consistent ordering among employees who have the same total, even though their ranks remain equal.

The limit must be applied after ranking so that employees tied at the cutoff are not accidentally excluded. For example, if the requested limit is three and the third position is shared by multiple employees, all employees sharing that rank should be included. This means the final result may contain more rows than the requested limit. I would verify the implementation with test data containing unique totals, multiple ties, and ties at the cutoff. These tests help ensure that ranking behavior is correct and predictable for API consumers.

## 4. Department Headcount

Department headcount should be calculated from the employees collection rather than from attendance records. Attendance records only show employees who have recorded attendance, so using them as the source of headcount can produce an incorrect result. Employees who have not punched in, are absent, or have no attendance records would otherwise be excluded.

For a selected month, I count employees belonging to the requested department whose `joined_on` date is on or before the final calendar day of that month. This includes employees who joined during the month as well as employees who joined earlier. Employees who joined after the selected month should not be included in that month's headcount.

Attendance statistics should then be calculated separately for the eligible employees and the selected date range. This separation makes headcount independent of attendance activity and prevents employees without logs from disappearing from the summary. It also makes the meaning of each metric clearer: headcount represents eligible employees, while attendance totals represent recorded activity. I would test the summary using employees with attendance records, employees without attendance records, and employees whose joining dates fall before, during, and after the selected month.

## 5. Scaling to 100x Data

If the dataset increased by 100 times, I would first identify which endpoints consume the most time and examine their MongoDB execution statistics. I would pay particular attention to documents examined, keys examined, returned documents, index usage, and collection scans. This would help distinguish inefficient queries from costs caused simply by processing more data.

I would move large-scale filtering, grouping, sorting, and calculations into MongoDB aggregation pipelines where appropriate, instead of transferring large collections to Python and processing them in memory. I would also ensure that indexes match the filters and sort orders used by frequently called endpoints. Compound indexes must be selected carefully because field order affects their usefulness.

Pagination should remain in place for list endpoints, and the application should avoid loading unnecessary fields or documents. I would generate a larger representative dataset and benchmark the endpoints before and after changes. I would also check memory consumption and response times under concurrent requests. Any optimization should preserve the API's expected behavior, including date handling, rounding, ranking ties, and empty results. Finally, I would document the measurements and explain why each optimization was introduced rather than assuming that adding more indexes automatically improves performance.
