# PayConnect Log Extractor

Desktop tool that searches MinIO log exports for one or more
Pay IDs and pulls out the full request/response block for each match --
one text file per Pay ID.

## Setup

1. Install Python 3.9+.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `config.example.json` to `config.json` and fill in your details:
   ```json
   {
     "endpoint": "https://sample.net",
     "username": "your-console-username",
     "password": "your-console-password",
     "bucket": "payconnect-log-exports-prod",
     "base_prefix": "logs/",
     "extensions": ".log,.gz,.txt",
     "block_marker": "Logging Request",
     "workers": 4,
     "out_dir": ""
   }
   ```
   `config.json` is gitignored -- it never gets committed.

## Run

```
python payid_log_extractor.py
```

Enter a date range (optional -- leave blank to search every date) and one
or more Pay IDs (one per line, or comma-separated), pick a save folder, and
click **Search & extract**. Each Pay ID gets its own `.txt` file containing
every matching request block found.

## How it works

- Logs into the MinIO console API (same login as the browser).
- Lists log files under `base_prefix` (paginated automatically).
- Downloads and, if gzipped, decompresses each file in parallel
  (`workers` controls how many at once).
- Groups lines into blocks using `block_marker` as the start-of-request
  marker, and writes out any block containing a searched Pay ID.
- Pay IDs with zero matches are reported and skipped (no empty file).

## Notes

- `block_marker` may need adjusting per log source if the logger format
  differs between services.
- Increase `workers` for faster scans on a good connection; lower it if
  requests start timing out.
