# API rate limits

## Default limits
The Northwind API allows 600 requests per minute per token on Business, and 60 per minute on
Starter. Exceeding the limit returns HTTP 429 with a Retry-After header in seconds.

## Raising your limit
Enterprise customers can request a higher limit through their account manager. There is no
self-serve limit increase on Starter or Business.
