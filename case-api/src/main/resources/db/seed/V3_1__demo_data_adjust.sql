-- Demo data fix (a NEW migration: never edit one that has already run).
-- Order 1050 was 9 calendar days late, which is only 5 business days on some weekdays,
-- exactly the policy limit, so "is it eligible?" changed with the day of the week.
-- 12 calendar days is always at least 8 business days.
update orders set promised_date = current_date - 12 where id = '1050';
