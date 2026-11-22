-- V5: free text the customer typed at checkout ("leave at the back door").
-- It is UNTRUSTED: anything can be written here, including text that looks like instructions.
alter table orders add column customer_note text;
