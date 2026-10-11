# DECISIONS.md

## 1. Indexes

I added a unique index on `emp_code` to prevent duplicate employees and a compound unique index on `emp_code` and `date` to prevent duplicate attendance records. I also added an index on `date` and `emp_code` to support attendance listing and sorting. The indexes need to be created reliably at application startup and tested with the actual queries.

## 2. Punch-in Race

Two punch-in requests for the same employee and date can arrive at nearly the same time. Checking for an existing record before inserting is not enough because both requests could pass the check. The unique compound index is needed so only one insert succeeds. The other request should return HTTP 409.

## 3. Ties in the Leaderboard

If employees have the same total late minutes, they should receive the same rank. The next rank should skip the tied positions. For example, the ranks could be 1, 2, 2, and 4. The result should be sorted by total late minutes in descending order and `emp_code` in ascending order for consistent output. The limit should be applied after ranking so employees tied at the cutoff are included.

## 4. Department Headcount

Department headcount should be calculated from the employees collection, not only from attendance logs. Employees who joined on or before the end of the selected month must be counted even if they have no attendance records. This avoids undercounting employees who have not yet punched in.

## 5. Scaling to 100x Data

If the data volume increased 100 times, I would move more filtering, grouping, and calculations into MongoDB aggregation pipelines instead of loading large result sets into Python. I would also review indexes using execution statistics and test query performance with a larger dataset.
